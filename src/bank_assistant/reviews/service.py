"""人工复核的读写、乐观并发与审计（M4-04/T20，#21；契约 §2/§3/§5/§7）。

数据流：

- ``POST /reviews`` 为一个已成功的质检创建复核；同一 inspection 只允许一个
  未完成复核（部分唯一索引 + 服务层预检），重复创建返回 409。
- ``POST /reviews/{id}/decisions`` 追加一条逐项结论。事务先 ``SELECT ... FOR
  UPDATE`` 锁住 ``review_cases`` 行，再校验 ``expected_revision``、写 decision、
  把 revision 加一。锁在数据库行上，不依赖进程内锁（CT-07）。
- ``POST /reviews/{id}/complete`` 校验每条适用 finding 都有最终人工结论；
  仍有 ``insufficient_evidence`` 时必须显式 ``pending_business_confirmation``，
  该状态继续出现在复核详情与后续报告数据里，绝不改成 clear。
- ``POST /reviews/{id}/reopen`` 只允许 completed → in_review，产生新 revision
  与审计事件；任何普通更新都不能改写已追加的历史。

机器判断 ``findings.machine_verdict`` 只读：本模块只读取 finding，人工结论只
追加到 ``review_decisions``。读取路径沿用 T19 的证据回验，无效证据的 ``hit``
按 ``insufficient_evidence`` 参与复核，不能被 confirm 成命中。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bank_assistant.infra.models import (
    Finding,
    InspectionRun,
    ReviewCase,
    ReviewDecision,
    User,
)
from bank_assistant.inspections.schema import InspectionStatus, RuleSnapshotEntry
from bank_assistant.inspections.service import (
    InspectionView,
    effective_machine_verdict,
    finding_evidence_valid,
    load_inspection,
)
from bank_assistant.reviews.schema import (
    NOT_APPLICABLE,
    CompletionBlocked,
    CompletionCheck,
    DecisionSnapshot,
    FindingSnapshot,
    ReviewAction,
    ReviewInputError,
    ReviewRevisionConflict,
    ReviewStateError,
    ReviewStatus,
    ensure_completable,
    ensure_transition,
    evaluate_completion,
    next_revision,
    resolve_final_verdict,
)
from bank_assistant.security.identity import Identity
from bank_assistant.services.audit import append_audit_event

__all__ = [
    "ACTION_REVIEW_COMPLETED",
    "ACTION_REVIEW_CREATED",
    "ACTION_REVIEW_DECISION",
    "ACTION_REVIEW_REOPENED",
    "MAX_PAGE_SIZE",
    "RESOURCE_TYPE_REVIEW",
    "ReviewConflict",
    "ReviewFindingView",
    "ReviewNotFound",
    "ReviewPage",
    "ReviewView",
    "build_review_view",
    "complete_review",
    "create_review",
    "get_review",
    "list_reviews",
    "reopen_review",
    "submit_decision",
]

RESOURCE_TYPE_REVIEW = "review_case"
ACTION_REVIEW_CREATED = "review.created"
ACTION_REVIEW_DECISION = "review.decision_recorded"
ACTION_REVIEW_COMPLETED = "review.completed"
ACTION_REVIEW_REOPENED = "review.reopened"

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

_ACTIVE_STATUSES = (ReviewStatus.PENDING.value, ReviewStatus.IN_REVIEW.value)


class ReviewNotFound(Exception):
    """复核/质检/指派对象不存在或跨范围；路由统一映射为 404。"""


class ReviewConflict(Exception):
    """状态/版本冲突；路由映射为 409，并带上当前 revision。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_revision: int | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.current_revision = current_revision
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ReviewFindingView:
    """复核详情里的一条 finding：原机器判断 + 有效判断 + 人工结论历史。"""

    finding: Finding
    rule: RuleSnapshotEntry | None
    effective_verdict: str
    evidence_valid: bool
    decisions: tuple[ReviewDecision, ...]
    latest_decision: ReviewDecision | None

    @property
    def applicable(self) -> bool:
        return self.finding.machine_verdict != NOT_APPLICABLE


