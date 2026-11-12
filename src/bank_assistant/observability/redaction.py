"""日志脱敏：兜底清除已知秘密形态（M7-02/T30）。

应用层的硬约束是**不把会话原文、请求体、令牌或凭据写进日志**；本模块是
第二道防线，只处理已经进入日志字符串的秘密形态，不能替代“不记录敏感内容”
的编码约定。

覆盖：

- 数据库/缓存连接串中的密码：``postgresql+psycopg://user:pass@host/db``；
- 常见 API key：``sk-...``、``pk-...`` 以及 ``DEEPSEEK_API_KEY=...`` 等
  键值对；
- ``Authorization: Bearer ...`` 与裸 JWT；
- ``password`` / ``secret`` / ``token`` 等键值对。

不处理中文会话正文——那必须靠“不记录请求体”来保证，而不是靠正则猜测。
"""

from __future__ import annotations

import logging
import re

__all__ = ["RedactingFilter", "redact_log_text", "install_redacting_filter"]

_REDACTED = "***"

# 连接串：只替换 userinfo 中的密码，保留 scheme/host/db 便于定位环境。
_URL_WITH_CREDENTIALS = re.compile(
    r"(?P<scheme>\b(?:postgresql|postgres|mysql|redis|mongodb)(?:\+[a-z0-9_]+)?://)"
    r"(?P<user>[^:/@\s]+):(?P<password>[^@/\s]+)@",
    re.IGNORECASE,
)

_API_KEY = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}\b")
_BEARER = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._\-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
_KV_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret[_-]?key|secret|password|passwd|token|access[_-]?token)"
    r"(\s*[:=]\s*)([\"']?)([^\s\"',;}\]&]{4,})"
)


def redact_log_text(text: str) -> str:
    """返回脱敏后的文本；幂等，可安全重复应用。"""
    if not text:
        return text
    redacted = _URL_WITH_CREDENTIALS.sub(
        lambda match: (
            f"{match.group('scheme')}{match.group('user')}:{_REDACTED}@"
        ),
        text,
    )
    redacted = _API_KEY.sub(f"sk-{_REDACTED}", redacted)
    redacted = _BEARER.sub(lambda match: f"{match.group(1)} {_REDACTED}", redacted)
    redacted = _JWT.sub(_REDACTED, redacted)
    redacted = _KV_SECRET.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}{_REDACTED}"
        ),
        redacted,
    )
    return redacted


class RedactingFilter(logging.Filter):
    """在日志记录进入处理器前脱敏消息与异常栈。

    放在 logger（而非 handler）上，保证 caplog、文件、stdout 等所有处理器
    看到的是同一份脱敏结果。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 格式化失败时保持原记录
            return True
        redacted = redact_log_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_info:
            # 预先生成脱敏后的异常文本，Formatter 会直接使用它。
            record.exc_text = redact_log_text(
                logging.Formatter().formatException(record.exc_info)
            )
        return True


def install_redacting_filter(logger: logging.Logger | None = None) -> None:
    """幂等地给 logger（默认 root）安装脱敏过滤器。"""
    target = logger or logging.getLogger()
    if any(isinstance(item, RedactingFilter) for item in target.filters):
        return
    target.addFilter(RedactingFilter())
