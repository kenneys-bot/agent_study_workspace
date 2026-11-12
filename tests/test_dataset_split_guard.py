"""训练/评测数据拆分守卫（T26，#27；M6-02/M6-06）。

冻结测试集的约束只有在脚本真的拒绝时才成立：训练只允许 ``train``，评测只
允许 ``dev``/``test``。本测试只打公开 seam ``require_split_for`` 与模块 CLI。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bank_assistant.datasets.split_guard import (
    SplitLeakageError,
    SplitPurpose,
    require_split_for,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_training_accepts_only_train_split() -> None:
    assert require_split_for(SplitPurpose.TRAIN, "train") == "train"
    with pytest.raises(SplitLeakageError):
        require_split_for(SplitPurpose.TRAIN, "dev")
    with pytest.raises(SplitLeakageError):
        require_split_for(SplitPurpose.TRAIN, "test")


def test_evaluation_accepts_only_held_out_splits() -> None:
    assert require_split_for(SplitPurpose.EVAL, "dev") == "dev"
    assert require_split_for(SplitPurpose.EVAL, "test") == "test"
    with pytest.raises(SplitLeakageError):
        require_split_for(SplitPurpose.EVAL, "train")


def test_unknown_purpose_and_split_fail_loudly() -> None:
    with pytest.raises(SplitLeakageError):
        require_split_for("fine-tune", "train")
    with pytest.raises(SplitLeakageError):
        require_split_for(SplitPurpose.TRAIN, "TEST")


def test_guard_cli_exits_nonzero_for_test_split_training() -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    ok = subprocess.run(
        [sys.executable, "-m", "bank_assistant.datasets.split_guard", "train", "train"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert ok.returncode == 0, ok.stderr
    blocked = subprocess.run(
        [sys.executable, "-m", "bank_assistant.datasets.split_guard", "train", "test"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert blocked.returncode != 0
    assert "train" in blocked.stderr.lower()


def test_frozen_test_eval_refuses_dev_and_train() -> None:
    """T27：正式评测只允许冻结 test；dev 是开发集，train 更不能用于评测。"""
    from bank_assistant.datasets.split_guard import require_frozen_test_split

    assert require_frozen_test_split("test") == "test"
    for split in ("dev", "train", "TEST"):
        with pytest.raises(SplitLeakageError):
            require_frozen_test_split(split)
