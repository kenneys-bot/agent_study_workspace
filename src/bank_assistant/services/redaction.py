"""最小化敏感信息展示：只在读取响应里遮蔽，绝不改写存储原文。

契约 §4 要求证据坐标以不可变 ``turn.content`` 的 Unicode 码点计数。因此
脱敏只能发生在展示层：

- 数据库中的 ``conversations.raw_text`` 与 ``turns.content`` 永远是原文；
- 读取响应返回等长遮蔽后的展示文本（数字→``*``，分隔符与换行保留），
  并显式给出 ``redaction_applied`` 存储标志；
- 响应同时给出原文码点长度，调用方不得把展示文本的坐标当原文坐标使用。

遮蔽是幂等的：对已遮蔽文本再执行一次不会产生新的变化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MASK_CHAR = "*"

# 顺序有意义：先识别 18 位身份证，再识别更宽的银行卡号，避免身份证被
# 当成 18 位卡号；已遮蔽的字符不再是数字，后续模式不会重复命中。
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 18 位中华人民共和国居民身份证（末位可为 X）。
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    # 16—19 位银行卡号/账号，允许空格或短横线分组。
    re.compile(r"(?<!\d)\d(?:[ -]?\d){15,18}(?!\d)"),
    # 11 位中国大陆手机号。
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """遮蔽结果：展示文本与是否实际命中敏感片段。"""

    text: str
    applied: bool


def _mask_span(match: re.Match[str]) -> str:
    """逐字符遮蔽数字与身份证校验位 X，保留分隔符与原文长度。"""
    return "".join(
        MASK_CHAR if char.isdigit() or char in "xX" else char
        for char in match.group(0)
    )


def redact_sensitive_text(text: str) -> RedactionResult:
    """返回展示用文本；``applied`` 表示是否命中敏感片段。"""
    masked = text
    applied = False
    for pattern in _PATTERNS:
        masked, hits = pattern.subn(_mask_span, masked)
        applied = applied or hits > 0
    return RedactionResult(text=masked, applied=applied)
