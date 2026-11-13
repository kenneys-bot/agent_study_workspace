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
from collections.abc import Mapping, Sequence
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

# T36：可配置的检索增强。全部是 harness 构造参数，默认值就是 T27 旧行为，
# 因此任何一项都可以在 EVAL_CONFIG 里单独关掉回退。
EMBEDDING_TEXT_CHUNK = "chunk"
EMBEDDING_TEXT_TITLE_CHUNK = "title_chunk"
EMBEDDING_TEXT_TITLE_SOURCE_CHUNK = "title_source_chunk"
EMBEDDING_TEXT_MODES: frozenset[str] = frozenset(
    {
        EMBEDDING_TEXT_CHUNK,
        EMBEDDING_TEXT_TITLE_CHUNK,
        EMBEDDING_TEXT_TITLE_SOURCE_CHUNK,
    }
)
BUSINESS_FILTER_HARD = "hard"
BUSINESS_FILTER_SOFT = "soft"
BUSINESS_FILTER_OFF = "off"
BUSINESS_FILTER_MODES: frozenset[str] = frozenset(
    {BUSINESS_FILTER_HARD, BUSINESS_FILTER_SOFT, BUSINESS_FILTER_OFF}
)
_CJK_OR_LATIN = re.compile(r"[0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def _char_grams(text: str) -> frozenset[str]:
    """字符二元组集合；用于确定性的本地词面重排，不引入外部模型/网络。"""
    cleaned = "".join(_CJK_OR_LATIN.findall(text.lower()))
    if len(cleaned) <= 1:
        return frozenset({cleaned}) if cleaned else frozenset()
    return frozenset(cleaned[index : index + 2] for index in range(len(cleaned) - 1))


class QueryStrategy:
    """三个变体的稳定字符串标识（用常量而非枚举，便于写进 JSON）。"""

    ORIGINAL = "original"
    CONTEXTUAL = "contextual_concat"
    CORE_INTENT = "core_intent_filter"


@dataclass(frozen=True, slots=True)
class SnapshotChunk:
    """知识快照中的一个分块；``key`` 与数据集 ``chunk_id`` 同构。

    ``text`` 始终是原文区间（引用必须可逐字回验）；``title``/``source_type``
    只用于构造 embedding 输入，不改变分块边界与 ``chunk_id``。
    """

    key: str
    doc_id: str
    text: str
    business_type: str
    title: str
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
        embedding_text: str = EMBEDDING_TEXT_CHUNK,
        business_filter: str = BUSINESS_FILTER_HARD,
        business_bonus: float = 0.02,
        rerank_alpha: float = 0.0,
        rerank_pool: int = 20,
        scenario_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k 必须为正整数")
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity 必须在 [0, 1] 内")
        if as_of.tzinfo is None:
            raise ValueError("as_of 必须带时区")
        if embedding_text not in EMBEDDING_TEXT_MODES:
            raise ValueError(
                f"embedding_text 只能是 {sorted(EMBEDDING_TEXT_MODES)}"
            )
        if business_filter not in BUSINESS_FILTER_MODES:
            raise ValueError(
                f"business_filter 只能是 {sorted(BUSINESS_FILTER_MODES)}"
            )
        if not 0.0 <= business_bonus <= 1.0:
            raise ValueError("business_bonus 必须在 [0, 1] 内")
        if not 0.0 <= rerank_alpha <= 1.0:
            raise ValueError("rerank_alpha 必须在 [0, 1] 内")
        if rerank_pool <= 0:
            raise ValueError("rerank_pool 必须为正整数")
        self.corpus = corpus
        self.provider = provider
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.as_of = as_of
        self.embedding_text = embedding_text
        self.business_filter = business_filter
        self.business_bonus = business_bonus
        self.rerank_alpha = rerank_alpha
        self.rerank_pool = rerank_pool
        self.scenario_overrides: dict[str, dict[str, Any]] = {
            scenario: dict(values)
            for scenario, values in (scenario_overrides or {}).items()
        }
        self._chunks: tuple[SnapshotChunk, ...] = tuple(
            SnapshotChunk(
                key=f"{entry.doc_id}#c{index}",
                doc_id=entry.doc_id,
                text=chunk.span.text,
                business_type=entry.business_type,
                title=entry.title,
                source_type=entry.source_type,
                start=chunk.span.start,
                end=chunk.span.end,
                active=entry.is_active(as_of),
            )
            for entry in sorted(corpus.knowledge, key=lambda item: item.doc_id)
            for index, chunk in enumerate(entry.chunks())
        )
        self._vectors: tuple[tuple[float, ...], ...] = tuple(
            tuple(vector)
            for vector in provider.embed(
                [self._embedding_input(chunk) for chunk in self._chunks]
            )
        )
        if len(self._vectors) != len(self._chunks):
            raise ValueError("embedding 返回数量与知识分块数量不一致")
        self._chunk_by_key = {chunk.key: chunk for chunk in self._chunks}
        self.knowledge_snapshot_id = corpus.knowledge_snapshot()
        self.rule_snapshot_id = corpus.rule_snapshot()
        self.embedding_version = provider.model_version
        # 索引身份必须包含 embedding 输入形态：标题/来源拼入会改变向量，
        # 于是 index_version 变化，符合 ADR-0005「改索引必须新建版本」。
        digest = hashlib.sha256(
            (
                f"{provider.model_version}|{self.knowledge_snapshot_id}|"
                f"{as_of.isoformat()}|{embedding_text}"
            ).encode("utf-8")
        ).hexdigest()[:12]
        self.index_version = f"eval-{_slug(provider.model_version)}-{digest}"

    @property
    def chunks(self) -> tuple[SnapshotChunk, ...]:
        return self._chunks

    def _embedding_input(self, chunk: SnapshotChunk) -> str:
        if self.embedding_text == EMBEDDING_TEXT_CHUNK:
            return chunk.text
        if self.embedding_text == EMBEDDING_TEXT_TITLE_CHUNK:
            return f"{chunk.title}\n{chunk.text}"
        return f"{chunk.title}\n来源类型：{chunk.source_type}\n{chunk.text}"

    def _override(self, scenario: str | None) -> Mapping[str, Any]:
        if scenario is None:
            return {}
        return self.scenario_overrides.get(scenario, {})

    def business_filter_mode(self, scenario: str | None = None) -> str:
        """该场景实际生效的业务过滤模式（场景覆盖优先）。"""
        return str(
            self._override(scenario).get("business_filter") or self.business_filter
        )

    def similarity_threshold(self, scenario: str | None = None) -> float:
        """该场景实际生效的无答案相似度阈值（场景覆盖优先）。"""
        value = self._override(scenario).get("min_similarity")
        return self.min_similarity if value is None else float(value)

    def top_k_for(self, scenario: str | None = None) -> int:
        value = self._override(scenario).get("top_k")
        return self.top_k if value is None else int(value)

    def query(
        self,
        text: str,
        *,
        business_type: str | None = None,
        require_active: bool = False,
        top_k: int | None = None,
        scenario: str | None = None,
    ) -> tuple[ScoredHit, ...]:
        if not text.strip():
            return ()
        mode = self.business_filter_mode(scenario)
        limit = top_k or self.top_k_for(scenario)
        vector = tuple(float(value) for value in self.provider.embed([text])[0])
        query_norm = _norm(vector)
        scored: list[tuple[float, ScoredHit]] = []
        for chunk, chunk_vector in zip(self._chunks, self._vectors, strict=True):
            if require_active and not chunk.active:
                continue
            in_business = (
                business_type is not None and chunk.business_type == business_type
            )
            if business_type is not None and mode == BUSINESS_FILTER_HARD and not in_business:
                continue
            chunk_norm = _norm(chunk_vector)
            similarity = (
                sum(a * b for a, b in zip(vector, chunk_vector, strict=True))
                / (query_norm * chunk_norm)
                if query_norm and chunk_norm
                else 0.0
            )
            effective = (
                similarity + self.business_bonus
                if mode == BUSINESS_FILTER_SOFT and in_business
                else similarity
            )
            scored.append(
                (
                    effective,
                    ScoredHit(
                        chunk_key=chunk.key,
                        doc_id=chunk.doc_id,
                        business_type=chunk.business_type,
                        active=chunk.active,
                        start=chunk.start,
                        end=chunk.end,
                        similarity=similarity,
                    ),
                )
            )
        scored.sort(key=lambda pair: (-pair[0], pair[1].chunk_key))
        limit = max(1, limit)
        if self.rerank_alpha > 0.0 and len(scored) > limit:
            # 确定性本地重排：只在候选池内用「查询词面覆盖率」修正排序，
            # 不调用模型、不联网；alpha=0 时完全跳过，可单独回退。
            pool = scored[: max(self.rerank_pool, limit)]
            query_grams = _char_grams(text)
            reranked: list[tuple[float, ScoredHit]] = []
            for effective, hit in pool:
                chunk = self._chunk_by_key[hit.chunk_key]
                chunk_grams = _char_grams(f"{chunk.title}\n{chunk.text}")
                coverage = (
                    len(query_grams & chunk_grams) / len(query_grams)
                    if query_grams
                    else 0.0
                )
                reranked.append((effective + self.rerank_alpha * coverage, hit))
            reranked.sort(key=lambda pair: (-pair[0], pair[1].chunk_key))
            return tuple(hit for _, hit in reranked[:limit])
        return tuple(hit for _, hit in scored[:limit])

    def is_no_answer(
        self, hits: Sequence[ScoredHit], *, scenario: str | None = None
    ) -> bool:
        """无候选或最高相似度低于门槛时判为「无答案」。

        软过滤下排序带业务奖励，但「有没有答案」仍应看原始余弦相似度，
        否则跨业务候选会被奖励分掩盖；因此取候选中的最大原始相似度。
        """
        if not hits:
            return True
        return max(hit.similarity for hit in hits) < self.similarity_threshold(scenario)


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
                scenario=item.scenario,
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
            no_answer = harness.is_no_answer(hits, scenario=item.scenario)
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
                    "business_filter_mode": (
                        harness.business_filter_mode(item.scenario)
                        if plan.business_filter
                        else BUSINESS_FILTER_OFF
                    ),
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
