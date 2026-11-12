"""报告导出的集成测试：真实 PostgreSQL + 真实 HTTP（T21，#22；M4-05）。

seam（真实端点 + 真实数据库 + 真实文件产物）：

- ``POST /api/v1/reports``：201 + export_id/下载引用；默认导出当前复核 revision，
  也可显式导出历史 revision；跨范围/不存在的质检一律 404；未完成质检 409；
  未知格式 422；
- ``GET /api/v1/reports/{id}``：再次校验权限后返回内容；JSON 为对象、HTML 为
  字符串；``content_hash`` 是产物字节的 SHA-256；
- CT-12：导出 → 再改判 → 旧导出重新读取仍逐字节相同，新 revision 的导出哈希不同；
- HTML：静态、无 ``<script>``，会话里的 ``<script>`` 被转义；手机号按读取层规则
  遮蔽；
- 数据库行：``report_exports`` 字段、单一迁移 head、只追加（UPDATE 显式失败）。

模型与 worker 不参与：测试直接写入 succeeded 质检与 findings（构造真实数据），
导出、复核、权限全部走真实端点与真实 PostgreSQL 事务。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from bank_assistant.config.settings import settings
from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import (
    AuditEvent,
    Conversation,
    Finding,
    InspectionRun,
    ReportExport,
    ReportExportImmutable,
    RuleSet,
    RuleVersion,
    Turn,
    User,
)
from bank_assistant.inspections.schema import (
    InspectionStatus,
    RuleSnapshot,
    RuleSnapshotEntry,
)
from bank_assistant.reports.schema import canonical_json_bytes
from bank_assistant.rules.schema import RuleApplicability, RuleCriteria
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_ENDPOINT = "/api/v1/reports"
REVIEWS_ENDPOINT = "/api/v1/reviews"
DEMO_PASSWORD = "demo-only-correct-horse-battery"

# 固定构造的会话：手机号用于验证读取层遮蔽；<script> 用于验证 HTML 转义。
PHONE = "13900001111"
XSS = '<script>alert("x")</script>'
CONVERSATION_TEXT = (
    f"客户：我要投诉，电话 {PHONE}，{XSS}\n"
    "客服：肯定免息，您放心\n"
)
HIT_QUOTE = "肯定免息"

_CREATED_ARTIFACT_REFS: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_artifacts() -> Iterator[None]:
    """只删除本模块创建的产物文件，避免在仓库 artifacts/ 留下运行垃圾。"""
    yield
    root = Path(settings.ARTIFACT_ROOT)
    for ref in _CREATED_ARTIFACT_REFS:
        try:
            (root / ref).unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class InspectionFixture:
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
    scope_a = f"report-a-{suffix}"
    scope_b = f"report-b-{suffix}"
    specs = {
        "exporter": (f"report-export-{suffix}", scope_a),
        "outsider": (f"report-out-{suffix}", scope_b),
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
                description="虚构演示制度：报告导出集成测试规则",
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
                        "ref": "https://demo.invalid/rules/t21",
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
            status=InspectionStatus.SUCCEEDED.value,
            scope_id=user["scope_id"],  # type: ignore[arg-type]
            diagnostics={},
            started_at=now,
            finished_at=now,
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
    inspection_id: UUID,
) -> UUID:
    response = client.post(
        REVIEWS_ENDPOINT,
        json={
            "inspection_id": str(inspection_id),
            "assignee_id": str(user["user_id"]),
        },
        headers=_headers(user),
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["review_id"])


def _post_decision(
    client: TestClient,
    user: dict[str, object],
    review_id: UUID,
    *,
    finding_id: UUID | str,
    action: str,
    expected_revision: int,
    reason: str = "已核对原文与规则",
    final_verdict: str | None = None,
) -> int:
    body: dict[str, object] = {
        "finding_id": str(finding_id),
        "action": action,
        "expected_revision": expected_revision,
        "reason": reason,
    }
    if final_verdict is not None:
        body["final_verdict"] = final_verdict
    response = client.post(
        f"{REVIEWS_ENDPOINT}/{review_id}/decisions",
        json=body,
        headers=_headers(user),
    )
    assert response.status_code == 201, response.text
    return int(response.json()["revision"])


def _complete(
    client: TestClient,
    user: dict[str, object],
    review_id: UUID,
    *,
    expected_revision: int,
    pending_business_confirmation: bool = False,
) -> int:
    response = client.post(
        f"{REVIEWS_ENDPOINT}/{review_id}/complete",
        json={
            "expected_revision": expected_revision,
            "pending_business_confirmation": pending_business_confirmation,
        },
        headers=_headers(user),
    )
    assert response.status_code == 200, response.text
    return int(response.json()["revision"])


def _export(
    client: TestClient,
    user: dict[str, object],
    inspection_id: UUID,
    *,
    fmt: str = "json",
    review_revision: int | None = None,
):
    body: dict[str, object] = {
        "inspection_id": str(inspection_id),
        "format": fmt,
    }
    if review_revision is not None:
        body["review_revision"] = review_revision
    response = client.post(
        REPORTS_ENDPOINT, json=body, headers=_headers(user)
    )
    if response.status_code == 201:
        _CREATED_ARTIFACT_REFS.append(response.json()["artifact_ref"])
    return response


def _artifact_path(artifact_ref: str) -> Path:
    return Path(settings.ARTIFACT_ROOT) / artifact_ref


def _rule_code(prefix: str) -> str:
    return f"DEMO-T21-{prefix}-{uuid4().hex[:6].upper()}"


def _three_verdicts() -> dict[str, str]:
    return {
        _rule_code("HIT"): "hit",
        _rule_code("CLEAR"): "clear",
        _rule_code("INSUFFICIENT"): "insufficient_evidence",
    }


# ---------------------------------------------------------------------------
# 表结构、迁移与只追加
# ---------------------------------------------------------------------------


def test_report_exports_table_matches_the_contract_minimum() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"  # ADR-0006：绝不静默用 SQLite
    inspector = inspect(engine)
    assert "report_exports" in set(inspector.get_table_names())

    columns = {
        column["name"] for column in inspector.get_columns("report_exports")
    }
    assert {
        "export_id",
        "inspection_id",
        "review_revision",
        "format",
        "content_hash",
        "artifact_ref",
        "generated_by",
        "created_at",
    } <= columns

    indexes = {
        index["name"] for index in inspector.get_indexes("report_exports")
    }
    assert "ix_report_exports_inspection_revision" in indexes


def test_report_exports_revision_is_the_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    assert script.get_current_head() == "0011_report_exports"
    with session_scope() as session:
        current = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert current == "0011_report_exports"


def test_report_export_rows_are_append_only(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """旧报告是固定快照：任何 UPDATE/DELETE 都在 ORM 层显式失败（CT-12）。"""
    fixture = _setup_inspection(
        client, users["exporter"], verdicts={_rule_code("HIT"): "hit"}
    )
    response = _export(client, users["exporter"], fixture.inspection_id)
    assert response.status_code == 201, response.text
    export_id = UUID(response.json()["export_id"])

    with pytest.raises(ReportExportImmutable):
        with session_scope() as session:
            row = session.get(ReportExport, export_id)
            assert row is not None
            row.content_hash = "0" * 64
            session.flush()

    with pytest.raises(ReportExportImmutable):
        with session_scope() as session:
            row = session.get(ReportExport, export_id)
            assert row is not None
            session.delete(row)
            session.flush()


# ---------------------------------------------------------------------------
# 导出 → 读取 → 再改判（CT-12）
# ---------------------------------------------------------------------------


def test_export_then_fetch_then_decide_again_preserves_old_bytes(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """CT-12：旧导出逐字节不变；新 revision 的导出是另一份报告。"""
    exporter = users["exporter"]
    verdicts = _three_verdicts()
    rule_codes = list(verdicts)
    hit_code, clear_code, insufficient_code = rule_codes
    fixture = _setup_inspection(client, exporter, verdicts=verdicts)
    review_id = _post_review(client, exporter, fixture.inspection_id)

    revision = _post_decision(
        client,
        exporter,
        review_id,
        finding_id=fixture.finding(hit_code),
        action="confirm",
        expected_revision=1,
    )
    assert revision == 2

    first = _export(client, exporter, fixture.inspection_id, fmt="json")
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["review_revision"] == 2
    assert first_body["format"] == "json"
    assert first_body["generated_by"] == str(exporter["user_id"])
    assert len(first_body["content_hash"]) == 64

    first_artifact = _artifact_path(first_body["artifact_ref"])
    first_bytes = first_artifact.read_bytes()
    assert hashlib.sha256(first_bytes).hexdigest() == first_body["content_hash"]

    before = client.get(
        f"{REPORTS_ENDPOINT}/{first_body['export_id']}",
        headers=_headers(exporter),
    )
    assert before.status_code == 200, before.text
    before_detail = before.json()
    assert before_detail["content_hash"] == first_body["content_hash"]
    # 响应内容可复算回与产物完全相同的字节。
    assert canonical_json_bytes(before_detail["content"]) == first_bytes
    before_findings = {
        finding["rule_code"]: finding
        for finding in before_detail["content"]["findings"]
    }
    assert before_findings[hit_code]["human_final_verdict"] == "hit"
    assert before_findings[clear_code]["human_final_verdict"] is None
    assert before_detail["content"]["review"]["review_revision"] == 2

    # 再改判：旧导出的产物文件与响应内容都必须不变。
    revision = _post_decision(
        client,
        exporter,
        review_id,
        finding_id=fixture.finding(clear_code),
        action="reject",
        expected_revision=2,
    )
    assert revision == 3

    after = client.get(
        f"{REPORTS_ENDPOINT}/{first_body['export_id']}",
        headers=_headers(exporter),
    )
    assert after.status_code == 200, after.text
    after_detail = after.json()
    assert after_detail["content_hash"] == first_body["content_hash"]
    assert canonical_json_bytes(after_detail["content"]) == first_bytes
    assert first_artifact.read_bytes() == first_bytes  # 文件也没有被改写
    assert (
        after_detail["content"]["findings"][0]["rule_code"]
        == before_detail["content"]["findings"][0]["rule_code"]
    )

    second = _export(client, exporter, fixture.inspection_id, fmt="json")
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["review_revision"] == 3
    assert second_body["content_hash"] != first_body["content_hash"]
    second_detail = client.get(
        f"{REPORTS_ENDPOINT}/{second_body['export_id']}",
        headers=_headers(exporter),
    )
    assert second_detail.status_code == 200, second_detail.text
    second_content = second_detail.json()["content"]
    second_findings = {
        finding["rule_code"]: finding for finding in second_content["findings"]
    }
    assert second_findings[clear_code]["human_final_verdict"] == "clear"
    assert second_content["review"]["review_revision"] == 3
    assert canonical_json_bytes(second_content) == _artifact_path(
        second_body["artifact_ref"]
    ).read_bytes()

    # 完成复核（revision 4）后，旧导出依旧不变；已完成复核也可按当前 revision 导出。
    revision = _post_decision(
        client,
        exporter,
        review_id,
        finding_id=fixture.finding(insufficient_code),
        action="confirm",
        expected_revision=3,
    )
    assert revision == 4
    _complete(
        client,
        exporter,
        review_id,
        expected_revision=4,
        pending_business_confirmation=True,
    )
    completed = _export(client, exporter, fixture.inspection_id, fmt="json")
    assert completed.status_code == 201, completed.text
    assert completed.json()["review_revision"] == 5
    assert completed.json()["content_hash"] not in {
        first_body["content_hash"],
        second_body["content_hash"],
    }
    completed_content = client.get(
        f"{REPORTS_ENDPOINT}/{completed.json()['export_id']}",
        headers=_headers(exporter),
    ).json()["content"]
    assert completed_content["summary"]["pending_business_confirmation_count"] == 1
    pending = {
        finding["rule_code"]: finding
        for finding in completed_content["findings"]
    }[insufficient_code]
    assert pending["pending_business_confirmation"] is True
    assert pending["human_final_verdict"] == "insufficient_evidence"

    still_old = client.get(
        f"{REPORTS_ENDPOINT}/{first_body['export_id']}",
        headers=_headers(exporter),
    )
    assert still_old.json()["content_hash"] == first_body["content_hash"]
    assert canonical_json_bytes(still_old.json()["content"]) == first_bytes


def test_export_defaults_to_current_revision_and_records_audit(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    verdicts = {_rule_code("HIT"): "hit", _rule_code("CLEAR"): "clear"}
    fixture = _setup_inspection(client, exporter, verdicts=verdicts)
    review_id = _post_review(client, exporter, fixture.inspection_id)
    _post_decision(
        client,
        exporter,
        review_id,
        finding_id=fixture.finding(next(iter(verdicts))),
        action="confirm",
        expected_revision=1,
    )

    response = _export(client, exporter, fixture.inspection_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_revision"] == 2
    assert body["artifact_ref"] == (
        f"exports/reports/{body['export_id']}.json"
    )
    assert body["content_url"] == f"{REPORTS_ENDPOINT}/{body['export_id']}"
    assert not body["artifact_ref"].startswith("/")
    assert PHONE not in body["artifact_ref"]

    with session_scope() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_type == "report_export",
                AuditEvent.resource_id == str(body["export_id"]),
                AuditEvent.action == "report.exported",
            )
        )
    assert event is not None
    assert event.actor_id == exporter["user_id"]
    assert event.after_summary["content_hash"] == body["content_hash"]
    assert PHONE not in str(event.after_summary)


def test_export_without_review_uses_zero_revision(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """没有人工复核时导出机器判断快照，review_revision=0，而不是伪造结论。"""
    exporter = users["exporter"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )
    response = _export(client, exporter, fixture.inspection_id)
    assert response.status_code == 201, response.text
    assert response.json()["review_revision"] == 0

    detail = client.get(
        f"{REPORTS_ENDPOINT}/{response.json()['export_id']}",
        headers=_headers(exporter),
    )
    assert detail.status_code == 200, detail.text
    content = detail.json()["content"]
    assert content["review"] == {
        "review_id": None,
        "review_revision": 0,
        "decision_count": 0,
    }
    assert content["summary"]["human_decided_count"] == 0
    assert all(
        finding["human_final_verdict"] is None
        and finding["human_decision"] is None
        for finding in content["findings"]
    )


def test_export_historical_revision_without_mutating_current_state(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """显式导出历史 revision：内容只含该 revision 时点已接受的决定。"""
    exporter = users["exporter"]
    verdicts = {_rule_code("HIT"): "hit", _rule_code("CLEAR"): "clear"}
    hit_code, clear_code = list(verdicts)
    fixture = _setup_inspection(client, exporter, verdicts=verdicts)
    review_id = _post_review(client, exporter, fixture.inspection_id)
    _post_decision(
        client,
        exporter,
        review_id,
        finding_id=fixture.finding(hit_code),
        action="confirm",
        expected_revision=1,
    )
    _post_decision(
        client,
        exporter,
        review_id,
        finding_id=fixture.finding(clear_code),
        action="reject",
        expected_revision=2,
    )

    historical = _export(
        client, exporter, fixture.inspection_id, review_revision=2
    )
    assert historical.status_code == 201, historical.text
    detail = client.get(
        f"{REPORTS_ENDPOINT}/{historical.json()['export_id']}",
        headers=_headers(exporter),
    ).json()
    findings = {
        finding["rule_code"]: finding for finding in detail["content"]["findings"]
    }
    assert detail["content"]["review"]["review_revision"] == 2
    assert findings[hit_code]["human_final_verdict"] == "hit"
    assert findings[clear_code]["human_final_verdict"] is None


# ---------------------------------------------------------------------------
# HTML：静态、XSS 安全、脱敏
# ---------------------------------------------------------------------------


def test_html_export_is_static_escaped_and_redacted(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )
    response = _export(client, exporter, fixture.inspection_id, fmt="html")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["format"] == "html"
    assert body["artifact_ref"].endswith(".html")

    detail = client.get(
        f"{REPORTS_ENDPOINT}/{body['export_id']}", headers=_headers(exporter)
    )
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    html = detail_body["content"]
    assert isinstance(html, str)

    lowered = html.lower()
    assert lowered.startswith("<!doctype html>")
    assert "<script" not in lowered
    assert "<img" not in lowered
    assert 'src="http' not in lowered
    assert "javascript:" not in lowered
    assert "&lt;script&gt;" in html  # 会话里的 <script> 被转义而非执行
    assert PHONE not in html  # 手机号按读取层规则遮蔽
    assert "*" * len(PHONE) in html  # 遮蔽保留原文长度
    assert "质检报告" in html

    artifact_bytes = _artifact_path(body["artifact_ref"]).read_bytes()
    assert html.encode("utf-8") == artifact_bytes
    assert (
        hashlib.sha256(html.encode("utf-8")).hexdigest()
        == body["content_hash"]
    )
    assert detail_body["content_hash"] == body["content_hash"]


# ---------------------------------------------------------------------------
# 权限与错误语义
# ---------------------------------------------------------------------------


def test_cross_scope_export_and_fetch_are_404(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    outsider = users["outsider"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )

    denied_create = _export(client, outsider, fixture.inspection_id)
    assert denied_create.status_code == 404, denied_create.text
    assert denied_create.json()["code"] == "NOT_FOUND"

    created = _export(client, exporter, fixture.inspection_id)
    assert created.status_code == 201, created.text
    export_id = created.json()["export_id"]

    denied_read = client.get(
        f"{REPORTS_ENDPOINT}/{export_id}", headers=_headers(outsider)
    )
    assert denied_read.status_code == 404, denied_read.text
    assert denied_read.json()["code"] == "NOT_FOUND"
    # 不泄露报告内容或产物路径。
    assert created.json()["content_hash"] not in denied_read.text
    assert created.json()["artifact_ref"] not in denied_read.text

    # 原范围内的用户可以正常读取。
    allowed = client.get(
        f"{REPORTS_ENDPOINT}/{export_id}", headers=_headers(exporter)
    )
    assert allowed.status_code == 200, allowed.text


def test_missing_inspection_and_export_are_404(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    missing = _export(client, exporter, uuid4())
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "NOT_FOUND"

    missing_read = client.get(
        f"{REPORTS_ENDPOINT}/{uuid4()}", headers=_headers(exporter)
    )
    assert missing_read.status_code == 404, missing_read.text
    assert missing_read.json()["code"] == "NOT_FOUND"


def test_invalid_format_is_422(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )
    response = _export(client, exporter, fixture.inspection_id, fmt="pdf")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"

    negative = client.post(
        REPORTS_ENDPOINT,
        json={
            "inspection_id": str(fixture.inspection_id),
            "format": "json",
            "review_revision": -1,
        },
        headers=_headers(exporter),
    )
    assert negative.status_code == 422, negative.text


def test_future_review_revision_is_409(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )
    review_id = _post_review(client, exporter, fixture.inspection_id)
    assert review_id is not None

    # 复核当前 revision 为 1（尚未提交决定）；请求 2 应显式 409。
    future = _export(
        client, exporter, fixture.inspection_id, review_revision=2
    )
    assert future.status_code == 409, future.text
    assert future.json()["code"] == "REVIEW_REVISION_UNAVAILABLE"
    assert future.json()["details"]["current_revision"] == 1


def test_export_with_no_api_keys_or_config_in_content(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    """报告只含业务数据：不得出现设置里的凭据或原始配置。"""
    exporter = users["exporter"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )
    response = _export(client, exporter, fixture.inspection_id, fmt="html")
    artifact = _artifact_path(response.json()["artifact_ref"]).read_text(
        encoding="utf-8"
    )
    for secret in (
        settings.DEEPSEEK_API_KEY,
        settings.DASHSCOPE_API_KEY,
        settings.SECRET_KEY,
        settings.DATABASE_URL,
    ):
        if secret:
            assert secret not in artifact
    assert "DATABASE_URL" not in artifact
    assert "ARTIFACT_ROOT" not in artifact


# ---------------------------------------------------------------------------
# 防误用：未完成质检不能导出为“最终报告”
# ---------------------------------------------------------------------------


def test_export_requires_succeeded_inspection(
    client: TestClient, users: dict[str, dict[str, object]]
) -> None:
    exporter = users["exporter"]
    fixture = _setup_inspection(
        client, exporter, verdicts={_rule_code("HIT"): "hit"}
    )
    with session_scope() as session:
        run = session.get(InspectionRun, fixture.inspection_id)
        assert run is not None
        run.status = InspectionStatus.RUNNING.value
        session.flush()

    response = _export(client, exporter, fixture.inspection_id)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "INSPECTION_NOT_REPORTABLE"
    assert response.json()["details"]["inspection_status"] == "running"
