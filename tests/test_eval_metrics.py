"""M6 评测指标数学（T27，#28）的离线单测。

只打公开 seam：召回/MRR、二分类 F1 计数、macro-F1、分位数、token 估算与
成本估算、以及把逐条预测聚合成报告。全部用小夹具，期望值用独立手算的
常数，不复用被测实现的算法。

零分母是这里的重点：没有相关样本、没有命中、没有预测都必须显式体现在
分母字段里，不能靠 0 或 1 把缺口藏起来。
"""

from __future__ import annotations

import pytest

from bank_assistant.evaluation.metrics import (
    Counts,
    Price,
    RetrievalRecord,
    Usage,
    counts_from_predictions,
    estimate_cost_cny,
    estimate_tokens,
    f1,
    macro_f1,
    percentile,
    precision,
    recall,
    recall_at_k,
    reciprocal_rank,
    summarize_retrieval,
)


# ---------------------------------------------------------------- 召回与 MRR


def test_recall_at_5_counts_relevant_coverage_in_top_k() -> None:
    assert recall_at_k(["x", "a", "y", "b", "z"], {"a", "b"}) == 1.0
    # 前 5 只出现 a，覆盖率 1/2。
    assert recall_at_k(["x", "y", "z", "w", "a", "b"], {"a", "b"}) == 0.5
    assert recall_at_k(["a"], {"a", "b"}, k=1) == 0.5


def test_recall_at_5_rejects_empty_relevance_instead_of_returning_zero() -> None:
    with pytest.raises(ValueError):
        recall_at_k(["a"], set())
    with pytest.raises(ValueError):
        recall_at_k(["a"], {"a"}, k=0)


def test_mrr_uses_first_relevant_rank() -> None:
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a", "x"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0
    with pytest.raises(ValueError):
        reciprocal_rank(["x"], set())


# ---------------------------------------------------------------- 分类指标


def test_counts_and_f1_include_every_denominator() -> None:
    counts = counts_from_predictions(
        ["hit", "hit", "clear", "insufficient_evidence"],
        ["hit", "clear", "hit", "failed"],
    )
    assert counts == Counts(tp=1, fp=1, fn=1)
    assert counts.support == 2
    assert precision(counts) == 0.5
    assert recall(counts) == 0.5
    assert f1(counts) == 0.5


def test_zero_denominators_are_zero_not_one() -> None:
    empty = Counts(tp=0, fp=0, fn=0)
    assert precision(empty) == 0.0
    assert recall(empty) == 0.0
    assert f1(empty) == 0.0
    # 只有漏检：P 分母为 0，R 为 0，F1 也必须是 0。
    missed = Counts(tp=0, fp=0, fn=2)
    assert precision(missed) == 0.0
    assert recall(missed) == 0.0
    assert f1(missed) == 0.0


def test_macro_f1_skips_rules_without_support_or_predictions() -> None:
    macro = macro_f1(
        {
            "a": Counts(tp=1, fp=0, fn=1),  # P=1 R=.5 F1=2/3
            "b": Counts(tp=1, fp=0, fn=0),  # F1=1
            "c": Counts(tp=0, fp=0, fn=0),  # 无支持也无预测 → 不计入 macro
        }
    )
    assert macro == pytest.approx((2 / 3 + 1.0) / 2)


def test_percentile_is_nearest_rank_and_empty_is_none() -> None:
    assert percentile([], 50) is None
    assert percentile([4.0], 95) == 4.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0
    with pytest.raises(ValueError):
        percentile([1.0], 0)


# ---------------------------------------------------------------- token 与成本


def test_estimate_tokens_counts_cjk_per_character_and_ascii_per_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("挂失") == 2
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("挂失abcde") == 4


def test_estimate_cost_uses_million_token_prices() -> None:
    price = Price(input_cny_per_mtok=2.0, output_cny_per_mtok=8.0)
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    assert estimate_cost_cny(usage, price) == pytest.approx(6.0)
    zero = Price(input_cny_per_mtok=0.0, output_cny_per_mtok=0.0)
    assert estimate_cost_cny(usage, zero) == 0.0
    with pytest.raises(ValueError):
        estimate_cost_cny(usage, Price(-1.0, 0.0))


# ---------------------------------------------------------------- 聚合


def _retrieval_record(**kwargs) -> RetrievalRecord:
    base = dict(
        variant="original",
        item_id="rq-1",
        scenario="lost_card",
        hard_categories=(),
        answerable=True,
        unanswerable_reason=None,
        ranked_keys=("doc#c0",),
        relevant_keys=("doc#c0",),
        excluded_doc_ids=(),
        predicted_no_answer=False,
        latency_ms=10.0,
        input_tokens=0,
        output_tokens=0,
    )
    base.update(kwargs)
    return RetrievalRecord(**base)


