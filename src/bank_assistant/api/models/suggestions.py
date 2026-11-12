"""候选答复 API schema（契约 §5 ``POST /suggestions``、``/feedback`` 与单条查询）。

反馈响应刻意不包含任何正确性标签字段：``adopt`` 只是坐席动作，不是评测用的
ground-truth。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bank_assistant.assistants.candidate_answer import Citation
from bank_assistant.assistants.suggestion_feedback import FeedbackAction


class SuggestionCreateRequest(BaseModel):
    """输入：会话或原始问题至少一个；业务类型可选。"""

    conversation_id: UUID | None = Field(None, description="会话 ID；与 query 至少提供一个")
    query: str | None = Field(None, max_length=4000, description="原始问题")
    business_type: str | None = Field(None, max_length=64, description="业务类型（可选）")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_conversation_or_query(self) -> "SuggestionCreateRequest":
        has_query = bool(self.query and self.query.strip())
        if self.conversation_id is None and not has_query:
            raise ValueError("必须提供 conversation_id 或非空 query")
        return self


class SuggestionPublic(BaseModel):
    """候选答复响应：状态、答复、已校验引用与检索快照。"""

    suggestion_id: UUID
    conversation_id: UUID | None = None
    run_id: UUID
    query: str
    rewritten_query: str | None = None
    business_type: str | None = None
    status: str
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    retrieval_snapshot: dict[str, Any] = Field(default_factory=dict)
    mode: str
    created_at: datetime


def to_public(suggestion: Any) -> SuggestionPublic:
    """ORM → 响应；``citations`` 已经是后端校验通过的白名单。"""
    return SuggestionPublic(
        suggestion_id=suggestion.suggestion_id,
        conversation_id=suggestion.conversation_id,
        run_id=suggestion.run_id,
        query=suggestion.query,
        rewritten_query=suggestion.rewritten_query,
        business_type=suggestion.business_type,
        status=suggestion.status,
        answer=suggestion.answer,
        citations=[Citation.model_validate(item) for item in suggestion.citations],
        missing_information=list(suggestion.missing_information or []),
        retrieval_snapshot=dict(suggestion.retrieval_snapshot or {}),
        mode=suggestion.mode,
        created_at=suggestion.created_at,
    )


class SuggestionFeedbackRequest(BaseModel):
    """坐席反馈输入：动作必填；edit 需编辑文本，reject 需理由（服务层校验）。

    ``action`` 用枚举在入口即拒绝非法值；``edited_text`` / ``reason`` 的
    条件必填由服务层统一判定，保证服务与 HTTP 口径一致。
    """

    action: FeedbackAction
    edited_text: str | None = Field(None, max_length=4000, description="edit 的编辑文本")
    reason: str | None = Field(None, max_length=2000, description="反馈理由")

    model_config = ConfigDict(extra="forbid")


class SuggestionFeedbackPublic(BaseModel):
    """反馈响应：只描述坐席动作，不含任何正确性标签字段。"""

    feedback_id: UUID
    suggestion_id: UUID
    actor_id: UUID
    action: str
    edited_text: str | None = None
    reason: str | None = None
    created_at: datetime


class SuggestionDetail(SuggestionPublic):
    """单条建议 + 反馈史；反馈挂在具体 suggestion_id 上，可逐条追溯。"""

    feedback: list[SuggestionFeedbackPublic] = Field(default_factory=list)


def feedback_to_public(feedback: Any) -> SuggestionFeedbackPublic:
    """ORM → 反馈响应；不做任何“正确/错误”推断。"""
    return SuggestionFeedbackPublic(
        feedback_id=feedback.feedback_id,
        suggestion_id=feedback.suggestion_id,
        actor_id=feedback.actor_id,
        action=feedback.action,
        edited_text=feedback.edited_text,
        reason=feedback.reason,
        created_at=feedback.created_at,
    )


def to_detail(suggestion: Any, feedback: Iterable[Any]) -> SuggestionDetail:
    """建议 + 反馈史；反馈按写入顺序返回。"""
    return SuggestionDetail(
        **to_public(suggestion).model_dump(),
        feedback=[feedback_to_public(item) for item in feedback],
    )


__all__ = [
    "SuggestionCreateRequest",
    "SuggestionDetail",
    "SuggestionFeedbackPublic",
    "SuggestionFeedbackRequest",
    "SuggestionPublic",
    "feedback_to_public",
    "to_detail",
    "to_public",
]
