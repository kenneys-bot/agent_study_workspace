"""训练 run manifest 与 model card（T28，#29）。

每次 dry-run/训练都写一个不可变目录：

``<artifact_root>/<run_id>/{manifest.json,model-card.md,config.json,dry-run.json}``

manifest 必须能独立回答五个问题：**用了什么模型/数据（哈希）、超参和种子是
什么、在什么硬件上跑的、跑了多久/显存峰值多少、最终状态是什么**。状态为
``NOT_RUN`` 时还必须有具体原因；不允许既没跑又不说为什么。

凭据扫描复用 T27 的 :func:`bank_assistant.evaluation.manifest.assert_no_secrets`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from bank_assistant.evaluation.manifest import (
    ManifestError,
    assert_no_secrets,
)

__all__ = [
    "REQUIRED_TRAINING_MANIFEST_FIELDS",
    "STATUSES",
    "TrainingManifestError",
    "TrainingRunContext",
    "build_training_manifest",
    "render_model_card",
    "validate_training_manifest",
    "write_training_artifacts",
]

STATUSES: frozenset[str] = frozenset(
    {"SUCCEEDED", "NOT_RUN", "FAILED", "BLOCKED"}
)

REQUIRED_TRAINING_MANIFEST_FIELDS: tuple[str, ...] = (
    "run_id",
    "task",
    "mode",
    "code_sha",
    "worktree_clean",
    "model_name",
    "model_provider",
    "model_revision",
    "model_license",
    "model_parameter_count_b",
    "dataset_id",
    "dataset_version",
    "dataset_manifest_sha256",
    "split_manifest_sha256",
    "train_split_sha256",
    "test_split_sha256",
    "train_items",
    "test_items",
    "label_names",
    "label_schema_sha256",
    "seed",
    "hyperparameters",
    "hardware_summary",
    "hardware",
    "gpu",
    "started_at",
    "finished_at",
    "wall_time_seconds",
    "vram_peak_gib",
    "status",
    "status_reason",
    "evaluation",
    "artifacts",
    "steps",
    "environment_ref",
    "contract_version",
)


class TrainingManifestError(ManifestError):
    """训练 manifest 字段不完整或状态自相矛盾。"""


@dataclass(frozen=True, slots=True)
class TrainingRunContext:
    run_id: str
    mode: str
    code_sha: str
    worktree_clean: bool
    model_name: str
    model_provider: str
    model_revision: str
    model_license: str
    model_parameter_count_b: float
    dataset_id: str
    dataset_version: str
    dataset_manifest_sha256: str
    split_manifest_sha256: str
    train_split_sha256: str
    test_split_sha256: str
    train_items: int
    test_items: int
    label_names: tuple[str, ...]
    label_schema_sha256: str
    seed: int
    hyperparameters: Mapping[str, Any]
    hardware_summary: str
    hardware: Mapping[str, Any]
    gpu: Mapping[str, Any]
    started_at: datetime
    finished_at: datetime
    wall_time_seconds: float
    vram_peak_gib: float | None
    status: str
    status_reason: str
    evaluation: Mapping[str, Any]
    artifacts: Mapping[str, Any]
    steps: tuple[Mapping[str, Any], ...] = ()
    notes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in {"train", "dry_run"}:
            raise TrainingManifestError(
                f"mode 只能是 train/dry_run，收到 {self.mode!r}"
            )
        if self.status not in STATUSES:
            raise TrainingManifestError(
                f"status 只能是 {sorted(STATUSES)}，收到 {self.status!r}"
            )
        if self.status != "SUCCEEDED" and not self.status_reason.strip():
            raise TrainingManifestError("非成功 run 必须写明 status_reason")
        if self.status == "SUCCEEDED":
            if self.vram_peak_gib is None:
                raise TrainingManifestError("成功训练必须记录 vram_peak_gib")
            if not self.evaluation.get("available"):
                raise TrainingManifestError(
                    "成功训练必须完成冻结 test 评测（evaluation.available=true）"
                )
        if self.mode == "dry_run" and self.status == "SUCCEEDED":
            raise TrainingManifestError("dry_run 不能写成 SUCCEEDED（没有真实训练）")
        for label in ("started_at", "finished_at"):
            value = getattr(self, label)
            if value.tzinfo is None:
                raise TrainingManifestError(f"{label} 必须带时区（统一 UTC）")
        if self.wall_time_seconds < 0:
            raise TrainingManifestError("wall_time_seconds 不能为负")


def build_training_manifest(context: TrainingRunContext) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "run_id": context.run_id,
        "task": "intent_classification",
        "mode": context.mode,
        "code_sha": context.code_sha,
        "worktree_clean": context.worktree_clean,
        "model_name": context.model_name,
        "model_provider": context.model_provider,
        "model_revision": context.model_revision,
        "model_license": context.model_license,
        "model_parameter_count_b": context.model_parameter_count_b,
        "dataset_id": context.dataset_id,
        "dataset_version": context.dataset_version,
        "dataset_manifest_sha256": context.dataset_manifest_sha256,
        "split_manifest_sha256": context.split_manifest_sha256,
        "train_split_sha256": context.train_split_sha256,
        "test_split_sha256": context.test_split_sha256,
        "train_items": context.train_items,
        "test_items": context.test_items,
        "label_names": list(context.label_names),
        "label_schema_sha256": context.label_schema_sha256,
        "seed": context.seed,
        "hyperparameters": dict(context.hyperparameters),
        "hardware_summary": context.hardware_summary,
        "hardware": dict(context.hardware),
        "gpu": dict(context.gpu),
        "started_at": context.started_at.isoformat(),
        "finished_at": context.finished_at.isoformat(),
        "wall_time_seconds": round(context.wall_time_seconds, 3),
        "vram_peak_gib": (
            round(context.vram_peak_gib, 3)
            if context.vram_peak_gib is not None
            else None
        ),
        "status": context.status,
        "status_reason": context.status_reason,
        "evaluation": dict(context.evaluation),
        "artifacts": dict(context.artifacts),
        "steps": [dict(step) for step in context.steps],
        "environment_ref": "docs/environment.md",
        "contract_version": "bank-customer-service-contracts#6",
        "notes": dict(context.notes),
    }
    assert_no_secrets(manifest)
    return manifest


def _require_sha256(manifest: Mapping[str, Any], key: str) -> None:
    value = manifest.get(key)
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise TrainingManifestError(f"{key} 必须是 64 位十六进制 sha256，收到 {value!r}")


def validate_training_manifest(manifest: Mapping[str, Any]) -> None:
    missing = [
        field
        for field in REQUIRED_TRAINING_MANIFEST_FIELDS
        if key_missing(manifest, field)
    ]
    if missing:
        raise TrainingManifestError(
            "训练 manifest 缺少必填字段：" + ", ".join(missing)
        )
    if manifest["status"] not in STATUSES:
        raise TrainingManifestError(f"status 非法：{manifest['status']!r}")
    if manifest["mode"] not in {"train", "dry_run"}:
        raise TrainingManifestError(f"mode 非法：{manifest['mode']!r}")
    if manifest["status"] != "SUCCEEDED" and not str(
        manifest["status_reason"]
    ).strip():
        raise TrainingManifestError("非成功 run 缺少 status_reason")
    if manifest["status"] == "SUCCEEDED":
        if manifest.get("vram_peak_gib") is None:
            raise TrainingManifestError("成功 run 缺少 vram_peak_gib")
        if not manifest.get("evaluation", {}).get("available"):
            raise TrainingManifestError("成功 run 缺少冻结 test 评测")
    if manifest["mode"] == "dry_run" and manifest["status"] == "SUCCEEDED":
        raise TrainingManifestError("dry_run 不能标记 SUCCEEDED")
    if not isinstance(manifest["seed"], int):
        raise TrainingManifestError("seed 必须是整数")
    for key in (
        "dataset_manifest_sha256",
        "split_manifest_sha256",
        "train_split_sha256",
        "test_split_sha256",
        "label_schema_sha256",
    ):
        _require_sha256(manifest, key)
    if not isinstance(manifest["label_names"], list) or len(manifest["label_names"]) < 2:
        raise TrainingManifestError("label_names 至少要有两个标签")
    if not isinstance(manifest["steps"], list) or not manifest["steps"]:
        raise TrainingManifestError("steps 不能为空：至少记录 dry-run/训练与评测状态")
    if manifest["evaluation"].get("split") != "test":
        raise TrainingManifestError("正式评测只允许冻结 test split")
    assert_no_secrets(manifest)


def key_missing(manifest: Mapping[str, Any], key: str) -> bool:
    if key not in manifest:
        return True
    value = manifest[key]
    if value is None:
        # NOT_RUN 时显存峰值合法地为 null；其余字段不允许空值。
        return key != "vram_peak_gib"
    return value in ("", [], {})


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def render_model_card(manifest: Mapping[str, Any]) -> str:
    """从 manifest 生成 model card；不引入 manifest 之外的事实。"""
    validate_training_manifest(manifest)
    status = manifest["status"]
    hyper = manifest.get("hyperparameters", {})
    training = hyper.get("training", {})
    quantization = hyper.get("quantization", {})
    lora = hyper.get("lora", {})
    gpu = manifest.get("gpu", {})
    evaluation = manifest.get("evaluation", {})
    artifacts = manifest.get("artifacts", {})

    lines = [
        "# M6 意图分类 QLoRA Model Card（T28 / #29）",
        "",
        f"- **状态**：`{status}`",
        f"- **run_id**：`{manifest['run_id']}`",
        f"- **代码 SHA**：`{manifest['code_sha']}`",
        f"- **worktree clean**：`{manifest['worktree_clean']}`",
        f"- **训练时间**：{manifest['started_at']} → {manifest['finished_at']}"
        f"（{manifest['wall_time_seconds']}s）",
        f"- **显存峰值**：{manifest['vram_peak_gib']} GiB"
        if manifest["vram_peak_gib"] is not None
        else "- **显存峰值**：未测量（未执行真实训练）",
        "",
        "## 结论",
        "",
    ]
    if status == "SUCCEEDED":
        lines.append(
            "本次完成了真实 4-bit QLoRA 训练，并在冻结 test split 上比较了"
            "基线模型与微调后模型；结论见下方评测表。"
        )
    elif status == "NOT_RUN":
        lines.append(
            "**本次未执行真实训练**（`NOT_RUN`）。原因："
            + manifest["status_reason"]
            + "。因此没有 baseline vs tuned 的质量对比；不得把本文件读成"
            '"微调有效"或"微调无效"。'
        )
    else:
        lines.append(
            f"运行未成功（`{status}`）。原因：{manifest['status_reason']}。"
        )
    lines += [
        "",
        "## 模型与许可",
        "",
        f"- 基础模型：`{manifest['model_name']}`（revision `{manifest['model_revision']}`）",
        f"- 参数量：{manifest['model_parameter_count_b']}B（ADR-0004 允许 0.6B–7B）",
        f"- 许可：{manifest['model_license']}",
        f"- 来源/提供方：{manifest['model_provider']}",
        f"- 4-bit 量化：{quantization.get('quant_type')} / double_quant="
        f"{quantization.get('double_quant')} / compute_dtype="
        f"{quantization.get('compute_dtype')}",
        f"- LoRA：r={lora.get('r')} alpha={lora.get('alpha')} "
        f"dropout={lora.get('dropout')} targets={lora.get('target_modules')}",
        "",
        "## 数据",
        "",
        f"- 数据集：`{manifest['dataset_id']}` `{manifest['dataset_version']}`",
        f"- train split：{manifest['train_items']} 条，sha256 "
        f"`{manifest['train_split_sha256']}`",
        f"- test split：{manifest['test_items']} 条，sha256 "
        f"`{manifest['test_split_sha256']}`（冻结；只评测，不训练/不挑 checkpoint）",
        f"- dataset manifest：`{manifest['dataset_manifest_sha256']}`",
        f"- split manifest：`{manifest['split_manifest_sha256']}`",
        f"- 标签：{', '.join(manifest['label_names'])}；标签 schema "
        f"`{manifest['label_schema_sha256']}`",
        "- 数据为合成数据（`contains_real_customer_data=false`），"
        "标注为开发者单人按构造标注，不是人工/业务金标。",
        "",
        "## 超参与随机种子",
        "",
        f"- seed：`{manifest['seed']}`（训练/数据/评测同一 seed）",
        f"- max_seq_length：{training.get('max_seq_length')}",
        f"- per_device_train_batch_size：{training.get('per_device_train_batch_size')}"
        "（8GB 约束固定为 1）",
        f"- gradient_accumulation_steps：{training.get('gradient_accumulation_steps')}",
        f"- epochs：{training.get('num_train_epochs')}；max_steps："
        f"{training.get('max_steps')}",
        f"- learning_rate：{training.get('learning_rate')}；scheduler："
        f"{training.get('lr_scheduler_type')}；warmup_ratio："
        f"{training.get('warmup_ratio')}",
        f"- gradient_checkpointing：{training.get('gradient_checkpointing')}；"
        f"bf16：{training.get('bf16')}",
        "",
        "## 硬件与执行环境",
        "",
        f"- 硬件：{manifest['hardware_summary']}",
        f"- GPU 可用：{gpu.get('available')}；被占用：{gpu.get('busy')}；"
        f"空闲显存 {gpu.get('free_vram_gib')} GiB / 总显存 "
        f"{gpu.get('total_vram_gib')} GiB",
        f"- 探测时间：{gpu.get('probed_at')}",
    ]
    if gpu.get("busy_reason"):
        lines.append(f"- 占用说明：{gpu['busy_reason']}")
    if gpu.get("raw_query"):
        lines += ["", "```text", str(gpu["raw_query"]), "```"]
    lines += [
        "",
        "## 执行清单",
        "",
    ]
    steps = manifest.get("steps") or []
    for step in steps:
        lines.append(
            f"- `{step.get('name')}`：**{step.get('status')}**"
            + (f" — {step.get('detail')}" if step.get("detail") else "")
        )
    if not steps:
        lines.append("- 未记录步骤（manifest 由外部构造）")
    lines += [
        "",
        "## 冻结 test 评测",
        "",
    ]
    if evaluation.get("available"):
        baseline = evaluation.get("baseline") or {}
        tuned = evaluation.get("tuned") or {}
        delta = evaluation.get("delta") or {}
        lines += [
            "| 指标 | baseline（zero-shot） | tuned（LoRA） | Δ |",
            "|---|---|---|---|",
            f"| accuracy | {_fmt(baseline.get('accuracy'))} | "
            f"{_fmt(tuned.get('accuracy'))} | {_fmt(delta.get('accuracy'))} |",
            f"| macro-F1 | {_fmt(baseline.get('macro_f1'))} | "
            f"{_fmt(tuned.get('macro_f1'))} | {_fmt(delta.get('macro_f1'))} |",
            f"| n | {baseline.get('n')} | {tuned.get('n')} | — |",
            "",
            f"决策：`{evaluation.get('decision')}`。{evaluation.get('note', '')}",
        ]
    else:
        lines += [
            "**不可用**：" + str(evaluation.get("reason") or "未执行训练与评测"),
            "",
        ]
        reference = evaluation.get("majority_reference") or {}
        if reference:
            lines += [
                "仅给出**多数类参考下限**（不是基线模型、不参与对比）："
                f"accuracy={_fmt(reference.get('accuracy'))}，"
                f"macro-F1={_fmt(reference.get('macro_f1'))}，"
                f"n={reference.get('n')}。",
            ]
    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "# 1) 构建/校验冻结数据集",
        "make dataset-build",
        "make dataset-verify",
        "# 2) dry-run（无需 GPU）：校验配置/数据/哈希并写 NOT_RUN 报告",
        "make train",
        "# 3) 真实训练（需要空闲 8GB GPU 与已缓存/已批准的模型权重）",
        "make train TRAIN_ARGS=--execute",
        "```",
        "",
        "训练依赖（可选组 `train`）：`uv sync --group train`。",
        "",
        "## 限制",
        "",
        "- 三分类 `business_type` 是数据集里唯一跨 split 不重叠的意图标签；"
        "细粒度 `intent_cluster` 在 train/dev/test 之间不同，不能作为监督目标。",
        "- 训练集 202 条检索查询，test 50 条；样本量小，指标区间宽，"
        "不宣称统计显著。",
        "- 合成数据 + 单人按构造标注，不等于真实银行业务效果。",
        "- 8GB 显存 + batch=1 + 梯度累积只能做小规模条件实验；"
        "7B 仍贴近上限，需 dry-run 实测序列长度。",
        "",
        "## 产物",
        "",
    ]
    for key, value in artifacts.items():
        lines.append(f"- `{key}`：`{value}`")
    lines.append("")
    return "\n".join(lines)


def write_training_artifacts(
    *,
    artifact_root: Path,
    run_id: str,
    manifest: Mapping[str, Any],
    model_card: str,
    config_payload: Mapping[str, Any],
    dry_run_payload: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    validate_training_manifest(manifest)
    run_dir = Path(artifact_root) / run_id
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        raise TrainingManifestError(
            f"run 目录已存在，拒绝覆盖不可变产物：{manifest_path}"
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "model-card.md").write_text(model_card, encoding="utf-8")
    (run_dir / "config.json").write_text(
        json.dumps(config_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if dry_run_payload is not None:
        (run_dir / "dry-run.json").write_text(
            json.dumps(dry_run_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return {
        "run_dir": run_dir,
        "manifest": manifest_path,
        "model_card": run_dir / "model-card.md",
        "config": run_dir / "config.json",
    }
