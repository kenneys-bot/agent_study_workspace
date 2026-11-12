"""训练编排（T28，#29）：dry-run、资源闸门、NOT_RUN 报告路径。

全部离线；GPU 用注入的 HardwareProbe 模拟，不调用真实 nvidia-smi，也不训练。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bank_assistant.training.config import TrainConfigError, load_train_config
from bank_assistant.training.hardware import HardwareProbe
from bank_assistant.training.trainer import (
    _encode_training_rows,
    inspect_tokenization,
    run_experiment,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "configs/train/qlora-intent-qwen3-0.6b.yaml"


def _config(tmp_path: Path, dataset_dir: Path) -> Any:
    config = load_train_config(CONFIG)
    payload = config.to_dict()
    payload["dataset"]["dataset_dir"] = str(dataset_dir)
    payload["run"]["artifact_root"] = str(tmp_path / "artifacts")
    payload["run"]["run_id"] = None
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_train_config(path)


def _probe(
    *,
    available: bool,
    busy: bool = False,
    free_gib: float = 6.57,
    total_gib: float = 8.0,
) -> HardwareProbe:
    processes: tuple[dict[str, Any], ...] = ()
    if busy:
        processes = (
            {
                "pid": 14219,
                "process_name": "/home/jack/workspace/dingtalk-embed/bin/python",
                "used_memory_mib": 1280,
            },
        )
    return HardwareProbe(
        probed_at="2026-11-12T18:32:45+08:00",
        platform="Linux-6.8.0-x86_64",
        machine="x86_64",
        cpu_count=12,
        memory_gib=62.0,
        python="3.13.1",
        nvidia_smi_available=available,
        nvidia_smi_version="NVIDIA-SMI 580.178.04" if available else None,
        gpu_name="NVIDIA GeForce RTX 3060 Ti" if available else None,
        driver_version="580.178.04" if available else None,
        total_vram_gib=total_gib if available else None,
        used_vram_gib=round(total_gib - free_gib, 2) if available else None,
        free_vram_gib=free_gib if available else None,
        gpu_utilization_pct=0.0,
        compute_processes=processes,
        error=None if available else "nvidia-smi not found in PATH",
        raw_query=(
            "NVIDIA GeForce RTX 3060 Ti, 580.178.04, 8192, 1457, 6735, 0"
            if available
            else None
        ),
    )


def test_dry_run_writes_not_run_report_without_gpu(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    outcome = run_experiment(
        _config(tmp_path, frozen_dataset_dir),
        dry_run=True,
        run_id="dry-run-no-gpu",
        hardware=_probe(available=False),
    )
    assert outcome.exit_code == 0
    assert outcome.status == "NOT_RUN"
    manifest = json.loads(
        (tmp_path / "artifacts/dry-run-no-gpu/manifest.json").read_text("utf-8")
    )
    assert manifest["status"] == "NOT_RUN"
    assert manifest["mode"] == "dry_run"
    assert "未检测到可用 NVIDIA GPU" in manifest["status_reason"]
    assert manifest["evaluation"]["available"] is False
    assert manifest["evaluation"]["baseline"] is None
    assert manifest["evaluation"]["majority_reference"]["n"] == 50
    assert manifest["vram_peak_gib"] is None
    assert manifest["train_items"] == 202
    assert manifest["test_items"] == 50
    card = (tmp_path / "artifacts/dry-run-no-gpu/model-card.md").read_text("utf-8")
    assert "NOT_RUN" in card
    assert "baseline vs tuned" in card


def test_dry_run_records_busy_gpu_as_not_run(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    outcome = run_experiment(
        _config(tmp_path, frozen_dataset_dir),
        dry_run=True,
        run_id="dry-run-busy-gpu",
        hardware=_probe(available=True, busy=True),
    )
    manifest = outcome.manifest
    assert outcome.status == "NOT_RUN"
    assert "GPU 被其他任务占用" in manifest["status_reason"]
    assert manifest["gpu"]["busy"] is True
    assert manifest["gpu"]["compute_processes"][0]["process_name"].endswith(
        "dingtalk-embed/bin/python"
    )


def test_execute_refuses_busy_gpu_and_writes_not_run(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    outcome = run_experiment(
        _config(tmp_path, frozen_dataset_dir),
        dry_run=False,
        execute=True,
        run_id="execute-busy-gpu",
        hardware=_probe(available=True, busy=True),
    )
    assert outcome.exit_code == 1
    assert outcome.status == "NOT_RUN"
    manifest = json.loads(
        (tmp_path / "artifacts/execute-busy-gpu/manifest.json").read_text("utf-8")
    )
    assert manifest["mode"] == "train"
    assert "GPU 被其他任务占用" in manifest["status_reason"]
    assert manifest["vram_peak_gib"] is None
    steps = {step["name"]: step["status"] for step in manifest["steps"]}
    assert steps["training"] == "SKIPPED"
    assert steps["evaluation"] == "SKIPPED"


def test_execute_without_gpu_is_not_run(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    outcome = run_experiment(
        _config(tmp_path, frozen_dataset_dir),
        dry_run=False,
        execute=True,
        run_id="execute-no-gpu",
        hardware=_probe(available=False),
    )
    assert outcome.exit_code == 1
    assert outcome.status == "NOT_RUN"
    assert "未检测到可用 NVIDIA GPU" in outcome.manifest["status_reason"]


def test_dry_run_tiny_smoke_is_recorded(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    def fake_smoke(**_: Any) -> dict[str, Any]:
        return {
            "status": "SUCCEEDED",
            "loss": 4.5,
            "trainable_params": 1024,
            "device": "cpu",
            "seq_len": 16,
        }

    outcome = run_experiment(
        _config(tmp_path, frozen_dataset_dir),
        dry_run=True,
        run_id="dry-run-tiny",
        tiny_smoke=True,
        tiny_smoke_runner=fake_smoke,
        hardware=_probe(available=False),
    )
    steps = {step["name"]: step for step in outcome.manifest["steps"]}
    assert steps["tiny_cpu_smoke"]["status"] == "SUCCEEDED"
    dry = json.loads(
        (tmp_path / "artifacts/dry-run-tiny/dry-run.json").read_text("utf-8")
    )
    assert dry["tiny_cpu_smoke"]["loss"] == 4.5


def test_tiny_smoke_failure_is_not_silent(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    from bank_assistant.training.trainer import TrainingError

    def failing_smoke(**_: Any) -> dict[str, Any]:
        return {"status": "FAILED", "reason": "boom"}

    with pytest.raises(TrainingError, match="微型 CPU 前反向失败"):
        run_experiment(
            _config(tmp_path, frozen_dataset_dir),
            dry_run=True,
            run_id="dry-run-tiny-fail",
            tiny_smoke=True,
            tiny_smoke_runner=failing_smoke,
            hardware=_probe(available=False),
        )


def test_dry_run_and_execute_are_mutually_exclusive(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    with pytest.raises(TrainConfigError, match="二选一"):
        run_experiment(
            _config(tmp_path, frozen_dataset_dir),
            dry_run=True,
            execute=True,
            hardware=_probe(available=False),
        )


class _FakeTokenizer:
    eos_token_id = 2

    def __call__(self, text: str, add_special_tokens: bool = True) -> dict[str, list[int]]:
        return {"input_ids": [3 + (ord(char) % 40) for char in text]}


def test_encode_masks_prompt_and_keeps_label() -> None:
    from bank_assistant.training.dataset import IntentExample

    examples = (
        IntentExample(
            item_id="x1",
            text="我的卡丢了",
            label="lost_card",
            scenario="lost_card",
            task="retrieval",
        ),
    )
    rows = _encode_training_rows(
        tokenizer=_FakeTokenizer(),
        examples=examples,
        labels=("complaint", "credit_card_fee", "lost_card"),
        max_seq_length=512,
    )
    assert len(rows) == 1
    labels = rows[0]["labels"]
    visible = [value for value in labels if value != -100]
    assert visible and visible[-1] == 2
    assert labels[0] == -100
    assert len(rows[0]["input_ids"]) == len(labels)


def test_encode_drops_rows_truncated_before_label() -> None:
    from bank_assistant.training.dataset import IntentExample

    examples = (
        IntentExample(
            item_id="x2",
            text="很长的问题" * 20,
            label="complaint",
            scenario="complaint",
            task="retrieval",
        ),
    )
    rows = _encode_training_rows(
        tokenizer=_FakeTokenizer(),
        examples=examples,
        labels=("complaint", "lost_card"),
        max_seq_length=32,
    )
    assert rows == []


def test_inspect_tokenization_never_downloads(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    config = _config(tmp_path, frozen_dataset_dir)
    from bank_assistant.training.dataset import load_intent_split

    train = load_intent_split(
        dataset_dir=frozen_dataset_dir,
        dataset_id=config.dataset.dataset_id,
        dataset_version=config.dataset.dataset_version,
        split="train",
        purpose="train",
        task="retrieval",
        text_field="query",
        label_field="business_type",
    )
    result = inspect_tokenization(config, train, train)
    assert result["status"] in {"SKIPPED", "SUCCEEDED"}
    if result["status"] == "SUCCEEDED":
        assert result["max_seq_length"] == 512
        assert result["train"]["n"] == 202
    else:
        assert "拒绝下载" in result["reason"] or "transformers 不可用" in result["reason"]
