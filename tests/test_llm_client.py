"""LLMClient 兼容层的行为测试。

所有测试都 monkeypatch DashScope 客户端，绝不联网、不使用真实凭据。
"""

from types import SimpleNamespace

import pytest


class TestChatSystemMessage:
    """chat() 必须把 system 消息真正发给模型（回归 #6 / M1-02）。"""

    def test_system_message_is_in_outgoing_payload(self, mocker):
        from bank_assistant.core.llm_client import LLMClient

        fake_call = mocker.patch("dashscope.Generation.call")
        fake_call.return_value = SimpleNamespace(
            status_code=200,
            output=SimpleNamespace(text="好的"),
        )

        client = LLMClient(api_key="test-key")
        messages = [
            {"role": "system", "content": "你是银行坐席助手，只依据给定知识回答。"},
            {"role": "user", "content": "信用卡年费怎么免？"},
        ]

        assert client.chat(messages) == "好的"

        payload = fake_call.call_args.kwargs
        assert payload["messages"] == messages
        assert payload["messages"][0] == {
            "role": "system",
            "content": "你是银行坐席助手，只依据给定知识回答。",
        }


class TestCapabilityUnavailable:
    """凭据/SDK 缺失必须显式失败，不能隐式 mock。"""

    def test_missing_api_key_fails_with_capability_unavailable(self):
        from bank_assistant.core.llm_client import CapabilityUnavailableError, LLMClient

        with pytest.raises(CapabilityUnavailableError, match="capability unavailable"):
            LLMClient(api_key="")

    def test_missing_sdk_fails_with_capability_unavailable(self, mocker):
        from bank_assistant.core.llm_client import CapabilityUnavailableError, LLMClient

        mocker.patch.dict("sys.modules", {"dashscope": None})

        with pytest.raises(CapabilityUnavailableError, match="capability unavailable"):
            LLMClient(api_key="test-key")

    def test_embedding_never_returns_a_random_vector(self):
        from bank_assistant.core.llm_client import CapabilityUnavailableError, LLMClient

        client = LLMClient(api_key="test-key")

        with pytest.raises(CapabilityUnavailableError, match="capability unavailable"):
            client.embedding("挂失信用卡")
