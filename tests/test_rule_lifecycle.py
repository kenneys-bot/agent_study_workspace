"""规则版本生命周期纯逻辑的单元测试（不依赖数据库）。

契约 §3「知识与规则」：``draft → in_review → published → retired``，审核退回
为 draft；修改已发布规则必须新建版本。规则用 ``applicability`` 的有效期窗口
判定冲突：同一 ``rule_id`` 的已发布窗口不得重叠。

这里把状态机、严重级别、窗口生效判定与内容哈希固定成公共 seam；HTTP 与
数据库行为在 ``tests/integration/test_rules.py`` 验证。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from bank_assistant.rules.lifecycle import (
    DEFAULT_RULE_CATEGORIES,
    SEVERITIES,
    InvalidRuleTransition,
    PublishedRuleImmutable,
    RuleStatus,
    ensure_rule_transition,
    is_effective_at,
    rule_content_hash,
    validate_severity,
)

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)


def test_documented_rule_transitions_are_allowed() -> None:
    for current, target in (
        (RuleStatus.DRAFT, RuleStatus.IN_REVIEW),
        (RuleStatus.IN_REVIEW, RuleStatus.DRAFT),
        (RuleStatus.IN_REVIEW, RuleStatus.PUBLISHED),
        (RuleStatus.PUBLISHED, RuleStatus.RETIRED),
    ):
        ensure_rule_transition(current, target)


def test_skipped_or_backwards_rule_transitions_are_rejected() -> None:
    for current, target in (
        (RuleStatus.DRAFT, RuleStatus.PUBLISHED),
        (RuleStatus.DRAFT, RuleStatus.RETIRED),
        (RuleStatus.PUBLISHED, RuleStatus.IN_REVIEW),
        (RuleStatus.RETIRED, RuleStatus.PUBLISHED),
        (RuleStatus.RETIRED, RuleStatus.DRAFT),
    ):
        with pytest.raises(InvalidRuleTransition):
            ensure_rule_transition(current, target)


def test_rule_status_values_match_the_contract() -> None:
    assert {status.value for status in RuleStatus} == {
        "draft",
        "in_review",
        "published",
        "retired",
    }


def test_effective_window_is_half_open() -> None:
    # 发布有效期 [1月, 2月)：1月1日生效，2月1日 00:00 起不再生效。
    assert is_effective_at(JAN, FEB, at=JAN) is True
    assert is_effective_at(JAN, FEB, at=datetime(2026, 1, 31, 23, 59, tzinfo=UTC))
    assert is_effective_at(JAN, FEB, at=FEB) is False
    assert is_effective_at(JAN, FEB, at=datetime(2025, 12, 31, tzinfo=UTC)) is False
    # valid_to 为空表示持续有效。
    assert is_effective_at(JAN, None, at=MAR) is True


def test_severities_are_limited_to_the_contract_vocabulary() -> None:
    assert SEVERITIES == frozenset({"low", "medium", "high"})
    for severity in SEVERITIES:
        validate_severity(severity)
    with pytest.raises(ValueError):
        validate_severity("critical")


def test_default_rule_categories_cover_the_six_m4_categories() -> None:
    # M4-02 的默认规则类别，顺序即业务展示顺序。
    assert DEFAULT_RULE_CATEGORIES == (
        "password_or_code_request",
        "unverified_completion_claim",
        "unverified_fee_waiver",
        "fee_conflict_with_policy",
        "complaint_obstruction",
        "insulting_language",
    )


def test_rule_content_hash_is_canonical_and_sensitive_to_content() -> None:
    payload = {"severity": "high", "business_type": "lost_card", "version": 1}
    # 独立真值：按键排序后的紧凑 JSON 再做 SHA-256。
    expected = hashlib.sha256(
        '{"business_type":"lost_card","severity":"high","version":1}'.encode("utf-8")
    ).hexdigest()
    assert rule_content_hash(payload) == expected
    assert rule_content_hash({"version": 1, "severity": "high", "business_type": "lost_card"}) == expected
    assert rule_content_hash({**payload, "severity": "low"}) != expected


def test_published_rule_immutable_is_a_rule_error() -> None:
    error = PublishedRuleImmutable("11111111-1111-1111-1111-111111111111")
    assert "已发布" in str(error)
    # 与知识生命周期的错误区分开，API 才能给出规则专用的稳定错误码。
    from bank_assistant.knowledge.lifecycle import KnowledgeError

    assert not isinstance(error, KnowledgeError)
