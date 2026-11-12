"""报告导出的持久化与快照构建（M4-05/T21，#22；契约 §2/§3/§5/§7 CT-12）。

数据流：

1. ``POST /reports`` 先按身份加载质检（跨范围/不存在统一 404），只允许
   ``succeeded`` 的质检；随后解析目标复核快照：
   - 缺省 ``review_revision``：取该质检最新一条复核的当前 revision；
   - 显式 ``review_revision``：取最近一条 ``revision >= 目标`` 的复核，并只纳入
     ``accepted_revision <= 目标`` 的人工决定；
   - 没有复核时 revision=0，报告只含机器判断，绝不伪造人工结论。
2. 报告文档由**持久化的机器判断 + 该 revision 时点的人工决定 + 不可变会话轮次**
   构建；所有自由文本按读取层规则做号码遮蔽（只影响展示，不改库）。
3. 产物按格式落盘：JSON 用规范字节，HTML 用静态转义页面；`content_hash` 是
   产物字节的 SHA-256，连同相对 `artifact_ref` 写入 `report_exports`。
4. ``GET /reports/{id}`` 每次重新校验质检 ``scope_id``、复算产物哈希，再返回
   同一份内容。旧导出不会被后续复核改写（CT-12）：行只追加，文件只写一次。

错误语义：跨范围/不存在 → :class:`ReportNotFound`（404）；状态/版本冲突 →
:class:`ReportConflict`（409）；格式等输入问题 → :class:`ReportInputError`（422）；
产物缺失/损坏 → :class:`ReportArtifactError`（服务端显式失败，不返回空内容）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.config.settings import settings
from bank_assistant.infra.models import (
    InspectionRun,
    ReportExport,
    ReviewCase,
    ReviewDecision,
)
from bank_assistant.inspections.evidence import annotate_evidence
from bank_assistant.inspections.schema import InspectionStatus
from bank_assistant.inspections.service import (
    InspectionView,
    effective_machine_verdict,
    finding_evidence_valid,
    load_inspection,
)
from bank_assistant.reports.schema import (
    REPORT_FORMAT_HTML,
    REPORT_FORMAT_JSON,
    REPORT_FORMAT_VALUES,
    REPORT_SCHEMA_VERSION,
    ReportArtifactError,
    canonical_json_bytes,
    content_hash,
    render_html,
    report_artifact_ref,
    resolve_artifact_path,
)
from bank_assistant.security.identity import Identity
from bank_assistant.services.audit import append_audit_event
from bank_assistant.services.conversations import load_conversation
from bank_assistant.services.redaction import redact_sensitive_text

__all__ = [
    "ACTION_REPORT_EXPORTED",
    "RESOURCE_TYPE_REPORT_EXPORT",
    "ReportArtifactError",
    "ReportConflict",
    "ReportExportView",
    "ReportInputError",
    "ReportNotFound",
    "ReportSnapshot",
    "build_report_document",
    "create_report_export",
    "load_report_export",
]

logger = logging.getLogger(__name__)

RESOURCE_TYPE_REPORT_EXPORT = "report_export"
ACTION_REPORT_EXPORTED = "report.exported"

_NOT_APPLICABLE = "not_applicable"
_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
_VERDICTS = ("hit", "clear", "insufficient_evidence", "not_applicable")


class ReportNotFound(Exception):
    """导出/质检不存在或跨范围；路由统一映射为 404，不泄露存在性。"""


class ReportConflict(Exception):
    """状态或版本冲突；路由映射为 409。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)


