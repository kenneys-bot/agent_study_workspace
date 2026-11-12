"""检索对比：原问题 / 情境拼接 / 核心诉求改写 + 业务·有效性过滤（T27，#28）。

M6-04 要求同一知识版本、同一查询集上比较三种检索方案。本模块把「知识快照
+ 索引」固定为一个 :class:`RetrievalHarness`，三种变体只在 **查询构造** 与
**过滤策略** 上不同：

============  ==============================  ==========  ==============
变体           查询文本                        业务过滤    有效期过滤
============  ==============================  ==========  ==============
original      原问题                          否          否
contextual    旧「情境拼接」形态：业务范围 +   否          否
              原问题
core_intent   核心诉求改写结果                是          是
============  ==============================  ==========  ==============

故意保留「旧行为不过滤」与「新行为过滤」的差异：M3-02 的问题正是先查全部
再过滤。回放模式只验证流水线，不把分数当模型质量；live 模式才调用真实
DeepSeek 做改写，embedding 用真实 BGE。

金标只用于打分：``intent_cluster``、模板家族、相关文档 id 绝不进入检索查询，
否则就是把答案泄漏给被测方案。
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bank_assistant.assistants.suggestions import (
    QueryRewriter,
    RewriteOutcome,
    REWRITE_PROMPT_VERSION,
    REWRITE_SYSTEM_PROMPT,
)
from bank_assistant.datasets.corpus import CORPUS_AS_OF, Corpus
from bank_assistant.evaluation.errors import BudgetExceeded
from bank_assistant.evaluation.metrics import RetrievalRecord, Usage, estimate_tokens
from bank_assistant.knowledge.embedding import EmbeddingProvider

__all__ = [
    "QueryPlan",
    "QueryStrategy",
    "RetrievalHarness",
    "RetrievalItem",
    "ScoredHit",
    "SnapshotChunk",
    "build_query_plan",
    "parse_retrieval_items",
    "run_retrieval",
]

STRATEGIES: tuple[str, ...] = (
    "original",
    "contextual_concat",
    "core_intent_filter",
)


class QueryStrategy:
    """三个变体的稳定字符串标识（用常量而非枚举，便于写进 JSON）。"""

    ORIGINAL = "original"
    CONTEXTUAL = "contextual_concat"
    CORE_INTENT = "core_intent_filter"


@dataclass(frozen=True, slots=True)
class SnapshotChunk:
    """知识快照中的一个分块；``key`` 与数据集 ``chunk_id`` 同构。"""

    key: str
    doc_id: str
    text: str
    business_type: str
    source_type: str
    start: int
    end: int
    active: bool


@dataclass(frozen=True, slots=True)
class ScoredHit:
    chunk_key: str
    doc_id: str
    business_type: str
    active: bool
    start: int
    end: int
    similarity: float

    @property
    def distance(self) -> float:
        return 1.0 - self.similarity


@dataclass(frozen=True, slots=True)
class RetrievalItem:
    """冻结测试集里的一条检索样本；标签只用于打分。"""

    item_id: str
    scenario: str
    business_type: str
    query: str
    relevant_doc_ids: tuple[str, ...]
    relevant_chunk_keys: tuple[str, ...]
    answerable: bool
    unanswerable_reason: str | None
    excluded_doc_ids: tuple[str, ...] = ()
    hard_categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """一个变体对一条样本的查询方案。"""

    strategy: str
    retrieval_query: str
    rewrite: dict[str, Any]
    business_filter: bool
    validity_filter: bool


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:24] or "embedding"


class RetrievalHarness:
    """固定知识快照 + 内存索引；三种变体共用同一实例。"""

    def __init__(
        self,
        *,
        corpus: Corpus,
        provider: EmbeddingProvider,
        top_k: int = 5,
        min_similarity: float = 0.55,
        as_of: datetime = CORPUS_AS_OF,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k 必须为正整数")
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity 必须在 [0, 1] 内")
        if as_of.tzinfo is None:
            raise ValueError("as_of 必须带时区")
        self.corpus = corpus
        self.provider = provider
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.as_of = as_of
        self._chunks: tuple[SnapshotChunk, ...] = tuple(
            SnapshotChunk(
                key=f"{entry.doc_id}#c{index}",
                doc_id=entry.doc_id,
                text=chunk.span.text,
                business_type=entry.business_type,
                source_type=entry.source_type,
                start=chunk.span.start,
                end=chunk.span.end,
                active=entry.is_active(as_of),
            )
            for entry in sorted(corpus.knowledge, key=lambda item: item.doc_id)
            for index, chunk in enumerate(entry.chunks())
        )
        self._vectors: tuple[tuple[float, ...], ...] = tuple(
            tuple(vector) for vector in provider.embed([chunk.text for chunk in self._chunks])
        )
        if len(self._vectors) != len(self._chunks):
            raise ValueError("embedding 返回数量与知识分块数量不一致")
        self.knowledge_snapshot_id = corpus.knowledge_snapshot()
        self.rule_snapshot_id = corpus.rule_snapshot()
        self.embedding_version = provider.model_version
        digest = hashlib.sha256(
            f"{provider.model_version}|{self.knowledge_snapshot_id}|{as_of.isoformat()}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        self.index_version = f"eval-{_slug(provider.model_version)}-{digest}"

    @property
    def chunks(self) -> tuple[SnapshotChunk, ...]:
        return self._chunks

    def query(
        self,
        text: str,
        *,
        business_type: str | None = None,
        require_active: bool = False,
        top_k: int | None = None,
    ) -> tuple[ScoredHit, ...]:
        if not text.strip():
            return ()
        vector = tuple(float(value) for value in self.provider.embed([text])[0])
        query_norm = _norm(vector)
        scored: list[ScoredHit] = []
        for chunk, chunk_vector in zip(self._chunks, self._vectors, strict=True):
            if business_type is not None and chunk.business_type != business_type:
                continue
            if require_active and not chunk.active:
                continue
            chunk_norm = _norm(chunk_vector)
            similarity = (
                sum(a * b for a, b in zip(vector, chunk_vector, strict=True))
                / (query_norm * chunk_norm)
                if query_norm and chunk_norm
                else 0.0
            )
            scored.append(
                ScoredHit(
                    chunk_key=chunk.key,
                    doc_id=chunk.doc_id,
                    business_type=chunk.business_type,
                    active=chunk.active,
                    start=chunk.start,
                    end=chunk.end,
                    similarity=similarity,
                )
            )
        scored.sort(key=lambda hit: (-hit.similarity, hit.chunk_key))
        return tuple(scored[: (top_k or self.top_k)])

    def is_no_answer(self, hits: Sequence[ScoredHit]) -> bool:
        """无候选或最高相似度低于门槛时判为「无答案」。"""
        return not hits or hits[0].similarity < self.min_similarity


def _norm(vector: Sequence[float]) -> float:
    return sum(value * value for value in vector) ** 0.5


def build_query_plan(
    strategy: str, item: RetrievalItem, *, rewriter: QueryRewriter | None
) -> QueryPlan:
    """构造一个变体的查询；未知变体显式失败。"""
    if strategy == QueryStrategy.ORIGINAL:
        return QueryPlan(
            strategy=strategy,
            retrieval_query=item.query,
            rewrite={"applied": False, "prompt_version": None, "error": None},
            business_filter=False,
            validity_filter=False,
        )
    if strategy == QueryStrategy.CONTEXTUAL:
        # 旧实现把情境字段拼进检索文本；这里只用运行时可见的业务范围，
        # 不伪造数据集中不存在的 topic/stage/emotion，也不泄漏金标意图。
        return QueryPlan(
            strategy=strategy,
            retrieval_query=f"情境: 业务范围={item.business_type}\n客户问题: {item.query}",
            rewrite={"applied": False, "prompt_version": None, "error": None},
            business_filter=False,
            validity_filter=False,
        )
    if strategy == QueryStrategy.CORE_INTENT:
        outcome = (
            rewriter.rewrite(item.query)
            if rewriter is not None
            else RewriteOutcome(applied=False, error="no_rewriter")
        )
        retrieval_query = (
            outcome.query
            if outcome.applied and outcome.query
            else item.query
        )
        return QueryPlan(
            strategy=strategy,
            retrieval_query=retrieval_query,
            rewrite={
                "applied": outcome.applied,
                "prompt_version": outcome.prompt_version,
                "model": outcome.model,
                "error": outcome.error,
            },
            business_filter=True,
            validity_filter=True,
        )
    raise ValueError(f"未知检索变体：{strategy!r}；只支持 {STRATEGIES}")


def _rewrite_usage(rewriter: object | None) -> Usage:
    usage = getattr(rewriter, "last_usage", None)
    return usage if isinstance(usage, Usage) else Usage()


def _rewrite_latency_ms(rewriter: object | None) -> float:
    value = getattr(rewriter, "last_latency_ms", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _dedupe_docs(hits: Sequence[ScoredHit]) -> tuple[str, ...]:
    docs: list[str] = []
    for hit in hits:
        if hit.doc_id not in docs:
            docs.append(hit.doc_id)
    return tuple(docs)


def run_retrieval(
    items: Sequence[RetrievalItem],
    *,
    harness: RetrievalHarness,
    rewriter: QueryRewriter | None,
    mode: str = "replay",
) -> tuple[list[RetrievalRecord], list[dict[str, Any]]]:
    """对每条样本跑三个变体；返回指标记录与可落盘的逐条预测。"""
    records: list[RetrievalRecord] = []
    predictions: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda candidate: candidate.item_id):
        for strategy in STRATEGIES:
            try:
                plan = build_query_plan(strategy, item, rewriter=rewriter)
            except BudgetExceeded:
                # 预算上限触发：保留已完成样本，由 runner 把运行标为
                # budget_exceeded；不得伪造剩余样本。
                return records, predictions
            started = time.perf_counter()
            hits = harness.query(
                plan.retrieval_query,
                business_type=item.business_type if plan.business_filter else None,
                require_active=plan.validity_filter,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            usage = _rewrite_usage(rewriter) if strategy == QueryStrategy.CORE_INTENT else Usage()
            latency_ms += (
                _rewrite_latency_ms(rewriter)
                if strategy == QueryStrategy.CORE_INTENT
                else 0.0
            )
            input_tokens = estimate_tokens(plan.retrieval_query) + usage.input_tokens
            output_tokens = usage.output_tokens
            no_answer = harness.is_no_answer(hits)
            records.append(
                RetrievalRecord(
                    variant=strategy,
                    item_id=item.item_id,
                    scenario=item.scenario,
                    hard_categories=item.hard_categories,
                    answerable=item.answerable,
                    unanswerable_reason=item.unanswerable_reason,
                    ranked_keys=_dedupe_docs(hits),
                    relevant_keys=item.relevant_doc_ids,
                    excluded_doc_ids=item.excluded_doc_ids,
                    predicted_no_answer=no_answer,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    ranked_chunk_keys=tuple(hit.chunk_key for hit in hits),
                    relevant_chunk_keys=item.relevant_chunk_keys,
                )
            )
            predictions.append(
                {
                    "type": "retrieval",
                    "mode": mode,
                    "variant": strategy,
                    "item_id": item.item_id,
                    "scenario": item.scenario,
                    "business_type": item.business_type,
                    "query": item.query,
                    "retrieval_query": plan.retrieval_query,
                    "rewrite": plan.rewrite,
                    "business_filter": plan.business_filter,
                    "validity_filter": plan.validity_filter,
                    "answerable": item.answerable,
                    "unanswerable_reason": item.unanswerable_reason,
                    "hard_categories": list(item.hard_categories),
                    "relevant_doc_ids": list(item.relevant_doc_ids),
                    "relevant_chunk_keys": list(item.relevant_chunk_keys),
                    "excluded_doc_ids": list(item.excluded_doc_ids),
                    "ranked_doc_ids": list(_dedupe_docs(hits)),
                    "ranked_chunk_keys": [hit.chunk_key for hit in hits],
                    "ranked": [
                        {
                            "chunk_key": hit.chunk_key,
                            "doc_id": hit.doc_id,
                            "similarity": round(hit.similarity, 6),
                            "active": hit.active,
                        }
                        for hit in hits
                    ],
                    "predicted_no_answer": no_answer,
                    "latency_ms": round(latency_ms, 3),
                    "input_tokens_estimated": input_tokens,
                    "output_tokens_estimated": output_tokens,
                }
            )
    return records, predictions


def parse_retrieval_items(records: Sequence[dict[str, Any]]) -> list[RetrievalItem]:
    """把冻结 test 的 JSONL 行转成检索样本；只读标签用于打分。"""
    items: list[RetrievalItem] = []
    for record in records:
        if record.get("task") != "retrieval":
            continue
        labels = record["labels"]
        items.append(
            RetrievalItem(
                item_id=str(record["item_id"]),
                scenario=str(record["scenario"]),
                business_type=str(record["business_type"]),
                query=str(record["query"]),
                relevant_doc_ids=tuple(str(doc) for doc in labels.get("relevant_doc_ids", [])),
                relevant_chunk_keys=tuple(
                    str(chunk) for chunk in labels.get("relevant_chunk_ids", [])
                ),
                answerable=bool(labels["answerable"]),
                unanswerable_reason=labels.get("unanswerable_reason"),
                excluded_doc_ids=tuple(
                    str(doc) for doc in labels.get("excluded_doc_ids", [])
                ),
                hard_categories=tuple(str(item) for item in record.get("hard_categories", [])),
            )
        )
    return sorted(items, key=lambda item: item.item_id)


def prompt_version() -> str:
    """检索侧提示词版本（改写提示词）；manifest 会与质检提示词一起记录。"""
    return REWRITE_PROMPT_VERSION


def rewrite_prompt() -> str:
    return REWRITE_SYSTEM_PROMPT
