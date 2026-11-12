"""人工复核 API 的请求/响应模型（契约 §5 ``/reviews``，M4-04/T20）。

- 请求只接受业务字段；``status``/``revision``/``accepted_revision`` 全部由
  服务端生成，客户端不能直接指定。
- 详情同时给出**原机器判断**（``machine_verdict``，只读）与读取路径回验后的
  ``effective_machine_verdict``，以及追加保存的人工决定历史；机器判断永远不
  被人工结论覆盖。
- 完成态继续暴露 ``pending_business_confirmation`` 与具体 finding，供报告数据
  原样列出「待业务确认」，不悄悄转成 clear。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bank_assistant.infra.models import ReviewCase, ReviewDecision
from bank_assistant.inspections.evidence import annotate_evidence
from bank_assistant.reviews.service import ReviewFindingView, ReviewPage, ReviewView

ReviewActionLiteral = Literal["confirm", "reject", "modify"]
ReviewVerdictLiteral = Literal[
    "hit", "clear", "insufficient_evidence", "not_applicable"
]
ReviewStatusLiteral = Literal["pending", "in_review", "completed"]


class ReviewCreateRequest(BaseModel):
    """``POST /reviews`` 输入：为某个已成功的质检指派一名复核员。"""

    model_config = ConfigDict(extra="forbid")

    inspection_id: UUID = Field(..., description="已完成的质检 ID")
    assignee_id: UUID = Field(..., description="复核员（同一业务范围内）")


class ReviewDecisionRequest(BaseModel):
    """``POST /reviews/{id}/decisions`` 输入；confirm/reject/modify 都必须带理由。"""

    model_config = ConfigDict(extra="forbid")

    finding_id: UUID = Field(..., description="被复核的 finding")
    action: ReviewActionLiteral = Field(..., description="confirm / reject / modify")
    final_verdict: ReviewVerdictLiteral | None = Field(
        default=None,
        description="仅 modify 使用：人工改判后的最终结论",
    )
    reason: str = Field(..., min_length=1, max_length=2000, description="必填理由")
    expected_revision: int = Field(..., ge=1, description="提交时的复核 revision")


class ReviewCompleteRequest(BaseModel):
    """``POST /reviews/{id}/complete`` 输入。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1, description="提交时的复核 revision")
    pending_business_confirmation: bool = Field(
        default=False,
        description="仍有证据不足项时，显式确认它们作为「待业务确认」保留",
    )