@dataclass(frozen=True, slots=True)
class ReviewView:
    """一次复核的完整读取视图（写入响应也复用它）。"""

    review: ReviewCase
    inspection: InspectionRun
    inspection_view: InspectionView
    findings: tuple[ReviewFindingView, ...]
    decisions: tuple[ReviewDecision, ...]
    completion: CompletionCheck


@dataclass(frozen=True, slots=True)
class ReviewPage:
    items: tuple[ReviewCase, ...]
    total: int
    limit: int
    offset: int


def _clock(now: Callable[[], datetime] | None) -> datetime:
    return (now or (lambda: datetime.now(UTC)))()


def _load_review(
    session: Session,
    identity: Identity,
    review_id: UUID,
    *,
    for_update: bool = False,
) -> ReviewCase:
    """按身份读取复核；不存在或跨 scope 统一 404，不泄露存在性。"""
    review = session.get(ReviewCase, review_id, with_for_update=for_update)
    if review is None or review.scope_id != identity.scope_id:
        raise ReviewNotFound("复核不存在")
    return review


def _audit(
    session: Session,
    *,
    identity: Identity,
    review: ReviewCase,
    action: str,
    trace_id: str,
    before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
) -> None:
    append_audit_event(
        session,
        actor_id=identity.user_id,
        action=action,
        resource_type=RESOURCE_TYPE_REVIEW,
        resource_id=str(review.review_id),
        trace_id=trace_id,
        before_summary=before,
        after_summary=after,
    )


def _active_review(
    session: Session, inspection_id: UUID
) -> ReviewCase | None:
    return session.scalar(
        select(ReviewCase).where(
            ReviewCase.inspection_id == inspection_id,
            ReviewCase.status.in_(_ACTIVE_STATUSES),
        )
    )


def _load_assignee(session: Session, identity: Identity, assignee_id: UUID) -> User:
    assignee = session.get(User, assignee_id)
    if (
        assignee is None
        or assignee.scope_id != identity.scope_id
        or not assignee.active
    ):
        # 跨范围/停用与不存在同一个 404，不泄露用户是否存在。
        raise ReviewNotFound("指派对象不存在")
    return assignee


def create_review(
    session: Session,
    identity: Identity,
    *,
    inspection_id: UUID,
    assignee_id: UUID,
    trace_id: str,
    now: Callable[[], datetime] | None = None,
) -> ReviewCase:
    """为一个已成功的质检创建复核；重复创建未完成复核返回 409。"""
    moment = _clock(now)
    inspection = session.get(InspectionRun, inspection_id)
    if inspection is None or inspection.scope_id != identity.scope_id:
        raise ReviewNotFound("质检不存在")
    if inspection.status != InspectionStatus.SUCCEEDED.value:
        # queued/running/failed/cancelled 都没有可复核的最终判断。
        raise ReviewConflict(
            "INSPECTION_NOT_REVIEWABLE",
            "只有已完成的质检才能创建人工复核",
            current_revision=None,
            details={"inspection_status": inspection.status},
        )

    assignee = _load_assignee(session, identity, assignee_id)

    existing = _active_review(session, inspection_id)
    if existing is not None:
        raise ReviewConflict(
            "REVIEW_ALREADY_ACTIVE",
            "该质检已有未完成的人工复核",
            current_revision=existing.revision,
            details={"review_id": str(existing.review_id)},
        )

    review = ReviewCase(
        review_id=uuid4(),
        inspection_id=inspection_id,
        assignee_id=assignee.user_id,
        status=ReviewStatus.PENDING.value,
        revision=1,
        scope_id=identity.scope_id,
        created_at=moment,
        updated_at=moment,
    )
    session.add(review)
    try:
        session.flush()
    except IntegrityError as exc:
        # 并发创建：部分唯一索引保证只有一条未完成复核；失败方显式 409。
        # 只有该约束冲突才转成业务冲突，其他完整性问题不能被掩盖。
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != "uq_review_cases_active_inspection":
            raise
        session.rollback()
        winner = _active_review(session, inspection_id)
        raise ReviewConflict(
            "REVIEW_ALREADY_ACTIVE",
            "该质检已有未完成的人工复核",
            current_revision=winner.revision if winner is not None else None,
            details=(
                {"review_id": str(winner.review_id)} if winner is not None else {}
            ),
        ) from None

    _audit(
        session,
        identity=identity,
        review=review,
        action=ACTION_REVIEW_CREATED,
        trace_id=trace_id,
        after={
            "review_id": str(review.review_id),
            "inspection_id": str(review.inspection_id),
            "assignee_id": str(review.assignee_id),
            "status": review.status,
            "revision": review.revision,
        },
    )
    session.flush()
    return review


