"""令牌桶限流（M7-03/T30）。

作用域：

- ``login``：按客户端 IP 限流；登录前没有用户身份，且不能让攻击者用无效
  用户名绕过；
- ``expensive``：按**服务端身份**（``user_id``）限流，覆盖
  ``POST /suggestions``、``POST /inspections``、``POST /reports`` 这些会调用
  模型或生成产物的端点。

返回：超限时抛 :class:`bank_assistant.api.errors.ApiError` 429，带
``retryable=true``、``Retry-After`` 秒数与 ``details.retry_after_seconds``，
沿用契约 §1 的错误结构。

限制：这是**单进程内存**令牌桶，适合当前一个 uvicorn worker 的部署；多实例
或重启会重置计数，不能当作计费依据。若将来横向扩展，需要换成 Redis/网关级
限流，届时保留同样的 429 语义即可。
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request

from bank_assistant.api.errors import ApiError
from bank_assistant.config.settings import settings
from bank_assistant.observability.metrics import RATE_LIMIT_REJECTIONS
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity

__all__ = [
    "RateLimitDecision",
    "TokenBucketLimiter",
    "client_ip",
    "enforce_expensive_rate_limit",
    "enforce_login_rate_limit",
    "reset_rate_limiters",
]

_MAX_BUCKETS = 4096
_STALE_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int
    remaining: float


class TokenBucketLimiter:
    """线程安全的令牌桶集合；每个 key 独立桶。

    ``rate_per_minute`` 与 ``burst`` 是配置；同一个 key 的配置变化会重置该桶，
    避免测试/热更新后出现过期状态。
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        # key -> (tokens, last_seen, rate_per_second, burst)
        self._buckets: dict[str, tuple[float, float, float, float]] = {}

    def consume(
        self,
        key: str,
        *,
        rate_per_minute: int,
        burst: int,
        cost: float = 1.0,
    ) -> RateLimitDecision:
        if rate_per_minute <= 0 or burst <= 0:
            raise ValueError("rate_per_minute 与 burst 必须为正整数")
        if cost <= 0:
            raise ValueError("cost 必须为正数")
        rate = rate_per_minute / 60.0
        now = self._clock()
        with self._lock:
            self._prune(now)
            tokens, last_seen, old_rate, old_burst = self._buckets.get(
                key, (float(burst), now, rate, float(burst))
            )
            if old_rate != rate or old_burst != float(burst):
                tokens, last_seen = float(burst), now
            else:
                elapsed = max(0.0, now - last_seen)
                tokens = min(float(burst), tokens + elapsed * rate)
            if tokens >= cost:
                tokens -= cost
                decision = RateLimitDecision(True, 0, tokens)
            else:
                needed = cost - tokens
                retry_after = max(1, math.ceil(needed / rate))
                decision = RateLimitDecision(False, retry_after, tokens)
            self._buckets[key] = (tokens, now, rate, float(burst))
        return decision

    def _prune(self, now: float) -> None:
        if len(self._buckets) <= _MAX_BUCKETS:
            return
        stale = [
            key
            for key, (_, last_seen, _, _) in self._buckets.items()
            if now - last_seen > _STALE_SECONDS
        ]
        for key in stale:
            self._buckets.pop(key, None)
        # 极端情况下仍超限：丢掉最旧的一半，保证内存有界。
        if len(self._buckets) > _MAX_BUCKETS:
            ordered = sorted(self._buckets.items(), key=lambda item: item[1][1])
            for key, _ in ordered[: len(self._buckets) // 2]:
                self._buckets.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_LIMITER = TokenBucketLimiter()


def client_ip(request: Request) -> str:
    """取客户端 IP；不信任 ``X-Forwarded-For``（当前没有反向代理）。"""
    client = request.client
    return client.host if client and client.host else "unknown"


def _reject(scope: str, retry_after: int) -> ApiError:
    RATE_LIMIT_REJECTIONS.inc({"scope": scope})
    return ApiError(
        status_code=429,
        code="RATE_LIMITED",
        message="请求过于频繁，请稍后重试",
        retryable=True,
        details={
            "scope": scope,
            "retry_after_seconds": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


def enforce_login_rate_limit(request: Request) -> None:
    """登录端点依赖：按 IP 限流；禁用时直接放行。"""
    if not settings.RATE_LIMIT_ENABLED:
        return
    decision = _LIMITER.consume(
        f"login:{client_ip(request)}",
        rate_per_minute=settings.RATE_LIMIT_LOGIN_PER_MINUTE,
        burst=settings.RATE_LIMIT_LOGIN_BURST,
    )
    if not decision.allowed:
        raise _reject("login", decision.retry_after_seconds)


def enforce_expensive_rate_limit(
    request: Request,
    identity: Identity = Depends(get_current_identity),
) -> None:
    """昂贵端点依赖：按用户限流；身份来自服务端，不能用请求体改变。"""
    if not settings.RATE_LIMIT_ENABLED:
        return
    decision = _LIMITER.consume(
        f"expensive:{identity.user_id}",
        rate_per_minute=settings.RATE_LIMIT_EXPENSIVE_PER_MINUTE,
        burst=settings.RATE_LIMIT_EXPENSIVE_BURST,
    )
    if not decision.allowed:
        raise _reject("expensive", decision.retry_after_seconds)


def reset_rate_limiters() -> None:
    """测试专用：清空所有令牌桶。"""
    _LIMITER.reset()
