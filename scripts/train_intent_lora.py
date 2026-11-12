#!/usr/bin/env python3
"""条件性 4-bit QLoRA 意图分类 CLI（T28，#29）。

用法::

    # 1) 安全默认：dry-run，校验配置/数据/哈希/分词并写 NOT_RUN 报告
    uv run python -m scripts.train_intent_lora \\
        --config configs/train/qlora-intent-qwen3-0.6b.yaml --dry-run

    # 2) 真实训练（必须显式 --execute；需要空闲 8GB GPU 与本地权重）
    uv run python -m scripts.train_intent_lora \\
        --config configs/train/qlora-intent-qwen3-0.6b.yaml --execute

退出码：

- ``0``：dry-run 完成（即使实验状态是 ``NOT_RUN``），或真实训练+评测成功；
- ``1``：真实训练因资源/依赖/执行失败而 ``NOT_RUN``/``FAILED``（报告仍会写出）；
- ``2``：配置、split 守卫、数据集哈希等前置条件错误（不会写伪造成功报告）。

stdout 只打印不含秘密的摘要 JSON。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bank_assistant.datasets.split_guard import SplitLeakageError
from bank_assistant.training.config import TrainConfigError, load_train_config
from bank_assistant.training.dataset import IntentDataError
from bank_assistant.training.manifest import TrainingManifestError
from bank_assistant.training.trainer import (
    TrainingError,
    run_experiment,
    write_manifest_summary,
)

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.train_intent_lora",
        description=(
            "M6 条件任务：4-bit QLoRA 意图分类。默认不训练；"
            "--dry-run 只校验并写 NOT_RUN 报告，--execute 才启动真实训练。"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="TRAIN_CONFIG（.json/.yaml/.yml）",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="不训练：校验配置/数据/哈希/分词（可选微型 CPU 前反向），写 NOT_RUN 报告",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="真实训练：先过 GPU 资源闸门，再跑 QLoRA 与冻结 test 评测",
    )
    parser.add_argument("--run-id", default=None, help="覆盖 run_id（不能含路径分隔符）")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="覆盖配置里的产物根目录",
    )
    parser.add_argument(
        "--tiny-smoke",
        action="store_true",
        help="dry-run 额外做一次随机微型 Qwen2+LoRA 的 CPU 前反向（不下载、不用 GPU）",
    )
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="允许从 Hugging Face 下载模型（默认只用本地缓存；下载前请确认磁盘/网络）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_train_config(args.config)
    except (TrainConfigError, SplitLeakageError, OSError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    try:
        outcome = run_experiment(
            config,
            dry_run=args.dry_run,
            execute=args.execute,
            run_id=args.run_id,
            artifact_root=args.artifact_root,
            tiny_smoke=args.tiny_smoke,
            allow_download=args.allow_download,
        )
    except (IntentDataError, TrainingManifestError, TrainConfigError) as exc:
        print(f"前置条件错误：{exc}", file=sys.stderr)
        return 2
    except TrainingError as exc:
        print(f"训练失败：{exc}", file=sys.stderr)
        return 1
    print(write_manifest_summary(outcome.manifest))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
