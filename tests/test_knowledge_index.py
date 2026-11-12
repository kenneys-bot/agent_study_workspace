"""版本化索引：清单、校准哈希、活动指针与失败保留（M3，契约 §3，ADR-0002/0005）。

seams：
- ``build_index`` / ``load_active_index`` / ``search_index``：索引构建与加载的
  公开接口；向量库用内存替身（生产是 Chroma）。
- ``read_active_pointer`` / ``read_manifest``：``data/index/active.json`` 与清单
  文件的公开读取接口；指针切换的原子性由实现保证，测试观察“切换前/后”。

关键行为：草稿、过期、未来、跨范围版本不进索引；重建产生新 ``index_version``
并切换指针；构建失败保留旧索引与旧指针；校准哈希不一致时拒绝使用索引。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bank_assistant.infra.db import Base
from bank_assistant.infra.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
    User,
)
from bank_assistant.knowledge.embedding import (
    EmbeddingUnavailable,
    FakeEmbeddingProvider,
    calibration_vector_sha256,
)
from bank_assistant.knowledge.indexing import (
    EmbeddingDrift,
    IndexBuildError,
    IndexNotBuilt,
    build_index,
    collection_name,
    load_active_index,
    manifest_path,
    read_active_pointer,
    read_manifest,
    search_index,
)
from bank_assistant.knowledge.lifecycle import KnowledgeStatus, hash_content
from tests.index_helpers import MemoryVectorStore

SCOPE_A = "demo-scope-a"
SCOPE_B = "demo-scope-b"
BUSINESS = "lost_card"
OTHER_BUSINESS = "credit_card_fee"
NOW = datetime(2026, 6, 1, tzinfo=UTC)

LONG_CONTENT = "挂失受理后补卡需要七个工作日。" * 20
EMOJI_CONTENT = (
    "客户说卡丢了😀，坐席说明挂失流程。\n"
    "补卡材料包括身份证件👨‍👩‍👧‍👦与受理回执。\n"
    "费用以柜台公示为准。"
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def author(session: Session) -> User:
    user = User(
        user_id=uuid4(),
        username=f"author-{uuid4().hex[:8]}",
        password_hash="$argon2id$unit-test-placeholder",
        role="agent",
        scope_id=SCOPE_A,
    )
    session.add(user)
    session.flush()
    return user


def _document(
    session: Session,
    *,
    scope_id: str = SCOPE_A,
    business_type: str = BUSINESS,
) -> KnowledgeDocument:
    document = KnowledgeDocument(
        document_id=uuid4(),
        title=f"演示制度-{uuid4().hex[:8]}",
        source_type="demo_policy",
        source_uri=f"https://demo.invalid/{uuid4().hex}",
        business_type=business_type,
        scope_id=scope_id,
    )
    session.add(document)
    session.flush()
    return document


def _version(
    session: Session,
    document: KnowledgeDocument,
    author: User,
    *,
    content: str = EMOJI_CONTENT,
    status: KnowledgeStatus = KnowledgeStatus.PUBLISHED,
    valid_from: datetime = datetime(2026, 5, 1, tzinfo=UTC),
    valid_to: datetime | None = None,
) -> KnowledgeVersion:
    version = KnowledgeVersion(
        version_id=uuid4(),
        document_id=document.document_id,
        version_no=1,
        content=content,
        content_hash=hash_content(content),
        status=status.value,
        valid_from=valid_from,
        valid_to=valid_to,
        author_id=author.user_id,
        revision=1,
    )
    session.add(version)
    session.flush()
    return version


def _build(session, tmp_path, store, provider, **kwargs):
    return build_index(
        session,
        provider=provider,
        store=store,
        scope_id=kwargs.pop("scope_id", SCOPE_A),
        index_dir=tmp_path,
        at=kwargs.pop("at", NOW),
        **kwargs,
    )


def test_build_index_keeps_only_retrievable_versions_and_writes_manifest(
    session: Session, author: User, tmp_path
) -> None:
    in_window_long = _version(
        session, _document(session), author, content=LONG_CONTENT
    )
    in_window_emoji = _version(
        session, _document(session), author, content=EMOJI_CONTENT
    )
    # 以下版本都不能进入索引：
    _version(session, _document(session), author, status=KnowledgeStatus.DRAFT)
    _version(session, _document(session), author, status=KnowledgeStatus.RETIRED)
    _version(
        session,
        _document(session),
        author,
        valid_from=datetime(2026, 3, 1, tzinfo=UTC),
        valid_to=datetime(2026, 4, 1, tzinfo=UTC),
    )
    _version(
        session,
        _document(session),
        author,
        valid_from=datetime(2026, 7, 1, tzinfo=UTC),
    )
    _version(session, _document(session, scope_id=SCOPE_B), author)
    _version(session, _document(session, business_type=OTHER_BUSINESS), author)

    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()
    result = _build(session, tmp_path, store, provider, business_type=BUSINESS)

    manifest = read_manifest(manifest_path(tmp_path, result.index_version))
    snapshot_ids = {
        entry["version_id"] for entry in manifest["knowledge_snapshot"]
    }
    assert snapshot_ids == {
        str(in_window_long.version_id),
        str(in_window_emoji.version_id),
    }
    assert manifest["embedding_model_version"] == provider.model_version
    assert manifest["calibration_vector_sha256"] == calibration_vector_sha256(provider)
    assert manifest["scope_id"] == SCOPE_A
    assert manifest["business_type"] == BUSINESS
    assert manifest["created_at"].endswith("+00:00")
    assert manifest["version_count"] == 2
    assert manifest["chunk_count"] == result.chunk_count
    assert manifest["collection"] == collection_name(result.index_version)
    for entry in manifest["knowledge_snapshot"]:
        version = session.get(KnowledgeVersion, UUID(entry["version_id"]))
        assert version is not None
        assert entry["content_hash"] == version.content_hash
        assert entry["chunk_count"] >= 1

    pointer = read_active_pointer(tmp_path)
    assert pointer is not None
    assert pointer["index_version"] == result.index_version
    assert pointer["embedding_model_version"] == provider.model_version
    assert pointer["calibration_vector_sha256"] == manifest["calibration_vector_sha256"]
    assert pointer["manifest"] == str(
        manifest_path(tmp_path, result.index_version).relative_to(tmp_path)
    )

    # 向量库集合按 index_version 命名，条数与清单、数据库行一致。
    assert collection_name(result.index_version) in store.collection_names()
    assert store.count(collection_name(result.index_version)) == result.chunk_count

    rows = list(
        session.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.index_version == result.index_version
            )
        )
    )
    assert len(rows) == result.chunk_count
    assert {row.version_id for row in rows} == {
        in_window_long.version_id,
        in_window_emoji.version_id,
    }
    contents = {
        in_window_long.version_id: in_window_long.content,
        in_window_emoji.version_id: in_window_emoji.content,
    }
    for row in rows:
        content = contents[row.version_id]
        assert row.embedding_model_version == provider.model_version
        assert 0 <= row.start_offset < row.end_offset <= len(content)
        assert row.text == content[row.start_offset : row.end_offset]


def test_rebuild_creates_new_version_and_switches_pointer_with_same_results(
    session: Session, author: User, tmp_path
) -> None:
    version = _version(session, _document(session), author, content=LONG_CONTENT)
    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()

    first = _build(session, tmp_path, store, provider)
    first_hits = search_index(
        store,
        provider,
        index_version=first.index_version,
        query="补卡需要几个工作日",
    )
    first_manifest = read_manifest(manifest_path(tmp_path, first.index_version))

    second = _build(session, tmp_path, store, provider)
    second_hits = search_index(
        store,
        provider,
        index_version=second.index_version,
        query="补卡需要几个工作日",
    )
    second_manifest = read_manifest(manifest_path(tmp_path, second.index_version))

    assert second.index_version != first.index_version
    assert collection_name(second.index_version) != collection_name(first.index_version)
    pointer = read_active_pointer(tmp_path)
    assert pointer is not None
    assert pointer["index_version"] == second.index_version

    def signature(hits):
        return [
            (hit.version_id, hit.start_offset, hit.end_offset) for hit in hits
        ]

    # 同一批知识版本 + 同一 embedding 模型 → 检索结果（区间与排序）一致。
    assert first_hits
    assert signature(second_hits) == signature(first_hits)
    assert second_manifest["knowledge_snapshot"] == first_manifest["knowledge_snapshot"]
    assert second_manifest["chunk_count"] == first_manifest["chunk_count"]

    # 旧索引版本保留（可回滚），不因为重建被删除。
    assert manifest_path(tmp_path, first.index_version).exists()
    assert collection_name(first.index_version) in store.collection_names()
    stored_versions = set(
        session.scalars(
            select(KnowledgeChunk.index_version).distinct()
        )
    )
    assert {first.index_version, second.index_version} <= stored_versions
    assert version.version_id in {hit.version_id for hit in second_hits}


def test_failed_build_keeps_previous_index_and_pointer(
    session: Session, author: User, tmp_path
) -> None:
    _version(session, _document(session), author, content=LONG_CONTENT)
    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()
    first = _build(session, tmp_path, store, provider)
    chunks_before = len(list(session.scalars(select(KnowledgeChunk))))

    class FailingProvider:
        model_version = "broken@512"
        dimension = 512

        def embed(self, texts):  # noqa: ARG002
            raise EmbeddingUnavailable("模型不可用（测试注入）")

    with pytest.raises(IndexBuildError):
        _build(session, tmp_path, store, FailingProvider())

    pointer = read_active_pointer(tmp_path)
    assert pointer is not None
    assert pointer["index_version"] == first.index_version
    assert store.collection_names() == [collection_name(first.index_version)]
    assert len(list(session.scalars(select(KnowledgeChunk)))) == chunks_before
    manifests = sorted((tmp_path / "manifests").glob("*.json"))
    assert [path.name for path in manifests] == [f"{first.index_version}.json"]


def test_store_failure_cleans_provisional_collection_and_keeps_pointer(
    session: Session, author: User, tmp_path
) -> None:
    _version(session, _document(session), author, content=LONG_CONTENT)
    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()
    first = _build(session, tmp_path, store, provider)
    chunks_before = len(list(session.scalars(select(KnowledgeChunk))))

    class FailingStore(MemoryVectorStore):
        def add(self, collection, records):  # noqa: ARG002
            raise RuntimeError("向量库不可用（测试注入）")

    with pytest.raises(IndexBuildError):
        _build(session, tmp_path, FailingStore(), provider)

    assert store.collection_names() == [collection_name(first.index_version)]
    pointer = read_active_pointer(tmp_path)
    assert pointer["index_version"] == first.index_version
    assert len(list(session.scalars(select(KnowledgeChunk)))) == chunks_before


def test_pointer_switch_failure_keeps_old_pointer_and_reports_error(
    session: Session, author: User, tmp_path, monkeypatch
) -> None:
    _version(session, _document(session), author, content=LONG_CONTENT)
    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()
    first = _build(session, tmp_path, store, provider)

    import bank_assistant.knowledge.indexing as indexing

    def fail_switch(*args, **kwargs):  # noqa: ARG001
        raise OSError("磁盘只读（测试注入）")

    monkeypatch.setattr(indexing, "switch_active_pointer", fail_switch)
    with pytest.raises(IndexBuildError, match="切换活动指针失败"):
        _build(session, tmp_path, store, provider)

    pointer = read_active_pointer(tmp_path)
    assert pointer is not None
    assert pointer["index_version"] == first.index_version



def test_empty_snapshot_is_an_error_and_does_not_create_pointer(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with pytest.raises(IndexBuildError, match="没有可索引"):
            _build(
                session,
                tmp_path,
                MemoryVectorStore(),
                FakeEmbeddingProvider(),
                scope_id="empty-scope",
            )

    assert read_active_pointer(tmp_path) is None
    assert not (tmp_path / "manifests").exists()


def test_load_active_index_rejects_embedding_drift(
    session: Session, author: User, tmp_path
) -> None:
    _version(session, _document(session), author)
    provider = FakeEmbeddingProvider(model_version="fake-model-a@512")
    store = MemoryVectorStore()
    result = _build(session, tmp_path, store, provider)

    drifted = FakeEmbeddingProvider(model_version="fake-model-b@512")
    with pytest.raises(EmbeddingDrift):
        load_active_index(tmp_path, provider=drifted, store=store)

    pointer, manifest = load_active_index(tmp_path, provider=provider, store=store)
    assert pointer["index_version"] == result.index_version
    assert manifest["index_version"] == result.index_version


def test_load_active_index_reports_missing_index(tmp_path) -> None:
    with pytest.raises(IndexNotBuilt):
        load_active_index(
            tmp_path, provider=FakeEmbeddingProvider(), store=MemoryVectorStore()
        )
