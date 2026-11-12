"""数据集拆分守卫（T26，#27；M6-02/M6-06）。

冻结测试集只有在脚本里真的被拒绝时才成立：训练只允许 ``train``，评测只允许
``dev``/``test``。训练脚本必须调用 :func:`require_split_for`，不能用“默认
train”掩盖传错 split 的情况。

CLI：``python -m bank_assistant.datasets.split_guard <purpose> <split>``，
不合规时非零退出并给出明确原因。
"""

from __future__ import annotations

import argparse
import sys
from enum import StrEnum

__all__ = [
    "ALLOWED_SPLITS",
    "SplitLeakageError",
    "SplitPurpose",
    "main",
    "require_frozen_test_split",
    "require_split_for",
]


class SplitPurpose(StrEnum):
    TRAIN = "train"
    EVAL = "eval"
    # T27 正式评测：M6-04 的基线与门槛必须在冻结 test 上报告；
    # dev 是调参/开发集，不能混进正式评测结论。
    FROZEN_TEST = "frozen_test"


class SplitLeakageError(ValueError):
    """把非训练 split 用于训练（或把 train 用于评测）时显式失败。"""


ALLOWED_SPLITS: dict[str, frozenset[str]] = {
    SplitPurpose.TRAIN.value: frozenset({"train"}),
    SplitPurpose.EVAL.value: frozenset({"dev", "test"}),
    SplitPurpose.FROZEN_TEST.value: frozenset({"test"}),
}


def require_split_for(purpose: str, split: str) -> str:
    """校验用途与 split 的组合；返回 split 以便链式使用。"""
    if purpose not in ALLOWED_SPLITS:
        raise SplitLeakageError(
            f"未知用途 {purpose!r}；只支持 {sorted(ALLOWED_SPLITS)}"
        )
    allowed = ALLOWED_SPLITS[purpose]
    if split not in allowed:
        raise SplitLeakageError(
            f"{purpose} 不允许使用 {split!r} split；允许：{sorted(allowed)}。"
            "冻结测试集不能用于训练，train 也不能当作留出评测集。"
        )
    return split


def require_frozen_test_split(split: str) -> str:
    """正式评测只接受冻结 ``test``；``dev`` 是开发集，``train`` 更不能评测。"""
    if split != "test":
        raise SplitLeakageError(
            f"正式评测只允许冻结 test split，收到 {split!r}；"
            "dev 用于调参、train 用于训练，都不能产出正式评测结论。"
        )
    return split


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验数据集用途与 split 的组合（训练只允许 train）"
    )
    parser.add_argument("purpose", choices=sorted(ALLOWED_SPLITS))
    parser.add_argument("split", choices=["train", "dev", "test", "TEST"])
    args = parser.parse_args(argv)
    try:
        require_split_for(args.purpose, args.split)
    except SplitLeakageError as exc:
        print(f"拒绝：{exc}", file=sys.stderr)
        return 2
    print(f"允许：{args.purpose} 使用 {args.split} split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
