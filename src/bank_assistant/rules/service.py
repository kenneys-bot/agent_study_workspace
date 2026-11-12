"""规则版本生命周期服务：草稿、提交、发布/退回、退役与授权读取（M4-01）。

契约 §2/§3/§5：

- ``draft → in_review → published → retired``，审核退回为 draft；
- 发布必须由 reviewer/admin 执行，且审核人不能是作者（发布者与作者分离）；
- 已发布内容不可覆盖，修改必须新建版本（数据库层另有兜底校验）；
- 演示规则 ``demo_only=true``，说明必须标注「虚构演示制度」。

**适用窗口重叠的处理（本票决策，需与测试一起阅读）**：发布一个 ``rule_id``
的新版本时，若同一规则存在有效期窗口重叠的已发布版本，服务层**显式退役**
旧版本（写入 ``retire_reason``），并在发布结果 ``superseded`` / API 响应的
``superseded_rule_version_ids`` 里列出被退役的版本 ID。理由：与知识发布采用
同一套“冲突必须被显式处理”的模式（契约 §3），且历史版本保留可追溯、不会
出现两个同时生效的规则版本；静默覆盖或让重叠版本共存都会让质检规则快照
不可解释。调用方若不想自动退役，应先把旧版本显式退役再发布新版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError
from bank_assistant.infra.models import RuleSet, RuleVersion
from bank_assistant.knowledge.lifecycle import (
    RevisionConflict,
    ensure_reviewer_separation,
    validate_window,
)
from bank_assistant.rules.lifecycle import (
    RuleStatus,
    ensure_rule_transition,
    rule_content_hash,
    validate_severity,
)
from bank_assistant.rules.schema import (
    RuleApplicability,
    RuleCriteria,
    RuleExamples,
    RuleSchemaError,
    RuleSourceRef,
    demo_marker_present,
    validate_rule_examples,
    validate_source_refs,
)
from bank_assistant.security.authorization import ensure_resource_visible
from bank_assistant.security.identity import Identity


@dataclass(frozen=True, slots=True)
class NewRuleSet:
    """新建规则身份所需的元数据；``scope_id`` 永远来自服务端身份。"""

    rule_code: str
    title: str
    category: str
    description: str
    demo_only: bool = False


@dataclass(frozen=True, slots=True)
class RuleVersionInput:
    """一个规则版本的完整内容（不含服务端计算的版本号与哈希）。"""

    business_type: str
    applicability: RuleApplicability
    criteria: RuleCriteria
    exceptions: tuple[str, ...]
    severity: str
    source_refs: tuple[RuleSourceRef, ...]
    examples: RuleExamples
    valid_from: datetime
    valid_to: datetime | None = None


@dataclass(frozen=True)
class PublishedRuleVersion:
    """一次规则发布的结果；``superseded`` 是被显式退役的重叠版本。"""

    version: RuleVersion
    rule: RuleSet
    superseded: list[UUID]
    outcome: Literal["published", "returned_to_draft"]


def not_found() -> ApiError:
    """跨范围与不存在共用同一 404，不泄露存在性（契约 §1）。"""
    return ApiError(status_code=404, code="NOT_FOUND", message="资源不存在")


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _load_rule_version(
    session: Session,
    identity: Identity,
    rule_version_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[RuleVersion, RuleSet]:
    """按身份读取版本及其规则身份；不存在或跨 ``scope_id`` 一律 404。"""
    version = session.get(RuleVersion, rule_version_id, with_for_update=for_update)
    if version is None:
        raise not_found()
    rule = session.get(RuleSet, version.rule_id, with_for_update=for_update)
    ensure_resource_visible(rule, identity)
    if rule is None:  # pragma: no cover - ensure_resource_visible 已抛错
        raise not_found()
    return version, rule


def _ensure_revision(version: RuleVersion, expected_revision: int) -> None:
    if version.revision != expected_revision:
        raise RevisionConflict(expected=expected_revision, current=version.revision)


def build_rule_content(
    *,
    business_type: str,
    applicability: RuleApplicability,
    criteria: RuleCriteria,
    exceptions: tuple[str, ...],
    severity: str,
    source_refs: tuple[RuleSourceRef, ...],
    examples: RuleExamples,
    demo_only: bool,
    valid_from: datetime,
    valid_to: datetime | None,
) -> dict[str, Any]:
    """规则内容的规范形（含窗口与示例），供内容哈希与幂等比较复用。"""
    return {
        "business_type": business_type,
        "applicability": applicability.model_dump(mode="json"),
        "criteria": criteria.model_dump(mode="json"),
        "exceptions": list(exceptions),
        "severity": severity,
        "source_refs": [ref.model_dump(mode="json") for ref in source_refs],
        "examples": examples.model_dump(mode="json"),
        "demo_only": demo_only,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat() if valid_to is not None else None,
    }


def content_hash_for(version: RuleVersionInput, *, demo_only: bool) -> str:
    """由版本输入计算内容哈希；演示种子据此判断“同一版本是否已导入”。"""
    return rule_content_hash(
        build_rule_content(
            business_type=version.business_type,
            applicability=version.applicability,
            criteria=version.criteria,
            exceptions=version.exceptions,
            severity=version.severity,
            source_refs=version.source_refs,
            examples=version.examples,
            demo_only=demo_only,
            valid_from=version.valid_from,
            valid_to=version.valid_to,
        )
    )


def _validate_version_input(version: RuleVersionInput, *, demo_only: bool) -> None:
    """服务层复核内容约束；API 模型之外的第二道校验（契约 §1）。"""
    validate_severity(version.severity)
    validate_window(version.valid_from, version.valid_to)
    if version.business_type not in version.applicability.business_types:
        raise RuleSchemaError(
            "applicability.business_types 必须包含版本的 business_type"
        )
    if not version.exceptions:
        raise RuleSchemaError("规则必须声明至少一条豁免条件（exceptions）")
    validate_source_refs(list(version.source_refs), demo_only=demo_only)


def create_rule_version(
    session: Session,
    identity: Identity,
    *,
    version: RuleVersionInput,
    rule: NewRuleSet | None = None,
    rule_id: UUID | None = None,
) -> tuple[RuleSet, RuleVersion]:
    """新建规则身份 + 草稿版本，或给已有规则追加草稿版本。

    追加版本不会改写规则身份（编码、类别、说明、演示标记），避免借新版本
    扩大范围或把演示规则洗成正式规则。
    """
    if (rule is None) == (rule_id is None):
        raise ValueError("必须且只能提供 rule（新建）或 rule_id（追加版本）")

    if rule is not None:
        if not rule.rule_code.strip() or not rule.title.strip():
            raise RuleSchemaError("新建规则必须提供非空 rule_code 与 title")
        if not rule.category.strip():
            raise RuleSchemaError("新建规则必须提供非空 category")
        if rule.demo_only and not demo_marker_present(rule.description):
            raise RuleSchemaError(
                "演示规则的说明必须注明「虚构演示制度」，不得与真实制度混同"
            )
        rule_set = RuleSet(
            rule_id=uuid4(),
            rule_code=rule.rule_code,
            title=rule.title,
            category=rule.category,
            description=rule.description,
            scope_id=identity.scope_id,
            demo_only=rule.demo_only,
        )
        session.add(rule_set)
        session.flush()
        version_no = 1
    else:
        rule_set = session.get(RuleSet, rule_id, with_for_update=True)
        ensure_resource_visible(rule_set, identity)
        if rule_set is None:  # pragma: no cover - ensure_resource_visible 已抛错
            raise not_found()
        highest = session.scalar(
            select(func.max(RuleVersion.version_no)).where(
                RuleVersion.rule_id == rule_set.rule_id
            )
        )
        version_no = (highest or 0) + 1

    demo_only = bool(rule_set.demo_only)
    _validate_version_input(version, demo_only=demo_only)
    validate_rule_examples(
        version.examples,
        rule_code=rule_set.rule_code,
        version_no=version_no,
        severity=version.severity,
    )

    rule_version = RuleVersion(
        rule_version_id=uuid4(),
        rule_id=rule_set.rule_id,
        version_no=version_no,
        business_type=version.business_type,
        applicability=version.applicability.model_dump(mode="json"),
        criteria=version.criteria.model_dump(mode="json"),
        exceptions=list(version.exceptions),
        severity=version.severity,
        source_refs=[ref.model_dump(mode="json") for ref in version.source_refs],
        examples=version.examples.model_dump(mode="json"),
        content_hash=content_hash_for(version, demo_only=demo_only),
        status=RuleStatus.DRAFT.value,
        demo_only=demo_only,
        valid_from=version.valid_from,
        valid_to=version.valid_to,
        author_id=identity.user_id,
        revision=1,
    )
    session.add(rule_version)
    _commit(session)
    return rule_set, rule_version


def submit_rule_version(
    session: Session,
    identity: Identity,
    rule_version_id: UUID,
    *,
    expected_revision: int,
) -> tuple[RuleVersion, RuleSet]:
    """``draft → in_review``；提交审核。"""
    version, rule = _load_rule_version(
        session, identity, rule_version_id, for_update=True
    )
    _ensure_revision(version, expected_revision)
    ensure_rule_transition(version.status, RuleStatus.IN_REVIEW)
    version.status = RuleStatus.IN_REVIEW.value
    version.revision += 1
    _commit(session)
    return version, rule


def _overlapping_published_query(version: RuleVersion):
    """同一规则中与 ``version`` 有效期窗口重叠的已发布版本（SQL 层判定）。"""
    statement = select(RuleVersion).where(
        RuleVersion.rule_id == version.rule_id,
        RuleVersion.rule_version_id != version.rule_version_id,
        RuleVersion.status == RuleStatus.PUBLISHED.value,
        or_(
            RuleVersion.valid_to.is_(None),
            version.valid_from < RuleVersion.valid_to,
        ),
    )
    if version.valid_to is not None:
        statement = statement.where(RuleVersion.valid_from < version.valid_to)
    return statement.with_for_update()


def publish_rule_version(
    session: Session,
    identity: Identity,
    rule_version_id: UUID,
    *,
    expected_revision: int,
    review_reason: str,
    decision: str = "approve",
) -> PublishedRuleVersion:
    """发布（approve）或退回草稿（reject）；审核人不能是作者本人。

    approve 时若存在窗口重叠的已发布版本，旧版本被显式退役并在 ``superseded``
    里列出其 ID——两级冲突不会静默消失（见模块级决策说明）。
    """
    if decision not in {"approve", "reject"}:
        raise ValueError("decision 只能是 approve 或 reject")
    version, rule = _load_rule_version(
        session, identity, rule_version_id, for_update=True
    )
    ensure_reviewer_separation(version.author_id, identity.user_id)
    _ensure_revision(version, expected_revision)

    if decision == "reject":
        ensure_rule_transition(version.status, RuleStatus.DRAFT)
        version.status = RuleStatus.DRAFT.value
        version.reviewer_id = identity.user_id
        version.review_reason = review_reason
        version.revision += 1
        _commit(session)
        return PublishedRuleVersion(
            version=version,
            rule=rule,
            superseded=[],
            outcome="returned_to_draft",
        )

    ensure_rule_transition(version.status, RuleStatus.PUBLISHED)
    superseded: list[UUID] = []
    for conflict in session.scalars(_overlapping_published_query(version)):
        ensure_rule_transition(conflict.status, RuleStatus.RETIRED)
        conflict.status = RuleStatus.RETIRED.value
        conflict.retire_reason = (
            f"被版本 {version.version_no} 取代：适用有效期窗口重叠，发布时显式退役"
        )
        conflict.revision += 1
        superseded.append(conflict.rule_version_id)

    version.status = RuleStatus.PUBLISHED.value
    version.reviewer_id = identity.user_id
    version.review_reason = review_reason
    version.revision += 1
    _commit(session)
    return PublishedRuleVersion(
        version=version,
        rule=rule,
        superseded=superseded,
        outcome="published",
    )


def retire_rule_version(
    session: Session,
    identity: Identity,
    rule_version_id: UUID,
    *,
    expected_revision: int,
    reason: str,
) -> tuple[RuleVersion, RuleSet]:
    """``published → retired``；退役后不再参与正式质检。"""
    version, rule = _load_rule_version(
        session, identity, rule_version_id, for_update=True
    )
    _ensure_revision(version, expected_revision)
    ensure_rule_transition(version.status, RuleStatus.RETIRED)
    version.status = RuleStatus.RETIRED.value
    version.retire_reason = reason
    version.revision += 1
    _commit(session)
    return version, rule


def get_published_rule_version(
    session: Session,
    identity: Identity,
    rule_id: UUID,
    *,
    at: datetime | None = None,
) -> tuple[RuleSet, RuleVersion]:
    """读取 ``at`` 时刻生效的已发布规则版本；不存在生效版本返回 404。

    时间窗口是半开的 ``[valid_from, valid_to)``（契约 §3 与知识一致）。若规则
    可见但没有生效版本，错误码为 ``RULE_NOT_EFFECTIVE``——明确区别于跨范围/不
    存在，避免调用方把“尚未发布”误当成“无权限”。
    """
    moment = at or datetime.now(UTC)
    rule = session.get(RuleSet, rule_id)
    ensure_resource_visible(rule, identity)
    if rule is None:  # pragma: no cover - ensure_resource_visible 已抛错
        raise not_found()

    version = session.scalar(
        select(RuleVersion)
        .where(
            RuleVersion.rule_id == rule_id,
            RuleVersion.status == RuleStatus.PUBLISHED.value,
            RuleVersion.valid_from <= moment,
            or_(
                RuleVersion.valid_to.is_(None),
                moment < RuleVersion.valid_to,
            ),
        )
        .order_by(RuleVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise ApiError(
            status_code=404,
            code="RULE_NOT_EFFECTIVE",
            message="该规则当前没有生效的已发布版本",
            details={"rule_id": str(rule_id), "at": moment.isoformat()},
        )
    return rule, version


__all__ = [
    "NewRuleSet",
    "PublishedRuleVersion",
    "RuleVersionInput",
    "build_rule_content",
    "content_hash_for",
    "create_rule_version",
    "get_published_rule_version",
    "not_found",
    "publish_rule_version",
    "retire_rule_version",
    "submit_rule_version",
]
