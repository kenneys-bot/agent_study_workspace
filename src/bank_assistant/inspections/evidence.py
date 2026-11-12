"""证据坐标与严格校验（T19/M4-03，#20；契约 §4、§7 CT-08/CT-09）。

契约 §4 的坐标规则只有一条：**不可变 ``turn.content`` 的 Unicode 码点计数，
``start`` 含、``end`` 不含**。本模块把这条规则变成可复用、可测试的纯逻辑：

- :func:`code_point_to_utf16_offset` / :func:`utf16_offset_to_code_point`：
  前端 JavaScript 的字符串下标是 UTF-16 code unit，中文与 emoji 的宽度不同，
  必须显式换算；落在代理对中间的偏移直接拒绝，不静默对齐；
- :func:`check_span`：轮次归属、``0 <= start < end <= 码点长度``、``quote``
  与 ``content[start:end]`` **逐字相等**（不做 trim、空白折叠或 Unicode
  规范化）；
- :func:`annotate_evidence`：读取路径用；返回带轮次序号与 ``validated``
  标记的证据，坏数据只标为未通过，不让详情接口 500；
- :func:`ensure_hit_evidence_valid`：写入前的硬约束——``hit`` 至少要有一条
  能精确回验的原句，否则调用方必须拒绝写入。

本模块不访问数据库、不调用模型，也不改写任何原文。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

__all__ = [
    "EvidenceError",
    "SpanCheck",
    "annotate_evidence",
    "check_span",
    "code_point_length",
    "code_point_to_utf16_offset",
    "ensure_hit_evidence_valid",
    "slice_by_code_points",
    "utf16_length",
    "utf16_offset_to_code_point",
    "validate_evidence",
]


class EvidenceError(ValueError):
    """证据无法按契约 §4 回验；调用方必须显式拒绝，不能降级为“无问题”。"""


@dataclass(frozen=True, slots=True)
class SpanCheck:
    """单条证据的校验结果；``reason`` 只描述坐标问题，不含会话正文。"""

    valid: bool
    reason: str | None = None
    turn_id: UUID | None = None
    turn_ordinal: int | None = None
    start: int | None = None
    end: int | None = None
    quote: str | None = None


# ---------------------------------------------------------------------------
# 码点 / UTF-16 换算（CT-08）
# ---------------------------------------------------------------------------


def code_point_length(text: str) -> int:
    """Python 字符串长度就是 Unicode 码点数（CT-08 的后端基准）。"""
    return len(text)


def utf16_length(text: str) -> int:
    """按 UTF-16 code unit 计数，等价于 JavaScript ``string.length``。"""
    return len(text.encode("utf-16-le")) // 2


def _require_int(offset: object, *, name: str) -> int:
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError(f"{name} 必须是整数")
    return offset


def code_point_to_utf16_offset(text: str, offset: int) -> int:
    """码点下标 → UTF-16 下标；``offset`` 必须落在 ``[0, len(text)]``。"""
    value = _require_int(offset, name="码点偏移")
    if value < 0 or value > len(text):
        raise ValueError("码点偏移超出原文范围")
    return utf16_length(text[:value])


def utf16_offset_to_code_point(text: str, offset: int) -> int:
    """UTF-16 下标 → 码点下标；落在代理对中间时拒绝，不猜左或右。"""
    value = _require_int(offset, name="UTF-16 偏移")
    total = utf16_length(text)
    if value < 0 or value > total:
        raise ValueError("UTF-16 偏移超出原文范围")
    units = 0
    points = 0
    for char in text:
        if units == value:
            return points
        units += 2 if ord(char) > 0xFFFF else 1
        if units > value:
            raise ValueError("UTF-16 偏移落在代理对中间")
        points += 1
    return points


def slice_by_code_points(text: str, start: int, end: int) -> str:
    """按码点切片；等价于后端 ``content[start:end]``，便于前端预览证据。"""
    first = _require_int(start, name="start")
    last = _require_int(end, name="end")
    if first < 0 or last > len(text) or last <= first:
        raise ValueError("码点区间非法")
    return text[first:last]


# ---------------------------------------------------------------------------
# 单条证据校验
# ---------------------------------------------------------------------------


def _field(span: object, name: str) -> Any:
    if isinstance(span, Mapping):
        return span.get(name)
    return getattr(span, name, None)


def check_span(
    span: object,
    *,
    turn_contents: Mapping[UUID, str],
    turn_ordinals: Mapping[UUID, int] | None = None,
) -> SpanCheck:
    """校验一条证据；返回结构化结果而不是抛异常，供读取路径标注。"""
    raw_turn_id = _field(span, "turn_id")
    try:
        turn_id = UUID(str(raw_turn_id))
    except (ValueError, TypeError, AttributeError):
        return SpanCheck(valid=False, reason="证据缺少合法的 turn_id")

    raw_start = _field(span, "start")
    raw_end = _field(span, "end")
    raw_quote = _field(span, "quote")
    ordinal = (turn_ordinals or {}).get(turn_id)
    if (
        isinstance(raw_start, bool)
        or not isinstance(raw_start, int)
        or isinstance(raw_end, bool)
        or not isinstance(raw_end, int)
    ):
        return SpanCheck(
            valid=False,
            reason="证据 start/end 必须是整数",
            turn_id=turn_id,
            turn_ordinal=ordinal,
        )
    if not isinstance(raw_quote, str) or not raw_quote:
        return SpanCheck(
            valid=False,
            reason="证据 quote 必须是非空字符串",
            turn_id=turn_id,
            turn_ordinal=ordinal,
            start=raw_start,
            end=raw_end,
        )

    content = turn_contents.get(turn_id)
    if content is None:
        return SpanCheck(
            valid=False,
            reason=f"证据引用了不属于该会话的轮次 {turn_id}",
            turn_id=turn_id,
            turn_ordinal=ordinal,
            start=raw_start,
            end=raw_end,
            quote=raw_quote,
        )
    if raw_start < 0 or raw_end <= raw_start:
        return SpanCheck(
            valid=False,
            reason=f"证据区间非法：start={raw_start} end={raw_end}",
            turn_id=turn_id,
            turn_ordinal=ordinal,
            start=raw_start,
            end=raw_end,
            quote=raw_quote,
        )
    points = len(content)
    if raw_end > points:
        return SpanCheck(
            valid=False,
            reason=(
                f"证据区间超出原文范围：end={raw_end}，原文共 {points} 个码点"
            ),
            turn_id=turn_id,
            turn_ordinal=ordinal,
            start=raw_start,
            end=raw_end,
            quote=raw_quote,
        )
    actual = content[raw_start:raw_end]
    if actual != raw_quote:
        # 不做任何规范化：多一个空格、少一个 emoji、换行不同都算不匹配。
        return SpanCheck(
            valid=False,
            reason=(
                f"证据区间原文不匹配：原文 {actual!r}，声明 {raw_quote!r}"
            ),
            turn_id=turn_id,
            turn_ordinal=ordinal,
            start=raw_start,
            end=raw_end,
            quote=raw_quote,
        )
    return SpanCheck(
        valid=True,
        turn_id=turn_id,
        turn_ordinal=ordinal,
        start=raw_start,
        end=raw_end,
        quote=raw_quote,
    )


def validate_evidence(
    spans: Sequence[object],
    *,
    turn_contents: Mapping[UUID, str],
) -> None:
    """逐条校验证据；任一不通过即抛 :class:`EvidenceError`。"""
    for index, span in enumerate(spans, start=1):
        check = check_span(span, turn_contents=turn_contents)
        if not check.valid:
            raise EvidenceError(f"证据[{index}] 未通过校验：{check.reason}")


def ensure_hit_evidence_valid(
    spans: Sequence[object],
    *,
    turn_contents: Mapping[UUID, str],
) -> None:
    """``hit`` 的写入前硬约束：至少一条证据且全部逐字匹配原文。"""
    if not spans:
        raise EvidenceError("命中判定没有任何证据区间")
    validate_evidence(spans, turn_contents=turn_contents)


def annotate_evidence(
    spans: Sequence[object],
    *,
    turn_contents: Mapping[UUID, str],
    turn_ordinals: Mapping[UUID, int] | None = None,
) -> list[dict[str, Any]]:
    """读取路径：证据补上轮次序号与 ``validated`` 标记，坏数据显式标红。"""
    annotated: list[dict[str, Any]] = []
    for span in spans:
        check = check_span(
            span, turn_contents=turn_contents, turn_ordinals=turn_ordinals
        )
        raw_turn_id = _field(span, "turn_id")
        item: dict[str, Any] = {
            "turn_id": str(check.turn_id) if check.turn_id else str(raw_turn_id),
            "turn_ordinal": check.turn_ordinal,
            "start": check.start,
            "end": check.end,
            "quote": check.quote,
            "validated": check.valid,
        }
        if not check.valid:
            item["validation_error"] = check.reason
        annotated.append(item)
    return annotated