def build_review_view(
    session: Session,
    identity: Identity,
    review: ReviewCase,
) -> ReviewView:
    """加载复核详情：原机器判断、有效判断、全部追加决定与完成门禁事实。"""
    inspection_view: InspectionView = load_inspection(
        session, identity, review.inspection_id
    )
    decisions = tuple(
        session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.review_id == review.review_id)
            .order_by(
                ReviewDecision.accepted_revision.asc(),
                ReviewDecision.decision_id.asc(),
            )
        ).all()
    )
    decisions_by_finding: dict[UUID, list[ReviewDecision]] = defaultdict(list)
    for decision in decisions:
        decisions_by_finding[decision.finding_id].append(decision)

    ordered_findings = sorted(
        inspection_view.findings,
        key=lambda finding: (
            inspection_view.rules_by_version[finding.rule_version_id].rule_code
            if finding.rule_version_id in inspection_view.rules_by_version
            else "",
            str(finding.finding_id),
        ),
    )

    finding_views: list[ReviewFindingView] = []
    effective_by_finding: dict[UUID, str] = {}
    for finding in ordered_findings:
        history = tuple(decisions_by_finding.get(finding.finding_id, ()))
        effective = effective_machine_verdict(finding, inspection_view)
        effective_by_finding[finding.finding_id] = effective
        finding_views.append(
            ReviewFindingView(
                finding=finding,
                rule=inspection_view.rules_by_version.get(finding.rule_version_id),
                effective_verdict=effective,
                evidence_valid=finding_evidence_valid(finding, inspection_view),
                decisions=history,
                latest_decision=history[-1] if history else None,
            )
        )

    completion = evaluate_completion(
        (
            FindingSnapshot(
                finding_id=finding.finding_id,
                machine_verdict=effective_by_finding[finding.finding_id],
            )
            for finding in ordered_findings
        ),
        (
            DecisionSnapshot(
                finding_id=decision.finding_id,
                final_verdict=decision.final_verdict,
                accepted_revision=decision.accepted_revision,
            )
            for decision in decisions
        ),
    )
    return ReviewView(
        review=review,
        inspection=inspection_view.run,
        inspection_view=inspection_view,
        findings=tuple(finding_views),
        decisions=decisions,
        completion=completion,
    )


def get_review(
    session: Session, identity: Identity, review_id: UUID
) -> ReviewView:
    """读取一次复核；不存在/跨范围统一 404。"""
    review = _load_review(session, identity, review_id)
    return build_review_view(session, identity, review)


