"""回放模式的确定性替身：改写器、质检网关与预算/计量包装（T27，#28）。

``mode: replay`` 只验证流水线：不联网、不读凭据、不产生费用，并且同一输入
必须得到同一输出（延迟除外）。这里的替身 **不是** 模型质量基线：

- :class:`ReplayQueryRewriter` 只做确定性的外壳剥离（去掉“请问/帮我查一下”等），
  代表生产 ``GatewayQueryRewriter`` 的调用形态；
- :class:`ReplayGateway` 用关键词朴素扫描模拟一个“不做否定/转述检查”的模型，
  让回放也能暴露规则与模型基线的差异；
- :class:`MeteredGateway` 包住真实或回放网关，统一估算 token、累计成本并在
  预算耗尽时抛 :class:`BudgetExceeded`（真实调用必须有上限）。

回放结果与 live 结果分别写进不同 manifest，绝不混在一份报告里。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from bank_assistant.assistants.suggestions import REWRITE_SYSTEM_PROMPT, RewriteOutcome
from bank_assistant.core.model_gateway import ModelCallStatus, ModelResult
from bank_assistant.datasets.selfcheck import RULE_KEYWORDS
from bank_assistant.inspections.deterministic import assess_context
from bank_assistant.evaluation.errors import BudgetExceeded
from bank_assistant.evaluation.metrics import Price, Usage, estimate_cost_cny, estimate_tokens
from bank_assistant.inspections.schema import RuleJudgmentPayload

__all__ = [
    "BudgetExceeded",
    "CostTracker",
    "MeteredGateway",
    "MeteredRewriter",
    "ReplayGateway",
    "ReplayQueryRewriter",
]

_LEADING_FRAMES: tuple[str, ...] = (
    "请问",
    "麻烦问一下",
    "帮我查一下",
    "客户问",
    "如果客户问到",
    "想问一下",
    "咨询一下",
)
_TRAILING_CLAUSES: tuple[str, ...] = (
    "应该怎么答复",
    "具体有什么规定",
    "按哪条制度说明",
    "需要满足什么条件",
)
_TRAILING_PHRASES: tuple[str, ...] = ("的要求", "的规定", "的要求是")


class ReplayQueryRewriter:
    """确定性的“核心诉求”剥离；无法产生变化时显式回退，不假装改写成功。"""

    model = "replay-rewrite"

    def rewrite(self, query: str) -> RewriteOutcome:
        original = query.strip()
        core = original
        for frame in _LEADING_FRAMES:
            if core.startswith(frame):
                core = core[len(frame) :].strip()
                break
        for separator in ("，", ","):
            if separator in core:
                head, tail = core.split(separator, 1)
                if any(tail.strip().startswith(clause) for clause in _TRAILING_CLAUSES):
                    core = head.strip()
                break
        for phrase in _TRAILING_PHRASES:
            if core.endswith(phrase):
                core = core[: -len(phrase)].strip()
        core = core.rstrip("？?。！!，, ").strip()
        if not core or core == original:
            return RewriteOutcome(
                applied=False,
                prompt_version="query-rewrite-v1:replay",
                model=self.model,
                error="rewrite_unchanged",
            )
        return RewriteOutcome(
            applied=True,
            query=core,
            prompt_version="query-rewrite-v1:replay",
            model=self.model,
        )


class ReplayGateway:
    """确定性朴素质检替身；只读提示词里的规则与会话，不接触金标标签。"""

    mode = "replay"

    def __init__(self, *, model: str = "replay-naive-keyword-model") -> None:
        self.model = model
        self.calls = 0

    def structured(
        self,
        messages,
        schema,
        **_kwargs: Any,
    ) -> ModelResult:
        self.calls += 1
        if schema is not RuleJudgmentPayload:
            return ModelResult(
                status=ModelCallStatus.INVALID_STRUCTURE,
                model=self.model,
                mode="replay",
                error="replay_gateway_only_supports_inspection_payload",
            )
        try:
            payload = _inspection_payload(messages)
            judgment = _naive_judgment(payload)
        except (KeyError, ValueError, TypeError) as exc:
            return ModelResult(
                status=ModelCallStatus.INVALID_STRUCTURE,
                model=self.model,
                mode="replay",
                error=f"replay_payload_invalid: {exc}",
            )
        data = RuleJudgmentPayload.model_validate(judgment)
        return ModelResult(
            status=ModelCallStatus.SUCCEEDED,
            model=self.model,
            mode="replay",
            text=json.dumps(judgment, ensure_ascii=False),
            data=data,
        )


def _inspection_payload(messages) -> Mapping[str, Any]:
    for message in reversed(list(messages)):
        content = str(message.get("content", ""))
        if message.get("role") == "user" and "\n" in content:
            return json.loads(content.split("\n", 1)[1])
    raise ValueError("提示词中没有质检 payload")


def _naive_judgment(payload: Mapping[str, Any]) -> dict[str, Any]:
    """确定性“朴素模型”：关键词扫描 + 否定/转述保护，但不检查规则适用范围。

    它必须产出能通过 ``parse_judgment`` 的证据（否则会把回放变成一串
    系统失败）；与规则基线的差异留在“不判 not_applicable、不判
    insufficient_evidence”这两个模型常见缺口上。
    """
    rule = payload["rule"]
    rule_code = str(rule["rule_code"])
    keywords = RULE_KEYWORDS.get(rule_code, ())
    for turn in payload.get("conversation", []):
        if turn.get("speaker") != "坐席":
            continue
        content = str(turn.get("content", ""))
        for keyword in keywords:
            start = content.find(keyword)
            if start < 0:
                continue
            end = start + len(keyword)
            if not assess_context(content, start, end).supported:
                continue
            return {
                "verdict": "hit",
                "rationale": f"朴素关键词命中：{keyword}",
                "evidence": [
                    {
                        "turn_id": str(turn["turn_id"]),
                        "start": start,
                        "end": end,
                        "quote": keyword,
                    }
                ],
                "knowledge_refs": [],
                "applicability": None,
            }
    return {
        "verdict": "clear",
        "rationale": "朴素关键词未命中",
        "evidence": [],
        "knowledge_refs": [],
        "applicability": None,
    }


class CostTracker:
    """共享的 token/成本账本；预算按“调用前预估 + 调用后实计”双重拦截。"""

    def __init__(self, *, price: Price | None, budget_cny: float | None) -> None:
        if budget_cny is not None and budget_cny < 0:
            raise ValueError("budget_cny 不能为负")
        if budget_cny and price is None:
            raise ValueError("启用预算上限时必须提供价目")
        self.price = price
        self.budget_cny = budget_cny
        self.usage = Usage()
        self.spent_cny = 0.0

    def before(self, projected: Usage) -> None:
        if self.budget_cny is None or self.price is None:
            return
        if self.spent_cny + estimate_cost_cny(projected, self.price) > self.budget_cny:
            raise BudgetExceeded(
                f"预算上限 {self.budget_cny} 元将超出；已花费约 "
                f"{self.spent_cny:.6f} 元，停止后续调用"
            )

    def add(self, usage: Usage) -> None:
        self.usage = self.usage + usage
        self.spent_cny = (
            estimate_cost_cny(self.usage, self.price) if self.price is not None else 0.0
        )
        if self.budget_cny is not None and self.spent_cny > self.budget_cny:
            raise BudgetExceeded(
                f"本轮调用后累计约 {self.spent_cny:.6f} 元，超过预算 "
                f"{self.budget_cny} 元；停止后续调用"
            )


class MeteredRewriter:
    """给改写器加延迟/token 计量；与质检网关共享同一个成本账本。"""

    def __init__(
        self,
        inner: Any,
        *,
        tracker: CostTracker | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.inner = inner
        self.tracker = tracker
        self._clock = clock
        self.last_usage = Usage()
        self.last_latency_ms = 0.0

    def rewrite(self, query: str) -> RewriteOutcome:
        prompt = REWRITE_SYSTEM_PROMPT + "\n" + query
        projected = Usage(input_tokens=estimate_tokens(prompt))
        if self.tracker is not None:
            self.tracker.before(projected)
        started = self._clock()
        outcome = self.inner.rewrite(query)
        self.last_latency_ms = (self._clock() - started) * 1000
        self.last_usage = Usage(
            input_tokens=projected.input_tokens,
            output_tokens=estimate_tokens(outcome.query or ""),
        )
        if self.tracker is not None:
            self.tracker.add(self.last_usage)
        return outcome


@dataclass(frozen=True, slots=True)
class GatewayCall:
    provider: str
    model: str
    status: str
    elapsed_ms: float
    input_tokens: int
    output_tokens: int
    error: str | None = None


class MeteredGateway:
    """给任意网关加 token 估算、延迟与预算上限；不记录 prompt/response 正文。"""

    def __init__(
        self,
        inner: Any,
        *,
        price: Price | None = None,
        budget_cny: float | None = None,
        model_provider: str = "unknown",
        model_version: str = "unknown",
        clock: Callable[[], float] = time.perf_counter,
        tracker: CostTracker | None = None,
    ) -> None:
        self.inner = inner
        self.tracker = tracker or CostTracker(price=price, budget_cny=budget_cny)
        self.price = self.tracker.price
        self.budget_cny = self.tracker.budget_cny
        self.model_provider = model_provider
        self.model_version = model_version
        self._clock = clock
        self.calls = 0
        self.call_log: list[GatewayCall] = []
        self.last_usage = Usage()

    @property
    def usage(self) -> Usage:
        return self.tracker.usage

    @property
    def spent_cny(self) -> float:
        return self.tracker.spent_cny

    @property
    def mode(self) -> str:
        return str(getattr(self.inner, "mode", "unknown"))

    def structured(self, messages, schema, **kwargs: Any) -> ModelResult:
        prompt = "\n".join(str(message.get("content", "")) for message in messages)
        input_tokens = estimate_tokens(prompt)
        self.tracker.before(Usage(input_tokens=input_tokens))
        started = self._clock()
        result = self.inner.structured(messages, schema=schema, **kwargs)
        elapsed_ms = (self._clock() - started) * 1000
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=estimate_tokens(result.text or ""),
        )
        self.last_usage = usage
        self.tracker.add(usage)
        self.calls += 1
        self.call_log.append(
            GatewayCall(
                provider=str(
                    getattr(result.provider, "value", self.model_provider)
                    or self.model_provider
                ),
                model=result.model or self.model_version,
                status=result.status.value,
                elapsed_ms=round(elapsed_ms, 3),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                error=result.error,
            )
        )
        return result

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens_estimated": self.usage.input_tokens,
            "output_tokens_estimated": self.usage.output_tokens,
            "estimated_cost_cny": round(self.spent_cny, 8),
            "estimated": True,
            "price": (
                {
                    "provider": self.model_provider,
                    "model": self.model_version,
                    "input_cny_per_mtok": self.price.input_cny_per_mtok,
                    "output_cny_per_mtok": self.price.output_cny_per_mtok,
                }
                if self.price is not None
                else None
            ),
        }
