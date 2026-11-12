"""人工复核的纯逻辑单测：状态机、revision 算术与完成门禁（T20，#21）。

这些测试不碰数据库、不调用模型，只锁定契约 §3「人工复核」的规则：

- ``pending → in_review → completed``，重新打开只能从 completed 显式回到
  in_review，并产生新 revision；
- 每次被接受的逐项决定恰好把 revision 加一；``expected_revision`` 过期必须
  报出当前 revision（CT-07 的服务层语义）；
- confirm 保留机器判断、reject 明确驳回为 clear、modify 必须给出不同的
  final_verdict；insufficient_evidence 永远不会被悄悄转成 clear；
- 完成前每条适用规则都必须有最终人工结论；仍有证据不足时，必须显式确认
  「待业务确认」才能完成，且该状态保持可见。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bank_assistant.reviews.schema import (
    CompletionBlocked,
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
    latest_decisions,
    next_revision,
    resolve_final_verdict,
)

# ---------------------------------------------------------------------------
# 状态机与 revision 算术
# ---------------------------------------------------------------------------


def test_review_status_lifecycle_allows_forward_transitions_and_explicit_reopen() -> (
    None
):
    ensure_transition(ReviewStatus.PENDING, ReviewStatus.IN_REVIEW)
    ensure_transition(ReviewStatus.PENDING, ReviewStatus.COMPLETED)
    ensure_transition(ReviewStatus.IN_REVIEW, ReviewStatus.COMPLETED)
    ensure_transition(ReviewStatus.COMPLETED, ReviewStatus.IN_REVIEW)


def test_review_status_lifecycle_forbids_implicit_backwards_or_repeat_transitions() -> (
    None
):
    for current, target in (
        (ReviewStatus.IN_REVIEW, ReviewStatus.PENDING),
        (ReviewStatus.COMPLETED, ReviewStatus.PENDING),
        (ReviewStatus.COMPLETED, ReviewStatus.COMPLETED),
        (ReviewStatus.IN_REVIEW, ReviewStatus.IN_REVIEW),
    ):
        with pytest.raises(ReviewStateError):
            ensure_transition(current, target)


def test_every_accepted_decision_advances_exactly_one_revision() -> None:
    assert next_revision(current=1, expected=1) == 2
    assert next_revision(current=4, expected=4) == 5
    for current in range(1, 20):
        assert next_revision(current=current, expected=current) == current + 1


def test_stale_expected_revision_reports_the_current_revision() -> None:
    with pytest.raises(ReviewRevisionConflict) as excinfo:
        next_revision(current=5, expected=4)

    assert excinfo.value.expected == 4
    assert excinfo.value.current == 5
    assert "5" in str(excinfo.value)


def test_revision_arithmetic_rejects_values_below_one() -> None:
    for current, expected in ((0, 0), (1, 0), (0, 1)):
        with pytest.raises(ValueError):
            next_revision(current=current, expected=expected)


# ---------------------------------------------------------------------------
# 逐项动作 → 最终结论
# ---------------------------------------------------------------------------


def test_confirm_keeps_the_machine_verdict_unchanged() -> None:
    assert resolve_final_verdict(ReviewAction.CONFIRM, "hit", None) == "hit"
    assert resolve_final_verdict(ReviewAction.CONFIRM, "clear", None) == "clear"
    assert (
        resolve_final_verdict(
            ReviewAction.CONFIRM, "insufficient_evidence", None
        )
        == "insufficient_evidence"
    )
    assert (
        resolve_final_verdict(ReviewAction.CONFIRM, "not_applicable", None)
        == "not_applicable"
    )


def test_reject_dismisses_the_risk_clue_as_clear() -> None:
    assert resolve_final_verdict(ReviewAction.REJECT, "hit", None) == "clear"
    assert (
        resolve_final_verdict(ReviewAction.REJECT, "insufficient_evidence", None)
        == "clear"
    )


def test_modify_requires_a_different_final_verdict() -> None:
    assert resolve_final_verdict(ReviewAction.MODIFY, "hit", "clear") == "clear"
    assert (
        resolve_final_verdict(ReviewAction.MODIFY, "hit", "insufficient_evidence")
        == "insufficient_evidence"
    )
    assert resolve_final_verdict(ReviewAction.MODIFY, "hit", "not_applicable") == (
        "not_applicable"
    )


def test_modify_without_a_new_verdict_or_with_the_same_verdict_is_rejected() -> None:
    with pytest.raises(ReviewInputError) as missing:
        resolve_final_verdict(ReviewAction.MODIFY, "hit", None)
    assert missing.value.code == "FINAL_VERDICT_REQUIRED"

    with pytest.raises(ReviewInputError) as unchanged:
        resolve_final_verdict(ReviewAction.MODIFY, "hit", "hit")
    assert unchanged.value.code == "FINAL_VERDICT_UNCHANGED"


def test_confirm_and_reject_cannot_smuggle_a_final_verdict() -> None:
    for action in (ReviewAction.CONFIRM, ReviewAction.REJECT):
        with pytest.raises(ReviewInputError) as excinfo:
            resolve_final_verdict(action, "hit", "clear")
        assert excinfo.value.code == "FINAL_VERDICT_NOT_ALLOWED"


def test_unknown_actions_and_verdicts_are_rejected_explicitly() -> None:
    with pytest.raises(ReviewInputError):
        resolve_final_verdict("approve", "hit", None)
    with pytest.raises(ReviewInputError):
        resolve_final_verdict(ReviewAction.MODIFY, "hit", "passed")


# ---------------------------------------------------------------------------
# 完成门禁
# ---------------------------------------------------------------------------


def test_completion_requires_a_decision_for_every_applicable_finding() -> None:
    hit, clear, insufficient, not_applicable = (uuid4() for _ in range(4))
    findings = (
        FindingSnapshot(hit, "hit"),
        FindingSnapshot(clear, "clear"),
        FindingSnapshot(insufficient, "insufficient_evidence"),
        FindingSnapshot(not_applicable, "not_applicable"),
    )

    check = evaluate_completion(findings, [])

    assert check.applicable_finding_ids == (hit, clear, insufficient)
    assert check.missing_finding_ids == (hit, clear, insufficient)
    with pytest.raises(CompletionBlocked) as excinfo:
        ensure_completable(check, pending_business_confirmation=False)
    assert excinfo.value.code == "REVIEW_INCOMPLETE"
    assert set(excinfo.value.finding_ids) == {hit, clear, insufficient}


def test_not_applicable_findings_do_not_need_a_human_decision() -> None:
    not_applicable = uuid4()
    check = evaluate_completion(
        (FindingSnapshot(not_applicable, "not_applicable"),), []
    )

    assert check.applicable_finding_ids == ()
    assert check.missing_finding_ids == ()
    assert check.pending_confirmation_finding_ids == ()
    ensure_completable(check, pending_business_confirmation=False)


def test_insufficient_evidence_needs_an_explicit_pending_business_confirmation() -> (
    None
):
    finding = uuid4()
    findings = (FindingSnapshot(finding, "insufficient_evidence"),)
    decisions = (DecisionSnapshot(finding, "insufficient_evidence", 2),)

    check = evaluate_completion(findings, decisions)

    assert check.missing_finding_ids == ()
    assert check.pending_confirmation_finding_ids == (finding,)
    with pytest.raises(CompletionBlocked) as excinfo:
        ensure_completable(check, pending_business_confirmation=False)
    assert excinfo.value.code == "PENDING_BUSINESS_CONFIRMATION"
    assert excinfo.value.finding_ids == (finding,)
    # 显式标记后可以完成，但该 finding 仍留在待业务确认列表里，绝不改成 clear。
    ensure_completable(check, pending_business_confirmation=True)
    assert check.pending_confirmation_finding_ids == (finding,)


def test_insufficient_evidence_is_never_silently_turned_into_clear() -> None:
    finding = uuid4()
    final = resolve_final_verdict(ReviewAction.CONFIRM, "insufficient_evidence", None)
    assert final == "insufficient_evidence"

    check = evaluate_completion(
        (FindingSnapshot(finding, "insufficient_evidence"),),
        (DecisionSnapshot(finding, final, 2),),
    )
    assert check.pending_confirmation_finding_ids == (finding,)


def test_the_latest_decision_per_finding_wins_while_history_is_preserved() -> None:
    finding = uuid4()
    first = DecisionSnapshot(finding, "clear", 2)
    second = DecisionSnapshot(finding, "insufficient_evidence", 3)

    latest = latest_decisions((first, second))
    assert latest[finding] is second

    check = evaluate_completion(
        (FindingSnapshot(finding, "hit"),), (first, second)
    )
    assert check.missing_finding_ids == ()
    assert check.pending_confirmation_finding_ids == (finding,)

    # 记录不会被原地修改：修订前的那条决定仍在，且顺序稳定。
    assert list(latest.values()) == [second]


def test_decisions_for_other_findings_do_not_satisfy_the_gate() -> None:
    expected = uuid4()
    other = uuid4()
    check = evaluate_completion(
        (FindingSnapshot(expected, "hit"),),
        (DecisionSnapshot(other, "hit", 2),),
    )

    assert check.missing_finding_ids == (expected,)
    assert check.pending_confirmation_finding_ids == ()
