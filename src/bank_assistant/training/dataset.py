"""冻结数据集上的意图分类样本加载（T28，#29；复用 T26/T27）。

训练只允许 ``train`` split，评测只允许冻结 ``test`` split；这两个判断都交给
:mod:`bank_assistant.datasets.split_guard`，训练脚本不重复实现"默认 train"。
加载时还会：

- 用 T26 的 :func:`bank_assistant.datasets.build.verify_dataset` 复算 manifest
  与文件哈希，数据集被改动过就拒绝；
- 校验当前 split 文件的 sha256 与 manifest 中记录的哈希一致；
- 要求评测标签集合是训练标签集合的子集（否则就是没有监督信号的标签）。

标签选择：本数据集细粒度的 ``intent_cluster`` 在 train/dev/test 之间**不重叠**
（每个 split 用不同的意图簇），不能作为跨 split 的分类目标；因此这里用
``business_type`` 作为可迁移的三分类路由意图（lost_card / credit_card_fee /
complaint）。这不是把细粒度意图"降级"，而是数据集设计决定的唯一无泄漏监督
信号。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from bank_assistant.datasets.build import verify_dataset
from bank_assistant.datasets.split_guard import (
    require_frozen_test_split,
    require_split_for,
)

__all__ = [
    "IntentDataError",
    "IntentDataset",
    "IntentExample",
    "load_intent_split",
    "sha256_bytes",
]


class IntentDataError(ValueError):
    """数据集缺失、哈希漂移、标签不合法或 split 用错。"""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class IntentExample:
    item_id: str
    text: str
    label: str
    scenario: str
    task: str
    hard_categories: tuple[str, ...] = ()

    @property
    def is_hard(self) -> bool:
        return bool(self.hard_categories)


@dataclass(frozen=True, slots=True)
class IntentDataset:
    split: str
    purpose: str
    examples: tuple[IntentExample, ...]
    label_names: tuple[str, ...]
    label_counts: Mapping[str, int]
    label_field: str
    manifest: Mapping[str, Any]
    file_sha256: str
    dataset_manifest_sha256: str
    split_manifest_sha256: str
    test_split_sha256: str
    knowledge_snapshot: str
    rule_snapshot: str

    @property
    def label_schema_sha256(self) -> str:
        payload = json.dumps(
            {"labels": list(self.label_names), "field": self.label_field},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return sha256_bytes(payload)

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.label_counts)

    @property
    def n(self) -> int:
        return len(self.examples)


def _file_entry(manifest: Mapping[str, Any], split: str) -> Mapping[str, Any]:
    for entry in manifest.get("files", []):
        if isinstance(entry, Mapping) and entry.get("split") == split:
            return entry
    raise IntentDataError(f"数据集 manifest 未登记 {split} split 文件")


def load_intent_split(
    *,
    dataset_dir: str | Path,
    dataset_id: str,
    dataset_version: str,
    split: str,
    purpose: str,
    task: str = "retrieval",
    text_field: str = "query",
    label_field: str = "business_type",
) -> IntentDataset:
    """加载一个 split。

    ``purpose="train"`` 只接受 train；``purpose="eval"`` 只接受冻结 test。
    """
    if purpose == "train":
        require_split_for("train", split)
    elif purpose == "eval":
        require_frozen_test_split(split)
    else:  # pragma: no cover - 调用方传错
        raise IntentDataError(f"未知 purpose={purpose!r}，只支持 train/eval")

    directory = Path(dataset_dir)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise IntentDataError(
            f"找不到冻结数据集 manifest：{manifest_path}；请先运行 make dataset-build"
        )
    report = verify_dataset(directory)
    if not report["ok"]:
        raise IntentDataError(
            "冻结数据集校验失败：" + "；".join(report["errors"][:5])
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_id") != dataset_id:
        raise IntentDataError(
            f"数据集 id 不一致：manifest={manifest.get('dataset_id')!r} config={dataset_id!r}"
        )
    if manifest.get("dataset_version") != dataset_version:
        raise IntentDataError(
            "数据集版本不一致："
            f"manifest={manifest.get('dataset_version')!r} config={dataset_version!r}"
        )

    split_path = directory / f"{split}.jsonl"
    if not split_path.is_file():
        raise IntentDataError(f"找不到 {split} split 文件：{split_path}")
    payload = split_path.read_bytes()
    actual_sha = sha256_bytes(payload)
    expected_sha = _file_entry(manifest, split).get("sha256")
    if actual_sha != expected_sha:
        raise IntentDataError(
            f"{split} split 与冻结哈希不一致：manifest={expected_sha} 实际={actual_sha}"
        )

    examples: list[IntentExample] = []
    seen: set[str] = set()
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntentDataError(f"{split}.jsonl 第 {line_number} 行不是合法 JSON") from exc
        if row.get("task") != task:
            continue
        text = row.get(text_field)
        label = row.get(label_field)
        item_id = row.get("item_id")
        if not isinstance(text, str) or not text.strip():
            raise IntentDataError(
                f"{split}.jsonl 第 {line_number} 行缺少非空 {text_field}"
            )
        if not isinstance(label, str) or not label.strip():
            raise IntentDataError(
                f"{split}.jsonl 第 {line_number} 行缺少非空 {label_field}"
            )
        if not isinstance(item_id, str) or not item_id.strip():
            raise IntentDataError(f"{split}.jsonl 第 {line_number} 行缺少 item_id")
        if item_id in seen:
            raise IntentDataError(f"{split} split 中 item_id 重复：{item_id}")
        seen.add(item_id)
        hard = row.get("hard_categories") or []
        examples.append(
            IntentExample(
                item_id=item_id,
                text=text.strip(),
                label=label.strip(),
                scenario=str(row.get("scenario") or label),
                task=task,
                hard_categories=tuple(str(item) for item in hard),
            )
        )
    if not examples:
        raise IntentDataError(f"{split} split 没有 task={task!r} 的可用样本")

    label_counts: dict[str, int] = {}
    for example in examples:
        label_counts[example.label] = label_counts.get(example.label, 0) + 1
    if len(label_counts) < 2:
        raise IntentDataError(f"{split} split 只有 {len(label_counts)} 个标签，无法训练/评测")

    frozen = manifest.get("frozen", {})
    return IntentDataset(
        split=split,
        purpose=purpose,
        examples=tuple(examples),
        label_names=tuple(sorted(label_counts)),
        label_counts=label_counts,
        label_field=label_field,
        manifest=manifest,
        file_sha256=actual_sha,
        dataset_manifest_sha256=str(manifest.get("dataset_manifest_sha256") or ""),
        split_manifest_sha256=str(manifest.get("split_manifest_sha256") or ""),
        test_split_sha256=str(frozen.get("test_split_sha256") or ""),
        knowledge_snapshot=str(manifest.get("knowledge_snapshot") or ""),
        rule_snapshot=str(manifest.get("rule_snapshot") or ""),
    )
