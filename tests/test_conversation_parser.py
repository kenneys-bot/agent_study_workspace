"""会话导入闭环回归测试（T03 / issue #4）。

覆盖三处已复现缺陷：
1. 文本解析正则的前瞻分组导致 speaker 与 content 都取到说话人标签；
2. JSON 数组分支未初始化 session_id；
3. 时间戳保持 str，后续 to_dict() 触发 AttributeError。

同时在公开 seam 上验证非法输入的 4xx 稳定错误码与原文/轮次保留。
"""

import pytest


class TestTextConversationParsing:
    """文本会话解析：speaker 与 content 不得错位。"""

    def test_text_keeps_speaker_and_content_separate(self):
        from bank_assistant.assistants.quality_inspector import ConversationParser

        raw = "客户：我想查询信用卡账单\n客服：好的，请稍等"
        parsed = ConversationParser().parse_conversation(raw, "text")

        assert [turn.speaker for turn in parsed.turns] == ["客户", "客服"]
        assert [turn.content for turn in parsed.turns] == [
            "我想查询信用卡账单",
            "好的，请稍等",
        ]


class TestJsonConversationParsing:
    """JSON 会话解析：数组分支也必须有稳定 session_id。"""

    def test_json_array_has_stable_session_id(self):
        from bank_assistant.assistants.quality_inspector import ConversationParser

        raw = (
            '[{"speaker": "客户", "content": "你好"}, '
            '{"speaker": "客服", "content": "您好，请讲"}]'
        )
        parser = ConversationParser()
        parsed = parser.parse_conversation(raw, "json")

        assert len(parsed.turns) == 2
        assert parsed.session_id
        assert parsed.session_id != "parse_error"
        assert parsed.to_dict()["session_id"] == parsed.session_id
        assert parser.parse_conversation(raw, "json").session_id == parsed.session_id


class TestTimestampTypes:
    """时间戳必须是带时区的 datetime（UTC），不能留成 str。"""

    def test_json_timestamps_are_aware_utc_datetimes(self):
        from datetime import datetime, timedelta, timezone

        from bank_assistant.assistants.quality_inspector import ConversationParser

        raw = (
            '[{"speaker": "客户", "content": "你好", '
            '"timestamp": "2026-11-12T10:30:00+08:00"}, '
            '{"speaker": "客服", "content": "您好", '
            '"timestamp": "2026-11-12T02:31:00Z"}]'
        )
        parsed = ConversationParser().parse_conversation(raw, "json")

        for turn in parsed.turns:
            assert isinstance(turn.timestamp, datetime)
            assert turn.timestamp.tzinfo is not None
            assert turn.timestamp.utcoffset() == timedelta(0)
        assert parsed.turns[0].timestamp == datetime(
            2026, 11, 12, 2, 30, tzinfo=timezone.utc
        )
        # 旧实现在这里抛 AttributeError: 'str' object has no attribute 'isoformat'
        serialized = parsed.to_dict()["turns"][0]["timestamp"]
        assert serialized == "2026-11-12T02:30:00+00:00"

    def test_text_turns_also_get_aware_timestamps(self):
        from datetime import timedelta

        from bank_assistant.assistants.quality_inspector import ConversationParser

        parsed = ConversationParser().parse_conversation("客户：你好", "text")
        turn = parsed.turns[0]
        assert turn.timestamp.tzinfo is not None
        assert turn.timestamp.utcoffset() == timedelta(0)


class TestOrdinalsAndRawText:
    """轮次序号稳定唯一，原文原样保留。"""

    def test_text_happy_path_with_emoji_and_newline(self):
        from bank_assistant.assistants.quality_inspector import ConversationParser

        raw = (
            "客户：您好😊 我想查一下\n"
            "上个月的信用卡账单\n"
            "客服：好的，我帮您查一下👍"
        )
        parsed = ConversationParser().parse_conversation(raw, "text")

        assert parsed.raw_text == raw
        assert [turn.ordinal for turn in parsed.turns] == [1, 2]
        assert parsed.turns[0].speaker == "客户"
        assert parsed.turns[0].content == "您好😊 我想查一下\n上个月的信用卡账单"
        assert parsed.turns[1].content == "好的，我帮您查一下👍"
        assert "\n" in parsed.turns[0].content
        assert "😊" in parsed.turns[0].content
        assert parsed.participants == ["客户", "客服"]

    def test_json_ordinals_are_unique_and_raw_text_preserved(self):
        from bank_assistant.assistants.quality_inspector import ConversationParser

        raw = (
            '[{"speaker": "客户", "content": "你好😊"}, '
            '{"speaker": "客服", "content": "您好\\n请讲"}]'
        )
        parsed = ConversationParser().parse_conversation(raw, "json")

        assert [turn.ordinal for turn in parsed.turns] == [1, 2]
        assert parsed.raw_text == raw
        assert parsed.turns[1].content == "您好\n请讲"


