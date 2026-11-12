"""规则版本生命周期与适用范围窗口（M4-01，契约 §2 RuleVersion、§3）。

复用知识生命周期的状态机形状：``draft → in_review → published → retired``，
审核退回为 draft；修改已发布规则只能新建版本。实现上不复制状态迁移表，而是
从 :mod:`bank_assistant.knowledge.lifecycle` 的同一张表派生 :class:`RuleStatus`
版本，保证两个生命周期不会悄悄分叉。

规则与知识的关键差别在“适用范围”：``RuleVersion.applicability`` 除了业务范围
与前提条件，还带一个半开有效期窗口 ``[valid_from, valid_to)``。发布时若同一
``rule_id`` 的已发布版本窗口与新版重叠，服务层会**显式退役旧版本**并回报
``superseded_rule_version_ids``（与知识发布一致，见 service 模块说明）。

本模块只放无数据库可验证的纯逻辑。错误类型继承 :class:`RuleError` 而不是
``KnowledgeError``：API 层需要把规则冲突映射为规则专用稳定错误码。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from bank_assistant.knowledge.lifecycle import (
    ALLOWED_TRANSITIONS as _KNOWLEDGE_TRANSITIONS,
)

# M4-02 的默认规则类别；顺序即业务展示顺序，也是演示规则集的必备类别。
DEFAULT_RULE_CATEGORIES: tuple[str, ...] = (
    "password_or_code_request",
    "unverified_completion_claim",
    "unverified_fee_waiver",
    "fee_conflict_with_policy",
    "complaint_obstruction",
    "insulting_language",
)

CATEGORY_LABELS: Mapping[str, str] = {
    "password_or_code_request": "索取密码/验证码",
    "unverified_completion_claim": "无依据声称办理成功",
    "unverified_fee_waiver": "无依据承诺减免费用",
    "fee_conflict_with_policy": "费用说明与有效条款冲突",
    "complaint_obstruction": "阻碍投诉",
    "insulting_language": "明显侮辱性表达",
}

# 契约 §4 证据 JSON 使用 low/medium/high；至少一条高风险规则是 #18 的验收项。
SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high"})


class RuleStatus(StrEnum):
    """规则版本在生命周期中的阶段；只有 PUBLISHED 能参与正式质检。"""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    RETIRED = "retired"


class RuleError(Exception):
    """规则生命周期的领域错误基类；API 层统一映射为契约 §1 错误结构。"""


class InvalidRuleTransition(RuleError):
    """状态机不允许的转换；调用方应返回 409，而不是悄悄改状态。"""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"规则版本状态不能从 {current} 变为 {target}")


class PublishedRuleImmutable(RuleError):
    """已发布规则的内容不可覆盖；修改必须新建版本（契约 §3）。"""

    def __init__(self, rule_version_id: Any) -> None:
        self.rule_version_id = rule_version_id
        super().__init__("已发布的规则版本内容不可修改")


class InvalidSeverity(RuleError, ValueError):
    """风险级别不在契约允许的 low/medium/high 内。"""


# 与知识生命周期共用同一张迁移图；这里按 RuleStatus 重新键入，永不复制字面量。
ALLOWED_TRANSITIONS: Mapping[RuleStatus, frozenset[RuleStatus]] = {
    RuleStatus(source.value): frozenset(
        RuleStatus(target.value) for target in targets
    )
    for source, targets in _KNOWLEDGE_TRANSITIONS.items()
}


def _coerce_status(value: RuleStatus | str) -> RuleStatus:
    return value if isinstance(value, RuleStatus) else RuleStatus(value)


def ensure_rule_transition(
    current: RuleStatus | str, target: RuleStatus | str
) -> None:
    """校验一次规则状态转换是否在契约允许的路径内。"""
    source = _coerce_status(current)
    destination = _coerce_status(target)
    if destination not in ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise InvalidRuleTransition(source.value, destination.value)


def validate_severity(severity: str) -> None:
    """风险级别必须是 low/medium/high（契约 §4 证据 JSON）。"""
    if severity not in SEVERITIES:
        raise InvalidSeverity(
            f"风险级别必须是 {sorted(SEVERITIES)}，实际 {severity!r}"
        )


def is_effective_at(
    valid_from: datetime, valid_to: datetime | None, *, at: datetime
) -> bool:
    """半开窗口 ``[valid_from, valid_to)`` 在 ``at`` 时刻是否生效。

    与知识有效期判定一致：``valid_to is None`` 表示持续有效；窗口结束时刻
    自身不再生效。
    """
    if at < valid_from:
        return False
    return valid_to is None or at < valid_to


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def rule_content_hash(payload: Mapping[str, Any]) -> str:
    """规则内容的规范化哈希（SHA-256 hex）。

    规范形是键排序后的紧凑 JSON（UTF-8），用于演示种子幂等判定与已发布内容
    不可变校验；键顺序不同不会改变哈希。
    """
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CATEGORY_LABELS",
    "DEFAULT_RULE_CATEGORIES",
    "InvalidRuleTransition",
    "InvalidSeverity",
    "PublishedRuleImmutable",
    "RuleError",
    "RuleStatus",
    "SEVERITIES",
    "ensure_rule_transition",
    "is_effective_at",
    "rule_content_hash",
    "validate_severity",
]