class ReportInputError(ValueError):
    """请求可解析但语义不合法（格式等）；路由映射为 422。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ReportExportView:
    """一次导出及其内容；内容从产物文件读取，不重新生成。"""

    export: ReportExport
    content: Any


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    """待落盘的固定快照：报告文档与它绑定的复核 revision。"""

    document: dict[str, Any]
    review_revision: int


def _clock(now: Callable[[], datetime] | None) -> datetime:
    return (now or (lambda: datetime.now(UTC)))()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _display_text(value: object) -> str:
    """展示层脱敏：号码类信息遮蔽，原文与哈希基准保持不变。"""
    return redact_sensitive_text(str(value)).text


def _resolve_review(
    session: Session,
    inspection_id: UUID,
    requested_revision: int | None,
) -> tuple[ReviewCase | None, int]:
    """解析 (复核, revision)；不存在/未到达的 revision 与状态冲突显式失败。"""
    reviews = list(
        session.scalars(
            select(ReviewCase)
            .where(ReviewCase.inspection_id == inspection_id)
            .order_by(ReviewCase.created_at.desc(), ReviewCase.review_id.desc())
        ).all()
    )
    if not reviews:
        if requested_revision is not None and requested_revision > 0:
            raise ReportConflict(
                "REVIEW_REVISION_UNAVAILABLE",
                "该质检还没有人工复核，无法导出指定 revision",
                details={"current_revision": 0},
            )
        return None, requested_revision or 0

    if requested_revision is None:
        chosen = reviews[0]
        return chosen, chosen.revision

    if requested_revision == 0:
        # revision 0 是「尚未有人工决定」的快照，不属于任何一条复核流。
        return None, 0

    chosen = next(
        (review for review in reviews if review.revision >= requested_revision),
        None,
    )
    if chosen is None:
        raise ReportConflict(
            "REVIEW_REVISION_UNAVAILABLE",
            "请求的复核 revision 尚未产生",
            details={"current_revision": reviews[0].revision},
        )
    return chosen, requested_revision


def _decisions_at_revision(
    session: Session, review: ReviewCase, revision: int
) -> tuple[ReviewDecision, ...]:
    """只纳入该 revision 时点已接受的决定；后续改判不会回溯修改旧快照。"""
    if revision < 2:
        return ()
    return tuple(
        session.scalars(
            select(ReviewDecision)
            .where(
                ReviewDecision.review_id == review.review_id,
                ReviewDecision.accepted_revision <= revision,
            )
            .order_by(ReviewDecision.accepted_revision.asc())
        ).all()
    )


def _latest_decisions(
    decisions: Sequence[ReviewDecision],
) -> dict[UUID, ReviewDecision]:
    """按 finding 取最新（accepted_revision 最大）的人工决定。"""
    latest: dict[UUID, ReviewDecision] = {}
    for decision in decisions:
        current = latest.get(decision.finding_id)
        if current is None or decision.accepted_revision > current.accepted_revision:
            latest[decision.finding_id] = decision
    return latest


def _ordered_findings(view: InspectionView) -> list[Any]:
    """与复核/质检详情一致的稳定顺序：先规则编码，再 finding_id。"""
    return sorted(
        view.findings,
        key=lambda finding: (
            view.rules_by_version[finding.rule_version_id].rule_code
            if finding.rule_version_id in view.rules_by_version
            else "",
            str(finding.finding_id),
        ),
    )


def _decision_document(decision: ReviewDecision) -> dict[str, Any]:
    return {
        "decision_id": str(decision.decision_id),
        "action": decision.action,
        "final_verdict": decision.final_verdict,
        "reason": _display_text(decision.reason),
        "actor_id": str(decision.actor_id),
        "expected_revision": decision.expected_revision,
        "accepted_revision": decision.accepted_revision,
        "created_at": _iso(decision.created_at),
    }


def build_report_document(
    session: Session,
    identity: Identity,
    *,
    inspection_id: UUID,
    requested_revision: int | None = None,
) -> ReportSnapshot:
    """构建报告文档（不含 export 元数据，保证同一快照可复现）。"""
    inspection_view = load_inspection(session, identity, inspection_id)
    run = inspection_view.run
    if run.status != InspectionStatus.SUCCEEDED.value:
        raise ReportConflict(
            "INSPECTION_NOT_REPORTABLE",
            "只有已完成的质检才能导出最终报告",
            details={"inspection_status": run.status},
        )

    review, revision = _resolve_review(session, inspection_id, requested_revision)
    decisions = (
        _decisions_at_revision(session, review, revision)
        if review is not None
        else ()
    )
    latest = _latest_decisions(decisions)

    conversation_view = load_conversation(session, run.conversation_id)
    turns: list[dict[str, Any]] = []
    if conversation_view is not None:
        for turn in conversation_view.turns:
            turns.append(
                {
                    "turn_id": str(turn.turn_id),
                    "ordinal": turn.ordinal,
                    "speaker": _display_text(turn.speaker),
                    "content": _display_text(turn.content),
                    "original_content_length": len(turn.content),
                }
            )

    finding_documents: list[dict[str, Any]] = []
    machine_counts = {verdict: 0 for verdict in _VERDICTS}
    human_decided = 0
    pending_count = 0
    for finding in _ordered_findings(inspection_view):
        rule = inspection_view.rules_by_version.get(finding.rule_version_id)
        effective = effective_machine_verdict(finding, inspection_view)
        evidence_valid = finding_evidence_valid(finding, inspection_view)
        decision = latest.get(finding.finding_id)
        human_final = decision.final_verdict if decision is not None else None
        if decision is not None:
            human_decided += 1

        applicable = effective != _NOT_APPLICABLE
        pending = applicable and (
            human_final == _INSUFFICIENT_EVIDENCE
            or (decision is None and effective == _INSUFFICIENT_EVIDENCE)
        )
        if pending:
            pending_count += 1
        machine_counts[effective] = machine_counts.get(effective, 0) + 1

        evidence = annotate_evidence(
            list(finding.evidence or []),
            turn_contents=inspection_view.turn_contents,
            turn_ordinals=inspection_view.turn_ordinals,
        )
        for item in evidence:
            masked = redact_sensitive_text(str(item.get("quote", "")))
            item["quote"] = masked.text
            item["quote_redacted"] = masked.applied

        finding_documents.append(
            {
                "finding_id": str(finding.finding_id),
                "rule_version_id": str(finding.rule_version_id),
                "rule_id": str(rule.rule_id) if rule is not None else None,
                "rule_code": rule.rule_code if rule is not None else None,
                "version_no": rule.version_no if rule is not None else None,
                "business_type": rule.business_type if rule is not None else None,
                "severity": finding.severity,
                "machine_verdict": effective,
                "raw_machine_verdict": (
                    finding.machine_verdict
                    if finding.machine_verdict != effective
                    else None
                ),
                "evidence_valid": evidence_valid,
                "rationale": _display_text(finding.rationale),
                "evidence": evidence,
                "knowledge_refs": list(finding.knowledge_refs or []),
                "human_final_verdict": human_final,
                "human_decision": (
                    _decision_document(decision)
                    if decision is not None
                    else None
                ),
                "pending_business_confirmation": pending,
            }
        )

    document: dict[str, Any] = {
        "report_version": REPORT_SCHEMA_VERSION,
        "inspection": {
            "inspection_id": str(run.inspection_id),
            "conversation_id": str(run.conversation_id),
            "status": run.status,
            "conversation_hash": run.conversation_hash,
            "rule_count": inspection_view.rule_count,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
        },
        "review": {
            "review_id": str(review.review_id) if review is not None else None,
            "review_revision": revision,
            "decision_count": len(decisions),
        },
        "conversation": {
            "turn_count": len(turns),
            "redaction_applied": bool(
                conversation_view.conversation.redaction_applied
            )
            if conversation_view is not None
            else False,
            "turns": turns,
        },
        "findings": finding_documents,
        "summary": {
            "finding_count": len(finding_documents),
            "machine_verdict_counts": machine_counts,
            "human_decided_count": human_decided,
            "pending_business_confirmation_count": pending_count,
        },
    }
    # 真正写库在 create_report_export；这里只返回可复现的文档与绑定的 revision。
    return ReportSnapshot(document=document, review_revision=revision)


def _render_artifact(
    document: Mapping[str, Any], report_format: str
) -> bytes:
    """按格式生成最终产物字节；HTML 是静态转义页面，JSON 是规范字节。"""
    if report_format == REPORT_FORMAT_JSON:
        return canonical_json_bytes(document)
    if report_format == REPORT_FORMAT_HTML:
        return render_html(document).encode("utf-8")
    raise ReportInputError(
        "INVALID_REPORT_FORMAT", f"未知报告格式: {report_format}"
    )


def _write_artifact(path: Path, payload: bytes) -> None:
    """先写临时文件再原子替换：读取方永远看不到半份报告。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def create_report_export(
    session: Session,
    identity: Identity,
    *,
    inspection_id: UUID,
    report_format: str,
    review_revision: int | None = None,
    trace_id: str,
    now: Callable[[], datetime] | None = None,
) -> ReportExport:
    """构建固定快照、落盘产物并记录哈希；返回可序列化的导出行。"""
    if report_format not in REPORT_FORMAT_VALUES:
        raise ReportInputError(
            "INVALID_REPORT_FORMAT", f"未知报告格式: {report_format}"
        )
    moment = _clock(now)
    snapshot = build_report_document(
        session,
        identity,
        inspection_id=inspection_id,
        requested_revision=review_revision,
    )
    payload = _render_artifact(snapshot.document, report_format)
    digest = content_hash(payload)
    export_id = uuid4()
    artifact_ref = report_artifact_ref(export_id, report_format)
    try:
        path = resolve_artifact_path(settings.ARTIFACT_ROOT, artifact_ref)
        _write_artifact(path, payload)
    except (OSError, ReportArtifactError) as exc:
        logger.exception(
            "report_artifact_write_failed inspection_id=%s format=%s",
            inspection_id,
            report_format,
        )
        raise ReportArtifactError("报告产物写入失败") from exc

    export = ReportExport(
        export_id=export_id,
        inspection_id=inspection_id,
        review_revision=snapshot.review_revision,
        format=report_format,
        content_hash=digest,
        artifact_ref=artifact_ref,
        generated_by=identity.user_id,
        created_at=moment,
    )
    session.add(export)
    session.flush()
    append_audit_event(
        session,
        actor_id=identity.user_id,
        action=ACTION_REPORT_EXPORTED,
        resource_type=RESOURCE_TYPE_REPORT_EXPORT,
        resource_id=str(export.export_id),
        trace_id=trace_id,
        after_summary={
            "inspection_id": str(inspection_id),
            "review_revision": export.review_revision,
            "format": export.format,
            "content_hash": digest,
            "artifact_ref": artifact_ref,
        },
    )
    return export


