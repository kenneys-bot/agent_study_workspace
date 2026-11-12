"""训练 CLI（T28，#29）：dry-run 退出码、配置错误、NOT_RUN 产物。

不训练、不联网；数据集用临时构建的冻结副本。
"""

from __future__ import annotations

import json
from pathlib import Path

from bank_assistant.training.config import load_train_config
from scripts.train_intent_lora import main

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "configs/train/qlora-intent-qwen3-0.6b.yaml"


def _config_path(tmp_path: Path, dataset_dir: Path, **overrides) -> Path:
    config = load_train_config(CONFIG)
    payload = config.to_dict()
    payload["dataset"]["dataset_dir"] = str(dataset_dir)
    payload["run"]["artifact_root"] = str(tmp_path / "artifacts")
    for key, value in overrides.items():
        payload[key] = value
    path = tmp_path / "train.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_dry_run_exits_zero_and_writes_not_run(
    tmp_path: Path, frozen_dataset_dir: Path, capsys
) -> None:
    path = _config_path(tmp_path, frozen_dataset_dir)
    code = main(
        [
            "--config",
            str(path),
            "--dry-run",
            "--run-id",
            "cli-dry-run",
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "NOT_RUN"
    assert summary["mode"] == "dry_run"
    assert summary["evaluation_available"] is False
    manifest = json.loads(
        (tmp_path / "artifacts/cli-dry-run/manifest.json").read_text("utf-8")
    )
    assert manifest["status"] == "NOT_RUN"
    assert (tmp_path / "artifacts/cli-dry-run/model-card.md").is_file()
    assert (tmp_path / "artifacts/cli-dry-run/config.json").is_file()


def test_dry_run_is_offline_and_does_not_need_torch(
    tmp_path: Path, frozen_dataset_dir: Path, monkeypatch
) -> None:
    # dry-run 不导入 torch；即使可执行文件不可用也必须能产出报告。
    monkeypatch.setenv("PATH", "/nonexistent")
    path = _config_path(tmp_path, frozen_dataset_dir)
    assert main(["--config", str(path), "--dry-run", "--run-id", "cli-no-path"]) == 0


def test_dev_split_config_exits_2(
    tmp_path: Path, frozen_dataset_dir: Path, capsys
) -> None:
    config = load_train_config(CONFIG)
    payload = config.to_dict()
    payload["dataset"]["dataset_dir"] = str(frozen_dataset_dir)
    payload["dataset"]["split"] = "dev"
    path = tmp_path / "dev.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    code = main(["--config", str(path), "--dry-run"])
    assert code == 2
    assert "dev" in capsys.readouterr().err


def test_test_split_config_exits_2(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    config = load_train_config(CONFIG)
    payload = config.to_dict()
    payload["dataset"]["dataset_dir"] = str(frozen_dataset_dir)
    payload["dataset"]["split"] = "test"
    path = tmp_path / "test-split.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["--config", str(path), "--dry-run"]) == 2


def test_invalid_yaml_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("run: [unclosed", encoding="utf-8")
    assert main(["--config", str(path), "--dry-run"]) == 2


def test_missing_dataset_exits_2(
    tmp_path: Path, frozen_dataset_dir: Path
) -> None:
    path = _config_path(tmp_path, frozen_dataset_dir)
    config = json.loads(path.read_text("utf-8"))
    config["dataset"]["dataset_dir"] = str(tmp_path / "absent-dataset")
    path.write_text(json.dumps(config), encoding="utf-8")
    assert main(["--config", str(path), "--dry-run"]) == 2


def test_cli_requires_mode(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(SystemExit):
        main(["--config", str(CONFIG)])


def test_execute_without_gpu_exits_1_and_writes_not_run(
    tmp_path: Path, frozen_dataset_dir: Path, monkeypatch
) -> None:
    """CLI 的 --execute 路径：GPU 不可用 → NOT_RUN 报告 + 退出码 1。"""
    from bank_assistant.training.hardware import HardwareProbe

    def fake_probe() -> HardwareProbe:
        return HardwareProbe(
            probed_at="2026-11-12T18:32:45+08:00",
            platform="Linux-6.8.0-x86_64",
            machine="x86_64",
            cpu_count=12,
            memory_gib=62.0,
            python="3.13.1",
            nvidia_smi_available=False,
            nvidia_smi_version=None,
            gpu_name=None,
            driver_version=None,
            total_vram_gib=None,
            used_vram_gib=None,
            free_vram_gib=None,
            gpu_utilization_pct=None,
            error="nvidia-smi not found in PATH",
        )

    monkeypatch.setattr(
        "bank_assistant.training.trainer.probe_hardware", lambda: fake_probe()
    )
    path = _config_path(tmp_path, frozen_dataset_dir)
    code = main(
        [
            "--config",
            str(path),
            "--execute",
            "--run-id",
            "cli-execute-no-gpu",
        ]
    )
    assert code == 1
    manifest = json.loads(
        (tmp_path / "artifacts/cli-execute-no-gpu/manifest.json").read_text("utf-8")
    )
    assert manifest["status"] == "NOT_RUN"
    assert manifest["mode"] == "train"
    assert "未检测到可用 NVIDIA GPU" in manifest["status_reason"]
    assert manifest["evaluation"]["available"] is False
