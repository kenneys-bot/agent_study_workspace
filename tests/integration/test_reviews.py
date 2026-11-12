"""人工复核闭环的集成测试：真实 PostgreSQL + 真实 HTTP（T20，#21；M4-04）。

seam（真实 HTTP + 真实数据库 + 真实事务行锁）：

- ``POST /api/v1/reviews``：201 / 重复未完成 409 / 非 succeeded 质检 409 / 跨范围 404；
- ``GET /api/v1/reviews``、``GET /api/v1/reviews/{id}``：过滤分页、原机器判断、
  追加式决定历史与 revision；
- ``POST /api/v1/reviews/{id}/decisions``：confirm/reject/modify、理由必填、
  expected_revision 冲突 409；
- ``POST /api/v1/reviews/{id}/complete``：适用规则必须有最终结论；证据不足
  只有显式「待业务确认」才可完成；
- ``POST /api/v1/reviews/{id}/reopen``：completed → in_review，新 revision + 审计；
- 数据库行：review_decisions 追加只读、findings.machine_verdict 不变。

模型与 worker 不参与：测试直接写入 succeeded 质检与 findings（构造真实数据），
复核本身全部走真实端点与真实 PostgreSQL 事务，并发用两条数据库连接验证
CT-07（一成功一 409），不依赖进程内锁。
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import (
    AuditEvent,
    Conversation,
    Finding,
    InspectionRun,
    ReviewCase,
    ReviewDecision,
    ReviewCaseImmutable,
    ReviewDecisionImmutable,
    RuleSet,
    RuleVersion,
    Turn,
    User,
)
from bank_assistant.inspections.schema import InspectionStatus, RuleSnapshot, RuleSnapshotEntry
from bank_assistant.rules.schema import RuleApplicability, RuleCriteria
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SUFFIX = uuid4().hex[:8]
SCOPE_A = f"review-a-{SUFFIX}"
SCOPE_B = f"review-b-{SUFFIX}"
DEMO_PASSWORD = "demo-only-correct-horse-battery"
REVIEWS_ENDPOINT = "/api/v1/reviews"
CONVERSATION_TEXT = (
    "客户：请问我的信用卡年费怎么算\n"
    "客服：肯定免息，您放心\n"
)
HIT_QUOTE = "肯定免息"


@dataclass(frozen=True)
class InspectionFixture:
    """一次构造好的 succeeded 质检：会话、规则版本与逐规则机器判断。"""

    inspection_id: UUID
    conversation_id: UUID
    business_type: str
    rule_versions: dict[str, UUID]
    finding_ids: dict[str, UUID]

    def finding(self, rule_code: str) -> str:
        return str(self.finding_ids[rule_code])


@pytest.fixture(scope="module")
def users() -> dict[str, dict[str, object]]:
    suffix = uuid4().hex[:8]
    specs = {
        # 两名复核员必须在同一业务范围，才能验证 CT-07 的并发冲突。
        "reviewer_a": (f"review-a-{suffix}", SCOPE_A),
        "reviewer_b": (f"review-b-{suffix}", SCOPE_A),
        "outsider": (f"review-out-{suffix}", SCOPE_B),
        "other_scope": (f"review-other-{suffix}", SCOPE_B),
    }
    created: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        for key, (username, scope_id) in specs.items():
            user = User(
                user_id=uuid4(),
                username=username,
                password_hash=hash_password(DEMO_PASSWORD),
                role="reviewer",
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
                        role="reviewer",
                        scope_id=scope_id,
                    )
                ),
            }
    return created


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


def _headers(user: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


def _create_user(scope_id: str) -> dict[str, object]:
    """为列表测试创建独享范围，避免模块内其他测试的复核污染 total。"""
    username = f"review-list-{uuid4().hex[:10]}"
    with session_scope() as session:
        user = User(
            user_id=uuid4(),
            username=username,
            password_hash=hash_password(DEMO_PASSWORD),
            role="reviewer",
            scope_id=scope_id,
            active=True,
        )
        session.add(user)
        session.flush()
        user_id = user.user_id
    return {
        "user_id": user_id,
        "username": username,
        "scope_id": scope_id,
        "token": create_access_token(
            Identity(
                user_id=user_id,
                username=username,
                role="reviewer",
                scope_id=scope_id,
            )
        ),
    }


def _create_conversation(
    client: TestClient, user: dict[str, object]
) -> UUID:
    response = client.post(
        "/api/v1/conversations",
        json={"content": CONVERSATION_TEXT, "format": "text"},
        headers=_headers(user),
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["conversation_id"])


def _setup_inspection(
    client: TestClient,
    user: dict[str, object],
    *,
    verdicts: dict[str, str],
    status: str = InspectionStatus.SUCCEEDED.value,
) -> InspectionFixture:
    """构造真实会话 + 规则版本 + succeeded 质检 + 逐规则 findings。"""
    conversation_id = _create_conversation(client, user)
    business_type = f"bt-{uuid4().hex[:8]}"
    now = datetime.now(UTC)

    with session_scope() as session:
        conversation = session.get(Conversation, conversation_id)
        assert conversation is not None
        turns = session.scalars(
            select(Turn)
            .where(Turn.conversation_id == conversation_id)
            .order_by(Turn.ordinal.asc())
        ).all()
        hit_turn = next(turn for turn in turns if HIT_QUOTE in turn.content)
        start = hit_turn.content.index(HIT_QUOTE)

        entries: list[RuleSnapshotEntry] = []
        rule_versions: dict[str, UUID] = {}
        for index, (rule_code, verdict) in enumerate(verdicts.items()):
            severity = "high" if verdict == "hit" else "medium"
            rule = RuleSet(
                rule_id=uuid4(),
                rule_code=rule_code,
                title=f"{rule_code} 演示规则",
                category="service_risk",
                description="虚构演示制度：人工复核集成测试规则",
                scope_id=user["scope_id"],  # type: ignore[arg-type]
                demo_only=True,
            )
            session.add(rule)
            session.flush()
            applicability = {
                "business_types": [business_type],
                "channels": ["phone"],
                "conditions": ["客户提出费用说明或减免诉求"],
            }
            criteria = {
                "evidence_requirements": ["定位坐席答复原文"],
                "decision_rules": ["否定表述不命中"],
            }
            version = RuleVersion(
                rule_version_id=uuid4(),
                rule_id=rule.rule_id,
                version_no=1,
                business_type=business_type,
                applicability=applicability,
                criteria=criteria,
                exceptions=["坐席说明需审批"],
                severity=severity,
                source_refs=[
                    {
                        "kind": "demo_rule_set",
                        "ref": "https://demo.invalid/rules/t20",
                    }
                ],
                examples={},
                content_hash=hashlib.sha256(
                    f"{rule_code}:{index}:{uuid4()}".encode("utf-8")
                ).hexdigest(),
                status="published",
                demo_only=True,
                valid_from=now - timedelta(days=1),
                valid_to=None,
                author_id=user["user_id"],  # type: ignore[arg-type]
            )
            session.add(version)
            session.flush()
            rule_versions[rule_code] = version.rule_version_id
            entries.append(
                RuleSnapshotEntry(
                    rule_version_id=version.rule_version_id,
                    rule_id=rule.rule_id,
                    rule_code=rule_code,
                    version_no=1,
                    business_type=business_type,
                    severity=severity,
                    applicability=RuleApplicability.model_validate(
                        applicability
                    ),
                    criteria=RuleCriteria.model_validate(criteria),
                    exceptions=["坐席说明需审批"],
                    source_refs=[],
                    content_hash=version.content_hash,
                )
            )

        snapshot = RuleSnapshot(
            captured_at=now,
            scope_id=user["scope_id"],  # type: ignore[arg-type]
            business_type=business_type,
            rules=entries,
        )
        inspection = InspectionRun(
            inspection_id=uuid4(),
            conversation_id=conversation_id,
            conversation_hash=conversation.content_hash,
            rule_snapshot=snapshot.model_dump(mode="json"),
            status=status,
            scope_id=user["scope_id"],  # type: ignore[arg-type]
            diagnostics={},
            started_at=now,
            finished_at=now if status == InspectionStatus.SUCCEEDED.value else None,
            created_at=now,
            updated_at=now,
        )
        session.add(inspection)
        session.flush()

        finding_ids: dict[str, UUID] = {}
        for rule_code, verdict in verdicts.items():
            evidence = (
                [
                    {
                        "turn_id": str(hit_turn.turn_id),
                        "start": start,
                        "end": start + len(HIT_QUOTE),
                        "quote": HIT_QUOTE,
                    }
                ]
                if verdict == "hit"
                else []
            )
            finding = Finding(
                finding_id=uuid4(),
                inspection_id=inspection.inspection_id,
                rule_version_id=rule_versions[rule_code],
                machine_verdict=verdict,
                severity="high" if verdict == "hit" else "medium",
                rationale=f"{rule_code} 的机器判断（只读）",
                evidence=evidence,
                knowledge_refs=[],
            )
            session.add(finding)
            session.flush()
            finding_ids[rule_code] = finding.finding_id

        return InspectionFixture(
            inspection_id=inspection.inspection_id,
            conversation_id=conversation_id,
            business_type=business_type,
            rule_versions=rule_versions,
            finding_ids=finding_ids,
        )


def _post_review(
    client: TestClient,
    user: dict[str, object],
    *,
    inspection_id: UUID,
    assignee_id: object,
):
    return client.post(
        REVIEWS_ENDPOINT,
        json={
            "inspection_id": str(inspection_id),
            "assignee_id": str(assignee_id),
        },
        headers=_headers(user),
    )


def _post_decision(
    client: TestClient,
    user: dict[str, object],
    review_id: UUID,
    *,
    finding_id: UUID | str,
    action: str,
    expected_revision: int,
    reason: str | None = "已核对原文与规则",
    final_verdict: str | None = None,
):
    body: dict[str, object] = {
        "finding_id": str(finding_id),
        "action": action,
        "expected_revision": expected_revision,
    }
    if reason is not None:
        body["reason"] = reason
    if final_verdict is not None:
        body["final_verdict"] = final_verdict
    return client.post(
        f"{REVIEWS_ENDPOINT}/{review_id}/decisions",
        json=body,
        headers=_headers(user),
    )


def _complete(
    client: TestClient,
    user: dict[str, object],
    review_id: UUID,
    *,
    expected_revision: int,
    pending_business_confirmation: bool = False,
):
    return client.post(
        f"{REVIEWS_ENDPOINT}/{review_id}/complete",
        json={
            "expected_revision": expected_revision,
            "pending_business_confirmation": pending_business_confirmation,
        },
        headers=_headers(user),
    )


def _reopen(
    client: TestClient,
    user: dict[str, object],
    review_id: UUID,
    *,
    expected_revision: int,
    reason: str = "补充逐项结论",
):
    return client.post(
        f"{REVIEWS_ENDPOINT}/{review_id}/reopen",
        json={"expected_revision": expected_revision, "reason": reason},
        headers=_headers(user),
    )


def _review(review_id: UUID | str) -> ReviewCase:
    with session_scope() as session:
        review = session.get(ReviewCase, UUID(str(review_id)))
        assert review is not None
        return review


def _decisions(review_id: UUID | str) -> list[ReviewDecision]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(ReviewDecision)
                .where(ReviewDecision.review_id == UUID(str(review_id)))
                .order_by(ReviewDecision.accepted_revision.asc())
            ).all()
        )


def _audit_actions(review_id: UUID | str) -> list[str]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(AuditEvent.action).where(
                    AuditEvent.resource_type == "review_case",
                    AuditEvent.resource_id == str(review_id),
                )
            ).all()
        )


def _active_review_count(inspection_id: UUID) -> int:
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ReviewCase)
                .where(
                    ReviewCase.inspection_id == inspection_id,
                    ReviewCase.status.in_(["pending", "in_review"]),
                )
            )
            or 0
        )


def _rule_code(prefix: str) -> str:
    return f"DEMO-T20-{prefix}-{uuid4().hex[:6].upper()}"


# ---------------------------------------------------------------------------
# 表结构与迁移
# ---------------------------------------------------------------------------


def test_review_tables_match_the_contract_minimum() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"review_cases", "review_decisions"} <= tables

    review_columns = {
        column["name"] for column in inspector.get_columns("review_cases")
    }
    assert {
        "review_id",
        "inspection_id",
        "assignee_id",
        "status",
        "revision",
        "scope_id",
    } <= review_columns

    decision_columns = {
        column["name"] for column in inspector.get_columns("review_decisions")
    }
    assert {
        "decision_id",
        "review_id",
        "finding_id",
        "action",
        "final_verdict",
        "reason",
        "actor_id",
        "expected_revision",
        "accepted_revision",
        "created_at",
    } <= decision_columns

    # 同一 inspection 只允许一个未完成复核：由部分唯一索引兜底。
    review_indexes = {
        index["name"] for index in inspector.get_indexes("review_cases")
    }
    assert "uq_review_cases_active_inspection" in review_indexes


def test_reviews_revision_extends_the_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    # T20 自己的 revision 不是最终 head：T21 的 0011_report_exports 接在其后。
    assert script.get_revision("0010_reviews") is not None
    assert script.get_current_head() == "0011_report_exports"
    with session_scope() as session:
        current = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert current == "0011_report_exports"


# ---------------------------------------------------------------------------
# 创建、重复、跨范围
# ---------------------------------------------------------------------------


def test_create_review_returns_201_and_preserves_machine_verdicts(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    hit_code = _rule_code("HIT")
    clear_code = _rule_code("CLEAR")
    fixture = _setup_inspection(
        client,
        users["reviewer_a"],
        verdicts={hit_code: "hit", clear_code: "clear"},
    )

    response = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_b"]["user_id"],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_id"]
    assert body["status"] == "pending"
    assert body["revision"] == 1
    assert body["assignee_id"] == str(users["reviewer_b"]["user_id"])
    assert body["decisions"] == []
    assert body["applicable_finding_count"] == 2
    by_code = {finding["rule_code"]: finding for finding in body["findings"]}
    assert by_code[hit_code]["machine_verdict"] == "hit"
    assert by_code[hit_code]["effective_machine_verdict"] == "hit"
    assert by_code[clear_code]["machine_verdict"] == "clear"


def test_create_duplicate_active_review_is_409(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={_rule_code("DUP"): "hit"}
    )
    first = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_a"]["user_id"],
    )
    assert first.status_code == 201, first.text

    second = _post_review(
        client,
        users["reviewer_b"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_b"]["user_id"],
    )

    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == "REVIEW_ALREADY_ACTIVE"
    assert body["details"]["review_id"] == first.json()["review_id"]
    assert body["details"]["current_revision"] == 1
    assert _active_review_count(fixture.inspection_id) == 1


def test_review_requires_a_finished_inspection(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    fixture = _setup_inspection(
        client,
        users["reviewer_a"],
        verdicts={_rule_code("RUNNING"): "hit"},
        status=InspectionStatus.RUNNING.value,
    )

    response = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_a"]["user_id"],
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "INSPECTION_NOT_REVIEWABLE"


def test_cross_scope_review_is_404_everywhere(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={_rule_code("SCOPE"): "hit"}
    )
    created = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_a"]["user_id"],
    )
    assert created.status_code == 201, created.text
    review_id = created.json()["review_id"]
    finding_id = next(iter(fixture.finding_ids.values()))

    outsider = users["outsider"]
    assert client.get(
        f"{REVIEWS_ENDPOINT}/{review_id}", headers=_headers(outsider)
    ).status_code == 404
    assert _post_decision(
        client,
        outsider,
        UUID(review_id),
        finding_id=finding_id,
        action="confirm",
        expected_revision=1,
    ).status_code == 404
    assert _complete(
        client, outsider, UUID(review_id), expected_revision=1
    ).status_code == 404
    assert _reopen(
        client, outsider, UUID(review_id), expected_revision=1
    ).status_code == 404

    # 跨范围创建：看不到的质检与指派对象都不泄露存在性。
    assert _post_review(
        client,
        outsider,
        inspection_id=fixture.inspection_id,
        assignee_id=users["outsider"]["user_id"],
    ).status_code == 404
    assert _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["other_scope"]["user_id"],
    ).status_code == 404

    assert client.get(REVIEWS_ENDPOINT).status_code == 401
    listing = client.get(REVIEWS_ENDPOINT, headers=_headers(outsider))
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


# ---------------------------------------------------------------------------
# 逐项决定：confirm / reject / modify
# ---------------------------------------------------------------------------


def test_confirm_records_human_verdict_without_overwriting_machine_verdict(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    hit_code = _rule_code("CONFIRM")
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={hit_code: "hit"}
    )
    review_id = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_a"]["user_id"],
    ).json()["review_id"]

    response = _post_decision(
        client,
        users["reviewer_b"],
        UUID(review_id),
        finding_id=fixture.finding(hit_code),
        action="confirm",
        expected_revision=1,
        reason="证据区间与原文一致",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["revision"] == 2
    assert body["status"] == "in_review"
    assert body["decision"]["action"] == "confirm"
    assert body["decision"]["final_verdict"] == "hit"
    assert body["decision"]["reason"] == "证据区间与原文一致"
    assert body["decision"]["expected_revision"] == 1

    with session_scope() as session:
        finding = session.get(Finding, fixture.finding_ids[hit_code])
        assert finding is not None
        assert finding.machine_verdict == "hit"

    detail = client.get(
        f"{REVIEWS_ENDPOINT}/{review_id}", headers=_headers(users["reviewer_a"])
    ).json()
    assert detail["revision"] == 2
    assert len(detail["decisions"]) == 1
    hit_finding = next(
        item for item in detail["findings"] if item["rule_code"] == hit_code
    )
    assert hit_finding["machine_verdict"] == "hit"
    assert hit_finding["final_verdict"] == "hit"
    assert hit_finding["decision_count"] == 1


def test_reject_and_modify_append_history_and_latest_wins(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    code = _rule_code("REJECT")
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={code: "hit"}
    )
    review_id = UUID(
        _post_review(
            client,
            users["reviewer_a"],
            inspection_id=fixture.inspection_id,
            assignee_id=users["reviewer_a"]["user_id"],
        ).json()["review_id"]
    )

    rejected = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="reject",
        expected_revision=1,
        reason="坐席说明需审批，不构成承诺",
    )
    assert rejected.status_code == 201, rejected.text
    assert rejected.json()["decision"]["final_verdict"] == "clear"

    modified = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="modify",
        expected_revision=2,
        final_verdict="insufficient_evidence",
        reason="制度依据不足，改为待业务确认",
    )
    assert modified.status_code == 201, modified.text
    body = modified.json()
    assert body["revision"] == 3
    assert body["decision"]["final_verdict"] == "insufficient_evidence"

    detail = client.get(
        f"{REVIEWS_ENDPOINT}/{review_id}", headers=_headers(users["reviewer_a"])
    ).json()
    assert [item["action"] for item in detail["decisions"]] == ["reject", "modify"]
    assert [item["accepted_revision"] for item in detail["decisions"]] == [2, 3]
    assert detail["findings"][0]["final_verdict"] == "insufficient_evidence"
    assert detail["findings"][0]["machine_verdict"] == "hit"


def test_decision_validation_and_stale_revision(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    code = _rule_code("VALID")
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={code: "hit"}
    )
    review_id = UUID(
        _post_review(
            client,
            users["reviewer_a"],
            inspection_id=fixture.inspection_id,
            assignee_id=users["reviewer_a"]["user_id"],
        ).json()["review_id"]
    )

    missing_reason = client.post(
        f"{REVIEWS_ENDPOINT}/{review_id}/decisions",
        json={
            "finding_id": fixture.finding(code),
            "action": "confirm",
            "expected_revision": 1,
        },
        headers=_headers(users["reviewer_a"]),
    )
    assert missing_reason.status_code == 422

    blank_reason = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="confirm",
        expected_revision=1,
        reason="   ",
    )
    assert blank_reason.status_code == 422
    assert blank_reason.json()["code"] == "REASON_REQUIRED"

    modify_without_verdict = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="modify",
        expected_revision=1,
    )
    assert modify_without_verdict.status_code == 422
    assert modify_without_verdict.json()["code"] == "FINAL_VERDICT_REQUIRED"

    modify_same_verdict = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="modify",
        expected_revision=1,
        final_verdict="hit",
    )
    assert modify_same_verdict.status_code == 422
    assert modify_same_verdict.json()["code"] == "FINAL_VERDICT_UNCHANGED"

    confirm_with_verdict = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="confirm",
        expected_revision=1,
        final_verdict="clear",
    )
    assert confirm_with_verdict.status_code == 422
    assert confirm_with_verdict.json()["code"] == "FINAL_VERDICT_NOT_ALLOWED"

    other_fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={_rule_code("OTHER"): "hit"}
    )
    other_finding = next(iter(other_fixture.finding_ids.values()))
    foreign = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=other_finding,
        action="confirm",
        expected_revision=1,
    )
    assert foreign.status_code == 422
    assert foreign.json()["code"] == "FINDING_NOT_IN_REVIEW"

    accepted = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="confirm",
        expected_revision=1,
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["revision"] == 2

    stale = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="reject",
        expected_revision=1,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "REVISION_CONFLICT"
    assert stale.json()["details"]["current_revision"] == 2
    assert len(_decisions(review_id)) == 1


# ---------------------------------------------------------------------------
# 完成门禁
# ---------------------------------------------------------------------------


def test_complete_is_blocked_until_every_applicable_finding_is_decided(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    hit_code = _rule_code("GATE-HIT")
    clear_code = _rule_code("GATE-CLEAR")
    na_code = _rule_code("GATE-NA")
    fixture = _setup_inspection(
        client,
        users["reviewer_a"],
        verdicts={
            hit_code: "hit",
            clear_code: "clear",
            na_code: "not_applicable",
        },
    )
    review_id = UUID(
        _post_review(
            client,
            users["reviewer_a"],
            inspection_id=fixture.inspection_id,
            assignee_id=users["reviewer_a"]["user_id"],
        ).json()["review_id"]
    )

    first = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(hit_code),
        action="confirm",
        expected_revision=1,
    )
    assert first.status_code == 201, first.text
    assert first.json()["revision"] == 2

    blocked = _complete(
        client, users["reviewer_a"], review_id, expected_revision=2
    )
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "REVIEW_INCOMPLETE"
    assert body["details"]["current_revision"] == 2
    assert body["details"]["finding_ids"] == [fixture.finding(clear_code)]
    assert _review(review_id).status == "in_review"

    second = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(clear_code),
        action="reject",
        expected_revision=2,
        reason="没有命中表述，驳回",
    )
    assert second.status_code == 201, second.text
    assert second.json()["revision"] == 3

    completed = _complete(
        client, users["reviewer_a"], review_id, expected_revision=3
    )
    assert completed.status_code == 200, completed.text
    detail = completed.json()
    assert detail["status"] == "completed"
    assert detail["revision"] == 4
    assert detail["applicable_finding_count"] == 2
    assert detail["decided_finding_count"] == 2
    assert detail["missing_decision_finding_ids"] == []
    # not_applicable 不需要人工结论，也不会被当成已完成项。
    na_finding = next(
        item for item in detail["findings"] if item["rule_code"] == na_code
    )
    assert na_finding["applicable"] is False
    assert na_finding["final_verdict"] is None

    after_complete = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(na_code),
        action="confirm",
        expected_revision=4,
    )
    assert after_complete.status_code == 409
    assert after_complete.json()["code"] == "REVIEW_COMPLETED"
    assert after_complete.json()["details"]["current_revision"] == 4

    # 完成态可查询历史；完成后可另起新的复核流（未完成才唯一）。
    stored = client.get(
        f"{REVIEWS_ENDPOINT}/{review_id}", headers=_headers(users["reviewer_b"])
    )
    assert stored.status_code == 200
    assert stored.json()["status"] == "completed"
    assert len(stored.json()["decisions"]) == 2

    next_review = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_a"]["user_id"],
    )
    assert next_review.status_code == 201, next_review.text
    assert next_review.json()["review_id"] != str(review_id)
    assert next_review.json()["revision"] == 1


def test_insufficient_evidence_requires_pending_business_confirmation(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    code = _rule_code("PENDING")
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={code: "insufficient_evidence"}
    )
    review_id = UUID(
        _post_review(
            client,
            users["reviewer_a"],
            inspection_id=fixture.inspection_id,
            assignee_id=users["reviewer_a"]["user_id"],
        ).json()["review_id"]
    )

    confirmed = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="confirm",
        expected_revision=1,
        reason="现有对话不足以判断，保留证据不足",
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["decision"]["final_verdict"] == "insufficient_evidence"
    assert confirmed.json()["revision"] == 2

    blocked = _complete(
        client, users["reviewer_a"], review_id, expected_revision=2
    )
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "PENDING_BUSINESS_CONFIRMATION"
    assert body["details"]["current_revision"] == 2
    assert body["details"]["finding_ids"] == [fixture.finding(code)]

    completed = _complete(
        client,
        users["reviewer_a"],
        review_id,
        expected_revision=2,
        pending_business_confirmation=True,
    )
    assert completed.status_code == 200, completed.text
    detail = completed.json()
    assert detail["status"] == "completed"
    assert detail["revision"] == 3
    assert detail["pending_business_confirmation"] is True
    assert detail["pending_confirmation_finding_ids"] == [fixture.finding(code)]
    finding = next(
        item for item in detail["findings"] if item["rule_code"] == code
    )
    # 证据不足保持可见，绝不悄悄变成 clear。
    assert finding["machine_verdict"] == "insufficient_evidence"
    assert finding["final_verdict"] == "insufficient_evidence"

    stored = _decisions(review_id)
    assert len(stored) == 1
    assert stored[0].final_verdict == "insufficient_evidence"


# ---------------------------------------------------------------------------
# 并发与追加式历史
# ---------------------------------------------------------------------------


def test_two_reviewers_concurrent_decisions_exactly_one_wins(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    code = _rule_code("RACE")
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={code: "hit"}
    )
    review_id = UUID(
        _post_review(
            client,
            users["reviewer_a"],
            inspection_id=fixture.inspection_id,
            assignee_id=users["reviewer_a"]["user_id"],
        ).json()["review_id"]
    )

    barrier = Barrier(2)

    def submit(user_key: str, reviewer_label: str):
        from bank_assistant.main import app

        thread_client = TestClient(app)
        barrier.wait(timeout=20)
        return _post_decision(
            thread_client,
            users[user_key],
            review_id,
            finding_id=fixture.finding(code),
            action="confirm",
            expected_revision=1,
            reason=f"并发复核（{reviewer_label}）",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(submit, "reviewer_a", "A"),
            executor.submit(submit, "reviewer_b", "B"),
        ]
        first, second = [future.result(timeout=30) for future in futures]

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [201, 409], (first.text, second.text)
    loser = first if first.status_code == 409 else second
    assert loser.json()["code"] == "REVISION_CONFLICT"
    assert loser.json()["details"]["current_revision"] == 2

    stored = _review(review_id)
    assert stored.status == "in_review"
    assert stored.revision == 2
    assert len(_decisions(review_id)) == 1

    detail = client.get(
        f"{REVIEWS_ENDPOINT}/{review_id}", headers=_headers(users["reviewer_a"])
    ).json()
    assert detail["revision"] == 2
    assert len(detail["decisions"]) == 1


def test_review_decisions_are_append_only(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    code = _rule_code("APPEND")
    fixture = _setup_inspection(
        client, users["reviewer_a"], verdicts={code: "hit"}
    )
    created = _post_review(
        client,
        users["reviewer_a"],
        inspection_id=fixture.inspection_id,
        assignee_id=users["reviewer_a"]["user_id"],
    )
    assert created.status_code == 201, created.text
    review_id = UUID(created.json()["review_id"])
    accepted = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(code),
        action="confirm",
        expected_revision=1,
        reason="原始理由",
    )
    assert accepted.status_code == 201, accepted.text
    decision_id = UUID(accepted.json()["decision"]["decision_id"])

    with session_scope() as session:
        decision = session.get(ReviewDecision, decision_id)
        assert decision is not None
        decision.reason = "试图改写历史"
        with pytest.raises(ReviewDecisionImmutable):
            session.flush()
        session.rollback()

    with session_scope() as session:
        decision = session.get(ReviewDecision, decision_id)
        assert decision is not None
        session.delete(decision)
        with pytest.raises(ReviewDecisionImmutable):
            session.flush()
        session.rollback()

    with session_scope() as session:
        review = session.get(ReviewCase, review_id)
        assert review is not None
        session.delete(review)
        with pytest.raises(ReviewCaseImmutable):
            session.flush()
        session.rollback()

    stored = _decisions(review_id)
    assert len(stored) == 1
    assert stored[0].reason == "原始理由"


# ---------------------------------------------------------------------------
# 重新打开与审计
# ---------------------------------------------------------------------------


def test_reopen_creates_new_revision_and_preserves_history(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    hit_code = _rule_code("REOPEN-HIT")
    clear_code = _rule_code("REOPEN-CLEAR")
    fixture = _setup_inspection(
        client,
        users["reviewer_a"],
        verdicts={hit_code: "hit", clear_code: "clear"},
    )
    review_id = UUID(
        _post_review(
            client,
            users["reviewer_a"],
            inspection_id=fixture.inspection_id,
            assignee_id=users["reviewer_a"]["user_id"],
        ).json()["review_id"]
    )
    assert _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(hit_code),
        action="confirm",
        expected_revision=1,
    ).status_code == 201
    assert _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(clear_code),
        action="confirm",
        expected_revision=2,
    ).status_code == 201
    completed = _complete(
        client, users["reviewer_a"], review_id, expected_revision=3
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["revision"] == 4
    original_decisions = completed.json()["decisions"]
    assert len(original_decisions) == 2

    reopened = _reopen(
        client,
        users["reviewer_b"],
        review_id,
        expected_revision=4,
        reason="发现新证据，需重新核对",
    )
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["status"] == "in_review"
    assert body["revision"] == 5
    assert body["last_reopen_reason"] == "发现新证据，需重新核对"
    # 历史逐字保留，重新打开不改写任何已追加的决定。
    assert body["decisions"] == original_decisions

    reopen_again = _reopen(
        client, users["reviewer_a"], review_id, expected_revision=5
    )
    assert reopen_again.status_code == 409
    assert reopen_again.json()["code"] == "INVALID_STATE"
    assert reopen_again.json()["details"]["current_revision"] == 5

    modified = _post_decision(
        client,
        users["reviewer_a"],
        review_id,
        finding_id=fixture.finding(hit_code),
        action="modify",
        expected_revision=5,
        final_verdict="clear",
        reason="新证据推翻原判断",
    )
    assert modified.status_code == 201, modified.text
    assert modified.json()["revision"] == 6

    final_complete = _complete(
        client, users["reviewer_a"], review_id, expected_revision=6
    )
    assert final_complete.status_code == 200, final_complete.text
    final_body = final_complete.json()
    assert final_body["status"] == "completed"
    assert final_body["revision"] == 7
    assert len(final_body["decisions"]) == 3
    assert [item["accepted_revision"] for item in final_body["decisions"]] == [2, 3, 6]
    # 最早的两条决定仍在，理由未被改写。
    assert final_body["decisions"][0]["reason"] == "已核对原文与规则"
    assert final_body["decisions"][2]["reason"] == "新证据推翻原判断"

    actions = _audit_actions(review_id)
    assert "review.created" in actions
    assert actions.count("review.decision_recorded") == 3
    assert actions.count("review.completed") == 2
    assert actions.count("review.reopened") == 1


# ---------------------------------------------------------------------------
# 列表过滤与分页
# ---------------------------------------------------------------------------


def test_list_reviews_supports_filters_and_pagination(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    # 独享范围：列表断言只统计本测试创建的复核。
    list_user = _create_user(f"review-list-{uuid4().hex[:8]}")
    assignee = list_user["user_id"]
    created_ids: list[str] = []
    inspection_ids: list[UUID] = []

    for verdict in ("hit", "clear", "not_applicable"):
        fixture = _setup_inspection(
            client, list_user, verdicts={_rule_code("LIST"): verdict}
        )
        inspection_ids.append(fixture.inspection_id)
        response = _post_review(
            client,
            list_user,
            inspection_id=fixture.inspection_id,
            assignee_id=assignee,
        )
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["review_id"])

    # 让第一条成为 completed：单条 applicable（或全部不适用）都可通过完成门禁。
    first_review = UUID(created_ids[0])
    detail = client.get(
        f"{REVIEWS_ENDPOINT}/{first_review}", headers=_headers(list_user)
    ).json()
    revision = detail["revision"]
    for finding in detail["findings"]:
        if finding["applicable"]:
            accepted = _post_decision(
                client,
                list_user,
                first_review,
                finding_id=finding["finding_id"],
                action="confirm",
                expected_revision=revision,
            )
            assert accepted.status_code == 201, accepted.text
            revision = accepted.json()["revision"]
    completed = _complete(
        client, list_user, first_review, expected_revision=revision
    )
    assert completed.status_code == 200, completed.text

    all_items = client.get(
        REVIEWS_ENDPOINT, headers=_headers(list_user)
    ).json()
    assert all_items["total"] == 3
    assert {item["review_id"] for item in all_items["items"]} == set(created_ids)

    completed_only = client.get(
        REVIEWS_ENDPOINT,
        params={"status": "completed"},
        headers=_headers(list_user),
    ).json()
    assert completed_only["total"] == 1
    assert completed_only["items"][0]["review_id"] == created_ids[0]

    by_inspection = client.get(
        REVIEWS_ENDPOINT,
        params={"inspection_id": str(inspection_ids[1])},
        headers=_headers(list_user),
    ).json()
    assert by_inspection["total"] == 1
    assert by_inspection["items"][0]["review_id"] == created_ids[1]

    by_assignee = client.get(
        REVIEWS_ENDPOINT,
        params={"assignee_id": str(assignee)},
        headers=_headers(list_user),
    ).json()
    assert by_assignee["total"] == 3

    page = client.get(
        REVIEWS_ENDPOINT,
        params={"limit": 2, "offset": 0},
        headers=_headers(list_user),
    ).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["limit"] == 2
    assert page["offset"] == 0

    tail = client.get(
        REVIEWS_ENDPOINT,
        params={"limit": 2, "offset": 2},
        headers=_headers(list_user),
    ).json()
    assert len(tail["items"]) == 1

    invalid = client.get(
        REVIEWS_ENDPOINT,
        params={"limit": 101},
        headers=_headers(list_user),
    )
    assert invalid.status_code == 422