def list_reviews(
    session: Session,
    identity: Identity,
    *,
    status: str | None = None,
    assignee_id: UUID | None = None,
    inspection_id: UUID | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> ReviewPage:
    """列出当前范围内的复核；支持状态/指派/质检过滤与分页。"""
    if status is not None and status not in {item.value for item in ReviewStatus}:
        raise ReviewInputError("INVALID_REVIEW_STATUS", f"未知复核状态: {status}")
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ReviewInputError(
            "INVALID_PAGE_LIMIT", f"limit 必须在 1..{MAX_PAGE_SIZE} 之间"
        )
    if offset < 0:
        raise ReviewInputError("INVALID_PAGE_OFFSET", "offset 不能为负")

    conditions = [ReviewCase.scope_id == identity.scope_id]
    if status is not None:
        conditions.append(ReviewCase.status == status)
    if assignee_id is not None:
        conditions.append(ReviewCase.assignee_id == assignee_id)
    if inspection_id is not None:
        conditions.append(ReviewCase.inspection_id == inspection_id)

    total = int(
        session.scalar(
            select(func.count()).select_from(ReviewCase).where(*conditions)
        )
        or 0
    )
    rows = session.scalars(
        select(ReviewCase)
        .where(*conditions)
        .order_by(ReviewCase.created_at.desc(), ReviewCase.review_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return ReviewPage(items=tuple(rows), total=total, limit=limit, offset=offset)


def _clean_reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise ReviewInputError("REASON_REQUIRED", "复核动作必须填写理由")
    return cleaned


def submit_decision(
    session: Session,
    identity: Identity,
    review_id: UUID,
    *,
    finding_id: UUID,
    action: str,
    reason: str,
    expected_revision: int,
    final_verdict: str | None = None,
    trace_id: str,
    now: Callable[[], datetime] | None = None,
) -> tuple[ReviewCase, ReviewDecision]:
    """追加一条逐项结论；成功时返回加一后的复核与决定。

    行锁 + expected_revision 检查在同一个短事务内完成：两个复核员并发提交
    同一 revision 时，后到者拿到 409 与当前 revision，绝不覆盖先到者（CT-07）。
    """
    moment = _clock(now)
    review = _load_review(session, identity, review_id, for_update=True)
    if review.status == ReviewStatus.COMPLETED.value:
        raise ReviewConflict(
            "REVIEW_COMPLETED",
            "已完成的复核不能追加决定；请先显式重新打开",
            current_revision=review.revision,
        )
    try:
        new_revision = next_revision(
            current=review.revision, expected=expected_revision
        )
    except ReviewRevisionConflict as exc:
        raise ReviewConflict(
            "REVISION_CONFLICT",
            "复核已被其他操作更新：revision 已过期",
            current_revision=exc.current,
            details={"current_revision": exc.current, "expected_revision": exc.expected},
        ) from None

    inspection_view = load_inspection(session, identity, review.inspection_id)
    finding = next(
        (
            item
            for item in inspection_view.findings
            if item.finding_id == finding_id
        ),
        None,
    )
    if finding is None:
        raise ReviewInputError(
            "FINDING_NOT_IN_REVIEW", "该 finding 不属于本次复核的质检"
        )

    cleaned_reason = _clean_reason(reason)
    effective = effective_machine_verdict(finding, inspection_view)
    final = resolve_final_verdict(action, effective, final_verdict)

    previous_status = review.status
    decision = ReviewDecision(
        decision_id=uuid4(),
        review_id=review.review_id,
        finding_id=finding.finding_id,
        action=ReviewAction(action).value,
        final_verdict=final,
        reason=cleaned_reason,
        actor_id=identity.user_id,
        expected_revision=expected_revision,
        accepted_revision=new_revision,
        created_at=moment,
    )
    session.add(decision)
    review.status = ReviewStatus.IN_REVIEW.value
    review.revision = new_revision
    review.updated_at = moment
    _audit(
        session,
        identity=identity,
        review=review,
        action=ACTION_REVIEW_DECISION,
        trace_id=trace_id,
        before={"revision": expected_revision, "status": previous_status},
        after={
            "revision": new_revision,
            "finding_id": str(finding.finding_id),
            "action": decision.action,
            "final_verdict": final,
        },
    )
    session.flush()
    return review, decision


def complete_review(
    session: Session,
    identity: Identity,
    review_id: UUID,
    *,
    expected_revision: int,
    pending_business_confirmation: bool,
    trace_id: str,
    now: Callable[[], datetime] | None = None,
) -> ReviewView:
    """完成复核；每条适用规则必须有最终结论，证据不足需显式标记待业务确认。"""
    moment = _clock(now)
    review = _load_review(session, identity, review_id, for_update=True)
    if review.status == ReviewStatus.COMPLETED.value:
        raise ReviewConflict(
            "REVIEW_COMPLETED",
            "复核已经完成",
            current_revision=review.revision,
        )
    try:
        ensure_transition(ReviewStatus(review.status), ReviewStatus.COMPLETED)
    except ReviewStateError as exc:
        raise ReviewConflict(
            "INVALID_STATE",
            str(exc),
            current_revision=review.revision,
        ) from None
    try:
        new_revision = next_revision(
            current=review.revision, expected=expected_revision
        )
    except ReviewRevisionConflict as exc:
        raise ReviewConflict(
            "REVISION_CONFLICT",
            "复核已被其他操作更新：revision 已过期",
            current_revision=exc.current,
            details={"current_revision": exc.current, "expected_revision": exc.expected},
        ) from None

    view = build_review_view(session, identity, review)
    try:
        ensure_completable(
            view.completion,
            pending_business_confirmation=pending_business_confirmation,
        )
    except CompletionBlocked as exc:
        raise ReviewConflict(
            exc.code,
            (
                "仍有适用规则缺少最终人工结论"
                if exc.code == "REVIEW_INCOMPLETE"
                else "仍有证据不足项，需显式标记待业务确认"
            ),
            current_revision=review.revision,
            details={
                "current_revision": review.revision,
                "finding_ids": [str(finding_id) for finding_id in exc.finding_ids],
            },
        ) from None

    previous_status = review.status
    review.status = ReviewStatus.COMPLETED.value
    review.revision = new_revision
    review.updated_at = moment
    _audit(
        session,
        identity=identity,
        review=review,
        action=ACTION_REVIEW_COMPLETED,
        trace_id=trace_id,
        before={"status": previous_status, "revision": expected_revision},
        after={
            "status": review.status,
            "revision": new_revision,
            "applicable_count": len(view.completion.applicable_finding_ids),
            "decided_count": len(view.completion.applicable_finding_ids)
            - len(view.completion.missing_finding_ids),
            "pending_business_confirmation": bool(
                view.completion.pending_confirmation_finding_ids
            ),
            "pending_confirmation_count": len(
                view.completion.pending_confirmation_finding_ids
            ),
        },
    )
    session.flush()
    return view


def reopen_review(
    session: Session,
    identity: Identity,
    review_id: UUID,
    *,
    expected_revision: int,
    reason: str,
    trace_id: str,
    now: Callable[[], datetime] | None = None,
) -> ReviewView:
    """显式重新打开已完成的复核：新 revision + 审计，历史一律保留。"""
    moment = _clock(now)
    review = _load_review(session, identity, review_id, for_update=True)
    try:
        ensure_transition(ReviewStatus(review.status), ReviewStatus.IN_REVIEW)
    except ReviewStateError as exc:
        raise ReviewConflict(
            "INVALID_STATE",
            str(exc),
            current_revision=review.revision,
        ) from None
    try:
        new_revision = next_revision(
            current=review.revision, expected=expected_revision
        )
    except ReviewRevisionConflict as exc:
        raise ReviewConflict(
            "REVISION_CONFLICT",
            "复核已被其他操作更新：revision 已过期",
            current_revision=exc.current,
            details={"current_revision": exc.current, "expected_revision": exc.expected},
        ) from None

    cleaned_reason = _clean_reason(reason)
    previous_status = review.status
    review.status = ReviewStatus.IN_REVIEW.value
    review.revision = new_revision
    review.last_reopen_reason = cleaned_reason
    review.updated_at = moment
    _audit(
        session,
        identity=identity,
        review=review,
        action=ACTION_REVIEW_REOPENED,
        trace_id=trace_id,
        before={"status": previous_status, "revision": expected_revision},
        after={
            "status": review.status,
            "revision": new_revision,
            # 完整理由属于当前状态；审计摘要只放长度，避免把长文本写进审计。
            "reason_length": len(cleaned_reason),
        },
    )
    session.flush()
    return build_review_view(session, identity, review)
