"""人工复核的纯逻辑：状态机、revision 算术与完成门禁（M4-04/T20，#21）。

契约 §2 的 ReviewCase / ReviewDecision 与 §3「人工复核」在这里落成可单测的
规则，不访问数据库、不调用模型：

- 状态只允许 ``pending → in_review → completed``；重新打开是唯一显式回退，
  只能 ``completed → in_review``，并产生新 revision 与审计；
- 每个被接受的逐项决定恰好把 revision 加一；``expected_revision`` 过期时
  报出当前 revision（HTTP 层映射为 409，详见 ``service``/``router``）；
- 逐项动作语义固定：confirm 保留机器判断、reject 明确驳回为 clear、
  modify 必须给出与机器判断不同的 final_verdict；
- 完成门禁：每条适用规则（``machine_verdict != not_applicable``）都必须有
  最终人工结论；仍有 ``insufficient_evidence`` 时，只有显式
  「待业务确认」标记才允许完成，且该状态保持可见，绝不悄悄改成 clear。

数据库读写与审计见 :mod:`bank_assistant.reviews.service`。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping, Sequence
from uuid import UUID

__all__ = [
    "ACTIONS",
    "CompletionBlocked",
    "CompletionCheck",
    "DecisionSnapshot",
    "FindingSnapshot",
    "REVIEW_VERDICTS",
    "ReviewAction",
    "ReviewInputError",
    "ReviewRevisionConflict",
    "ReviewStateError",
    "ReviewStatus",
    "ensure_completable",
    "ensure_transition",
    "evaluate_completion",
    "latest_decisions",
    "next_revision",
    "resolve_final_verdict",
]

# 与 findings / ReviewDecision 的最终结论共用同一组取值（契约 §3）。
NOT_APPLICABLE = "not_applicable"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
CLEAR = "clear"

REVIEW_VERDICTS: tuple[str, ...] = (
    "hit",
    "clear",
    "insufficient_evidence",
    "not_applicable",
)

ACTIONS: tuple[str, ...] = ("confirm", "reject", "modify")


class ReviewStatus(StrEnum):
    """契约 §3 人工复核状态：``pending → in_review → completed``。"""

    PENDING = "pending"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"


class ReviewAction(StrEnum):
    """逐项复核动作；每个动作都必须带理由。"""

    CONFIRM = "confirm"
    REJECT = "reject"
    MODIFY = "modify"


# 允许的状态迁移。``pending → completed`` 只在没有需要人工结论的适用规则时
# 可达（否则第一条决定已把状态推进到 in_review），它让「全部不适用」的质检
# 可以被明确收口，而不是被困在 pending。
_ALLOWED_TRANSITIONS: frozenset[tuple[ReviewStatus, ReviewStatus]] = frozenset(
    {
        (ReviewStatus.PENDING, ReviewStatus.IN_REVIEW),
        (ReviewStatus.PENDING, ReviewStatus.COMPLETED),
        (ReviewStatus.IN_REVIEW, ReviewStatus.COMPLETED),
        # 显式重新打开：revision 由调用方在同一事务内加一，并写审计。
        (ReviewStatus.COMPLETED, ReviewStatus.IN_REVIEW),
    }
)


class ReviewStateError(ValueError):
    """状态不允许该操作；HTTP 层映射为 409，不静默改写状态。"""

    def __init__(self, current: ReviewStatus, target: ReviewStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"复核状态不允许从 {current.value} 变为 {target.value}")


class ReviewRevisionConflict(Exception):
    """客户端 expected_revision 已过期；必须报出当前 revision。"""

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f"revision 冲突：期望 {expected}，当前 {current}")


class ReviewInputError(ValueError):
    """请求语义不合法（动作/结论/理由）；HTTP 层映射为 422。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CompletionBlocked(Exception):
    """完成门禁未通过；HTTP 层映射为 409 并带上未完成的 finding。"""

    def __init__(self, code: str, finding_ids: Sequence[UUID]) -> None:
        self.code = code
        self.finding_ids = tuple(finding_ids)
        super().__init__(f"复核不能完成：{code}（{len(self.finding_ids)} 条）")


@dataclass(frozen=True, slots=True)
class FindingSnapshot:
    """完成门禁需要的 finding 事实；只读机器判断，不含人工结论。"""

    finding_id: UUID
    machine_verdict: str

    def __post_init__(self) -> None:
        if self.machine_verdict not in REVIEW_VERDICTS:
            raise ValueError(f"未知机器判断: {self.machine_verdict}")


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """一条已接受的人工结论；``accepted_revision`` 唯一标识其先后。"""

    finding_id: UUID
    final_verdict: str
    accepted_revision: int

    def __post_init__(self) -> None:
        if self.final_verdict not in REVIEW_VERDICTS:
            raise ValueError(f"未知人工结论: {self.final_verdict}")
        if self.accepted_revision < 2:
            raise ValueError("accepted_revision 必须 >= 2（创建时为 1）")


