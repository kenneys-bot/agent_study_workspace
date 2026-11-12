"""质检任务执行的集成测试：真实 PostgreSQL + 真实空库迁移（T18，#19；M4-03）。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 起停一次性
PostgreSQL 18，并在空库执行 ``alembic upgrade head``；本文件只假设迁移已完成。

seam（真实 HTTP + 真实数据库 + 真实租约队列）：
- ``POST /api/v1/inspections``：202 + inspection_id/task_id、幂等键、跨范围 404；
- ``GET /api/v1/inspections/{id}``：状态、逐规则判断、证据、失败原因；
- ``bank_assistant.worker.Worker``：领取租约任务、按规则快照执行、幂等收尾；
- 数据库行：findings 不可覆盖、``(inspection_id, rule_version_id)`` 唯一。

模型用确定性假网关（不联网、不产生真实模型调用）；其余全部走真实实现。
覆盖 CT-05/CT-06（幂等）、CT-11（租约恢复、无重复最终结果）与
「失败任务有 error_code」「机器判断不静默为空」。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from bank_assistant.config.settings import settings
from bank_assistant.core.model_gateway import (
    ModelCallStatus,
    ModelProvider,
    ModelResult,
)
from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import (
    Finding,
    InspectionRun,
    RuleSet,
    RuleVersion,
    Task,
    User,
)
from bank_assistant.infra.task_queue import MAX_ATTEMPTS_ERROR_CODE, TaskQueue
from bank_assistant.inspections.evidence import (
    code_point_length,
    utf16_offset_to_code_point,
)
from bank_assistant.inspections.schema import InspectionStatus, RuleJudgmentPayload
from bank_assistant.inspections.service import TASK_TYPE
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token
from bank_assistant.worker import Worker

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SUFFIX = uuid4().hex[:8]
SCOPE_A = f"inspection-a-{SUFFIX}"
SCOPE_B = f"inspection-b-{SUFFIX}"
DEMO_PASSWORD = "demo-only-correct-horse-battery"
INSPECTIONS_ENDPOINT = "/api/v1/inspections"
CONVERSATION_TEXT = (
    "客户：你们必须把年费免了，不然我投诉\n"
    "客服：肯定免息，您放心，我帮您处理\n"
)
EMOJI_CONVERSATION_TEXT = (
    "客户：请问年费\n"
    "客服：这笔年费我给您免了😀\n"
    "不用还\n"
)


class Clock:
    """可推进的测试时钟：租约过期不依赖 sleep。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeGateway:
    """确定性模型网关：按提示词里的 rule_code 返回预设判断，并记录调用。"""

    mode = "replay"

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def structured(self, messages, schema, **kwargs):  # noqa: ANN001, ARG002
        payload = json.loads(messages[-1]["content"].split("\n", 1)[1])
        rule_code = payload["rule"]["rule_code"]
        self.calls.append({"messages": list(messages), "rule_code": rule_code})
        response = self.responses[rule_code]
        if isinstance(response, ModelResult):
            return response
        if isinstance(response, str):
            # 模拟模型输出非 JSON：ModelGateway 会归类为 INVALID_STRUCTURE。
            return ModelResult(
                status=ModelCallStatus.INVALID_STRUCTURE,
                error=response,
                mode="replay",
            )
        return ModelResult(
            status=ModelCallStatus.SUCCEEDED,
            provider=ModelProvider.DEEPSEEK,
            model="fake-inspection-model",
            mode="replay",
            text=json.dumps(response, ensure_ascii=False),
            data=schema.model_validate(response),
        )


