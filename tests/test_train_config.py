"""训练配置校验（T28，#29）：8GB 约束、候选模型、split 守卫。

测试不联网、不需要 GPU，也不加载 torch。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bank_assistant.datasets.split_guard import SplitLeakageError
from bank_assistant.training.config import (
    TrainConfig,
    TrainConfigError,
    load_train_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _payload(**overrides) -> dict:
    payload = {
        "run": {"artifact_root": "artifacts/training", "seed": 42, "run_id": None},
        "dataset": {
            "dataset_id": "bank-cs-m6-synthetic",
            "dataset_version": "v1",
            "dataset_dir": "artifacts/data/bank-cs-m6-synthetic/v1",
            "split": "train",
            "task": "retrieval",
            "text_field": "query",
            "label_field": "business_type",
        },
        "model": {
            "name": "Qwen/Qwen3-0.6B",
            "revision": "main",
            "license": "Apache-2.0",
            "parameter_count_b": 0.6,
            "provider": "local_model",
            "trust_remote_code": False,
        },
        "quantization": {
            "load_in_4bit": True,
            "quant_type": "nf4",
            "double_quant": True,
            "compute_dtype": "bfloat16",
        },
        "lora": {
            "r": 16,
            "alpha": 32,
            "dropout": 0.05,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
        },
        "training": {
            "max_seq_length": 512,
            "per_device_train_batch_size": 1,
            "gradient_accumulation_steps": 8,
            "num_train_epochs": 3,
            "learning_rate": 2.0e-4,
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.03,
            "gradient_checkpointing": True,
            "logging_steps": 5,
            "max_steps": -1,
            "bf16": True,
        },
        "evaluation": {
            "split": "test",
            "batch_size": 1,
            "max_new_tokens": 8,
            "prompt_version": "intent-routing-v1",
        },
        "hardware": {
            "require_cuda": True,
            "min_total_vram_gib": 8.0,
            "min_free_vram_gib": 2.0,
            "allow_time_sharing": False,
        },
    }
    for key, value in overrides.items():
        payload[key] = value
    return payload


def _write(tmp_path: Path, payload: dict, name: str = "train.yaml") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    config = load_train_config(_write(tmp_path, _payload()))
    assert isinstance(config, TrainConfig)
    assert config.dataset.split == "train"
    assert config.evaluation.split == "test"
    assert config.model.parameter_count_b == 0.6
    assert config.training.per_device_train_batch_size == 1
    assert config.training.gradient_checkpointing is True
    assert config.quantization.load_in_4bit is True


def test_checked_in_configs_load() -> None:
    paths = sorted((REPO_ROOT / "configs/train").glob("*.yaml"))
    assert len(paths) >= 2
    for path in paths:
        config = load_train_config(path)
        assert config.dataset.split == "train"
        assert config.evaluation.split == "test"
        assert 0.6 <= config.model.parameter_count_b <= 7.0


@pytest.mark.parametrize("split", ["dev", "test"])
def test_training_split_guard_refuses_non_train(tmp_path: Path, split: str) -> None:
    payload = _payload()
    payload["dataset"]["split"] = split
    with pytest.raises(SplitLeakageError):
        load_train_config(_write(tmp_path, payload))


@pytest.mark.parametrize("split", ["dev", "train"])
def test_evaluation_split_guard_refuses_non_test(tmp_path: Path, split: str) -> None:
    payload = _payload()
    payload["evaluation"]["split"] = split
    with pytest.raises(SplitLeakageError):
        load_train_config(_write(tmp_path, payload))


def test_rejects_batch_size_above_one(tmp_path: Path) -> None:
    payload = _payload()
    payload["training"]["per_device_train_batch_size"] = 2
    with pytest.raises(TrainConfigError, match="batch_size=1"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_disabled_gradient_checkpointing(tmp_path: Path) -> None:
    payload = _payload()
    payload["training"]["gradient_checkpointing"] = False
    with pytest.raises(TrainConfigError, match="gradient_checkpointing"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_16bit_lora(tmp_path: Path) -> None:
    payload = _payload()
    payload["quantization"]["load_in_4bit"] = False
    with pytest.raises(TrainConfigError, match="4-bit"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_model_above_7b(tmp_path: Path) -> None:
    payload = _payload()
    payload["model"]["name"] = "Qwen/Qwen3-8B"
    with pytest.raises(TrainConfigError, match="候选清单"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_parameter_count_above_limit(tmp_path: Path) -> None:
    payload = _payload()
    payload["model"]["parameter_count_b"] = 8.0
    with pytest.raises(TrainConfigError, match="parameter_count_b"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_seq_length_not_multiple_of_64(tmp_path: Path) -> None:
    payload = _payload()
    payload["training"]["max_seq_length"] = 500
    with pytest.raises(TrainConfigError, match="64 的倍数"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_unknown_field(tmp_path: Path) -> None:
    payload = _payload()
    payload["training"]["batch_size"] = 1
    with pytest.raises(TrainConfigError, match="未知字段"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    payload = _payload()
    payload["deepspeed"] = {}
    with pytest.raises(TrainConfigError, match="未知字段"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_missing_required_section(tmp_path: Path) -> None:
    payload = _payload()
    del payload["quantization"]
    with pytest.raises(TrainConfigError, match="缺少字段"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_negative_seed(tmp_path: Path) -> None:
    payload = _payload()
    payload["run"]["seed"] = -1
    with pytest.raises(TrainConfigError, match="seed"):
        load_train_config(_write(tmp_path, payload))


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TrainConfigError, match="找不到训练配置"):
        load_train_config(tmp_path / "absent.yaml")
