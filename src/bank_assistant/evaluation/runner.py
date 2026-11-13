"""M6 固定评测运行器（T27，#28）。

职责：读冻结 test split → 校验数据集/快照哈希 → 跑检索三变体与质检三基线 →
聚合指标 → 写 manifest/metrics/predictions。所有正式产物落在
``<ARTIFACT_ROOT>/evals/<run_id>/``。

硬约束：

- **只用冻结 test**：``split_guard.require_frozen_test_split`` 在配置加载与运行
  时都会拒绝 dev/train；
- **哈希闸门**：数据集 manifest、test 文件哈希、语料知识/规则快照必须一致，
  任一漂移就显式失败；
- **模式隔离**：replay 结果只验证流水线，manifest 的 ``mode/model_mode`` 都写
  replay 且 ``gates_applied=false``；live 才产生真实延迟/token/成本；
- **不静默丢样本**：四态 verdict 与 failed 都进入聚合；失败案例单独列出；
- **不写凭据**：manifest 写入前递归扫描，命中疑似 key 直接拒绝。

数据库说明：本运行器读的是数据集冻结时绑定的合成语料快照，不直接连应用
数据库；应用运行时的知识/规则快照来自 DB。当前基础设施仍是过渡性的本机
容器（ADR-0006 未实现），详见 ``docs/eval/README.md``。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bank_assistant.assistants.suggestions import GatewayQueryRewriter
from bank_assistant.core.model_gateway import ModelGateway
from bank_assistant.datasets.build import verify_dataset
from bank_assistant.datasets.corpus import REPO_ROOT, Corpus, load_corpus
from bank_assistant.datasets.split_guard import require_frozen_test_split
from bank_assistant.evaluation.config import EvalConfig
from bank_assistant.evaluation.embedding import ReplayLexicalEmbedding
from bank_assistant.evaluation.errors import EvalBlocked, EvalError
from bank_assistant.evaluation.inspection import (
    VARIANTS as INSPECTION_VARIANTS,
    build_rule_entries,
    inspection_prompt_components,
    parse_inspection_items,
    run_inspection,
)
from bank_assistant.evaluation.manifest import (
    RunContext,
    build_run_manifest,
    validate_manifest,
    write_run_artifacts,
)
from bank_assistant.evaluation.metrics import (
    InspectionRecord,
    Price,
    RetrievalRecord,
    percentile,
    recall_at_k,
    summarize_inspection,
    summarize_retrieval,
)
from bank_assistant.evaluation.sampling import stratified_sample
from bank_assistant.evaluation.replay import (
    CostTracker,
    MeteredGateway,
    MeteredRewriter,
    ReplayGateway,
    ReplayQueryRewriter,
)
from bank_assistant.evaluation.retrieval import (
    RetrievalHarness,
    parse_retrieval_items,
    run_retrieval,
)
from bank_assistant.knowledge.embedding import (
    FastEmbedProvider,
    calibration_vector_sha256,
)

__all__ = ["EvalRun", "run_eval"]

SCHEMA_VERSION = 1
PRIMARY_RETRIEVAL_VARIANT = "core_intent_filter"
PRIMARY_INSPECTION_VARIANT = "combined"
# 计划 §7 的工程门槛；只登记，不因为回放/小样本而宣称达标。
THRESHOLDS: dict[str, float] = {
    "retrieval_recall_at_5": 0.85,
    "no_answer_handling_rate": 0.90,
    "citation_validity": 1.0,
    "macro_f1": 0.80,
    "high_risk_recall": 0.95,
    "evidence_localization_accuracy": 1.0,
}
_MAX_FAILURE_CASES = 40


@dataclass(frozen=True, slots=True)
class EvalRun:
    run_id: str
    status: str
    manifest: dict[str, Any]
    metrics: dict[str, Any]
    predictions: list[dict[str, Any]]
    paths: dict[str, str]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _code_sha() -> str:
    injected = os.environ.get("CODE_SHA", "").strip()
    if injected:
        return injected[:64]
    return (_git("rev-parse", "HEAD") or "unknown")[:64]


def _worktree_clean() -> bool:
    return _git("status", "--porcelain") == ""


def _hardware() -> tuple[str, dict[str, Any]]:
    memory_bytes: int | None = None
    try:
        memory_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        memory_bytes = None
    hardware = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "memory_gib": round(memory_bytes / 1024**3, 1) if memory_bytes else None,
        "python": sys.version.split()[0],
    }
    summary = (
        f"{hardware['platform']} / {hardware['machine']} / "
        f"{hardware['cpu_count']} CPU / Python {hardware['python']}"
        + (
            f" / {hardware['memory_gib']} GiB"
            if hardware["memory_gib"] is not None
            else ""
        )
    )
    return summary, hardware


def _timestamp() -> datetime:
    return datetime.now(UTC)


def _timestamp_slug(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _load_dataset(config: EvalConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = config.dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise EvalError(
            f"找不到冻结数据集 manifest：{manifest_path}；"
            "请先运行 make dataset-build（或 make dataset-verify 校验已有产物）"
        )
    report = verify_dataset(config.dataset_dir)
    if not report["ok"]:
        raise EvalError(
            "冻结数据集校验失败：" + "；".join(report["errors"][:5])
        )
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest.get("dataset_id") != config.dataset_id:
        raise EvalError(
            f"数据集 id 不一致：manifest={manifest.get('dataset_id')!r} "
            f"config={config.dataset_id!r}"
        )
    if manifest.get("dataset_version") != config.dataset_version:
        raise EvalError(
            f"数据集版本不一致：manifest={manifest.get('dataset_version')!r} "
            f"config={config.dataset_version!r}"
        )
    test_path = config.dataset_dir / "test.jsonl"
    payload = test_path.read_bytes()
    tests_sha = _sha256_bytes(payload)
    frozen_sha = manifest.get("frozen", {}).get("test_split_sha256")
    if tests_sha != frozen_sha:
        raise EvalError(
            "test.jsonl 与冻结哈希不一致："
            f"manifest={frozen_sha} 实际={tests_sha}"
        )
    records = [json.loads(line) for line in payload.splitlines()]
    if not records:
        raise EvalError("冻结 test split 为空，不能产出评测报告")
    return manifest, records


def _load_corpus(manifest: dict[str, Any]) -> Corpus:
    corpus = load_corpus()
    if corpus.knowledge_snapshot() != manifest.get("knowledge_snapshot"):
        raise EvalError(
            "知识快照漂移：当前 data/demo 语料与数据集 manifest 的 "
            "knowledge_snapshot 不一致，拒绝在漂移快照上评测"
        )
    if corpus.rule_snapshot() != manifest.get("rule_snapshot"):
        raise EvalError(
            "规则快照漂移：当前 data/demo 规则与数据集 manifest 的 rule_snapshot 不一致"
        )
    return corpus


def _build_runtime(
    config: EvalConfig,
) -> tuple[Any, MeteredGateway, MeteredRewriter, CostTracker, str, str]:
    """构造 embedding、网关与改写器；live 能力不足时抛 EvalBlocked。"""
    if config.mode == "replay":
        provider = ReplayLexicalEmbedding()
        inner_gateway: Any = ReplayGateway()
        rewriter_inner: Any = ReplayQueryRewriter()
        model_provider = "replay"
        model_version = "deterministic-replay"
        tracker = CostTracker(price=None, budget_cny=None)
    else:
        try:
            provider = (
                ReplayLexicalEmbedding()
                if config.embedding_provider == "replay_lexical"
                else FastEmbedProvider()
            )
        except Exception as exc:  # noqa: BLE001 - 依赖/下载失败统一标记 BLOCKED
            raise EvalBlocked(f"embedding 不可用：{exc}") from exc
        try:
            base_gateway = ModelGateway.from_settings()
        except Exception as exc:  # noqa: BLE001 - 凭据/SDK 缺失统一标记 BLOCKED
            raise EvalBlocked(f"模型能力不可用：{exc}") from exc
        from bank_assistant.config.settings import settings

        if settings.DEEPSEEK_MODEL != config.model_name:
            raise EvalError(
                f"配置模型 {config.model_name!r} 与 .env DEEPSEEK_MODEL "
                f"{settings.DEEPSEEK_MODEL!r} 不一致；manifest 不能记录错误模型"
            )
        entry = config.price_for(config.model_provider, config.model_name)
        price = Price(
            input_cny_per_mtok=entry.input_cny_per_mtok,
            output_cny_per_mtok=entry.output_cny_per_mtok,
        )
        tracker = CostTracker(price=price, budget_cny=config.budget_cny)
        inner_gateway = base_gateway
        rewriter_inner = GatewayQueryRewriter(base_gateway)
        model_provider = config.model_provider
        model_version = config.model_name
    gateway = MeteredGateway(
        inner_gateway,
        tracker=tracker,
        model_provider=model_provider,
        model_version=model_version,
    )
    rewriter = MeteredRewriter(rewriter_inner, tracker=tracker)
    return provider, gateway, rewriter, tracker, model_provider, model_version


def _cap(items: list[Any], max_items: int | None) -> list[Any]:
    return stratified_sample(
        items,
        max_items,
        scenario_of=lambda item: item.scenario,
        is_hard=lambda item: bool(item.hard_categories),
    )


def _retrieval_failures(records: list[RetrievalRecord]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for record in records:
        if record.error:
            failures.append(
                {
                    "task": "retrieval",
                    "variant": record.variant,
                    "item_id": record.item_id,
                    "reason": "error",
                    "error": record.error,
                }
            )
            continue
        if record.answerable:
            if record.predicted_no_answer:
                reason = "false_no_answer"
            elif record.relevant_keys and recall_at_k(
                record.ranked_keys, record.relevant_keys
            ) < 1.0:
                reason = "relevant_document_missed"
            else:
                continue
        elif record.predicted_no_answer:
            continue
        elif record.unanswerable_reason == "expired_knowledge" and not any(
            key.split("#", 1)[0] in set(record.excluded_doc_ids)
            for key in record.ranked_keys
        ):
            continue
        else:
            reason = f"unanswerable_answered:{record.unanswerable_reason or 'unknown'}"
        failures.append(
            {
                "task": "retrieval",
                "variant": record.variant,
                "item_id": record.item_id,
                "scenario": record.scenario,
                "reason": reason,
                "answerable": record.answerable,
                "predicted_no_answer": record.predicted_no_answer,
                "ranked_doc_ids": list(record.ranked_keys),
                "relevant_doc_ids": list(record.relevant_keys),
                "excluded_doc_ids": list(record.excluded_doc_ids),
            }
        )
    return failures


def _inspection_failures(records: list[InspectionRecord]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for record in records:
        if (
            record.predicted_verdict == record.gold_verdict
            and not record.error
            and record.evidence_valid
        ):
            continue
        failures.append(
            {
                "task": "inspection",
                "variant": record.variant,
                "item_id": record.item_id,
                "rule_code": record.rule_code,
                "gold_verdict": record.gold_verdict,
                "predicted_verdict": record.predicted_verdict,
                "error": record.error,
                "evidence_valid": record.evidence_valid,
            }
        )
    return failures


def _trim_failures(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return failures[:_MAX_FAILURE_CASES]


def _assert_citations(records: list[InspectionRecord]) -> None:
    for record in records:
        if record.predicted_verdict == "hit" and not record.evidence_valid:
            raise AssertionError(
                f"引用/证据合法性断言失败：{record.item_id}/{record.rule_code}"
            )


def _blocked_run(
    *,
    config: EvalConfig,
    dataset_manifest: dict[str, Any],
    corpus: Corpus,
    started_at: datetime,
    error: str,
    run_id: str | None = None,
) -> EvalRun:
    finished_at = _timestamp()
    run_id = run_id or config.run_id or (
        f"eval-{config.mode}-{config.dataset_id}-{config.dataset_version}-"
        f"{_timestamp_slug(started_at)}"
    )
    prompt_components = inspection_prompt_components()
    prompt_version = "eval-v1:" + hashlib.sha256(
        json.dumps(prompt_components, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    hardware_summary, hardware = _hardware()
    context = RunContext(
        run_id=run_id,
        task="m6_eval",
        mode=config.mode,
        model_mode=config.model_mode,
        dataset_id=dataset_manifest["dataset_id"],
        dataset_version=dataset_manifest["dataset_version"],
        dataset_manifest_sha256=dataset_manifest["dataset_manifest_sha256"],
        split=config.split,
        split_manifest_sha256=dataset_manifest["split_manifest_sha256"],
        dataset_test_sha256=dataset_manifest["frozen"]["test_split_sha256"],
        knowledge_snapshot=corpus.knowledge_snapshot(),
        rule_snapshot=corpus.rule_snapshot(),
        prompt_version=prompt_version,
        prompt_components=prompt_components,
        model_provider=config.model_provider,
        model_version=config.model_name,
        embedding_version="unavailable",
        index_version="unavailable",
        seed=config.seed,
        code_sha=_code_sha(),
        worktree_clean=_worktree_clean(),
        hardware_summary=hardware_summary,
        hardware=hardware,
        started_at=started_at,
        finished_at=finished_at,
        status="blocked",
        notes={"blocked_reason": error},
    )
    manifest = build_run_manifest(context=context)
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": run_id,
            "mode": config.mode,
            "status": "blocked",
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        },
        "status": "blocked",
        "error": error,
        "retrieval": {"skipped": True},
        "inspection": {"skipped": True},
        "citation": {
            "validity": None,
            "support": {"status": "NOT_MEASURED"},
        },
        "cost": {"estimated": True, "cny": None},
    }
    paths = write_run_artifacts(
        artifact_root=config.artifact_root,
        manifest=manifest,
        metrics=metrics,
        predictions=[],
    )
    stored = json.loads((Path(paths["run_dir"]) / "manifest.json").read_text("utf-8"))
    validate_manifest(stored)
    return EvalRun(
        run_id=run_id,
        status="blocked",
        manifest=stored,
        metrics=metrics,
        predictions=[],
        paths=paths,
    )


def run_eval(
    config: EvalConfig,
    *,
    run_id: str | None = None,
    started_at: datetime | None = None,
) -> EvalRun:
    """执行一次完整评测并落盘；返回内存中的运行结果。"""
    started = started_at or _timestamp()
    if started.tzinfo is None:
        raise EvalError("started_at 必须带时区")
    require_frozen_test_split(config.split)
    dataset_manifest, records = _load_dataset(config)
    corpus = _load_corpus(dataset_manifest)

    resolved_run_id = run_id or config.run_id or (
        f"eval-{config.mode}-{config.dataset_id}-{config.dataset_version}-"
        f"{_timestamp_slug(started)}"
    )
    try:
        provider, gateway, rewriter, tracker, model_provider, model_version = (
            _build_runtime(config)
        )
    except EvalBlocked as exc:
        return _blocked_run(
            config=config,
            dataset_manifest=dataset_manifest,
            corpus=corpus,
            started_at=started,
            error=str(exc),
            run_id=resolved_run_id,
        )

    retrieval_items = parse_retrieval_items(records)
    inspection_items = parse_inspection_items(records)
    capped_retrieval = _cap(retrieval_items, config.max_items)
    capped_inspection = _cap(inspection_items, config.max_items)

    harness = RetrievalHarness(
        corpus=corpus,
        provider=provider,
        top_k=config.top_k,
        min_similarity=config.min_similarity,
        as_of=config.knowledge_as_of,
        embedding_text=config.retrieval.embedding_text,
        business_filter=config.retrieval.business_filter,
        business_bonus=config.retrieval.business_bonus,
        rerank_alpha=config.retrieval.rerank_alpha,
        rerank_pool=config.retrieval.rerank_pool,
        scenario_overrides={
            scenario: override.to_dict()
            for scenario, override in config.retrieval.scenario_overrides.items()
        },
    )
    rules = build_rule_entries(corpus)
    # ADR-0005：索引身份不只写版本号，也要写 provider 校准向量哈希。评估索引
    # 重建时重算同一哈希即可证明 embedding 未漂移。
    calibration_hash = calibration_vector_sha256(provider)

    retrieval_records: list[RetrievalRecord] = []
    retrieval_predictions: list[dict[str, Any]] = []
    if "retrieval" in config.tasks:
        retrieval_records, retrieval_predictions = run_retrieval(
            capped_retrieval,
            harness=harness,
            rewriter=rewriter,
            mode=config.mode,
        )

    inspection_records: list[InspectionRecord] = []
    inspection_predictions: list[dict[str, Any]] = []
    inspection_stats: dict[str, Any] = {"stopped_reason": None}
    if "inspection" in config.tasks:
        inspection_records, inspection_predictions, inspection_stats = run_inspection(
            capped_inspection,
            rules=rules,
            gateway=gateway,
            mode=config.mode,
        )

    finished = _timestamp()
    stopped_reason = inspection_stats.get("stopped_reason")
    over_budget = (
        config.budget_cny > 0 and tracker.spent_cny > config.budget_cny
    )
    status = "budget_exceeded" if stopped_reason or over_budget else "succeeded"

    _assert_citations(inspection_records)
    retrieval_summary = summarize_retrieval(retrieval_records)
    rules_meta = {
        entry.rule_code: {"severity": entry.severity} for entry in rules
    }
    inspection_by_variant = {
        variant: summarize_inspection(
            [record for record in inspection_records if record.variant == variant],
            rules=rules_meta,
        )
        for variant in INSPECTION_VARIANTS
        if any(record.variant == variant for record in inspection_records)
    }
    inspection_comparison = {
        variant: {
            "macro_f1": summary["macro_f1"],
            "high_risk_recall": summary["high_risk_recall"]["recall"],
            "evidence_localization_accuracy": summary["evidence_localization"][
                "accuracy"
            ],
            "system_failures": summary["system_failures"],
        }
        for variant, summary in inspection_by_variant.items()
    }

    primary_retrieval = (
        retrieval_summary["by_variant"].get(PRIMARY_RETRIEVAL_VARIANT)
        if "by_variant" in retrieval_summary
        else retrieval_summary
    )
    primary_inspection = inspection_by_variant.get(PRIMARY_INSPECTION_VARIANT, {})
    measured = {
        "retrieval": {
            "variant": PRIMARY_RETRIEVAL_VARIANT,
            "recall_at_5": primary_retrieval.get("overall", {}).get("recall_at_5"),
            "recall_at_5_by_scenario": {
                scenario: block["recall_at_5"]
                for scenario, block in primary_retrieval.get("by_scenario", {}).items()
            },
            "mrr": primary_retrieval.get("overall", {}).get("mrr"),
            "no_answer_handling_rate": primary_retrieval.get(
                "no_answer_subset", {}
            ).get("handling_rate"),
        },
        "inspection": {
            "variant": PRIMARY_INSPECTION_VARIANT,
            "macro_f1": primary_inspection.get("macro_f1"),
            "high_risk_recall": primary_inspection.get("high_risk_recall", {}).get(
                "recall"
            ),
            "evidence_localization_accuracy": primary_inspection.get(
                "evidence_localization", {}
            ).get("accuracy"),
        },
        "citation_validity": 1.0,
    }

    price_entry = (
        config.price_for(config.model_provider, config.model_name)
        if config.mode == "live"
        else None
    )
    metrics: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": resolved_run_id,
            "task": "m6_eval",
            "mode": config.mode,
            "model_mode": config.model_mode,
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "code_sha": _code_sha(),
            "split": config.split,
            "seed": config.seed,
            "dataset_id": dataset_manifest["dataset_id"],
            "dataset_version": dataset_manifest["dataset_version"],
            "dataset_manifest_sha256": dataset_manifest["dataset_manifest_sha256"],
            "split_manifest_sha256": dataset_manifest["split_manifest_sha256"],
            "dataset_test_sha256": dataset_manifest["frozen"]["test_split_sha256"],
            "knowledge_snapshot": corpus.knowledge_snapshot(),
            "rule_snapshot": corpus.rule_snapshot(),
            "embedding_version": harness.embedding_version,
            "index_version": harness.index_version,
            "calibration_vector_sha256": calibration_hash,
            "retrieval_options": config.retrieval.to_dict(),
            "model_provider": model_provider,
            "model_version": model_version,
            "prompt_version": "eval-v1:"
            + hashlib.sha256(
                json.dumps(
                    inspection_prompt_components(), sort_keys=True
                ).encode("utf-8")
            ).hexdigest()[:16],
            "annotation": dataset_manifest.get("annotation"),
            "sample": {
                "max_items": config.max_items,
                "retrieval_items": len(capped_retrieval),
                "inspection_items": len(capped_inspection),
                "retrieval_judgments": len(retrieval_records),
                "inspection_judgments": len(inspection_records),
                "retrieval_sample_ids": [item.item_id for item in capped_retrieval],
                "inspection_sample_ids": [item.item_id for item in capped_inspection],
            },
        },
        "retrieval": (
            {**retrieval_summary, "primary_variant": PRIMARY_RETRIEVAL_VARIANT}
            if retrieval_records
            else {"skipped": True}
        ),
        "inspection": (
            {
                "by_variant": inspection_by_variant,
                "comparison": inspection_comparison,
                "primary_variant": PRIMARY_INSPECTION_VARIANT,
                "citation": {
                    "validity": 1.0,
                    "validity_by_construction": True,
                    "citations_accepted": inspection_stats.get("citations_accepted", 0),
                    "invalid_citations_dropped": inspection_stats.get(
                        "invalid_citations_dropped", 0
                    ),
                    "references_seen": inspection_stats.get("citation_refs_seen", 0),
                },
                "stopped_reason": inspection_stats.get("stopped_reason"),
            }
            if inspection_records
            else {"skipped": True}
        ),
        "citation": {
            "validity": 1.0,
            "validity_by_construction": True,
            "citations_accepted": inspection_stats.get("citations_accepted", 0),
            "invalid_citations_dropped": inspection_stats.get(
                "invalid_citations_dropped", 0
            ),
            "support": {
                "status": "NOT_MEASURED",
                "reason": (
                    "没有人工标注的语义支持标签；引用合法性（本项 100%）与"
                    "语义支持度分开报告（计划 §7），不得用合法性替代支持度"
                ),
            },
        },
        "latency_ms": {
            "retrieval": {
                variant: block.get("latency_ms")
                for variant, block in (
                    retrieval_summary.get("by_variant", {})
                ).items()
            }
            or {"skipped": True},
            "inspection": {
                variant: summary.get("latency_ms")
                for variant, summary in inspection_by_variant.items()
            }
            or {"skipped": True},
            "model_calls": {
                "count": len(gateway.call_log),
                "p50": percentile(
                    [call.elapsed_ms for call in gateway.call_log], 50
                ),
                "p95": percentile(
                    [call.elapsed_ms for call in gateway.call_log], 95
                ),
            },
        },
        "cost": {
            "estimated": True,
            "method": (
                "估算：CJK 每字 1 token、其他非空白字符每 4 字符 1 token；"
                "成本 = token/1e6 × 配置单价"
            ),
            "cny": round(tracker.spent_cny, 8),
            "token_usage_estimated": {
                "input": tracker.usage.input_tokens,
                "output": tracker.usage.output_tokens,
            },
            "price": (
                {
                    "provider": price_entry.provider,
                    "model": price_entry.model,
                    "input_cny_per_mtok": price_entry.input_cny_per_mtok,
                    "output_cny_per_mtok": price_entry.output_cny_per_mtok,
                    "source": price_entry.source,
                    "as_of": price_entry.as_of,
                }
                if price_entry is not None
                else None
            ),
            "budget_cny": config.budget_cny if config.mode == "live" else None,
            "note": (
                "replay 没有真实模型调用，成本固定为 0；token 仍按同一方法估算"
                if config.mode == "replay"
                else "成本为按价目表与估算 token 计算，不是供应商账单"
            ),
        },
        "acceptance": {
            "thresholds": THRESHOLDS,
            "measured": measured,
            "gates_applied": False,
            "reason": (
                "replay 只验证流水线，分数由确定性词法 embedding 与替身模型产生，"
                "不能用于质量门槛判定"
                if config.mode == "replay"
                else (
                    "live 仅覆盖 max_items 子集，样本量不足以判定质量门槛；"
                    "本报告只报告实测值"
                )
            ),
            "gates_source": "docs/plan/bank-customer-service-plan.md §7",
        },
        "failure_cases": {
            "retrieval": _trim_failures(_retrieval_failures(retrieval_records)),
            "inspection": _trim_failures(_inspection_failures(inspection_records)),
            "note": (
                f"每类最多展示 {_MAX_FAILURE_CASES} 条；完整逐条结果见 predictions_path"
            ),
        },
        "database_note": (
            "本运行读冻结数据集绑定的合成语料快照，不直连应用数据库；"
            "应用运行时快照来自 DB。当前基础设施为过渡性本机容器，"
            "ADR-0006（Ubuntu 唯一实例 + SSH 隧道）未实现，详见 docs/eval/README.md"
        ),
    }

    prompt_components = inspection_prompt_components()
    prompt_version = metrics["run"]["prompt_version"]
    hardware_summary, hardware = _hardware()
    context = RunContext(
        run_id=resolved_run_id,
        task="m6_eval",
        mode=config.mode,
        model_mode=config.model_mode,
        dataset_id=dataset_manifest["dataset_id"],
        dataset_version=dataset_manifest["dataset_version"],
        dataset_manifest_sha256=dataset_manifest["dataset_manifest_sha256"],
        split=config.split,
        split_manifest_sha256=dataset_manifest["split_manifest_sha256"],
        dataset_test_sha256=dataset_manifest["frozen"]["test_split_sha256"],
        knowledge_snapshot=corpus.knowledge_snapshot(),
        rule_snapshot=corpus.rule_snapshot(),
        prompt_version=prompt_version,
        prompt_components=prompt_components,
        model_provider=model_provider,
        model_version=model_version,
        embedding_version=harness.embedding_version,
        index_version=harness.index_version,
        seed=config.seed,
        code_sha=_code_sha(),
        worktree_clean=_worktree_clean(),
        hardware_summary=hardware_summary,
        hardware=hardware,
        started_at=started,
        finished_at=finished,
        status=status,
        notes={
            "sample_cap": config.max_items,
            "embedding_provider": config.embedding_provider,
            "calibration_vector_sha256": calibration_hash,
            "retrieval_options": config.retrieval.to_dict(),
            "budget_cny": config.budget_cny if config.mode == "live" else None,
            "stop_reason": stopped_reason,
            "replay_notice": (
                "replay：确定性词法 embedding + 替身模型，只验证流水线"
                if config.mode == "replay"
                else None
            ),
        },
    )
    manifest = build_run_manifest(context=context)
    predictions = retrieval_predictions + inspection_predictions
    paths = write_run_artifacts(
        artifact_root=config.artifact_root,
        manifest=manifest,
        metrics=metrics,
        predictions=predictions,
    )
    stored = json.loads((Path(paths["run_dir"]) / "manifest.json").read_text("utf-8"))
    validate_manifest(stored)
    return EvalRun(
        run_id=resolved_run_id,
        status=status,
        manifest=stored,
        metrics=metrics,
        predictions=predictions,
        paths=paths,
    )
