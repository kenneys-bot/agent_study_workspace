"""会话导入与读取路由（契约 §5，M2-04）。

- ``POST /conversations``：接受 text 或 JSON 原文，交给 T03 解析器；轮次
  ordinal 按解析结果原样持久化。内容与审计在同一事务写入。
- ``GET /conversations/{id}``：只返回授权范围内的会话；跨范围与不存在都
  返回同一个 404，不泄露存在性。响应中的展示文本可能已脱敏，证据坐标仍
  以不可变原文的 Unicode 码点为准（契约 §4）。
- 授权：身份与 ``scope_id`` 一律来自服务端数据库（T10）；请求体里声明的
  ``scope_id`` 只在与服务端一致时被接受，否则 403。
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError, resolve_trace_id
from bank_assistant.api.models.conversations import (
    ConversationCreateRequest,
    ConversationPublic,
    to_public,
)
from bank_assistant.assistants.quality_inspector.conversation_parser import (
    ConversationParseError,
    ConversationParser,
)
from bank_assistant.infra.db import get_db_session
from bank_assistant.security.authorization import (
    ensure_resource_visible,
    ensure_scope,
    get_current_identity,
)
from bank_assistant.security.identity import Identity
from bank_assistant.services.conversations import (
    ConversationIntegrityError,
    create_conversation,
    load_conversation,
    record_conversation_read,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["会话持久化"])


def get_conversation_parser() -> ConversationParser:
    """T03 解析器：text/JSON 输入的唯一解析入口。"""
    return ConversationParser()


def _parse_or_400(
    parser: ConversationParser, payload: ConversationCreateRequest
) -> object:
    """把解析器的稳定错误码映射为契约 §1 的 4xx，不回传原文。"""
    try:
        return parser.parse_conversation(payload.content, payload.format)
    except ConversationParseError as exc:
        logger.warning("会话导入输入非法 [%s]", exc.code)
        raise ApiError(
            status_code=exc.http_status,
            code=exc.code,
            message=exc.message,
            retryable=False,
            details={},
        ) from exc


@router.post(
    "/conversations",
    response_model=ConversationPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation_endpoint(
    payload: ConversationCreateRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    parser: ConversationParser = Depends(get_conversation_parser),
    session: Session = Depends(get_db_session),
) -> ConversationPublic:
    """导入会话：原文不可变，ordinal 原样保存，并追加创建审计。"""
    if payload.scope_id is not None:
        # 客户端显式声明其他范围 → 403；服务端身份绝不因请求体改变。
        ensure_scope(identity, payload.scope_id)

    parsed = _parse_or_400(parser, payload)
    try:
        view = create_conversation(
            session,
            identity=identity,
            parsed=parsed,
            source_type=payload.source_type,
            source_ref=payload.source_ref,
            data_classification=payload.data_classification,
            trace_id=resolve_trace_id(request),
        )
    except ConversationIntegrityError as exc:
        raise ApiError(
            status_code=400,
            code="CONVERSATION_INVALID_TURNS",
            message=str(exc),
            retryable=False,
            details={},
        ) from exc

    return to_public(view)


@router.get("/conversations/{conversation_id}", response_model=ConversationPublic)
def read_conversation_endpoint(
    conversation_id: UUID,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
) -> ConversationPublic:
    """读取授权会话；跨范围资源与不存在资源返回同一个 404。"""
    view = load_conversation(session, conversation_id)
    ensure_resource_visible(
        view.conversation if view is not None else None,
        identity,
    )
    assert view is not None  # ensure_resource_visible 已在 None 时抛出 404
    record_conversation_read(
        session,
        view,
        identity=identity,
        trace_id=resolve_trace_id(request),
    )
    return to_public(view)
