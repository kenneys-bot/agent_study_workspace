"""知识版本生命周期：状态、内容哈希与有效期判定（M2-03）。

契约 §3「知识与规则」：

- ``draft → in_review → published → retired``；审核退回为 draft；
- 修改已发布内容必须创建新版本，已发布内容不可覆盖；
- 发布前验证来源、范围与时间；同一知识的冲突有效版本必须被检测并显式
  处理，不能静默覆盖。

本模块只放可在无数据库情况下验证的纯逻辑；状态落库与授权在
``bank_assistant.knowledge.service``，检索过滤在
``bank_assistant.knowledge.retrieval``。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

# GLOSSARY「知识来源」：本项目只有公开监管条文与演示制度两类。
# 演示制度必须显式标注（business 侧展示），不得与真实制度混同。
SOURCE_TYPES: frozenset[str] = frozenset({"public_regulation", "demo_policy"})


class KnowledgeStatus(StrEnum):
    """知识版本在生命周期中的阶段；只有 PUBLISHED 能进入正式检索。"""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    RETIRED = "retired"


class SourceType(StrEnum):
    """知识来源类别（GLOSSARY「知识来源」）。"""

    PUBLIC_REGULATION = "public_regulation"
    DEMO_POLICY = "demo_policy"


class KnowledgeError(Exception):
    """知识生命周期的领域错误基类；API 层统一映射为契约 §1 错误结构。"""


class InvalidTransition(KnowledgeError):
    """状态机不允许的转换；调用方应返回 409，而不是悄悄改状态。"""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"知识版本状态不能从 {current} 变为 {target}")


class InvalidWindow(KnowledgeError, ValueError):
    """有效期区间非法（起始晚于结束、起止相同或时区不一致）。"""


class SelfReviewForbidden(KnowledgeError):
    """审核人不能是作者本人（发布者与作者分离）。"""

    def __init__(self, author_id: Any) -> None:
        self.author_id = author_id
        super().__init__("审核人不能是作者本人")


class PublishedContentImmutable(KnowledgeError):
    """已发布版本的内容不可覆盖；修改变更必须新建版本（契约 §3）。"""

    def __init__(self, version_id: Any) -> None:
        self.version_id = version_id
        super().__init__("已发布的知识版本内容不可修改")


class RevisionConflict(KnowledgeError):
    """客户端提交的 revision 已过期；返回 409，不覆盖并发更新。"""

    def __init__(self, expected: int, current: int) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f"revision 冲突：期望 {expected}，当前 {current}")


ALLOWED_TRANSITIONS: Mapping[KnowledgeStatus, frozenset[KnowledgeStatus]] = {
    KnowledgeStatus.DRAFT: frozenset({KnowledgeStatus.IN_REVIEW}),
    KnowledgeStatus.IN_REVIEW: frozenset(
        {KnowledgeStatus.PUBLISHED, KnowledgeStatus.DRAFT}
    ),
    KnowledgeStatus.PUBLISHED: frozenset({KnowledgeStatus.RETIRED}),
    KnowledgeStatus.RETIRED: frozenset(),
}


def _coerce_status(value: KnowledgeStatus | str) -> KnowledgeStatus:
    return value if isinstance(value, KnowledgeStatus) else KnowledgeStatus(value)


def ensure_transition(
    current: KnowledgeStatus | str, target: KnowledgeStatus | str
) -> None:
    """校验一次状态转换是否在契约允许的路径内。"""
    source = _coerce_status(current)
    destination = _coerce_status(target)
    if destination not in ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise InvalidTransition(source.value, destination.value)


def hash_content(content: str) -> str:
    """内容哈希（SHA-256 hex），用于索引重建与内容不可变校验。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def windows_overlap(
    left_from: datetime,
    left_to: datetime | None,
    right_from: datetime,
    right_to: datetime | None,
) -> bool:
    """两个半开区间 ``[valid_from, valid_to)`` 是否重叠。

    ``valid_to is None`` 表示持续有效。相接的区间（前者结束 == 后者开始）
    不重叠：同一时刻只能有一个有效版本。
    """
    left_before_right_ends = right_to is None or left_from < right_to
    right_before_left_ends = left_to is None or right_from < left_to
    return left_before_right_ends and right_before_left_ends


def validate_window(valid_from: datetime, valid_to: datetime | None) -> None:
    """有效期必须非空且能比较；时区混用直接拒绝，避免比较结果不可解释。"""
    if valid_to is None:
        return
    if (valid_from.tzinfo is None) != (valid_to.tzinfo is None):
        raise InvalidWindow("有效期的开始与结束必须同为带时区或同为不带时区")
    if valid_to <= valid_from:
        raise InvalidWindow("有效期结束必须晚于开始")


def ensure_reviewer_separation(author_id: UUID | str, reviewer_id: UUID | str) -> None:
    """发布者（审核人）不能是作者；作者自审直接拒绝（M2-03）。"""
    if UUID(str(author_id)) == UUID(str(reviewer_id)):
        raise SelfReviewForbidden(author_id)
