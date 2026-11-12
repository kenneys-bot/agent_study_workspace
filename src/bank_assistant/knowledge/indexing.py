"""版本化知识索引：分块落库、向量集合、清单与活动指针（M3，契约 §3）。

设计要点（ADR-0002/ADR-0005）：

- PostgreSQL 是事实源；索引可由「已发布且在有效期、范围」的知识版本重建；
- 每次构建得到新的 ``index_version``，Chroma 集合按 ``index_version`` 命名，
  旧集合保留，重建不覆盖旧索引；
- 清单 JSON（``data/index/manifests/<index_version>.json``）记录
  ``index_version``、``embedding_model_version``、知识快照（version_id +
  content_hash）与**校准向量哈希**（固定校准串向量的 SHA-256）；
- 构建并校验成功后，原子切换活动指针 ``data/index/active.json``
  （临时文件 + ``os.replace``，同一文件系统内原子）；构建失败删除临时集合、
  回滚分块行、保留旧指针，并抛出 ``IndexBuildError``；
- 加载索引前用当前 provider 重新计算校准哈希，不一致即判定 embedding 模型
  漂移，拒绝使用（``EmbeddingDrift``）。

``ChunkVectorStore`` 是向量库 seam：生产用 ``ChromaVectorStore``，测试用内存
替身，因此构建/切换/失败路径都能离线验证。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
)
from bank_assistant.knowledge.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    chunk_text,
)
from bank_assistant.knowledge.embedding import (
    CALIBRATION_TEXT,
    EmbeddingProvider,
    calibration_vector_sha256,
    ensure_dimension,
)
from bank_assistant.knowledge.retrieval import fetch_retrievable_versions

COLLECTION_PREFIX = "knowledge_"
ACTIVE_POINTER_FILENAME = "active.json"
MANIFEST_DIRNAME = "manifests"
INDEX_SCHEMA_VERSION = 1
EMBED_BATCH_SIZE = 32
DEFAULT_INDEX_DIR = Path("data/index")


class KnowledgeIndexError(Exception):
    """索引领域的错误基类；脚本/API 必须如实报错，不能静默使用旧索引。"""


class IndexBuildError(KnowledgeIndexError):
    """索引构建或校验失败；旧索引与旧指针保持不变。"""


class IndexNotBuilt(KnowledgeIndexError):
    """还没有活动索引（``active.json`` 不存在或清单缺失）。"""


class EmbeddingDrift(KnowledgeIndexError):
    """校准向量哈希或模型标识不一致：embedding 模型已漂移，索引不可信。"""


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """写入向量库的一条分块（含向量与检索过滤所需的元数据）。"""

    chunk_id: UUID
    version_id: UUID
    business_type: str
    source_type: str
    scope_id: str
    start_offset: int
    end_offset: int
    text: str
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class VectorHit:
    """一次向量检索的命中；``distance`` 为余弦距离（越小越近）。"""

    chunk_id: UUID
    version_id: UUID
    text: str
    start_offset: int
    end_offset: int
    distance: float


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    """构建结果摘要；``manifest`` 已写入磁盘，``pointer`` 已切换。"""

    index_version: str
    collection: str
    manifest: dict[str, Any]
    pointer: dict[str, Any]
    chunk_count: int
    version_count: int


@runtime_checkable
class ChunkVectorStore(Protocol):
    """向量库最小接口；Chroma 与内存替身都实现它。"""

    kind: str

    def add(self, collection: str, records: Sequence[ChunkRecord]) -> None:
        """写入整批分块；集合不存在时创建。"""
        ...

    def query(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        top_k: int,
        business_type: str | None = None,
    ) -> list[VectorHit]:
        """按余弦距离返回最近邻；可按业务类型预先过滤。"""
        ...

    def count(self, collection: str) -> int:
        """集合内条数；用于构建后校验。"""
        ...

    def delete(self, collection: str) -> None:
        """删除集合；仅用于清理构建失败的临时集合。"""
        ...


# ---------------------------------------------------------------- 路径与命名


def ensure_index_dir(index_dir: Path | str) -> Path:
    """创建索引目录并返回 ``Path``；幂等。"""
    directory = Path(index_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def collection_name(index_version: str) -> str:
    """Chroma 集合名：按 ``index_version`` 命名，旧版本集合保留。"""
    return f"{COLLECTION_PREFIX}{index_version}"


def manifest_path(index_dir: Path | str, index_version: str) -> Path:
    return Path(index_dir) / MANIFEST_DIRNAME / f"{index_version}.json"


def active_pointer_path(index_dir: Path | str) -> Path:
    return Path(index_dir) / ACTIVE_POINTER_FILENAME


def chunk_id_for(
    index_version: str, version_id: UUID, start_offset: int, end_offset: int
) -> UUID:
    """确定性 chunk_id：同一构建重复执行不产生重复行，重建后仍可追溯。"""
    name = f"bank-assistant:chunk:{index_version}:{version_id}:{start_offset}:{end_offset}"
    return uuid5(NAMESPACE_URL, name)


def _generate_index_version(moment: datetime, seed: str) -> str:
    """``idx-<UTC时间戳>-<随机短哈希>``；每次构建唯一，便于保留旧版本。"""
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{stamp}:{seed}:{uuid4().hex}".encode()).hexdigest()[:8]
    return f"idx-{stamp}-{suffix}"


def _fsync_directory(directory: Path) -> None:
    """把目录项也刷盘，降低掉电后指针文件丢失的概率（POSIX）。"""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - 非 POSIX 文件系统
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - 部分文件系统不支持目录 fsync
        pass
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """同目录临时文件 + ``os.replace``：读者只会看到旧内容或新内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_manifest(path: Path | str) -> dict[str, Any]:
    """读取索引清单 JSON；损坏的清单不能被当成可用索引。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IndexNotBuilt(f"索引清单不可读：{path}（{exc}）") from exc
    if not isinstance(payload, dict) or "index_version" not in payload:
        raise IndexNotBuilt(f"索引清单结构非法：{path}")
    return payload


def read_active_pointer(index_dir: Path | str) -> dict[str, Any] | None:
    """读取活动指针；尚未构建索引时返回 ``None``。"""
    path = active_pointer_path(index_dir)
    if not path.exists():
        return None
    return read_manifest(path)


def switch_active_pointer(
    index_dir: Path | str,
    *,
    index_version: str,
    manifest: dict[str, Any],
    switched_at: datetime | None = None,
) -> dict[str, Any]:
    """原子切换活动指针；失败时旧指针保持不变。"""
    moment = switched_at or datetime.now(UTC)
    payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "index_version": index_version,
        "collection": manifest["collection"],
        "embedding_model_version": manifest["embedding_model_version"],
        "calibration_vector_sha256": manifest["calibration_vector_sha256"],
        "manifest": f"{MANIFEST_DIRNAME}/{index_version}.json",
        "switched_at": moment.isoformat(),
    }
    _atomic_write_json(active_pointer_path(index_dir), payload)
    return payload


# ---------------------------------------------------------------- 构建与校验


def _batch_embed(provider: EmbeddingProvider, texts: Sequence[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for offset in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = list(texts[offset : offset + EMBED_BATCH_SIZE])
        embedded = provider.embed(batch)
        if len(embedded) != len(batch):
            raise IndexBuildError(
                f"embedding 返回 {len(embedded)} 条向量，期望 {len(batch)} 条"
            )
        ensure_dimension(provider, embedded)
        vectors.extend(embedded)
    return vectors


def build_index(
    session: Session,
    *,
    provider: EmbeddingProvider,
    store: ChunkVectorStore,
    scope_id: str,
    index_dir: Path | str = DEFAULT_INDEX_DIR,
    at: datetime | None = None,
    business_type: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> IndexBuildResult:
    """从可检索知识版本构建新索引，校验后切换活动指针。

    失败时：删除临时集合、回滚分块行、保留旧清单与旧指针，抛
    ``IndexBuildError``。成功时返回的 ``index_version`` 已写入活动指针。
    """
    moment = at or datetime.now(UTC)
    directory = ensure_index_dir(index_dir)
    versions = fetch_retrievable_versions(
        session, scope_id=scope_id, business_type=business_type, at=moment
    )
    if not versions:
        raise IndexBuildError(
            f"没有可索引的知识版本（scope={scope_id}, business_type={business_type}）"
        )

    documents = {
        document.document_id: document
        for document in session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.document_id.in_(
                    {version.document_id for version in versions}
                )
            )
        )
    }

    index_version = _generate_index_version(moment, provider.model_version)
    collection = collection_name(index_version)
    manifest_file = manifest_path(directory, index_version)

    try:
        calibration_hash = calibration_vector_sha256(provider)

        pieces: list[tuple[KnowledgeVersion, Any]] = []
        for version in versions:
            document = documents.get(version.document_id)
            if document is None:  # pragma: no cover - 外键保证不会发生
                raise IndexBuildError(f"知识版本 {version.version_id} 缺少文档")
            versions_chunks = chunk_text(
                version.content, max_chars=max_chars, overlap=overlap
            )
            if not versions_chunks:
                raise IndexBuildError(f"知识版本 {version.version_id} 正文为空")
            for piece in versions_chunks:
                pieces.append((version, piece))

        vectors = _batch_embed(provider, [piece.text for _, piece in pieces])

        records: list[ChunkRecord] = []
        chunk_rows: list[KnowledgeChunk] = []
        per_version_chunks: dict[UUID, int] = {}
        for (version, piece), vector in zip(pieces, vectors, strict=True):
            document = documents[version.document_id]
            chunk_id = chunk_id_for(
                index_version, version.version_id, piece.start, piece.end
            )
            records.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    version_id=version.version_id,
                    business_type=document.business_type,
                    source_type=document.source_type,
                    scope_id=document.scope_id,
                    start_offset=piece.start,
                    end_offset=piece.end,
                    text=piece.text,
                    embedding=tuple(float(value) for value in vector),
                )
            )
            chunk_rows.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    version_id=version.version_id,
                    text=piece.text,
                    start_offset=piece.start,
                    end_offset=piece.end,
                    embedding_model_version=provider.model_version,
                    index_version=index_version,
                )
            )
            per_version_chunks[version.version_id] = (
                per_version_chunks.get(version.version_id, 0) + 1
            )

        snapshot = [
            {
                "version_id": str(version.version_id),
                "document_id": str(version.document_id),
                "version_no": version.version_no,
                "content_hash": version.content_hash,
                "chunk_count": per_version_chunks[version.version_id],
            }
            for version in versions
        ]
        manifest: dict[str, Any] = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "index_version": index_version,
            "collection": collection,
            "embedding_model_version": provider.model_version,
            "embedding_dimension": provider.dimension,
            "calibration_text_sha256": hashlib.sha256(
                CALIBRATION_TEXT.encode("utf-8")
            ).hexdigest(),
            "calibration_vector_sha256": calibration_hash,
            "scope_id": scope_id,
            "business_type": business_type,
            "created_at": moment.isoformat(),
            "chunk_max_chars": max_chars,
            "chunk_overlap": overlap,
            "version_count": len(versions),
            "chunk_count": len(records),
            "knowledge_snapshot": snapshot,
        }

        session.add_all(chunk_rows)
        session.flush()
        store.add(collection, records)
        stored = store.count(collection)
        if stored != len(records):
            raise IndexBuildError(
                f"集合 {collection} 条数 {stored} 与清单 {len(records)} 不一致"
            )
        _atomic_write_json(manifest_file, manifest)
        session.commit()
    except Exception as exc:
        _safe_delete(store, collection)
        session.rollback()
        manifest_file.unlink(missing_ok=True)
        raise IndexBuildError(f"索引构建失败，保留旧索引：{exc}") from exc

    try:
        pointer = switch_active_pointer(
            directory,
            index_version=index_version,
            manifest=manifest,
            switched_at=moment,
        )
    except Exception as exc:
        # 指针写入是原子的：失败时旧指针仍指向旧索引，不能谎报切换成功。
        raise IndexBuildError(
            f"索引已构建但切换活动指针失败，旧指针保留：{exc}"
        ) from exc
    return IndexBuildResult(
        index_version=index_version,
        collection=collection,
        manifest=manifest,
        pointer=pointer,
        chunk_count=len(records),
        version_count=len(versions),
    )


def _safe_delete(store: ChunkVectorStore, collection: str) -> None:
    """清理失败构建的临时集合；清理失败不能掩盖原始错误。"""
    try:
        store.delete(collection)
    except Exception:  # noqa: BLE001 - 原始异常更重要
        pass


def verify_index(
    *,
    provider: EmbeddingProvider,
    store: ChunkVectorStore,
    manifest: dict[str, Any],
) -> None:
    """校验清单与当前 embedding 提供方、集合条数是否一致。"""
    if manifest.get("embedding_model_version") != provider.model_version:
        raise EmbeddingDrift(
            "embedding 模型标识与清单不一致："
            f"清单 {manifest.get('embedding_model_version')!r}，"
            f"当前 {provider.model_version!r}"
        )
    if manifest.get("embedding_dimension") != provider.dimension:
        raise EmbeddingDrift(
            f"embedding 维度与清单不一致：清单 {manifest.get('embedding_dimension')}，"
            f"当前 {provider.dimension}"
        )
    expected = manifest.get("calibration_vector_sha256")
    if expected != calibration_vector_sha256(provider):
        raise EmbeddingDrift(
            "校准向量哈希不一致：embedding 模型已漂移，索引不可信，必须重建"
        )
    collection = manifest.get("collection")
    stored = store.count(collection)
    if stored != manifest.get("chunk_count"):
        raise IndexBuildError(
            f"集合 {collection} 条数 {stored} 与清单 {manifest.get('chunk_count')} 不一致"
        )


def load_active_index(
    index_dir: Path | str,
    *,
    provider: EmbeddingProvider,
    store: ChunkVectorStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """加载并校验活动索引；返回 ``(pointer, manifest)``。"""
    pointer = read_active_pointer(index_dir)
    if pointer is None:
        raise IndexNotBuilt("尚无活动索引；请先 make index-rebuild")
    manifest_file = Path(index_dir) / pointer["manifest"]
    manifest = read_manifest(manifest_file)
    verify_index(provider=provider, store=store, manifest=manifest)
    return pointer, manifest


def search_index(
    store: ChunkVectorStore,
    provider: EmbeddingProvider,
    *,
    index_version: str,
    query: str,
    top_k: int = 5,
    business_type: str | None = None,
) -> list[VectorHit]:
    """在指定索引版本上召回最近邻；业务过滤在向量库侧下推。"""
    if not query.strip():
        raise ValueError("查询不能为空")
    vectors = provider.embed([query])
    ensure_dimension(provider, vectors)
    return store.query(
        collection_name(index_version),
        vectors[0],
        top_k=top_k,
        business_type=business_type,
    )


# ---------------------------------------------------------------- Chroma 实现


class ChromaVectorStore:
    """Chroma 持久化向量库；集合按 ``index_version`` 命名（ADR-0002）。"""

    kind = "chroma"

    def __init__(self, persist_directory: Path | str) -> None:
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - 依赖随 uv.lock 安装
            raise IndexBuildError("chromadb 未安装；请先 uv sync") from exc
        directory = Path(persist_directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.persist_directory = str(directory)
        self._client = chromadb.PersistentClient(path=str(directory))

    def add(self, collection: str, records: Sequence[ChunkRecord]) -> None:
        if not records:
            return
        target = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )
        target.add(
            ids=[str(record.chunk_id) for record in records],
            embeddings=[list(record.embedding) for record in records],
            documents=[record.text for record in records],
            metadatas=[
                {
                    "version_id": str(record.version_id),
                    "business_type": record.business_type,
                    "source_type": record.source_type,
                    "scope_id": record.scope_id,
                    "start_offset": record.start_offset,
                    "end_offset": record.end_offset,
                }
                for record in records
            ],
        )

    def query(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        top_k: int,
        business_type: str | None = None,
    ) -> list[VectorHit]:
        target = self._client.get_collection(name=collection)
        kwargs: dict[str, Any] = {
            "query_embeddings": [list(vector)],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if business_type is not None:
            kwargs["where"] = {"business_type": business_type}
        result = target.query(**kwargs)
        hits: list[VectorHit] = []
        for chunk_id, document, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
            strict=True,
        ):
            hits.append(
                VectorHit(
                    chunk_id=UUID(chunk_id),
                    version_id=UUID(metadata["version_id"]),
                    text=document,
                    start_offset=int(metadata["start_offset"]),
                    end_offset=int(metadata["end_offset"]),
                    distance=float(distance),
                )
            )
        return hits

    def count(self, collection: str) -> int:
        return int(self._client.get_collection(name=collection).count())

    def delete(self, collection: str) -> None:
        self._client.delete_collection(name=collection)


__all__ = [
    "ACTIVE_POINTER_FILENAME",
    "COLLECTION_PREFIX",
    "DEFAULT_INDEX_DIR",
    "INDEX_SCHEMA_VERSION",
    "MANIFEST_DIRNAME",
    "ChunkRecord",
    "ChunkVectorStore",
    "ChromaVectorStore",
    "EmbeddingDrift",
    "IndexBuildError",
    "IndexBuildResult",
    "IndexNotBuilt",
    "KnowledgeIndexError",
    "VectorHit",
    "active_pointer_path",
    "build_index",
    "chunk_id_for",
    "collection_name",
    "ensure_index_dir",
    "load_active_index",
    "manifest_path",
    "read_active_pointer",
    "read_manifest",
    "search_index",
    "switch_active_pointer",
    "verify_index",
]
