"""会话与轮次的持久化服务（契约 §2、§4；M2-04）。

设计要点：

- 解析后的 ordinal 原样入库，**绝不重新编号**；重复/非法 ordinal 直接拒绝，
  避免把同一会话的轮次顺序悄悄改写；
- ``conversations.raw_text`` 与 ``turns.content`` 是不可变原文，本模块没有
  改写入口；:func:`replace_turn_content` 作为唯一“更正”入口会显式失败，
  要求以新会话追加记录；
- 创建会话与审计事件在同一事务提交，审计摘要只含元数据（见
  :mod:`bank_assistant.services.audit`）；
- 读取返回原文，展示脱敏由响应层完成（见 :mod:`bank_assistant.services.redaction`），
  因此证据坐标始终指向不可变原文。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import Conversation, Turn
from bank_assistant.security.identity import Identity
from bank_assistant.services.audit import (
    ACTION_CONVERSATION_CREATED,
    ACTION_CONVERSATION_READ,
    RESOURCE_TYPE_CONVERSATION,
    append_audit_event,
    conversation_summary,
)
from bank_assistant.services.redaction import redact_sensitive_text


class ConversationIntegrityError(ValueError):
    """解析结果不满足持久化约束（ordinal 重复/非法、轮次为空）。"""


class TurnImmutableError(Exception):
    """试图改写已存储的轮次原文；契约 §2 明确禁止。"""


@dataclass(frozen=True, slots=True)
class ConversationView:
    """会话与按 ordinal 排序的轮次；读取与创建共用同一视图。"""

    conversation: Conversation
    turns: tuple[Turn, ...]


def _as_utc(value: datetime) -> datetime:
    """所有轮次时间以 UTC 存储；无时区输入按 UTC 解释（契约 §1）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ordered_turns(parsed: Any) -> list[Any]:
    """校验解析结果的 ordinal：正整数、互不重复，顺序按解析给出的顺序。"""
    turns = list(parsed.turns)
    if not turns:
        raise ConversationIntegrityError("会话至少需要一个轮次")
    ordinals = [getattr(turn, "ordinal", None) for turn in turns]
    for ordinal in ordinals:
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ConversationIntegrityError("轮次 ordinal 必须是正整数")
    if len(set(ordinals)) != len(ordinals):
        raise ConversationIntegrityError("轮次 ordinal 在会话内必须唯一")
    return turns


def build_conversation_entities(
    parsed: Any,
    *,
    owner_id: UUID,
    scope_id: str,
    source_type: str,
    source_ref: str | None,
    data_classification: str,
) -> tuple[Conversation, list[Turn]]:
    """把 T03 的解析结果转成待持久化实体；只做映射，不访问数据库。"""
    turns_in = _ordered_turns(parsed)
    raw_text = parsed.raw_text
    now = datetime.now(timezone.utc)

    conversation = Conversation(
        conversation_id=uuid4(),
        scope_id=scope_id,
        owner_id=owner_id,
        source_type=source_type,
        raw_text=raw_text,
        source_ref=source_ref,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        data_classification=data_classification,
        redaction_applied=redact_sensitive_text(raw_text).applied,
        created_at=now,
        updated_at=now,
    )
    turns = [
        Turn(
            turn_id=uuid4(),
            conversation_id=conversation.conversation_id,
            ordinal=int(turn.ordinal),
            speaker=turn.speaker,
            content=turn.content,
            timestamp=_as_utc(turn.timestamp),
        )
        for turn in turns_in
    ]
    return conversation, turns


def create_conversation(
    session: Session,
    *,
    identity: Identity,
    parsed: Any,
    source_type: str,
    source_ref: str | None,
    data_classification: str,
    trace_id: str,
) -> ConversationView:
    """创建会话、轮次与审计事件；三者同一事务提交，任一失败整体回滚。"""
    conversation, turns = build_conversation_entities(
        parsed,
        owner_id=identity.user_id,
        scope_id=identity.scope_id,
        source_type=source_type,
        source_ref=source_ref,
        data_classification=data_classification,
    )
    session.add(conversation)
    session.add_all(turns)
    append_audit_event(
        session,
        actor_id=identity.user_id,
        action=ACTION_CONVERSATION_CREATED,
        resource_type=RESOURCE_TYPE_CONVERSATION,
        resource_id=str(conversation.conversation_id),
        trace_id=trace_id,
        after_summary=conversation_summary(conversation, turns),
    )
    session.commit()
    return ConversationView(conversation=conversation, turns=tuple(turns))


def load_conversation(session: Session, conversation_id: UUID) -> ConversationView | None:
    """按 ID 读取会话与轮次；不存在返回 ``None``，由路由统一做 404 判定。"""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    turns = session.scalars(
        select(Turn)
        .where(Turn.conversation_id == conversation_id)
        .order_by(Turn.ordinal)
    ).all()
    return ConversationView(conversation=conversation, turns=tuple(turns))


def record_conversation_read(
    session: Session,
    view: ConversationView,
    *,
    identity: Identity,
    trace_id: str,
) -> None:
    """记录授权读取；只追加审计，不修改会话内容。"""
    append_audit_event(
        session,
        actor_id=identity.user_id,
        action=ACTION_CONVERSATION_READ,
        resource_type=RESOURCE_TYPE_CONVERSATION,
        resource_id=str(view.conversation.conversation_id),
        trace_id=trace_id,
        after_summary=conversation_summary(view.conversation, view.turns),
    )
    session.commit()


def replace_turn_content(
    session: Session | None,
    *,
    turn_id: UUID,
    content: str,
) -> None:
    """轮次原文不可变的唯一服务层写入口：永远拒绝，绝不静默改写。

    需要更正时只能以新会话/追加记录表达；证据坐标依赖原文稳定（契约 §4）。
    """
    raise TurnImmutableError(
        "轮次原文不可变；请以新会话追加记录，不能改写已有 turn.content"
    )
