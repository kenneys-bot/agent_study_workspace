"""规则生命周期 API 的请求/响应模型（契约 §2/§5，M4-01）。

输入与实体分开校验：``scope_id``、``author_id``、``status``、``content_hash``、
``version_no`` 都不在请求模型里，服务端从身份与内容自行计算。``applicability``
只提交非时间部分；有效期窗口用 ``valid_from``/``valid_to``，客户端不能把
窗口塞进 ``applicability`` 冒充。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from bank_assistant.rules.schema import (
    RuleApplicability,
    RuleCriteria,
    RuleExamples,
    RuleSourceRef,
)


class CreateRuleRequest(BaseModel):
    """新建规则身份 + 草稿版本，或给已有规则追加草稿版本。

    省略 ``rule_id`` 表示新建规则，必须同时提供 rule_code/title/category/
    description 并显式声明 ``demo_only``；提供 ``rule_id`` 表示追加版本，此时
    规则身份字段一律禁止提交（不能借新版本改写类别、说明或演示标记）。
    """

    # 规则身份（仅新建时需要）
    rule_code: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    demo_only: bool | None = None

    # 版本内容（新建与追加都需要）
    rule_id: UUID | None = None
    business_type: str = Field(..., min_length=1, max_length=64)
    applicability: RuleApplicability
    criteria: RuleCriteria
    exceptions: list[str] = Field(..., min_length=1)
    severity: Literal["low", "medium", "high"]
    source_refs: list[RuleSourceRef] = Field(..., min_length=1)
    examples: RuleExamples
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def _rule_identity_must_match_mode(self) -> "CreateRuleRequest":
        identity_fields = (
            "rule_code",
            "title",
            "category",
            "description",
            "demo_only",
        )
        if self.rule_id is None:
            missing = [
                name for name in identity_fields if getattr(self, name) is None
            ]
            if missing:
                raise ValueError("新建规则必须提供 " + "、".join(missing))
        else:
            provided = [
                name for name in identity_fields if getattr(self, name) is not None
            ]
            if provided:
                raise ValueError(
                    "追加版本不能同时提交规则身份字段：" + "、".join(provided)
                )
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("有效期结束必须晚于开始")
        return self


class SubmitRuleVersionRequest(BaseModel):
    """提交审核：``draft → in_review``，revision 做乐观并发校验。"""

    revision: int = Field(..., ge=1)


class PublishRuleVersionRequest(BaseModel):
    """审核决定：approve 发布，reject 退回 draft；审核意见必填。"""

    revision: int = Field(..., ge=1)
    review_reason: str = Field(..., min_length=1, max_length=2000)
    decision: Literal["approve", "reject"] = "approve"


class RetireRuleVersionRequest(BaseModel):
    """退役：``published → retired``，理由必填并留痕。"""

    revision: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=2000)


class RuleVersionResponse(BaseModel):
    """规则版本内容、状态与适用范围；``applicability`` 含时间窗口。"""

    rule_version_id: UUID
    rule_id: UUID
    rule_code: str
    title: str
    category: str
    description: str
    version_no: int
    business_type: str
    applicability: dict[str, Any]
    criteria: dict[str, Any]
    exceptions: list[str]
    severity: str
    source_refs: list[dict[str, Any]]
    examples: dict[str, Any]
    content_hash: str
    status: str
    demo_only: bool
    valid_from: datetime
    valid_to: datetime | None
    author_id: UUID
    reviewer_id: UUID | None
    review_reason: str | None
    retire_reason: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


class CreateRuleResponse(BaseModel):
    """创建结果：规则身份 + 草稿版本 ID（契约 §5）。"""

    rule_id: UUID
    rule_version_id: UUID
    rule_code: str
    version_no: int
    status: str
    demo_only: bool
    content_hash: str
    revision: int


class PublishRuleResponse(RuleVersionResponse):
    """发布/退回结果；重叠版本被显式退役时在 ``superseded_rule_version_ids`` 列出。"""

    outcome: Literal["published", "returned_to_draft"]
    superseded_rule_version_ids: list[UUID] = Field(default_factory=list)


__all__ = [
    "CreateRuleRequest",
    "CreateRuleResponse",
    "PublishRuleResponse",
    "PublishRuleVersionRequest",
    "RetireRuleVersionRequest",
    "RuleVersionResponse",
    "SubmitRuleVersionRequest",
]
