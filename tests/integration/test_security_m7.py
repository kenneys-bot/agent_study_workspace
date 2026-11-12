"""M7-03/M7-04/T30 安全与恢复集成测试（真实 PostgreSQL + 真实 HTTP）。

覆盖契约 §7 的入口级证据：

- 未认证访问全部契约 §5 业务端点 → 401；
- 跨范围读取会话/任务 → 404，创建时声明他人范围 → 403；
- 审核/导出角色分离：agent 不能创建复核、提交决定或导出报告；
- 重复 ``Idempotency-Key`` → 同一任务/结果（CT-05/CT-06 的端到端变体）；
- ``/metrics`` 从真实数据库采集 DB readiness 与队列深度，并记录任务租约事件。

CT-07（并发复核 409）与 CT-11（worker 崩溃后租约恢复、单一最终结果）已有
``tests/integration/test_reviews.py`` 与 ``tests/integration/test_inspections.py``
的专门用例；本文件不重复伪造 fixture，只在报告中引用它们。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from bank_assistant.infra.db import get_session_factory, session_scope
from bank_assistant.infra.models import User
from bank_assistant.infra.task_queue import TaskQueue
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

pytestmark = pytest.mark.integration

SUFFIX = uuid4().hex[:8]
SCOPE_A = f"security-a-{SUFFIX}"
SCOPE_B = f"security-b-{SUFFIX}"
DEMO_PASSWORD = "demo-only-correct-horse-battery"
API = "/api/v1"


def _create_user(username: str, role: str, scope_id: str) -> dict[str, object]:
    with session_scope() as session:
        user = User(
            user_id=uuid4(),
            username=username,
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            scope_id=scope_id,
            active=True,
        )
        session.add(user)
        session.flush()
        user_id = user.user_id
    return {
        "user_id": user_id,
        "username": username,
        "role": role,
        "scope_id": scope_id,
        "token": create_access_token(
            Identity(
                user_id=user_id,
                username=username,
                role=role,
                scope_id=scope_id,
            )
        ),
    }


@pytest.fixture(scope="module")
def users() -> dict[str, dict[str, object]]:
    return {
        "agent_a": _create_user(f"sec-agent-a-{SUFFIX}", "agent", SCOPE_A),
        "reviewer_a": _create_user(
            f"sec-reviewer-a-{SUFFIX}", "reviewer", SCOPE_A
        ),
        "agent_b": _create_user(f"sec-agent-b-{SUFFIX}", "agent", SCOPE_B),
    }


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


def _headers(user: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


# ---------------------------------------------------------------------------
# 未认证 401
# ---------------------------------------------------------------------------


def test_all_contract_business_endpoints_require_authentication(client):
    missing = uuid4()
    cases = [
        ("GET", f"{API}/me", {}),
        ("POST", f"{API}/knowledge/documents", {"json": {}}),
        ("GET", f"{API}/knowledge/versions/{missing}", {}),
        ("POST", f"{API}/conversations", {"json": {}}),
        ("GET", f"{API}/conversations/{missing}", {}),
        ("POST", f"{API}/suggestions", {"json": {}}),
        ("GET", f"{API}/suggestions/{missing}", {}),
        ("POST", f"{API}/inspections", {"json": {}}),
        ("GET", f"{API}/inspections/{missing}", {}),
        ("POST", f"{API}/reviews", {"json": {}}),
        ("GET", f"{API}/reviews", {}),
        ("GET", f"{API}/reviews/{missing}", {}),
        (
            "POST",
            f"{API}/reviews/{missing}/decisions",
            {"json": {"finding_id": str(missing)}},
        ),
        ("POST", f"{API}/reviews/{missing}/complete", {"json": {}}),
        ("POST", f"{API}/reviews/{missing}/reopen", {"json": {}}),
        ("POST", f"{API}/reports", {"json": {}}),
        ("GET", f"{API}/reports/{missing}", {}),
        (
            "POST",
            f"{API}/tasks",
            {
                "json": {"type": "auth-check", "payload_ref": "x"},
                "headers": {"Idempotency-Key": f"auth-{missing}"},
            },
        ),
        ("GET", f"{API}/tasks/{missing}", {}),
        ("POST", f"{API}/rules", {"json": {}}),
        ("GET", f"{API}/rules/{missing}", {}),
        ("GET", f"{API}/system/version", {}),
        # 旧版 M1 原型端点也必须认证（它们仍会处理会话/知识文本）。
        ("POST", f"{API}/knowledge-base/extract-questions", {"json": {}}),
        (
            "POST",
            f"{API}/quality-inspector/parse-conversation",
            {"json": {}},
        ),
        ("POST", f"{API}/script-recommender/analyze-context", {"json": {}}),
        (
            "POST",
            f"{API}/quality-inspector/generate-report",
            {"json": {}},
        ),
    ]

    for method, path, kwargs in cases:
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        body = response.json()
        assert body["code"] == "UNAUTHENTICATED", (method, path, body)
        assert body["trace_id"]


# ---------------------------------------------------------------------------
# 跨范围 404 / 403
# ---------------------------------------------------------------------------


def test_cross_scope_conversation_read_is_404_without_leaking_body(client, users):
    marker = "跨范围不应泄露的会话正文"
    created = client.post(
        f"{API}/conversations",
        json={"content": f"客户：{marker}\n客服：收到", "format": "text"},
        headers=_headers(users["agent_a"]),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation_id"]

    response = client.get(
        f"{API}/conversations/{conversation_id}",
        headers=_headers(users["agent_b"]),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert marker not in response.text


def test_cross_scope_task_read_is_404(client, users):
    created = client.post(
        f"{API}/tasks",
        json={"type": "security-scope", "payload_ref": str(uuid4())},
        headers={
            **_headers(users["agent_a"]),
            "Idempotency-Key": f"scope-task-{uuid4().hex}",
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]

    response = client.get(
        f"{API}/tasks/{task_id}", headers=_headers(users["agent_b"])
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_creating_conversation_for_another_scope_is_403(client, users):
    response = client.post(
        f"{API}/conversations",
        json={
            "content": "客户：你好\n客服：您好",
            "format": "text",
            "scope_id": SCOPE_B,
        },
        headers=_headers(users["agent_a"]),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 审核/导出角色分离
# ---------------------------------------------------------------------------


def test_agent_cannot_review_or_export_but_reviewer_can_enter_service(client, users):
    missing = uuid4()
    agent_headers = _headers(users["agent_a"])
    reviewer_headers = _headers(users["reviewer_a"])

    create_review = client.post(
        f"{API}/reviews",
        json={"inspection_id": str(missing), "assignee_id": str(missing)},
        headers=agent_headers,
    )
    assert create_review.status_code == 403
    assert create_review.json()["code"] == "FORBIDDEN"

    decision = client.post(
        f"{API}/reviews/{missing}/decisions",
        json={
            "finding_id": str(missing),
            "action": "confirm",
            "expected_revision": 0,
            "reason": "x",
        },
        headers=agent_headers,
    )
    assert decision.status_code == 403

    export = client.post(
        f"{API}/reports",
        json={"inspection_id": str(missing), "format": "json"},
        headers=agent_headers,
    )
    assert export.status_code == 403
    assert export.json()["code"] == "FORBIDDEN"

    # 同一范围内 agent 仍可读列表（读取按 scope 授权，不是写入权限）。
    listing = client.get(f"{API}/reviews", headers=agent_headers)
    assert listing.status_code == 200

    # reviewer 通过角色门禁后进入服务层；资源不存在 → 404（不是 403）。
    reviewer_export = client.post(
        f"{API}/reports",
        json={"inspection_id": str(missing), "format": "json"},
        headers=reviewer_headers,
    )
    assert reviewer_export.status_code == 404


# ---------------------------------------------------------------------------
# 幂等重复提交
# ---------------------------------------------------------------------------


def test_duplicate_idempotency_key_returns_same_task_and_replay_marker(client, users):
    key = f"dup-{uuid4().hex}"
    payload = {"type": "security-idempotent", "payload_ref": "conversation-ref"}
    headers = {**_headers(users["agent_a"]), "Idempotency-Key": key}

    first = client.post(f"{API}/tasks", json=payload, headers=headers)
    second = client.post(f"{API}/tasks", json=payload, headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.json()["task_id"] == second.json()["task_id"]

    conflict = client.post(
        f"{API}/tasks",
        json={**payload, "payload_ref": "different"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


# ---------------------------------------------------------------------------
# /metrics 真实采集
# ---------------------------------------------------------------------------


def test_metrics_collects_db_readiness_queue_depth_and_task_events(client, users):
    # 造一个真实任务并用真实租约队列领取/完成，验证指标接线。
    task_type = f"security-metrics-{SUFFIX}"
    created = client.post(
        f"{API}/tasks",
        json={"type": task_type, "payload_ref": str(uuid4())},
        headers={
            **_headers(users["agent_a"]),
            "Idempotency-Key": f"metrics-{uuid4().hex}",
        },
    )
    assert created.status_code == 201, created.text
    task_id = UUID(created.json()["task_id"])

    queue = TaskQueue(session_factory=get_session_factory())
    clock = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    claimed = queue.claim_next(task_type, 30, owner="metrics-worker")
    assert claimed is not None and claimed.task_id == task_id
    queue.complete(task_id, owner="metrics-worker", result_ref="result://ok")

    response = client.get("/metrics")

    assert response.status_code == 200
    text = response.text
    assert "bank_db_ready 1" in text
    assert (
        'bank_task_queue_depth{task_type="inspection",status="queued"}'
        in text
    )
    assert (
        f'bank_task_events_total{{event="claim",task_type="{task_type}"}} 1'
        in text
    )
    assert (
        f'bank_task_events_total{{event="success",task_type="{task_type}"}} 1'
        in text
    )
    # 指标里不能出现用户 id、令牌或任何会话内容。
    assert str(users["agent_a"]["user_id"]) not in text
    assert str(users["agent_a"]["token"]) not in text


def test_metrics_series_have_no_secret_values(client):
    text = client.get("/metrics").text
    assert "SECRET_KEY" not in text
    assert "DATABASE_URL" not in text
    assert "sk-" not in text
    # 只输出 Prometheus 文本，不返回 HTML 调试页。
    assert text.startswith("# HELP") or text.startswith("# TYPE")