def load_report_export(
    session: Session, identity: Identity, export_id: UUID
) -> ReportExportView:
    """读取导出：每次重新按质检 scope 鉴权，并复算产物哈希。"""
    export = session.get(ReportExport, export_id)
    if export is None:
        raise ReportNotFound("报告不存在")
    inspection = session.get(InspectionRun, export.inspection_id)
    if inspection is None or inspection.scope_id != identity.scope_id:
        raise ReportNotFound("报告不存在")

    try:
        path = resolve_artifact_path(settings.ARTIFACT_ROOT, export.artifact_ref)
        payload = path.read_bytes()
    except ReportArtifactError:
        raise
    except OSError as exc:
        logger.error(
            "report_artifact_unreadable export_id=%s", export.export_id
        )
        raise ReportArtifactError("报告产物不可读") from exc

    if content_hash(payload) != export.content_hash:
        logger.error("report_artifact_hash_mismatch export_id=%s", export.export_id)
        raise ReportArtifactError("报告产物哈希与记录不一致")

    try:
        if export.format == REPORT_FORMAT_JSON:
            content: Any = json.loads(payload.decode("utf-8"))
        elif export.format == REPORT_FORMAT_HTML:
            content = payload.decode("utf-8")
        else:
            raise ReportArtifactError("报告格式非法")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportArtifactError("报告产物无法解析") from exc
    return ReportExportView(export=export, content=content)
