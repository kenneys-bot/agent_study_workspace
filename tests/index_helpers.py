"""索引测试替身：内存向量库（离线、确定性、不联网）。

生产实现是 Chroma（``ChromaVectorStore``）；单元测试用本替身，保证测试不
依赖 Chroma 的持久化目录与后台线程，也避免为每个用例写磁盘。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from uuid import UUID

from bank_assistant.knowledge.indexing import ChunkRecord, VectorHit


class MemoryVectorStore:
    """按集合名隔离的内存向量库，余弦距离与 Chroma 的 ``hnsw:space=cosine`` 对齐。"""

    kind = "memory"

    def __init__(self) -> None:
        self.collections: dict[str, dict[UUID, ChunkRecord]] = {}

    def collection_names(self) -> list[str]:
        return sorted(self.collections)

    def add(self, collection: str, records: Sequence[ChunkRecord]) -> None:
        bucket = self.collections.setdefault(collection, {})
        for record in records:
            if record.chunk_id in bucket:
                raise ValueError(f"重复 chunk_id：{record.chunk_id}")
            bucket[record.chunk_id] = record

    def count(self, collection: str) -> int:
        return len(self.collections.get(collection, {}))

    def delete(self, collection: str) -> None:
        self.collections.pop(collection, None)

    def query(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        top_k: int,
        business_type: str | None = None,
    ) -> list[VectorHit]:
        records = self.collections.get(collection, {})
        candidates = [
            record
            for record in records.values()
            if business_type is None or record.business_type == business_type
        ]
        hits: list[VectorHit] = []
        for record in candidates:
            hits.append(
                VectorHit(
                    chunk_id=record.chunk_id,
                    version_id=record.version_id,
                    text=record.text,
                    start_offset=record.start_offset,
                    end_offset=record.end_offset,
                    distance=_cosine_distance(vector, record.embedding),
                )
            )
        hits.sort(key=lambda hit: (hit.distance, str(hit.chunk_id)))
        return hits[:top_k]


def _cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)