@dataclass(frozen=True, slots=True)
class CompletionCheck:
    """完成门禁的事实快照，供 API 展示与错误详情复用。"""

    applicable_finding_ids: tuple[UUID, ...]
    missing_finding_ids: tuple[UUID, ...]
    pending_confirmation_finding_ids: tuple[UUID, ...]

    @property
    def decided(self) -> bool:
        return not self.missing_finding_ids

    @property
    def has_pending_confirmation(self) -> bool:
        return bool(self.pending_confirmation_finding_ids)


def ensure_transition(current: ReviewStatus, target: ReviewStatus) -> None:
    """校验状态迁移；非法迁移显式失败，不做隐式纠正。"""
    current = ReviewStatus(current)
    target = ReviewStatus(target)
    if (current, target) not in _ALLOWED_TRANSITIONS:
        raise ReviewStateError(current, target)


def next_revision(*, current: int, expected: int) -> int:
    """乐观并发：expected 必须等于 current，返回 current + 1。"""
    if current < 1 or expected < 1:
        raise ValueError("revision 必须 >= 1")
    if current != expected:
        raise ReviewRevisionConflict(expected=expected, current=current)
    return current + 1


def resolve_final_verdict(
    action: ReviewAction | str,
    machine_verdict: str,
    requested_final_verdict: str | None,
) -> str:
    """把动作解析成最终结论；机器判断本身永远不被改写。

    - confirm：沿用机器判断（包括 insufficient_evidence，绝不当成 clear）；
    - reject：人工明确驳回为 clear；
    - modify：调用方必须给出与机器判断不同的 final_verdict。
    """
    try:
        action = ReviewAction(action)
    except ValueError:
        raise ReviewInputError(
            "INVALID_REVIEW_ACTION", f"未知复核动作: {action}"
        ) from None
    if machine_verdict not in REVIEW_VERDICTS:
        raise ReviewInputError(
            "INVALID_MACHINE_VERDICT", f"未知机器判断: {machine_verdict}"
        )

    if action is ReviewAction.CONFIRM:
        if requested_final_verdict is not None:
            raise ReviewInputError(
                "FINAL_VERDICT_NOT_ALLOWED", "confirm 不接受 final_verdict"
            )
        return machine_verdict

    if action is ReviewAction.REJECT:
        if requested_final_verdict is not None:
            raise ReviewInputError(
                "FINAL_VERDICT_NOT_ALLOWED", "reject 不接受 final_verdict"
            )
        return CLEAR

    if requested_final_verdict is None:
        raise ReviewInputError(
            "FINAL_VERDICT_REQUIRED", "modify 必须提供新的 final_verdict"
        )
    if requested_final_verdict not in REVIEW_VERDICTS:
        raise ReviewInputError(
            "INVALID_FINAL_VERDICT", f"未知最终结论: {requested_final_verdict}"
        )
    if requested_final_verdict == machine_verdict:
        raise ReviewInputError(
            "FINAL_VERDICT_UNCHANGED", "modify 的 final_verdict 必须不同于机器判断"
        )
    return requested_final_verdict


def latest_decisions(
    decisions: Iterable[DecisionSnapshot],
) -> Mapping[UUID, DecisionSnapshot]:
    """按 finding 归并出最新一条决定；``accepted_revision`` 越大越新。"""
    latest: dict[UUID, DecisionSnapshot] = {}
    for decision in decisions:
        current = latest.get(decision.finding_id)
        if current is None or decision.accepted_revision > current.accepted_revision:
            latest[decision.finding_id] = decision
    return latest


def evaluate_completion(
    findings: Iterable[FindingSnapshot],
    decisions: Iterable[DecisionSnapshot],
) -> CompletionCheck:
    """计算完成门禁：缺结论的适用 finding 与仍需业务确认的 finding。"""
    findings = tuple(findings)
    latest = latest_decisions(decisions)
    applicable = tuple(
        finding.finding_id
        for finding in findings
        if finding.machine_verdict != NOT_APPLICABLE
    )
    missing = tuple(
        finding_id for finding_id in applicable if finding_id not in latest
    )
    pending = tuple(
        finding_id
        for finding_id in applicable
        if finding_id in latest
        and latest[finding_id].final_verdict == INSUFFICIENT_EVIDENCE
    )
    return CompletionCheck(
        applicable_finding_ids=applicable,
        missing_finding_ids=missing,
        pending_confirmation_finding_ids=pending,
    )


def ensure_completable(
    check: CompletionCheck, *, pending_business_confirmation: bool
) -> None:
    """完成门禁：先补齐所有适用规则，再决定是否接受待业务确认标记。"""
    if check.missing_finding_ids:
        raise CompletionBlocked("REVIEW_INCOMPLETE", check.missing_finding_ids)
    if check.pending_confirmation_finding_ids and not pending_business_confirmation:
        raise CompletionBlocked(
            "PENDING_BUSINESS_CONFIRMATION",
            check.pending_confirmation_finding_ids,
        )
