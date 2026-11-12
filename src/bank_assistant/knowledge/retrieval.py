"""正式检索的候选知识：只取 published 且在有效期、范围、业务内的版本。

契约 §3：只有 ``published`` 且在有效期、访问范围与业务范围内的版本能参与
正式检索。三条过滤全部下推到 SQL（``WHERE``），不在 Python 里事后筛除——
M3 的召回直接复用本查询，避免“先查全部再过滤”把草稿或过期知识带进候选。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion
from bank_assistant.knowledge.lifecycle import KnowledgeStatus


def retrievable_versions_query(
    *,
    scope_id: str,
    business_type: str | None = None,
    at: datetime | None = None,
    document_id: UUID | None = None,
) -> Select[tuple[KnowledgeVersion]]:
    """构造“可检索版本”的 SQL 查询；``at`` 缺省为当前时刻。

    有效期是半开区间 ``[valid_from, valid_to)``：``valid_to`` 为空表示长期
    有效。调用方可以按业务类型与文档进一步收窄，但不能放宽这里的状态/范围
    过滤。
    """
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("检索时刻必须带时区（契约 §1：时间统一按 UTC 存储）")

    statement = (
        select(KnowledgeVersion)
        .join(
            KnowledgeDocument,
            KnowledgeVersion.document_id == KnowledgeDocument.document_id,
        )
        .where(KnowledgeVersion.status == KnowledgeStatus.PUBLISHED.value)
        .where(KnowledgeDocument.scope_id == scope_id)
        .where(KnowledgeVersion.valid_from <= moment)
        .where(
            or_(
                KnowledgeVersion.valid_to.is_(None),
                KnowledgeVersion.valid_to > moment,
            )
        )
        .order_by(KnowledgeVersion.valid_from, KnowledgeVersion.version_no)
    )
    if business_type is not None:
        statement = statement.where(KnowledgeDocument.business_type == business_type)
    if document_id is not None:
        statement = statement.where(KnowledgeDocument.document_id == document_id)
    return statement


def fetch_retrievable_versions(
    session: Session,
    *,
    scope_id: str,
    business_type: str | None = None,
    at: datetime | None = None,
    document_id: UUID | None = None,
) -> list[KnowledgeVersion]:
    """执行查询并返回可检索版本（M3 召回入口）。"""
    return list(
        session.scalars(
            retrievable_versions_query(
                scope_id=scope_id,
                business_type=business_type,
                at=at,
                document_id=document_id,
            )
        )
    )
