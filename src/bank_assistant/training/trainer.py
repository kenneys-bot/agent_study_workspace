"""条件性 4-bit QLoRA 意图分类实验编排（T28，#29）。

设计原则：

- **默认不训练**：CLI 必须显式 ``--execute`` 才会启动真实训练；``--dry-run``
  只做配置/数据/哈希/分词/微型 CPU 前反向校验，并写 ``NOT_RUN`` 报告。
- **资源闸门**：训练前探测 ``nvidia-smi``；GPU 被别的任务占用且未获准
  time-sharing 时写 ``NOT_RUN``，不杀进程、不排队。
- **冻结 test**：训练只用 train split；test 只在训练结束后评测一次，不用来
  挑 checkpoint（本实验不保存中间 checkpoint）。
- **可追溯**：manifest 记录 code sha、数据哈希、超参、seed、硬件、显存峰值、
  耗时与状态；model card 由 manifest 生成。
- **离线可测**：torch/transformers/peft 都是可选依赖（``uv sync --group train``），
  模块导入不依赖它们；dry-run 在缺依赖时如实记录 ``SKIPPED``。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from bank_assistant.datasets.corpus import REPO_ROOT
from bank_assistant.training.config import TrainConfig, TrainConfigError
from bank_assistant.training.dataset import (
    IntentDataError,
    IntentDataset,
    IntentExample,
    load_intent_split,
)
from bank_assistant.training.evaluate import (
    IntentMetrics,
    build_intent_prompt,
    compare_metrics,
    dump_predictions,
    evaluate_examples,
    majority_baseline,
)
from bank_assistant.training.hardware import HardwareProbe, probe_hardware
from bank_assistant.training.manifest import (
    TrainingRunContext,
    build_training_manifest,
    render_model_card,
    write_training_artifacts,
)

__all__ = [
    "ExperimentOutcome",
    "TrainingError",
    "dependency_status",
    "inspect_tokenization",
    "run_experiment",
    "run_tiny_cpu_smoke",
]

_TRAIN_DEPS = ("torch", "transformers", "peft", "bitsandbytes", "accelerate")

class TrainingError(RuntimeError):
    """训练/评测执行期错误；调用方必须把它写成失败状态，而不是静默通过。"""


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    manifest: Mapping[str, Any]
    paths: Mapping[str, Path]
    exit_code: int

    @property
    def status(self) -> str:
        return str(self.manifest["status"])


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _code_sha() -> str:
    injected = os.environ.get("CODE_SHA", "").strip()
    if injected:
        return injected[:64]
    return (_git("rev-parse", "HEAD") or "unknown")[:64]


def _worktree_clean() -> bool:
    return _git("status", "--porcelain") == ""


def dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in _TRAIN_DEPS}


def _gpu_payload(probe: HardwareProbe) -> dict[str, Any]:
    processes = [dict(item) for item in probe.compute_processes]
    busy_reason = None
    if processes:
        busy_reason = "；".join(
            f"{item.get('process_name')} pid={item.get('pid')} "
            f"{item.get('used_memory_mib')} MiB"
            for item in processes
        )
    return {
        "available": probe.gpu_available,
        "busy": probe.gpu_busy,
        "name": probe.gpu_name,
        "driver_version": probe.driver_version,
        "total_vram_gib": probe.total_vram_gib,
        "used_vram_gib": probe.used_vram_gib,
        "free_vram_gib": probe.free_vram_gib,
        "utilization_pct": probe.gpu_utilization_pct,
        "compute_processes": processes,
        "busy_reason": busy_reason,
        "probed_at": probe.probed_at,
        "raw_query": probe.raw_query,
        "raw_processes": probe.raw_processes,
        "error": probe.error,
        "command": (
            "nvidia-smi --query-gpu=name,driver_version,memory.total,"
            "memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits"
        ),
    }


def inspect_tokenization(
    config: TrainConfig,
    train: IntentDataset,
    test: IntentDataset,
) -> dict[str, Any]:
    """若 tokenizer 已在本地缓存则统计真实长度；绝不联网下载。"""
    try:
        from transformers import AutoTokenizer
    except Exception as exc:  # noqa: BLE001 - 缺依赖必须降级为 SKIPPED
        return {
            "status": "SKIPPED",
            "reason": f"transformers 不可用（{type(exc).__name__}: {exc}）；未做真实分词",
            "max_seq_length": config.training.max_seq_length,
        }
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            config.model.name,
            revision=config.model.revision,
            trust_remote_code=config.model.trust_remote_code,
            local_files_only=True,
        )
    except Exception as exc:  # noqa: BLE001 - 缓存缺失/权限/模型不支持
        return {
            "status": "SKIPPED",
            "reason": (
                f"tokenizer 未在本地缓存（local_files_only=True，拒绝下载）："
                f"{type(exc).__name__}"
            ),
            "max_seq_length": config.training.max_seq_length,
        }

    def _lengths(dataset: IntentDataset) -> list[int]:
        """按实际训练序列（prompt + 标签 + EOS）统计，而不是只数原问题。"""
        lengths: list[int] = []
        for example in dataset.examples:
            sequence = build_intent_prompt(dataset.label_names, example.text)
            sequence += " " + example.label
            encoded = tokenizer(sequence, add_special_tokens=False)
            lengths.append(len(encoded["input_ids"]) + 1)  # + EOS
        return lengths

    train_lengths = _lengths(train)
    test_lengths = _lengths(test)
    limit = config.training.max_seq_length
    return {
        "status": "SUCCEEDED",
        "tokenizer_class": type(tokenizer).__name__,
        "max_seq_length": limit,
        "train": _length_stats(train_lengths, limit),
        "test": _length_stats(test_lengths, limit),
        "local_files_only": True,
    }


def _length_stats(lengths: list[int], limit: int) -> dict[str, Any]:
    if not lengths:
        return {"n": 0}
    ordered = sorted(lengths)

    def pct(fraction: float) -> int:
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    return {
        "n": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 2),
        "p50": pct(0.5),
        "p95": pct(0.95),
        "max": ordered[-1],
        "over_limit": sum(1 for value in ordered if value > limit),
    }


def run_tiny_cpu_smoke(*, max_seq_length: int = 64) -> dict[str, Any]:
    """随机初始化微型 Qwen2 + LoRA 的 CPU 前向/反向；不下载、不用 GPU。"""
    try:
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import Qwen2Config, Qwen2ForCausalLM
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "SKIPPED",
            "reason": f"训练可选依赖未安装（uv sync --group train）："
            f"{type(exc).__name__}: {exc}",
        }
    try:
        torch.manual_seed(0)
        model_config = Qwen2Config(
            vocab_size=128,
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=2,
            num_attention_heads=4,
            num_key_value_heads=2,
            max_position_embeddings=128,
            tie_word_embeddings=True,
        )
        model = Qwen2ForCausalLM(model_config)
        lora = LoraConfig(
            r=4,
            lora_alpha=8,
            lora_dropout=0.0,
            target_modules=["q_proj", "v_proj"],
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora)
        model.train()
        seq_len = min(16, max_seq_length)
        generator = torch.Generator().manual_seed(0)
        token_ids = torch.randint(1, 128, (1, seq_len), generator=generator)
        output = model(input_ids=token_ids, labels=token_ids)
        loss = output.loss
        loss.backward()
        trainable = sum(
            parameter.numel()
            for parameter in model.parameters()
            if parameter.requires_grad
        )
        return {
            "status": "SUCCEEDED",
            "loss": round(float(loss.detach()), 6),
            "trainable_params": int(trainable),
            "device": "cpu",
            "seq_len": seq_len,
            "note": (
                "随机初始化微型 Qwen2 + LoRA 的一次 forward/backward；"
                "只验证训练机制，不涉及候选模型权重，也不代表任何质量指标。"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _summary_eval(
    *,
    test: IntentDataset,
    baseline: IntentMetrics,
    tuned: IntentMetrics,
    majority: IntentMetrics,
    prompt_version: str,
    paths: Mapping[str, str],
) -> dict[str, Any]:
    comparison = compare_metrics(baseline, tuned)
    return {
        "available": True,
        "split": "test",
        "n": test.n,
        "prompt_version": prompt_version,
        "protocol": (
            "冻结 test split 单次评测；baseline=同一基础模型 zero-shot，"
            "tuned=LoRA adapter；prompt/解码参数/seed 完全相同；"
            "test 不参与训练或 checkpoint 选择。"
        ),
        "baseline": comparison["baseline"],
        "tuned": comparison["tuned"],
        "delta": comparison["delta"],
        "decision": comparison["decision"],
        "note": comparison["note"],
        "majority_reference": majority.to_dict(),
        "paths": dict(paths),
    }


def _base_eval_block(
    *,
    test: IntentDataset,
    majority: IntentMetrics,
    prompt_version: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "available": False,
        "split": "test",
        "n": test.n,
        "prompt_version": prompt_version,
        "reason": reason,
        "baseline": None,
        "tuned": None,
        "delta": None,
        "decision": None,
        "majority_reference": majority.to_dict(),
        "note": (
            "训练未执行，因此没有 baseline vs tuned 对比；"
            "majority_reference 只是多数类参考下限，不是基线模型。"
        ),
        "paths": {},
    }


def _dataset_step(dataset: IntentDataset, label: str) -> dict[str, Any]:
    return {
        "name": label,
        "status": "SUCCEEDED",
        "detail": (
            f"split={dataset.split} n={dataset.n} labels={list(dataset.label_names)} "
            f"sha256={dataset.file_sha256[:12]}…"
        ),
    }


def run_experiment(
    config: TrainConfig,
    *,
    dry_run: bool,
    execute: bool = False,
    run_id: str | None = None,
    artifact_root: Path | None = None,
    tiny_smoke: bool = False,
    allow_download: bool = False,
    hardware: HardwareProbe | None = None,
    tiny_smoke_runner: Callable[..., dict[str, Any]] | None = None,
) -> ExperimentOutcome:
    """执行一次 dry-run 或真实训练；两种路径都写完整 manifest/model card。"""
    if dry_run == execute:
        raise TrainConfigError("必须二选一：--dry-run 或 --execute")
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()

    probe = hardware or probe_hardware()
    gpu = _gpu_payload(probe)
    block_reasons = probe.block_reasons(
        min_free_vram_gib=config.hardware.min_free_vram_gib,
        min_total_vram_gib=config.hardware.min_total_vram_gib,
        allow_time_sharing=config.hardware.allow_time_sharing,
    )

    steps: list[dict[str, Any]] = [
        {
            "name": "config_validated",
            "status": "SUCCEEDED",
            "detail": f"source={config.source_path}",
        },
        {
            "name": "hardware_probe",
            "status": "SUCCEEDED" if probe.gpu_available else "NOT_AVAILABLE",
            "detail": probe.summary,
        },
    ]

    train = load_intent_split(
        dataset_dir=config.dataset.dataset_dir,
        dataset_id=config.dataset.dataset_id,
        dataset_version=config.dataset.dataset_version,
        split=config.dataset.split,
        purpose="train",
        task=config.dataset.task,
        text_field=config.dataset.text_field,
        label_field=config.dataset.label_field,
    )
    steps.append(_dataset_step(train, "dataset_train_loaded"))
    test = load_intent_split(
        dataset_dir=config.dataset.dataset_dir,
        dataset_id=config.dataset.dataset_id,
        dataset_version=config.dataset.dataset_version,
        split=config.evaluation.split,
        purpose="eval",
        task=config.dataset.task,
        text_field=config.dataset.text_field,
        label_field=config.dataset.label_field,
    )
    steps.append(_dataset_step(test, "dataset_test_loaded"))
    missing_labels = sorted(set(test.label_names) - set(train.label_names))
    if missing_labels:
        raise IntentDataError(
            "test split 含训练集没有的标签，无法评测：" + ", ".join(missing_labels)
        )

    majority_predictions, majority_metrics = majority_baseline(train, test)
    tokenization = inspect_tokenization(config, train, test)
    steps.append(
        {
            "name": "tokenization",
            "status": tokenization["status"],
            "detail": tokenization.get("reason")
            or (
                f"真实分词：train p95={tokenization['train'].get('p95')} "
                f"max={tokenization['train'].get('max')}（上限 "
                f"{config.training.max_seq_length}）"
            ),
        }
    )

    smoke_result: dict[str, Any] | None = None
    if tiny_smoke:
        runner = tiny_smoke_runner or run_tiny_cpu_smoke
        smoke_result = runner(max_seq_length=config.training.max_seq_length)
        steps.append(
            {
                "name": "tiny_cpu_smoke",
                "status": smoke_result.get("status", "UNKNOWN"),
                "detail": smoke_result.get("reason")
                or (
                    f"loss={smoke_result.get('loss')} "
                    f"trainable_params={smoke_result.get('trainable_params')} "
                    f"device={smoke_result.get('device')}"
                ),
            }
        )
        if smoke_result.get("status") == "FAILED":
            raise TrainingError(
                "微型 CPU 前反向失败：" + str(smoke_result.get("reason"))
            )
    else:
        steps.append(
            {
                "name": "tiny_cpu_smoke",
                "status": "SKIPPED",
                "detail": "默认不跑；显式 --tiny-smoke 才做一次微型 CPU 前反向",
            }
        )

    dependencies = dependency_status()
    if dry_run:
        steps.append(
            {
                "name": "training",
                "status": "SKIPPED",
                "detail": "dry-run：未启动真实 QLoRA 训练",
            }
        )
        steps.append(
            {
                "name": "evaluation",
                "status": "SKIPPED",
                "detail": "training 未执行，冻结 test 对比不可用",
            }
        )
        reason_parts = ["dry-run：只做配置/数据/哈希/分词/微型冒烟校验，未启动真实训练"]
        reason_parts.extend(block_reasons)
        status = "NOT_RUN"
        evaluation = _base_eval_block(
            test=test,
            majority=majority_metrics,
            prompt_version=config.evaluation.prompt_version,
            reason=(
                "dry-run 未训练；baseline vs tuned 不可用"
                + ("（" + "；".join(block_reasons) + "）" if block_reasons else "")
            ),
        )
        return _finalize(
            config=config,
            run_id=run_id,
            artifact_root=artifact_root,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status=status,
            status_reason="；".join(reason_parts),
            steps=steps,
            train=train,
            test=test,
            probe=probe,
            gpu=gpu,
            evaluation=evaluation,
            vram_peak_gib=None,
            mode="dry_run",
            extra_artifacts={},
            dry_run_payload={
                "tokenization": tokenization,
                "tiny_cpu_smoke": smoke_result,
                "dependencies": dependencies,
                "gpu_block_reasons": block_reasons,
                "majority_predictions_path": None,
                "majority_metrics": majority_metrics.to_dict(),
                "allow_download": allow_download,
            },
            exit_code=0,
        )

    # ---- 真实训练路径：先过资源闸门 --------------------------------
    steps.append(
        {
            "name": "dependencies",
            "status": "SUCCEEDED"
            if all(dependencies.values())
            else "MISSING",
            "detail": json.dumps(dependencies, ensure_ascii=False),
        }
    )
    if block_reasons:
        steps.append(
            {
                "name": "training",
                "status": "SKIPPED",
                "detail": "资源闸门未通过：" + "；".join(block_reasons),
            }
        )
        steps.append(
            {
                "name": "evaluation",
                "status": "SKIPPED",
                "detail": "training 未执行，冻结 test 对比不可用",
            }
        )
        return _finalize(
            config=config,
            run_id=run_id,
            artifact_root=artifact_root,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="NOT_RUN",
            status_reason="资源闸门未通过：" + "；".join(block_reasons),
            steps=steps,
            train=train,
            test=test,
            probe=probe,
            gpu=gpu,
            evaluation=_base_eval_block(
                test=test,
                majority=majority_metrics,
                prompt_version=config.evaluation.prompt_version,
                reason="资源不足，未训练：" + "；".join(block_reasons),
            ),
            vram_peak_gib=None,
            mode="train",
            extra_artifacts={},
            dry_run_payload=None,
            exit_code=1,
        )
    if not all(dependencies.values()):
        missing = sorted(name for name, ok in dependencies.items() if not ok)
        raise TrainingError(
            "缺少训练依赖：" + ", ".join(missing) + "；先运行 uv sync --group train"
        )

    steps.append(
        {
            "name": "training",
            "status": "RUNNING",
            "detail": "4-bit QLoRA 启动中",
        }
    )
    run_dir_probe = _resolve_run_dir(
        config, run_id=run_id, artifact_root=artifact_root, started_at=started_at
    )
    training_started = time.monotonic()
    try:
        training = _train_qlora(
            config=config,
            run_dir=run_dir_probe,
            train=train,
            test=test,
            allow_download=allow_download,
        )
    except Exception as exc:  # noqa: BLE001 - 失败也必须落 manifest，不能只抛栈
        steps[-1] = {
            "name": "training",
            "status": "FAILED",
            "detail": str(exc),
        }
        steps.append(
            {
                "name": "evaluation",
                "status": "SKIPPED",
                "detail": "training 失败，冻结 test 对比不可用",
            }
        )
        return _finalize(
            config=config,
            run_id=run_id,
            artifact_root=artifact_root,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="FAILED",
            status_reason=f"训练失败：{exc}",
            steps=steps,
            train=train,
            test=test,
            probe=probe,
            gpu=gpu,
            evaluation=_base_eval_block(
                test=test,
                majority=majority_metrics,
                prompt_version=config.evaluation.prompt_version,
                reason=f"训练失败：{exc}",
            ),
            vram_peak_gib=None,
            mode="train",
            extra_artifacts={},
            dry_run_payload=None,
            exit_code=1,
        )
    training_seconds = time.monotonic() - training_started
    steps[-1] = {
        "name": "training",
        "status": "SUCCEEDED",
        "detail": (
            f"adapter={training['adapter_path']} "
            f"train_seconds={round(training_seconds, 1)} "
            f"vram_peak={training['vram_peak_gib']} GiB"
        ),
    }
    steps.append(
        {
            "name": "evaluation",
            "status": "SUCCEEDED",
            "detail": (
                f"frozen test n={test.n}；baseline accuracy="
                f"{training['baseline_metrics'].accuracy} vs tuned="
                f"{training['tuned_metrics'].accuracy}"
            ),
        }
    )
    evaluation = _summary_eval(
        test=test,
        baseline=training["baseline_metrics"],
        tuned=training["tuned_metrics"],
        majority=majority_metrics,
        prompt_version=config.evaluation.prompt_version,
        paths=training["eval_paths"],
    )
    return _finalize(
        config=config,
        run_id=run_id,
        artifact_root=artifact_root,
        started_at=started_at,
        started_monotonic=started_monotonic,
        status="SUCCEEDED",
        status_reason="真实 QLoRA 训练与冻结 test 评测完成",
        steps=steps,
        train=train,
        test=test,
        probe=probe,
        gpu=gpu,
        evaluation=evaluation,
        vram_peak_gib=training["vram_peak_gib"],
        mode="train",
        extra_artifacts=training["artifacts"],
        dry_run_payload={
            "tokenization": tokenization,
            "tiny_cpu_smoke": smoke_result,
            "dependencies": dependencies,
            "training_seconds": round(training_seconds, 2),
            "allow_download": allow_download,
            "majority_metrics": majority_metrics.to_dict(),
        },
        exit_code=0,
    )


def _resolve_run_dir(
    config: TrainConfig,
    *,
    run_id: str | None,
    artifact_root: Path | None,
    started_at: datetime,
) -> Path:
    root = Path(artifact_root) if artifact_root is not None else config.run.artifact_root
    resolved_id = run_id or config.run.run_id or (
        config.model.name.rsplit("/", 1)[-1].lower()
        + "-intent-qlora-"
        + started_at.strftime("%Y%m%dT%H%M%SZ")
        + f"-s{config.run.seed}"
    )
    return root / resolved_id


def _finalize(
    *,
    config: TrainConfig,
    run_id: str | None,
    artifact_root: Path | None,
    started_at: datetime,
    started_monotonic: float,
    status: str,
    status_reason: str,
    steps: list[dict[str, Any]],
    train: IntentDataset,
    test: IntentDataset,
    probe: HardwareProbe,
    gpu: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    vram_peak_gib: float | None,
    mode: str,
    extra_artifacts: Mapping[str, str],
    dry_run_payload: Mapping[str, Any] | None,
    exit_code: int,
) -> ExperimentOutcome:
    finished_at = datetime.now(UTC)
    run_dir = _resolve_run_dir(
        config, run_id=run_id, artifact_root=artifact_root, started_at=started_at
    )
    resolved_id = run_dir.name
    artifacts = {
        "manifest": f"{resolved_id}/manifest.json",
        "model_card": f"{resolved_id}/model-card.md",
        "config": f"{resolved_id}/config.json",
        **{key: f"{resolved_id}/{value}" for key, value in extra_artifacts.items()},
    }
    hyperparameters = config.to_dict()
    hyperparameters.pop("run", None)
    hyperparameters.pop("dataset", None)
    hyperparameters.pop("model", None)
    hyperparameters.pop("evaluation", None)
    hyperparameters.pop("hardware", None)
    context = TrainingRunContext(
        run_id=resolved_id,
        mode=mode,
        code_sha=_code_sha(),
        worktree_clean=_worktree_clean(),
        model_name=config.model.name,
        model_provider=config.model.provider,
        model_revision=config.model.revision,
        model_license=config.model.license,
        model_parameter_count_b=config.model.parameter_count_b,
        dataset_id=train.manifest["dataset_id"],
        dataset_version=train.manifest["dataset_version"],
        dataset_manifest_sha256=train.dataset_manifest_sha256,
        split_manifest_sha256=train.split_manifest_sha256,
        train_split_sha256=train.file_sha256,
        test_split_sha256=test.file_sha256,
        train_items=train.n,
        test_items=test.n,
        label_names=train.label_names,
        label_schema_sha256=train.label_schema_sha256,
        seed=config.run.seed,
        hyperparameters=hyperparameters,
        hardware_summary=probe.summary,
        hardware=probe.to_dict(),
        gpu=gpu,
        started_at=started_at,
        finished_at=finished_at,
        wall_time_seconds=time.monotonic() - started_monotonic,
        vram_peak_gib=vram_peak_gib,
        status=status,
        status_reason=status_reason,
        evaluation=evaluation,
        artifacts=artifacts,
        steps=tuple(steps),
        notes={
            "conditional_task": (
                "微调是条件任务（计划 M6-05/M6-07）；NOT_RUN 时保留基线，"
                "生产不以微调模型为前提。"
            ),
            "test_usage": "冻结 test 仅用于训练后的最终评测，不参与训练或 checkpoint 选择。",
            "allow_download": dry_run_payload.get("allow_download")
            if dry_run_payload
            else None,
            "tiny_cpu_smoke": (dry_run_payload or {}).get("tiny_cpu_smoke"),
            "tokenization": (dry_run_payload or {}).get("tokenization"),
        },
    )
    manifest = build_training_manifest(context)
    model_card = render_model_card(manifest)
    paths = write_training_artifacts(
        artifact_root=Path(artifact_root)
        if artifact_root is not None
        else config.run.artifact_root,
        run_id=resolved_id,
        manifest=manifest,
        model_card=model_card,
        config_payload=config.to_dict(),
        dry_run_payload=dry_run_payload,
    )
    return ExperimentOutcome(manifest=manifest, paths=paths, exit_code=exit_code)


def _train_qlora(
    *,
    config: TrainConfig,
    run_dir: Path,
    train: IntentDataset,
    test: IntentDataset,
    allow_download: bool,
) -> dict[str, Any]:
    """真实 4-bit QLoRA 训练 + 冻结 test 评测。

    这里只做一次训练、不保存中间 checkpoint，因此 test 不可能被用来挑
    checkpoint；评测在训练结束后进行。
    """
    import torch
    from peft import (
        LoraConfig,
        get_peft_model,
        prepare_model_for_kbit_training,
    )
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    )

    torch.manual_seed(config.run.seed)
    torch.cuda.manual_seed_all(config.run.seed)
    local_files_only = not allow_download
    tokenizer = AutoTokenizer.from_pretrained(
        config.model.name,
        revision=config.model.revision,
        trust_remote_code=config.model.trust_remote_code,
        local_files_only=local_files_only,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    compute_dtype = (
        torch.bfloat16
        if config.quantization.compute_dtype == "bfloat16"
        else torch.float16
    )
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type=config.quantization.quant_type,
        bnb_4bit_use_double_quant=config.quantization.double_quant,
        bnb_4bit_compute_dtype=compute_dtype,
    )
    model = AutoModelForCausalLM.from_pretrained(
        config.model.name,
        revision=config.model.revision,
        quantization_config=quantization,
        device_map={"": 0},
        trust_remote_code=config.model.trust_remote_code,
        local_files_only=local_files_only,
        torch_dtype=compute_dtype,
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    lora_config = LoraConfig(
        r=config.lora.r,
        lora_alpha=config.lora.alpha,
        lora_dropout=config.lora.dropout,
        target_modules=list(config.lora.target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    labels = train.label_names
    rows = _encode_training_rows(
        tokenizer=tokenizer,
        examples=train.examples,
        labels=labels,
        max_seq_length=config.training.max_seq_length,
    )
    if not rows:
        raise TrainingError(
            "训练样本在截断后没有可用标签 token；请增大 max_seq_length"
        )

    class _Rows(torch.utils.data.Dataset):
        def __len__(self) -> int:
            return len(rows)

        def __getitem__(self, index: int) -> dict[str, list[int]]:
            return rows[index]

    pad_token_id = tokenizer.pad_token_id

    def collate(batch: list[dict[str, list[int]]]) -> dict[str, Any]:
        max_len = max(len(item["input_ids"]) for item in batch)
        input_ids, attention_mask, label_ids = [], [], []
        for item in batch:
            pad = max_len - len(item["input_ids"])
            input_ids.append(item["input_ids"] + [pad_token_id] * pad)
            attention_mask.append(item["attention_mask"] + [0] * pad)
            label_ids.append(item["labels"] + [-100] * pad)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
            "labels": torch.tensor(label_ids, dtype=torch.long),
        }

    bf16 = bool(config.training.bf16 and torch.cuda.is_bf16_supported())
    arguments = TrainingArguments(
        output_dir=str(run_dir / "checkpoints"),
        per_device_train_batch_size=config.training.per_device_train_batch_size,
        gradient_accumulation_steps=config.training.gradient_accumulation_steps,
        num_train_epochs=config.training.num_train_epochs,
        max_steps=config.training.max_steps,
        learning_rate=config.training.learning_rate,
        lr_scheduler_type=config.training.lr_scheduler_type,
        warmup_ratio=config.training.warmup_ratio,
        logging_steps=config.training.logging_steps,
        save_strategy="no",
        report_to=[],
        bf16=bf16,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        seed=config.run.seed,
        data_seed=config.run.seed,
        optim="paged_adamw_8bit",
        remove_unused_columns=False,
        dataloader_num_workers=0,
        ddp_find_unused_parameters=False,
    )
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    trainer = Trainer(
        model=model,
        args=arguments,
        train_dataset=_Rows(),
        data_collator=collate,
    )
    trainer.train()
    vram_peak_gib = round(torch.cuda.max_memory_allocated() / 1024**3, 3)

    adapter_dir = run_dir / "adapter"
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    eval_dir = run_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    def predict(example: IntentExample) -> str:
        prompt = build_intent_prompt(labels, example.text)
        encoded = tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=config.training.max_seq_length,
        ).to(model.device)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                max_new_tokens=config.evaluation.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_tokens = generated[0][encoded["input_ids"].shape[1] :]
        return tokenizer.decode(new_tokens, skip_special_tokens=True)

    with model.disable_adapter():
        baseline_predictions, baseline_metrics = evaluate_examples(
            predict, test.examples, labels=test.label_names
        )
    tuned_predictions, tuned_metrics = evaluate_examples(
        predict, test.examples, labels=test.label_names
    )
    (eval_dir / "baseline.predictions.jsonl").write_text(
        dump_predictions(baseline_predictions), encoding="utf-8"
    )
    (eval_dir / "tuned.predictions.jsonl").write_text(
        dump_predictions(tuned_predictions), encoding="utf-8"
    )
    comparison = compare_metrics(baseline_metrics, tuned_metrics)
    (eval_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "adapter_path": str(adapter_dir.relative_to(run_dir.parent)),
        "vram_peak_gib": vram_peak_gib,
        "baseline_metrics": baseline_metrics,
        "tuned_metrics": tuned_metrics,
        "eval_paths": {
            "baseline_predictions": f"{run_dir.name}/eval/baseline.predictions.jsonl",
            "tuned_predictions": f"{run_dir.name}/eval/tuned.predictions.jsonl",
            "comparison": f"{run_dir.name}/eval/comparison.json",
        },
        "artifacts": {
            "adapter": "adapter",
            "baseline_predictions": "eval/baseline.predictions.jsonl",
            "tuned_predictions": "eval/tuned.predictions.jsonl",
            "comparison": "eval/comparison.json",
        },
    }


def _encode_training_rows(
    *,
    tokenizer: Any,
    examples: tuple[IntentExample, ...],
    labels: tuple[str, ...],
    max_seq_length: int,
) -> list[dict[str, list[int]]]:
    rows: list[dict[str, list[int]]] = []
    eos_id = tokenizer.eos_token_id
    for example in examples:
        if example.label not in labels:
            raise TrainingError(f"训练样本标签不在标签集合内：{example.label}")
        prompt_ids = tokenizer(
            build_intent_prompt(labels, example.text), add_special_tokens=False
        )["input_ids"]
        completion_ids = tokenizer(
            " " + example.label, add_special_tokens=False
        )["input_ids"] + [eos_id]
        input_ids = (prompt_ids + completion_ids)[:max_seq_length]
        target = ([-100] * len(prompt_ids) + completion_ids)[:max_seq_length]
        if all(token == -100 for token in target):
            continue
        rows.append(
            {
                "input_ids": input_ids,
                "attention_mask": [1] * len(input_ids),
                "labels": target,
            }
        )
    return rows


def write_manifest_summary(manifest: Mapping[str, Any]) -> str:
    """CLI stdout：不含秘密的摘要 JSON。"""
    return json.dumps(
        {
            "run_id": manifest.get("run_id"),
            "status": manifest.get("status"),
            "mode": manifest.get("mode"),
            "status_reason": manifest.get("status_reason"),
            "model": manifest.get("model_name"),
            "dataset": {
                "id": manifest.get("dataset_id"),
                "version": manifest.get("dataset_version"),
                "train_items": manifest.get("train_items"),
                "test_items": manifest.get("test_items"),
                "train_split_sha256": manifest.get("train_split_sha256"),
                "test_split_sha256": manifest.get("test_split_sha256"),
            },
            "seed": manifest.get("seed"),
            "hardware_summary": manifest.get("hardware_summary"),
            "gpu": {
                key: (manifest.get("gpu") or {}).get(key)
                for key in ("available", "busy", "free_vram_gib", "total_vram_gib")
            },
            "wall_time_seconds": manifest.get("wall_time_seconds"),
            "vram_peak_gib": manifest.get("vram_peak_gib"),
            "evaluation_available": (manifest.get("evaluation") or {}).get("available"),
            "artifacts": manifest.get("artifacts"),
            "model_card": (manifest.get("artifacts") or {}).get("model_card"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ += ["write_manifest_summary"]
