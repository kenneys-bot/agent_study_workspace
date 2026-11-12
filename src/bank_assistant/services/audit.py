"""追加式审计（契约 §2）：只追加、不更新；摘要绝不包含敏感正文。

审计行记录“谁在什么 trace 下对哪个资源做了什么”。before/after 摘要只允许
短元数据（计数、哈希、状态、时间、范围），由 :func:`conversation_summary`
统一构造，并由 :func:`append_audit_event` 在写入前机械校验：

- 字符串值不得含换行，长度不超过 :data:`MAX_SUMMARY_VALUE_LENGTH`；
- 只接受标量、标量列表与嵌套摘要字典，拒绝任意对象/正文。

这样即使未来调用方误把 ``turn.content`` 放进摘要，也会在写库前失败，而不是
把会话原文复制进审计表。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from bank_assistant.infra.models import AuditEvent, Conversation, Turn
from bank_assistant.services.redaction import redact_sensitive_text

ACTION_CONVERSATION_CREATED = "conversation.created"
ACTION_CONVERSATION_READ = "conversation.read"
RESOURCE_TYPE_CONVERSATION = "conversation"

# 审计值只放短元数据；64 位哈希与 ISO 时间都在此范围内。
MAX_SUMMARY_VALUE_LENGTH = 128
MAX_SUMMARY_LIST_ITEMS = 64

# 纯哈希/UUID 是合法元数据；不能因为里面碰巧出现长数字串就误判为敏感正文。
_HEX_HASH = re.compile(r"^[0-9a-fA-F]{32,64}$")
_UUID_TEXT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class AuditSummaryError(ValueError):
    """审计摘要包含正文或超长文本；拒绝写库，避免把敏感内容存进审计。"""


def _utc_iso(value: datetime) -> str:
    """把时间统一成 UTC ISO 8601 字符串（审计只存 UTC）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _validate_value(value: Any, *, field: str) -> None:
    """递归校验摘要值：只允许短标量与标量列表/摘要字典。"""
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise AuditSummaryError(f"{field} 含换行，审计摘要不得存放正文")
        if len(value) > MAX_SUMMARY_VALUE_LENGTH:
            raise AuditSummaryError(
                f"{field} 超过 {MAX_SUMMARY_VALUE_LENGTH} 字符，审计摘要不得存放正文"
            )
        # 摘要里出现手机号/身份证/卡号等敏感模式，说明正文被误放进审计。
        # 纯哈希与 UUID 先排除，避免误伤合法元数据。
        if (
            not _HEX_HASH.match(value)
            and not _UUID_TEXT.match(value)
            and redact_sensitive_text(value).applied
        ):
            raise AuditSummaryError(f"{field} 含敏感信息，审计摘要不得存放正文")
        return
    if isinstance(value, Mapping):
        _validate_summary(dict(value), field=field)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_SUMMARY_LIST_ITEMS:
            raise AuditSummaryError(f"{field} 列表过长，审计摘要不得存放正文")
        for index, item in enumerate(value):
            _validate_value(item, field=f"{field}[{index}]")
        return
    raise AuditSummaryError(f"{field} 类型 {type(value).__name__} 不允许进入审计摘要")


def _validate_summary(summary: Mapping[str, Any], *, field: str) -> None:
    for key, value in summary.items():
        if not isinstance(key, str) or not key:
            raise AuditSummaryError(f"{field} 的键必须是非空字符串")
        _validate_value(value, field=f"{field}.{key}")


def conversation_summary(
    conversation: Conversation, turns: Sequence[Turn]
) -> dict[str, Any]:
    """构造会话审计摘要：只有元数据，没有原文、发言内容或手机号等正文。"""
    timestamps = [turn.timestamp for turn in turns if turn.timestamp is not None]
    summary: dict[str, Any] = {
        "conversation_id": str(conversation.conversation_id),
        "scope_id": conversation.scope_id,
        "owner_id": str(conversation.owner_id),
        "source_type": conversation.source_type,
        "content_hash": conversation.content_hash,
        "data_classification": conversation.data_classification,
        "redaction_applied": bool(conversation.redaction_applied),
        "turn_count": len(turns),
        "ordinals": [int(turn.ordinal) for turn in turns],
    }
    if timestamps:
        summary["first_turn_at"] = _utc_iso(min(timestamps))
        summary["last_turn_at"] = _utc_iso(max(timestamps))
    return summary


def append_audit_event(
    session: Session,
    *,
    actor_id: UUID,
    action: str,
    resource_type: str,
    resource_id: str,
    trace_id: str,
    before_summary: Mapping[str, Any] | None = None,
    after_summary: Mapping[str, Any] | None = None,
) -> AuditEvent:
    """追加一条审计事件；不做提交，由调用方与业务写入放在同一事务。

    ``trace_id`` 来自请求中间件；缺失或格式非法时直接失败，不能静默写空值。
    """
    if not trace_id or not isinstance(trace_id, str):
        raise ValueError("审计事件必须携带 trace_id")
    before = dict(before_summary or {})
    after = dict(after_summary or {})
    _validate_summary(before, field="before_summary")
    _validate_summary(after, field="after_summary")

    event = AuditEvent(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_summary=before,
        after_summary=after,
        trace_id=trace_id,
    )
    session.add(event)
    return event