def test_summarize_retrieval_reports_denominators_and_no_answer_subset() -> None:
    records = [
        _retrieval_record(item_id="rq-hit"),
        _retrieval_record(
            item_id="rq-miss",
            relevant_keys=("other#c0",),
            ranked_keys=("doc#c0",),
        ),
        _retrieval_record(
            item_id="rq-unanswer",
            answerable=False,
            unanswerable_reason="no_answer",
            hard_categories=("no_answer",),
            ranked_keys=(),
            relevant_keys=(),
            predicted_no_answer=True,
        ),
        _retrieval_record(
            item_id="rq-false-reject",
            predicted_no_answer=True,
            latency_ms=30.0,
        ),
    ]
    summary = summarize_retrieval(records)
    overall = summary["overall"]
    assert overall["answerable_items"] == 3
    assert overall["recall_at_5"] == pytest.approx(2 / 3)
    assert overall["mrr"] == pytest.approx((1 + 0 + 1) / 3)
    assert summary["by_scenario"]["lost_card"]["answerable_items"] == 3
    no_answer = summary["no_answer_subset"]
    assert no_answer["items"] == 1
    assert no_answer["handled"] == 1
    assert no_answer["handling_rate"] == 1.0
    assert summary["false_reject"]["answerable_items"] == 3
    assert summary["false_reject"]["rejected"] == 1
    assert summary["false_reject"]["rate"] == pytest.approx(1 / 3)
    assert overall["items"] == 4
    assert summary["latency_ms"]["p50"] == 10.0
    assert summary["latency_ms"]["p95"] == 30.0


def test_summarize_retrieval_flags_expired_document_leaks() -> None:
    records = [
        _retrieval_record(
            item_id="rq-expired",
            answerable=False,
            unanswerable_reason="expired_knowledge",
            hard_categories=("expired_knowledge",),
            relevant_keys=(),
            ranked_keys=("edge-expired-01#c0", "lost-card-demo-03#c0"),
            excluded_doc_ids=("edge-expired-01",),
            predicted_no_answer=False,
        ),
        _retrieval_record(
            item_id="rq-expired-clean",
            answerable=False,
            unanswerable_reason="expired_knowledge",
            hard_categories=("expired_knowledge",),
            relevant_keys=(),
            ranked_keys=("lost-card-demo-03#c0",),
            excluded_doc_ids=("edge-expired-01",),
            predicted_no_answer=False,
        ),
    ]
    summary = summarize_retrieval(records)
    assert summary["expired_knowledge"]["items"] == 2
    assert summary["expired_knowledge"]["leaks"] == 1
    assert summary["expired_knowledge"]["leak_rate"] == 0.5


def test_summarize_retrieval_groups_variants_without_mixing_them() -> None:
    records = [
        _retrieval_record(variant="original"),
        _retrieval_record(variant="contextual_concat", relevant_keys=("other#c0",)),
    ]
    summary = summarize_retrieval(records)
    assert set(summary["by_variant"]) == {"original", "contextual_concat"}
    assert summary["by_variant"]["original"]["overall"]["recall_at_5"] == 1.0
    assert summary["by_variant"]["contextual_concat"]["overall"]["recall_at_5"] == 0.0


# ---------------------------------------------------------------- 质检聚合


def _inspection_record(**kwargs):
    from bank_assistant.evaluation.metrics import InspectionRecord

    base = dict(
        variant="rule_baseline",
        item_id="ins-1",
        rule_code="DEMO-PWD-01",
        severity="high",
        gold_verdict="hit",
        predicted_verdict="hit",
        gold_evidence=(("turn-1", 0, 4),),
        predicted_evidence=(("turn-1", 0, 4),),
        evidence_valid=True,
        latency_ms=1.0,
    )
    base.update(kwargs)
    return InspectionRecord(**base)


def test_summarize_inspection_reports_per_rule_denominators_and_shares() -> None:
    from bank_assistant.evaluation.metrics import summarize_inspection

    rules = {
        "DEMO-PWD-01": {"severity": "high"},
        "DEMO-CLAIM-01": {"severity": "medium"},
    }
    records = [
        _inspection_record(item_id="a", gold_verdict="hit", predicted_verdict="hit"),
        _inspection_record(
            item_id="b",
            gold_verdict="hit",
            predicted_verdict="clear",
            predicted_evidence=(),
        ),
        _inspection_record(
            item_id="c",
            gold_verdict="clear",
            predicted_verdict="hit",
            gold_evidence=(),
            predicted_evidence=(("turn-2", 3, 6),),
        ),
        _inspection_record(
            item_id="d",
            rule_code="DEMO-CLAIM-01",
            gold_verdict="insufficient_evidence",
            predicted_verdict="insufficient_evidence",
            gold_evidence=(),
            predicted_evidence=(),
        ),
        _inspection_record(
            item_id="e",
            rule_code="DEMO-CLAIM-01",
            gold_verdict="not_applicable",
            predicted_verdict="failed",
            gold_evidence=(),
            predicted_evidence=(),
            error="MODEL_TIMEOUT",
        ),
    ]
    summary = summarize_inspection(records, rules=rules)
    pwd = summary["per_rule"]["DEMO-PWD-01"]
    assert pwd["support"] == 2
    assert (pwd["tp"], pwd["fp"], pwd["fn"]) == (1, 1, 1)
    assert pwd["precision"] == 0.5
    assert pwd["recall"] == 0.5
    assert pwd["evidence_localization"]["predicted_hits"] == 2
    assert pwd["evidence_localization"]["exact_matches"] == 1
    claim = summary["per_rule"]["DEMO-CLAIM-01"]
    assert claim["support"] == 0
    assert claim["predicted_positive"] == 0
    assert claim["failed"] == 1
    assert summary["macro_f1_rule_count"] == 1  # CLAIM 无支持也无预测，不参与 macro
    assert summary["high_risk_recall"]["denominator"] == 2
    assert summary["high_risk_recall"]["recall"] == 0.5
    assert summary["predicted_verdicts"]["counts"]["insufficient_evidence"] == 1
    assert summary["predicted_verdicts"]["counts"]["not_applicable"] == 0
    assert summary["gold_verdicts"]["counts"]["not_applicable"] == 1
    assert summary["system_failures"] == 1
    assert summary["latency_ms"]["p50"] == 1.0
