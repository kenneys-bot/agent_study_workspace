"""报告导出 API 的请求/响应模型（契约 §5 ``POST /reports`` / ``GET /reports/{id}``）。

- 请求只接受 ``inspection_id``、``format`` 与可选的 ``review_revision``；
  export_id、content_hash、artifact_ref、generated_by、created_at 全部由服务端
  生成，客户端不能指定；
- ``format`` 只允许 ``json`` / ``html``，其他值由 pydantic 在路由前拒绝（422）；
- ``GET`` 响应在元数据之外返回 ``content``：JSON 为解析后的对象，HTML 为字符串。
  两者都来自同一份已落盘产物，``content_hash`` 始终是产物字节的 SHA-256。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bank_assistant.infra.models import ReportExport

ReportFormatLiteral = Literal["json", "html"]

# 下载引用前缀：与 main.API_PREFIX + reports router 一致；单独常量便于测试断言。
REPORTS_PATH = "/api/v1/reports"


def content_url_for(export_id: UUID) -> str:
    """返回下载/复读该导出内容的相对 URL（仍需认证与范围校验）。"""
    return f"{REPORTS_PATH}/{export_id}"


class ReportExportRequest(BaseModel):
    """``POST /reports`` 输入：固定某个质检在某个复核 revision 的快照。"""

    model_config = ConfigDict(extra="forbid")

    inspection_id: UUID = Field(..., description="要导出的质检 ID")
    format: ReportFormatLiteral = Field(..., description="json 或 html")
    review_revision: int | None = Field(
        default=None,
        ge=0,
        description="可选：导出该复核 revision 的快照；缺省为当前 revision",
    )


class ReportExportSummary(BaseModel):
    """一次导出的元数据；``artifact_ref`` 是相对 ARTIFACT_ROOT 的产物引用。"""

    export_id: UUID
    inspection_id: UUID
    review_revision: int
    format: str
    content_hash: str
    artifact_ref: str
    generated_by: UUID
    created_at: datetime
    # 下载引用：GET 该路径会再次鉴权并返回同一份产物内容。
    content_url: str


class ReportExportDetail(ReportExportSummary):
    """``GET /reports/{id}`` 响应：元数据 + 该快照的内容。

    JSON 报告返回解析后的对象；HTML 报告返回静态 HTML 字符串。内容逐字节
    等同于导出时的产物，``content_hash`` 可复算核对（CT-12）。
    """

    content: Any = Field(..., description="JSON 对象或 HTML 字符串；与 content_hash 对应")


def to_summary(export: ReportExport) -> ReportExportSummary:
    return ReportExportSummary(
        export_id=export.export_id,
        inspection_id=export.inspection_id,
        review_revision=export.review_revision,
        format=export.format,
        content_hash=export.content_hash,
        artifact_ref=export.artifact_ref,
        generated_by=export.generated_by,
        created_at=export.created_at,
        content_url=content_url_for(export.export_id),
    )


def to_detail(export: ReportExport, content: Any) -> ReportExportDetail:
    return ReportExportDetail(
        export_id=export.export_id,
        inspection_id=export.inspection_id,
        review_revision=export.review_revision,
        format=export.format,
        content_hash=export.content_hash,
        artifact_ref=export.artifact_ref,
        generated_by=export.generated_by,
        created_at=export.created_at,
        content_url=content_url_for(export.export_id),
        content=content,
    )


__all__ = [
    "REPORTS_PATH",
    "ReportExportDetail",
    "ReportExportRequest",
    "ReportExportSummary",
    "ReportFormatLiteral",
    "content_url_for",
    "to_detail",
    "to_summary",
]
