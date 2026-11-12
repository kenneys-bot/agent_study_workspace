"""M6 评测指标：召回/MRR、逐规则 F1、分位数、token 与成本估算（T27，#28）。

本模块是纯函数层，不读文件、不调用模型，评测脚本与单元测试共用。所有
聚合结果都显式带 **分母**：没有相关样本、没有命中、没有预测都不能被 0 或
1 掩盖。指标口径对应计划 §7：

- ``Recall@K``：相关知识版本进入前 K 候选的覆盖率；
- ``MRR``：第一个相关结果的倒数排名；
- 质检以「会话 × 适用规则」为判定单元，逐规则报告 TP/FP/FN 与支持数；
- ``macro-F1`` 只在有支持或有预测的规则上求平均，并单独记录参与规则数；
- 延迟用 nearest-rank 分位数；token 用显式估算方法，成本按配置价目表估算。
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Counts",
    "InspectionRecord",
    "Price",
    "RetrievalRecord",
    "Usage",
    "counts_from_predictions",
    "estimate_cost_cny",
    "estimate_tokens",
    "f1",
    "macro_f1",
    "percentile",
    "precision",
    "recall",
    "recall_at_k",
    "reciprocal_rank",
    "summarize_inspection",
    "summarize_retrieval",
]

POSITIVE_VERDICT = "hit"
VERDICTS: tuple[str, ...] = (
    "hit",
    "clear",
    "insufficient_evidence",
    "not_applicable",
    "failed",
)


# ---------------------------------------------------------------- 召回与 MRR


def recall_at_k(
    ranked_keys: Sequence[str], relevant_keys: Collection[str], k: int = 5
) -> float:
    """前 ``k`` 个候选覆盖了多少相关分块；无相关样本时显式报错。"""
    relevant = set(relevant_keys)
    if not relevant:
        raise ValueError("Recall@K 需要至少一个相关样本；无答案样本不进入分母")
    if k <= 0:
        raise ValueError("k 必须为正整数")
    return len(set(ranked_keys[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked_keys: Sequence[str], relevant_keys: Collection[str]) -> float:
    """第一个相关结果的倒数排名；没有相关样本时显式报错。"""
    relevant = set(relevant_keys)
    if not relevant:
        raise ValueError("MRR 需要至少一个相关样本；无答案样本不进入分母")
    for rank, key in enumerate(ranked_keys, start=1):
        if key in relevant:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------- 分类指标


@dataclass(frozen=True, slots=True)
class Counts:
    """二分类混淆计数（正类固定为 ``hit``）；``support`` 是金标正例数。"""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __post_init__(self) -> None:
        if min(self.tp, self.fp, self.fn) < 0:
            raise ValueError("混淆计数不能为负")

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def predicted_positive(self) -> int:
        return self.tp + self.fp


def counts_from_predictions(
    gold_verdicts: Iterable[str],
    predicted_verdicts: Iterable[str],
    *,
    positive: str = POSITIVE_VERDICT,
) -> Counts:
    """把逐条判定折成 TP/FP/FN；``failed`` 等系统失败按“未命中”计。"""
    gold = list(gold_verdicts)
    predicted = list(predicted_verdicts)
    if len(gold) != len(predicted):
        raise ValueError("金标与预测数量不一致")
    tp = fp = fn = 0
    for gold_verdict, predicted_verdict in zip(gold, predicted, strict=True):
        gold_positive = gold_verdict == positive
        predicted_positive = predicted_verdict == positive
        if gold_positive and predicted_positive:
            tp += 1
        elif predicted_positive:
            fp += 1
        elif gold_positive:
            fn += 1
    return Counts(tp=tp, fp=fp, fn=fn)


def precision(counts: Counts) -> float:
    denominator = counts.tp + counts.fp
    return counts.tp / denominator if denominator else 0.0


def recall(counts: Counts) -> float:
    denominator = counts.tp + counts.fn
    return counts.tp / denominator if denominator else 0.0


def f1(counts: Counts) -> float:
    p = precision(counts)
    r = recall(counts)
    return 2 * p * r / (p + r) if p + r else 0.0


def macro_f1(counts_by_rule: Mapping[str, Counts]) -> float:
    """只在有金标正例或有预测正例的规则上求平均；没有规则的 macro 记为 0。"""
    values = [
        f1(counts)
        for counts in counts_by_rule.values()
        if counts.support or counts.predicted_positive
    ]
    return sum(values) / len(values) if values else 0.0


def percentile(values: Iterable[float], p: float) -> float | None:
    """nearest-rank 分位数；空样本返回 ``None``，不伪造成 0。"""
    if not 0 < p <= 100:
        raise ValueError("p 必须在 (0, 100] 内")
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


# ---------------------------------------------------------------- token 与成本


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token 数不能为负")

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True, slots=True)
class Price:
    """每百万 token 的人民币价格；来源与生效日期由配置记录。"""

    input_cny_per_mtok: float
    output_cny_per_mtok: float

    def __post_init__(self) -> None:
        if self.input_cny_per_mtok < 0 or self.output_cny_per_mtok < 0:
            raise ValueError("价格不能为负")


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return (
        0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0x4E00 <= code <= 0x9FFF  # 基本区
        or 0xF900 <= code <= 0xFAFF  # 兼容表意
        or 0x3040 <= code <= 0x30FF  # 日文假名（演示语料里可能出现）
        or 0xAC00 <= code <= 0xD7AF  # 韩文
    )


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 每字 1 token，其余非空白字符每 4 字符 1 token。

    这是 **估算**，不是供应商 tokenizer 的精确计数；报告必须标注
    ``estimated=true`` 与估算方法，避免把估算值当账单。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for character in text:
        if character.isspace():
            continue
        if _is_cjk(character):
            cjk += 1
        else:
            other += 1
    return cjk + math.ceil(other / 4)


def estimate_cost_cny(usage: Usage, price: Price) -> float:
    """按每百万 token 单价估算成本（人民币）。"""
    return (
        usage.input_tokens / 1_000_000 * price.input_cny_per_mtok
        + usage.output_tokens / 1_000_000 * price.output_cny_per_mtok
    )


# ---------------------------------------------------------------- 检索聚合


@dataclass(frozen=True, slots=True)
class RetrievalRecord:
    """单条检索评测结果。

    ``ranked_keys`` / ``relevant_keys`` 是 **文档/版本级** 的排序与金标，对应
    计划 §7「相关知识版本进入前 K 候选」；``ranked_chunk_keys`` /
    ``relevant_chunk_keys`` 是分块级覆盖，作为附加诊断（不替代主口径）。
    """

    variant: str
    item_id: str
    scenario: str
    hard_categories: tuple[str, ...]
    answerable: bool
    unanswerable_reason: str | None
    ranked_keys: tuple[str, ...]
    relevant_keys: tuple[str, ...]
    excluded_doc_ids: tuple[str, ...]
    predicted_no_answer: bool
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    ranked_chunk_keys: tuple[str, ...] = ()
    relevant_chunk_keys: tuple[str, ...] = ()


def _ratio(numerator: float, denominator: int) -> dict[str, Any]:
    return {
        "count": numerator,
        "denominator": denominator,
        "rate": (numerator / denominator) if denominator else None,
    }


def _recall_block(records: Sequence[RetrievalRecord]) -> dict[str, Any]:
    answerable = [record for record in records if record.answerable]
    recalls = [
        recall_at_k(record.ranked_keys, record.relevant_keys)
        for record in answerable
    ]
    reciprocal_ranks = [
        reciprocal_rank(record.ranked_keys, record.relevant_keys)
        for record in answerable
    ]
    return {
        "items": len(records),
        "answerable_items": len(answerable),
        "unanswerable_items": len(records) - len(answerable),
        "recall_at_5": (
            sum(recalls) / len(recalls) if recalls else None
        ),
        "mrr": (
            sum(reciprocal_ranks) / len(reciprocal_ranks)
            if reciprocal_ranks
            else None
        ),
        "errors": sum(1 for record in records if record.error),
        "recall_at_5_chunks": (
            sum(
                recall_at_k(record.ranked_chunk_keys, record.relevant_chunk_keys)
                for record in answerable
                if record.relevant_chunk_keys
            )
            / sum(1 for record in answerable if record.relevant_chunk_keys)
            if any(record.relevant_chunk_keys for record in answerable)
            else None
        ),
    }


def _summarize_variant(records: Sequence[RetrievalRecord]) -> dict[str, Any]:
    no_answer_subset = [
        record
        for record in records
        if record.unanswerable_reason in {"no_answer", "insufficient_info"}
    ]
    handled = sum(1 for record in no_answer_subset if record.predicted_no_answer)
    answerable = [record for record in records if record.answerable]
    false_rejects = sum(1 for record in answerable if record.predicted_no_answer)
    expired = [
        record
        for record in records
        if record.unanswerable_reason == "expired_knowledge"
    ]
    leaks = sum(
        1
        for record in expired
        if any(
            key.split("#", 1)[0] in set(record.excluded_doc_ids)
            for key in record.ranked_keys
        )
    )
    scenarios = sorted({record.scenario for record in records})
    latencies = [record.latency_ms for record in records]
    usage = Usage(
        input_tokens=sum(record.input_tokens for record in records),
        output_tokens=sum(record.output_tokens for record in records),
    )
    return {
        "overall": _recall_block(records),
        "by_scenario": {
            scenario: _recall_block(
                [record for record in records if record.scenario == scenario]
            )
            for scenario in scenarios
        },
        "no_answer_subset": {
            "items": len(no_answer_subset),
            "handled": handled,
            **_ratio(handled, len(no_answer_subset)),
            "handling_rate": (
                handled / len(no_answer_subset) if no_answer_subset else None
            ),
        },
        "false_reject": {
            "answerable_items": len(answerable),
            "rejected": false_rejects,
            **_ratio(false_rejects, len(answerable)),
            "rate": (false_rejects / len(answerable) if answerable else None),
        },
        "expired_knowledge": {
            "items": len(expired),
            "leaks": leaks,
            **_ratio(leaks, len(expired)),
            "leak_rate": (leaks / len(expired) if expired else None),
        },
        "latency_ms": {
            "count": len(latencies),
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "token_usage_estimated": {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
        },
    }


def summarize_retrieval(records: Sequence[RetrievalRecord]) -> dict[str, Any]:
    """按变体聚合检索结果；同一份报告里绝不混合不同变体/模式。"""
    records = list(records)
    variants = sorted({record.variant for record in records})
    if len(variants) == 1:
        return _summarize_variant(records)
    return {
        "by_variant": {
            variant: _summarize_variant(
                [record for record in records if record.variant == variant]
            )
            for variant in variants
        }
    }


# ---------------------------------------------------------------- 质检聚合


@dataclass(frozen=True, slots=True)
class InspectionRecord:
    """一条「会话 × 规则」评测结果；``failed`` 表示系统失败而非业务判定。"""

    variant: str
    item_id: str
    rule_code: str
    severity: str
    gold_verdict: str
    predicted_verdict: str
    gold_evidence: tuple[tuple[str, int, int], ...] = ()
    predicted_evidence: tuple[tuple[str, int, int], ...] = ()
    evidence_valid: bool = True
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def _evidence_matches(
    predicted: Sequence[tuple[str, int, int]],
    gold: Sequence[tuple[str, int, int]],
) -> bool:
    """证据定位：预测区间与金标区间在同一轮次上互相包含或完全一致。

    金标证据通常是完整风险短语（如“短信验证码报给我”），确定性关键词基线
    只能定位其中一部分（“验证码”）；只要预测区间落在金标短语内，就说明
    定位到了正确位置。空金标（clear/insufficient）不匹配任何预测命中。
    """
    for predicted_turn, predicted_start, predicted_end in predicted:
        for gold_turn, gold_start, gold_end in gold:
            if predicted_turn != gold_turn:
                continue
            contained = gold_start <= predicted_start and predicted_end <= gold_end
            covers = predicted_start <= gold_start and gold_end <= predicted_end
            if contained or covers:
                return True
    return False


def _verdict_distribution(verdicts: Sequence[str]) -> dict[str, Any]:
    counts = {verdict: 0 for verdict in VERDICTS}
    for verdict in verdicts:
        counts[verdict if verdict in counts else "failed"] += 1
    total = len(verdicts)
    return {
        "counts": counts,
        "shares": {
            verdict: (count / total if total else None)
            for verdict, count in counts.items()
        },
        "total": total,
    }


def summarize_inspection(
    records: Sequence[InspectionRecord],
    *,
    rules: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """逐规则与整体聚合质检结果；稀有规则的区间与分母都保留。"""
    records = list(records)
    rule_codes = sorted({record.rule_code for record in records})
    per_rule: dict[str, Any] = {}
    counts_by_rule: dict[str, Counts] = {}
    for rule_code in rule_codes:
        subset = [record for record in records if record.rule_code == rule_code]
        counts = counts_from_predictions(
            [record.gold_verdict for record in subset],
            [record.predicted_verdict for record in subset],
        )
        counts_by_rule[rule_code] = counts
        predicted_hits = [
            record for record in subset if record.predicted_verdict == "hit"
        ]
        localized = sum(
            1
            for record in predicted_hits
            if _evidence_matches(record.predicted_evidence, record.gold_evidence)
        )
        per_rule[rule_code] = {
            "items": len(subset),
            "support": counts.support,
            "predicted_positive": counts.predicted_positive,
            "tp": counts.tp,
            "fp": counts.fp,
            "fn": counts.fn,
            "precision": precision(counts),
            "recall": recall(counts),
            "f1": f1(counts),
            "severity": rules.get(rule_code, {}).get("severity", "unknown"),
            "failed": sum(1 for record in subset if record.error),
            "evidence_localization": {
                "predicted_hits": len(predicted_hits),
                "exact_matches": localized,
                "accuracy": (localized / len(predicted_hits) if predicted_hits else None),
            },
            "gold_verdicts": _verdict_distribution(
                [record.gold_verdict for record in subset]
            ),
            "predicted_verdicts": _verdict_distribution(
                [record.predicted_verdict for record in subset]
            ),
        }

    high_risk = [
        code
        for code, rule in rules.items()
        if rule.get("severity") == "high" and code in counts_by_rule
    ]
    high_tp = sum(counts_by_rule[code].tp for code in high_risk)
    high_fn = sum(counts_by_rule[code].fn for code in high_risk)
    predicted_hits = [
        record for record in records if record.predicted_verdict == "hit"
    ]
    localized = sum(
        1
        for record in predicted_hits
        if _evidence_matches(record.predicted_evidence, record.gold_evidence)
    )
    latencies = [record.latency_ms for record in records]
    return {
        "items": len({record.item_id for record in records}),
        "judgments": len(records),
        "rules": len(rule_codes),
        "per_rule": per_rule,
        "macro_f1": macro_f1(counts_by_rule),
        "macro_f1_rule_count": sum(
            1
            for counts in counts_by_rule.values()
            if counts.support or counts.predicted_positive
        ),
        "high_risk_recall": {
            "rules": high_risk,
            "tp": high_tp,
            "fn": high_fn,
            "denominator": high_tp + high_fn,
            "recall": (high_tp / (high_tp + high_fn) if high_tp + high_fn else None),
        },
        "evidence_localization": {
            "predicted_hits": len(predicted_hits),
            "exact_matches": localized,
            "accuracy": (localized / len(predicted_hits) if predicted_hits else None),
            "invalid_evidence": sum(
                1 for record in predicted_hits if not record.evidence_valid
            ),
        },
        "gold_verdicts": _verdict_distribution(
            [record.gold_verdict for record in records]
        ),
        "predicted_verdicts": _verdict_distribution(
            [record.predicted_verdict for record in records]
        ),
        "system_failures": sum(1 for record in records if record.error),
        "latency_ms": {
            "count": len(latencies),
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "token_usage_estimated": {
            "input": sum(record.input_tokens for record in records),
            "output": sum(record.output_tokens for record in records),
        },
    }