@pytest.fixture(scope="module")
def users() -> dict[str, dict[str, object]]:
    suffix = uuid4().hex[:8]
    specs = {
        "agent_a": (f"insp-a-{suffix}", SCOPE_A),
        "agent_b": (f"insp-b-{suffix}", SCOPE_B),
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


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


def _headers(user: dict[str, object], key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {user['token']}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _create_conversation_with_text(
    client: TestClient, user: dict[str, object], text: str
) -> dict[str, object]:
    response = client.post(
        "/api/v1/conversations",
        json={"content": text, "format": "text"},
        headers=_headers(user),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_conversation(
    client: TestClient, user: dict[str, object]
) -> dict[str, object]:
    return _create_conversation_with_text(client, user, CONVERSATION_TEXT)


def _create_published_rule(
    *,
    scope_id: str,
    author_id: UUID,
    rule_code: str,
    business_type: str = "lost_card",
    severity: str = "high",
    status: str = "published",
) -> UUID:
    """直接写一条规则版本（发布态），避免每个测试都重走完整审核流程。"""
    now = datetime.now(UTC)
    with session_scope() as session:
        rule = RuleSet(
            rule_id=uuid4(),
            rule_code=rule_code,
            title=f"{rule_code} 演示规则",
            category="fee_waiver",
            description="虚构演示制度：用于集成测试的规则身份",
            scope_id=scope_id,
            demo_only=True,
        )
        session.add(rule)
        session.flush()
        version = RuleVersion(
            rule_version_id=uuid4(),
            rule_id=rule.rule_id,
            version_no=1,
            business_type=business_type,
            applicability={
                "business_types": [business_type],
                "channels": ["phone"],
                "conditions": ["客户提出费用减免诉求"],
            },
            criteria={
                "evidence_requirements": ["定位坐席答复原文"],
                "decision_rules": ["否定表述不命中"],
            },
            exceptions=["坐席说明需审批"],
            severity=severity,
            source_refs=[
                {"kind": "demo_rule_set", "ref": "https://demo.invalid/rules/test"}
            ],
            examples={},
            content_hash=hashlib.sha256(
                f"{scope_id}:{rule_code}".encode("utf-8")
            ).hexdigest(),
            status=status,
            demo_only=True,
            valid_from=now - timedelta(days=1),
            valid_to=None,
            author_id=author_id,
        )
        session.add(version)
        session.flush()
        return version.rule_version_id


def _judgment(
    turn_id: UUID,
    *,
    verdict: str = "hit",
    start: int = 7,
    end: int = 9,
    quote: str = "免了",
    evidence: list[dict[str, object]] | None = None,
    applicability: dict[str, object] | None = None,
) -> dict[str, object]:
    """构造模型输出；默认证据对应首轮“你们必须把年费免了”。"""
    payload: dict[str, object] = {
        "verdict": verdict,
        "rationale": "坐席承诺减免费用且没有审批依据",
        "evidence": (
            evidence
            if evidence is not None
            else [{"turn_id": str(turn_id), "start": start, "end": end, "quote": quote}]
        ),
        "knowledge_refs": [],
    }
    if applicability is not None:
        payload["applicability"] = applicability
    return payload


def _unique_business_type() -> str:
    """每个测试独享业务类型，避免同范围其他测试的已发布规则混入快照。"""
    return f"bt-{uuid4().hex[:10]}"


def _post_inspection(
    client: TestClient,
    user: dict[str, object],
    conversation_id: str,
    *,
    business_type: str | None = "lost_card",
    rule_ids: list[str] | None = None,
    key: str | None = None,
):
    body: dict[str, object] = {"conversation_id": conversation_id}
    if business_type is not None:
        body["business_type"] = business_type
    if rule_ids is not None:
        body["rule_ids"] = rule_ids
    return client.post(
        INSPECTIONS_ENDPOINT,
        json=body,
        headers=_headers(user, key or f"idem-{uuid4().hex}"),
    )


def _cancel_pending_inspections() -> None:
    """测试隔离：取消此前 API 测试遗留的 queued/running 质检，避免 worker 领错任务。"""
    now = datetime.now(UTC)
    with session_scope() as session:
        runs = session.scalars(
            select(InspectionRun).where(
                InspectionRun.status.in_(["queued", "running"])
            )
        ).all()
        for run in runs:
            run.status = InspectionStatus.CANCELLED.value
            run.error_code = "TEST_CLEANUP"
            run.finished_at = now
            run.updated_at = now
            if run.task_id is not None:
                task = session.get(Task, run.task_id)
                if task is not None and task.status in ("queued", "running"):
                    task.status = InspectionStatus.CANCELLED.value
                    task.lease_owner = None
                    task.lease_expires_at = None


def _stored_run(inspection_id: str) -> InspectionRun:
    with session_scope() as session:
        run = session.get(InspectionRun, UUID(inspection_id))
        assert run is not None
        return run


def _finding_count(inspection_id: str) -> int:
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Finding)
                .where(Finding.inspection_id == UUID(inspection_id))
            )
            or 0
        )


# ---------------------------------------------------------------------------
# 表结构与迁移
# ---------------------------------------------------------------------------


def test_inspection_tables_match_the_contract_minimum() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"inspection_runs", "findings"} <= tables

    run_columns = {
        column["name"] for column in inspector.get_columns("inspection_runs")
    }
    assert {
        "inspection_id",
        "conversation_id",
        "conversation_hash",
        "rule_snapshot",
        "run_id",
        "status",
        "error_code",
        "started_at",
        "finished_at",
        "parent_run_id",
        "scope_id",
    } <= run_columns

    finding_columns = {column["name"] for column in inspector.get_columns("findings")}
    assert {
        "finding_id",
        "inspection_id",
        "rule_version_id",
        "machine_verdict",
        "severity",
        "rationale",
        "evidence",
        "knowledge_refs",
    } <= finding_columns


def test_inspections_revision_is_the_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    assert script.get_current_head() == "0009_inspections"
    with session_scope() as session:
        current = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert current == "0009_inspections"


