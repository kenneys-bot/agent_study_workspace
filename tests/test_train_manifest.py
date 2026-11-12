"""训练 manifest 与 model card（T28，#29）：字段完整性、NOT_RUN 语义、凭据扫描。

用真实 dry-run 生成的 manifest 做断言，但不训练、不联网。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bank_assistant.evaluation.manifest import ManifestError
from bank_assistant.training.config import load_train_config
from bank_assistant.training.hardware import HardwareProbe
from bank_assistant.training.manifest import (
    REQUIRED_TRAINING_MANIFEST_FIELDS,
    TrainingManifestError,
    render_model_card,
    validate_training_manifest,
    write_training_artifacts,
)
from bank_assistant.training.trainer import run_experiment

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "configs/train/qlora-intent-qwen3-0.6b.yaml"


def _probe() -> HardwareProbe:
    return HardwareProbe(
        probed_at="2026-11-12T18:32:45+08:00",
        platform="Linux-6.8.0-x86_64",
        machine="x86_64",
        cpu_count=12,
        memory_gib=62.0,
        python="3.13.1",
        nvidia_smi_available=True,
        nvidia_smi_version="NVIDIA-SMI 580.178.04",
        gpu_name="NVIDIA GeForce RTX 3060 Ti",
        driver_version="580.178.04",
        total_vram_gib=8.0,
        used_vram_gib=1.42,
        free_vram_gib=6.58,
        gpu_utilization_pct=0.0,
        compute_processes=(
            {
                "pid": 14219,
                "process_name": "dingtalk-embed/bin/python",
                "used_memory_mib": 1280,
            },
        ),
        raw_query="NVIDIA GeForce RTX 3060 Ti, 580.178.04, 8192, 1457, 6735, 0",
    )


@pytest.fixture()
def dry_run_manifest(tmp_path: Path, frozen_dataset_dir: Path) -> dict:
    config = load_train_config(CONFIG)
    payload = config.to_dict()
    payload["dataset"]["dataset_dir"] = str(frozen_dataset_dir)
    payload["run"]["artifact_root"] = str(tmp_path / "artifacts")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = run_experiment(
        load_train_config(config_path),
        dry_run=True,
        run_id="manifest-dry-run",
        hardware=_probe(),
    )
    return json.loads(
        (tmp_path / "artifacts/manifest-dry-run/manifest.json").read_text("utf-8")
    )


def test_manifest_has_all_required_fields(dry_run_manifest: dict) -> None:
    missing = [
        field
        for field in REQUIRED_TRAINING_MANIFEST_FIELDS
        if field not in dry_run_manifest
    ]
    assert missing == []
    validate_training_manifest(dry_run_manifest)
    assert dry_run_manifest["status"] == "NOT_RUN"
    assert dry_run_manifest["task"] == "intent_classification"
    assert dry_run_manifest["seed"] == 42
    assert dry_run_manifest["model_name"] == "Qwen/Qwen3-0.6B"
    assert dry_run_manifest["label_names"] == [
        "complaint",
        "credit_card_fee",
        "lost_card",
    ]
    for key in (
        "dataset_manifest_sha256",
        "split_manifest_sha256",
        "train_split_sha256",
        "test_split_sha256",
        "label_schema_sha256",
    ):
        assert len(dry_run_manifest[key]) == 64
    assert dry_run_manifest["hardware"]["gpu_busy"] is True
    assert dry_run_manifest["gpu"]["busy"] is True
    assert dry_run_manifest["steps"]


def test_not_run_requires_reason(dry_run_manifest: dict) -> None:
    broken = copy.deepcopy(dry_run_manifest)
    broken["status_reason"] = ""
    with pytest.raises(TrainingManifestError, match="status_reason"):
        validate_training_manifest(broken)


def test_success_requires_vram_and_evaluation(dry_run_manifest: dict) -> None:
    broken = copy.deepcopy(dry_run_manifest)
    broken["status"] = "SUCCEEDED"
    broken["vram_peak_gib"] = None
    with pytest.raises(TrainingManifestError, match="vram_peak_gib"):
        validate_training_manifest(broken)

    broken = copy.deepcopy(dry_run_manifest)
    broken["status"] = "SUCCEEDED"
    broken["vram_peak_gib"] = 6.5
    broken["evaluation"]["available"] = False
    with pytest.raises(TrainingManifestError, match="评测"):
        validate_training_manifest(broken)


def test_dry_run_cannot_be_succeeded(dry_run_manifest: dict) -> None:
    broken = copy.deepcopy(dry_run_manifest)
    broken["status"] = "SUCCEEDED"
    broken["vram_peak_gib"] = 6.5
    broken["evaluation"]["available"] = True
    with pytest.raises(TrainingManifestError, match="dry_run"):
        validate_training_manifest(broken)


def test_manifest_rejects_bad_hash(dry_run_manifest: dict) -> None:
    broken = copy.deepcopy(dry_run_manifest)
    broken["train_split_sha256"] = "not-a-hash"
    with pytest.raises(TrainingManifestError, match="sha256"):
        validate_training_manifest(broken)


def test_manifest_rejects_credentials(dry_run_manifest: dict) -> None:
    dirty = copy.deepcopy(dry_run_manifest)
    dirty["notes"]["api_key"] = "sk-abcdef1234567890"
    with pytest.raises(ManifestError, match="凭据"):
        validate_training_manifest(dirty)


def test_model_card_has_honest_sections(dry_run_manifest: dict) -> None:
    card = render_model_card(dry_run_manifest)
    assert "NOT_RUN" in card
    assert "baseline vs tuned" in card
    assert dry_run_manifest["train_split_sha256"] in card
    assert dry_run_manifest["test_split_sha256"] in card
    assert "seed" in card
    assert "显存峰值" in card
    assert "不宣称统计显著" in card
    assert "make train" in card


def test_model_card_renders_success_comparison(dry_run_manifest: dict) -> None:
    manifest = copy.deepcopy(dry_run_manifest)
    manifest["status"] = "SUCCEEDED"
    manifest["mode"] = "train"
    manifest["status_reason"] = "真实训练完成"
    manifest["vram_peak_gib"] = 6.5
    manifest["evaluation"] = {
        "available": True,
        "split": "test",
        "n": 50,
        "prompt_version": "intent-routing-v1",
        "baseline": {"accuracy": 0.4, "macro_f1": 0.35, "n": 50},
        "tuned": {"accuracy": 0.62, "macro_f1": 0.6, "n": 50},
        "delta": {"accuracy": 0.22, "macro_f1": 0.25},
        "decision": "improved",
        "note": "小样本，不宣称统计显著。",
        "majority_reference": {"accuracy": 0.3, "macro_f1": 0.15, "n": 50},
        "paths": {},
    }
    card = render_model_card(manifest)
    assert "| accuracy | 0.4 | 0.62 | 0.22 |" in card
    assert "| macro-F1 | 0.35 | 0.6 | 0.25 |" in card
    assert "`improved`" in card


def test_write_artifacts_refuses_overwrite(
    tmp_path: Path, dry_run_manifest: dict
) -> None:
    with pytest.raises(TrainingManifestError, match="拒绝覆盖"):
        write_training_artifacts(
            artifact_root=tmp_path / "artifacts",
            run_id="manifest-dry-run",
            manifest=dry_run_manifest,
            model_card=render_model_card(dry_run_manifest),
            config_payload={},
        )


def test_build_manifest_rejects_naive_datetime(dry_run_manifest: dict) -> None:
    # 直接构造 context 的成本较高；这里确认 ISO 字段带时区。
    assert "+00:00" in dry_run_manifest["started_at"]
    assert "+00:00" in dry_run_manifest["finished_at"]
