"""T39 供应商故障演练的离线断言：契约映射与“不得假成功”。"""

from __future__ import annotations

import pytest

from bank_assistant.api.errors import _MODEL_FAILURE_SPECS
from bank_assistant.core.model_gateway import ModelCallStatus
from scripts.supplier_failure_drill import (
    has_fabricated_answer,
    verify_failure_response,
)


@pytest.mark.parametrize(
    ("model_status", "expected_status", "expected_code"),
    [
        (ModelCallStatus.RATE_LIMITED, 429, "MODEL_RATE_LIMITED"),
        (ModelCallStatus.TIMEOUT, 504, "MODEL_TIMEOUT"),
        (ModelCallStatus.PROVIDER_UNAVAILABLE, 503, "MODEL_UNAVAILABLE"),
        (ModelCallStatus.INVALID_STRUCTURE, 502, "MODEL_INVALID_STRUCTURE"),
        (ModelCallStatus.EMPTY_CONTENT, 502, "MODEL_EMPTY_CONTENT"),
    ],
)
def test_failure_mapping_matches_contract(
    model_status: ModelCallStatus, expected_status: int, expected_code: str
) -> None:
    status_code, code, _message = _MODEL_FAILURE_SPECS[model_status]
    assert (status_code, code) == (expected_status, expected_code)


def test_verify_failure_response_accepts_contract_error() -> None:
    body = {
        "code": "MODEL_RATE_LIMITED",
        "message": "模型调用被限流",
        "trace_id": "t39-drill-rate-limit",
        "retryable": True,
        "details": {"model_status": "rate_limited"},
    }
    assert (
        verify_failure_response(
            429,
            body,
            expected_status=429,
            expected_code="MODEL_RATE_LIMITED",
            expected_trace_id="t39-drill-rate-limit",
        )
        == []
    )


def test_verify_failure_response_rejects_fake_success_and_wrong_trace() -> None:
    problems = verify_failure_response(
        200,
        {"code": "OK", "retryable": True, "trace_id": "other", "answer": "伪造答复"},
        expected_status=502,
        expected_code="MODEL_INVALID_STRUCTURE",
        expected_trace_id="expected",
    )
    assert any("2xx" in problem for problem in problems)
    assert any("status=200" in problem for problem in problems)
    assert any("code=" in problem for problem in problems)
    assert any("trace_id" in problem for problem in problems)
    assert any("answer" in problem for problem in problems)


@pytest.mark.parametrize("retryable", [None, False, "true"])
def test_verify_failure_response_requires_boolean_retryable(
    retryable: object,
) -> None:
    body = {
        "code": "MODEL_TIMEOUT",
        "trace_id": "t39-drill-timeout",
        "retryable": retryable,
    }
    problems = verify_failure_response(
        504,
        body,
        expected_status=504,
        expected_code="MODEL_TIMEOUT",
    )
    assert any("retryable" in problem for problem in problems)


def test_has_fabricated_answer() -> None:
    assert has_fabricated_answer({"answer": "已为您办理", "citations": []}) is True
    assert has_fabricated_answer({"answer": "", "citations": [{"version_id": "v"}]}) is True
    assert has_fabricated_answer({"answer": "", "citations": []}) is False
    assert has_fabricated_answer({"answer": None, "citations": None}) is False
