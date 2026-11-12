"""确定性否定与引用/转述检查（T19/M4-03，#20）。

M4-02 的要求是「否定和引用他人话语不能按关键词直接判定」。模型可能会把
“没有收取年费”“客户说有人告诉他可以免年费”当成命中，因此写入前还要有一层
**确定性**（不依赖模型、可复现）的语境检查：

- **否定**：否定标记必须出现在风险动作之前、且在同一分句内，才否定该动作。
  这样“投诉也没用”中的“没用”不会推翻“投诉”本身，而“没有收取年费”
  “不会向您索要验证码”会被正确排除；
- **引用/转述**：命中片段之前（同一句内）出现“客户说/他说/有人告诉他/
  听说/据说/转述/引用”等标记，或片段本身以这类标记开头，就按引用他人话语
  处理，不算坐席自身行为；
- 分句边界（逗号、句号、分号、换行等）限制否定作用域；转述标记允许跨逗号
  （“客户说有人告诉他，可以免年费”），但不跨句号。

这是保守的规则检查，不是通用中文语法分析：它只用于拒绝明显不成立的
``hit`` 证据，不负责自动生成命中。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "ATTRIBUTION_MARKERS",
    "NEGATION_MARKERS",
    "ContextAssessment",
    "KeywordOccurrence",
    "actionable_occurrences",
    "assess_context",
    "scan_keywords",
]

# 只收组合词，避免把“不错/不仅/不过”里的“不”当成否定。
NEGATION_MARKERS: tuple[str, ...] = (
    "没有",
    "没能",
    "未曾",
    "未能",
    "未",
    "不会",
    "不能",
    "不用",
    "不要",
    "无需",
    "无须",
    "并非",
    "不是",
    "不再",
    "不予",
    "别",
    "没",
)

# 必须是指向第三方话语的固定搭配，避免把“说明/告知”当成转述。
ATTRIBUTION_MARKERS: tuple[str, ...] = (
    "他说",
    "她说",
    "客户说",
    "客户称",
    "客户反映",
    "有人告诉",
    "他告诉我",
    "她告诉我",
    "听说",
    "据说",
    "转述",
    "引用",
    "别人说",
    "对方说",
)

_CLAUSE_BOUNDARIES = "。！？；\n，,;!?、：:"
_SENTENCE_BOUNDARIES = "。！？；\n"
_CLAUSE_PATTERN = re.compile(f"[{re.escape(_CLAUSE_BOUNDARIES)}]")
_SENTENCE_PATTERN = re.compile(f"[{re.escape(_SENTENCE_BOUNDARIES)}]")


@dataclass(frozen=True, slots=True)
class ContextAssessment:
    """一段原文区间的确定性语境判断。"""

    negated: bool
    attributed: bool

    @property
    def supported(self) -> bool:
        """不是否定、也不是引用他人话语，才可作为坐席自身行为的证据。"""
        return not self.negated and not self.attributed


@dataclass(frozen=True, slots=True)
class KeywordOccurrence:
    """一个风险关键词出现位置及其语境判断；偏移是 Unicode 码点。"""

    keyword: str
    start: int
    end: int
    negated: bool
    attributed: bool

    @property
    def supported(self) -> bool:
        return not self.negated and not self.attributed


def _clause_prefix(text: str, start: int) -> str:
    boundaries = [match.end() for match in _CLAUSE_PATTERN.finditer(text, 0, start)]
    return text[boundaries[-1] : start] if boundaries else text[:start]


def _sentence_prefix(text: str, start: int) -> str:
    boundaries = [match.end() for match in _SENTENCE_PATTERN.finditer(text, 0, start)]
    return text[boundaries[-1] : start] if boundaries else text[:start]


def _starts_with_marker(segment: str, markers: Sequence[str]) -> bool:
    return any(segment.startswith(marker) for marker in markers)


def _contains_marker(segment: str, markers: Sequence[str]) -> bool:
    return any(marker in segment for marker in markers)


def assess_context(text: str, start: int, end: int) -> ContextAssessment:
    """判断 ``text[start:end]`` 是否处于否定或引用语境。

    否定只看当前分句前缀与片段开头；转述看整句前缀与片段开头，因此引用语
    在逗号后继续时仍会被识别。
    """
    if start < 0 or end <= start or end > len(text):
        raise ValueError("证据区间非法")
    segment = text[start:end]
    negated = _starts_with_marker(segment, NEGATION_MARKERS) or _contains_marker(
        _clause_prefix(text, start), NEGATION_MARKERS
    )
    attributed = _starts_with_marker(
        segment, ATTRIBUTION_MARKERS
    ) or _contains_marker(_sentence_prefix(text, start), ATTRIBUTION_MARKERS)
    return ContextAssessment(negated=negated, attributed=attributed)


def scan_keywords(text: str, keywords: Iterable[str]) -> list[KeywordOccurrence]:
    """扫描关键词的全部出现位置；不重叠、按出现顺序返回。"""
    occurrences: list[KeywordOccurrence] = []
    for keyword in keywords:
        if not keyword:
            continue
        position = text.find(keyword)
        while position != -1:
            end = position + len(keyword)
            assessment = assess_context(text, position, end)
            occurrences.append(
                KeywordOccurrence(
                    keyword=keyword,
                    start=position,
                    end=end,
                    negated=assessment.negated,
                    attributed=assessment.attributed,
                )
            )
            position = text.find(keyword, position + 1)
    occurrences.sort(key=lambda item: (item.start, item.end))
    return occurrences


def actionable_occurrences(
    text: str, keywords: Iterable[str]
) -> list[KeywordOccurrence]:
    """只返回未被否定、也不是引用他人话语的关键词出现位置。"""
    return [
        occurrence
        for occurrence in scan_keywords(text, keywords)
        if occurrence.supported
    ]
