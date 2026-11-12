"""认证/授权集成测试：真实 PostgreSQL + 真实空库迁移（M2-02）。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 起停一次性
PostgreSQL；本文件只假设 ``alembic upgrade head`` 已执行。

seam：
- ``POST /api/v1/auth/login``、``GET /api/v1/me``（真实 HTTP + 真实数据库）；
- 未来业务路由将使用的授权依赖（``require_scope`` / ``ensure_resource_visible``），
  用测试内探针路由走同样的 HTTP 错误契约；
- 数据库行本身：密码必须是 Argon2 哈希而非明文。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from bank_assistant.api.errors import install_error_handlers
from bank_assistant.infra.db import get_db_session, get_engine, session_scope
from bank_assistant.infra.models import User
from bank_assistant.security.authorization import (
    ensure_resource_visible,
    get_current_identity,
    require_scope,
)
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration

# 演示/测试专用假凭据，不是任何环境的真实秘密。
DEMO_PASSWORD = "demo-only-correct-horse-battery"
SCOPE_A = "demo-bank-a"
SCOPE_B = "demo-bank-b"


@pytest.fixture(scope="module")
def demo_users() -> dict[str, dict[str, object]]:
    """显式测试夹具建用户；任何导入路径都不会自动创建演示用户。"""
    suffix = uuid4().hex[:8]
    specs = {
        "agent_a": {
            "username": f"agent-a-{suffix}",
            "role": "agent",
            "scope_id": SCOPE_A,
            "active": True,
        },
        "agent_b": {
            "username": f"agent-b-{suffix}",
            "role": "agent",
            "scope_id": SCOPE_B,
            "active": True,
        },
        "inactive_a": {
            "username": f"inactive-a-{suffix}",
            "role": "agent",
            "scope_id": SCOPE_A,
            "active": False,
        },
    }
    created: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        for key, spec in specs.items():
            user = User(
                user_id=uuid4(),
                username=str(spec["username"]),
                password_hash=hash_password(DEMO_PASSWORD),
                role=str(spec["role"]),
                scope_id=str(spec["scope_id"]),
                active=bool(spec["active"]),
            )
            session.add(user)
            session.flush()
            created[key] = {**spec, "user_id": str(user.user_id)}
    return created


@pytest.fixture
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


@pytest.fixture
def probe_client() -> TestClient:
    """用未来业务路由的授权依赖搭一个最小探针应用。"""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/probe/scope-b")
    def probe_scope_b(
        identity: Identity = Depends(require_scope(SCOPE_B)),
    ) -> dict[str, str]:
        return {"scope_id": identity.scope_id}

    @app.get("/probe/users/{user_id}")
    def probe_user(
        user_id: UUID,
        identity: Identity = Depends(get_current_identity),
        session: Session = Depends(get_db_session),
    ) -> dict[str, str]:
        ensure_resource_visible(session.get(User, user_id), identity)
        return {"user_id": str(user_id)}

    return TestClient(app)


def _login(client: TestClient, username: str, password: str = DEMO_PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_success_returns_working_token_and_identity(
    client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    agent = demo_users["agent_a"]

    response = _login(client, str(agent["username"]))

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["user_id"] == agent["user_id"]
    assert body["user"]["role"] == "agent"
    assert body["user"]["scope_id"] == SCOPE_A
    assert DEMO_PASSWORD not in response.text

    me = client.get("/api/v1/me", headers=_bearer(body["access_token"]))

    assert me.status_code == 200
    assert me.json()["user_id"] == agent["user_id"]
    assert me.json()["scope_id"] == SCOPE_A


def test_client_supplied_role_and_scope_are_ignored(
    client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    """身份来自数据库；请求体里自报的 role/scope 不能抬高权限。"""
    agent = demo_users["agent_a"]

    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": agent["username"],
            "password": DEMO_PASSWORD,
            "role": "admin",
            "scope_id": SCOPE_B,
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "agent"
    assert response.json()["user"]["scope_id"] == SCOPE_A


def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    agent = demo_users["agent_a"]

    wrong_password = _login(client, str(agent["username"]), "definitely-wrong")
    unknown_user = _login(client, "no-such-user-at-all", DEMO_PASSWORD)

    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json()["code"] == unknown_user.json()["code"]
    assert wrong_password.json()["message"] == unknown_user.json()["message"]
    assert wrong_password.json()["code"] == "AUTHENTICATION_FAILED"
    assert "password" not in wrong_password.text.lower()


def test_me_without_token_is_401_in_contract_shape(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["retryable"] is False
    assert body["trace_id"]
    assert body["details"] == {}
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_tampered_and_foreign_signed_tokens(
    client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    agent = demo_users["agent_a"]
    valid = _login(client, str(agent["username"])).json()["access_token"]

    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": agent["user_id"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "attacker-secret-that-is-long-enough-32",
        algorithm="HS256",
    )

    assert client.get("/api/v1/me", headers=_bearer(valid + "x")).status_code == 401
    assert client.get("/api/v1/me", headers=_bearer(forged)).status_code == 401


def test_inactive_user_cannot_login_or_use_an_old_token(
    client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    inactive = demo_users["inactive_a"]

    login = _login(client, str(inactive["username"]))
    assert login.status_code == 401

    stale_token = create_access_token(
        Identity(
            user_id=UUID(str(inactive["user_id"])),
            username=str(inactive["username"]),
            role="agent",
            scope_id=SCOPE_A,
        )
    )
    assert client.get("/api/v1/me", headers=_bearer(stale_token)).status_code == 401


def test_cross_scope_request_is_403(
    client: TestClient, probe_client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    agent = demo_users["agent_a"]
    token = _login(client, str(agent["username"])).json()["access_token"]

    response = probe_client.get("/probe/scope-b", headers=_bearer(token))

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert SCOPE_B not in response.text


def test_invisible_resource_is_404_without_leaking_existence(
    client: TestClient, probe_client: TestClient, demo_users: dict[str, dict[str, object]]
) -> None:
    agent = demo_users["agent_a"]
    token = _login(client, str(agent["username"])).json()["access_token"]
    headers = _bearer(token)

    own = probe_client.get(f"/probe/users/{agent['user_id']}", headers=headers)
    foreign = probe_client.get(
        f"/probe/users/{demo_users['agent_b']['user_id']}", headers=headers
    )
    missing = probe_client.get(f"/probe/users/{uuid4()}", headers=headers)

    assert own.status_code == 200
    assert foreign.status_code == 404
    assert missing.status_code == 404
    # 同范围之外的资源与不存在的资源必须给出完全相同的判定信息。
    assert foreign.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    assert foreign.json()["message"] == missing.json()["message"]
    assert foreign.json()["details"] == missing.json()["details"] == {}


def test_probe_requires_authentication(probe_client: TestClient) -> None:
    response = probe_client.get(f"/probe/users/{uuid4()}")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_password_is_hashed_at_rest(
    demo_users: dict[str, dict[str, object]]
) -> None:
    with session_scope() as session:
        user = session.scalar(
            select(User).where(User.username == str(demo_users["agent_a"]["username"]))
        )

    assert user is not None
    assert user.password_hash != DEMO_PASSWORD
    assert DEMO_PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")


def test_users_table_matches_the_contract_minimum() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("users")}

    assert {
        "user_id",
        "username",
        "password_hash",
        "role",
        "scope_id",
        "active",
        "created_at",
        "updated_at",
    } <= columns


def test_alembic_has_a_single_head_containing_the_users_revision() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))

    assert len(script.get_heads()) == 1
    assert "0002_users" in {revision.revision for revision in script.walk_revisions()}
