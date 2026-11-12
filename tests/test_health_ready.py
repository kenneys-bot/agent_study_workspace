"""健康探针的边界：readiness 依赖真实数据库，liveness 不依赖。

单元测试不连数据库：数据库检查由 monkeypatch 替换，只验证 HTTP 契约
（200/503、标准错误结构、trace_id 透传），真实 SQL 由 tests/integration 覆盖。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError


def _database_down() -> None:
    raise OperationalError(
        "SELECT 1", {}, ConnectionRefusedError("connection refused")
    )


@pytest.fixture
def client():
    from bank_assistant.main import app

    return TestClient(app)


def test_ready_returns_503_when_database_unavailable(client, monkeypatch):
    from bank_assistant import main

    monkeypatch.setattr(main, "ping_database", _database_down)

    response = client.get("/health/ready", headers={"X-Trace-Id": "trace-ready-503"})

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "DEPENDENCY_UNAVAILABLE"
    assert body["retryable"] is True
    assert body["trace_id"] == "trace-ready-503"
    assert body["details"] == {"dependency": "postgresql"}
    # 不把底层连接细节回给调用方
    assert "connection refused" not in body["message"].lower()


def test_ready_returns_200_when_database_reachable(client, monkeypatch):
    from bank_assistant import main

    monkeypatch.setattr(main, "ping_database", lambda: None)

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"


def test_live_does_not_touch_database(client, monkeypatch):
    from bank_assistant import main

    def fail_if_called() -> None:
        raise AssertionError("liveness 不应访问数据库")

    monkeypatch.setattr(main, "ping_database", fail_if_called)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"
