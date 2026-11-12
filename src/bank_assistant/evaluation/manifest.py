"""运行 manifest 与评测产物落盘（T27，#28；契约 §6）。

每次正式运行都输出一个不可变目录：``<ARTIFACT_ROOT>/evals/<run_id>/``，内含
``manifest.json``、``metrics.json`` 与 ``predictions.jsonl``。manifest 的
``metrics_path`` / ``predictions_path`` 是相对 ARTIFACT_ROOT 的路径，不写死
个人机器绝对路径。

契约 §6 的 ``mode`` 用 ``cloud / local_model / replay`` 表达模型来源；本任务
同时要求区分 ``live / replay`` 的调用方式。因此 manifest 同时记录：

- ``mode``：``live``（真实模型调用）或 ``replay``（确定性回放，无网络、无成本）；
- ``model_mode``：``cloud`` / ``local_model`` / ``replay``，与契约 §6 对齐。

两个字段必须自洽；回放结果不得混进 live 报告。

本模块只做字段与文件系统操作，不读凭据。manifest 会在写入前扫描键名与
值中的凭据形态，命中即拒绝写入（公开仓库不得出现任何 secret）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "CONTRACT_VERSION",
    "REQUIRED_MANIFEST_FIELDS",
    "ManifestError",
    "RunContext",
    "assert_no_secrets",
    "build_run_manifest",
    "validate_manifest",
    "write_run_artifacts",
]

CONTRACT_VERSION = "bank-customer-service-contracts#6"
MODES: frozenset[str] = frozenset({"live", "replay"})
MODEL_MODES: frozenset[str] = frozenset({"cloud", "local_model", "replay"})
STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "budget_exceeded", "blocked"}
)

REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    # 契约 §6 最低字段
    "run_id",
    "task",
    "code_sha",
    "worktree_clean",
    "mode",
    "model_mode",
    "dataset_id",
    "dataset_version",
    "dataset_manifest_sha256",
    "split",
    "split_manifest_sha256",
    "dataset_test_sha256",
    "knowledge_snapshot",
    "rule_snapshot",
    "prompt_version",
    "model_provider",
    "model_version",
    "embedding_version",
    "index_version",
    "seed",
    "hardware_summary",
    "hardware",
    "started_at",
    "finished_at",
    "status",
    "metrics_path",
    "predictions_path",
    # 追溯用附加字段
    "contract_version",
    "prompt_components",
    "environment_ref",
)

_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "secret_key",
        "password",
        "passwd",
        "token",
        "access_token",
        "authorization",
    }
)
_FORBIDDEN_KEY_SUFFIXES: tuple[str, ...] = ("_api_key", "_secret", "_password")
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"\b(DEEPSEEK|LLM|DASHSCOPE)_API_KEY\s*=", re.IGNORECASE),
    re.compile(r"\bapi[_-]?key\s*[:=]\s*\S{6,}", re.IGNORECASE),
)
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ManifestError(ValueError):
    """manifest 缺字段、mode 不自洽或包含疑似凭据。"""


@dataclass(frozen=True, slots=True)
class RunContext:
    """一次评测运行的追溯上下文；时间必须是带时区的 UTC。"""

    run_id: str
    task: str
    mode: str
    model_mode: str
    dataset_id: str
    dataset_version: str
    dataset_manifest_sha256: str
    split: str
    split_manifest_sha256: str
    dataset_test_sha256: str
    knowledge_snapshot: str
    rule_snapshot: str
    prompt_version: str
    prompt_components: Mapping[str, str]
    model_provider: str
    model_version: str
    embedding_version: str
    index_version: str
    seed: int
    code_sha: str
    worktree_clean: bool
    hardware_summary: str
    hardware: Mapping[str, Any]
    started_at: datetime
    finished_at: datetime
    status: str
    environment_ref: str = "docs/environment.md"
    notes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ManifestError(f"mode 只能是 {sorted(MODES)}，收到 {self.mode!r}")
        if self.model_mode not in MODEL_MODES:
            raise ManifestError(
                f"model_mode 只能是 {sorted(MODEL_MODES)}，收到 {self.model_mode!r}"
            )
        if self.status not in STATUSES:
            raise ManifestError(
                f"status 只能是 {sorted(STATUSES)}，收到 {self.status!r}"
            )
        for label in ("started_at", "finished_at"):
            value = getattr(self, label)
            if value.tzinfo is None:
                raise ManifestError(f"{label} 必须带时区（契约 §1：统一 UTC）")


def _iso(value: datetime) -> str:
    return value.isoformat()


def build_run_manifest(
    *,
    context: RunContext,
    metrics_path: str | None = None,
    predictions_path: str | None = None,
) -> dict[str, Any]:
    """按契约 §6 组装 manifest；路径缺省为 ``None``，由落盘步骤回填。"""
    manifest: dict[str, Any] = {
        "run_id": context.run_id,
        "task": context.task,
        "code_sha": context.code_sha,
        "worktree_clean": context.worktree_clean,
        "mode": context.mode,
        "model_mode": context.model_mode,
        "dataset_id": context.dataset_id,
        "dataset_version": context.dataset_version,
        "dataset_manifest_sha256": context.dataset_manifest_sha256,
        "split": context.split,
        "split_manifest_sha256": context.split_manifest_sha256,
        "dataset_test_sha256": context.dataset_test_sha256,
        "knowledge_snapshot": context.knowledge_snapshot,
        "rule_snapshot": context.rule_snapshot,
        "prompt_version": context.prompt_version,
        "prompt_components": dict(context.prompt_components),
        "model_provider": context.model_provider,
        "model_version": context.model_version,
        "embedding_version": context.embedding_version,
        "index_version": context.index_version,
        "seed": context.seed,
        "hardware_summary": context.hardware_summary,
        "hardware": dict(context.hardware),
        "environment_ref": context.environment_ref,
        "started_at": _iso(context.started_at),
        "finished_at": _iso(context.finished_at),
        "status": context.status,
        "metrics_path": metrics_path,
        "predictions_path": predictions_path,
        "contract_version": CONTRACT_VERSION,
        "notes": dict(context.notes),
    }
    assert_no_secrets(manifest)
    return manifest


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    """校验字段完整性与枚举值；一次列出全部缺失字段，便于修复。"""
    missing = [
        field
        for field in REQUIRED_MANIFEST_FIELDS
        if field not in manifest or manifest[field] in (None, "")
    ]
    if missing:
        raise ManifestError(f"manifest 缺少必填字段：{', '.join(missing)}")
    if manifest["mode"] not in MODES:
        raise ManifestError(f"manifest mode 非法：{manifest['mode']!r}")
    if manifest["model_mode"] not in MODEL_MODES:
        raise ManifestError(f"manifest model_mode 非法：{manifest['model_mode']!r}")
    if manifest["status"] not in STATUSES:
        raise ManifestError(f"manifest status 非法：{manifest['status']!r}")
    if manifest["mode"] == "replay" and manifest["model_mode"] != "replay":
        raise ManifestError("replay 运行的 model_mode 必须是 replay，不能混入真实模型指标")
    if manifest["mode"] == "live" and manifest["model_mode"] == "replay":
        raise ManifestError("live 运行的 model_mode 不能是 replay")
    if manifest["split"] != "test":
        raise ManifestError("正式评测 manifest 的 split 只能是冻结 test")
    if not isinstance(manifest["seed"], int):
        raise ManifestError("manifest seed 必须是整数")
    assert_no_secrets(manifest)


def assert_no_secrets(payload: Any) -> None:
    """递归扫描键名与字符串值；命中疑似凭据即拒绝。"""
    problems: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                lowered = str(key).strip().lower()
                if lowered in _FORBIDDEN_KEYS or lowered.endswith(
                    _FORBIDDEN_KEY_SUFFIXES
                ):
                    problems.append(f"{path}.{key} 是凭据字段")
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        elif isinstance(node, str):
            for pattern in _SECRET_VALUE_PATTERNS:
                if pattern.search(node):
                    problems.append(f"{path} 疑似包含凭据")
                    break

    walk(payload, "manifest")
    if problems:
        raise ManifestError("manifest 拒绝写入疑似凭据：" + "；".join(problems[:5]))


def _safe_run_dir(artifact_root: Path, run_id: str) -> Path:
    if not _SAFE_RUN_ID.match(run_id) or ".." in run_id:
        raise ManifestError(f"run_id 不能包含路径分隔符或 ..：{run_id!r}")
    return artifact_root / "evals" / run_id


def write_run_artifacts(
    *,
    artifact_root: Path | str,
    manifest: Mapping[str, Any],
    metrics: Mapping[str, Any],
    predictions: Iterable[Mapping[str, Any]],
    overwrite: bool = False,
) -> dict[str, str]:
    """写入 manifest/metrics/predictions；默认拒绝覆盖已有运行目录。"""
    root = Path(artifact_root)
    run_id = str(manifest.get("run_id", ""))
    run_dir = _safe_run_dir(root, run_id)
    if run_dir.exists() and not overwrite:
        raise ManifestError(
            f"运行目录已存在：{run_dir}；失败实验也要保留，不能静默覆盖"
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = run_dir / "metrics.json"
    predictions_path = run_dir / "predictions.jsonl"
    manifest_path = run_dir / "manifest.json"

    with predictions_path.open("w", encoding="utf-8") as handle:
        for record in predictions:
            handle.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            )
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stored = dict(manifest)
    stored["metrics_path"] = metrics_path.relative_to(root).as_posix()
    stored["predictions_path"] = predictions_path.relative_to(root).as_posix()
    validate_manifest(stored)
    manifest_path.write_text(
        json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "run_dir": str(run_dir),
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "metrics_path": stored["metrics_path"],
        "predictions_path": stored["predictions_path"],
    }