class ReviewReopenRequest(BaseModel):
    """``POST /reviews/{id}/reopen`` 输入；理由必填并写入审计。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=1, description="提交时的复核 revision")
    reason: str = Field(..., min_length=1, max_length=2000, description="重新打开理由")


class ReviewDecisionPublic(BaseModel):
    """一条追加保存的人工决定（历史条目，不可修改）。"""

    decision_id: UUID
    review_id: UUID
    finding_id: UUID
    action: str
    final_verdict: str
    reason: str
    actor_id: UUID
    expected_revision: int
    accepted_revision: int
    created_at: datetime


class ReviewFindingPublic(BaseModel):
    """复核详情里的一条 finding：原机器判断 + 有效判断 + 最新人工结论。"""

    finding_id: UUID
    rule_version_id: UUID
    rule_id: UUID | None = None
    rule_code: str | None = None
    version_no: int | None = None
    severity: str
    machine_verdict: str = Field(..., description="原始机器判断，只读、不可覆盖")
    effective_machine_verdict: str = Field(
        ..., description="读取路径回验证据后的判断；无效证据的 hit 降级为证据不足"
    )
    evidence_valid: bool = True
    rationale: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_refs: list[dict[str, Any]] = Field(default_factory=list)
    applicable: bool = True
    final_verdict: str | None = None
    latest_action: str | None = None
    latest_decision_id: UUID | None = None
    decision_count: int = 0


class ReviewDetail(BaseModel):
    """复核详情：原判断、人工决定历史、完成门禁事实与 revision。"""

    review_id: UUID
    inspection_id: UUID
    inspection_status: str
    assignee_id: UUID
    status: str
    revision: int
    last_reopen_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    applicable_finding_count: int
    decided_finding_count: int
    missing_decision_finding_ids: list[UUID] = Field(default_factory=list)
    pending_business_confirmation: bool = False
    pending_confirmation_finding_ids: list[UUID] = Field(default_factory=list)
    findings: list[ReviewFindingPublic] = Field(default_factory=list)
    decisions: list[ReviewDecisionPublic] = Field(
        default_factory=list, description="追加式决定历史，按 accepted_revision 升序"
    )


class ReviewSummary(BaseModel):
    """复核列表项；不含会话正文或模型原始输出。"""

    review_id: UUID
    inspection_id: UUID
    assignee_id: UUID
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class ReviewList(BaseModel):
    """``GET /reviews`` 响应：过滤后的总数与当前页。"""

    items: list[ReviewSummary] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class ReviewDecisionAccepted(BaseModel):
    """接受逐项决定后的复核 version 与决定本身。"""

    review_id: UUID
    inspection_id: UUID
    status: str
    revision: int
    decision: ReviewDecisionPublic


def to_decision(decision: ReviewDecision) -> ReviewDecisionPublic:
    return ReviewDecisionPublic(
        decision_id=decision.decision_id,
        review_id=decision.review_id,
        finding_id=decision.finding_id,
        action=decision.action,
        final_verdict=decision.final_verdict,
        reason=decision.reason,
        actor_id=decision.actor_id,
        expected_revision=decision.expected_revision,
        accepted_revision=decision.accepted_revision,
        created_at=decision.created_at,
    )


def _finding_public(
    finding: ReviewFindingView, view: ReviewView
) -> ReviewFindingPublic:
    rule = finding.rule
    latest = finding.latest_decision
    return ReviewFindingPublic(
        finding_id=finding.finding.finding_id,
        rule_version_id=finding.finding.rule_version_id,
        rule_id=rule.rule_id if rule is not None else None,
        rule_code=rule.rule_code if rule is not None else None,
        version_no=rule.version_no if rule is not None else None,
        severity=finding.finding.severity,
        machine_verdict=finding.finding.machine_verdict,
        effective_machine_verdict=finding.effective_verdict,
        evidence_valid=finding.evidence_valid,
        rationale=finding.finding.rationale,
        evidence=annotate_evidence(
            list(finding.finding.evidence or []),
            turn_contents=view.inspection_view.turn_contents,
            turn_ordinals=view.inspection_view.turn_ordinals,
        ),
        knowledge_refs=list(finding.finding.knowledge_refs or []),
        applicable=finding.applicable,
        final_verdict=latest.final_verdict if latest is not None else None,
        latest_action=latest.action if latest is not None else None,
        latest_decision_id=latest.decision_id if latest is not None else None,
        decision_count=len(finding.decisions),
    )


def to_detail(view: ReviewView) -> ReviewDetail:
    review = view.review
    completion = view.completion
    return ReviewDetail(
        review_id=review.review_id,
        inspection_id=review.inspection_id,
        inspection_status=view.inspection.status,
        assignee_id=review.assignee_id,
        status=review.status,
        revision=review.revision,
        last_reopen_reason=review.last_reopen_reason,
        created_at=review.created_at,
        updated_at=review.updated_at,
        applicable_finding_count=len(completion.applicable_finding_ids),
        decided_finding_count=len(completion.applicable_finding_ids)
        - len(completion.missing_finding_ids),
        missing_decision_finding_ids=list(completion.missing_finding_ids),
        pending_business_confirmation=completion.has_pending_confirmation,
        pending_confirmation_finding_ids=list(
            completion.pending_confirmation_finding_ids
        ),
        findings=[_finding_public(finding, view) for finding in view.findings],
        decisions=[to_decision(decision) for decision in view.decisions],
    )


def to_summary(review: ReviewCase) -> ReviewSummary:
    return ReviewSummary(
        review_id=review.review_id,
        inspection_id=review.inspection_id,
        assignee_id=review.assignee_id,
        status=review.status,
        revision=review.revision,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def to_list(page: ReviewPage) -> ReviewList:
    return ReviewList(
        items=[to_summary(review) for review in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


def to_accepted(review: ReviewCase, decision: ReviewDecision) -> ReviewDecisionAccepted:
    return ReviewDecisionAccepted(
        review_id=review.review_id,
        inspection_id=review.inspection_id,
        status=review.status,
        revision=review.revision,
        decision=to_decision(decision),
    )


__all__ = [
    "ReviewActionLiteral",
    "ReviewCompleteRequest",
    "ReviewCreateRequest",
    "ReviewDecisionAccepted",
    "ReviewDecisionPublic",
    "ReviewDecisionRequest",
    "ReviewDetail",
    "ReviewFindingPublic",
    "ReviewList",
    "ReviewReopenRequest",
    "ReviewStatusLiteral",
    "ReviewSummary",
    "ReviewVerdictLiteral",
    "to_accepted",
    "to_decision",
    "to_detail",
    "to_list",
    "to_summary",
]