# ---------------------------------------------------------------------------
# 创建质检：202 + 幂等 + 跨范围 404
# ---------------------------------------------------------------------------


def test_post_inspection_returns_202_with_ids_and_queued_state(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    conversation = _create_conversation(client, users["agent_a"])
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=f"DEMO-T18-{uuid4().hex[:6].upper()}",
        business_type=business_type,
    )

    response = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["inspection_id"] and body["task_id"]
    assert body["status"] == "queued"
    assert body["rule_count"] == 1
    assert body["conversation_id"] == conversation["conversation_id"]

    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{body['inspection_id']}",
        headers=_headers(users["agent_a"]),
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "queued"
    assert detail.json()["findings"] == []
    assert detail.json()["rule_count"] == 1

    task = client.get(
        f"/api/v1/tasks/{body['task_id']}", headers=_headers(users["agent_a"])
    )
    assert task.status_code == 200
    assert task.json()["type"] == TASK_TYPE
    assert task.json()["payload_ref"] == f"inspection:{body['inspection_id']}"
    assert task.json()["status"] == "queued"


def test_same_key_and_input_replays_the_same_inspection(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    conversation = _create_conversation(client, users["agent_a"])
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=f"DEMO-IDEM-{uuid4().hex[:6].upper()}",
        business_type=business_type,
    )
    key = f"idem-{uuid4().hex}"

    first = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
        key=key,
    )
    second = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
        key=key,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.headers.get("idempotent-replayed") == "true"
    assert second.json() == first.json()
    assert second.json()["inspection_id"] == first.json()["inspection_id"]
    assert second.json()["task_id"] == first.json()["task_id"]

    with session_scope() as session:
        bindings = session.execute(
            text(
                "SELECT count(*) FROM inspection_runs WHERE inspection_id = :id"
            ),
            {"id": first.json()["inspection_id"]},
        ).scalar_one()
        tasks = session.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.task_id == UUID(first.json()["task_id"]))
        ).scalar_one()
    assert bindings == 1
    assert tasks == 1


def test_same_key_with_different_input_is_409(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    first_conversation = _create_conversation(client, users["agent_a"])
    second_conversation = _create_conversation(client, users["agent_a"])
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=f"DEMO-CONFLICT-{uuid4().hex[:6].upper()}",
        business_type=business_type,
    )
    key = f"idem-{uuid4().hex}"

    first = _post_inspection(
        client,
        users["agent_a"],
        first_conversation["conversation_id"],
        business_type=business_type,
        key=key,
    )
    conflict = _post_inspection(
        client,
        users["agent_a"],
        second_conversation["conversation_id"],
        business_type=business_type,
        key=key,
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert conflict.json()["retryable"] is False


def test_cross_scope_reads_and_creates_are_404(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    conversation = _create_conversation(client, users["agent_a"])
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=f"DEMO-SCOPE-{uuid4().hex[:6].upper()}",
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]

    foreign = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}", headers=_headers(users["agent_b"])
    )
    missing = client.get(
        f"{INSPECTIONS_ENDPOINT}/{uuid4()}", headers=_headers(users["agent_b"])
    )
    foreign_create = _post_inspection(
        client,
        users["agent_b"],
        conversation["conversation_id"],
        business_type=business_type,
    )

    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    assert foreign.json()["details"] == missing.json()["details"] == {}
    assert foreign_create.status_code == 404
    assert foreign_create.json()["code"] == "NOT_FOUND"


def test_snapshot_only_uses_published_applicable_rules_in_scope(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    conversation = _create_conversation(client, users["agent_a"])
    author_id = users["agent_a"]["user_id"]
    business_type = _unique_business_type()
    published_one = _create_published_rule(
        scope_id=SCOPE_A,
        author_id=author_id,  # type: ignore[arg-type]
        rule_code=f"DEMO-SNAP-A-{uuid4().hex[:5].upper()}",
        business_type=business_type,
    )
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=author_id,  # type: ignore[arg-type]
        rule_code=f"DEMO-SNAP-B-{uuid4().hex[:5].upper()}",
        business_type=business_type,
    )
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=author_id,  # type: ignore[arg-type]
        rule_code=f"DEMO-SNAP-DRAFT-{uuid4().hex[:5].upper()}",
        business_type=business_type,
        status="draft",
    )
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=author_id,  # type: ignore[arg-type]
        rule_code=f"DEMO-SNAP-OTHER-{uuid4().hex[:5].upper()}",
        business_type="complaint",
    )
    # 其他范围已发布的规则绝不能进入本次快照。
    _create_published_rule(
        scope_id=SCOPE_B,
        author_id=users["agent_b"]["user_id"],  # type: ignore[index]
        rule_code=f"DEMO-SNAP-FOREIGN-{uuid4().hex[:5].upper()}",
        business_type=business_type,
    )

    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    assert created.status_code == 202, created.text
    assert created.json()["rule_count"] == 2

    run = _stored_run(created.json()["inspection_id"])
    snapshot = run.rule_snapshot
    assert snapshot["scope_id"] == SCOPE_A
    assert snapshot["business_type"] == business_type
    version_ids = {rule["rule_version_id"] for rule in snapshot["rules"]}
    assert str(published_one) in version_ids
    assert len(snapshot["rules"]) == 2

    # 规则范围过滤：只保留指定 rule_id。
    rule_id = snapshot["rules"][0]["rule_id"]
    filtered = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
        rule_ids=[rule_id],
    )
    assert filtered.status_code == 202
    assert filtered.json()["rule_count"] == 1

    # 没有适用规则时显式 422，不创建空跑质检。
    no_rules = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type="complaint_not_configured",
    )
    assert no_rules.status_code == 422
    assert no_rules.json()["code"] == "NO_APPLICABLE_RULES"


