"""可检索知识版本的过滤规则（SQLite 作为单元测试替身，ADR-0002）。

契约 §3 只允许：``status=published`` 且在有效期、访问范围与业务范围内的
版本进入正式检索。过滤必须在 SQL 里完成（M3 会直接复用该查询），因此这里
既验证返回集合，也验证生成的 SQL 已把状态/范围/有效期下推到 WHERE。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from bank_assistant.infra.db import Base
from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion, User
from bank_assistant.knowledge.lifecycle import KnowledgeStatus, hash_content
from bank_assistant.knowledge.retrieval import (
    fetch_retrievable_versions,
    retrievable_versions_query,
)

SCOPE_A = "demo-bank-a"
SCOPE_B = "demo-bank-b"
BUSINESS = "lost_card"
OTHER_BUSINESS = "credit_card_fee"
NOW = datetime(2026, 6, 1, tzinfo=UTC)


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
        source_uri="https://example.invalid/demo-policy",
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
    status: KnowledgeStatus,
    valid_from: datetime,
    valid_to: datetime | None,
) -> KnowledgeVersion:
    content = f"{document.title}-{status.value}-{valid_from.isoformat()}"
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


def test_only_published_in_window_business_and_scope_are_retrievable(
    session: Session, author: User
) -> None:
    in_window = _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=datetime(2026, 7, 1, tzinfo=UTC),
    )
    _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.DRAFT,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=datetime(2026, 7, 1, tzinfo=UTC),
    )
    _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.RETIRED,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=datetime(2026, 7, 1, tzinfo=UTC),
    )
    _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=datetime(2026, 3, 1, tzinfo=UTC),
        valid_to=datetime(2026, 4, 1, tzinfo=UTC),
    )
    _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=datetime(2026, 7, 1, tzinfo=UTC),
        valid_to=datetime(2026, 9, 1, tzinfo=UTC),
    )
    starts_at_moment = _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=NOW,
        valid_to=None,
    )
    ends_at_moment = _version(
        session,
        _document(session),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=NOW,
    )
    _version(
        session,
        _document(session, business_type=OTHER_BUSINESS),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=None,
    )
    _version(
        session,
        _document(session, scope_id=SCOPE_B),
        author,
        status=KnowledgeStatus.PUBLISHED,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=None,
    )

    found = fetch_retrievable_versions(
        session, scope_id=SCOPE_A, business_type=BUSINESS, at=NOW
    )

    # 半开区间：valid_from 恰好等于时刻算命中，valid_to 恰好等于时刻不算。
    assert [version.version_id for version in found] == [
        in_window.version_id,
        starts_at_moment.version_id,
    ]
    assert ends_at_moment.version_id not in {version.version_id for version in found}


def test_retrievable_query_filters_in_sql_not_in_python() -> None:
    statement = retrievable_versions_query(
        scope_id=SCOPE_A, business_type=BUSINESS, at=NOW
    )
    sql = " ".join(str(statement).split())

    # 状态、范围与有效期都必须出现在 WHERE 中；M3 直接复合该查询。
    assert "knowledge_versions.status =" in sql
    assert "knowledge_documents.scope_id =" in sql
    assert "knowledge_versions.valid_from <=" in sql
    assert "knowledge_versions.valid_to IS NULL OR" in sql
    assert "knowledge_documents.business_type =" in sql
