"""候选答复生成测试：系统失败只能落到 failed，不得伪装成业务状态。"""

import json

import httpx

from bank_assistant.core.model_gateway import (
    ModelCallStatus,
    ModelGateway,
    ModelProvider,
    ProviderSpec,
)
from bank_assistant.assistants.candidate_answer import (
    CandidateAnswerService,
    CandidateAnswerStatus,
)


MESSAGES = [
    {"role": "system", "content": "只依据给定知识回答。"},
    {"role": "user", "content": "信用卡挂失怎么办理？"},
]


def completion(content, finish_reason="stop"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def service_with(handler):
    provider = ProviderSpec(
        provider=ModelProvider.DEEPSEEK,
        base_url="https://api.deepseek.com/v1",
        api_key="test-key",
        model="deepseek-chat",
    )
    gateway = ModelGateway(
        providers={ModelProvider.DEEPSEEK: provider},
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    return CandidateAnswerService(gateway)


def answer_handler(payload, finish_reason="stop"):
    return lambda request: httpx.Response(200, json=completion(json.dumps(payload), finish_reason))


class TestBusinessStatesFromModel:
    def test_answered_carries_citations_and_provenance(self):
        service = service_with(
            answer_handler(
                {
                    "status": "answered",
                    "answer": "请携带身份证到柜台或通过手机银行办理挂失。",
                    "citations": [{"version_id": "v1", "chunk_id": "c1", "quote": "挂失"}],
                    "missing_information": [],
                }
            )
        )

        result = service.generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.ANSWERED
        assert result.model_status is ModelCallStatus.SUCCEEDED
        assert "挂失" in result.answer
        assert result.citations[0].version_id == "v1"
        assert result.provider == "deepseek"
        assert result.model == "deepseek-chat"
        assert result.mode == "live"

    def test_model_requested_clarification_is_a_business_state(self):
        service = service_with(
            answer_handler(
                {
                    "status": "needs_clarification",
                    "answer": "",
                    "missing_information": ["卡号后四位"],
                }
            )
        )

        result = service.generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.NEEDS_CLARIFICATION
        assert result.missing_information == ("卡号后四位",)
        assert result.model_status is ModelCallStatus.SUCCEEDED


class TestSystemFailuresAreNotBusinessStates:
    def test_timeout_becomes_failed_timeout(self):
        def handler(request):
            raise httpx.TimeoutException("timed out")

        result = service_with(handler).generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.FAILED
        assert result.model_status is ModelCallStatus.TIMEOUT
        assert result.status not in {
            CandidateAnswerStatus.NEEDS_CLARIFICATION,
            CandidateAnswerStatus.NEEDS_HUMAN,
        }
        assert result.error

    def test_non_json_becomes_failed_invalid_structure(self):
        result = service_with(
            lambda request: httpx.Response(200, json=completion("我不确定"))
        ).generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.FAILED
        assert result.model_status is ModelCallStatus.INVALID_STRUCTURE

    def test_length_with_empty_content_becomes_failed_empty_content(self):
        result = service_with(
            lambda request: httpx.Response(
                200, json=completion("", finish_reason="length")
            )
        ).generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.FAILED
        assert result.model_status is ModelCallStatus.EMPTY_CONTENT

    def test_answered_with_empty_answer_is_invalid_structure(self):
        result = service_with(
            answer_handler({"status": "answered", "answer": "  ", "citations": []})
        ).generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.FAILED
        assert result.model_status is ModelCallStatus.INVALID_STRUCTURE

    def test_provider_unavailable_becomes_failed(self):
        def handler(request):
            return httpx.Response(503, json={"error": {"message": "down"}})

        result = service_with(handler).generate(MESSAGES)

        assert result.status is CandidateAnswerStatus.FAILED
        assert result.model_status is ModelCallStatus.PROVIDER_UNAVAILABLE
