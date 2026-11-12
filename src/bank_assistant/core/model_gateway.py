"""统一模型适配层。

生成默认使用 DeepSeek 官方（`deepseek-chat`）；第三方 OpenAI 兼容中继只作
对比与显式回退。核心约束（ADR-0004 / M1-04）：

- 凭据或 SDK 缺失时抛 `CapabilityUnavailableError`，不自动降级到 mock。
- 结构化输出必须通过 pydantic schema 校验；非 JSON、schema 不匹配、
  `finish_reason=length` 且 content 为空都是失败，不是空答复。
- 失败分类与业务状态分离；系统失败不得伪装成 `needs_clarification` /
  `needs_human`。
- 回放（replay）只能显式开启，并在结果与日志中标记 `mode=replay`。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Sequence, Type

import httpx
from pydantic import BaseModel, ValidationError

from bank_assistant.config.settings import settings
from bank_assistant.core.errors import CapabilityUnavailableError
from bank_assistant.observability.metrics import (
    MODEL_CALLS,
    MODEL_LATENCY,
    MODEL_TOKENS,
)

logger = logging.getLogger(__name__)


class ModelProvider(str, Enum):
    """可用的 OpenAI 兼容供应商。"""

    DEEPSEEK = "deepseek"
    RELAY = "relay"


class ModelCallStatus(str, Enum):
    """模型调用失败分类。

    与业务状态（answered / needs_clarification / needs_human / failed）分离：
    系统失败只能映射到 `failed`，不能产生业务追问或转人工状态。
    """

    SUCCEEDED = "succeeded"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    INVALID_STRUCTURE = "invalid_structure"
    EMPTY_CONTENT = "empty_content"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


ReplayResponder = Callable[
    [Sequence[Mapping[str, str]], Optional[Type[BaseModel]]], str
]


@dataclass(frozen=True)
class ProviderSpec:
    """单个 OpenAI 兼容端点的配置。"""

    provider: ModelProvider
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0
    max_tokens: int = 3000

    def __post_init__(self) -> None:
        if not self.api_key:
            raise CapabilityUnavailableError(
                f"capability unavailable: 供应商 {self.provider.value} 缺少 API key"
                "（请配置环境变量，不要把凭据写进代码）"
            )
        if not self.base_url:
            raise CapabilityUnavailableError(
                f"capability unavailable: 供应商 {self.provider.value} 缺少 base_url"
            )
        if not self.model:
            raise CapabilityUnavailableError(
                f"capability unavailable: 供应商 {self.provider.value} 缺少模型标识"
            )


@dataclass(frozen=True)
class ModelResult:
    """一次模型调用的结果；失败时 `data`/`text` 不作为业务内容使用。"""

    status: ModelCallStatus
    provider: Optional[ModelProvider] = None
    model: str = ""
    mode: str = "live"
    text: str = ""
    data: Any = None
    finish_reason: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 1
    fallback_used: bool = False
    # 真实调用返回的 usage；回放/失败调用为 0。只用于聚合指标，不含内容。
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.status is ModelCallStatus.SUCCEEDED


_RETRYABLE = {ModelCallStatus.TIMEOUT, ModelCallStatus.PROVIDER_UNAVAILABLE}


class ModelGateway:
    """唯一的模型调用入口。"""

    def __init__(
        self,
        *,
        providers: Optional[Mapping[ModelProvider, ProviderSpec]] = None,
        primary: ModelProvider = ModelProvider.DEEPSEEK,
        fallback: Optional[ModelProvider] = None,
        replay_responses: Optional[Sequence[str]] = None,
        replay_responder: Optional[ReplayResponder] = None,
        http_client: Optional[httpx.Client] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self._replay = replay_responses is not None or replay_responder is not None
        self._replay_responses = list(replay_responses or [])
        self._replay_responder = replay_responder
        self._replay_index = 0
        self._max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
        self._http_client = http_client
        self._clients: dict[ModelProvider, Any] = {}

        if self._replay:
            # 显式回放：无需凭据，但必须在日志与结果里可见。
            self._providers: dict[ModelProvider, ProviderSpec] = {}
            self._primary = primary
            self._fallback = None
            self._mode = "replay"
            logger.warning(
                "MODEL REPLAY MODE ENABLED: 显式回放模式，不会调用真实模型（mode=replay）"
            )
            return

        self._providers = dict(providers) if providers is not None else self._load_providers()
        if primary not in self._providers:
            raise CapabilityUnavailableError(
                f"capability unavailable: 供应商 {primary.value} 未配置"
                "（需要 base_url、API key 与模型标识）"
            )
        if fallback is not None and fallback not in self._providers:
            raise CapabilityUnavailableError(
                f"capability unavailable: 回退供应商 {fallback.value} 未配置"
            )
        self._primary = primary
        self._fallback = fallback
        self._mode = "live"
        self._import_sdk()

    # ------------------------------------------------------------------ 配置
    @staticmethod
    def _load_providers() -> dict[ModelProvider, ProviderSpec]:
        """从 settings/env 读取供应商配置，只返回凭据齐全的项。"""
        providers: dict[ModelProvider, ProviderSpec] = {}
        timeout = settings.LLM_TIMEOUT_SECONDS
        max_tokens = settings.LLM_MAX_TOKENS_CHAT

        if settings.DEEPSEEK_API_KEY:
            providers[ModelProvider.DEEPSEEK] = ProviderSpec(
                provider=ModelProvider.DEEPSEEK,
                base_url=settings.DEEPSEEK_BASE_URL,
                api_key=settings.DEEPSEEK_API_KEY,
                model=settings.DEEPSEEK_MODEL,
                timeout_seconds=timeout,
                max_tokens=max_tokens,
            )
        if settings.LLM_API_KEY and settings.LLM_BASE_URL and settings.LLM_MODEL_CHAT:
            providers[ModelProvider.RELAY] = ProviderSpec(
                provider=ModelProvider.RELAY,
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                model=settings.LLM_MODEL_CHAT,
                timeout_seconds=timeout,
                max_tokens=max_tokens,
            )
        return providers

    @classmethod
    def from_settings(cls, **kwargs: Any) -> "ModelGateway":
        """按环境变量构建；缺少主供应商凭据时直接抛能力不可用。

        ``MODEL_MODE=replay`` 是显式回放：不需要云凭据，但必须使用确定性的
        回放处理器并在结果里标记 ``mode=replay``；它不会回退到 live，也不会
        把回放内容当成真实模型结论。
        """
        if settings.MODEL_MODE == "replay":
            from bank_assistant.demo.replay import DemoReplayResponder

            replay_responses = kwargs.pop("replay_responses", None)
            if replay_responses is not None:
                return cls(replay_responses=replay_responses, **kwargs)
            return cls(replay_responder=DemoReplayResponder(), **kwargs)

        provider = _provider_from_name(settings.MODEL_PROVIDER)
        fallback = (
            _provider_from_name(settings.MODEL_FALLBACK_PROVIDER)
            if settings.MODEL_FALLBACK_PROVIDER
            else None
        )
        return cls(providers=cls._load_providers(), primary=provider, fallback=fallback, **kwargs)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers)

    @property
    def primary(self) -> ModelProvider:
        return self._primary

    # ------------------------------------------------------------------ 调用
    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema: Optional[Type[BaseModel]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
    ) -> ModelResult:
        """调用模型；可选地把 content 解析并按 `schema` 校验。

        所有路径都在这里统一记录最终结果与耗时指标（失败也计数），回放同样
        计入，但 ``mode`` 标签会如实区分 live/replay。
        """
        started = time.perf_counter()
        if self._replay:
            result = self._complete_replay(messages, schema)
        else:
            result = self._complete_live(messages, schema, max_tokens, temperature)
        self._record_result(result, time.perf_counter() - started)
        self._log_result(result)
        return result

    def structured(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: Type[BaseModel],
        **kwargs: Any,
    ) -> ModelResult:
        """结构化输出：等价于 complete(..., schema=schema)。"""
        return self.complete(messages, schema=schema, **kwargs)

    def _complete_replay(
        self, messages: Sequence[Mapping[str, str]], schema: Optional[Type[BaseModel]]
    ) -> ModelResult:
        if self._replay_responder is not None:
            try:
                raw = self._replay_responder(messages, schema)
            except Exception as exc:  # 回放脚本失败同样必须显式失败
                return ModelResult(
                    status=ModelCallStatus.PROVIDER_UNAVAILABLE,
                    model="replay",
                    mode="replay",
                    error=f"replay_error: {type(exc).__name__}: {_short(exc)}",
                )
            return self._interpret_text(
                raw, "stop", schema, None, "replay", "replay", 1, False
            )
        if self._replay_index >= len(self._replay_responses):
            return ModelResult(
                status=ModelCallStatus.PROVIDER_UNAVAILABLE,
                model="replay",
                mode="replay",
                error="replay_exhausted: 显式回放内容已用完",
            )
        raw = self._replay_responses[self._replay_index]
        self._replay_index += 1
        return self._interpret_text(raw, "stop", schema, None, "replay", "replay", 1, False)

    def _complete_live(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: Optional[Type[BaseModel]],
        max_tokens: Optional[int],
        temperature: float,
    ) -> ModelResult:
        order = [self._primary]
        if self._fallback is not None and self._fallback != self._primary:
            order.append(self._fallback)

        attempts = 0
        last_error = "模型调用失败"
        for index, provider in enumerate(order):
            spec = self._providers[provider]
            fallback_used = index > 0
            retries_left = self._max_retries
            while True:
                attempts += 1
                try:
                    response = self._client(provider).chat.completions.create(
                        model=spec.model,
                        messages=list(messages),
                        temperature=temperature,
                        max_tokens=max_tokens or spec.max_tokens,
                    )
                except Exception as exc:  # 分类后转为显式失败
                    status = _classify_exception(exc)
                    last_error = _safe_error(exc, spec.api_key)
                    result = ModelResult(
                        status=status,
                        provider=provider,
                        model=spec.model,
                        mode="live",
                        error=last_error,
                        attempts=attempts,
                        fallback_used=fallback_used,
                    )
                    if status in _RETRYABLE and retries_left > 0:
                        retries_left -= 1
                        logger.warning(
                            "model_call_retry provider=%s model=%s status=%s attempts=%d",
                            provider.value,
                            spec.model,
                            status.value,
                            attempts,
                        )
                        continue
                    if index == 0 and len(order) > 1:
                        logger.warning(
                            "model_provider_fallback from=%s to=%s status=%s",
                            provider.value,
                            order[1].value,
                            status.value,
                        )
                        break
                    return result

                finish_reason = None
                content = ""
                if response.choices:
                    finish_reason = response.choices[0].finish_reason
                    content = response.choices[0].message.content or ""
                usage = getattr(response, "usage", None)
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                result = self._interpret_text(
                    content,
                    finish_reason,
                    schema,
                    provider,
                    spec.model,
                    "live",
                    attempts,
                    fallback_used,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                return result

        return ModelResult(
            status=ModelCallStatus.PROVIDER_UNAVAILABLE,
            model="",
            mode="live",
            error=last_error,
            attempts=attempts,
            fallback_used=True,
        )

    def _interpret_text(
        self,
        content: str,
        finish_reason: Optional[str],
        schema: Optional[Type[BaseModel]],
        provider: Optional[ModelProvider],
        model: str,
        mode: str,
        attempts: int,
        fallback_used: bool,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> ModelResult:
        """把模型输出变成成功或显式失败。非 JSON / 不合 schema / 空内容均为失败。"""
        base = {
            "provider": provider,
            "model": model,
            "mode": mode,
            "finish_reason": finish_reason,
            "attempts": attempts,
            "fallback_used": fallback_used,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if not content or not content.strip():
            # ADR-0004：finish_reason=length 且 content 为空必须判失败，
            # 否则“模型没说话”会污染成“无答案/证据不足”。
            return ModelResult(
                status=ModelCallStatus.EMPTY_CONTENT,
                error=(
                    "empty_content: 模型返回空内容"
                    f"（finish_reason={finish_reason!r}）"
                ),
                **base,
            )

        if schema is None:
            return ModelResult(status=ModelCallStatus.SUCCEEDED, text=content, **base)

        try:
            payload = _loads_json(content)
        except (ValueError, TypeError) as exc:
            return ModelResult(
                status=ModelCallStatus.INVALID_STRUCTURE,
                error=f"invalid_structure: 模型输出不是合法 JSON: {exc}",
                **base,
            )

        try:
            data = schema.model_validate(payload)
        except ValidationError as exc:
            return ModelResult(
                status=ModelCallStatus.INVALID_STRUCTURE,
                error=f"invalid_structure: 输出不符合 schema: {_short(exc)}",
                **base,
            )
        return ModelResult(status=ModelCallStatus.SUCCEEDED, text=content, data=data, **base)

    # ------------------------------------------------------------------ 内部
    def _import_sdk(self) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise CapabilityUnavailableError(
                "capability unavailable: 缺少 openai SDK（uv sync 安装依赖后重试）"
            ) from exc

    def _client(self, provider: ModelProvider) -> Any:
        if provider in self._clients:
            return self._clients[provider]
        import openai

        spec = self._providers[provider]
        try:
            client = openai.OpenAI(
                api_key=spec.api_key,
                base_url=spec.base_url,
                timeout=spec.timeout_seconds,
                max_retries=0,  # 重试由本层统一控制（次数有上限）
                http_client=self._http_client,
            )
        except ImportError as exc:  # pragma: no cover - 由 _import_sdk 兜底
            raise CapabilityUnavailableError(
                "capability unavailable: 缺少 openai SDK"
            ) from exc
        self._clients[provider] = client
        return client

    def _record_result(self, result: ModelResult, duration_seconds: float) -> None:
        """记录聚合指标；标签只含供应商/模型/状态/模式，不含 prompt 或内容。"""
        provider = result.provider.value if result.provider else "none"
        model = result.model or "none"
        MODEL_CALLS.inc(
            {
                "provider": provider,
                "model": model,
                "status": result.status.value,
                "mode": result.mode,
            }
        )
        if result.prompt_tokens:
            MODEL_TOKENS.inc(
                {"provider": provider, "model": model, "kind": "prompt"},
                result.prompt_tokens,
            )
        if result.completion_tokens:
            MODEL_TOKENS.inc(
                {"provider": provider, "model": model, "kind": "completion"},
                result.completion_tokens,
            )
        MODEL_LATENCY.observe(
            duration_seconds, {"provider": provider, "mode": result.mode}
        )

    def _log_result(self, result: ModelResult) -> None:
        logger.info(
            "model_call provider=%s model=%s mode=%s status=%s finish_reason=%s "
            "attempts=%d fallback_used=%s prompt_tokens=%d completion_tokens=%d",
            result.provider.value if result.provider else "none",
            result.model,
            result.mode,
            result.status.value,
            result.finish_reason,
            result.attempts,
            result.fallback_used,
            result.prompt_tokens,
            result.completion_tokens,
        )


def _provider_from_name(name: str) -> ModelProvider:
    try:
        return ModelProvider(name.strip().lower())
    except ValueError as exc:
        raise CapabilityUnavailableError(
            f"capability unavailable: 未知模型供应商 {name!r}"
            f"（可选 {[p.value for p in ModelProvider]}）"
        ) from exc


def _loads_json(text: str) -> Any:
    """容忍 Markdown 代码围栏；其余按严格 JSON 解析。"""
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return json.loads(value)


def _classify_exception(exc: Exception) -> ModelCallStatus:
    import openai

    if isinstance(exc, openai.APITimeoutError):
        return ModelCallStatus.TIMEOUT
    if isinstance(exc, openai.RateLimitError):
        return ModelCallStatus.RATE_LIMITED
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code == 429:
            return ModelCallStatus.RATE_LIMITED
        if exc.status_code in (408, 504):
            return ModelCallStatus.TIMEOUT
        return ModelCallStatus.PROVIDER_UNAVAILABLE
    if isinstance(exc, openai.APIConnectionError):
        return ModelCallStatus.PROVIDER_UNAVAILABLE
    return ModelCallStatus.PROVIDER_UNAVAILABLE


def _safe_error(exc: Exception, api_key: str) -> str:
    """错误文本不泄漏凭据，并限制长度。"""
    message = f"{type(exc).__name__}: {exc}"
    if api_key:
        message = message.replace(api_key, "***")
    return _short(message)


def _short(value: Any, limit: int = 500) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"


__all__ = [
    "CapabilityUnavailableError",
    "ModelCallStatus",
    "ModelGateway",
    "ModelProvider",
    "ModelResult",
    "ProviderSpec",
]