@pytest.fixture
def client():
    """解析接口的测试客户端（旧原型端点现要求认证）。"""
    from fastapi.testclient import TestClient

    from bank_assistant.main import app

    from uuid import uuid4

    from bank_assistant.security.authorization import get_current_identity
    from bank_assistant.security.identity import Identity

    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=uuid4(),
        username="legacy-api-test",
        role="agent",
        scope_id="legacy-api-scope",
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestInvalidInput:
    """非法输入必须是 4xx + 稳定错误码，而不是 500 或静默 parse_error。"""

    @pytest.mark.parametrize(
        ("payload", "expected_code"),
        [
            ({"content": "   ", "format": "text"}, "CONVERSATION_EMPTY"),
            ({"content": "{not json", "format": "json"}, "CONVERSATION_INVALID_JSON"),
            (
                {"content": '{"turns": "not-a-list"}', "format": "json"},
                "CONVERSATION_INVALID_STRUCTURE",
            ),
            (
                {
                    "content": '[{"speaker": "客户", "content": "你好", '
                    '"timestamp": "昨天下午"}]',
                    "format": "json",
                },
                "CONVERSATION_INVALID_TIMESTAMP",
            ),
            (
                {"content": "客户：你好", "format": "xml"},
                "CONVERSATION_UNSUPPORTED_FORMAT",
            ),
        ],
    )
    def test_invalid_input_returns_4xx_with_stable_code(
        self, client, payload, expected_code
    ):
        response = client.post(
            "/api/v1/quality-inspector/parse-conversation",
            json=payload,
        )

        assert 400 <= response.status_code < 500
        body = response.json()
        assert body["code"] == expected_code
        assert body["message"]
        assert body["retryable"] is False

    def test_parser_raises_typed_error_instead_of_parse_error_sentinel(self):
        from bank_assistant.assistants.quality_inspector.conversation_parser import (
            ConversationParseError,
            ConversationParser,
        )

        with pytest.raises(ConversationParseError) as exc_info:
            ConversationParser().parse_conversation("{not json", "json")

        assert exc_info.value.code == "CONVERSATION_INVALID_JSON"
        assert exc_info.value.http_status == 400


class TestParseConversationResponseModel:
    """解析路由必须声明并返回 ParseConversationResponse 模型。"""

    def test_route_declares_parse_conversation_response_model(self):
        from bank_assistant.main import app

        schema = app.openapi()
        operation = schema["paths"][
            "/api/v1/quality-inspector/parse-conversation"
        ]["post"]
        ref = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert ref == "#/components/schemas/ParseConversationResponse"

        component = schema["components"]["schemas"]["ParseConversationResponse"]
        assert "raw_text" in component["properties"]
        turn_ref = component["properties"]["turns"]["items"]["$ref"]
        turn_component = schema["components"]["schemas"][
            turn_ref.rsplit("/", 1)[-1]
        ]
        assert "ordinal" in turn_component["properties"]
        assert turn_component["properties"]["timestamp"]["format"] == "date-time"

    def test_api_happy_path_returns_stable_turns_and_raw_text(self, client):
        from datetime import datetime, timedelta

        raw = (
            "客户：您好😊 我想查一下\n"
            "上个月的信用卡账单\n"
            "客服：好的，我帮您查一下👍"
        )
        response = client_post_parse(client, raw)

        assert response.status_code == 200
        body = response.json()
        assert body["raw_text"] == raw
        assert [turn["ordinal"] for turn in body["turns"]] == [1, 2]
        assert body["turns"][0]["speaker"] == "客户"
        assert body["turns"][0]["content"] == "您好😊 我想查一下\n上个月的信用卡账单"
        timestamp = datetime.fromisoformat(body["turns"][0]["timestamp"])
        assert timestamp.utcoffset() == timedelta(0)

    def test_request_session_id_is_used(self, client):
        response = client_post_parse(
            client, "客户：你好\n客服：您好", session_id="sess-T03"
        )

        assert response.status_code == 200
        assert response.json()["session_id"] == "sess-T03"


def client_post_parse(client, content, session_id=None):
    payload = {"content": content, "format": "text"}
    if session_id is not None:
        payload["session_id"] = session_id
    return client.post(
        "/api/v1/quality-inspector/parse-conversation", json=payload
    )
