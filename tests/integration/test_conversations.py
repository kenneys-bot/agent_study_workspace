"""会话持久化与审计集成测试（T12 / issue #13）：真实 PostgreSQL。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 起停一次性
PostgreSQL 18，并在空库执行 ``alembic upgrade head``；本文件只断言事实。

seam（真实 HTTP + 真实数据库）：
- ``POST /api/v1/conversations``、``GET /api/v1/conversations/{id}``；
- 数据库行本身：原文不可变、ordinal 唯一、UTC 时间、追加式审计与 trace_id；
- 进程重启等价场景：用全新 engine/session 读取已提交记录；
- 跨范围：显式写入他人范围 403，读取他人会话与不存在会话同为 404。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bank_assistant.api.errors import TRACE_ID_HEADER
from bank_assistant.config.settings import settings
from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import AuditEvent, Conversation, Turn, User
from bank_assistant.security.passwords import hash_password
from bank_assistant.services.conversations import (
    TurnImmutableError,
    replace_turn_content,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration

# 测试专用假凭据，不是任何环境的真实秘密。
DEMO_PASSWORD = "demo-only-correct-horse-battery"
SCOPE_A = "demo-bank-a"
SCOPE_B = "demo-bank-b"

PHONE = "13812345678"
SENSITIVE_TEXT = f"客户：我的手机是{PHONE}，想咨询挂失\n客服：好的，我帮您查一下👍"
CLEAN_TEXT = "客户：您好，我想查询信用卡账单\n客服：好的，请稍等"


@pytest.fixture(scope="module")
def demo_users() -> dict[str, dict[str, str]]:
    """显式测试夹具建两个范围各一个坐席；不自动创建任何演示用户。"""
    suffix = uuid4().hex[:8]
    specs = {
        "agent_a": {"username": f"t12-a-{suffix}", "scope_id": SCOPE_A},
        "agent_b": {"username": f"t12-b-{suffix}", "scope_id": SCOPE_B},
    }
    created: dict[str, dict[str, str]] = {}
    with session_scope() as session:
        for key, spec in specs.items():
            user = User(
                user_id=uuid4(),
                username=spec["username"],
                password_hash=hash_password(DEMO_PASSWORD),
                role="agent",
                scope_id=spec["scope_id"],
                active=True,
            )
            session.add(user)
            session.flush()
            created[key] = {**spec, "user_id": str(user.user_id)}
    return created


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def tokens(
    client: TestClient, demo_users: dict[str, dict[str, str]]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("agent_a", "agent_b"):
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": demo_users[key]["username"],
                "password": DEMO_PASSWORD,
            },
        )
        assert response.status_code == 200, response.text
        result[key] = response.json()["access_token"]
    return result


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(
    client: TestClient,
    token: str,
    content: str = SENSITIVE_TEXT,
    *,
    format: str = "text",
    trace_id: str | None = None,
    **extra: object,
):
    headers = _auth(token)
    if trace_id is not None:
        headers[TRACE_ID_HEADER] = trace_id
    return client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"content": content, "format": format, **extra},
    )


def _audit_events(resource_id: str) -> list[AuditEvent]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.resource_id == resource_id)
                .order_by(AuditEvent.timestamp, AuditEvent.action)
            )
        )


def _stored(conversation_id: str) -> tuple[Conversation, list[Turn]]:
    with session_scope() as session:
        conversation = session.get(Conversation, UUID(conversation_id))
        assert conversation is not None
        turns = list(
            session.scalars(
                select(Turn)
                .where(Turn.conversation_id == conversation.conversation_id)
                .order_by(Turn.ordinal)
            )
        )
        return conversation, turns


def test_conversation_tables_match_contract_and_migration_head() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"

    inspector = inspect(engine)
    conversation_columns = {
        column["name"] for column in inspector.get_columns("conversations")
    }
    turn_columns = {column["name"] for column in inspector.get_columns("turns")}
    audit_columns = {column["name"] for column in inspector.get_columns("audit_events")}

    assert {
        "conversation_id",
        "scope_id",
        "owner_id",
        "source_type",
        "raw_text",
        "content_hash",
        "data_classification",
        "redaction_applied",
        "created_at",
        "updated_at",
    } <= conversation_columns
    assert {
        "turn_id",
        "conversation_id",
        "ordinal",
        "speaker",
        "content",
        "timestamp",
    } <= turn_columns
    assert {
        "event_id",
        "actor_id",
        "action",
        "resource_type",
        "resource_id",
        "before_summary",
        "after_summary",
        "trace_id",
        "timestamp",
    } <= audit_columns

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    heads = script.get_heads()
    # 并行票从 0002 分叉，由 merge revision 收口；断言"单 head"而非"某一张票是 head"。
    assert len(heads) == 1, f"迁移图必须只有一个 head，实际 {heads}"
    assert "0004_conversations" in {r.revision for r in script.walk_revisions()}


def test_create_persists_immutable_original_and_redacts_display(
    client: TestClient, tokens: dict[str, str]
) -> None:
    response = _create(client, tokens["agent_a"], trace_id="trace-t12-create-1")

    assert response.status_code == 201, response.text
    body = response.json()
    conversation_id = body["conversation_id"]

    assert body["redaction_applied"] is True
    assert body["content_hash"] == hashlib.sha256(SENSITIVE_TEXT.encode()).hexdigest()
    assert body["evidence_coordinate_basis"] == "original_immutable_content"
    assert [turn["ordinal"] for turn in body["turns"]] == [1, 2]
    assert PHONE not in response.text
    assert body["turns"][0]["original_content_length"] == len(
        f"我的手机是{PHONE}，想咨询挂失"
    )

    conversation, turns = _stored(conversation_id)
    assert conversation.raw_text == SENSITIVE_TEXT
    assert conversation.content_hash == body["content_hash"]
    assert conversation.redaction_applied is True
    assert conversation.scope_id == SCOPE_A
    assert turns[0].content == f"我的手机是{PHONE}，想咨询挂失"
    assert PHONE in turns[0].content  # 存储原文绝不改写

    read = client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers={
            **_auth(tokens["agent_a"]),
            TRACE_ID_HEADER: "trace-t12-read-1",
        },
    )
    assert read.status_code == 200, read.text
    assert read.json()["redaction_applied"] is True
    assert PHONE not in read.text
    assert read.json()["turns"][0]["original_content_length"] == len(turns[0].content)

    clean = _create(client, tokens["agent_a"], CLEAN_TEXT)
    assert clean.status_code == 201
    assert clean.json()["redaction_applied"] is False
    assert clean.json()["turns"][0]["content"] == "您好，我想查询信用卡账单"


def test_json_input_uses_t03_parser_and_keeps_parsed_turns(
    client: TestClient, tokens: dict[str, str]
) -> None:
    raw = (
        '[{"speaker": "客户", "content": "你好😊"}, '
        '{"speaker": "客服", "content": "您好\\n请讲"}]'
    )

    response = _create(client, tokens["agent_a"], raw, format="json")

    assert response.status_code == 201, response.text
    body = response.json()
    assert [turn["ordinal"] for turn in body["turns"]] == [1, 2]
    assert body["turns"][0]["content"] == "你好😊"
    assert body["turns"][1]["content"] == "您好\n请讲"
    assert body["content_hash"] == hashlib.sha256(raw.encode()).hexdigest()

    conversation, turns = _stored(body["conversation_id"])
    assert conversation.raw_text == raw
    assert [turn.ordinal for turn in turns] == [1, 2]
    assert turns[1].content == "您好\n请讲"
    for turn in turns:
        assert turn.timestamp.utcoffset() is not None
        assert turn.timestamp.utcoffset().total_seconds() == 0


def test_records_survive_process_restart_with_new_engine(
    client: TestClient, tokens: dict[str, str]
) -> None:
    created = _create(client, tokens["agent_a"], CLEAN_TEXT)
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    # 等价于进程重启：全新 engine/session，不读任何进程内缓存。
    engine = create_engine(settings.DATABASE_URL)
    try:
        with Session(engine) as fresh_session:
            conversation = fresh_session.get(Conversation, UUID(conversation_id))
            assert conversation is not None
            assert conversation.raw_text == CLEAN_TEXT
            turns = list(
                fresh_session.scalars(
                    select(Turn)
                    .where(Turn.conversation_id == conversation.conversation_id)
                    .order_by(Turn.ordinal)
                )
            )
            assert [turn.content for turn in turns] == [
                "您好，我想查询信用卡账单",
                "好的，请稍等",
            ]
    finally:
        engine.dispose()


def test_ordinal_is_unique_per_conversation(
    client: TestClient, tokens: dict[str, str]
) -> None:
    created = _create(client, tokens["agent_a"], CLEAN_TEXT)
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    with session_scope() as session:
        session.add(
            Turn(
                conversation_id=UUID(conversation_id),
                ordinal=1,  # 已存在 → 数据库唯一约束必须拒绝
                speaker="客户",
                content="重复序号的轮次",
                timestamp=datetime.fromisoformat(
                    created.json()["turns"][0]["timestamp"]
                ),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    _, turns = _stored(conversation_id)
    assert [turn.ordinal for turn in turns] == [1, 2]
    assert "重复序号的轮次" not in "".join(turn.content for turn in turns)


def test_turn_content_is_immutable_after_creation(
    client: TestClient, tokens: dict[str, str]
) -> None:
    created = _create(client, tokens["agent_a"], CLEAN_TEXT)
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    _, turns = _stored(conversation_id)

    with pytest.raises(TurnImmutableError):
        replace_turn_content(None, turn_id=turns[0].turn_id, content="改写后的内容")

    _, reloaded = _stored(conversation_id)
    assert reloaded[0].content == turns[0].content
    assert reloaded[0].turn_id == turns[0].turn_id

    # 接口也不提供改写入口（只读 GET 与创建 POST）。
    from bank_assistant.main import app

    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/conversations"]) == {"post"}
    assert set(paths["/api/v1/conversations/{conversation_id}"]) == {"get"}


def test_audit_is_append_only_and_carries_request_trace_id(
    client: TestClient, tokens: dict[str, str]
) -> None:
    created = _create(
        client, tokens["agent_a"], SENSITIVE_TEXT, trace_id="trace-t12-audit-create"
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    read = client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers={
            **_auth(tokens["agent_a"]),
            TRACE_ID_HEADER: "trace-t12-audit-read",
        },
    )
    assert read.status_code == 200
    assert read.headers[TRACE_ID_HEADER] == "trace-t12-audit-read"

    events = _audit_events(conversation_id)
    by_action = {event.action: event for event in events}
    assert by_action["conversation.created"].trace_id == "trace-t12-audit-create"
    assert by_action["conversation.read"].trace_id == "trace-t12-audit-read"

    summary_text = repr(
        [event.before_summary for event in events]
        + [event.after_summary for event in events]
    )
    assert PHONE not in summary_text
    assert "挂失" not in summary_text
    assert SENSITIVE_TEXT not in summary_text
    assert by_action["conversation.created"].after_summary["content_hash"] == (
        hashlib.sha256(SENSITIVE_TEXT.encode()).hexdigest()
    )
    assert by_action["conversation.created"].after_summary["turn_count"] == 2

    # 追加式：再次读取只新增事件，旧事件原样保留。
    before = {
        event.event_id: (event.trace_id, repr(event.before_summary), repr(event.after_summary))
        for event in events
    }
    again = client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers={
            **_auth(tokens["agent_a"]),
            TRACE_ID_HEADER: "trace-t12-audit-read-2",
        },
    )
    assert again.status_code == 200
    after = _audit_events(conversation_id)
    assert len(after) == len(events) + 1
    after_by_id = {event.event_id: event for event in after}
    for event_id, snapshot in before.items():
        assert after_by_id[event_id].trace_id == snapshot[0]
        assert repr(after_by_id[event_id].before_summary) == snapshot[1]
        assert repr(after_by_id[event_id].after_summary) == snapshot[2]
    new_ids = set(after_by_id) - set(before)
    assert len(new_ids) == 1
    assert after_by_id[new_ids.pop()].trace_id == "trace-t12-audit-read-2"


def test_cross_scope_resource_is_invisible_404(
    client: TestClient, tokens: dict[str, str]
) -> None:
    created = _create(client, tokens["agent_a"], CLEAN_TEXT)
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]
    audit_before = {event.event_id for event in _audit_events(conversation_id)}

    foreign = client.get(
        f"/api/v1/conversations/{conversation_id}", headers=_auth(tokens["agent_b"])
    )
    missing = client.get(
        f"/api/v1/conversations/{uuid4()}", headers=_auth(tokens["agent_b"])
    )

    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    assert foreign.json()["message"] == missing.json()["message"]
    assert foreign.json()["details"] == missing.json()["details"] == {}
    assert CLEAN_TEXT.splitlines()[0] not in foreign.text
    # 未授权读取不产生审计（不确认资源存在，也不泄露访问尝试）。
    assert {event.event_id for event in _audit_events(conversation_id)} == audit_before


def test_cross_scope_write_attempt_is_403(
    client: TestClient, tokens: dict[str, str]
) -> None:
    response = _create(client, tokens["agent_a"], CLEAN_TEXT, scope_id=SCOPE_B)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert SCOPE_B not in response.text

    with session_scope() as session:
        assert (
            session.scalar(
                select(Conversation).where(
                    Conversation.content_hash
                    == hashlib.sha256(CLEAN_TEXT.encode()).hexdigest(),
                    Conversation.scope_id == SCOPE_B,
                )
            )
            is None
        )


def test_requests_require_authentication(client: TestClient) -> None:
    create = client.post("/api/v1/conversations", json={"content": CLEAN_TEXT})
    read = client.get(f"/api/v1/conversations/{uuid4()}")

    for response in (create, read):
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"


def test_invalid_input_is_400_without_echoing_content(
    client: TestClient, tokens: dict[str, str]
) -> None:
    invalid_json = _create(client, tokens["agent_a"], "客户：{不是合法 JSON", format="json")
    empty = _create(client, tokens["agent_a"], "客户：   ")

    assert invalid_json.status_code == 400
    assert invalid_json.json()["code"] == "CONVERSATION_INVALID_JSON"
    assert invalid_json.json()["details"] == {}
    assert "不是合法 JSON" not in invalid_json.text

    assert empty.status_code == 400
    assert empty.json()["code"] in {
        "CONVERSATION_EMPTY",
        "CONVERSATION_UNPARSEABLE",
    }


def test_timestamps_are_stored_as_utc(
    client: TestClient, tokens: dict[str, str]
) -> None:
    created = _create(client, tokens["agent_a"], CLEAN_TEXT)
    assert created.status_code == 201
    conversation_id = created.json()["conversation_id"]

    conversation, turns = _stored(conversation_id)
    for value in (conversation.created_at, conversation.updated_at, *(t.timestamp for t in turns)):
        assert value.tzinfo is not None
        assert value.utcoffset() == UTC.utcoffset(None)
    assert created.json()["turns"][0]["timestamp"].endswith(("+00:00", "Z"))
