"""检索对比变体（T27，#28；M6-04）的离线单测。

三条变体必须共用同一知识快照/索引版本，只在查询构造与过滤策略上不同：

1. ``original``：原问题，不额外过滤；
2. ``contextual_concat``：按旧「情境拼接」形态把业务范围与问题拼在一起；
3. ``core_intent_filter``：核心诉求改写 + 业务范围/有效期过滤。

回放用确定性词法 embedding（不是生产 BGE），因此断言只检查流程、过滤与
可复现性，不把回放分数当模型质量。
"""

from __future__ import annotations

import hashlib

import pytest

from bank_assistant.assistants.suggestions import RewriteOutcome
from bank_assistant.datasets.corpus import load_corpus
from bank_assistant.evaluation.embedding import ReplayLexicalEmbedding
from bank_assistant.evaluation.retrieval import (
    QueryPlan,
    RetrievalHarness,
    RetrievalItem,
    build_query_plan,
    run_retrieval,
)

QUERY = "挂失可以通过哪些渠道办理"


class _Rewriter:
    def __init__(self, outcome: RewriteOutcome) -> None:
        self.outcome = outcome

    def rewrite(self, query: str) -> RewriteOutcome:
        return self.outcome


def _harness() -> RetrievalHarness:
    return RetrievalHarness(
        corpus=load_corpus(),
        provider=ReplayLexicalEmbedding(),
        top_k=5,
        min_similarity=0.1,
    )


def _item(**overrides) -> RetrievalItem:
    base = dict(
        item_id="rq-test-01",
        scenario="lost_card",
        business_type="lost_card",
        query=QUERY,
        relevant_doc_ids=("lost-card-demo-01",),
        relevant_chunk_keys=("lost-card-demo-01#c0",),
        answerable=True,
        unanswerable_reason=None,
        excluded_doc_ids=(),
        hard_categories=(),
    )
    base.update(overrides)
    return RetrievalItem(**base)


def test_replay_embedding_is_deterministic_and_normalized() -> None:
    provider = ReplayLexicalEmbedding()
    first = provider.embed(["挂失补卡需要多久？"])[0]
    second = provider.embed(["挂失补卡需要多久？"])[0]
    assert first == second
    assert len(first) == 512
    norm = sum(value * value for value in first) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_replay_embedding_prefers_topically_related_text() -> None:
    provider = ReplayLexicalEmbedding()
    query, related, unrelated = provider.embed(
        ["挂失补卡需要多久", "挂失后补卡时限与工本费", "投诉受理登记要素与分级"]
    )
    dot = lambda a, b: sum(x * y for x, y in zip(a, b, strict=True))  # noqa: E731
    assert dot(query, related) > dot(query, unrelated)


def test_harness_uses_one_snapshot_and_index_for_every_variant() -> None:
    harness = _harness()
    snapshot_id = harness.knowledge_snapshot_id
    index_version = harness.index_version
    assert harness.knowledge_snapshot_id == snapshot_id
    assert harness.index_version == index_version
    # 索引版本由 embedding 版本 + 知识快照 + 观察时点派生，不依赖调用顺序。
    again = _harness()
    assert again.knowledge_snapshot_id == snapshot_id
    assert again.index_version == index_version


def test_business_and_validity_filters_exclude_out_of_scope_knowledge() -> None:
    harness = _harness()
    # 未过滤时，过期边界文档可被召回；过滤后不能出现。
    unfiltered = harness.query("2024年挂失后5个工作日补卡的规定现在还有效吗", top_k=10)
    assert any(hit.doc_id == "edge-expired-01" for hit in unfiltered)
    filtered = harness.query(
        "2024年挂失后5个工作日补卡的规定现在还有效吗",
        business_type="lost_card",
        require_active=True,
        top_k=10,
    )
    assert all(hit.doc_id != "edge-expired-01" for hit in filtered)
    assert all(hit.business_type == "lost_card" for hit in filtered)


def test_query_plans_share_snapshot_but_differ_in_query_and_filters() -> None:
    rewriter = _Rewriter(
        RewriteOutcome(applied=True, query="挂失办理渠道", model="replay")
    )
    item = _item()

    original = build_query_plan("original", item, rewriter=rewriter)
    contextual = build_query_plan("contextual_concat", item, rewriter=rewriter)
    core = build_query_plan("core_intent_filter", item, rewriter=rewriter)

    assert original.retrieval_query == item.query
    assert original.business_filter is False and original.validity_filter is False
    assert item.query in contextual.retrieval_query
    assert "lost_card" in contextual.retrieval_query
    assert contextual.business_filter is False
    assert core.retrieval_query == "挂失办理渠道"
    assert core.business_filter is True and core.validity_filter is True
    assert core.rewrite["applied"] is True


def test_core_intent_falls_back_to_original_when_rewrite_fails() -> None:
    rewriter = _Rewriter(RewriteOutcome(applied=False, error="timeout"))
    plan = build_query_plan("core_intent_filter", _item(), rewriter=rewriter)
    assert isinstance(plan, QueryPlan)
    assert plan.retrieval_query == QUERY
    assert plan.rewrite["error"] == "timeout"
    assert plan.business_filter is True


def test_unknown_variant_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_query_plan("magic", _item(), rewriter=None)


