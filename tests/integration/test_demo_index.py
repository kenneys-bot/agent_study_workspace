"""真实 PostgreSQL 上的演示知识导入与版本化索引（M3，契约 §3，ADR-0002/0005）。

由 ``make test-integration`` 在空库迁移后运行；本文件只做集成验证：

- 演示种子在真实 PostgreSQL 上幂等（第二次全部跳过，不新增版本）；
- 过期/未生效条目已发布但不进入正式检索，从而不进索引；
- 新草稿版本与跨范围版本不进入索引；
- 索引构建/重建/校准哈希/活动指针/失败保留旧索引在真实数据库上成立；
- Chroma 集合按 ``index_version`` 命名，条数与清单一致。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from bank_assistant.infra.db import session_scope
from bank_assistant.infra.models import KnowledgeChunk, KnowledgeVersion, User

# 先导入 demo_seed（它会先初始化 api.errors，规避 service 的循环导入）。
from bank_assistant.knowledge.demo_seed import (
    DemoKnowledgeEntry,
    import_demo_knowledge,
    load_demo_entries,
)
from bank_assistant.knowledge.embedding import (
    EmbeddingUnavailable,
    FakeEmbeddingProvider,
    calibration_vector_sha256,
)
from bank_assistant.knowledge.indexing import (
    ChromaVectorStore,
    IndexBuildError,
    build_index,
    collection_name,
    load_active_index,
    manifest_path,
    read_active_pointer,
    read_manifest,
    search_index,
)
from bank_assistant.knowledge.retrieval import fetch_retrievable_versions
from bank_assistant.knowledge.service import (
    create_draft_version,
    publish_version,
    submit_version,
)
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password

pytestmark = pytest.mark.integration

SUFFIX = uuid4().hex[:8]
SCOPE = f"demo-index-{SUFFIX}"
OTHER_SCOPE = f"demo-index-other-{SUFFIX}"
# 固定时刻：过期条目（2025-01-01 失效）与未生效条目（2027-01-01 生效）都被排除。
NOW = datetime(2026, 6, 1, tzinfo=UTC)
DEMO_PASSWORD = "demo-only-correct-horse-battery"


@pytest.fixture(scope="module")
def actors() -> dict[str, Identity]:
    """真实数据库里的两个演示账号（作者/审核人）与另一个范围的账号。"""
    specs = {
        "author": ("agent", SCOPE),
        "reviewer": ("reviewer", SCOPE),
        "other_author": ("agent", OTHER_SCOPE),
        "other_reviewer": ("reviewer", OTHER_SCOPE),
    }
    created: dict[str, Identity] = {}
    with session_scope() as session:
        for key, (role, scope_id) in specs.items():
            user = User(
                user_id=uuid4(),
                username=f"{key}-{SUFFIX}",
                password_hash=hash_password(DEMO_PASSWORD),
                role=role,
                scope_id=scope_id,
                active=True,
            )
            session.add(user)
            session.flush()
            created[key] = Identity(
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                scope_id=user.scope_id,
            )
    return created


@pytest.fixture(scope="module")
def seeded(actors: dict[str, Identity]) -> tuple[DemoKnowledgeEntry, ...]:
    entries = load_demo_entries()
    with session_scope() as session:
        report = import_demo_knowledge(
            session,
            entries,
            author=actors["author"],
            reviewer=actors["reviewer"],
            scope_id=SCOPE,
        )
    assert report.published_versions == len(entries)
    return entries


def _signature(hits) -> list[tuple[object, int, int]]:
    return [(hit.version_id, hit.start_offset, hit.end_offset) for hit in hits]


def test_demo_seed_is_idempotent_on_postgres(
    actors: dict[str, Identity], seeded: tuple[DemoKnowledgeEntry, ...]
) -> None:
    with session_scope() as session:
        report = import_demo_knowledge(
            session,
            seeded,
            author=actors["author"],
            reviewer=actors["reviewer"],
            scope_id=SCOPE,
        )
        retrievable = fetch_retrievable_versions(session, scope_id=SCOPE, at=NOW)
        stored = session.scalar(
            select(func.count())
            .select_from(KnowledgeVersion)
            .where(KnowledgeVersion.reviewer_id == actors["reviewer"].user_id)
        )

    assert report.published_versions == 0
    assert report.created_documents == 0
    assert report.skipped_versions == len(seeded)
    assert stored == len(seeded)
    # 过期 / 未生效的两条边界条目已发布，但不参与正式检索。
    assert len(retrievable) == len(seeded) - 2


def test_rebuild_on_postgres_switches_pointer_and_keeps_results(
    actors: dict[str, Identity], seeded: tuple[DemoKnowledgeEntry, ...], tmp_path: Path
) -> None:
    provider = FakeEmbeddingProvider()
    store = ChromaVectorStore(tmp_path / "chroma")

    with session_scope() as session:
        first = build_index(
            session,
            provider=provider,
            store=store,
            scope_id=SCOPE,
            index_dir=tmp_path,
            at=NOW,
        )
    first_manifest = read_manifest(manifest_path(tmp_path, first.index_version))
    assert first.version_count == len(seeded) - 2
    assert first.chunk_count == store.count(collection_name(first.index_version))
    assert first_manifest["calibration_vector_sha256"] == calibration_vector_sha256(
        provider
    )
    first_hits = search_index(
        store,
        provider,
        index_version=first.index_version,
        query="挂失后多久可以补卡",
        top_k=3,
    )
    assert first_hits

    with session_scope() as session:
        second = build_index(
            session,
            provider=provider,
            store=store,
            scope_id=SCOPE,
            index_dir=tmp_path,
            at=NOW,
        )

    assert second.index_version != first.index_version
    pointer = read_active_pointer(tmp_path)
    assert pointer is not None
    assert pointer["index_version"] == second.index_version
    # 旧集合仍保留（可回滚），新集合条数与清单一致。
    assert store.count(collection_name(first.index_version)) == first.chunk_count
    assert store.count(collection_name(second.index_version)) == second.chunk_count

    second_hits = search_index(
        store,
        provider,
        index_version=second.index_version,
        query="挂失后多久可以补卡",
        top_k=3,
    )
    assert _signature(second_hits) == _signature(first_hits)

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.index_version == second.index_version
                )
            )
        )
        versions = {
            version.version_id: version
            for version in session.scalars(
                select(KnowledgeVersion).where(
                    KnowledgeVersion.version_id.in_(
                        {row.version_id for row in rows}
                    )
                )
            )
        }
        assert len(rows) == second.chunk_count
        for row in rows:
            content = versions[row.version_id].content
            assert row.text == content[row.start_offset : row.end_offset]
            assert row.embedding_model_version == provider.model_version

    _, verified = load_active_index(tmp_path, provider=provider, store=store)
    assert verified["index_version"] == second.index_version


def test_draft_and_cross_scope_versions_do_not_enter_index(
    actors: dict[str, Identity], seeded: tuple[DemoKnowledgeEntry, ...], tmp_path: Path
) -> None:
    with session_scope() as session:
        _, draft = create_draft_version(
            session,
            actors["author"],
            title=f"草稿-{SUFFIX}",
            source_type="demo_policy",
            source_uri=f"https://demo.invalid/draft/{SUFFIX}",
            business_type="lost_card",
            content="【虚构演示制度】草稿版本不应进入正式检索或索引。",
            valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        )
        _, other_version = create_draft_version(
            session,
            actors["other_author"],
            title=f"跨范围-{SUFFIX}",
            source_type="demo_policy",
            source_uri=f"https://demo.invalid/other/{SUFFIX}",
            business_type="lost_card",
            content="【虚构演示制度】其他范围的版本不应进入本范围索引。",
            valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        )
        submit_version(
            session,
            actors["other_author"],
            other_version.version_id,
            expected_revision=1,
        )
        publish_version(
            session,
            actors["other_reviewer"],
            other_version.version_id,
            expected_revision=2,
            review_reason="集成测试：另一个范围的已发布版本",
        )

    provider = FakeEmbeddingProvider()
    store = ChromaVectorStore(tmp_path / "chroma")
    with session_scope() as session:
        result = build_index(
            session,
            provider=provider,
            store=store,
            scope_id=SCOPE,
            index_dir=tmp_path,
            at=NOW,
        )
        draft_rows = session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.version_id == draft.version_id)
        )

    snapshot_ids = {
        entry["version_id"] for entry in result.manifest["knowledge_snapshot"]
    }
    assert str(draft.version_id) not in snapshot_ids
    assert str(other_version.version_id) not in snapshot_ids
    assert result.version_count == len(seeded) - 2
    assert draft_rows == 0


def test_failed_rebuild_keeps_previous_pointer_and_rows(
    actors: dict[str, Identity], seeded: tuple[DemoKnowledgeEntry, ...], tmp_path: Path
) -> None:
    provider = FakeEmbeddingProvider()
    store = ChromaVectorStore(tmp_path / "chroma")
    with session_scope() as session:
        first = build_index(
            session,
            provider=provider,
            store=store,
            scope_id=SCOPE,
            index_dir=tmp_path,
            at=NOW,
        )
    with session_scope() as session:
        rows_before = session.scalar(select(func.count()).select_from(KnowledgeChunk))

    class FailingProvider:
        model_version = "broken@512"
        dimension = 512

        def embed(self, texts):  # noqa: ARG002
            raise EmbeddingUnavailable("模型不可用（集成测试注入）")

    with pytest.raises(IndexBuildError):
        with session_scope() as session:
            build_index(
                session,
                provider=FailingProvider(),
                store=store,
                scope_id=SCOPE,
                index_dir=tmp_path,
                at=NOW,
            )

    pointer = read_active_pointer(tmp_path)
    assert pointer is not None
    assert pointer["index_version"] == first.index_version
    with session_scope() as session:
        rows_after = session.scalar(select(func.count()).select_from(KnowledgeChunk))
    assert rows_after == rows_before
