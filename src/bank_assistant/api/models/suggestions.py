"""候选答复 API schema（契约 §5 ``POST /suggestions``）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bank_assistant.assistants.candidate_answer import Citation


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


__all__ = ["SuggestionCreateRequest", "SuggestionPublic", "to_public"]
