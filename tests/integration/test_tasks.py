"""任务与幂等的集成测试：真实 PostgreSQL（M2-05，CT-05/CT-06/CT-11）。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 提供一次性
PostgreSQL 18 容器，并在空库上执行 ``alembic upgrade head``。本文件只假设
迁移已完成，不启动、不清理容器。

seam：
- ``POST /api/v1/tasks`` / ``GET /api/v1/tasks/{id}``：真实 HTTP + 真实数据库；
- ``TaskQueue.claim_next/renew/complete/fail``：worker 使用的租约接口；
- 数据库表结构：columns 与迁移 head 可达性。

并发测试使用两个真实数据库会话 + ``SELECT ... FOR UPDATE SKIP LOCKED``，
不用进程内锁（契约 §3、ADR-0002）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, inspect, select, text

from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import Task, User
from bank_assistant.infra.task_queue import (
    MAX_ATTEMPTS_ERROR_CODE,
    LeaseLostError,
    TaskQueue,
)
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration

DEMO_PASSWORD = "demo-only-correct-horse-battery"
TASKS_ENDPOINT = "/api/v1/tasks"
SCOPE_A = "demo-bank-a"
SCOPE_B = "demo-bank-b"


class Clock:
    """可推进的测试时钟：租约过期不靠 sleep。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="module")
def users() -> dict[str, dict[str, object]]:
    suffix = uuid4().hex[:8]
    specs = {
        "agent_a": (f"t13-agent-a-{suffix}", SCOPE_A),
        "agent_b": (f"t13-agent-b-{suffix}", SCOPE_B),
    }
    created: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        for key, (username, scope_id) in specs.items():
            user = User(
                user_id=uuid4(),
                username=username,
                password_hash=hash_password(DEMO_PASSWORD),
                role="agent",
                scope_id=scope_id,
                active=True,
            )
            session.add(user)
            session.flush()
            created[key] = {
                "user_id": user.user_id,
                "username": username,
                "scope_id": scope_id,
                "token": create_access_token(
                    Identity(
                        user_id=user.user_id,
                        username=username,
                        role="agent",
                        scope_id=scope_id,
                    )
                ),
            }
    return created


@pytest.fixture
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


def _headers(user: dict[str, object], key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {user['token']}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _unique_type() -> str:
    return f"t13{uuid4().hex[:12]}"


def _count_tasks_for_key(key: str) -> int:
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count()).select_from(Task).where(
                    Task.idempotency_key == key
                )
            )
        )


# ---------------------------------------------------------------------------
# 幂等键：POST /tasks
# ---------------------------------------------------------------------------


