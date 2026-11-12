#!/usr/bin/env python3
"""M6 固定评测 CLI（T27，#28）。

用法::

    python -m scripts.run_eval --config configs/eval/replay.yaml
    python -m scripts.run_eval --config configs/eval/live-subset.yaml

`make eval EVAL_CONFIG=...` 是仓库唯一入口。运行前先 ``make dataset-build``
（或确认 ``ARTIFACT_ROOT`` 下已有冻结数据集）。产物写入
``<ARTIFACT_ROOT>/evals/<run_id>/``，stdout 只打印不含秘密的摘要 JSON。

退出码：0 = succeeded；1 = failed/budget_exceeded/blocked；2 = 配置或前置条件
错误（不会写出伪造的成功报告）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bank_assistant.evaluation.config import EvalConfigError, load_eval_config
from bank_assistant.evaluation.errors import EvalError
from bank_assistant.evaluation.runner import run_eval

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_eval",
        description=(
            "M6 固定评测：冻结 test split 上的检索/质检基线对比；"
            "replay 无网络无成本，live 需显式样本与预算上限。"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="EVAL_CONFIG（.json/.yaml/.yml）",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="覆盖运行 ID（默认由模式/数据集/时间生成，不能含路径分隔符）",
    )
    return parser


def _summary(run) -> dict:
    retrieval = run.metrics.get("retrieval", {})
    inspection = run.metrics.get("inspection", {})
    by_variant = retrieval.get("by_variant", {})
    return {
        "run_id": run.run_id,
        "status": run.status,
        "mode": run.metrics.get("run", {}).get("mode"),
        "paths": run.paths,
        "dataset": {
            key: run.metrics.get("run", {}).get(key)
            for key in (
                "dataset_id",
                "dataset_version",
                "split",
                "dataset_manifest_sha256",
                "dataset_test_sha256",
                "knowledge_snapshot",
                "rule_snapshot",
                "index_version",
                "embedding_version",
                "model_provider",
                "model_version",
            )
        },
        "retrieval": {
            variant: {
                "recall_at_5": block.get("overall", {}).get("recall_at_5"),
                "mrr": block.get("overall", {}).get("mrr"),
                "no_answer_handling_rate": block.get("no_answer_subset", {}).get(
                    "handling_rate"
                ),
            }
            for variant, block in by_variant.items()
        },
        "inspection": inspection.get("comparison", {}),
        "citation": run.metrics.get("citation"),
        "cost": {
            key: run.metrics.get("cost", {}).get(key)
            for key in ("estimated", "cny", "token_usage_estimated")
        },
        "error": run.metrics.get("error"),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_eval_config(args.config)
        run = run_eval(config, run_id=args.run_id)
    except (EvalConfigError, EvalError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc), "config": str(args.config)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(_summary(run), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if run.status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
