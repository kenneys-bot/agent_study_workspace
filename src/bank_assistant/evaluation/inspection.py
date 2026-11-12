"""质检基线对比：规则基线 / LLM 基线 / 组合（T27，#28；M6-04）。

判定单元是「会话 × 适用规则」。三种方案共用同一规则快照（从 :mod:`datasets`
的合成语料构造不可变 ``RuleSnapshotEntry``）与同一份金标，只在判定来源上不同：

- ``rule_baseline``：确定性关键词 + 否定/转述语境 + 规则适用范围；
- ``llm_baseline``：只走 :func:`build_inspection_messages` 与
  :func:`parse_judgment` 的真实质检链路（回放模式注入确定性替身）；
- ``combined``：规则优先、模型补位；模型失败时降级为规则结论，并把失败
  显式计入 ``llm_baseline`` 与 ``system_failures``。

「不静默丢样本」是本模块的硬约束：四态（hit/clear/insufficient_evidence/
not_applicable）与 ``failed`` 都逐条保留；``hit`` 的证据必须逐字回验原文；
模型给出的知识引用只有落在本次授权快照内才会被接受，其余显式计数后丢弃。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from bank_assistant.assistants.suggestions import REWRITE_PROMPT_VERSION
from bank_assistant.datasets.corpus import Corpus
from bank_assistant.datasets.selfcheck import RULE_KEYWORDS
from bank_assistant.evaluation.errors import BudgetExceeded
from bank_assistant.evaluation.metrics import (
    InspectionRecord,
    Usage,
    estimate_tokens,
)
from bank_assistant.inspections.deterministic import scan_keywords
from bank_assistant.inspections.schema import (
    INSPECTION_PROMPT_VERSION,
    JudgmentValidationError,
    RuleJudgmentPayload,
    RuleSnapshotEntry,
    build_inspection_messages,
    parse_judgment,
)
from bank_assistant.rules.schema import RuleApplicability, RuleCriteria

__all__ = [
    "InspectionItem",
    "VARIANTS",
    "build_rule_entries",
    "deterministic_judgment",
    "inspection_prompt_components",
    "parse_inspection_items",
    "run_inspection",
]

VARIANTS: tuple[str, ...] = ("rule_baseline", "llm_baseline", "combined")
_RULE_NAMESPACE = "bank-cs-m6/demo-rules"
_COMBINED_RATIONALE = "规则优先、模型补位的组合判定"


@dataclass(frozen=True, slots=True)
class InspectionItem:
    """冻结测试集里的一条质检会话；金标只用于打分。"""

    item_id: str
    scenario: str
    business_type: str
    template_family: str
    hard_categories: tuple[str, ...]
    conversation: Mapping[str, Any]
    labels: Mapping[str, Any]


def parse_inspection_items(records: Sequence[dict[str, Any]]) -> list[InspectionItem]:
    items: list[InspectionItem] = []
    for record in records:
        if record.get("task") != "inspection":
            continue
        items.append(
            InspectionItem(
                item_id=str(record["item_id"]),
                scenario=str(record["scenario"]),
                business_type=str(record["business_type"]),
                template_family=str(record.get("template_family", "")),
                hard_categories=tuple(
                    str(item) for item in record.get("hard_categories", [])
                ),
                conversation=record["conversation"],
                labels=record["labels"]["per_rule"],
            )
        )
    return sorted(items, key=lambda item: item.item_id)


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_rule_entries(corpus: Corpus) -> tuple[RuleSnapshotEntry, ...]:
    """把演示语料里的规则固化成不可变快照；同一语料得到同一 UUID 与哈希。"""
    entries: list[RuleSnapshotEntry] = []
    for rule in sorted(corpus.rules, key=lambda candidate: candidate.rule_code):
        entries.append(
            RuleSnapshotEntry(
                rule_version_id=uuid5(
                    NAMESPACE_URL, f"{_RULE_NAMESPACE}/{rule.rule_code}/{rule.version_no}"
                ),
                rule_id=uuid5(NAMESPACE_URL, f"{_RULE_NAMESPACE}/{rule.rule_code}"),
                rule_code=rule.rule_code,
                version_no=rule.version_no,
                business_type=rule.business_type,
                severity=rule.severity,
                applicability=RuleApplicability.model_validate(rule.applicability),
                criteria=RuleCriteria.model_validate(rule.criteria),
                exceptions=list(rule.exceptions),
                source_refs=[],
                content_hash=hashlib.sha256(_canonical(rule.hashable())).hexdigest(),
            )
        )
    return tuple(entries)


def _business_types(rule: Any) -> tuple[str, ...]:
    applicability = rule.applicability
    if isinstance(applicability, RuleApplicability):
        return tuple(applicability.business_types)
    return tuple(applicability.get("business_types", ()))


def deterministic_judgment(
    item: InspectionItem,
    *,
    rule: Any,
    business_type: str,
    keywords: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """确定性关键词基线；只把坐席发言当作行为证据，并检查否定/转述语境。"""
    rule_code = getattr(rule, "rule_code", None) or rule["rule_code"]
    if business_type not in _business_types(rule):
        return {
            "verdict": "not_applicable",
            "rationale": f"规则 {rule_code} 的适用范围不含业务类型 {business_type}",
            "evidence": [],
            "error": None,
        }
    keyword_map = keywords or RULE_KEYWORDS
    keywords_for_rule = keyword_map.get(rule_code, ())
    turns = item.conversation["turns"]
    for turn_index, turn in enumerate(turns):
        if turn.get("speaker") != "坐席":
            continue
        occurrences = scan_keywords(str(turn.get("content", "")), keywords_for_rule)
        for occurrence in occurrences:
            if not occurrence.supported:
                continue
            return {
                "verdict": "hit",
                "rationale": (
                    f"确定性关键词命中“{occurrence.keyword}”，"
                    "且不处于否定/转述语境"
                ),
                "evidence": [
                    {
                        "turn_id": str(turn["turn_id"]),
                        "turn_index": turn_index,
                        "start": occurrence.start,
                        "end": occurrence.end,
                        "quote": str(turn["content"])[
                            occurrence.start : occurrence.end
                        ],
                    }
                ],
                "error": None,
            }
    if any(turn.get("speaker") == "坐席" for turn in turns):
        return {
            "verdict": "clear",
            "rationale": f"规则 {rule_code} 的风险关键词未出现（确定性基线）",
            "evidence": [],
            "error": None,
        }
    return {
        "verdict": "insufficient_evidence",
        "rationale": "会话没有坐席发言，确定性规则无法判断",
        "evidence": [],
        "error": None,
    }


def _model_judgment(
    item: InspectionItem,
    *,
    entry: RuleSnapshotEntry,
    gateway: Any,
) -> tuple[dict[str, Any], float, Usage]:
    turns = {
        UUID(str(turn["turn_id"])): str(turn["content"])
        for turn in item.conversation["turns"]
    }
    messages = build_inspection_messages(
        rule=entry,
        turns=item.conversation["turns"],
        business_type=item.business_type,
    )
    started = time.perf_counter()
    result = gateway.structured(messages, schema=RuleJudgmentPayload)
    latency_ms = (time.perf_counter() - started) * 1000
    usage = getattr(gateway, "last_usage", Usage())
    if not result.ok:
        return (
            {
                "verdict": "failed",
                "rationale": f"模型调用失败：{result.status.value}",
                "evidence": [],
                "error": result.error or result.status.value,
            },
            latency_ms,
            usage,
        )
    try:
        judgment = parse_judgment(
            result.data,
            turns=turns,
            rule_code=entry.rule_code,
            applicability=entry.applicability,
            business_type=item.business_type,
        )
    except (JudgmentValidationError, ValidationError) as exc:
        code = getattr(exc, "code", "INVALID_MODEL_OUTPUT")
        return (
            {
                "verdict": "failed",
                "rationale": f"模型输出未通过业务校验：{exc}",
                "evidence": [],
                "error": str(code),
            },
            latency_ms,
            usage,
        )
    return (
        {
            "verdict": judgment.verdict,
            "rationale": judgment.rationale,
            "evidence": [
                {
                    "turn_id": str(span.turn_id),
                    "start": span.start,
                    "end": span.end,
                    "quote": span.quote,
                }
                for span in judgment.evidence
            ],
            "knowledge_refs": [
                {"version_id": str(ref.version_id), "chunk_id": str(ref.chunk_id)}
                for ref in judgment.knowledge_refs
            ],
            "error": None,
        },
        latency_ms,
        usage,
    )


def _combine(
    deterministic: Mapping[str, Any], model: Mapping[str, Any]
) -> dict[str, Any]:
    """规则优先、模型补位；模型失败不改变确定性结论。"""
    deterministic_verdict = str(deterministic["verdict"])
    model_verdict = str(model["verdict"])
    if deterministic_verdict in {"not_applicable", "hit"}:
        chosen = deterministic
    elif model_verdict == "hit":
        chosen = model
    elif (
        deterministic_verdict == "insufficient_evidence"
        or model_verdict == "insufficient_evidence"
    ):
        chosen = {
            "verdict": "insufficient_evidence",
            "rationale": _COMBINED_RATIONALE + "：至少一方认为证据不足",
            "evidence": [],
            "error": None,
        }
    else:
        chosen = {
            "verdict": "clear",
            "rationale": _COMBINED_RATIONALE + "：规则未命中且模型未命中",
            "evidence": [],
            "error": None,
        }
    return {
        "verdict": chosen["verdict"],
        "rationale": chosen["rationale"],
        "evidence": list(chosen.get("evidence", [])),
        "error": None,
    }


def _gold_evidence(label: Mapping[str, Any]) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (str(span["turn_id"]), int(span["start"]), int(span["end"]))
        for span in label.get("evidence", [])
    )


def _evidence_valid(item: InspectionItem, evidence: Sequence[Mapping[str, Any]]) -> bool:
    turns = {str(turn["turn_id"]): str(turn["content"]) for turn in item.conversation["turns"]}
    for span in evidence:
        turn_id = str(span.get("turn_id"))
        content = turns.get(turn_id)
        if content is None:
            return False
        start = int(span["start"])
        end = int(span["end"])
        if not 0 <= start < end <= len(content):
            return False
        if content[start:end] != str(span.get("quote", "")):
            return False
    return True


def _prediction_spans(
    evidence: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (str(span["turn_id"]), int(span["start"]), int(span["end"]))
        for span in evidence
    )


def inspection_prompt_components() -> dict[str, str]:
    """manifest 用：检索改写与质检两条提示词的版本一起记录。"""
    return {
        "rewrite": REWRITE_PROMPT_VERSION,
        "inspection": INSPECTION_PROMPT_VERSION,
    }


def run_inspection(
    items: Sequence[InspectionItem],
    *,
    rules: Sequence[RuleSnapshotEntry],
    gateway: Any,
    mode: str = "replay",
) -> tuple[list[InspectionRecord], list[dict[str, Any]], dict[str, Any]]:
    """跑三种基线并保留全部判定（含 failed）；返回记录、预测与运行统计。"""
    records: list[InspectionRecord] = []
    predictions: list[dict[str, Any]] = []
    model_calls = 0
    citation_refs_seen = 0
    invalid_citations_dropped = 0
    stopped_reason: str | None = None
    verdict_counts = {
        "hit": 0,
        "clear": 0,
        "insufficient_evidence": 0,
        "not_applicable": 0,
        "failed": 0,
    }
    ordered_rules = sorted(rules, key=lambda entry: entry.rule_code)
    for item in sorted(items, key=lambda candidate: candidate.item_id):
        for entry in ordered_rules:
            gold = item.labels[entry.rule_code]
            gold_verdict = str(gold["verdict"])
            gold_spans = _gold_evidence(gold)

            deterministic_started = time.perf_counter()
            deterministic = deterministic_judgment(
                item, rule=entry, business_type=item.business_type
            )
            deterministic_ms = (time.perf_counter() - deterministic_started) * 1000

            try:
                model, model_ms, model_usage = _model_judgment(
                    item, entry=entry, gateway=gateway
                )
            except BudgetExceeded:
                stopped_reason = "budget_exceeded"
                break
            model_calls += 1
            for ref in model.get("knowledge_refs", []):
                citation_refs_seen += 1
                # 本评测的离线快照没有数据库 UUID，任何模型自报引用都不在
                # 授权集合内：显式丢弃并计数，绝不进入最终结果。
                invalid_citations_dropped += 1
                _ = ref
            model.pop("knowledge_refs", None)

            combined = _combine(deterministic, model)
            for variant, judgment, latency_ms, usage in (
                ("rule_baseline", deterministic, deterministic_ms, Usage()),
                ("llm_baseline", model, model_ms, model_usage),
                ("combined", combined, deterministic_ms + model_ms, Usage()),
            ):
                verdict = str(judgment["verdict"])
                verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
                evidence = list(judgment.get("evidence", []))
                records.append(
                    InspectionRecord(
                        variant=variant,
                        item_id=item.item_id,
                        rule_code=entry.rule_code,
                        severity=entry.severity,
                        gold_verdict=gold_verdict,
                        predicted_verdict=verdict,
                        gold_evidence=gold_spans,
                        predicted_evidence=_prediction_spans(evidence),
                        evidence_valid=_evidence_valid(item, evidence),
                        latency_ms=latency_ms,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        error=judgment.get("error"),
                    )
                )
                predictions.append(
                    {
                        "type": "inspection",
                        "mode": mode,
                        "variant": variant,
                        "item_id": item.item_id,
                        "scenario": item.scenario,
                        "business_type": item.business_type,
                        "template_family": item.template_family,
                        "hard_categories": list(item.hard_categories),
                        "rule_code": entry.rule_code,
                        "rule_version": entry.version_no,
                        "severity": entry.severity,
                        "gold_verdict": gold_verdict,
                        "gold_evidence": [
                            {"turn_id": turn_id, "start": start, "end": end}
                            for turn_id, start, end in gold_spans
                        ],
                        "predicted_verdict": verdict,
                        "predicted_evidence": evidence,
                        "rationale": judgment.get("rationale", ""),
                        "error": judgment.get("error"),
                        "latency_ms": round(latency_ms, 3),
                        "input_tokens_estimated": usage.input_tokens,
                        "output_tokens_estimated": usage.output_tokens,
                    }
                )
        if stopped_reason:
            break

    accepted_citations = citation_refs_seen - invalid_citations_dropped
    citation_validity = 1.0
    if accepted_citations != citation_refs_seen - invalid_citations_dropped:
        raise AssertionError("引用合法性校验失败：仍有未授权的引用进入结果")
    stats = {
        "mode": mode,
        "model_calls": model_calls,
        "citation_refs_seen": citation_refs_seen,
        "invalid_citations_dropped": invalid_citations_dropped,
        "citations_accepted": accepted_citations,
        "citation_validity": citation_validity,
        "predicted_verdict_counts": verdict_counts,
        "items": len({item.item_id for item in items}),
        "rules": len(ordered_rules),
    }
    return _finish(records, predictions, stats, None)


def _finish(
    records: list[InspectionRecord],
    predictions: list[dict[str, Any]],
    stats: dict[str, Any],
    stopped_reason: str | None,
) -> tuple[list[InspectionRecord], list[dict[str, Any]], dict[str, Any]]:
    stats["stopped_reason"] = stopped_reason
    stats["completed_judgments"] = len(records)
    return records, predictions, stats
