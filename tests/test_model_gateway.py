"""模型适配层测试。

全部使用 httpx.MockTransport（经 openai SDK 注入），绝不发起真实网络请求，
也不读取真实凭据。
"""

import json

import httpx
import pytest

from bank_assistant.core.model_gateway import (
    ModelCallStatus,
    ModelGateway,
    ModelProvider,
    ProviderSpec,
)


def completion(
    content,
    finish_reason="stop",
    *,
    model="deepseek-chat",
    extra=None,
):
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    if extra:
        body.update(extra)
    return body


def handler_for(body=None, status=200, capture=None, exc=None):
    def handler(request):
        if capture is not None:
            capture.append(json.loads(request.content))
        if exc is not None:
            raise exc
        if status != 200:
            return httpx.Response(
                status,
                json={"error": {"message": "provider error", "type": "test"}},
            )
        return httpx.Response(200, json=body)

    return handler


def make_provider(provider=ModelProvider.DEEPSEEK, **overrides):
    values = {
        "provider": provider,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "test-key",
        "model": "deepseek-chat",
    }
    values.update(overrides)
    return ProviderSpec(**values)


def make_gateway(handler, *, providers=None, **kwargs):
    providers = providers or {ModelProvider.DEEPSEEK: make_provider()}
    return ModelGateway(
        providers=providers,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
        **kwargs,
    )


MESSAGES = [
    {"role": "system", "content": "只依据给定知识回答，禁止猜测。"},
    {"role": "user", "content": "信用卡挂失怎么办理？"},
]


class TestRequestShape:
    def test_system_message_is_sent_in_outgoing_payload(self):
        captured = []
        gateway = make_gateway(
            handler_for(completion("请致电客服挂失。"), capture=captured)
        )

        result = gateway.complete(MESSAGES)

        assert result.status is ModelCallStatus.SUCCEEDED
        assert captured[0]["messages"] == MESSAGES
        assert captured[0]["messages"][0]["role"] == "system"
        assert captured[0]["model"] == "deepseek-chat"

    def test_success_reports_provider_model_and_mode(self):
        gateway = make_gateway(handler_for(completion("您好")))

        result = gateway.complete(MESSAGES)

        assert result.ok
        assert result.text == "您好"
        assert result.provider is ModelProvider.DEEPSEEK
        assert result.model == "deepseek-chat"
        assert result.mode == "live"
        assert result.finish_reason == "stop"


class TestStructuredOutput:
    def test_valid_json_passing_schema_is_success(self):
        from pydantic import BaseModel

        class Answer(BaseModel):
            answer: str
            citations: list[str] = []

        body = completion(json.dumps({"answer": "可以挂失", "citations": ["doc-1"]}))
        gateway = make_gateway(handler_for(body))

        result = gateway.structured(MESSAGES, schema=Answer)

        assert result.ok
        assert result.data == Answer(answer="可以挂失", citations=["doc-1"])
        assert result.status is ModelCallStatus.SUCCEEDED


class TestStructuredFailures:
    """无效输出必须是失败，不能变成空答复（#6 / ADR-0004）。"""

    def test_non_json_content_is_invalid_structure(self):
        from pydantic import BaseModel

        class Answer(BaseModel):
            answer: str

        gateway = make_gateway(handler_for(completion("不是 JSON，只是普通文本")))

        result = gateway.structured(MESSAGES, schema=Answer)

        assert result.status is ModelCallStatus.INVALID_STRUCTURE
        assert not result.ok
        assert result.data is None
        assert "invalid_structure" in result.error

    def test_schema_mismatch_is_invalid_structure(self):
        from pydantic import BaseModel

        class Answer(BaseModel):
            answer: str
            confidence: float

        gateway = make_gateway(handler_for(completion(json.dumps({"answer": "有"}))))

        result = gateway.structured(MESSAGES, schema=Answer)

        assert result.status is ModelCallStatus.INVALID_STRUCTURE
        assert result.data is None

    def test_length_finish_reason_with_empty_content_is_empty_content(self):
        gateway = make_gateway(handler_for(completion("", finish_reason="length")))

        result = gateway.complete(MESSAGES)

        assert result.status is ModelCallStatus.EMPTY_CONTENT
        assert result.finish_reason == "length"
        assert not result.ok

    def test_extra_provider_fields_are_tolerated(self):
        """中继会返回 latency_checkpoint / annotations 等非标准字段。"""
        from pydantic import BaseModel

        class Answer(BaseModel):
            answer: str

        body = completion(
            json.dumps({"answer": "可以", "annotations": ["extra"]}),
            extra={"latency_checkpoint": {"ms": 12}, "annotations": [{"x": 1}]},
        )
        body["choices"][0]["message"]["annotations"] = [{"source": "relay"}]
        gateway = make_gateway(handler_for(body))

        result = gateway.structured(MESSAGES, schema=Answer)

        assert result.ok
        assert result.data.answer == "可以"

    def test_fenced_json_is_accepted(self):
        from pydantic import BaseModel

        class Answer(BaseModel):
            answer: str

        gateway = make_gateway(
            handler_for(completion("```json\n" + json.dumps({"answer": "好"}) + "\n```"))
        )

        result = gateway.structured(MESSAGES, schema=Answer)

        assert result.ok
        assert result.data.answer == "好"


