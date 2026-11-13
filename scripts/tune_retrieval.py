#!/usr/bin/env python3
"""T36 检索调参入口：**只跑 dev**，复用正式评测的 embedding/改写/指标代码。

冻结 test split 只在最后用 ``make eval`` 报告一次；本脚本因此在参数层面
就拒绝 test，避免调参时误触。它读取 EVAL_CONFIG 的 ``retrieval`` 段，所以
dev 上验证过的开关可以直接被正式评测复用（同一份配置、同一套实现）。

用法::

    python -m scripts.tune_retrieval --scenario complaint
    python -m scripts.tune_retrieval --embedding-text title_chunk \\
        --rerank-alpha 0.2 --dump artifacts/t36-dev-predictions.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from bank_assistant.datasets.corpus import CORPUS_AS_OF, load_corpus
from bank_assistant.evaluation.config import (
    BUSINESS_FILTER_MODES,
    EMBEDDING_TEXT_MODES,
    EvalConfigError,
    load_eval_payload,
    parse_retrieval_options,
)
from bank_assistant.evaluation.embedding import ReplayLexicalEmbedding
from bank_assistant.evaluation.metrics import summarize_retrieval
from bank_assistant.evaluation.replay import ReplayQueryRewriter
from bank_assistant.evaluation.retrieval import (
    RetrievalHarness,
    parse_retrieval_items,
    run_retrieval,
)

DEFAULT_CONFIG = Path("configs/eval/replay.yaml")
DEV_ONLY = "dev"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.tune_retrieval",
        description="T36 dev-only 检索调参：复用 replay embedding/改写/指标，不碰 test。",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--split",
        choices=[DEV_ONLY],
        default=DEV_ONLY,
        help="只允许 dev；冻结 test 由 make eval 单独报告",
    )
    parser.add_argument("--scenario", default=None, help="只跑某个场景（如 complaint）")
    parser.add_argument(
        "--embedding-text",
        choices=sorted(EMBEDDING_TEXT_MODES),
        default=None,
        help="覆盖 retrieval.embedding_text",
    )
    parser.add_argument(
        "--business-filter",
        choices=sorted(BUSINESS_FILTER_MODES),
        default=None,
        help="覆盖 retrieval.business_filter",
    )
    parser.add_argument("--business-bonus", type=float, default=None)
    parser.add_argument("--rerank-alpha", type=float, default=None)
    parser.add_argument("--rerank-pool", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--min-similarity", type=float, default=None)
    parser.add_argument(
        "--variant",
        default="core_intent_filter",
        help="写 --dump 时只导出该变体（默认正式主变体）",
    )
    parser.add_argument("--dump", type=Path, default=None, help="逐条预测 JSONL 路径")
    return parser


def _verify_dev_file(dataset_dir: Path) -> tuple[str, dict[str, Any]]:
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise EvalConfigError(f"找不到数据集 manifest：{manifest_path}")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    dev_path = dataset_dir / "dev.jsonl"
    if not dev_path.is_file():
        raise EvalConfigError(f"找不到 dev split：{dev_path}")
    payload = dev_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    expected = next(
        (
            entry["sha256"]
            for entry in manifest.get("files", [])
            if entry.get("split") == DEV_ONLY
        ),
        None,
    )
    if expected is None or digest != expected:
        raise EvalConfigError(
            f"dev.jsonl 与 manifest 哈希不一致：manifest={expected} 实际={digest}"
        )
    records = [json.loads(line) for line in payload.splitlines()]
    return digest, {"manifest": manifest, "records": records}


def _summary(records, predictions) -> dict[str, Any]:
    summary = summarize_retrieval(records)
    variants = summary.get("by_variant", {})
    out: dict[str, Any] = {}
    for variant, block in variants.items():
        overall = block["overall"]
        out[variant] = {
            "overall": {
                "recall_at_5": overall["recall_at_5"],
                "mrr": overall["mrr"],
                "answerable_items": overall["answerable_items"],
                "unanswerable_items": overall["unanswerable_items"],
            },
            "by_scenario": {
                scenario: {
                    "recall_at_5": scene["recall_at_5"],
                    "mrr": scene["mrr"],
                    "answerable_items": scene["answerable_items"],
                    "unanswerable_items": scene["unanswerable_items"],
                }
                for scenario, scene in block["by_scenario"].items()
            },
            "no_answer_subset": block["no_answer_subset"],
            "false_reject": block["false_reject"],
            "expired_knowledge": block["expired_knowledge"],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = load_eval_payload(args.config)
        dataset_dir = Path(
            str(
                payload.get("dataset_dir")
                or (
                    Path(str(payload.get("artifact_root", "artifacts")))
                    / "data"
                    / str(payload.get("dataset_id", ""))
                    / str(payload.get("dataset_version", ""))
                )
            )
        )
        options = parse_retrieval_options(payload.get("retrieval"))
        if args.embedding_text is not None:
            options = replace(options, embedding_text=args.embedding_text)
        if args.business_filter is not None:
            options = replace(options, business_filter=args.business_filter)
        if args.business_bonus is not None:
            options = replace(options, business_bonus=args.business_bonus)
        if args.rerank_alpha is not None:
            options = replace(options, rerank_alpha=args.rerank_alpha)
        if args.rerank_pool is not None:
            options = replace(options, rerank_pool=args.rerank_pool)

        dev_sha, loaded = _verify_dev_file(dataset_dir)
        items = parse_retrieval_items(loaded["records"])
        if args.scenario:
            items = [item for item in items if item.scenario == args.scenario]
        if not items:
            raise EvalConfigError("筛选后没有检索样本，拒绝产出空报告")
    except EvalConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2

    top_k = args.top_k or int(payload.get("top_k", 5))
    min_similarity = (
        args.min_similarity
        if args.min_similarity is not None
        else float(payload.get("min_similarity", 0.55))
    )
    harness = RetrievalHarness(
        corpus=load_corpus(),
        provider=ReplayLexicalEmbedding(),
        top_k=top_k,
        min_similarity=min_similarity,
        as_of=CORPUS_AS_OF,
        embedding_text=options.embedding_text,
        business_filter=options.business_filter,
        business_bonus=options.business_bonus,
        rerank_alpha=options.rerank_alpha,
        rerank_pool=options.rerank_pool,
        scenario_overrides={
            scenario: override.to_dict()
            for scenario, override in options.scenario_overrides.items()
        },
    )
    records, predictions = run_retrieval(
        items, harness=harness, rewriter=ReplayQueryRewriter(), mode="replay-dev"
    )
    result = {
        "status": "succeeded",
        "split": args.split,
        "dev_sha256": dev_sha,
        "items": len(items),
        "scenario_filter": args.scenario,
        "retrieval": {
            "top_k": top_k,
            "min_similarity": min_similarity,
            "options": options.to_dict(),
            "index_version": harness.index_version,
        },
        "variants": _summary(records, predictions),
    }
    if args.dump is not None:
        selected = [
            prediction
            for prediction in predictions
            if prediction["variant"] == args.variant
        ]
        if args.scenario:
            selected = [
                prediction
                for prediction in selected
                if prediction["scenario"] == args.scenario
            ]
        args.dump.parent.mkdir(parents=True, exist_ok=True)
        with args.dump.open("w", encoding="utf-8") as handle:
            for prediction in selected:
                handle.write(json.dumps(prediction, ensure_ascii=False) + "\n")
        result["dump"] = {"path": str(args.dump), "rows": len(selected)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
