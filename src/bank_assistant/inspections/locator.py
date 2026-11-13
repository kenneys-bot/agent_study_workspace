"""quote-first 证据定位（T40，#40；R-19；契约 §4）。

T39 的 live 质检暴露了「把模型自报的数字 offset 当权威坐标」的脆弱性：模型经常
把 ``end`` 少算/多算一个码点、越界，或给 quote 补一个原文没有的句号，导致整次
质检以 ``EVIDENCE_INVALID`` 失败。本模块把定位职责收回后端：

1. **精确匹配**：在不可变 ``turn.content`` 上做逐字子串搜索（Python 字符串即
   Unicode 码点），命中区间就是原文码点区间；
2. **归一化匹配**：精确找不到时，用 NFKC + 空白/标点归一化再做一次搜索，并把
   归一化串上的命中区间映射回**原码点区间**；映射后还要复核
   ``normalize(content[start:end]) == normalize(quote)``，避免把 NFKC 展开的
   半个字符算进证据；
3. **找不到**：返回 ``None``，调用方把该条规则降级为 ``insufficient_evidence``
   （``EVIDENCE_NOT_LOCATABLE``），而不是让整次运行失败。

模型给的 ``turn_id`` / ``turn_ordinal`` / ``start`` / ``end`` 一律只是**提示**：
可选值只用于在多个出现位置之间消歧；定位结果永远以原文为准。持久化的
``quote`` 一律取 ``content[start:end]``，因此 ``quote == content[start:end]``
在写入时就成立。

跨轮次不拼接：quote 必须在**单条**轮次内命中；拼两轮才能得到的片段视为找不到，
避免证据跨越说话人边界后被误读。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

__all__ = [
    "LocatedQuote",
    "MatchKind",
    "locate_quote",
    "normalize_for_match",
]


MatchKind = Literal["exact", "normalized"]


@dataclass(frozen=True, slots=True)
class LocatedQuote:
    """一次成功的定位：``quote`` 永远等于 ``content[start:end]``。"""

    turn_id: UUID
    turn_ordinal: int | None
    start: int
    end: int
    quote: str
    match: MatchKind

    @property
    def exact(self) -> bool:
        return self.match == "exact"


@dataclass(frozen=True, slots=True)
class _NormalizedText:
    """归一化文本 + 每个归一化字符对应的原码点下标。"""

    text: str
    source_indexes: tuple[int, ...]


def _fold_character(char: str) -> str:
    """单字符归一化：NFKC，随后丢弃空白与标点。"""
    folded = unicodedata.normalize("NFKC", char)
    return "".join(
        piece
        for piece in folded
        if not piece.isspace() and not unicodedata.category(piece).startswith("P")
    )


def normalize_for_match(text: str) -> _NormalizedText:
    """把原文/quote 归一化为可比较串，并保留回原码点下标的映射。

    - NFKC：全角/半角、兼容字符、部分连字都会被折叠；
    - 空白与标点整体丢弃：模型补一个句号、把换行写成空格、quote 里没有逗号，
      都不再是否决条件；
    - ``source_indexes[i]`` 是归一化串第 ``i`` 个字符来自的原码点下标，因此
      命中区间可以严格映射回原文。
    """
    pieces: list[str] = []
    indexes: list[int] = []
    for position, char in enumerate(text):
        folded = _fold_character(char)
        for piece in folded:
            pieces.append(piece)
            indexes.append(position)
    return _NormalizedText("".join(pieces), tuple(indexes))


def _ordered_turns(
    turns: Mapping[UUID, str],
    ordinals: Mapping[UUID, int] | None,
    hint_turn_id: UUID | None,
    hint_ordinal: int | None,
) -> list[tuple[UUID, str]]:
    """轮次顺序：提示轮次优先，其余按 ordinal、再按 UUID 稳定排序。"""
    ordinal_map = dict(ordinals or {})
    ordered = sorted(
        turns.items(),
        key=lambda item: (ordinal_map.get(item[0], 1 << 30), str(item[0])),
    )
    hinted = hint_turn_id
    if hinted is None and hint_ordinal is not None:
        hinted = next(
            (turn_id for turn_id, value in ordinal_map.items() if value == hint_ordinal),
            None,
        )
    if hinted is not None and hinted in turns:
        ordered = [(hinted, turns[hinted])] + [
            item for item in ordered if item[0] != hinted
        ]
    return ordered


def _exact_position(
    content: str, quote: str, hint_start: int | None
) -> int | None:
    positions: list[int] = []
    position = content.find(quote)
    while position != -1:
        positions.append(position)
        position = content.find(quote, position + 1)
    if not positions:
        return None
    if hint_start is not None and hint_start in positions:
        return hint_start
    return positions[0]


def _normalized_span(
    content: str, normalized_quote: str, hint_start: int | None
) -> tuple[int, int] | None:
    normalized_content = normalize_for_match(content)
    haystack = normalized_content.text
    if not haystack or not normalized_quote:
        return None

    first_valid: tuple[int, int] | None = None
    position = haystack.find(normalized_quote)
    while position != -1:
        last = position + len(normalized_quote) - 1
        source = normalized_content.source_indexes
        start = source[position]
        end = source[last] + 1
        # NFKC 可能把一个原字符展开成多个归一化字符。只有整段原文重新归一化后
        # 与 quote 完全一致，才承认这个区间，避免半个字符/多余字符进入证据。
        candidate = content[start:end]
        if (
            end > start
            and normalize_for_match(candidate).text == normalized_quote
        ):
            if hint_start is not None and start == hint_start:
                return start, end
            if first_valid is None:
                first_valid = (start, end)
        position = haystack.find(normalized_quote, position + 1)
    return first_valid


def locate_quote(
    quote: str,
    *,
    turns: Mapping[UUID, str],
    turn_ordinals: Mapping[UUID, int] | None = None,
    turn_id: UUID | None = None,
    turn_ordinal: int | None = None,
    start: int | None = None,
    end: int | None = None,
) -> LocatedQuote | None:
    """在单条轮次内定位 quote；找不到返回 ``None``（调用方不得让 run 失败）。

    所有位置参数都只是提示：``turn_id`` / ``turn_ordinal`` 决定优先搜索哪一轮，
    ``start`` 用于在多个精确出现位置间消歧；数字 offset 绝不作为最终坐标。
    """
    if not isinstance(quote, str) or not quote:
        return None
    ordinal_map = dict(turn_ordinals or {})
    candidates = _ordered_turns(turns, ordinal_map, turn_id, turn_ordinal)

    # 第一轮：精确匹配优先——精度最高，且不改变原文语义。
    for candidate_id, content in candidates:
        position = _exact_position(content, quote, start)
        if position is not None:
            return LocatedQuote(
                turn_id=candidate_id,
                turn_ordinal=ordinal_map.get(candidate_id),
                start=position,
                end=position + len(quote),
                quote=quote,
                match="exact",
            )

    # 第二轮：归一化匹配，命中区间映射回原码点。
    normalized_quote = normalize_for_match(quote).text
    if not normalized_quote:
        return None
    for candidate_id, content in candidates:
        span = _normalized_span(content, normalized_quote, start)
        if span is None:
            continue
        first, last = span
        return LocatedQuote(
            turn_id=candidate_id,
            turn_ordinal=ordinal_map.get(candidate_id),
            start=first,
            end=last,
            quote=content[first:last],
            match="normalized",
        )
    return None