def test_run_retrieval_is_deterministic_and_never_uses_gold_fields_in_query() -> None:
    harness = _harness()
    item = _item()
    first_records, first_predictions = run_retrieval(
        [item], harness=harness, rewriter=None
    )
    second_records, second_predictions = run_retrieval(
        [item], harness=harness, rewriter=None
    )
    assert [record.ranked_keys for record in first_records] == [
        record.ranked_keys for record in second_records
    ]
    def stable(predictions):
        return [
            {key: value for key, value in prediction.items() if key != "latency_ms"}
            for prediction in predictions
        ]

    assert stable(first_predictions) == stable(second_predictions)
    variants = {record.variant for record in first_records}
    assert variants == {"original", "contextual_concat", "core_intent_filter"}
    for prediction in first_predictions:
        # 查询里只能出现原问题/业务范围，不能出现金标意图或模板家族。
        assert item.relevant_doc_ids[0] not in prediction["retrieval_query"]
        assert "retrieval_paraphrase" not in prediction["retrieval_query"]
    assert all(record.ranked_keys for record in first_records)


def test_run_retrieval_counts_tokens_and_no_answer_decisions() -> None:
    harness = RetrievalHarness(
        corpus=load_corpus(),
        provider=ReplayLexicalEmbedding(),
        top_k=5,
        min_similarity=0.99,  # 故意抬高门槛：所有样本都应判为无答案
    )
    records, predictions = run_retrieval([_item()], harness=harness, rewriter=None)
    assert all(record.predicted_no_answer for record in records)
    assert all(record.input_tokens > 0 for record in records)
    assert predictions[0]["predicted_no_answer"] is True


def test_title_prepending_changes_index_identity_but_not_chunk_spans() -> None:
    corpus = load_corpus()
    baseline = RetrievalHarness(
        corpus=corpus,
        provider=ReplayLexicalEmbedding(),
        embedding_text="chunk",
    )
    titled = RetrievalHarness(
        corpus=corpus,
        provider=ReplayLexicalEmbedding(),
        embedding_text="title_chunk",
    )
    # 分块边界与原文不变（引用仍可逐字回验），只有 embedding 输入与索引身份变化。
    assert [chunk.key for chunk in baseline.chunks] == [
        chunk.key for chunk in titled.chunks
    ]
    assert [chunk.text for chunk in baseline.chunks] == [
        chunk.text for chunk in titled.chunks
    ]
    assert baseline.index_version != titled.index_version

    def rank(harness: RetrievalHarness, doc_id: str) -> int:
        hits = harness.query(
            "补卡需要多久", business_type="lost_card", require_active=True
        )
        return [hit.doc_id for hit in hits].index(doc_id) + 1

    # 标题「补卡时限与工本费」被拼入后，相关文档排名上升。
    assert rank(titled, "lost-card-demo-03") < rank(baseline, "lost-card-demo-03")


def test_soft_business_filter_keeps_cross_business_candidates() -> None:
    corpus = load_corpus()
    hard = RetrievalHarness(
        corpus=corpus,
        provider=ReplayLexicalEmbedding(),
        business_filter="hard",
    )
    soft = RetrievalHarness(
        corpus=corpus,
        provider=ReplayLexicalEmbedding(),
        business_filter="soft",
        business_bonus=0.02,
    )
    hard_hits = hard.query("年费为什么收", business_type="complaint", require_active=True)
    soft_hits = soft.query("年费为什么收", business_type="complaint", require_active=True)
    assert all(hit.business_type == "complaint" for hit in hard_hits)
    assert any(hit.business_type == "credit_card_fee" for hit in soft_hits)
    assert soft_hits[0].doc_id == "fee-demo-01"


def test_scenario_overrides_control_filter_and_top_k() -> None:
    harness = RetrievalHarness(
        corpus=load_corpus(),
        provider=ReplayLexicalEmbedding(),
        top_k=5,
        business_filter="hard",
        scenario_overrides={
            "complaint": {"business_filter": "soft", "top_k": 2, "min_similarity": 0.9}
        },
    )
    assert harness.business_filter_mode("complaint") == "soft"
    assert harness.business_filter_mode("lost_card") == "hard"
    assert len(harness.query("投诉回访怎么做", business_type="complaint", scenario="complaint")) == 2
    assert harness.top_k_for("lost_card") == 5
    assert harness.similarity_threshold("complaint") == 0.9
    assert harness.similarity_threshold("lost_card") == harness.min_similarity


def test_reranker_is_deterministic_and_configurable() -> None:
    corpus = load_corpus()
    harness = RetrievalHarness(
        corpus=corpus,
        provider=ReplayLexicalEmbedding(),
        rerank_alpha=0.2,
        rerank_pool=10,
    )
    first = harness.query("投诉回访怎么做", business_type="complaint", require_active=True)
    second = harness.query("投诉回访怎么做", business_type="complaint", require_active=True)
    assert [hit.chunk_key for hit in first] == [hit.chunk_key for hit in second]
    assert len(first) == 5
    assert harness.rerank_alpha == 0.2 and harness.rerank_pool == 10


def test_title_index_rebuild_reproduces_index_version_and_ranking() -> None:
    """ADR-0005 的重建验证：同一语料/provider/参数重建得到同一索引身份与排序。"""
    from bank_assistant.knowledge.embedding import calibration_vector_sha256

    corpus = load_corpus()
    provider = ReplayLexicalEmbedding()
    first = RetrievalHarness(
        corpus=corpus,
        provider=provider,
        embedding_text="title_chunk",
        rerank_alpha=0.2,
    )
    second = RetrievalHarness(
        corpus=corpus,
        provider=ReplayLexicalEmbedding(),
        embedding_text="title_chunk",
        rerank_alpha=0.2,
    )
    assert first.index_version == second.index_version
    assert calibration_vector_sha256(provider) == calibration_vector_sha256(
        ReplayLexicalEmbedding()
    )
    query = "投诉回访怎么做"
    assert [hit.chunk_key for hit in first.query(query, business_type="complaint")] == [
        hit.chunk_key for hit in second.query(query, business_type="complaint")
    ]
