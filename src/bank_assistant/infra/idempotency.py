"""幂等键：请求输入哈希、绑定判定与持久化执行（契约 §3，CT-05/CT-06）。

幂等键绑定 ``(user_id, endpoint, input_hash)``：

- 键从未使用过 → 创建资源；
- 相同键 + 相同输入哈希 → 返回第一次的成功结果（replay）；
- 相同键 + 不同输入哈希 → 409（``IDEMPOTENCY_KEY_REUSED``），绝不覆盖原绑定。

绑定必须持久化（``idempotency_keys`` 表），不能用进程内字典：多 worker、
重启和故障恢复都要求同一条规则。哈希只覆盖请求输入本身，不包含幂等键、
用户或端点——后三者是绑定维度，不是输入。

并发安全：先尝试插入绑定行；唯一约束冲突时读取已提交的绑定行。插入与业务
资源创建在同一事务内完成，因此不会留下“有键无结果”的中间状态。这里用
SAVEPOINT 隔离唯一冲突，避免冲突把整个外层事务回滚。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bank_assistant.infra.models import IdempotencyKey

__all__ = [
    "BindingOutcome",
    "IdempotencyConflict",
    "IdempotentOutcome",
    "canonical_request_hash",
    "execute_idempotent",
    "resolve_binding",
]


class BindingOutcome(StrEnum):
    """幂等键再次出现时的判定结果。"""

    CREATE = "create"
    REPLAY = "replay"
    CONFLICT = "conflict"


class IdempotencyConflict(Exception):
    """同一幂等键已绑定到不同输入（HTTP 409），或绑定记录不完整。"""


@dataclass(frozen=True, slots=True)
class IdempotentOutcome:
    """一次幂等执行的结果：是否重放、原响应与资源 ID。"""

    replayed: bool
    resource_id: UUID | None
    response_status: int
    response_snapshot: dict[str, Any]


def canonical_request_hash(payload: BaseModel | Mapping[str, Any]) -> str:
    """对请求输入做规范化 JSON 编码并取 SHA-256。

    规范化规则固定为：键排序、无多余空白、UTF-8 原样保留非 ASCII 字符。
    这样同一输入无论字段顺序或客户端转义方式如何，都得到同一个哈希。
    """
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    else:
        data = dict(payload)
    encoded = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_binding(
    existing_request_hash: str | None,
    request_hash: str,
) -> BindingOutcome:
    """根据已存绑定判定：首次创建、同输入重放、还是同键不同输入的冲突。"""
    if existing_request_hash is None:
        return BindingOutcome.CREATE
    if existing_request_hash == request_hash:
        return BindingOutcome.REPLAY
    return BindingOutcome.CONFLICT


def _replay(existing: IdempotencyKey, request_hash: str) -> IdempotentOutcome:
    """校验已有绑定并返回原响应；不同输入直接冲突，不泄露原输入。"""
    if resolve_binding(existing.request_hash, request_hash) is not BindingOutcome.REPLAY:
        raise IdempotencyConflict("同一幂等键已用于不同输入")
    snapshot = existing.response_snapshot
    if snapshot is None:
        # 原子事务下不应出现；宁可冲突也不重复创建资源。
        raise IdempotencyConflict("幂等绑定缺少原始响应，拒绝重复创建")
    return IdempotentOutcome(
        replayed=True,
        resource_id=existing.resource_id,
        response_status=existing.response_status or 200,
        response_snapshot=dict(snapshot),
    )


def _find_binding(
    session: Session,
    *,
    user_id: UUID,
    endpoint: str,
    idempotency_key: str,
) -> IdempotencyKey | None:
    return session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.idempotency_key == idempotency_key,
        )
    )


def execute_idempotent(
    session: Session,
    *,
    user_id: UUID,
    endpoint: str,
    idempotency_key: str,
    request_hash: str,
    create: Callable[[], tuple[UUID, dict[str, Any]]],
    response_status: int = 201,
) -> IdempotentOutcome:
    """在调用方事务内执行一次幂等创建。

    ``create`` 只在首次使用该键时调用，返回 ``(resource_id, response_snapshot)``。
    同一键的后续调用直接返回已存响应，不重新执行业务逻辑。
    """
    existing = _find_binding(
        session,
        user_id=user_id,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        return _replay(existing, request_hash)

    reservation = IdempotencyKey(
        idempotency_id=uuid4(),
        user_id=user_id,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        response_status=response_status,
    )
    try:
        with session.begin_nested():
            session.add(reservation)
            session.flush()
    except IntegrityError:
        # 另一个并发请求先提交了同一绑定；读取它并按绑定规则处理。
        if reservation in session:
            session.expunge(reservation)
        existing = _find_binding(
            session,
            user_id=user_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            raise
        return _replay(existing, request_hash)

    resource_id, snapshot = create()
    reservation.resource_id = resource_id
    reservation.response_status = response_status
    reservation.response_snapshot = snapshot
    session.flush()
    return IdempotentOutcome(
        replayed=False,
        resource_id=resource_id,
        response_status=response_status,
        response_snapshot=snapshot,
    )