# ---------------------------------------------------------------------------
# worker 成功路径
# ---------------------------------------------------------------------------


def test_worker_success_writes_one_verdict_per_applicable_rule(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    conversation = _create_conversation(client, users["agent_a"])
    author_id = users["agent_a"]["user_id"]
    code_one = f"DEMO-W1-{uuid4().hex[:5].upper()}"
    code_two = f"DEMO-W2-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    version_one = _create_published_rule(
        scope_id=SCOPE_A,
        author_id=author_id,  # type: ignore[arg-type]
        rule_code=code_one,
        business_type=business_type,
        severity="high",
    )
    version_two = _create_published_rule(
        scope_id=SCOPE_A,
        author_id=author_id,  # type: ignore[arg-type]
        rule_code=code_two,
        business_type=business_type,
        severity="medium",
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    assert created.status_code == 202, created.text
    inspection_id = created.json()["inspection_id"]
    turn_id = UUID(conversation["turns"][0]["turn_id"])

    gateway = FakeGateway(
        {
            code_one: _judgment(turn_id, verdict="hit"),
            code_two: _judgment(turn_id, verdict="insufficient_evidence")
            | {"evidence": []},
        }
    )
    worker = Worker(gateway=gateway, worker_id="worker-success")

    outcome = worker.run_once()

    assert outcome is not None
    assert outcome.inspection_id == UUID(inspection_id)
    assert outcome.status == InspectionStatus.SUCCEEDED.value
    assert outcome.findings_count == 2
    # 每条适用规则恰好调用一次模型；提示词包含会话轮次。
    assert {call["rule_code"] for call in gateway.calls} == {code_one, code_two}
    first_call = gateway.calls[0]["messages"]
    assert first_call[0]["role"] == "system"
    assert conversation["turns"][0]["content"] in first_call[1]["content"]

    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "succeeded"
    assert body["error_code"] is None
    assert body["started_at"] and body["finished_at"]
    assert body["rule_count"] == 2
    assert len(body["findings"]) == 2
    by_code = {finding["rule_code"]: finding for finding in body["findings"]}
    assert by_code[code_one]["machine_verdict"] == "hit"
    assert by_code[code_one]["severity"] == "high"
    assert by_code[code_one]["evidence"][0]["turn_id"] == str(turn_id)
    assert by_code[code_one]["rationale"]
    assert by_code[code_two]["machine_verdict"] == "insufficient_evidence"
    assert by_code[code_two]["severity"] == "medium"
    # rule_version_id 来自快照/规则版本，不是模型自报。
    assert {finding["rule_version_id"] for finding in body["findings"]} == {
        str(version_one),
        str(version_two),
    }

    task = client.get(
        f"/api/v1/tasks/{created.json()['task_id']}",
        headers=_headers(users["agent_a"]),
    )
    assert task.json()["status"] == "succeeded"
    assert task.json()["result_ref"] == f"inspection:{inspection_id}"
    assert task.json()["attempts"] == 1


def test_duplicate_task_delivery_does_not_duplicate_findings(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    conversation = _create_conversation(client, users["agent_a"])
    code = f"DEMO-DUP-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    turn_id = UUID(conversation["turns"][0]["turn_id"])
    gateway = FakeGateway({code: _judgment(turn_id)})

    first = Worker(gateway=gateway, worker_id="worker-dup-1").run_once()
    assert first is not None and first.status == "succeeded"
    assert _finding_count(inspection_id) == 1

    # 重复投递：同一个 payload_ref 再建一个任务，模拟消息队列至少一次语义。
    with session_scope() as session:
        duplicate = Task(
            task_id=uuid4(),
            type=TASK_TYPE,
            status="queued",
            payload_ref=f"inspection:{inspection_id}",
            scope_id=SCOPE_A,
        )
        session.add(duplicate)
        session.flush()
        duplicate_id = duplicate.task_id

    second = Worker(gateway=gateway, worker_id="worker-dup-2").run_once()

    assert second is not None
    assert second.inspection_id == UUID(inspection_id)
    assert second.status == "succeeded"
    # 运行已终态：不重跑模型、不追加 findings。
    assert _finding_count(inspection_id) == 1
    with session_scope() as session:
        stored = session.get(Task, duplicate_id)
        assert stored is not None
        assert stored.status == "succeeded"

    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    )
    assert detail.json()["status"] == "succeeded"
    assert len(detail.json()["findings"]) == 1


def test_findings_and_final_state_are_immutable_in_the_database(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    """findings 不能 UPDATE；同一 (inspection, rule_version) 不能有第二行。"""
    from bank_assistant.infra.models import FindingImmutable

    conversation = _create_conversation(client, users["agent_a"])
    code = f"DEMO-IMMUT-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = UUID(created.json()["inspection_id"])
    Worker(
        gateway=FakeGateway(
            {code: _judgment(UUID(conversation["turns"][0]["turn_id"]))}
        ),
        worker_id="worker-immutability",
    ).run_once()

    with session_scope() as session:
        finding = session.scalars(
            select(Finding).where(Finding.inspection_id == inspection_id)
        ).one()
        finding_id = finding.finding_id
        rule_version_id = finding.rule_version_id

    with pytest.raises(FindingImmutable):
        with session_scope() as session:
            stored = session.get(Finding, finding_id)
            assert stored is not None
            stored.machine_verdict = "hit"

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(
                Finding(
                    finding_id=uuid4(),
                    inspection_id=inspection_id,
                    rule_version_id=rule_version_id,
                    machine_verdict="hit",
                    severity="high",
                    rationale="重复判断",
                    evidence=[],
                    knowledge_refs=[],
                )
            )


# ---------------------------------------------------------------------------
# 失败路径：显式 error_code，绝不静默空 findings
# ---------------------------------------------------------------------------


def test_invalid_model_output_retries_then_fails_with_error_code(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    conversation = _create_conversation(client, users["agent_a"])
    code = f"DEMO-BAD-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    gateway = FakeGateway({code: "这不是 JSON"})
    worker = Worker(gateway=gateway, worker_id="worker-bad-output")

    first = worker.run_once()
    assert first is not None
    assert first.status == "queued"  # 第一次失败：重排队，运行保留诊断
    run = _stored_run(inspection_id)
    assert run.status == "running"
    assert run.error_code == "INVALID_MODEL_OUTPUT"
    assert run.diagnostics["rule_code"] == code

    # 默认重试上限 3：第 3 次失败后任务与运行都终结为 failed。
    assert settings.TASK_MAX_ATTEMPTS == 3
    for _ in range(2):
        outcome = worker.run_once()
        assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_code == "INVALID_MODEL_OUTPUT"
    assert outcome.attempts == 3

    run = _stored_run(inspection_id)
    assert run.status == "failed"
    assert run.error_code == "INVALID_MODEL_OUTPUT"
    assert run.finished_at is not None
    assert run.diagnostics["rule_code"] == code
    # 失败时绝不写入空 findings 冒充“无问题”。
    assert _finding_count(inspection_id) == 0

    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    )
    body = detail.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "INVALID_MODEL_OUTPUT"
    assert body["diagnostics"]["rule_code"] == code
    assert body["findings"] == []
    with session_scope() as session:
        stored = session.get(Task, UUID(created.json()["task_id"]))
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error_code == "INVALID_MODEL_OUTPUT"


def test_equivalent_invalid_model_output_never_becomes_empty_findings(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    """模型 schema 合法但判断非法（hit 无证据）→ EVIDENCE_INVALID，不写 findings。"""
    conversation = _create_conversation(client, users["agent_a"])
    code = f"DEMO-NOEV-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    gateway = FakeGateway(
        {code: {"verdict": "hit", "rationale": "命中", "evidence": []}}
    )
    worker = Worker(
        gateway=gateway,
        queue=TaskQueue(max_attempts=1),
        worker_id="worker-bad-evidence",
    )

    outcome = worker.run_once()

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_code == "EVIDENCE_INVALID"
    run = _stored_run(inspection_id)
    assert run.status == "failed"
    assert run.error_code == "EVIDENCE_INVALID"
    assert _finding_count(inspection_id) == 0


# ---------------------------------------------------------------------------
# CT-11：租约恢复与对账
# ---------------------------------------------------------------------------


def test_killed_worker_lease_expires_and_exactly_one_final_result(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    conversation = _create_conversation(client, users["agent_a"])
    code = f"DEMO-CT11-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    task_id = UUID(created.json()["task_id"])
    turn_id = UUID(conversation["turns"][0]["turn_id"])
    start = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    clock = Clock(start)

    # worker A 领到任务后“崩溃”：只写 running，不写任何最终结果。
    crashed_queue = TaskQueue(now=clock)
    claimed = crashed_queue.claim_next(TASK_TYPE, 10, owner="worker-crashed")
    assert claimed is not None and claimed.task_id == task_id

    # 租约到期：worker B 重新领取并完成质检。
    clock.now = start + timedelta(seconds=11)
    recovered_queue = TaskQueue(now=clock)
    gateway = FakeGateway({code: _judgment(turn_id)})
    recovered = Worker(
        gateway=gateway,
        queue=recovered_queue,
        worker_id="worker-recovered",
        lease_seconds=10,
        now=clock,
    )
    outcome = recovered.run_once()

    assert outcome is not None
    assert outcome.status == "succeeded"
    assert outcome.findings_count == 1
    run = _stored_run(inspection_id)
    assert run.status == "succeeded"
    assert run.error_code is None
    with session_scope() as session:
        stored_task = session.get(Task, task_id)
    assert stored_task is not None
    assert stored_task.attempts == 2
    assert stored_task.status == "succeeded"
    # 恰好一份最终结果：旧 worker 不会补写。
    assert _finding_count(inspection_id) == 1

    # 旧 worker 恢复后拿到的是过期租约：不能覆盖最终结果。
    stale = Worker(
        gateway=gateway,
        queue=crashed_queue,
        worker_id="worker-crashed",
        lease_seconds=10,
        now=clock,
    ).runner
    stale_outcome = stale.process(
        claimed, owner="worker-crashed", queue=crashed_queue
    )
    # 已完成的同一结果重放是幂等的（queue.complete 直接返回原任务）；
    # 无论如何都不会写第二份 findings，也不会改写最终状态。
    assert stale_outcome.status in {"succeeded", "lease_lost"}
    assert _finding_count(inspection_id) == 1
    assert _stored_run(inspection_id).status == "succeeded"


def test_exhausted_attempts_are_reconciled_from_running_to_failed(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    """worker 在最后一次尝试也崩溃：sweep 终结任务后对账把运行收口为 failed。"""
    conversation = _create_conversation(client, users["agent_a"])
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=f"DEMO-CRASH-{uuid4().hex[:5].upper()}",
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    start = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    clock = Clock(start)

    for attempt in range(3):
        clock.now = start + timedelta(seconds=11 * attempt)
        claimed = TaskQueue(now=clock).claim_next(
            TASK_TYPE, 10, owner=f"crashed-{attempt}"
        )
        assert claimed is not None
        assert claimed.attempts == attempt + 1

    # 第 4 次领取触发 sweep：达到上限的任务被终结为 failed，运行仍 running。
    clock.now = start + timedelta(seconds=40)
    worker = Worker(
        gateway=FakeGateway({}),
        queue=TaskQueue(now=clock),
        worker_id="worker-reconcile",
        lease_seconds=10,
        now=clock,
    )
    assert worker.run_once() is None
    assert worker.reconcile() == 0  # run_once 已在空队列时完成对账

    run = _stored_run(inspection_id)
    assert run.status == "failed"
    assert run.error_code == MAX_ATTEMPTS_ERROR_CODE
    assert run.finished_at is not None
    assert _finding_count(inspection_id) == 0


# ---------------------------------------------------------------------------
# 并发：两个 worker 只有一个领取到任务
# ---------------------------------------------------------------------------


def test_two_workers_claim_and_process_inspection_exactly_once(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    conversation = _create_conversation(client, users["agent_a"])
    code = f"DEMO-RACE-{uuid4().hex[:5].upper()}"
    business_type = _unique_business_type()
    _create_published_rule(
        scope_id=SCOPE_A,
        author_id=users["agent_a"]["user_id"],  # type: ignore[index]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    turn_id = UUID(conversation["turns"][0]["turn_id"])
    barrier = Barrier(2)

    def run(worker_id: str):
        barrier.wait(timeout=10)
        worker = Worker(
            gateway=FakeGateway({code: _judgment(turn_id)}),
            worker_id=worker_id,
            lease_seconds=30,
        )
        return worker.run_once()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, ["worker-race-a", "worker-race-b"]))

    processed = [outcome for outcome in outcomes if outcome is not None]
    assert len(processed) == 1
    assert processed[0].status == "succeeded"
    assert _finding_count(inspection_id) == 1
    run_row = _stored_run(inspection_id)
    assert run_row.status == "succeeded"
    with session_scope() as session:
        task = session.get(Task, UUID(created.json()["task_id"]))
    assert task is not None
    assert task.status == "succeeded"
    assert task.attempts == 1


def test_judgment_payload_schema_rejects_unknown_verdict() -> None:
    with pytest.raises(ValidationError):
        RuleJudgmentPayload.model_validate(
            {"verdict": "probably", "rationale": "x", "evidence": []}
        )


# ---------------------------------------------------------------------------
# T19：严格证据、四态独立与证据展示
# ---------------------------------------------------------------------------


def _single_rule_inspection(
    client: TestClient,
    user: dict[str, object],
    *,
    text: str = CONVERSATION_TEXT,
):
    """建一个会话 + 一条已发布规则 + 一次质检，返回构造好的输入。"""
    conversation = _create_conversation_with_text(client, user, text)
    business_type = _unique_business_type()
    code = f"DEMO-T19-{uuid4().hex[:6].upper()}"
    _create_published_rule(
        scope_id=str(user["scope_id"]),
        author_id=user["user_id"],  # type: ignore[arg-type]
        rule_code=code,
        business_type=business_type,
    )
    created = _post_inspection(
        client,
        user,
        conversation["conversation_id"],
        business_type=business_type,
    )
    assert created.status_code == 202, created.text
    return conversation, code, created.json()["inspection_id"]


@pytest.mark.parametrize(
    "start,end,quote",
    [
        (7, 999, "免了"),  # end 越过原文码点长度
        (7, 9, "我给您"),  # quote 与原文不一致
    ],
)
def test_worker_rejects_invalid_evidence_interval_with_stable_code(
    client: TestClient,
    users: dict[str, dict[str, object]],
    start: int,
    end: int,
    quote: str,
) -> None:
    """CT-09：错误证据区间必须被拒绝，且不能留下任何 hit 结论。"""
    _cancel_pending_inspections()
    conversation, code, inspection_id = _single_rule_inspection(
        client, users["agent_a"]
    )
    turn_id = UUID(conversation["turns"][0]["turn_id"])
    gateway = FakeGateway(
        {code: _judgment(turn_id, start=start, end=end, quote=quote)}
    )

    outcome = Worker(
        gateway=gateway,
        queue=TaskQueue(max_attempts=1),
        worker_id="worker-invalid-evidence",
    ).run_once()

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_code == "EVIDENCE_INVALID"
    assert _finding_count(inspection_id) == 0
    run = _stored_run(inspection_id)
    assert run.status == "failed"
    assert run.error_code == "EVIDENCE_INVALID"
    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    )
    assert detail.json()["status"] == "failed"
    assert detail.json()["findings"] == []


def test_worker_rejects_knowledge_refs_that_do_not_exist(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """CT-09：引用不存在的 chunk/version 同样不能生成确认风险。"""
    _cancel_pending_inspections()
    conversation, code, inspection_id = _single_rule_inspection(
        client, users["agent_a"]
    )
    turn_id = UUID(conversation["turns"][0]["turn_id"])
    payload = _judgment(turn_id) | {
        "knowledge_refs": [
            {"version_id": str(uuid4()), "chunk_id": str(uuid4())}
        ]
    }
    gateway = FakeGateway({code: payload})

    outcome = Worker(
        gateway=gateway,
        queue=TaskQueue(max_attempts=1),
        worker_id="worker-unknown-knowledge",
    ).run_once()

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_code == "EVIDENCE_INVALID"
    assert _finding_count(inspection_id) == 0


def test_worker_rejects_a_hit_whose_evidence_is_negated(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """M4-02：否定句里的关键词不能成为命中证据。"""
    _cancel_pending_inspections()
    conversation, code, inspection_id = _single_rule_inspection(
        client,
        users["agent_a"],
        text="客户：年费\n客服：我们没有收取年费\n",
    )
    turn_id = UUID(conversation["turns"][1]["turn_id"])
    assert conversation["turns"][1]["content"] == "我们没有收取年费"
    gateway = FakeGateway(
        {
            code: _judgment(
                turn_id, start=2, end=8, quote="没有收取年费"
            )
        }
    )

    outcome = Worker(
        gateway=gateway,
        queue=TaskQueue(max_attempts=1),
        worker_id="worker-negated-evidence",
    ).run_once()

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_code == "EVIDENCE_INVALID"
    assert _finding_count(inspection_id) == 0


def test_emoji_and_newline_evidence_round_trips_and_get_exposes_ordinal(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """CT-08：中文 + emoji（代理对）+ 换行按码点定位，前后端换算一致。"""
    _cancel_pending_inspections()
    conversation, code, inspection_id = _single_rule_inspection(
        client, users["agent_a"], text=EMOJI_CONVERSATION_TEXT
    )
    turn = conversation["turns"][1]
    turn_id = UUID(turn["turn_id"])
    content = turn["content"]  # "这笔年费我给您免了😀\n不用还"
    assert turn["ordinal"] == 2
    assert content == "这笔年费我给您免了😀\n不用还"
    assert code_point_length(content) == 14
    # 码点 end=13 对应 JS 的 UTF-16 下标 14（emoji 占两个 code unit）。
    assert utf16_offset_to_code_point(content, 14) == 13

    gateway = FakeGateway(
        {code: _judgment(turn_id, start=7, end=13, quote="免了😀\n不用")}
    )
    outcome = Worker(gateway=gateway, worker_id="worker-emoji").run_once()
    assert outcome is not None and outcome.status == "succeeded"

    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    )
    assert detail.status_code == 200
    finding = detail.json()["findings"][0]
    assert finding["machine_verdict"] == "hit"
    assert finding["evidence_valid"] is True
    assert finding["evidence"][0]["turn_id"] == str(turn_id)
    assert finding["evidence"][0]["turn_ordinal"] == 2
    assert finding["evidence"][0]["quote"] == "免了😀\n不用"
    assert finding["evidence"][0]["validated"] is True


def test_get_never_exposes_an_invalid_hit_as_hit(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """读取路径重新回验：被篡改/历史坏证据的 hit 不能以 hit 展示。"""
    _cancel_pending_inspections()
    conversation, code, inspection_id = _single_rule_inspection(
        client, users["agent_a"]
    )
    turn_id = UUID(conversation["turns"][0]["turn_id"])
    Worker(
        gateway=FakeGateway({code: _judgment(turn_id)}),
        worker_id="worker-tamper-source",
    ).run_once()

    # 绕过 ORM 不可变事件，模拟历史坏数据/库内篡改。
    with session_scope() as session:
        session.execute(
            update(Finding)
            .where(Finding.inspection_id == UUID(inspection_id))
            .values(
                evidence=[
                    {
                        "turn_id": str(turn_id),
                        "start": 7,
                        "end": 9,
                        "quote": "我给您",
                    }
                ]
            )
        )

    detail = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    )
    assert detail.status_code == 200
    finding = detail.json()["findings"][0]
    assert finding["machine_verdict"] == "insufficient_evidence"
    assert finding["raw_machine_verdict"] == "hit"
    assert finding["evidence_valid"] is False
    assert finding["evidence"][0]["validated"] is False
    assert finding["evidence"][0]["validation_error"]


def test_four_verdict_states_are_persisted_and_returned_distinctly(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    _cancel_pending_inspections()
    conversation = _create_conversation(client, users["agent_a"])
    author_id = users["agent_a"]["user_id"]
    business_type = _unique_business_type()
    codes = {
        "hit": f"DEMO-T19-H-{uuid4().hex[:5].upper()}",
        "clear": f"DEMO-T19-C-{uuid4().hex[:5].upper()}",
        "insufficient_evidence": f"DEMO-T19-I-{uuid4().hex[:5].upper()}",
        "not_applicable": f"DEMO-T19-N-{uuid4().hex[:5].upper()}",
    }
    for code in codes.values():
        _create_published_rule(
            scope_id=SCOPE_A,
            author_id=author_id,  # type: ignore[arg-type]
            rule_code=code,
            business_type=business_type,
        )
    created = _post_inspection(
        client,
        users["agent_a"],
        conversation["conversation_id"],
        business_type=business_type,
    )
    inspection_id = created.json()["inspection_id"]
    turn_id = UUID(conversation["turns"][0]["turn_id"])

    responses = {
        codes["hit"]: _judgment(turn_id),
        codes["clear"]: _judgment(turn_id, verdict="clear", evidence=[]),
        codes["insufficient_evidence"]: _judgment(
            turn_id, verdict="insufficient_evidence", evidence=[]
        ),
        codes["not_applicable"]: _judgment(
            turn_id,
            verdict="not_applicable",
            evidence=[],
            applicability={
                "applicable": False,
                "dimension": "condition",
                "item": "客户提出费用减免诉求",
            },
        ),
    }
    outcome = Worker(
        gateway=FakeGateway(responses), worker_id="worker-four-states"
    ).run_once()
    assert outcome is not None and outcome.status == "succeeded"
    assert outcome.findings_count == 4

    body = client.get(
        f"{INSPECTIONS_ENDPOINT}/{inspection_id}",
        headers=_headers(users["agent_a"]),
    ).json()
    by_code = {finding["rule_code"]: finding["machine_verdict"] for finding in body["findings"]}
    assert by_code == {
        codes["hit"]: "hit",
        codes["clear"]: "clear",
        codes["insufficient_evidence"]: "insufficient_evidence",
        codes["not_applicable"]: "not_applicable",
    }
    # 四态没有被折叠成“命中/未命中”两态。
    assert len(set(by_code.values())) == 4
