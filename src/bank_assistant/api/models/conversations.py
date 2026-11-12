"""会话 API 的请求/响应模型（契约 §5：POST/GET /conversations）。

响应里的 ``turns[].content`` 是**展示文本**：读取时对手机号、身份证、银行卡号
做等长遮蔽。证据坐标（``start``/``end``）始终以数据库中的不可变原文
``turn.content`` 的 Unicode 码点计数（契约 §4），所以响应同时给出：

- ``conversation.redaction_applied``：创建时是否命中敏感片段的存储标志；
- ``turn.original_content_length``：原文码点长度，供调用方核对区间边界。

前端不得把展示文本的索引当作原文坐标复用（契约 §4）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from bank_assistant.services.conversations import ConversationView
from bank_assistant.services.redaction import redact_sensitive_text

SourceType = Literal["manual_paste", "file_upload", "demo_seed"]
DataClassification = Literal["synthetic_demo", "internal"]


class ConversationCreateRequest(BaseModel):
    """导入一段会话：``content`` 为 text 或 JSON 原文，交给 T03 解析器。"""

    content: str = Field(
        ...,
        min_length=1,
        max_length=200_000,
        description="会话原文（text 或 JSON 字符串），按原样保存且不可变",
    )
    format: Literal["text", "json"] = Field(
        "text", description="内容格式：text 或 json"
    )
    source_type: SourceType = Field(
        "manual_paste", description="来源类型：手工粘贴、文件上传或演示种子"
    )
    source_ref: str | None = Field(
        None, max_length=512, description="可选来源引用（文件名/外部记录 ID），不存文件内容"
    )
    data_classification: DataClassification = Field(
        "synthetic_demo", description="数据分级：synthetic_demo 或 internal"
    )
    scope_id: str | None = Field(
        None,
        max_length=64,
        description="客户端声明的目标业务范围；必须与服务端身份一致，否则 403",
    )


class TurnPublic(BaseModel):
    """轮次公开视图：展示文本可能已脱敏，证据坐标以原文为准。"""

    turn_id: UUID
    ordinal: int = Field(..., ge=1, description="会话内唯一轮次序号（按解析结果原样保存）")
    speaker: str
    content: str = Field(..., description="展示文本（可能已脱敏）；原文坐标见 original_content_length")
    original_content_length: int = Field(
        ..., ge=0, description="原文 content 的 Unicode 码点长度（证据坐标基准）"
    )
    timestamp: datetime = Field(..., description="带时区 UTC 时间戳")


class ConversationPublic(BaseModel):
    """会话公开视图（POST 201 与 GET 200 共用）。"""

    conversation_id: UUID
    scope_id: str
    owner_id: UUID
    source_type: str
    source_ref: str | None = None
    content_hash: str = Field(..., description="不可变原文的 SHA-256")
    data_classification: str
    redaction_applied: bool = Field(
        ..., description="创建时是否命中敏感片段并需要遮蔽展示"
    )
    created_at: datetime
    updated_at: datetime
    turns: list[TurnPublic]
    evidence_coordinate_basis: Literal["original_immutable_content"] = Field(
        "original_immutable_content",
        description="证据坐标基准：数据库中的不可变原文，而非脱敏展示文本",
    )


def to_public(view: ConversationView) -> ConversationPublic:
    """把实体视图映射为响应模型；脱敏只发生在这里，永不回写存储。"""
    conversation = view.conversation
    return ConversationPublic(
        conversation_id=conversation.conversation_id,
        scope_id=conversation.scope_id,
        owner_id=conversation.owner_id,
        source_type=conversation.source_type,
        source_ref=conversation.source_ref,
        content_hash=conversation.content_hash,
        data_classification=conversation.data_classification,
        redaction_applied=bool(conversation.redaction_applied),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        turns=[
            TurnPublic(
                turn_id=turn.turn_id,
                ordinal=turn.ordinal,
                # 说话人标签同样按展示文本处理，避免把号码塞进 speaker 绕过遮蔽。
                speaker=redact_sensitive_text(turn.speaker).text,
                content=redact_sensitive_text(turn.content).text,
                original_content_length=len(turn.content),
                timestamp=turn.timestamp,
            )
            for turn in view.turns
        ],
    )
