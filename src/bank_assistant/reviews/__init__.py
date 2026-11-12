"""人工复核闭环（M4-04/T20，#21；契约 §2/§3/§5/§7）。

- :mod:`bank_assistant.reviews.schema`：状态机、revision 算术与完成门禁（纯逻辑）；
- :mod:`bank_assistant.reviews.service`：review_cases / review_decisions 的读写、
  乐观并发与追加式审计。

机器判断（``findings.machine_verdict``）只读；人工结论只追加到
``review_decisions``，绝不改写原判断。
"""

from __future__ import annotations

from bank_assistant.reviews.schema import (
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
    latest_decisions,
    next_revision,
    resolve_final_verdict,
)

__all__ = [
    "CompletionBlocked",
    "CompletionCheck",
    "DecisionSnapshot",
    "FindingSnapshot",
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
