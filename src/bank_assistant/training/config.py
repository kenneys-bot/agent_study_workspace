"""QLoRA 意图分类训练配置（T28，#29）。

配置只描述 **怎么训练/评测**，不含凭据、不含数据路径以外的环境细节。所有
8GB 显存约束都在这里显式校验：batch=1、4-bit、梯度检查点、序列长度上限，
以及候选模型参数量 0.6B–7B。配置不合法时立刻失败，不静默补默认值。

拆分守卫在配置加载阶段就调用：

- 训练 split 只允许 ``train``（:func:`require_split_for`）；
- 评测 split 只允许冻结 ``test``（:func:`require_frozen_test_split`）。

这样即使 CLI 传错配置，也不会出现"用 test 训练"或"用 dev 出正式结论"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from bank_assistant.datasets.split_guard import (
    require_frozen_test_split,
    require_split_for,
)

__all__ = [
    "CANDIDATE_MODEL_PATTERNS",
    "DatasetSpec",
    "EvaluationSpec",
    "HardwareSpec",
    "LoraSpec",
    "ModelSpec",
    "QuantizationSpec",
    "RunSpec",
    "TrainConfig",
    "TrainConfigError",
    "TrainingSpec",
    "is_candidate_model",
    "load_train_config",
]

# ADR-0004：8GB 显存下候选模型限定 0.6B–7B。这里按模型名做第一道闸门，
# ``parameter_count_b`` 再做数值闸门，避免"名字看着小、实际 8B"。
CANDIDATE_MODEL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:Qwen/)?Qwen3-(?:0\.6B|1\.7B|4B)$"),
    re.compile(r"^(?:Qwen/)?Qwen2\.5-(?:1\.5B|3B|7B)(?:-Instruct)?$"),
    re.compile(r"^(?:meta-llama/)?Llama-3\.2-(?:1B|3B)(?:-Instruct)?$"),
)

_QUANT_TYPES = frozenset({"nf4", "fp4"})
_COMPUTE_DTYPES = frozenset({"bfloat16", "float16"})
_LR_SCHEDULERS = frozenset({"cosine", "linear", "constant"})

_TOP_KEYS = frozenset(
    {
        "run",
        "dataset",
        "model",
        "quantization",
        "lora",
        "training",
        "evaluation",
        "hardware",
    }
)
_RUN_KEYS = frozenset({"artifact_root", "seed", "run_id"})
_DATASET_KEYS = frozenset(
    {
        "dataset_id",
        "dataset_version",
        "dataset_dir",
        "split",
        "task",
        "text_field",
        "label_field",
    }
)
_MODEL_KEYS = frozenset(
    {
        "name",
        "revision",
        "license",
        "parameter_count_b",
        "provider",
        "trust_remote_code",
    }
)
_QUANT_KEYS = frozenset(
    {"load_in_4bit", "quant_type", "double_quant", "compute_dtype"}
)
_LORA_KEYS = frozenset({"r", "alpha", "dropout", "target_modules"})
_TRAINING_KEYS = frozenset(
    {
        "max_seq_length",
        "per_device_train_batch_size",
        "gradient_accumulation_steps",
        "num_train_epochs",
        "learning_rate",
        "lr_scheduler_type",
        "warmup_ratio",
        "gradient_checkpointing",
        "logging_steps",
        "max_steps",
        "bf16",
    }
)
_EVAL_KEYS = frozenset(
    {"split", "batch_size", "max_new_tokens", "prompt_version"}
)
_HARDWARE_KEYS = frozenset(
    {
        "require_cuda",
        "min_total_vram_gib",
        "min_free_vram_gib",
        "allow_time_sharing",
    }
)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TrainConfigError(ValueError):
    """训练配置不合法；必须显式失败，不能静默回退。"""


def is_candidate_model(name: str) -> bool:
    """ADR-0004 候选模型名是否匹配（0.6B–7B、4-bit QLoRA 可跑）。"""
    return any(pattern.match(name) for pattern in CANDIDATE_MODEL_PATTERNS)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrainConfigError(f"{where} 必须是对象，收到 {type(value).__name__}")
    return value


def _keys(
    payload: Mapping[str, Any], *, allowed: frozenset[str], required: tuple[str, ...], where: str
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise TrainConfigError(f"{where} 含未知字段：{', '.join(unknown)}")
    missing = [key for key in required if key not in payload]
    if missing:
        raise TrainConfigError(f"{where} 缺少字段：{', '.join(missing)}")


def _int(value: Any, where: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrainConfigError(f"{where} 必须是整数，收到 {value!r}")
    if not minimum <= value <= maximum:
        raise TrainConfigError(f"{where} 必须在 [{minimum}, {maximum}]，收到 {value}")
    return value


def _float(value: Any, where: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainConfigError(f"{where} 必须是数字，收到 {value!r}")
    number = float(value)
    if not minimum <= number <= maximum:
        raise TrainConfigError(f"{where} 必须在 [{minimum}, {maximum}]，收到 {number}")
    return number


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise TrainConfigError(f"{where} 必须是布尔值，收到 {value!r}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrainConfigError(f"{where} 必须是非空字符串")
    return value.strip()


@dataclass(frozen=True, slots=True)
class RunSpec:
    artifact_root: Path
    seed: int
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    dataset_id: str
    dataset_version: str
    dataset_dir: Path
    split: str
    task: str
    text_field: str
    label_field: str


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    revision: str
    license: str
    parameter_count_b: float
    provider: str
    trust_remote_code: bool


@dataclass(frozen=True, slots=True)
class QuantizationSpec:
    load_in_4bit: bool
    quant_type: str
    double_quant: bool
    compute_dtype: str


@dataclass(frozen=True, slots=True)
class LoraSpec:
    r: int
    alpha: int
    dropout: float
    target_modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrainingSpec:
    max_seq_length: int
    per_device_train_batch_size: int
    gradient_accumulation_steps: int
    num_train_epochs: float
    learning_rate: float
    lr_scheduler_type: str
    warmup_ratio: float
    gradient_checkpointing: bool
    logging_steps: int
    max_steps: int
    bf16: bool


@dataclass(frozen=True, slots=True)
class EvaluationSpec:
    split: str
    batch_size: int
    max_new_tokens: int
    prompt_version: str


@dataclass(frozen=True, slots=True)
class HardwareSpec:
    require_cuda: bool
    min_total_vram_gib: float
    min_free_vram_gib: float
    allow_time_sharing: bool


@dataclass(frozen=True, slots=True)
class TrainConfig:
    run: RunSpec
    dataset: DatasetSpec
    model: ModelSpec
    quantization: QuantizationSpec
    lora: LoraSpec
    training: TrainingSpec
    evaluation: EvaluationSpec
    hardware: HardwareSpec
    source_path: Path

    def to_dict(self) -> dict[str, Any]:
        """可写入 manifest 的纯 JSON 结构（不含凭据）。"""
        return {
            "run": {
                "artifact_root": str(self.run.artifact_root),
                "seed": self.run.seed,
                "run_id": self.run.run_id,
            },
            "dataset": {
                "dataset_id": self.dataset.dataset_id,
                "dataset_version": self.dataset.dataset_version,
                "dataset_dir": str(self.dataset.dataset_dir),
                "split": self.dataset.split,
                "task": self.dataset.task,
                "text_field": self.dataset.text_field,
                "label_field": self.dataset.label_field,
            },
            "model": {
                "name": self.model.name,
                "revision": self.model.revision,
                "license": self.model.license,
                "parameter_count_b": self.model.parameter_count_b,
                "provider": self.model.provider,
                "trust_remote_code": self.model.trust_remote_code,
            },
            "quantization": {
                "load_in_4bit": self.quantization.load_in_4bit,
                "quant_type": self.quantization.quant_type,
                "double_quant": self.quantization.double_quant,
                "compute_dtype": self.quantization.compute_dtype,
            },
            "lora": {
                "r": self.lora.r,
                "alpha": self.lora.alpha,
                "dropout": self.lora.dropout,
                "target_modules": list(self.lora.target_modules),
            },
            "training": {
                "max_seq_length": self.training.max_seq_length,
                "per_device_train_batch_size": self.training.per_device_train_batch_size,
                "gradient_accumulation_steps": self.training.gradient_accumulation_steps,
                "num_train_epochs": self.training.num_train_epochs,
                "learning_rate": self.training.learning_rate,
                "lr_scheduler_type": self.training.lr_scheduler_type,
                "warmup_ratio": self.training.warmup_ratio,
                "gradient_checkpointing": self.training.gradient_checkpointing,
                "logging_steps": self.training.logging_steps,
                "max_steps": self.training.max_steps,
                "bf16": self.training.bf16,
            },
            "evaluation": {
                "split": self.evaluation.split,
                "batch_size": self.evaluation.batch_size,
                "max_new_tokens": self.evaluation.max_new_tokens,
                "prompt_version": self.evaluation.prompt_version,
            },
            "hardware": {
                "require_cuda": self.hardware.require_cuda,
                "min_total_vram_gib": self.hardware.min_total_vram_gib,
                "min_free_vram_gib": self.hardware.min_free_vram_gib,
                "allow_time_sharing": self.hardware.allow_time_sharing,
            },
        }


def _parse_run(payload: Mapping[str, Any]) -> RunSpec:
    _keys(payload, allowed=_RUN_KEYS, required=("artifact_root", "seed"), where="run")
    run_id = payload.get("run_id")
    if run_id is not None:
        run_id = _text(run_id, "run.run_id")
        if not _RUN_ID_RE.match(run_id):
            raise TrainConfigError(
                "run.run_id 只能包含字母/数字/._-，且不能以符号开头"
            )
    return RunSpec(
        artifact_root=Path(_text(payload["artifact_root"], "run.artifact_root")),
        seed=_int(payload["seed"], "run.seed", minimum=0, maximum=2**31 - 1),
        run_id=run_id,
    )


def _parse_dataset(payload: Mapping[str, Any]) -> DatasetSpec:
    _keys(
        payload,
        allowed=_DATASET_KEYS,
        required=(
            "dataset_id",
            "dataset_version",
            "dataset_dir",
            "split",
            "text_field",
            "label_field",
        ),
        where="dataset",
    )
    split = _text(payload["split"], "dataset.split")
    # 训练只允许 train；test/dev 在这里就被 split_guard 拒绝。
    require_split_for("train", split)
    return DatasetSpec(
        dataset_id=_text(payload["dataset_id"], "dataset.dataset_id"),
        dataset_version=_text(payload["dataset_version"], "dataset.dataset_version"),
        dataset_dir=Path(_text(payload["dataset_dir"], "dataset.dataset_dir")),
        split=split,
        task=_text(payload.get("task", "retrieval"), "dataset.task"),
        text_field=_text(payload["text_field"], "dataset.text_field"),
        label_field=_text(payload["label_field"], "dataset.label_field"),
    )


def _parse_model(payload: Mapping[str, Any]) -> ModelSpec:
    _keys(
        payload,
        allowed=_MODEL_KEYS,
        required=(
            "name",
            "revision",
            "license",
            "parameter_count_b",
            "provider",
        ),
        where="model",
    )
    name = _text(payload["name"], "model.name")
    if not is_candidate_model(name):
        raise TrainConfigError(
            f"model.name={name!r} 不在 ADR-0004 候选清单（0.6B–7B Qwen/Llama-3.2）"
        )
    return ModelSpec(
        name=name,
        revision=_text(payload["revision"], "model.revision"),
        license=_text(payload["license"], "model.license"),
        parameter_count_b=_float(
            payload["parameter_count_b"],
            "model.parameter_count_b",
            minimum=0.6,
            maximum=7.0,
        ),
        provider=_text(payload["provider"], "model.provider"),
        trust_remote_code=_bool(
            payload.get("trust_remote_code", False), "model.trust_remote_code"
        ),
    )


def _parse_quantization(payload: Mapping[str, Any]) -> QuantizationSpec:
    _keys(
        payload,
        allowed=_QUANT_KEYS,
        required=("load_in_4bit", "quant_type", "double_quant", "compute_dtype"),
        where="quantization",
    )
    load_in_4bit = _bool(payload["load_in_4bit"], "quantization.load_in_4bit")
    if not load_in_4bit:
        raise TrainConfigError(
            "8GB 显存只允许 4-bit QLoRA；quantization.load_in_4bit 必须为 true"
        )
    quant_type = _text(payload["quant_type"], "quantization.quant_type")
    if quant_type not in _QUANT_TYPES:
        raise TrainConfigError(
            f"quantization.quant_type 只能是 {sorted(_QUANT_TYPES)}，收到 {quant_type!r}"
        )
    compute_dtype = _text(payload["compute_dtype"], "quantization.compute_dtype")
    if compute_dtype not in _COMPUTE_DTYPES:
        raise TrainConfigError(
            "quantization.compute_dtype 只能是 "
            f"{sorted(_COMPUTE_DTYPES)}，收到 {compute_dtype!r}"
        )
    return QuantizationSpec(
        load_in_4bit=load_in_4bit,
        quant_type=quant_type,
        double_quant=_bool(payload["double_quant"], "quantization.double_quant"),
        compute_dtype=compute_dtype,
    )


def _parse_lora(payload: Mapping[str, Any]) -> LoraSpec:
    _keys(
        payload,
        allowed=_LORA_KEYS,
        required=("r", "alpha", "dropout", "target_modules"),
        where="lora",
    )
    targets = payload["target_modules"]
    if (
        not isinstance(targets, (list, tuple))
        or not targets
        or any(not isinstance(item, str) or not item.strip() for item in targets)
    ):
        raise TrainConfigError("lora.target_modules 必须是非空字符串列表")
    normalized = tuple(item.strip() for item in targets)
    if len(set(normalized)) != len(normalized):
        raise TrainConfigError("lora.target_modules 不能重复")
    return LoraSpec(
        r=_int(payload["r"], "lora.r", minimum=4, maximum=64),
        alpha=_int(payload["alpha"], "lora.alpha", minimum=4, maximum=128),
        dropout=_float(payload["dropout"], "lora.dropout", minimum=0.0, maximum=0.5),
        target_modules=normalized,
    )


def _parse_training(payload: Mapping[str, Any]) -> TrainingSpec:
    _keys(
        payload,
        allowed=_TRAINING_KEYS,
        required=(
            "max_seq_length",
            "per_device_train_batch_size",
            "gradient_accumulation_steps",
            "num_train_epochs",
            "learning_rate",
            "gradient_checkpointing",
        ),
        where="training",
    )
    batch_size = _int(
        payload["per_device_train_batch_size"],
        "training.per_device_train_batch_size",
        minimum=1,
        maximum=8,
    )
    if batch_size != 1:
        raise TrainConfigError(
            "8GB 显存约束要求 training.per_device_train_batch_size=1；"
            "增大 batch 请改用 gradient_accumulation_steps"
        )
    if not _bool(payload["gradient_checkpointing"], "training.gradient_checkpointing"):
        raise TrainConfigError(
            "8GB 显存约束要求 training.gradient_checkpointing=true"
        )
    seq = _int(
        payload["max_seq_length"],
        "training.max_seq_length",
        minimum=128,
        maximum=2048,
    )
    if seq % 64 != 0:
        raise TrainConfigError(
            f"training.max_seq_length 应为 64 的倍数，收到 {seq}"
        )
    scheduler = _text(
        payload.get("lr_scheduler_type", "cosine"), "training.lr_scheduler_type"
    )
    if scheduler not in _LR_SCHEDULERS:
        raise TrainConfigError(
            f"training.lr_scheduler_type 只能是 {sorted(_LR_SCHEDULERS)}"
        )
    return TrainingSpec(
        max_seq_length=seq,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=_int(
            payload["gradient_accumulation_steps"],
            "training.gradient_accumulation_steps",
            minimum=1,
            maximum=256,
        ),
        num_train_epochs=_float(
            payload["num_train_epochs"],
            "training.num_train_epochs",
            minimum=0.1,
            maximum=10.0,
        ),
        learning_rate=_float(
            payload["learning_rate"],
            "training.learning_rate",
            minimum=1e-6,
            maximum=1e-3,
        ),
        lr_scheduler_type=scheduler,
        warmup_ratio=_float(
            payload.get("warmup_ratio", 0.0),
            "training.warmup_ratio",
            minimum=0.0,
            maximum=0.5,
        ),
        gradient_checkpointing=True,
        logging_steps=_int(
            payload.get("logging_steps", 5),
            "training.logging_steps",
            minimum=1,
            maximum=1000,
        ),
        max_steps=_int(
            payload.get("max_steps", -1),
            "training.max_steps",
            minimum=-1,
            maximum=1_000_000,
        ),
        bf16=_bool(payload.get("bf16", True), "training.bf16"),
    )


def _parse_evaluation(payload: Mapping[str, Any]) -> EvaluationSpec:
    _keys(
        payload,
        allowed=_EVAL_KEYS,
        required=("split",),
        where="evaluation",
    )
    split = require_frozen_test_split(_text(payload["split"], "evaluation.split"))
    batch_size = _int(
        payload.get("batch_size", 1),
        "evaluation.batch_size",
        minimum=1,
        maximum=8,
    )
    return EvaluationSpec(
        split=split,
        batch_size=batch_size,
        max_new_tokens=_int(
            payload.get("max_new_tokens", 8),
            "evaluation.max_new_tokens",
            minimum=1,
            maximum=64,
        ),
        prompt_version=_text(
            payload.get("prompt_version", "intent-routing-v1"),
            "evaluation.prompt_version",
        ),
    )


def _parse_hardware(payload: Mapping[str, Any]) -> HardwareSpec:
    _keys(
        payload,
        allowed=_HARDWARE_KEYS,
        required=(),
        where="hardware",
    )
    return HardwareSpec(
        require_cuda=_bool(
            payload.get("require_cuda", True), "hardware.require_cuda"
        ),
        min_total_vram_gib=_float(
            payload.get("min_total_vram_gib", 8.0),
            "hardware.min_total_vram_gib",
            minimum=1.0,
            maximum=80.0,
        ),
        min_free_vram_gib=_float(
            payload.get("min_free_vram_gib", 2.0),
            "hardware.min_free_vram_gib",
            minimum=0.0,
            maximum=80.0,
        ),
        allow_time_sharing=_bool(
            payload.get("allow_time_sharing", False),
            "hardware.allow_time_sharing",
        ),
    )


def load_train_config(path: str | Path) -> TrainConfig:
    """读取 JSON/YAML 训练配置；未知字段、越界超参一律失败。"""
    config_path = Path(path)
    if not config_path.is_file():
        raise TrainConfigError(f"找不到训练配置：{config_path}")
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 文件系统错误
        raise TrainConfigError(f"无法读取训练配置 {config_path}：{exc}") from exc
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TrainConfigError(f"训练配置不是合法 YAML：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise TrainConfigError("训练配置顶层必须是对象")
    _keys(
        payload,
        allowed=_TOP_KEYS,
        required=(
            "run",
            "dataset",
            "model",
            "quantization",
            "lora",
            "training",
            "evaluation",
        ),
        where="配置",
    )
    config = TrainConfig(
        run=_parse_run(_mapping(payload["run"], "run")),
        dataset=_parse_dataset(_mapping(payload["dataset"], "dataset")),
        model=_parse_model(_mapping(payload["model"], "model")),
        quantization=_parse_quantization(
            _mapping(payload["quantization"], "quantization")
        ),
        lora=_parse_lora(_mapping(payload["lora"], "lora")),
        training=_parse_training(_mapping(payload["training"], "training")),
        evaluation=_parse_evaluation(
            _mapping(payload["evaluation"], "evaluation")
        ),
        hardware=_parse_hardware(_mapping(payload.get("hardware", {}), "hardware")),
        source_path=config_path,
    )
    return config