class TestTransportFailures:
    def test_timeout_is_a_failure_not_success(self):
        gateway = make_gateway(handler_for(exc=httpx.TimeoutException("timed out")))

        result = gateway.complete(MESSAGES)

        assert result.status is ModelCallStatus.TIMEOUT
        assert not result.ok
        assert result.error

    def test_rate_limit_is_classified(self):
        gateway = make_gateway(handler_for(status=429))

        result = gateway.complete(MESSAGES)

        assert result.status is ModelCallStatus.RATE_LIMITED
        assert not result.ok

    def test_server_error_is_provider_unavailable(self):
        gateway = make_gateway(handler_for(status=503))

        result = gateway.complete(MESSAGES)

        assert result.status is ModelCallStatus.PROVIDER_UNAVAILABLE
        assert not result.ok


class TestCapabilityUnavailable:
    def test_missing_key_fails_with_capability_unavailable(self):
        from bank_assistant.core.errors import CapabilityUnavailableError

        with pytest.raises(CapabilityUnavailableError, match="capability unavailable"):
            make_provider(api_key="")

    def test_no_provider_configured_fails_instead_of_mocking(self):
        from bank_assistant.core.errors import CapabilityUnavailableError

        gateway_kwargs = {"providers": {}, "primary": ModelProvider.DEEPSEEK}
        with pytest.raises(CapabilityUnavailableError, match="capability unavailable"):
            ModelGateway(**gateway_kwargs)


class TestReplayMode:
    def test_replay_is_explicit_and_visible_in_result_and_log(self, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="bank_assistant.core.model_gateway")

        gateway = ModelGateway(
            replay_responses=["这是回放内容"],
            max_retries=0,
        )

        assert gateway.mode == "replay"
        result = gateway.complete(MESSAGES)
        assert result.ok
        assert result.mode == "replay"
        assert result.text == "这是回放内容"
        assert any("REPLAY" in record.getMessage() for record in caplog.records)

    def test_replay_exhaustion_is_provider_unavailable(self):
        gateway = ModelGateway(replay_responses=[], max_retries=0)

        result = gateway.complete(MESSAGES)

        assert result.status is ModelCallStatus.PROVIDER_UNAVAILABLE
        assert not result.ok


class TestFallback:
    def test_fallback_switch_is_recorded_not_silent(self):
        relay = make_provider(
            ModelProvider.RELAY,
            base_url="https://relay.example/v1",
            model="relay-model",
        )

        def handler(request):
            if "relay.example" in str(request.url):
                return httpx.Response(200, json=completion("回退答复", model="relay-model"))
            return httpx.Response(
                503, json={"error": {"message": "unavailable", "type": "test"}}
            )

        gateway = make_gateway(
            handler,
            providers={ModelProvider.DEEPSEEK: make_provider(), ModelProvider.RELAY: relay},
            fallback=ModelProvider.RELAY,
        )

        result = gateway.complete(MESSAGES)

        assert result.ok
        assert result.provider is ModelProvider.RELAY
        assert result.fallback_used is True
        assert result.attempts == 2


class TestSettingsWiring:
    def test_providers_are_loaded_from_settings(self, monkeypatch):
        from bank_assistant.core import model_gateway as mg

        monkeypatch.setattr(mg.settings, "DEEPSEEK_API_KEY", "ds-key")
        monkeypatch.setattr(mg.settings, "DEEPSEEK_MODEL", "deepseek-chat")
        monkeypatch.setattr(mg.settings, "LLM_API_KEY", "relay-key")
        monkeypatch.setattr(mg.settings, "LLM_BASE_URL", "https://relay.example/v1")
        monkeypatch.setattr(mg.settings, "LLM_MODEL_CHAT", "relay-model")
        monkeypatch.setattr(mg.settings, "MODEL_PROVIDER", "deepseek")
        monkeypatch.setattr(mg.settings, "MODEL_FALLBACK_PROVIDER", "relay")

        gateway = mg.ModelGateway.from_settings()

        assert gateway.providers == (ModelProvider.DEEPSEEK, ModelProvider.RELAY)
        assert gateway.primary is ModelProvider.DEEPSEEK

    def test_missing_credentials_from_settings_fails_at_startup(self, monkeypatch):
        from bank_assistant.core import model_gateway as mg
        from bank_assistant.core.errors import CapabilityUnavailableError

        monkeypatch.setattr(mg.settings, "DEEPSEEK_API_KEY", None)
        monkeypatch.setattr(mg.settings, "LLM_API_KEY", None)
        monkeypatch.setattr(mg.settings, "MODEL_PROVIDER", "deepseek")
        monkeypatch.setattr(mg.settings, "MODEL_FALLBACK_PROVIDER", None)

        with pytest.raises(CapabilityUnavailableError, match="capability unavailable"):
            mg.ModelGateway.from_settings()
