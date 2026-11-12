"""T06：报告导出与复核不再伪造成功。

契约 §1：错误结构为 {code, message, trace_id, retryable, details}。
M4 接通真实持久化之前，这些接口必须明确返回未实现，而不是固定分数或假时间戳。
"""

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/quality-inspector"


@pytest.fixture
def client():
    from bank_assistant.main import app

    return TestClient(app)


def assert_not_implemented(response) -> dict:
    """断言返回契约 §1 的未实现错误，且不是 2xx 伪成功。"""
    assert response.status_code == 501
    body = response.json()
    assert body["code"] == "NOT_IMPLEMENTED"
    assert body["retryable"] is False
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["trace_id"], str) and body["trace_id"]
    assert isinstance(body["details"], dict)
    return body


class TestReportExport:
    def test_export_does_not_return_fabricated_scores(self, client):
        """导出不得返回固定的 85/90/85/95 分或“客服表现良好”总结。"""
        response = client.post(
            f"{API}/generate-report",
            json={"report_id": "session-demo", "format": "json"},
        )

        body = assert_not_implemented(response)
        # 未实现时不能带成功响应字段（内容、大小），更不能出现编造的评分与总结。
        assert "content" not in body
        assert "size" not in body
        assert "客服表现良好" not in response.text
        assert "总体评分" not in response.text

    def test_error_shape_echoes_request_trace_id(self, client):
        """错误结构沿用请求的 x-trace-id，便于串联调用链。"""
        response = client.post(
            f"{API}/generate-report",
            json={"report_id": "session-demo"},
            headers={"x-trace-id": "trace-t06-export"},
        )

        assert response.json()["trace_id"] == "trace-t06-export"


class TestReviewSubmission:
    def test_submit_review_has_no_fake_timestamp_or_review_id(self, client):
        """复核提交不得伪造 review_id 或 2024-01-01 时间戳。"""
        response = client.post(
            f"{API}/submit-review",
            json={"report_id": "session-demo", "reviewer": "质检员甲"},
        )

        body = assert_not_implemented(response)
        assert "review_id" not in body
        assert "submitted_at" not in body
        assert "2024-01-01T00:00:00" not in response.text

    @pytest.mark.parametrize(
        "path,payload",
        [
            ("/approve", {"review_id": "review_x", "approver": "质检员甲"}),
            (
                "/reject",
                {
                    "review_id": "review_x",
                    "approver": "质检员甲",
                    "reasons": ["证据不足"],
                },
            ),
        ],
    )
    def test_review_decisions_do_not_fake_updated_at(self, client, path, payload):
        """批准/拒绝不得伪造 updated_at，也不得凭进程内状态返回成功。"""
        response = client.post(f"{API}{path}", json=payload)

        body = assert_not_implemented(response)
        assert "updated_at" not in body
        assert "2024-01-01T00:00:00" not in response.text

    def test_pending_reviews_does_not_serve_instance_state(self, client):
        """待复核列表不得读取随请求销毁的实例字典。"""
        response = client.get(f"{API}/pending-reviews")

        body = assert_not_implemented(response)
        assert "count" not in body
        assert "reviews" not in body


class TestOpenApiHonesty:
    def test_unimplemented_operations_document_501_not_200(self, client):
        """未实现接口在 OpenAPI 中只声明 501，不再宣称 200 成功响应。"""
        spec = client.get("/openapi.json").json()
        operations = [
            ("/generate-report", "post"),
            ("/submit-review", "post"),
            ("/approve", "post"),
            ("/reject", "post"),
            ("/pending-reviews", "get"),
        ]

        for path, method in operations:
            responses = spec["paths"][f"{API}{path}"][method]["responses"]
            assert "200" not in responses, path
            assert "501" in responses, path
            schema = responses["501"]["content"]["application/json"]["schema"]
            assert schema.get("$ref", "").endswith("/ErrorResponse"), path