def test_same_key_and_input_returns_the_original_task(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    agent = users["agent_a"]
    key = f"idem-{uuid4().hex}"
    body = {"type": "inspection", "payload_ref": "conversation:demo-1"}

    first = client.post(TASKS_ENDPOINT, json=body, headers=_headers(agent, key))
    second = client.post(TASKS_ENDPOINT, json=body, headers=_headers(agent, key))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers.get("idempotent-replayed") == "true"
    assert second.json() == first.json()
    assert second.json()["task_id"] == first.json()["task_id"]
    assert second.json()["status"] == "queued"
    assert _count_tasks_for_key(key) == 1


def test_same_key_with_different_input_is_409(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    agent = users["agent_a"]
    key = f"idem-{uuid4().hex}"
    original = {"type": "inspection", "payload_ref": "conversation:demo-1"}

    first = client.post(
        TASKS_ENDPOINT, json=original, headers=_headers(agent, key)
    )
    second = client.post(
        TASKS_ENDPOINT,
        json={"type": "inspection", "payload_ref": "conversation:demo-2"},
        headers=_headers(agent, key),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert body["retryable"] is False
    assert body["trace_id"]
    # 冲突响应不泄露原输入内容，也没有创建第二个任务。
    assert "conversation:demo-2" not in second.text
    assert _count_tasks_for_key(key) == 1


def test_idempotency_key_is_bound_to_the_user(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    key = f"idem-{uuid4().hex}"
    body = {"type": "inspection", "payload_ref": "conversation:demo-1"}

    first = client.post(
        TASKS_ENDPOINT, json=body, headers=_headers(users["agent_a"], key)
    )
    other_user = client.post(
        TASKS_ENDPOINT, json=body, headers=_headers(users["agent_b"], key)
    )

    assert first.status_code == 201
    assert other_user.status_code == 201
    assert other_user.json()["task_id"] != first.json()["task_id"]
    assert _count_tasks_for_key(key) == 2


def test_concurrent_same_key_requests_create_exactly_one_task(
    users: dict[str, dict[str, object]]
) -> None:
    """两个并发请求同时使用同一键：一个创建、一个重放，绝不创建重复任务。"""
    from bank_assistant.main import app

    key = f"idem-{uuid4().hex}"
    body = {"type": "inspection", "payload_ref": "conversation:race"}
    barrier = Barrier(2)

    def post() -> Response:
        barrier.wait(timeout=10)
        with TestClient(app) as client:
            return client.post(
                TASKS_ENDPOINT,
                json=body,
                headers=_headers(users["agent_a"], key),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(), range(2)))

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 201]
    task_ids = {response.json()["task_id"] for response in responses}
    assert len(task_ids) == 1
    assert _count_tasks_for_key(key) == 1


def test_missing_idempotency_key_is_rejected(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    response = client.post(
        TASKS_ENDPOINT,
        json={"type": "inspection"},
        headers=_headers(users["agent_a"]),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_get_task_hides_other_scopes_as_404(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    agent = users["agent_a"]
    created = client.post(
        TASKS_ENDPOINT,
        json={"type": "inspection", "payload_ref": "conversation:demo-1"},
        headers=_headers(agent, f"idem-{uuid4().hex}"),
    )
    task_id = created.json()["task_id"]

    own = client.get(f"{TASKS_ENDPOINT}/{task_id}", headers=_headers(agent))
    foreign = client.get(
        f"{TASKS_ENDPOINT}/{task_id}", headers=_headers(users["agent_b"])
    )
    missing = client.get(
        f"{TASKS_ENDPOINT}/{uuid4()}", headers=_headers(agent)
    )

    assert own.status_code == 200
    assert own.json()["task_id"] == task_id
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    assert foreign.json()["details"] == missing.json()["details"] == {}


# ---------------------------------------------------------------------------
# 租约：TaskQueue
# ---------------------------------------------------------------------------


def test_two_workers_claim_concurrently_without_double_processing(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    task_type = _unique_type()
    created = client.post(
        TASKS_ENDPOINT,
        json={"type": task_type, "payload_ref": "conversation:concurrent"},
        headers=_headers(users["agent_a"], f"idem-{uuid4().hex}"),
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    barrier = Barrier(2)

    def claim(owner: str) -> Task | None:
        barrier.wait(timeout=10)
        return TaskQueue().claim_next(task_type, 30, owner=owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))

    claimed = [task for task in results if task is not None]
    assert len(claimed) == 1
    assert str(claimed[0].task_id) == task_id
    assert claimed[0].attempts == 1

    with session_scope() as session:
        stored = session.get(Task, UUID(task_id))
        assert stored is not None
        assert stored.attempts == 1
        assert stored.status == "running"

    # 同一任务不会被第二个 worker 再领取；完成后也不会。
    winner = claimed[0].lease_owner
    assert winner in {"worker-a", "worker-b"}
    loser = "worker-b" if winner == "worker-a" else "worker-a"
    assert TaskQueue().claim_next(task_type, 30, owner=loser) is None

    completed = TaskQueue().complete(
        UUID(task_id), owner=winner or "", result_ref="result:concurrent"
    )
    assert completed.status == "succeeded"
    assert completed.result_ref == "result:concurrent"
    # 第二个 worker 即使拿到过期任务也不能写最终结果。
    with pytest.raises(LeaseLostError):
        TaskQueue().complete(
            UUID(task_id), owner=loser, result_ref="result:duplicate"
        )


def test_two_workers_drain_a_backlog_without_double_claiming(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """两个 worker 并发抽干 5 个任务：每个任务恰好被领取一次。"""
    task_type = _unique_type()
    created_ids: set[str] = set()
    for index in range(5):
        created = client.post(
            TASKS_ENDPOINT,
            json={
                "type": task_type,
                "payload_ref": f"conversation:backlog-{index}",
            },
            headers=_headers(users["agent_a"], f"idem-{uuid4().hex}"),
        )
        assert created.status_code == 201
        created_ids.add(created.json()["task_id"])

    barrier = Barrier(2)

    def drain(owner: str) -> list[str]:
        barrier.wait(timeout=10)
        queue = TaskQueue()
        claimed: list[str] = []
        while True:
            task = queue.claim_next(task_type, 30, owner=owner)
            if task is None:
                return claimed
            claimed.append(str(task.task_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(drain, ["worker-a", "worker-b"]))

    claimed_ids = [task_id for batch in batches for task_id in batch]
    assert len(claimed_ids) == 5
    assert len(set(claimed_ids)) == 5
    assert set(claimed_ids) == created_ids

    with session_scope() as session:
        attempts = session.scalars(
            select(Task.attempts).where(Task.type == task_type)
        ).all()
    assert attempts == [1, 1, 1, 1, 1]


def test_renew_extends_the_lease_for_the_owner(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    task_type = _unique_type()
    created = client.post(
        TASKS_ENDPOINT,
        json={"type": task_type, "payload_ref": "conversation:renew"},
        headers=_headers(users["agent_a"], f"idem-{uuid4().hex}"),
    )
    task_id = UUID(created.json()["task_id"])

    start = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    clock = Clock(start)
    queue = TaskQueue(now=clock)
    assert queue.claim_next(task_type, 10, owner="worker-a") is not None

    # 原租约在 +10s 到期；+9s 续租后延长到 +19s，+15s 仍可完成。
    clock.now = start + timedelta(seconds=9)
    renewed = queue.renew(task_id, owner="worker-a", lease_seconds=10)
    assert renewed.lease_expires_at == start + timedelta(seconds=19)

    clock.now = start + timedelta(seconds=15)
    polished = queue.complete(task_id, owner="worker-a", result_ref="result:renewed")
    assert polished.status == "succeeded"
    assert polished.result_ref == "result:renewed"


def test_expired_lease_can_be_reclaimed_and_old_owner_loses_it(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    task_type = _unique_type()
    created = client.post(
        TASKS_ENDPOINT,
        json={"type": task_type, "payload_ref": "conversation:crash"},
        headers=_headers(users["agent_a"], f"idem-{uuid4().hex}"),
    )
    task_id = UUID(created.json()["task_id"])

    start = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    crashed = TaskQueue(now=Clock(start))
    claimed = crashed.claim_next(task_type, 10, owner="worker-a")
    assert claimed is not None
    assert claimed.task_id == task_id
    assert claimed.attempts == 1

    later = start + timedelta(seconds=11)
    recovered = TaskQueue(now=Clock(later))
    reclaimed = recovered.claim_next(task_type, 10, owner="worker-b")

    assert reclaimed is not None
    assert reclaimed.task_id == task_id
    assert reclaimed.attempts == 2
    assert reclaimed.lease_owner == "worker-b"

    # 原 worker 的租约已过期：续租/完成/报告失败都必须被拒绝。
    crashed_later = TaskQueue(now=Clock(later))
    with pytest.raises(LeaseLostError):
        crashed_later.renew(task_id, owner="worker-a", lease_seconds=10)
    with pytest.raises(LeaseLostError):
        crashed_later.complete(task_id, owner="worker-a", result_ref="result:stale")
    with pytest.raises(LeaseLostError):
        crashed_later.fail(task_id, owner="worker-a", error_code="STALE_WORKER")

    # 新租约持有者可以正常完成，且结果只写一次。
    finished = recovered.complete(task_id, owner="worker-b", result_ref="result:ok")
    assert finished.status == "succeeded"
    assert finished.result_ref == "result:ok"
    with pytest.raises(LeaseLostError):
        recovered.complete(task_id, owner="worker-b", result_ref="result:duplicate")


def test_attempts_cap_ends_in_failed_with_error_code(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    task_type = _unique_type()
    created = client.post(
        TASKS_ENDPOINT,
        json={"type": task_type, "payload_ref": "conversation:cap"},
        headers=_headers(users["agent_a"], f"idem-{uuid4().hex}"),
    )
    task_id = UUID(created.json()["task_id"])

    start = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    clock = Clock(start)
    queue = TaskQueue(now=clock, max_attempts=2)

    first = queue.claim_next(task_type, 5, owner="worker-a")
    assert first is not None and first.attempts == 1

    clock.now = start + timedelta(seconds=6)
    second = queue.claim_next(task_type, 5, owner="worker-a")
    assert second is not None and second.attempts == 2

    clock.now = start + timedelta(seconds=12)
    assert queue.claim_next(task_type, 5, owner="worker-a") is None

    with session_scope() as session:
        stored = session.get(Task, task_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.error_code == MAX_ATTEMPTS_ERROR_CODE
        assert stored.attempts == 2


def test_retryable_failure_requeues_until_attempt_cap(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    task_type = _unique_type()
    created = client.post(
        TASKS_ENDPOINT,
        json={"type": task_type, "payload_ref": "conversation:retry"},
        headers=_headers(users["agent_a"], f"idem-{uuid4().hex}"),
    )
    task_id = UUID(created.json()["task_id"])
    queue = TaskQueue(max_attempts=2)

    first = queue.claim_next(task_type, 30, owner="worker-a")
    assert first is not None
    requeued = queue.fail(task_id, owner="worker-a", error_code="MODEL_TIMEOUT")
    assert requeued.status == "queued"
    assert requeued.error_code == "MODEL_TIMEOUT"
    assert requeued.lease_owner is None

    second = queue.claim_next(task_type, 30, owner="worker-b")
    assert second is not None and second.attempts == 2
    finished = queue.fail(task_id, owner="worker-b", error_code="MODEL_TIMEOUT")
    assert finished.status == "failed"
    assert finished.error_code == "MODEL_TIMEOUT"
    assert queue.claim_next(task_type, 30, owner="worker-b") is None


def test_non_retryable_failure_fails_immediately() -> None:
    now = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    with session_scope() as session:
        task = Task(type=_unique_type(), payload_ref=None, scope_id=SCOPE_A)
        session.add(task)
        session.flush()
        task_id = task.task_id
        task_type = task.type

    clocked = TaskQueue(now=Clock(now))
    claimed = clocked.claim_next(task_type, 30, owner="worker-a")
    assert claimed is not None
    failed = clocked.fail(
        task_id, owner="worker-a", error_code="INVALID_INPUT", retryable=False
    )
    assert failed.status == "failed"
    assert failed.error_code == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# 迁移与表结构
# ---------------------------------------------------------------------------


def test_tasks_and_idempotency_tables_match_the_contract_minimum() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"tasks", "idempotency_keys"} <= tables

    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    assert {
        "task_id",
        "type",
        "status",
        "idempotency_key",
        "payload_ref",
        "lease_owner",
        "lease_expires_at",
        "attempts",
        "result_ref",
        "error_code",
        "created_at",
        "updated_at",
    } <= task_columns

    key_columns = {
        column["name"] for column in inspector.get_columns("idempotency_keys")
    }
    assert {
        "user_id",
        "endpoint",
        "idempotency_key",
        "request_hash",
        "resource_id",
        "response_snapshot",
    } <= key_columns


def test_fresh_database_head_contains_the_tasks_revision() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    revisions = {revision.revision for revision in script.walk_revisions()}

    assert "0005_tasks" in revisions
    assert script.get_revision("0005_tasks").down_revision == "0002_users"
    # 当前库已迁移到脚本 head；迁移链在本分支内是可达的（Lead 的合并 revision 只增不减）。
    with session_scope() as session:
        current = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert current in revisions
    assert current == script.get_current_head()
