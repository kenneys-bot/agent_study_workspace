"""知识分块：把知识版本正文切成可引用的原文区间（M3，契约 §2/§4）。

坐标契约：``TextChunk.start`` 含、``end`` 不含，按 Unicode **码点**计数
（Python ``str`` 下标即码点；前端需显式把码点转成 UTF-16 索引）。每个分块
满足 ``chunk.text == content[start:end]``，引用因此可以逐字回验原文。

切分策略（确定性、可重建）：

1. 先按段落（换行）切；段落放得下就整段保留；
2. 超长段落再按句末标点（。！？；!?;）切；
3. 仍超长时按 ``max_chars`` 硬切并保留 ``overlap`` 个码点的重叠，避免把
   连续表述切断后检索不到；
4. 最后把相邻且合并后不超限的片段装进同一分块。

本模块不依赖数据库与模型，便于离线单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 单个分块的目标上限（码点）。演示知识条目通常 100~400 字，300 能保持
# 语义完整，又不会让 embedding 输入过长。
DEFAULT_MAX_CHARS = 300
DEFAULT_OVERLAP_CHARS = 40

_SENTENCE_END = re.compile(r"[。！？；!?;\n]")


@dataclass(frozen=True, slots=True)
class TextChunk:
    """一个原文区间；``text`` 必须等于 ``content[start:end]``。"""

    start: int
    end: int
    text: str

    @property
    def length(self) -> int:
        return self.end - self.start


def _trimmed_span(content: str, start: int, end: int) -> tuple[int, int]:
    """收缩到首个/末个非空白码点，返回可能为空的区间。"""
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return start, end


def _paragraph_spans(content: str) -> list[tuple[int, int]]:
    """按换行切段落；用 ``splitlines(keepends=True)`` 保证偏移累积等于 ``len``。"""
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in content.splitlines(keepends=True):
        span = _trimmed_span(content, offset, offset + len(line))
        offset += len(line)
        if span[0] < span[1]:
            spans.append(span)
    if offset < len(content):  # 理论上 splitlines 已覆盖全部字符
        span = _trimmed_span(content, offset, len(content))
        if span[0] < span[1]:
            spans.append(span)
    return spans


def _sentence_spans(
    content: str, start: int, end: int
) -> list[tuple[int, int]]:
    """按句末标点切分一个超长段落，保留标点在句内。"""
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_END.finditer(content, start, end):
        span = _trimmed_span(content, cursor, match.end())
        if span[0] < span[1]:
            spans.append(span)
        cursor = match.end()
    span = _trimmed_span(content, cursor, end)
    if span[0] < span[1]:
        spans.append(span)
    return spans


def _hard_split(
    content: str, start: int, end: int, *, max_chars: int, overlap: int
) -> list[tuple[int, int]]:
    """按固定窗口硬切，窗口之间保留 ``overlap`` 个码点。"""
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + max_chars, end)
        span = _trimmed_span(content, cursor, window_end)
        if span[0] < span[1]:
            spans.append(span)
        if window_end >= end:
            break
        next_cursor = window_end - overlap
        cursor = next_cursor if next_cursor > cursor else cursor + 1
    return spans


def _pack(
    content: str, spans: list[tuple[int, int]], *, max_chars: int
) -> list[TextChunk]:
    """把相邻片段合成不超过上限的分块；重叠片段不合并。"""
    chunks: list[TextChunk] = []
    current_start: int | None = None
    current_end = 0
    for start, end in spans:
        if current_start is None:
            current_start, current_end = start, end
            continue
        gap = content[current_end:start]
        mergeable = (
            start >= current_end
            and not gap.strip()
            and end - current_start <= max_chars
        )
        if mergeable:
            current_end = end
        else:
            chunks.append(
                TextChunk(
                    start=current_start,
                    end=current_end,
                    text=content[current_start:current_end],
                )
            )
            current_start, current_end = start, end
    if current_start is not None:
        chunks.append(
            TextChunk(
                start=current_start,
                end=current_end,
                text=content[current_start:current_end],
            )
        )
    return chunks


def chunk_text(
    content: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[TextChunk]:
    """把正文切成有序的原文区间；空白内容返回空列表。"""
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正整数")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap 必须满足 0 <= overlap < max_chars")
    if not content or not content.strip():
        return []

    spans: list[tuple[int, int]] = []
    for paragraph_start, paragraph_end in _paragraph_spans(content):
        if paragraph_end - paragraph_start <= max_chars:
            spans.append((paragraph_start, paragraph_end))
            continue
        for sentence_start, sentence_end in _sentence_spans(
            content, paragraph_start, paragraph_end
        ):
            if sentence_end - sentence_start <= max_chars:
                spans.append((sentence_start, sentence_end))
            else:
                spans.extend(
                    _hard_split(
                        content,
                        sentence_start,
                        sentence_end,
                        max_chars=max_chars,
                        overlap=overlap,
                    )
                )
    return _pack(content, spans, max_chars=max_chars)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "TextChunk",
    "chunk_text",
]
