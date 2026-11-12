"""T08：所有端点共享同一错误契约与 trace_id。

契约 §1：``{code, message, trace_id, retryable, details}``；4xx/409/422 映射，
依赖失败用 502/503/504；错误响应不得回传堆栈或原始异常文本。
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bank_assistant.api.errors import (
    ApiError,
    TraceIdMiddleware,
    install_error_handlers,
    model_failure_error,
)
from bank_assistant.core.errors import CapabilityUnavailableError
from bank_assistant.core.model_gateway import (
    ModelCallStatus,
    ModelProvider,
    ModelResult,
)

ERROR_KEYS = {"code", "message", "trace_id", "retryable", "details"}


def build_app(register_routes, *, raise_server_exceptions: bool = True) -> TestClient:
    """构造仅用于验证错误契约的最小应用（与 main.py 使用同一套安装函数）。"""
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)
    register_routes(app)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_api_error_renders_shared_shape():
    def routes(app):
        @app.get("/missing")
        async def missing():
            raise ApiError(
                status_code=404,
                code="KNOWLEDGE_NOT_FOUND",
                message="知识不存在",
            )

    client = build_app(routes)
    response = client.get("/missing")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == "KNOWLEDGE_NOT_FOUND"
    assert body["message"] == "知识不存在"
    assert body["retryable"] is False
    assert body["details"] == {}
    assert body["trace_id"]


@pytest.mark.parametrize(
    "status_code,expected_code",
    [
        (400, "INVALID_INPUT"),
        (401, "UNAUTHENTICATED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (409, "CONFLICT"),
        (422, "VALIDATION_ERROR"),
        (429, "RATE_LIMITED"),
        (502, "DEPENDENCY_BAD_RESPONSE"),
        (503, "DEPENDENCY_UNAVAILABLE"),
        (504, "DEPENDENCY_TIMEOUT"),
    ],
)
def test_http_status_maps_to_stable_error_code(status_code, expected_code):
    def routes(app):
        @app.get("/status")
        async def status():
            raise ApiError(status_code=status_code, message="错误")

    response = build_app(routes).get("/status")

    assert response.status_code == status_code
    assert response.json()["code"] == expected_code


@pytest.mark.parametrize("status_code", [429, 502, 503, 504])
def test_dependency_and_rate_limit_errors_are_retryable(status_code):
    def routes(app):
        @app.get("/retryable")
        async def retryable():
            raise ApiError(status_code=status_code, message="稍后重试")

    assert build_app(routes).get("/retryable").json()["retryable"] is True


def test_request_trace_id_is_echoed_in_body_and_header():
    def routes(app):
        @app.get("/ok")
        async def ok():
            return {"status": "ok"}

        @app.get("/fail")
        async def fail():
            raise ApiError(status_code=409, message="版本冲突")

    client = build_app(routes)
    ok = client.get("/ok", headers={"x-trace-id": "trace-t08-ok"})
    failed = client.get("/fail", headers={"x-trace-id": "trace-t08-fail"})

    assert ok.headers["x-trace-id"] == "trace-t08-ok"
    assert failed.headers["x-trace-id"] == "trace-t08-fail"
    assert failed.json()["trace_id"] == "trace-t08-fail"


def test_trace_id_is_generated_when_absent_and_echoed():
    def routes(app):
        @app.get("/fail")
        async def fail():
            raise ApiError(status_code=500, message="内部错误")

    response = build_app(routes).get("/fail")

    trace_id = response.headers["x-trace-id"]
    assert trace_id
    assert response.json()["trace_id"] == trace_id


def test_invalid_input_is_422_without_exception_text():
    from bank_assistant.main import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/quality-inspector/parse-conversation",
        json={},
        headers={"x-trace-id": "trace-t08-invalid"},
    )

    assert response.status_code == 422
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == "VALIDATION_ERROR"
    assert body["trace_id"] == "trace-t08-invalid"
    assert body["retryable"] is False
    assert isinstance(body["details"], dict)
    # 不回传 pydantic 原始错误列表、堆栈或异常文本
    assert "detail" not in body
    assert "Traceback" not in response.text
    assert "pydantic" not in response.text.lower()


def test_unknown_route_returns_unified_404():
    from bank_assistant.main import app

    response = TestClient(app).get(
        "/no-such-endpoint", headers={"x-trace-id": "trace-t08-404"}
    )

    assert response.status_code == 404
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == "NOT_FOUND"
    assert body["trace_id"] == "trace-t08-404"


def test_unhandled_exception_is_500_without_raw_exception_text():
    def routes(app):
        @app.get("/boom")
        async def boom():
            raise RuntimeError("secret-internal-detail password=hunter2")

    response = build_app(routes, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == "INTERNAL_ERROR"
    assert "secret-internal-detail" not in response.text
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["x-trace-id"] == body["trace_id"]


def test_5xx_http_exception_does_not_leak_detail():
    def routes(app):
        @app.get("/legacy-500")
        async def legacy_500():
            raise HTTPException(
                status_code=500,
                detail="OperationalError: connection refused secret-token",
            )

    response = build_app(routes).get("/legacy-500")

    assert response.status_code == 500
    assert "OperationalError" not in response.text
    assert "secret-token" not in response.text
    assert response.json()["code"] == "INTERNAL_ERROR"


def test_capability_unavailable_is_503_without_credential_details():
    def routes(app):
        @app.get("/capability")
        async def capability():
            raise CapabilityUnavailableError(
                "capability unavailable: DASHSCOPE_API_KEY 未配置"
            )

    response = build_app(routes).get("/capability")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "CAPABILITY_UNAVAILABLE"
    assert body["retryable"] is False
    assert "DASHSCOPE_API_KEY" not in response.text


@pytest.mark.parametrize(
    "model_status,expected_http,expected_code",
    [
        (ModelCallStatus.TIMEOUT, 504, "MODEL_TIMEOUT"),
        (ModelCallStatus.RATE_LIMITED, 429, "MODEL_RATE_LIMITED"),
        (
            ModelCallStatus.PROVIDER_UNAVAILABLE,
            503,
            "MODEL_UNAVAILABLE",
        ),
        (
            ModelCallStatus.INVALID_STRUCTURE,
            502,
            "MODEL_INVALID_STRUCTURE",
        ),
        (ModelCallStatus.EMPTY_CONTENT, 502, "MODEL_EMPTY_CONTENT"),
    ],
)
def test_model_gateway_failure_maps_to_dependency_status(
    model_status, expected_http, expected_code
):
    def routes(app):
        @app.get("/model")
        async def model():
            raise model_failure_error(
                ModelResult(
                    status=model_status,
                    provider=ModelProvider.DEEPSEEK,
                    model="deepseek-chat",
                    error="upstream sk-live-secret",
                )
            )

    response = build_app(routes).get("/model")
    body = response.json()

    assert response.status_code == expected_http
    assert body["code"] == expected_code
    assert body["retryable"] is True
    assert body["details"]["model_status"] == model_status.value
    # details 只含安全诊断信息，不含上游原始错误或凭据
    assert set(body["details"]) <= {"model_status", "provider", "model"}
    assert "sk-live-secret" not in response.text


def test_model_failure_helper_rejects_success():
    with pytest.raises(ValueError):
        model_failure_error(ModelResult(status=ModelCallStatus.SUCCEEDED))


def test_health_live_stays_non_error_and_echoes_trace():
    from bank_assistant.main import app

    response = TestClient(app).get(
        "/health/live", headers={"x-trace-id": "trace-t08-live"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert response.headers["x-trace-id"] == "trace-t08-live"


def test_parse_conversation_4xx_has_trace_id():
    from bank_assistant.main import app

    response = TestClient(app).post(
        "/api/v1/quality-inspector/parse-conversation",
        json={"content": "   ", "format": "text"},
        headers={"x-trace-id": "trace-t08-parse"},
    )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == "CONVERSATION_EMPTY"
    assert body["trace_id"] == "trace-t08-parse"
    assert response.headers["x-trace-id"] == "trace-t08-parse"


def test_not_implemented_error_shape_is_unchanged():
    from bank_assistant.main import app

    response = TestClient(app).post(
        "/api/v1/quality-inspector/generate-report",
        json={"report_id": "session-demo"},
        headers={"x-trace-id": "trace-t08-501"},
    )

    assert response.status_code == 501
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == "NOT_IMPLEMENTED"
    assert body["trace_id"] == "trace-t08-501"
