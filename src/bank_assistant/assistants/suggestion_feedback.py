"""坐席采纳反馈（T16，M3-05，契约 §2/§3/§5）。

坐席对一条候选答复登记 ``adopt`` / ``edit`` / ``reject`` 与理由；反馈通过
``suggestion_id`` 绑定到**当时的具体建议记录**，后续重新生成只会得到新的
suggestion_id，历史不会被改挂。

**反馈不是正确性标签。** ``adopt`` 只表示坐席决定采用（原样或编辑后），不表示
答复在语义上正确；这些记录不得被用作评测的 ground-truth 标签，任何代码都
不得把 adopt 当作“正确”。

本模块只提供追加写入与读取；没有更新/删除入口，表上的 ORM ``before_update``
事件会拒绝任何改写，保证历史可追溯。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.assistants.suggestions import SuggestionNotFound
from bank_assistant.infra.models import Suggestion, SuggestionFeedback
from bank_assistant.security.identity import Identity


class FeedbackAction(StrEnum):
    """坐席反馈动作；只是反馈类别，不是正确性判定。"""

    ADOPT = "adopt"
    EDIT = "edit"
    REJECT = "reject"


class SuggestionFeedbackInputError(Exception):
    """反馈输入不满足约定（动作非法、edit 无文本、reject 无理由等）。"""

    code = "FEEDBACK_INVALID_INPUT"
    http_status = 422


class SuggestionFeedbackService:
    """登记与读取建议反馈；HTTP 与测试共用同一个入口。"""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def record_feedback(
        self,
        session: Session,
        *,
        identity: Identity,
        suggestion_id: UUID,
        action: str,
        edited_text: str | None = None,
        reason: str | None = None,
    ) -> SuggestionFeedback:
        """追加一条反馈；建议不存在或跨范围统一按“不存在”处理。"""
        suggestion = session.get(Suggestion, suggestion_id)
        if suggestion is None or suggestion.scope_id != identity.scope_id:
            # 不存在与不可见返回同一结果，不泄露资源存在性（契约 §5）。
            raise SuggestionNotFound("建议不存在")

        try:
            normalized_action = FeedbackAction(action)
        except ValueError:
            raise SuggestionFeedbackInputError(
                f"不支持的动作: {action!r}"
            ) from None

        edited = edited_text.strip() if edited_text is not None else None
        rationale = reason.strip() if reason is not None else None
        # 编辑文本只属于 edit，理由必须属于 reject；两者不能混入其他动作。
        if normalized_action is FeedbackAction.EDIT and not edited:
            raise SuggestionFeedbackInputError("edit 必须提供编辑后的文本")
        if normalized_action is FeedbackAction.REJECT:
            if not rationale:
                raise SuggestionFeedbackInputError("reject 必须提供理由")
            if edited:
                raise SuggestionFeedbackInputError("reject 不接受编辑文本")
        if normalized_action is FeedbackAction.ADOPT and edited:
            raise SuggestionFeedbackInputError("adopt 不接受编辑文本")

        feedback = SuggestionFeedback(
            suggestion_id=suggestion.suggestion_id,
            actor_id=identity.user_id,
            action=normalized_action.value,
            edited_text=edited,
            reason=rationale,
            created_at=self._clock(),
        )
        session.add(feedback)
        session.commit()
        return feedback

    def suggestion_history(
        self,
        session: Session,
        *,
        identity: Identity,
        suggestion_id: UUID,
    ) -> tuple[Suggestion, Sequence[SuggestionFeedback]]:
        """读取一条建议及其按时间排列的反馈史；跨范围同样按不存在处理。"""
        suggestion = session.get(Suggestion, suggestion_id)
        if suggestion is None or suggestion.scope_id != identity.scope_id:
            raise SuggestionNotFound("建议不存在")
        feedback = list(
            session.scalars(
                select(SuggestionFeedback)
                .where(SuggestionFeedback.suggestion_id == suggestion.suggestion_id)
                .order_by(SuggestionFeedback.created_at)
            )
        )
        return suggestion, feedback


__all__ = [
    "FeedbackAction",
    "SuggestionFeedbackInputError",
    "SuggestionFeedbackService",
]
