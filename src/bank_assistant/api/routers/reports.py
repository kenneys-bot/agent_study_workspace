"""报告导出路由（契约 §5 ``/reports``，M4-05/T21，#22）。

| 方法与路径 | 作用 |
|---|---|
| POST /reports | 按 ``(inspection_id, review_revision, format)`` 固化报告快照 |
| GET /reports/{id} | 再次校验权限后返回快照内容（JSON 内联 / HTML 字符串） |

- 只允许导出 ``succeeded`` 的质检；未完成质检返回 409，不把中间态当最终报告；
- ``format`` 只接受 json/html，其他值由 pydantic 在路由前拒绝（422）；
- ``review_revision`` 缺省为当前 revision；请求尚未产生的 revision 返回 409；
- 跨范围与不存在的质检/导出统一 404，不泄露存在性；
- 读取路径每次重新校验质检 scope、复算产物 SHA-256；产物缺失或损坏显式失败，
  绝不返回空报告或换一份新生成的内容（CT-12）。

并发/幂等：导出是只追加操作；同一 revision 重复导出会得到内容相同但
export_id/created_at 不同的两条记录，不覆盖已有导出。
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError, resolve_trace_id
from bank_assistant.api.models.reports import (
    ReportExportDetail,
    ReportExportRequest,
    ReportExportSummary,
    to_detail,
    to_summary,
)
from bank_assistant.infra.db import get_db_session, session_scope
from bank_assistant.inspections.service import InspectionNotFound
from bank_assistant.reports.schema import ReportArtifactError
from bank_assistant.reports.service import (
    ReportConflict,
    ReportInputError,
    ReportNotFound,
    create_report_export,
    load_report_export,
)
from bank_assistant.security.authorization import get_current_identity, require_role
from bank_assistant.security.identity import Role
from bank_assistant.security.rate_limit import enforce_expensive_rate_limit
from bank_assistant.security.identity import Identity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["报告导出"])

# 导出是可审计的对外交付动作，只允许复核/管理员角色；普通坐席可读取授权范围内的
# 既有导出（GET），但不能生成新快照。
_EXPORT_ROLES = (Role.REVIEWER, Role.ADMIN)


def _not_found() -> ApiError:
    return ApiError(status_code=404, code="NOT_FOUND", message="报告不存在")


def _conflict(exc: ReportConflict) -> ApiError:
    return ApiError(
        status_code=409,
        code=exc.code,
        message=exc.message,
        retryable=False,
        details=dict(exc.details),
    )


def _input_error(exc: ReportInputError) -> ApiError:
    return ApiError(
        status_code=422,
        code=exc.code,
        message=str(exc),
        retryable=False,
    )


def _artifact_error(exc: ReportArtifactError) -> ApiError:
    """产物失败是服务端数据完整性问题：显式 500，不回传路径或内容。"""
    logger.error("report_artifact_failure reason=%s", exc)
    return ApiError(
        status_code=500,
        code="REPORT_ARTIFACT_UNAVAILABLE",
        message="报告产物不可用：请重试导出",
        retryable=True,
    )


@router.post(
    "",
    response_model=ReportExportSummary,
    status_code=201,
    dependencies=[
        Depends(enforce_expensive_rate_limit),
        Depends(require_role(*_EXPORT_ROLES)),
    ],
)
def create_report_endpoint(
    payload: ReportExportRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
) -> ReportExportSummary:
    """固化并落盘一份报告快照，返回 export_id 与下载引用。"""
    trace_id = resolve_trace_id(request)
    try:
        with session_scope() as session:
            export = create_report_export(
                session,
                identity,
                inspection_id=payload.inspection_id,
                report_format=payload.format,
                review_revision=payload.review_revision,
                trace_id=trace_id,
            )
            return to_summary(export)
    except (ReportNotFound, InspectionNotFound):
        raise _not_found() from None
    except ReportConflict as exc:
        raise _conflict(exc) from None
    except ReportInputError as exc:
        raise _input_error(exc) from None
    except ReportArtifactError as exc:
        raise _artifact_error(exc) from None


@router.get("/{export_id}", response_model=ReportExportDetail)
def read_report_endpoint(
    export_id: UUID,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
) -> ReportExportDetail:
    """重新校验权限与产物哈希后返回内容；跨范围与不存在都是 404。"""
    try:
        view = load_report_export(session, identity, export_id)
    except ReportNotFound:
        raise _not_found() from None
    except ReportArtifactError as exc:
        raise _artifact_error(exc) from None
    return to_detail(view.export, view.content)


__all__ = [
    "create_report_endpoint",
    "read_report_endpoint",
    "router",
]
