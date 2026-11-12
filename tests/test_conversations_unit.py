"""会话持久化与审计的单元测试（T12 / issue #13，不需要数据库）。

seam（公开接口，不测私有实现）：
- ``bank_assistant.services.redaction.redact_sensitive_text``：读取展示时的
  最小化遮蔽，纯函数，不改存储原文；
- ``bank_assistant.services.conversations.build_conversation_entities``：
  把 T03 解析结果变成待持久化实体，ordinal 按解析结果原样保留，绝不重排；
- ``bank_assistant.services.audit.conversation_summary`` 与
  ``append_audit_event``：审计摘要只含元数据，禁止正文进入审计；
- ``bank_assistant.services.conversations.replace_turn_content``：轮次原文
  不可变的唯一写入口，必须拒绝；
- ``bank_assistant.api.models.conversations.to_public``：GET/POST 响应模型，
  脱敏标志来自存储列，证据坐标以原文码点为准；
- 迁移链：``0004_conversations`` 直接接在 ``0002_users`` 之后且只有一个 head。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


class TestSensitiveRedaction:
    """读取展示的最小化遮蔽：手机号、身份证、银行卡号。"""

    def test_masks_mobile_number_without_changing_rest_of_text(self):
        from bank_assistant.services.redaction import redact_sensitive_text

        raw = "客户：我的手机是13812345678，请回拨\n客服：好的"

        result = redact_sensitive_text(raw)

        assert result.applied is True
        assert "13812345678" not in result.text
        assert result.text.startswith("客户：我的手机是")
        assert result.text.endswith("，请回拨\n客服：好的")
        # 等长遮蔽：坐标仍可按原文码点解释。
        assert len(result.text) == len(raw)

    def test_masks_id_card_and_bank_card_numbers(self):
        from bank_assistant.services.redaction import redact_sensitive_text

        raw = (
            "身份证 11010519491231002X\n"
            "卡号 6222 0212 3456 7890 123"
        )

        result = redact_sensitive_text(raw)

        assert result.applied is True
        assert "11010519491231002X" not in result.text
        assert "6222 0212 3456 7890 123" not in result.text
        # 分组空格保留，数字被遮蔽。
        assert "6222" not in result.text
        assert " " in result.text
        assert len(result.text) == len(raw)

    def test_leaves_ordinary_conversation_untouched(self):
        from bank_assistant.services.redaction import redact_sensitive_text

        raw = (
            "客户：您好😊 我想查一下上个月的账单\n"
            "客服：好的，请提供尾号 1234 的卡"
        )

        result = redact_sensitive_text(raw)

        assert result.applied is False
        assert result.text == raw

    def test_masking_is_stable_when_applied_twice(self):
        from bank_assistant.services.redaction import redact_sensitive_text

        once = redact_sensitive_text("电话 13900001111 找我")
        twice = redact_sensitive_text(once.text)

        assert twice.text == once.text
        assert twice.applied is False


class TestTurnOrdinalPersistence:
    """ordinal 按解析结果持久化，绝不重新编号。"""

    def test_keeps_parsed_ordinals_instead_of_renumbering(self):
        from bank_assistant.services.conversations import build_conversation_entities

        raw = '[{"speaker":"客户","content":"你好"},{"speaker":"客服","content":"您好"}]'
        parsed = SimpleNamespace(
            raw_text=raw,
            turns=[
                SimpleNamespace(ordinal=2, speaker="客户", content="你好", timestamp=_utc(0)),
                SimpleNamespace(ordinal=7, speaker="客服", content="您好", timestamp=_utc(60)),
            ],
        )
        owner_id = uuid4()

        conversation, turns = build_conversation_entities(
            parsed,
            owner_id=owner_id,
            scope_id="scope-a",
            source_type="manual_paste",
            source_ref=None,
            data_classification="synthetic_demo",
        )

        assert [turn.ordinal for turn in turns] == [2, 7]
        assert [turn.content for turn in turns] == ["你好", "您好"]
        assert conversation.raw_text == raw
        assert conversation.content_hash == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert conversation.owner_id == owner_id
        assert conversation.scope_id == "scope-a"
        assert all(turn.conversation_id == conversation.conversation_id for turn in turns)

    def test_rejects_duplicate_parsed_ordinals(self):
        from bank_assistant.services.conversations import (
            ConversationIntegrityError,
            build_conversation_entities,
        )

        parsed = SimpleNamespace(
            raw_text="客户：你好",
            turns=[
                SimpleNamespace(ordinal=1, speaker="客户", content="你好", timestamp=_utc(0)),
                SimpleNamespace(ordinal=1, speaker="客服", content="您好", timestamp=_utc(1)),
            ],
        )

        with pytest.raises(ConversationIntegrityError):
            build_conversation_entities(
                parsed,
                owner_id=uuid4(),
                scope_id="scope-a",
                source_type="manual_paste",
                source_ref=None,
                data_classification="synthetic_demo",
            )

    def test_stores_redaction_flag_from_created_content(self):
        from bank_assistant.services.conversations import build_conversation_entities

        parsed = SimpleNamespace(
            raw_text="客户：我的号码是13812345678",
            turns=[
                SimpleNamespace(
                    ordinal=1, speaker="客户", content="我的号码是13812345678", timestamp=_utc(0)
                )
            ],
        )

        conversation, _ = build_conversation_entities(
            parsed,
            owner_id=uuid4(),
            scope_id="scope-a",
            source_type="manual_paste",
            source_ref=None,
            data_classification="synthetic_demo",
        )

        assert conversation.redaction_applied is True
        # 存储原文不被改写（契约 §4：证据坐标基于不可变原文）。
        assert "13812345678" in conversation.raw_text


class TestConversationPublicView:
    """响应模型：展示文本可脱敏，但原文长度与坐标基准保持可核对。"""

    def test_public_view_masks_display_and_keeps_original_length(self):
        from bank_assistant.api.models.conversations import to_public
        from bank_assistant.services.conversations import ConversationView, build_conversation_entities

        raw = "客户：我的号码是13812345678"
        parsed = SimpleNamespace(
            raw_text=raw,
            turns=[
                SimpleNamespace(
                    ordinal=1, speaker="客户", content="我的号码是13812345678", timestamp=_utc(0)
                )
            ],
        )
        conversation, turns = build_conversation_entities(
            parsed,
            owner_id=uuid4(),
            scope_id="scope-a",
            source_type="manual_paste",
            source_ref=None,
            data_classification="synthetic_demo",
        )

        public = to_public(ConversationView(conversation=conversation, turns=tuple(turns)))

        assert public.redaction_applied is True
        assert "13812345678" not in public.turns[0].content
        assert public.turns[0].original_content_length == len("我的号码是13812345678")
        # 存储实体保持原文；脱敏只发生在响应里。
        assert turns[0].content == "我的号码是13812345678"

    def test_unmasked_conversation_reports_flag_false(self):
        from bank_assistant.api.models.conversations import to_public
        from bank_assistant.services.conversations import ConversationView, build_conversation_entities

        parsed = SimpleNamespace(
            raw_text="客户：你好",
            turns=[SimpleNamespace(ordinal=1, speaker="客户", content="你好", timestamp=_utc(0))],
        )
        conversation, turns = build_conversation_entities(
            parsed,
            owner_id=uuid4(),
            scope_id="scope-a",
            source_type="manual_paste",
            source_ref=None,
            data_classification="synthetic_demo",
        )

        public = to_public(ConversationView(conversation=conversation, turns=tuple(turns)))

        assert public.redaction_applied is False
        assert public.turns[0].content == "你好"

    def test_public_view_masks_sensitive_speaker_label(self):
        from bank_assistant.api.models.conversations import to_public
        from bank_assistant.services.conversations import ConversationView, build_conversation_entities

        parsed = SimpleNamespace(
            raw_text="13812345678：你好",
            turns=[
                SimpleNamespace(
                    ordinal=1, speaker="13812345678", content="你好", timestamp=_utc(0)
                )
            ],
        )
        conversation, turns = build_conversation_entities(
            parsed,
            owner_id=uuid4(),
            scope_id="scope-a",
            source_type="manual_paste",
            source_ref=None,
            data_classification="synthetic_demo",
        )

        public = to_public(ConversationView(conversation=conversation, turns=tuple(turns)))

        assert public.turns[0].speaker == "***********"


class TestTurnImmutability:
    """turn.content 原文不可变；服务层唯一写入口直接拒绝。"""

    def test_replace_turn_content_is_rejected(self):
        from bank_assistant.services.conversations import (
            TurnImmutableError,
            replace_turn_content,
        )

        with pytest.raises(TurnImmutableError):
            replace_turn_content(None, turn_id=uuid4(), content="改写后的内容")


class TestAuditSummary:
    """审计 before/after 摘要只含元数据，不含敏感正文。"""

    def test_summary_excludes_raw_text_and_turn_content(self):
        from bank_assistant.services.audit import conversation_summary
        from bank_assistant.services.conversations import build_conversation_entities

        raw = "客户：我的号码是13812345678，想咨询挂失"
        parsed = SimpleNamespace(
            raw_text=raw,
            turns=[
                SimpleNamespace(
                    ordinal=1,
                    speaker="客户",
                    content="我的号码是13812345678，想咨询挂失",
                    timestamp=_utc(0),
                )
            ],
        )
        conversation, turns = build_conversation_entities(
            parsed,
            owner_id=uuid4(),
            scope_id="scope-a",
            source_type="manual_paste",
            source_ref=None,
            data_classification="synthetic_demo",
        )

        summary = conversation_summary(conversation, turns)

        serialized = repr(summary)
        assert "13812345678" not in serialized
        assert "挂失" not in serialized
        assert summary["turn_count"] == 1
        assert summary["content_hash"] == conversation.content_hash
        assert summary["data_classification"] == "synthetic_demo"

    def test_audit_rejects_summaries_that_would_store_full_text(self):
        from bank_assistant.services.audit import (
            AuditSummaryError,
            append_audit_event,
        )

        class FakeSession:
            def __init__(self) -> None:
                self.added = []

            def add(self, item) -> None:
                self.added.append(item)

        session = FakeSession()
        common = {
            "actor_id": uuid4(),
            "action": "conversation.created",
            "resource_type": "conversation",
            "resource_id": str(uuid4()),
            "trace_id": "trace-1",
        }

        # 多行正文与含手机号的正文都必须被拒绝。
        with pytest.raises(AuditSummaryError):
            append_audit_event(
                session,
                after_summary={"raw_text": "客户：你好\n客服：您好"},
                **common,
            )
        with pytest.raises(AuditSummaryError):
            append_audit_event(
                session,
                after_summary={"raw_text": "客户：我的号码是13812345678"},
                **common,
            )

        assert session.added == []

    def test_append_audit_event_keeps_only_metadata_and_trace_id(self):
        from bank_assistant.services.audit import append_audit_event

        class FakeSession:
            def __init__(self) -> None:
                self.added = []

            def add(self, item) -> None:
                self.added.append(item)

        session = FakeSession()
        event = append_audit_event(
            session,
            actor_id=uuid4(),
            action="conversation.read",
            resource_type="conversation",
            resource_id=str(uuid4()),
            trace_id="trace-abc",
            after_summary={"turn_count": 2, "content_hash": "a" * 64},
        )

        assert session.added == [event]
        assert event.trace_id == "trace-abc"
        assert event.before_summary == {}
        assert event.after_summary == {"turn_count": 2, "content_hash": "a" * 64}


class TestConversationRoutes:
    """打开 OpenAPI 就能看到契约 §5 的两个会话端点（无需数据库）。"""

    def test_openapi_exposes_create_and_read_conversation(self):
        from bank_assistant.main import app

        spec = app.openapi()
        paths = spec["paths"]

        assert "post" in paths["/api/v1/conversations"]
        assert "get" in paths["/api/v1/conversations/{conversation_id}"]
        # 未认证不得访问：两个端点都声明 Bearer。
        for operation in (
            paths["/api/v1/conversations"]["post"],
            paths["/api/v1/conversations/{conversation_id}"]["get"],
        ):
            assert operation["security"] == [{"HTTPBearer": []}]


class TestMigrationChain:
    """迁移链：0004_conversations 接在 0002_users 之后，且只有一个 head。"""

    def test_conversations_revision_extends_users_and_is_the_only_head(self):
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        repo_root = Path(__file__).resolve().parents[1]
        script = ScriptDirectory.from_config(Config(str(repo_root / "alembic.ini")))

        revision = script.get_revision("0004_conversations")
        assert revision is not None
        assert revision.down_revision == "0002_users"
        # 并行票从同一父节点分叉，最终由 merge revision 收口为单 head。
        heads = script.get_heads()
        assert len(heads) == 1, f"迁移图必须只有一个 head，实际 {heads}"
        reachable = {r.revision for r in script.walk_revisions()}
        assert "0004_conversations" in reachable


def _utc(seconds: int) -> datetime:
    return datetime(2026, 11, 12, 2, 30, tzinfo=timezone.utc) + timedelta(seconds=seconds)
