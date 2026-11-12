"""知识生命周期集成测试：真实 PostgreSQL + 真实空库迁移（M2-03，契约 §3/§5）。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 起停一次性
PostgreSQL，并在空库上执行 ``alembic upgrade head``；本文件只假设迁移已完成。

seam：
- HTTP：``POST /api/v1/knowledge/documents``、``.../submit``、``.../publish``、
  ``.../retire``、``GET /api/v1/knowledge/versions/{id}``；
- 检索助手 ``fetch_retrievable_versions``：M3 会直接复用，因此这里的过滤
  证据必须在真实 PostgreSQL 上成立，而不是 SQLite 替身；
- 数据库行：发布后内容不可覆盖（数据库层兜底）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from bank_assistant.infra.db import get_engine, get_session_factory, session_scope
from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion, User
from bank_assistant.knowledge.lifecycle import (
    KnowledgeStatus,
    PublishedContentImmutable,
    hash_content,
)
from bank_assistant.knowledge.retrieval import fetch_retrievable_versions
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SUFFIX = uuid4().hex[:8]
SCOPE_A = f"demo-scope-a-{SUFFIX}"
SCOPE_B = f"demo-scope-b-{SUFFIX}"
DEMO_PASSWORD = "demo-only-correct-horse-battery"
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def actors() -> dict[str, dict[str, object]]:
    """真实数据库用户 + 已签名的 Bearer 令牌；角色只由服务端写入。"""
    specs = {
        "author": ("agent", SCOPE_A),
        "reviewer": ("reviewer", SCOPE_A),
        "agent_b": ("agent", SCOPE_B),
        "reviewer_b": ("reviewer", SCOPE_B),
    }
    created: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        for key, (role, scope) in specs.items():
            user = User(
                user_id=uuid4(),
                username=f"{key}-{SUFFIX}",
                password_hash=hash_password(DEMO_PASSWORD),
                role=role,
                scope_id=scope,
                active=True,
            )
            session.add(user)
            session.flush()
            identity = Identity(
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                scope_id=user.scope_id,
            )
            created[key] = {
                "user_id": user.user_id,
                "role": role,
                "scope_id": scope,
                "token": create_access_token(identity),
            }
    return created


def _headers(actors: dict[str, dict[str, object]], key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {actors[key]['token']}"}


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _create(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    *,
    key: str = "author",
    business_type: str | None = None,
    content: str | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    document_id: UUID | None = None,
    title: str = "演示制度：挂失与冻结",
):
    business_type = business_type or f"lost-card-{uuid4().hex[:8]}"
    payload: dict[str, object] = {
        "title": title,
        "source_type": "demo_policy",
        "source_uri": "https://example.invalid/demo-policy/lost-card",
        "business_type": business_type,
        "content": content or f"演示制度正文 {uuid4().hex}",
        "valid_from": _iso(valid_from or (NOW - timedelta(hours=1))),
        "valid_to": _iso(valid_to) if valid_to else None,
    }
    if document_id is not None:
        payload["document_id"] = str(document_id)
    return client.post(
        "/api/v1/knowledge/documents", json=payload, headers=_headers(actors, key)
    )


def _transition(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    version_id: str,
    action: str,
    *,
    key: str,
    revision: int,
    **extra: object,
):
    return client.post(
        f"/api/v1/knowledge/versions/{version_id}/{action}",
        json={"revision": revision, **extra},
        headers=_headers(actors, key),
    )


def _publish_approved(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    version_id: str,
    *,
    key: str = "reviewer",
    revision: int,
) -> dict[str, object]:
    response = _transition(
        client,
        actors,
        version_id,
        "publish",
        key=key,
        revision=revision,
        review_reason="来源与适用范围已核对",
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_database_is_upgraded_to_the_knowledge_head() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"

    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert current == "0003_knowledge"

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"knowledge_documents", "knowledge_versions"} <= tables

    document_columns = {
        column["name"] for column in inspector.get_columns("knowledge_documents")
    }
    assert {
        "document_id",
        "title",
        "source_type",
        "source_uri",
        "business_type",
        "scope_id",
        "created_at",
        "updated_at",
    } <= document_columns

    version_columns = {
        column["name"] for column in inspector.get_columns("knowledge_versions")
    }
    assert {
        "version_id",
        "document_id",
        "version_no",
        "content",
        "content_hash",
        "status",
        "valid_from",
        "valid_to",
        "author_id",
        "reviewer_id",
        "review_reason",
        "revision",
    } <= version_columns

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("0003_knowledge").down_revision == "0002_users"


def test_create_document_writes_draft_version_with_scope_from_identity(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    content = "演示制度：口头挂失后 5 日内补办书面手续。"

    response = client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "演示制度：挂失流程",
            "source_type": "demo_policy",
            "source_uri": "https://example.invalid/demo-policy/lost-card",
            "business_type": "lost-card",
            "content": content,
            "valid_from": _iso(NOW - timedelta(hours=1)),
            "scope_id": SCOPE_B,  # 客户端自报的范围必须被忽略
        },
        headers=_headers(actors, "author"),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == KnowledgeStatus.DRAFT.value
    assert body["version_no"] == 1
    assert body["revision"] == 1
    assert body["content_hash"] == hash_content(content)
    assert body["document"]["scope_id"] == SCOPE_A
    assert body["document"]["business_type"] == "lost-card"
    assert body["document"]["source_type"] == "demo_policy"

    with session_scope() as session:
        version = session.get(KnowledgeVersion, UUID(body["version_id"]))
        assert version is not None
        assert version.status == KnowledgeStatus.DRAFT.value
        assert version.author_id == actors["author"]["user_id"]
        assert version.reviewer_id is None
        # 原文与哈希都来自服务端计算，不是客户端提交的字段。
        assert version.content == content
        assert version.content_hash == hash_content(content)


def test_one_document_supports_multiple_immutable_versions(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    created = _create(client, actors, content="第一版正文：挂失需冻结账户。")
    assert created.status_code == 201, created.text
    first = created.json()

    second = _create(
        client,
        actors,
        content="第二版正文：挂失需冻结账户并核验身份。",
        document_id=UUID(first["document_id"]),
        title="演示制度：挂失与冻结（修订）",
    )

    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["document_id"] == first["document_id"]
    assert second_body["version_no"] == 2
    assert second_body["status"] == KnowledgeStatus.DRAFT.value
    assert second_body["version_id"] != first["version_id"]

    # 追加版本只需要内容与有效期：文档元数据沿用原文档。
    third = client.post(
        "/api/v1/knowledge/documents",
        json={
            "document_id": first["document_id"],
            "content": "第三版正文：挂失后可通过手机银行补办。",
        },
        headers=_headers(actors, "author"),
    )
    assert third.status_code == 201, third.text
    assert third.json()["version_no"] == 3

    original = client.get(
        f"/api/v1/knowledge/versions/{first['version_id']}",
        headers=_headers(actors, "author"),
    )
    assert original.status_code == 200
    assert original.json()["content"] == "第一版正文：挂失需冻结账户。"
    assert original.json()["version_no"] == 1
    # 文档级元数据不随新版本静默改写。
    assert original.json()["document"]["title"] == first["document"]["title"]


def test_lifecycle_publish_retire_and_retrievability(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    business = f"lost-card-{uuid4().hex[:8]}"
    created = _create(
        client,
        actors,
        business_type=business,
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=30),
    )
    assert created.status_code == 201, created.text
    version = created.json()
    version_id = version["version_id"]

    submitted = _transition(
        client,
        actors,
        version_id,
        "submit",
        key="author",
        revision=version["revision"],
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == KnowledgeStatus.IN_REVIEW.value
    assert submitted.json()["revision"] == 2

    published = _publish_approved(
        client, actors, version_id, revision=submitted.json()["revision"]
    )
    assert published["status"] == KnowledgeStatus.PUBLISHED.value
    assert published["outcome"] == "published"
    assert published["reviewer_id"] == str(actors["reviewer"]["user_id"])
    assert published["review_reason"] == "来源与适用范围已核对"
    assert published["revision"] == 3
    assert published["superseded_version_ids"] == []

    with session_scope() as session:
        retrievable = fetch_retrievable_versions(
            session, scope_id=SCOPE_A, business_type=business, at=NOW
        )
    assert [item.version_id for item in retrievable] == [UUID(version_id)]

    retired = _transition(
        client,
        actors,
        version_id,
        "retire",
        key="reviewer",
        revision=published["revision"],
        reason="演示制度已废止",
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == KnowledgeStatus.RETIRED.value
    assert retired.json()["retire_reason"] == "演示制度已废止"

    with session_scope() as session:
        assert (
            fetch_retrievable_versions(
                session, scope_id=SCOPE_A, business_type=business, at=NOW
            )
            == []
        )


def test_reject_returns_the_version_to_draft_and_allows_resubmit(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    business = f"lost-card-{uuid4().hex[:8]}"
    created = _create(client, actors, business_type=business)
    version = created.json()

    submitted = _transition(
        client,
        actors,
        version["version_id"],
        "submit",
        key="author",
        revision=version["revision"],
    )
    rejected = _transition(
        client,
        actors,
        version["version_id"],
        "publish",
        key="reviewer",
        revision=submitted.json()["revision"],
        review_reason="来源链接无法访问，退回补充",
        decision="reject",
    )

    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == KnowledgeStatus.DRAFT.value
    assert body["outcome"] == "returned_to_draft"
    assert body["reviewer_id"] == str(actors["reviewer"]["user_id"])
    assert body["review_reason"] == "来源链接无法访问，退回补充"

    with session_scope() as session:
        assert (
            fetch_retrievable_versions(
                session, scope_id=SCOPE_A, business_type=business, at=NOW
            )
            == []
        )

    resubmitted = _transition(
        client,
        actors,
        version["version_id"],
        "submit",
        key="author",
        revision=body["revision"],
    )
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] == KnowledgeStatus.IN_REVIEW.value


def test_author_cannot_publish_own_version(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    created = _create(client, actors, key="reviewer")
    version = created.json()
    submitted = _transition(
        client,
        actors,
        version["version_id"],
        "submit",
        key="reviewer",
        revision=version["revision"],
    )

    response = _transition(
        client,
        actors,
        version["version_id"],
        "publish",
        key="reviewer",
        revision=submitted.json()["revision"],
        review_reason="自审尝试",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "SELF_REVIEW_FORBIDDEN"

    with session_scope() as session:
        stored = session.get(KnowledgeVersion, UUID(version["version_id"]))
        assert stored is not None
        assert stored.status == KnowledgeStatus.IN_REVIEW.value
        assert stored.reviewer_id is None


def test_publish_requires_reviewer_role(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    created = _create(client, actors, key="author")
    version = created.json()
    submitted = _transition(
        client,
        actors,
        version["version_id"],
        "submit",
        key="author",
        revision=version["revision"],
    )

    response = _transition(
        client,
        actors,
        version["version_id"],
        "publish",
        key="author",
        revision=submitted.json()["revision"],
        review_reason="agent 尝试发布",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    with session_scope() as session:
        stored = session.get(KnowledgeVersion, UUID(version["version_id"]))
        assert stored is not None
        assert stored.status == KnowledgeStatus.IN_REVIEW.value


def test_cross_scope_version_is_404_without_leaking_existence(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    created = _create(client, actors, key="author")
    version = created.json()
    missing = uuid4()

    foreign_read = client.get(
        f"/api/v1/knowledge/versions/{version['version_id']}",
        headers=_headers(actors, "reviewer_b"),
    )
    missing_read = client.get(
        f"/api/v1/knowledge/versions/{missing}",
        headers=_headers(actors, "reviewer_b"),
    )
    assert foreign_read.status_code == missing_read.status_code == 404
    assert foreign_read.json()["code"] == missing_read.json()["code"] == "NOT_FOUND"
    assert foreign_read.json()["message"] == missing_read.json()["message"]
    assert foreign_read.json()["details"] == missing_read.json()["details"] == {}

    foreign_write = _transition(
        client,
        actors,
        version["version_id"],
        "submit",
        key="reviewer_b",
        revision=version["revision"],
    )
    assert foreign_write.status_code == 404

    create_foreign = _create(
        client,
        actors,
        key="reviewer_b",
        document_id=UUID(version["document_id"]),
    )
    assert create_foreign.status_code == 404


def test_conflicting_valid_versions_are_explicitly_superseded(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    business = f"lost-card-{uuid4().hex[:8]}"
    first = _create(
        client,
        actors,
        business_type=business,
        content="旧版：挂失后 5 日内补办。",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2026, 6, 1, tzinfo=UTC),
    ).json()
    first = _publish_ready(
        client, actors, first, review_reason="首版审核通过"
    )

    overlapping = _create(
        client,
        actors,
        business_type=business,
        content="新版：挂失后 3 日内补办。",
        valid_from=datetime(2026, 3, 1, tzinfo=UTC),
        valid_to=datetime(2026, 9, 1, tzinfo=UTC),
        document_id=UUID(first["document_id"]),
    ).json()
    second = _publish_ready(
        client,
        actors,
        overlapping,
        review_reason="有效期重叠的新版本，需取代旧版本",
    )

    # 冲突不是静默覆盖：旧版本被显式退役并记录原因，响应里列出被取代的版本。
    assert second["superseded_version_ids"] == [first["version_id"]]

    with session_scope() as session:
        old = session.get(KnowledgeVersion, UUID(first["version_id"]))
        new = session.get(KnowledgeVersion, UUID(second["version_id"]))
        assert old is not None and new is not None
        assert old.status == KnowledgeStatus.RETIRED.value
        assert old.retire_reason
        assert "重叠" in old.retire_reason or "取代" in old.retire_reason
        assert new.status == KnowledgeStatus.PUBLISHED.value

        at = datetime(2026, 4, 1, tzinfo=UTC)
        retrievable = fetch_retrievable_versions(
            session, scope_id=SCOPE_A, business_type=business, at=at
        )
        assert [item.version_id for item in retrievable] == [
            UUID(second["version_id"])
        ]

    # 不重叠的窗口可以并存，不会误退役其他已发布版本。
    disjoint = _create(
        client,
        actors,
        business_type=business,
        content="后续版：2026 年 10 月起适用。",
        valid_from=datetime(2026, 10, 1, tzinfo=UTC),
        valid_to=None,
        document_id=UUID(first["document_id"]),
    ).json()
    third = _publish_ready(
        client, actors, disjoint, review_reason="后续制度，有效期不重叠"
    )
    assert third["superseded_version_ids"] == []

    with session_scope() as session:
        second_row = session.get(KnowledgeVersion, UUID(second["version_id"]))
        assert second_row is not None
        assert second_row.status == KnowledgeStatus.PUBLISHED.value


def test_retrievable_versions_exclude_drafts_expired_and_foreign_scope(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    business = f"lost-card-{uuid4().hex[:8]}"
    other_business = f"credit-card-fee-{uuid4().hex[:8]}"

    live = _create(
        client,
        actors,
        business_type=business,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_to=datetime(2026, 12, 1, tzinfo=UTC),
    ).json()
    _publish_ready(client, actors, live)

    draft = _create(client, actors, business_type=business).json()
    assert draft["status"] == KnowledgeStatus.DRAFT.value

    expired = _create(
        client,
        actors,
        business_type=business,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        valid_to=datetime(2021, 1, 1, tzinfo=UTC),
    ).json()
    _publish_ready(client, actors, expired)

    future = _create(
        client,
        actors,
        business_type=business,
        valid_from=datetime(2030, 1, 1, tzinfo=UTC),
        valid_to=None,
    ).json()
    _publish_ready(client, actors, future)

    other_type = _create(
        client, actors, business_type=other_business
    ).json()
    _publish_ready(client, actors, other_type)

    other_scope = _create(
        client, actors, key="agent_b", business_type=business
    ).json()
    _publish_ready(client, actors, other_scope, key="agent_b")

    with session_scope() as session:
        at = datetime(2026, 6, 1, tzinfo=UTC)
        retrievable = fetch_retrievable_versions(
            session, scope_id=SCOPE_A, business_type=business, at=at
        )

    assert [item.version_id for item in retrievable] == [UUID(live["version_id"])]


def test_stale_revision_and_illegal_state_transitions_are_409(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    created = _create(client, actors)
    version = created.json()

    # 草稿不能直接发布（必须先提交审核）。
    illegal = _transition(
        client,
        actors,
        version["version_id"],
        "publish",
        key="reviewer",
        revision=version["revision"],
        review_reason="跳过审核",
    )
    assert illegal.status_code == 409
    assert illegal.json()["code"] == "INVALID_STATE"

    submitted = _transition(
        client,
        actors,
        version["version_id"],
        "submit",
        key="author",
        revision=version["revision"],
    )
    assert submitted.status_code == 200

    # 使用过期 revision 提交发布，必须 409 而不是覆盖状态。
    stale = _transition(
        client,
        actors,
        version["version_id"],
        "publish",
        key="reviewer",
        revision=version["revision"],
        review_reason="使用旧 revision",
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"
    assert stale.json()["details"]["current_revision"] == submitted.json()["revision"]

    with session_scope() as session:
        stored = session.get(KnowledgeVersion, UUID(version["version_id"]))
        assert stored is not None
        assert stored.status == KnowledgeStatus.IN_REVIEW.value


def test_published_content_cannot_be_overwritten(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    created = _create(client, actors, content="已发布原文：不得覆盖。")
    version = _publish_ready(client, actors, created.json())

    with pytest.raises(PublishedContentImmutable):
        with session_scope() as session:
            stored = session.get(KnowledgeVersion, UUID(version["version_id"]))
            assert stored is not None
            stored.content = "篡改后的正文"
            stored.content_hash = hash_content("篡改后的正文")

    with session_scope() as session:
        stored = session.get(KnowledgeVersion, UUID(version["version_id"]))
        assert stored is not None
        assert stored.content == "已发布原文：不得覆盖。"
        assert stored.content_hash == hash_content("已发布原文：不得覆盖。")


def test_invalid_windows_are_rejected_at_input_validation(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    backwards = _create(
        client,
        actors,
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_to=datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert backwards.status_code == 422
    assert backwards.json()["code"] == "VALIDATION_ERROR"

    missing_metadata = client.post(
        "/api/v1/knowledge/documents",
        json={"content": "只有正文"},
        headers=_headers(actors, "author"),
    )
    assert missing_metadata.status_code == 422
    assert missing_metadata.json()["code"] == "VALIDATION_ERROR"

    naive = client.post(
        "/api/v1/knowledge/documents",
        json={
            "title": "无时区时间",
            "source_type": "demo_policy",
            "business_type": "lost-card",
            "content": "正文",
            "valid_from": "2026-01-01T00:00:00",
        },
        headers=_headers(actors, "author"),
    )
    assert naive.status_code == 422
    assert naive.json()["code"] == "VALIDATION_ERROR"


def _publish_ready(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    version: dict[str, object],
    *,
    key: str = "author",
    review_reason: str = "来源与适用范围已核对",
) -> dict[str, object]:
    """提交并由 reviewer 审核通过；返回发布响应。"""
    submitted = _transition(
        client,
        actors,
        str(version["version_id"]),
        "submit",
        key=key,
        revision=int(version["revision"]),
    )
    assert submitted.status_code == 200, submitted.text
    return _publish_approved(
        client,
        actors,
        str(version["version_id"]),
        key="reviewer" if key == "author" else "reviewer_b",
        revision=int(submitted.json()["revision"]),
    )
