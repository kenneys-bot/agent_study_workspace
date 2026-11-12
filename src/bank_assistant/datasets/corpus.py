"""M6 数据集只读语料层（T26，#27）。

数据集必须可离线重建，因此这里只读仓库内的合成演示语料：

- ``data/demo/knowledge/*.json``：三场景虚构演示知识与公开监管要点摘要；
- ``data/demo/rules/*.json``：六条虚构演示质检规则。

本模块不连接数据库、不调用模型、不访问网络；索引分块调用
:func:`bank_assistant.knowledge.chunking.chunk_text` 的纯函数，保证数据集里的
``chunk_id`` 与真实索引使用同一套切分规则（``<doc_id>#c<序号>``）。``chunk_id``
是数据集内的稳定键，评测时按 ``(doc_id, start, end)`` 映射到实际索引的 chunk。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bank_assistant.knowledge.chunking import TextChunk, chunk_text

__all__ = [
    "CORPUS_AS_OF",
    "DEMO_KNOWLEDGE_DIR",
    "DEMO_RULES_DIR",
    "ChunkRef",
    "Corpus",
    "KnowledgeEntry",
    "RuleEntry",
    "load_corpus",
    "snapshot_sha256",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_KNOWLEDGE_DIR = REPO_ROOT / "data" / "demo" / "knowledge"
DEMO_RULES_DIR = REPO_ROOT / "data" / "demo" / "rules"

# 数据集对语料有效期的固定观察时点：2026-06-01。写死而不是取当前时间，保证
# 同一种子在任意机器上的“有效/过期/未生效”判定完全一致。
CORPUS_AS_OF = datetime(2026, 6, 1, tzinfo=UTC)


def canonical_bytes(payload: Any) -> bytes:
    """稳定的 JSON 序列化：键排序、紧凑、UTF-8，用于哈希而不用于人读。"""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """一条合成知识；``doc_id`` 就是知识文档 id，来源类型只有两类。"""

    doc_id: str
    file_name: str
    scenario: str
    business_type: str
    title: str
    source_type: str
    source_uri: str
    content: str
    version_no: int
    valid_from: datetime
    valid_to: datetime | None

    def is_active(self, as_of: datetime = CORPUS_AS_OF) -> bool:
        if self.valid_from > as_of:
            return False
        return self.valid_to is None or self.valid_to > as_of

    def chunks(self) -> tuple[ChunkRef, ...]:
        return tuple(
            ChunkRef(
                chunk_id=f"{self.doc_id}#c{index}",
                doc_id=self.doc_id,
                index=index,
                span=chunk,
            )
            for index, chunk in enumerate(chunk_text(self.content))
        )


@dataclass(frozen=True, slots=True)
class ChunkRef:
    """数据集内的稳定分块键；``span`` 与真实索引的码点区间一致。"""

    chunk_id: str
    doc_id: str
    index: int
    span: TextChunk

    def to_record(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "start": self.span.start,
            "end": self.span.end,
            "text_sha256": hashlib.sha256(
                self.span.text.encode("utf-8")
            ).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class RuleEntry:
    """一条演示规则；``applicability`` 决定跨场景时会话为何“不适用”。"""

    rule_code: str
    file_name: str
    scenario: str
    business_type: str
    title: str
    category: str
    severity: str
    applicability: dict[str, Any]
    criteria: dict[str, Any]
    exceptions: tuple[str, ...]
    version_no: int

    def hashable(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "version_no": self.version_no,
            "business_type": self.business_type,
            "severity": self.severity,
            "applicability": self.applicability,
            "criteria": self.criteria,
            "exceptions": list(self.exceptions),
        }


@dataclass(frozen=True, slots=True)
class Corpus:
    knowledge: tuple[KnowledgeEntry, ...]
    rules: tuple[RuleEntry, ...]

    def knowledge_by_business_type(self, business_type: str) -> tuple[KnowledgeEntry, ...]:
        return tuple(
            entry
            for entry in self.knowledge
            if entry.business_type == business_type and entry.is_active()
        )

    def entry(self, doc_id: str) -> KnowledgeEntry:
        for entry in self.knowledge:
            if entry.doc_id == doc_id:
                return entry
        raise KeyError(f"语料中没有知识条目 {doc_id!r}")

    def rule(self, rule_code: str) -> RuleEntry:
        for entry in self.rules:
            if entry.rule_code == rule_code:
                return entry
        raise KeyError(f"语料中没有规则 {rule_code!r}")

    def knowledge_snapshot(self) -> str:
        return snapshot_sha256(
            [
                {
                    "doc_id": entry.doc_id,
                    "file_name": entry.file_name,
                    "version_no": entry.version_no,
                    "business_type": entry.business_type,
                    "source_type": entry.source_type,
                    "source_uri": entry.source_uri,
                    "valid_from": entry.valid_from.isoformat(),
                    "valid_to": entry.valid_to.isoformat() if entry.valid_to else None,
                    "content_sha256": hashlib.sha256(
                        entry.content.encode("utf-8")
                    ).hexdigest(),
                }
                for entry in self.knowledge
            ]
        )

    def rule_snapshot(self) -> str:
        return snapshot_sha256([entry.hashable() for entry in self.rules])


def snapshot_sha256(entries: list[dict[str, Any]]) -> str:
    """按 ``doc_id``/``rule_code`` 排序后哈希，避免文件读取顺序影响结果。"""
    key = "rule_code" if entries and "rule_code" in entries[0] else "doc_id"
    ordered = sorted(entries, key=lambda item: item[key])
    return hashlib.sha256(canonical_bytes(ordered)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise ValueError(f"演示语料格式不合法：{path}")
    return payload


def load_corpus(
    *,
    knowledge_dir: Path = DEMO_KNOWLEDGE_DIR,
    rules_dir: Path = DEMO_RULES_DIR,
) -> Corpus:
    """读取并校验仓库内的合成知识与规则；缺少语料必须显式失败。"""
    knowledge: list[KnowledgeEntry] = []
    for path in sorted(knowledge_dir.glob("*.json")):
        payload = _read_json(path)
        for entry in payload["entries"]:
            if not entry.get("demo_only") and entry.get("source_type") != "public_regulation":
                raise ValueError(f"{entry.get('id')!r} 不是合成演示知识")
            knowledge.append(
                KnowledgeEntry(
                    doc_id=str(entry["id"]),
                    file_name=path.name,
                    scenario=str(payload.get("scenario", "")),
                    business_type=str(entry["business_type"]),
                    title=str(entry["title"]),
                    source_type=str(entry["source_type"]),
                    source_uri=str(entry["source_uri"]),
                    content=str(entry["content"]),
                    version_no=int(entry["version_no"]),
                    valid_from=datetime.fromisoformat(entry["valid_from"]),
                    valid_to=_iso(entry.get("valid_to")),
                )
            )

    rules: list[RuleEntry] = []
    for path in sorted(rules_dir.glob("*.json")):
        payload = _read_json(path)
        for entry in payload["entries"]:
            if entry.get("demo_only") is not True:
                raise ValueError(f"规则 {entry.get('rule_code')!r} 必须显式标注 demo_only")
            rules.append(
                RuleEntry(
                    rule_code=str(entry["rule_code"]),
                    file_name=path.name,
                    scenario=str(payload.get("scenario", "")),
                    business_type=str(entry["business_type"]),
                    title=str(entry["title"]),
                    category=str(entry["category"]),
                    severity=str(entry["severity"]),
                    applicability=dict(entry["applicability"]),
                    criteria=dict(entry["criteria"]),
                    exceptions=tuple(entry.get("exceptions", [])),
                    version_no=int(entry["version_no"]),
                )
            )

    if len(knowledge) < 30:
        raise ValueError(f"演示知识条目过少：{len(knowledge)}")
    if len(rules) != 6:
        raise ValueError(f"演示规则应为 6 条，实际 {len(rules)}")
    return Corpus(knowledge=tuple(knowledge), rules=tuple(rules))
