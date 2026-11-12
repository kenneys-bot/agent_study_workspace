"""知识版本生命周期纯逻辑的单元测试（不依赖数据库）。

契约 §3「知识与规则」规定：``draft → in_review → published → retired``，
审核退回为 draft；同一知识条目的冲突有效版本必须被检测并显式处理。
这里把状态机、内容哈希和有效期重叠判定当成公共 seam 固定下来，HTTP 与
数据库行为在 ``tests/integration/test_knowledge.py`` 验证。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bank_assistant.knowledge.lifecycle import (
    InvalidTransition,
    InvalidWindow,
    KnowledgeStatus,
    SelfReviewForbidden,
    ensure_reviewer_separation,
    ensure_transition,
    hash_content,
    validate_window,
    windows_overlap,
)

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)
APR = datetime(2026, 4, 1, tzinfo=UTC)


def test_content_hash_matches_the_published_sha256_known_vector() -> None:
    # 独立真值：SHA-256("abc") 的公开标准测试向量。
    assert (
        hash_content("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert hash_content("演示制度：挂失需先冻结账户") != hash_content("演示制度：挂失")


def test_validity_windows_are_half_open() -> None:
    # [1月, 2月) 与 [2月, 3月) 相接但不重叠。
    assert windows_overlap(JAN, FEB, FEB, MAR) is False
    assert windows_overlap(JAN, MAR, FEB, APR) is True
    # 开放结束时间：一直有效。
    assert windows_overlap(JAN, None, FEB, MAR) is True
    assert windows_overlap(JAN, None, None, None) is True
    assert windows_overlap(FEB, MAR, JAN, None) is True


def test_window_must_be_ordered_and_non_empty() -> None:
    validate_window(JAN, FEB)
    validate_window(JAN, None)

    with pytest.raises(InvalidWindow):
        validate_window(FEB, JAN)
    with pytest.raises(InvalidWindow):
        validate_window(JAN, JAN)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (KnowledgeStatus.DRAFT, KnowledgeStatus.IN_REVIEW),
        (KnowledgeStatus.IN_REVIEW, KnowledgeStatus.DRAFT),
        (KnowledgeStatus.IN_REVIEW, KnowledgeStatus.PUBLISHED),
        (KnowledgeStatus.PUBLISHED, KnowledgeStatus.RETIRED),
    ],
)
def test_documented_transitions_are_allowed(
    current: KnowledgeStatus, target: KnowledgeStatus
) -> None:
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (KnowledgeStatus.DRAFT, KnowledgeStatus.PUBLISHED),
        (KnowledgeStatus.DRAFT, KnowledgeStatus.RETIRED),
        (KnowledgeStatus.RETIRED, KnowledgeStatus.PUBLISHED),
        (KnowledgeStatus.RETIRED, KnowledgeStatus.DRAFT),
        (KnowledgeStatus.PUBLISHED, KnowledgeStatus.IN_REVIEW),
    ],
)
def test_skipped_or_backwards_transitions_are_rejected(
    current: KnowledgeStatus, target: KnowledgeStatus
) -> None:
    with pytest.raises(InvalidTransition):
        ensure_transition(current, target)


def test_reviewer_separation_rejects_self_review() -> None:
    author = "11111111-1111-1111-1111-111111111111"

    ensure_reviewer_separation(author, "22222222-2222-2222-2222-222222222222")
    with pytest.raises(SelfReviewForbidden):
        ensure_reviewer_separation(author, author)
