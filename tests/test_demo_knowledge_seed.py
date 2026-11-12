"""演示知识种子：三场景语料、来源标注与幂等导入（M3，契约 §3）。

GLOSSARY「知识来源/演示制度」要求：只有公开监管条文与虚构演示制度两类；
演示制度必须显式标注且不得与真实制度混同。本测试同时验证导入走真实生命
周期（作者提交、另一审核人发布），且重复执行不产生重复版本。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from bank_assistant.infra.db import Base
from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion, User
from bank_assistant.knowledge.demo_seed import (
    DEMO_BUSINESS_TYPES,
    DEMO_MARKER,
    PUBLIC_SOURCE_HOSTS,
    DemoSeedError,
    import_demo_knowledge,
    load_demo_entries,
)
from bank_assistant.knowledge.lifecycle import KnowledgeStatus
from bank_assistant.knowledge.retrieval import fetch_retrievable_versions
from bank_assistant.security.identity import Identity

SCOPE = "demo-bank"
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _entry_payload(**overrides) -> dict:
    payload = {
        "id": "demo-001",
        "title": "演示制度条目",
        "source_type": "demo_policy",
        "source_uri": "https://demo.invalid/bank-assistant/demo-001",
        "business_type": "lost_card",
        "version_no": 1,
        "demo_only": True,
        "valid_from": "2025-01-01T00:00:00+00:00",
        "valid_to": None,
        "content": f"{DEMO_MARKER}：本演示银行规定挂失受理后补卡。",
    }
    payload.update(overrides)
    return payload


def _write_corpus(directory: Path, entries: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "corpus.json"
    path.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )
    return path


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def actors(session: Session) -> tuple[Identity, Identity]:
    author_user = User(
        user_id=uuid4(),
        username="demo-author-unit",
        password_hash="$argon2id$unit-test-placeholder",
        role="agent",
        scope_id=SCOPE,
    )
    reviewer_user = User(
        user_id=uuid4(),
        username="demo-reviewer-unit",
        password_hash="$argon2id$unit-test-placeholder",
        role="reviewer",
        scope_id=SCOPE,
    )
    session.add_all([author_user, reviewer_user])
    session.commit()
    return (
        Identity(
            user_id=author_user.user_id,
            username=author_user.username,
            role=author_user.role,
            scope_id=author_user.scope_id,
        ),
        Identity(
            user_id=reviewer_user.user_id,
            username=reviewer_user.username,
            role=reviewer_user.role,
            scope_id=reviewer_user.scope_id,
        ),
    )


def test_corpus_covers_three_scenarios_with_at_least_ten_entries_each() -> None:
    entries = load_demo_entries()

    counts = Counter(entry.business_type for entry in entries)
    for business_type in DEMO_BUSINESS_TYPES:
        assert counts[business_type] >= 10, (business_type, counts)
    assert len(entries) >= 30
    assert len({entry.entry_id for entry in entries}) == len(entries)


def test_every_entry_has_required_fields_and_honest_provenance() -> None:
    entries = load_demo_entries()

    for entry in entries:
        assert entry.title.strip()
        assert len(entry.content.strip()) >= 20
        assert entry.version_no >= 1
        assert entry.valid_from.tzinfo is not None
        if entry.valid_to is not None:
            assert entry.valid_to > entry.valid_from

        host = (urlparse(entry.source_uri).hostname or "").lower()
        if entry.demo_only:
            assert entry.source_type == "demo_policy"
            assert DEMO_MARKER in entry.content
            assert host == "demo.invalid" or host.endswith(".invalid")
        else:
            assert entry.source_type == "public_regulation"
            assert DEMO_MARKER not in entry.content
            assert host in PUBLIC_SOURCE_HOSTS


def test_document_title_and_version_pairs_are_unique() -> None:
    entries = load_demo_entries()
    keys = [(entry.title, entry.version_no) for entry in entries]

    assert len(keys) == len(set(keys))


def test_loader_rejects_demo_entry_without_fictional_marker(tmp_path) -> None:
    payload = _entry_payload(content="未标注虚构演示字样的正常制度文本，用于验证校验会拒绝它。")
    _write_corpus(tmp_path, [payload])

    with pytest.raises(DemoSeedError, match=DEMO_MARKER):
        load_demo_entries(tmp_path)


def test_loader_rejects_public_regulation_with_placeholder_host(tmp_path) -> None:
    payload = _entry_payload(
        id="pub-001",
        source_type="public_regulation",
        demo_only=False,
        source_uri="https://demo.invalid/public-001",
        content="公开监管条文摘要，正文自行组织，来源必须是公开可访问的链接。",
    )
    _write_corpus(tmp_path, [payload])

    with pytest.raises(DemoSeedError, match="公开链接"):
        load_demo_entries(tmp_path)


def test_loader_rejects_duplicate_document_and_version(tmp_path) -> None:
    _write_corpus(tmp_path, [_entry_payload(), _entry_payload(id="demo-002")])

    with pytest.raises(DemoSeedError, match="重复"):
        load_demo_entries(tmp_path)


def test_loader_requires_timezone_aware_validity_window(tmp_path) -> None:
    _write_corpus(
        tmp_path,
        [_entry_payload(valid_from="2025-01-01T00:00:00")],
    )

    with pytest.raises(DemoSeedError, match="时区"):
        load_demo_entries(tmp_path)


def test_import_is_idempotent_and_publishes_through_two_accounts(
    session: Session, actors: tuple[Identity, Identity]
) -> None:
    entries = load_demo_entries()
    author, reviewer = actors

    first = import_demo_knowledge(
        session, entries, author=author, reviewer=reviewer, scope_id=SCOPE
    )

    assert first.total_entries == len(entries)
    assert first.created_documents == len(entries)
    assert first.published_versions == len(entries)
    assert first.skipped_versions == 0

    second = import_demo_knowledge(
        session, entries, author=author, reviewer=reviewer, scope_id=SCOPE
    )

    assert second.published_versions == 0
    assert second.skipped_versions == len(entries)
    assert second.created_documents == 0

    documents = session.scalar(select(func.count()).select_from(KnowledgeDocument))
    versions = list(session.scalars(select(KnowledgeVersion)))
    assert documents == len(entries)
    assert len(versions) == len(entries)
    for version in versions:
        assert version.status == KnowledgeStatus.PUBLISHED.value
        assert version.author_id == author.user_id
        assert version.reviewer_id == reviewer.user_id
        assert version.review_reason

    # 演示语料里包含“已过期 / 未生效”边界条目，它们已发布但不参与正式检索。
    retrievable = fetch_retrievable_versions(session, scope_id=SCOPE, at=NOW)
    assert len(retrievable) == len(entries) - 2


def test_import_requires_two_distinct_accounts(
    session: Session, actors: tuple[Identity, Identity]
) -> None:
    author, _ = actors
    entries = load_demo_entries()[:1]

    with pytest.raises(DemoSeedError, match="两个不同账号"):
        import_demo_knowledge(
            session, entries, author=author, reviewer=author, scope_id=SCOPE
        )


def test_import_refuses_to_overwrite_changed_content_with_same_version(
    session: Session, actors: tuple[Identity, Identity]
) -> None:
    author, reviewer = actors
    entry = load_demo_entries()[0]
    import_demo_knowledge(
        session, [entry], author=author, reviewer=reviewer, scope_id=SCOPE
    )

    changed = replace(entry, content=f"{entry.content} 补充说明。")
    with pytest.raises(DemoSeedError, match="不能覆盖"):
        import_demo_knowledge(
            session, [changed], author=author, reviewer=reviewer, scope_id=SCOPE
        )
