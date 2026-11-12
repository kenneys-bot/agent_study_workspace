"""质检规则生命周期路由（契约 §2/§5，M4-01）。

| 方法与路径 | 作用 |
|---|---|
| POST /rules | 新建规则身份 + 草稿版本，或给已有规则追加草稿版本 |
| POST /rules/versions/{id}/submit | draft → in_review |
| POST /rules/versions/{id}/publish | 审核通过发布 / 退回草稿（reviewer/admin，且非作者） |
| POST /rules/versions/{id}/retire | published → retired |
| GET /rules/{rule_id} | 读取生效的已发布版本与 applicability |

``scope_id`` 永远取自服务端身份，跨范围资源与不存在资源返回同一个 404。
发布/退役要求 reviewer 或 admin 角色，且审核人不能是作者本人。发布的重叠
窗口决策见 ``bank_assistant.rules.service``：旧版本被显式退役并在响应里列出
``superseded_rule_version_ids``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from bank_assistant.api.errors import error_response
from bank_assistant.api.models.rules import (
    CreateRuleRequest,
    CreateRuleResponse,
    PublishRuleResponse,
    PublishRuleVersionRequest,
    RetireRuleVersionRequest,
    RuleVersionResponse,
    SubmitRuleVersionRequest,
)
from bank_assistant.infra.db import get_db_session
from bank_assistant.infra.models import RuleSet, RuleVersion
from bank_assistant.rules.lifecycle import (
    InvalidRuleTransition,
    PublishedRuleImmutable,
    RuleError,
)
from bank_assistant.rules.schema import RuleSchemaError
from bank_assistant.rules.service import (
    NewRuleSet,
    RuleVersionInput,
    create_rule_version,
    get_published_rule_version,
    publish_rule_version,
    retire_rule_version,
    submit_rule_version,
)
from bank_assistant.security.authorization import get_current_identity, require_role
from bank_assistant.security.identity import Identity, Role

router = APIRouter(prefix="/rules", tags=["rules"])

_REVIEWER_ROLES = (Role.REVIEWER, Role.ADMIN)


def _applicability(version: RuleVersion) -> dict[str, object]:
    """把服务端维护的有效期窗口合并进 applicability（客户端不能自报窗口）。"""
    merged: dict[str, object] = dict(version.applicability or {})
    merged["valid_from"] = version.valid_from
    merged["valid_to"] = version.valid_to
    return merged


def _version_response(
    version: RuleVersion, rule: RuleSet
) -> RuleVersionResponse:
    return RuleVersionResponse(
        rule_version_id=version.rule_version_id,
        rule_id=version.rule_id,
        rule_code=rule.rule_code,
        title=rule.title,
        category=rule.category,
        description=rule.description,
        version_no=version.version_no,
        business_type=version.business_type,
        applicability=_applicability(version),
        criteria=version.criteria,
        exceptions=version.exceptions,
        severity=version.severity,
        source_refs=version.source_refs,
        examples=version.examples,
        content_hash=version.content_hash,
        status=version.status,
        demo_only=version.demo_only,
        valid_from=version.valid_from,
        valid_to=version.valid_to,
        author_id=version.author_id,
        reviewer_id=version.reviewer_id,
        review_reason=version.review_reason,
        retire_reason=version.retire_reason,
        revision=version.revision,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


@router.post("", response_model=CreateRuleResponse, status_code=201)
def create_rule_endpoint(
    payload: CreateRuleRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(get_current_identity),
) -> CreateRuleResponse:
    """创建规则草稿；范围取自身份，客户端提交的 scope/author/status 被忽略。"""
    rule = (
        NewRuleSet(
            rule_code=payload.rule_code or "",
            title=payload.title or "",
            category=payload.category or "",
            description=payload.description or "",
            demo_only=bool(payload.demo_only),
        )
        if payload.rule_id is None
        else None
    )
    version_input = RuleVersionInput(
        business_type=payload.business_type,
        applicability=payload.applicability,
        criteria=payload.criteria,
        exceptions=tuple(payload.exceptions),
        severity=payload.severity,
        source_refs=tuple(payload.source_refs),
        examples=payload.examples,
        valid_from=payload.valid_from or datetime.now(UTC),
        valid_to=payload.valid_to,
    )
    rule_set, version = create_rule_version(
        session,
        identity,
        rule=rule,
        rule_id=payload.rule_id,
        version=version_input,
    )
    return CreateRuleResponse(
        rule_id=rule_set.rule_id,
        rule_version_id=version.rule_version_id,
        rule_code=rule_set.rule_code,
        version_no=version.version_no,
        status=version.status,
        demo_only=version.demo_only,
        content_hash=version.content_hash,
        revision=version.revision,
    )


@router.post(
    "/versions/{rule_version_id}/submit", response_model=RuleVersionResponse
)
def submit_rule_version_endpoint(
    rule_version_id: UUID,
    payload: SubmitRuleVersionRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(get_current_identity),
) -> RuleVersionResponse:
    """提交审核；只有 draft 可以提交，revision 不匹配返回 409。"""
    version, rule = submit_rule_version(
        session, identity, rule_version_id, expected_revision=payload.revision
    )
    return _version_response(version, rule)


@router.post(
    "/versions/{rule_version_id}/publish", response_model=PublishRuleResponse
)
def publish_rule_version_endpoint(
    rule_version_id: UUID,
    payload: PublishRuleVersionRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(require_role(*_REVIEWER_ROLES)),
) -> PublishRuleResponse:
    """审核通过发布，或退回草稿；审核人必须是 reviewer/admin 且不是作者。"""
    result = publish_rule_version(
        session,
        identity,
        rule_version_id,
        expected_revision=payload.revision,
        review_reason=payload.review_reason,
        decision=payload.decision,
    )
    response = _version_response(result.version, result.rule)
    return PublishRuleResponse(
        **response.model_dump(),
        outcome=result.outcome,
        superseded_rule_version_ids=result.superseded,
    )


@router.post(
    "/versions/{rule_version_id}/retire", response_model=RuleVersionResponse
)
def retire_rule_version_endpoint(
    rule_version_id: UUID,
    payload: RetireRuleVersionRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(require_role(*_REVIEWER_ROLES)),
) -> RuleVersionResponse:
    """退役已发布版本；理由与 revision 必填，退役后不再参与质检。"""
    version, rule = retire_rule_version(
        session,
        identity,
        rule_version_id,
        expected_revision=payload.revision,
        reason=payload.reason,
    )
    return _version_response(version, rule)


@router.get("/{rule_id}", response_model=RuleVersionResponse)
def read_published_rule_endpoint(
    rule_id: UUID,
    at: AwareDatetime | None = None,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(get_current_identity),
) -> RuleVersionResponse:
    """读取 ``at``（默认当前）生效的已发布版本与 applicability。"""
    rule, version = get_published_rule_version(
        session, identity, rule_id, at=at
    )
    return _version_response(version, rule)


def rules_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """把规则领域错误映射成契约 §1 的稳定错误码，不泄露内部堆栈。"""
    if isinstance(exc, RuleSchemaError):
        return error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="规则内容不合法",
            request=request,
            retryable=False,
            details={"reason": str(exc)[:500]},
        )
    status_code, code, message = 409, "RULE_CONFLICT", "规则版本状态冲突"
    if isinstance(exc, InvalidRuleTransition):
        status_code, code, message = (
            409,
            "RULE_STATE_CONFLICT",
            "规则版本状态不允许该操作",
        )
    elif isinstance(exc, PublishedRuleImmutable):
        status_code, code, message = (
            409,
            "RULE_CONTENT_IMMUTABLE",
            "已发布的规则版本内容不可修改",
        )
    details: dict[str, object] = {}
    if isinstance(exc, InvalidRuleTransition):
        details = {"current": exc.current, "target": exc.target}
    return error_response(
        status_code=status_code,
        code=code,
        message=message,
        request=request,
        retryable=False,
        details=details,
    )


def install_rules_error_handler(app: FastAPI) -> None:
    """安装规则领域错误处理器；由 ``main`` 在创建应用时调用。"""
    app.add_exception_handler(RuleError, rules_error_handler)
    app.add_exception_handler(RuleSchemaError, rules_error_handler)


__all__ = [
    "install_rules_error_handler",
    "router",
    "rules_error_handler",
]
