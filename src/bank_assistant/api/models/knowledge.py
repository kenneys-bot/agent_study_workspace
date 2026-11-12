"""知识生命周期 API 的请求/响应模型（契约 §5，M2-03）。

输入与实体分开校验：``scope_id``、``author_id``、``status``、``content_hash``
都不在请求模型里，服务端从身份与内容自行计算，客户端无法自报权限或哈希。
时间统一要求带时区（契约 §1 存储 UTC）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from bank_assistant.knowledge.lifecycle import SourceType


class CreateDocumentRequest(BaseModel):
    """新建知识文档，或在已有文档下追加一个新草稿版本。

    ``document_id`` 省略表示新文档（版本号从 1 开始），此时必须提供
    title/source_type/business_type；提供 ``document_id`` 表示给该文档追加版本
    ——契约 §3 要求“修改已发布内容要创建新版本”。文档级元数据（标题、来源、
    业务范围、访问范围）在创建时确定，追加版本不会通过请求体静默改写它们，
    避免借新版本扩大已发布知识的业务范围。
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: SourceType | None = None
    source_uri: str | None = Field(default=None, max_length=512)
    business_type: str | None = Field(default=None, min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=200_000)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    document_id: UUID | None = None

    @model_validator(mode="after")
    def _new_document_requires_metadata(self) -> "CreateDocumentRequest":
        if self.document_id is None:
            missing = [
                name
                for name in ("title", "source_type", "business_type")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    "新建文档必须提供 " + "、".join(missing)
                )
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("有效期结束必须晚于开始")
        return self


class SubmitVersionRequest(BaseModel):
    """提交审核：``draft → in_review``，revision 做乐观并发校验。"""

    revision: int = Field(..., ge=1)


class PublishVersionRequest(BaseModel):
    """审核决定：approve 发布，reject 退回 draft；审核意见必填。"""

    revision: int = Field(..., ge=1)
    review_reason: str = Field(..., min_length=1, max_length=2000)
    decision: Literal["approve", "reject"] = "approve"


class RetireVersionRequest(BaseModel):
    """退役：``published → retired``，理由必填并留痕。"""

    revision: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=2000)


class DocumentSummary(BaseModel):
    """文档来源与适用范围（响应里可安全展示给授权用户）。"""

    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    title: str
    source_type: str
    source_uri: str | None
    business_type: str
    scope_id: str
    created_at: datetime
    updated_at: datetime


class VersionResponse(BaseModel):
    """版本原文、状态、有效期与来源；只有授权范围内可读。"""

    version_id: UUID
    document_id: UUID
    version_no: int
    content: str
    content_hash: str
    status: str
    valid_from: datetime
    valid_to: datetime | None
    author_id: UUID
    reviewer_id: UUID | None
    review_reason: str | None
    retire_reason: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    document: DocumentSummary


class CreateDocumentResponse(BaseModel):
    """创建结果：文档 + 草稿版本 ID（契约 §5）。"""

    document_id: UUID
    document: DocumentSummary
    version_id: UUID
    version_no: int
    status: str
    content_hash: str
    revision: int


class PublishResponse(VersionResponse):
    """发布/退回结果；冲突版本被显式退役时在 ``superseded_version_ids`` 列出。"""

    outcome: Literal["published", "returned_to_draft"]
    superseded_version_ids: list[UUID] = Field(default_factory=list)
