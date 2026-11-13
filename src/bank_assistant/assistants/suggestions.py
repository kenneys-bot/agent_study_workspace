"""候选答复端到端：过滤 → 检索 → 生成 → 引用校验 → 状态机（M3，契约 §3/§4）。

一条请求的固定顺序：

1. **业务/范围/有效期过滤**：只使用当前身份 ``scope_id`` 下、业务匹配的
   可检索知识；授权范围来自服务端身份，请求体不能放宽。
2. **活动索引校验**：加载 ``active.json`` 对应的索引清单，先用当前 embedding
   provider 重算固定校准串的向量哈希；不一致即 ``EmbeddingDrift``，索引不可信，
   本次运行记为系统失败（``failed``），绝不静默使用旧索引。
3. **核心诉求改写（可选、显式）**：``QueryRewriter`` 不注入时不做改写；
   注入时同时保留原问题（``query``）与改写结果（``rewritten_query``），
   并在 ``retrieval_snapshot.rewrite`` 里记录是否应用、提示词版本与错误。
   改写失败会显式回退到原问题，不假装成功。
4. **检索 + 重排 + 数据库复核**：向量召回（候选池）后按 T36 采用的确定性
   本地重排（查询字符二元组覆盖率，α=0.2）排序，再逐个回查
   ``knowledge_chunks`` / ``knowledge_versions`` / ``knowledge_documents``，
   草稿、退役、过期、跨范围、业务不符的分块全部丢弃（CT-01）。重排的应用
   状态与参数写入 ``retrieval_snapshot.rerank``，可审计也可关掉回退。
   同一文档存在多个当前有效版本时显式判为冲突并转 ``needs_clarification``。
5. **模型生成**：通过 ``ModelGateway``，系统提示词 + 本次召回片段；模型只能
   从给定 ``chunk_id`` / ``version_id`` 引用。模型失败只产生 ``failed``。
6. **引用校验**：每条引用必须来自本次召回、版本仍 published 且在有效期、
   属于当前范围、quote 与片段原文完全匹配；失败引用被丢弃，且不会出现在
   响应里。若 ``answered`` 没有任何有效引用，降级 ``needs_human`` 并清空
   答案，绝不交付无依据答复。
7. **CT-02**：生成期间知识退役/过期时，先重新复核召回；仍有有效证据则用
   剩余证据重生成一次，否则返回 ``needs_human``，不交付失效引用。

``model_runs`` 记录模型、提示词、知识快照、索引版本与代码 SHA；没有发生模型
调用的业务短路也写一行 ``model='none'`` 的运行记录，run_id 永不为空。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.assistants.candidate_answer import (
    CandidateAnswer,
    CandidateAnswerService,
    CandidateAnswerStatus,
    Citation,
)
from bank_assistant.config.settings import settings
from bank_assistant.core.model_gateway import (
    ModelCallStatus,
    ModelGateway,
    ModelResult,
)
from bank_assistant.security.input_limits import truncate_text
from bank_assistant.infra.models import (
    Conversation,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
    ModelRun,
    Suggestion,
    Turn,
)
from bank_assistant.knowledge.embedding import EmbeddingProvider
from bank_assistant.knowledge.indexing import (
    DEFAULT_INDEX_DIR,
    ChunkVectorStore,
    KnowledgeIndexError,
    VectorHit,
    load_active_index,
    search_index,
)
from bank_assistant.knowledge.ranking import RerankCandidate, rerank_candidates
from bank_assistant.knowledge.retrieval import fetch_retrievable_versions
from bank_assistant.security.identity import Identity

logger = logging.getLogger(__name__)

SUGGESTION_SYSTEM_PROMPT = """你是银行内部坐席辅助系统。只依据 <knowledge> 中的知识片段起草候选答复。

必须遵守：
1. 只能引用 <knowledge> 中出现的 chunk_id 与 version_id；quote 必须逐字来自该片段原文。
2. 不得自行补足知识中没有的费率、时限、办理结果或政策结论。
3. 知识不足、条件不足或问题不明确时，status 只能返回 needs_clarification 或 needs_human，并在 missing_information 里说明缺什么；不得编造 answer。
4. 只输出一个 JSON 对象，不要 Markdown：
{"status":"answered|needs_clarification|needs_human","answer":"...","citations":[{"version_id":"...","chunk_id":"...","quote":"..."}],"missing_information":["..."]}
"""

REWRITE_SYSTEM_PROMPT = """你是银行客服检索助手。把客户原问题改写成用于知识检索的核心诉求，保留业务对象与关键条件，去掉寒暄与重复。只输出 JSON：{"core_query":"..."}。不要回答问题。
"""

SUGGESTION_PROMPT_VERSION = (
    "suggestion-v1:" + hashlib.sha256(SUGGESTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16]
)
REWRITE_PROMPT_VERSION = (
    "query-rewrite-v1:" + hashlib.sha256(REWRITE_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16]
)

DEFAULT_TOP_K = 5
DEFAULT_MAX_DISTANCE = 0.55
MAX_QUOTE_LENGTH = 500


class SuggestionError(Exception):
    """候选答复领域错误的基类。"""


class SuggestionInputError(SuggestionError):
    """输入不满足前置条件（既无会话也无问题、问题为空等）。"""

    code = "SUGGESTION_INVALID_INPUT"
    http_status = 422


class SuggestionNotFound(SuggestionError):
    """会话不存在或跨范围；路由统一映射为不泄露存在性的 404。"""

    code = "NOT_FOUND"
    http_status = 404


class CoreIntentPayload(BaseModel):
    """核心诉求改写的结构化输出。"""

    core_query: str

    model_config = ConfigDict(extra="ignore")


@dataclass(frozen=True, slots=True)
class RewriteOutcome:
    """一次改写的显式结果；``applied=False`` 时必须带原因。"""

    applied: bool
    query: str | None = None
    prompt_version: str = REWRITE_PROMPT_VERSION
    model: str | None = None
    error: str | None = None


class QueryRewriter(Protocol):
    """核心诉求改写 seam；生产用模型，测试可注入确定性替身。"""

    def rewrite(self, query: str) -> RewriteOutcome:
        ...


class GatewayQueryRewriter:
    """通过统一模型适配层做核心诉求改写；失败不抛异常，显式回退。"""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def rewrite(self, query: str) -> RewriteOutcome:
        messages = [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        result = self.gateway.structured(messages, schema=CoreIntentPayload)
        if not result.ok:
            return RewriteOutcome(
                applied=False,
                prompt_version=REWRITE_PROMPT_VERSION,
                model=result.model,
                error=result.error or result.status.value,
            )
        core = (result.data.core_query or "").strip()
        if not core or core == query.strip():
            return RewriteOutcome(
                applied=False,
                prompt_version=REWRITE_PROMPT_VERSION,
                model=result.model,
                error="rewrite_unchanged",
            )
        return RewriteOutcome(
            applied=True,
            query=core,
            prompt_version=REWRITE_PROMPT_VERSION,
            model=result.model,
        )


@dataclass(frozen=True, slots=True)
class RecalledChunk:
    """通过数据库复核的本次召回片段；引用只能来自这里。"""

    chunk_id: UUID
    version_id: UUID
    document_id: UUID
    version_no: int
    title: str
    source_type: str
    business_type: str
    text: str
    start_offset: int
    end_offset: int
    distance: float

    @property
    def score(self) -> float:
        """余弦相似度 = 1 - 距离；只用于展示召回排序，不是校准概率。"""
        return 1.0 - self.distance

    def snapshot(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "version_id": str(self.version_id),
            "document_id": str(self.document_id),
            "version_no": self.version_no,
            "title": self.title,
            "source_type": self.source_type,
            "business_type": self.business_type,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "distance": round(self.distance, 6),
            "score": round(self.score, 6),
        }


@dataclass(frozen=True)
class SuggestionOutcome:
    """一次候选答复运行的结果；``status`` 与持久化行一致。"""

    suggestion: Suggestion
    run: ModelRun
    status: CandidateAnswerStatus
    error_code: str | None = None
    model_status: ModelCallStatus | None = None
    provider: str | None = None
    model: str | None = None
    mode: str = "live"


def current_code_sha() -> str:
    """当前代码 SHA：优先 ``CODE_SHA``（部署注入），否则只读 git HEAD。"""
    value = os.environ.get("CODE_SHA", "").strip()
    if value:
        return value[:64]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip()[:64] or "unknown"


class SuggestionService:
    """候选答复端到端服务；HTTP 与测试共用同一个入口。"""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        embedding_provider: EmbeddingProvider,
        vector_store: ChunkVectorStore,
        index_dir: Path | str = DEFAULT_INDEX_DIR,
        rewriter: QueryRewriter | None = None,
        clock: Callable[[], datetime] | None = None,
        top_k: int = DEFAULT_TOP_K,
        max_distance: float = DEFAULT_MAX_DISTANCE,
        rerank_alpha: float | None = None,
        rerank_pool: int | None = None,
        code_sha: str | None = None,
    ) -> None:
        self.gateway = gateway
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.index_dir = Path(index_dir)
        self.rewriter = rewriter
        self._clock = clock or (lambda: datetime.now(UTC))
        self.top_k = top_k
        self.max_distance = max_distance
        # 默认值来自配置（与 T36 评测采用值一致：α=0.2、候选池 20）；
        # 显式传 0 即可关掉重排回退到纯向量排序。
        self.rerank_alpha = (
            settings.RETRIEVAL_RERANK_ALPHA if rerank_alpha is None else rerank_alpha
        )
        self.rerank_pool = (
            settings.RETRIEVAL_RERANK_POOL if rerank_pool is None else rerank_pool
        )
        if not 0.0 <= self.rerank_alpha <= 1.0:
            raise ValueError("rerank_alpha 必须在 [0, 1] 内")
        if self.rerank_pool <= 0:
            raise ValueError("rerank_pool 必须为正整数")
        self.code_sha = code_sha or current_code_sha()

    # ------------------------------------------------------------------ 入口
    def create_suggestion(
        self,
        session: Session,
        *,
        identity: Identity,
        conversation_id: UUID | None = None,
        query: str | None = None,
        business_type: str | None = None,
    ) -> SuggestionOutcome:
        """生成并持久化一条候选答复；系统失败也保留运行记录与 failed 状态。"""
        now = self._clock()
        conversation, source_query = self._resolve_query(
            session,
            identity=identity,
            conversation_id=conversation_id,
            query=query,
        )
        if not source_query.strip():
            return self._persist(
                session,
                identity=identity,
                conversation=conversation,
                query=source_query,
                rewritten_query=None,
                business_type=business_type,
                status=CandidateAnswerStatus.NEEDS_CLARIFICATION,
                answer="",
                citations=(),
                missing_information=("请提供客户的具体问题",),
                snapshot={"error": "empty_query"},
                run_fields=_run_fields(self.gateway),
                error_code="empty_query",
                now=now,
            )

        # 1-2. 活动索引 + 校准哈希；任何索引不可信都按系统失败处理。
        try:
            _, manifest = load_active_index(
                self.index_dir,
                provider=self.embedding_provider,
                store=self.vector_store,
            )
        except KnowledgeIndexError as exc:
            logger.warning("suggestion_index_unavailable: %s", exc)
            return self._persist(
                session,
                identity=identity,
                conversation=conversation,
                query=source_query,
                rewritten_query=None,
                business_type=business_type,
                status=CandidateAnswerStatus.FAILED,
                answer="",
                citations=(),
                missing_information=(),
                snapshot={"error": "index_unavailable", "detail": str(exc)[:500]},
                run_fields=_run_fields(self.gateway, index_version=None),
                error_code="index_unavailable",
                now=now,
            )

        index_version = manifest["index_version"]
        calibration_hash = manifest.get("calibration_vector_sha256")
        knowledge_snapshot = manifest.get("knowledge_snapshot", [])
        base_snapshot: dict[str, Any] = {
            "index_version": index_version,
            "embedding_model_version": manifest.get("embedding_model_version"),
            "calibration_vector_sha256": calibration_hash,
            "business_type": business_type,
            "top_k": self.top_k,
            "max_distance": self.max_distance,
            "query": source_query,
            "query_strategy": "baseline",
            "index_embedding_text": manifest.get("embedding_text") or "chunk",
            "index_content_sha256": manifest.get("index_content_sha256"),
            "chunks": [],
            "dropped_chunks": [],
            "rerank_status": "not_run",
        }

        if manifest.get("scope_id") != identity.scope_id:
            # 活动索引属于其他范围：本请求没有任何授权知识可用，不能跨范围检索。
            base_snapshot["error"] = "scope_mismatch"
            return self._persist(
                session,
                identity=identity,
                conversation=conversation,
                query=source_query,
                rewritten_query=None,
                business_type=business_type,
                status=CandidateAnswerStatus.NEEDS_HUMAN,
                answer="",
                citations=(),
                missing_information=("当前访问范围没有可用知识",),
                snapshot=base_snapshot,
                run_fields=_run_fields(
                    self.gateway,
                    index_version=index_version,
                    knowledge_snapshot=knowledge_snapshot,
                ),
                error_code="scope_mismatch",
                now=now,
            )

        # 3. 核心诉求改写：显式记录，失败回退原问题。
        rewrite = RewriteOutcome(applied=False)
        retrieval_query = source_query
        if self.rewriter is not None:
            rewrite = self.rewriter.rewrite(source_query)
            if rewrite.applied and rewrite.query:
                retrieval_query = rewrite.query
        base_snapshot["rewrite"] = {
            "applied": rewrite.applied,
            "prompt_version": rewrite.prompt_version,
            "model": rewrite.model,
            "error": rewrite.error,
        }
        if rewrite.applied:
            base_snapshot["query_strategy"] = "rewrite"
            base_snapshot["retrieval_query"] = retrieval_query
        else:
            base_snapshot["retrieval_query"] = source_query

        # 4. 召回 + 重排 + 数据库复核（草稿/退役/过期/跨范围/业务不符全部丢弃）。
        recalled, dropped, rerank_info, hit_count, useful_count = self._recall(
            session,
            query=retrieval_query,
            identity=identity,
            business_type=business_type,
            index_version=index_version,
            now=now,
        )
        base_snapshot["chunks"] = [chunk.snapshot() for chunk in recalled]
        base_snapshot["dropped_chunks"] = dropped
        base_snapshot["candidate_hit_count"] = hit_count
        base_snapshot["rerank_status"] = rerank_info["status"]
        base_snapshot["rerank"] = rerank_info

        # 模型调用不持有数据库事务：先结束只读事务，再调用外部模型。
        session.commit()

        if not recalled:
            reason = "no_evidence" if not useful_count else "recall_invalidated"
            base_snapshot["error"] = reason
            return self._persist(
                session,
                identity=identity,
                conversation=conversation,
                query=source_query,
                rewritten_query=rewrite.query if rewrite.applied else None,
                business_type=business_type,
                status=CandidateAnswerStatus.NEEDS_HUMAN,
                answer="",
                citations=(),
                missing_information=(
                    "未检索到与问题相关且当前有效的知识",
                ),
                snapshot=base_snapshot,
                run_fields=_run_fields(
                    self.gateway,
                    index_version=index_version,
                    knowledge_snapshot=knowledge_snapshot,
                ),
                error_code=reason,
                now=now,
            )

        # 同一文档存在多个当前有效版本 → 冲突必须显式转澄清，不能让模型挑一个。
        conflicts = self._detect_conflicts(
            session, recalled=recalled, identity=identity, now=now
        )
        if conflicts:
            base_snapshot["conflict"] = conflicts
            base_snapshot["error"] = "conflicting_knowledge"
            return self._persist(
                session,
                identity=identity,
                conversation=conversation,
                query=source_query,
                rewritten_query=rewrite.query if rewrite.applied else None,
                business_type=business_type,
                status=CandidateAnswerStatus.NEEDS_CLARIFICATION,
                answer="",
                citations=(),
                missing_information=("同一知识存在多个有效版本，需业务确认适用版本",),
                snapshot=base_snapshot,
                run_fields=_run_fields(
                    self.gateway,
                    index_version=index_version,
                    knowledge_snapshot=knowledge_snapshot,
                ),
                error_code="conflicting_knowledge",
                now=now,
            )

        # 业务分类不确定时追问，而不是强制选择一个业务过滤。
        if business_type is None:
            business_types = sorted({chunk.business_type for chunk in recalled})
            if len(business_types) > 1:
                base_snapshot["business_types"] = business_types
                base_snapshot["error"] = "ambiguous_business_type"
                return self._persist(
                    session,
                    identity=identity,
                    conversation=conversation,
                    query=source_query,
                    rewritten_query=rewrite.query if rewrite.applied else None,
                    business_type=business_type,
                    status=CandidateAnswerStatus.NEEDS_CLARIFICATION,
                    answer="",
                    citations=(),
                    missing_information=(
                        "请确认业务类型：" + " / ".join(business_types),
                    ),
                    snapshot=base_snapshot,
                    run_fields=_run_fields(
                        self.gateway,
                        index_version=index_version,
                        knowledge_snapshot=knowledge_snapshot,
                    ),
                    error_code="ambiguous_business_type",
                    now=now,
                )

        # 5. 模型生成。
        messages = build_suggestion_messages(
            query=source_query,
            chunks=recalled,
            business_type=business_type,
        )
        candidate = CandidateAnswerService(self.gateway).generate(messages)
        if candidate.status is CandidateAnswerStatus.FAILED:
            base_snapshot["model_status"] = candidate.model_status.value
            base_snapshot["error"] = candidate.error
            return self._persist(
                session,
                identity=identity,
                conversation=conversation,
                query=source_query,
                rewritten_query=rewrite.query if rewrite.applied else None,
                business_type=business_type,
                status=CandidateAnswerStatus.FAILED,
                answer="",
                citations=(),
                missing_information=(),
                snapshot=base_snapshot,
                run_fields=_run_fields(
                    self.gateway,
                    index_version=index_version,
                    knowledge_snapshot=knowledge_snapshot,
                    candidate=candidate,
                ),
                error_code="model_failure",
                model_status=candidate.model_status,
                provider=candidate.provider,
                model=candidate.model,
                now=now,
            )

        # 6. CT-02：生成期间知识退役/过期 → 用剩余有效证据重生成一次，否则转人工。
        current, retired = self._recheck_recall(
            session,
            recalled=recalled,
            identity=identity,
            business_type=business_type,
            index_version=index_version,
            now=now,
        )
        ct02: dict[str, Any] = {"retired": retired, "regenerated": False}
        if retired:
            if candidate.status is CandidateAnswerStatus.ANSWERED:
                # 重新检索同一活动索引，再按数据库最新状态复核；剩余证据才可重生成。
                current, retry_dropped, retry_rerank, _, _ = self._recall(
                    session,
                    query=retrieval_query,
                    identity=identity,
                    business_type=business_type,
                    index_version=index_version,
                    now=now,
                )
                ct02["re_retrieved"] = True
                ct02["retry_dropped_chunks"] = retry_dropped
                ct02["retry_rerank_status"] = retry_rerank["status"]
                base_snapshot["chunks"] = [chunk.snapshot() for chunk in current]
                if not current:
                    base_snapshot["ct02"] = ct02
                    base_snapshot["error"] = "knowledge_retired_during_generation"
                    return self._persist(
                        session,
                        identity=identity,
                        conversation=conversation,
                        query=source_query,
                        rewritten_query=rewrite.query if rewrite.applied else None,
                        business_type=business_type,
                        status=CandidateAnswerStatus.NEEDS_HUMAN,
                        answer="",
                        citations=(),
                        missing_information=("可用知识在生成期间失效，请转人工核实",),
                        snapshot=base_snapshot,
                        run_fields=_run_fields(
                            self.gateway,
                            index_version=index_version,
                            knowledge_snapshot=knowledge_snapshot,
                            candidate=candidate,
                        ),
                        error_code="knowledge_retired_during_generation",
                        now=now,
                    )
                ct02["regenerated"] = True
                base_snapshot["ct02"] = ct02
                session.commit()
                messages = build_suggestion_messages(
                    query=source_query, chunks=current, business_type=business_type
                )
                candidate = CandidateAnswerService(self.gateway).generate(messages)
                if candidate.status is CandidateAnswerStatus.FAILED:
                    base_snapshot["model_status"] = candidate.model_status.value
                    base_snapshot["error"] = candidate.error
                    return self._persist(
                        session,
                        identity=identity,
                        conversation=conversation,
                        query=source_query,
                        rewritten_query=rewrite.query if rewrite.applied else None,
                        business_type=business_type,
                        status=CandidateAnswerStatus.FAILED,
                        answer="",
                        citations=(),
                        missing_information=(),
                        snapshot=base_snapshot,
                        run_fields=_run_fields(
                            self.gateway,
                            index_version=index_version,
                            knowledge_snapshot=knowledge_snapshot,
                            candidate=candidate,
                        ),
                        error_code="model_failure",
                        model_status=candidate.model_status,
                        provider=candidate.provider,
                        model=candidate.model,
                        now=now,
                    )
                recalled = current
                current, retired_again = self._recheck_recall(
                    session,
                    recalled=recalled,
                    identity=identity,
                    business_type=business_type,
                    index_version=index_version,
                    now=now,
                )
                ct02["retired_after_regeneration"] = retired_again
                if retired_again:
                    base_snapshot["ct02"] = ct02
                    base_snapshot["error"] = "knowledge_retired_during_generation"
                    return self._persist(
                        session,
                        identity=identity,
                        conversation=conversation,
                        query=source_query,
                        rewritten_query=rewrite.query if rewrite.applied else None,
                        business_type=business_type,
                        status=CandidateAnswerStatus.NEEDS_HUMAN,
                        answer="",
                        citations=(),
                        missing_information=("可用知识在生成期间失效，请转人工核实",),
                        snapshot=base_snapshot,
                        run_fields=_run_fields(
                            self.gateway,
                            index_version=index_version,
                            knowledge_snapshot=knowledge_snapshot,
                            candidate=candidate,
                        ),
                        error_code="knowledge_retired_during_generation",
                        now=now,
                    )
            else:
                base_snapshot["ct02"] = ct02
                base_snapshot["error"] = "knowledge_retired_during_generation"
                return self._persist(
                    session,
                    identity=identity,
                    conversation=conversation,
                    query=source_query,
                    rewritten_query=rewrite.query if rewrite.applied else None,
                    business_type=business_type,
                    status=CandidateAnswerStatus.NEEDS_HUMAN,
                    answer="",
                    citations=(),
                    missing_information=("可用知识在生成期间失效，请转人工核实",),
                    snapshot=base_snapshot,
                    run_fields=_run_fields(
                        self.gateway,
                        index_version=index_version,
                        knowledge_snapshot=knowledge_snapshot,
                        candidate=candidate,
                    ),
                    error_code="knowledge_retired_during_generation",
                    now=now,
                )
        else:
            ct02["retired"] = []
            base_snapshot["ct02"] = ct02

        # 7. 引用校验：只保留本次召回、当前有效、quote 完全匹配的引用。
        valid_citations, rejected = self._validate_citations(
            session,
            candidate.citations,
            recalled=recalled,
            identity=identity,
            business_type=business_type,
            index_version=index_version,
            now=now,
        )
        base_snapshot["rejected_citations"] = rejected

        status = candidate.status
        answer = candidate.answer
        missing_information = list(candidate.missing_information)
        error_code: str | None = None
        mode = candidate.mode
        if status is CandidateAnswerStatus.ANSWERED and not valid_citations:
            # 模型说“能答”却没有任何可校验引用：不能交付无依据答案。
            status = CandidateAnswerStatus.NEEDS_HUMAN
            answer = ""
            missing_information.append("模型答复缺少可校验的知识引用")
            error_code = "no_valid_citations"
            base_snapshot["error"] = error_code

        return self._persist(
            session,
            identity=identity,
            conversation=conversation,
            query=source_query,
            rewritten_query=rewrite.query if rewrite.applied else None,
            business_type=business_type,
            status=status,
            answer=answer,
            citations=tuple(valid_citations),
            missing_information=tuple(missing_information),
            snapshot=base_snapshot,
            run_fields=_run_fields(
                self.gateway,
                index_version=index_version,
                knowledge_snapshot=knowledge_snapshot,
                candidate=candidate,
            ),
            error_code=error_code,
            model_status=candidate.model_status,
            provider=candidate.provider,
            model=candidate.model,
            mode=mode,
            now=now,
        )

    # ------------------------------------------------------------------ 查询处理
    def _resolve_query(
        self,
        session: Session,
        *,
        identity: Identity,
        conversation_id: UUID | None,
        query: str | None,
    ) -> tuple[Conversation | None, str]:
        conversation: Conversation | None = None
        if conversation_id is not None:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.scope_id != identity.scope_id:
                raise SuggestionNotFound("会话不存在")
            if query is None or not query.strip():
                last_turn = session.scalar(
                    select(Turn)
                    .where(Turn.conversation_id == conversation_id)
                    .order_by(Turn.ordinal.desc())
                    .limit(1)
                )
                query = last_turn.content if last_turn is not None else ""
        if query is None:
            raise SuggestionInputError("必须提供 conversation_id 或 query")
        if conversation is None and not query.strip():
            raise SuggestionInputError("query 不能为空")
        return conversation, query

    # ------------------------------------------------------------------ 召回与重排
    def _recall(
        self,
        session: Session,
        *,
        query: str,
        identity: Identity,
        business_type: str | None,
        index_version: str,
        now: datetime,
    ) -> tuple[
        list[RecalledChunk], list[dict[str, Any]], dict[str, Any], int, int
    ]:
        """召回 → 数据库复核 → 确定性重排。

        返回 ``(交付候选, 丢弃, 重排记录, 命中数, 距离内命中数)``。

        重排只在「已通过授权/有效期复核」的候选内进行，因此引用白名单不变：
        重排只改顺序与截断，不引入任何新 chunk。``top_k`` 仍是最终交付条数，
        重排候选池默认 20（可由配置覆盖）。
        """
        search_top_k = self.top_k
        if self.rerank_alpha > 0.0:
            search_top_k = max(self.top_k, self.rerank_pool)
        hits = search_index(
            self.vector_store,
            self.embedding_provider,
            index_version=index_version,
            query=query,
            top_k=search_top_k,
            business_type=business_type,
        )
        useful_hits = [hit for hit in hits if hit.distance <= self.max_distance]
        recalled, dropped = self._revalidate_hits(
            session,
            hits=useful_hits,
            identity=identity,
            business_type=business_type,
            index_version=index_version,
            now=now,
        )
        ordered, info = self._rerank(query, recalled)
        return ordered, dropped, info, len(hits), len(useful_hits)

    def _rerank(
        self, query: str, recalled: Sequence[RecalledChunk]
    ) -> tuple[list[RecalledChunk], dict[str, Any]]:
        """对已复核候选做确定性词面重排（与评测同一公式），返回交付顺序。

        - ``disabled``：α=0，纯向量排序（可单独回退）；
        - ``insufficient_candidates``：候选不足 2 条，重排没有意义；
        - ``applied``：候选池内按 ``score + α * 覆盖率`` 排序后截断 top_k。
        """
        by_chunk_id = {chunk.chunk_id: chunk for chunk in recalled}
        before = [str(chunk.chunk_id) for chunk in recalled]
        info: dict[str, Any] = {
            "mode": "lexical_char_ngram",
            "query": query,
            "alpha": self.rerank_alpha,
            "pool": self.rerank_pool,
            "candidate_count": len(recalled),
            "top_k": self.top_k,
            "before": before,
        }
        if self.rerank_alpha <= 0.0:
            ordered = list(recalled)
            info["status"] = "disabled"
        elif len(recalled) < 2:
            ordered = list(recalled)
            info["status"] = "insufficient_candidates"
        else:
            candidates = [
                RerankCandidate(
                    key=str(chunk.chunk_id),
                    title=chunk.title,
                    text=chunk.text,
                    score=chunk.score,
                )
                for chunk in recalled
            ]
            reranked = rerank_candidates(
                query,
                candidates,
                alpha=self.rerank_alpha,
                pool=max(self.rerank_pool, len(candidates)),
            )
            ordered = [by_chunk_id[UUID(candidate.key)] for candidate in reranked]
            info["status"] = "applied"
        delivered = ordered[: self.top_k]
        info["delivered"] = [str(chunk.chunk_id) for chunk in delivered]
        info["moved"] = before[: len(delivered)] != info["delivered"]
        return delivered, info

    # ------------------------------------------------------------------ 召回复核
    def _revalidate_hits(
        self,
        session: Session,
        *,
        hits: Sequence[VectorHit],
        identity: Identity,
        business_type: str | None,
        index_version: str,
        now: datetime,
    ) -> tuple[list[RecalledChunk], list[dict[str, Any]]]:
        recalled: list[RecalledChunk] = []
        dropped: list[dict[str, Any]] = []
        for hit in hits:
            chunk, reason = self._inspect_hit(
                session,
                hit=hit,
                identity=identity,
                business_type=business_type,
                index_version=index_version,
                now=now,
            )
            if chunk is None:
                dropped.append(
                    {
                        "chunk_id": str(hit.chunk_id),
                        "version_id": str(hit.version_id),
                        "distance": round(hit.distance, 6),
                        "reason": reason,
                    }
                )
                continue
            recalled.append(chunk)
        return recalled, dropped

    def _inspect_hit(
        self,
        session: Session,
        *,
        hit: VectorHit,
        identity: Identity,
        business_type: str | None,
        index_version: str,
        now: datetime,
        populate_existing: bool = False,
    ) -> tuple[RecalledChunk | None, str | None]:
        chunk = session.get(KnowledgeChunk, hit.chunk_id, populate_existing=populate_existing)
        if chunk is None:
            return None, "unknown_chunk"
        if chunk.index_version != index_version:
            return None, "chunk_not_in_active_index"
        if chunk.version_id != hit.version_id:
            return None, "chunk_version_mismatch"
        if chunk.text != hit.text:
            return None, "chunk_text_mismatch"
        version = session.get(
            KnowledgeVersion, chunk.version_id, populate_existing=populate_existing
        )
        if version is None:
            return None, "unknown_version"
        document = session.get(
            KnowledgeDocument, version.document_id, populate_existing=populate_existing
        )
        if document is None or document.scope_id != identity.scope_id:
            return None, "scope_denied"
        if business_type is not None and document.business_type != business_type:
            return None, "business_mismatch"
        reason = _version_validity_reason(version, now)
        if reason is not None:
            return None, reason
        if version.content[chunk.start_offset : chunk.end_offset] != chunk.text:
            return None, "content_offset_mismatch"
        return (
            RecalledChunk(
                chunk_id=chunk.chunk_id,
                version_id=version.version_id,
                document_id=document.document_id,
                version_no=version.version_no,
                title=document.title,
                source_type=document.source_type,
                business_type=document.business_type,
                text=chunk.text,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                distance=hit.distance,
            ),
            None,
        )

    def _recheck_recall(
        self,
        session: Session,
        *,
        recalled: Sequence[RecalledChunk],
        identity: Identity,
        business_type: str | None,
        index_version: str,
        now: datetime,
    ) -> tuple[list[RecalledChunk], list[dict[str, Any]]]:
        """用数据库最新状态复核本次召回；返回仍有效片段与被淘汰原因。"""
        current: list[RecalledChunk] = []
        retired: list[dict[str, Any]] = []
        for chunk in recalled:
            hit = VectorHit(
                chunk_id=chunk.chunk_id,
                version_id=chunk.version_id,
                text=chunk.text,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                distance=chunk.distance,
            )
            fresh, reason = self._inspect_hit(
                session,
                hit=hit,
                identity=identity,
                business_type=business_type,
                index_version=index_version,
                now=now,
                populate_existing=True,
            )
            if fresh is None:
                retired.append(
                    {
                        "chunk_id": str(chunk.chunk_id),
                        "version_id": str(chunk.version_id),
                        "reason": reason,
                    }
                )
            else:
                current.append(fresh)
        return current, retired

    def _detect_conflicts(
        self,
        session: Session,
        *,
        recalled: Sequence[RecalledChunk],
        identity: Identity,
        now: datetime,
    ) -> list[dict[str, Any]]:
        """同一召回文档若存在多个当前有效版本，显式标记冲突（契约 §3）。"""
        checked: set[UUID] = set()
        conflicts: list[dict[str, Any]] = []
        for chunk in recalled:
            if chunk.document_id in checked:
                continue
            checked.add(chunk.document_id)
            versions = fetch_retrievable_versions(
                session,
                scope_id=identity.scope_id,
                at=now,
                document_id=chunk.document_id,
            )
            if len(versions) > 1:
                conflicts.append(
                    {
                        "document_id": str(chunk.document_id),
                        "version_ids": [str(version.version_id) for version in versions],
                    }
                )
        return conflicts

    # ------------------------------------------------------------------ 引用校验
    def _validate_citations(
        self,
        session: Session,
        citations: Sequence[Citation],
        *,
        recalled: Sequence[RecalledChunk],
        identity: Identity,
        business_type: str | None,
        index_version: str,
        now: datetime,
    ) -> tuple[list[Citation], list[dict[str, Any]]]:
        by_chunk = {str(chunk.chunk_id): chunk for chunk in recalled}
        valid: list[Citation] = []
        rejected: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for citation in citations:
            try:
                chunk_id = UUID(citation.chunk_id)
                version_id = UUID(citation.version_id)
            except (ValueError, AttributeError, TypeError):
                rejected.append(
                    {
                        "chunk_id": citation.chunk_id,
                        "version_id": citation.version_id,
                        "reason": "invalid_id",
                    }
                )
                continue
            key = (str(chunk_id), str(version_id), citation.quote)
            if key in seen:
                continue
            seen.add(key)

            recalled_chunk = by_chunk.get(str(chunk_id))
            if recalled_chunk is None:
                rejected.append(
                    {
                        "chunk_id": str(chunk_id),
                        "version_id": str(version_id),
                        "reason": "citation_not_in_recall",
                    }
                )
                continue
            if recalled_chunk.version_id != version_id:
                rejected.append(
                    {
                        "chunk_id": str(chunk_id),
                        "version_id": str(version_id),
                        "reason": "citation_version_mismatch",
                    }
                )
                continue
            quote = citation.quote.strip()
            if quote and quote not in recalled_chunk.text:
                rejected.append(
                    {
                        "chunk_id": str(chunk_id),
                        "version_id": str(version_id),
                        "reason": "quote_not_in_chunk",
                    }
                )
                continue
            if len(quote) > MAX_QUOTE_LENGTH:
                rejected.append(
                    {
                        "chunk_id": str(chunk_id),
                        "version_id": str(version_id),
                        "reason": "quote_too_long",
                    }
                )
                continue

            hit = VectorHit(
                chunk_id=recalled_chunk.chunk_id,
                version_id=recalled_chunk.version_id,
                text=recalled_chunk.text,
                start_offset=recalled_chunk.start_offset,
                end_offset=recalled_chunk.end_offset,
                distance=recalled_chunk.distance,
            )
            _, reason = self._inspect_hit(
                session,
                hit=hit,
                identity=identity,
                business_type=business_type,
                index_version=index_version,
                now=now,
                populate_existing=True,
            )
            if reason is not None:
                rejected.append(
                    {
                        "chunk_id": str(chunk_id),
                        "version_id": str(version_id),
                        "reason": reason,
                    }
                )
                continue
            valid.append(citation)
        return valid, rejected

    # ------------------------------------------------------------------ 持久化
    def _persist(
        self,
        session: Session,
        *,
        identity: Identity,
        conversation: Conversation | None,
        query: str,
        rewritten_query: str | None,
        business_type: str | None,
        status: CandidateAnswerStatus,
        answer: str,
        citations: Sequence[Citation],
        missing_information: Sequence[str],
        snapshot: dict[str, Any],
        run_fields: dict[str, Any],
        error_code: str | None,
        now: datetime,
        model_status: ModelCallStatus | None = None,
        provider: str | None = None,
        model: str | None = None,
        mode: str | None = None,
    ) -> SuggestionOutcome:
        run_id = uuid4()
        run = ModelRun(
            run_id=run_id,
            provider=provider or run_fields.get("provider") or "none",
            model=model or run_fields.get("model") or "none",
            model_version=run_fields.get("model_version") or "none",
            prompt_version=run_fields.get("prompt_version") or SUGGESTION_PROMPT_VERSION,
            knowledge_snapshot=run_fields.get("knowledge_snapshot") or [],
            index_version=run_fields.get("index_version"),
            code_sha=self.code_sha,
            mode=mode or run_fields.get("mode") or self.gateway.mode,
            created_at=now,
        )
        suggestion = Suggestion(
            suggestion_id=uuid4(),
            conversation_id=conversation.conversation_id if conversation else None,
            run_id=run_id,
            scope_id=identity.scope_id,
            query=query,
            rewritten_query=rewritten_query,
            business_type=business_type,
            status=status.value,
            answer=answer,
            citations=[citation.model_dump() for citation in citations],
            missing_information=list(missing_information),
            retrieval_snapshot=snapshot,
            mode=run.mode,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.add(suggestion)
        session.commit()
        if error_code:
            logger.info(
                "suggestion_status=%s error=%s run_id=%s suggestion_id=%s",
                status.value,
                error_code,
                run_id,
                suggestion.suggestion_id,
            )
        return SuggestionOutcome(
            suggestion=suggestion,
            run=run,
            status=status,
            error_code=error_code,
            model_status=model_status,
            provider=run.provider,
            model=run.model,
            mode=run.mode,
        )


def _version_validity_reason(version: KnowledgeVersion, now: datetime) -> str | None:
    """版本是否仍可用于交付引用；返回稳定原因串，合法时返回 ``None``。"""
    if version.status != "published":
        return "version_not_published"
    valid_from = _as_utc(version.valid_from)
    valid_to = _as_utc(version.valid_to) if version.valid_to is not None else None
    if valid_from > now:
        return "version_not_yet_valid"
    if valid_to is not None and valid_to <= now:
        return "version_expired"
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_suggestion_messages(
    *,
    query: str,
    chunks: Sequence[RecalledChunk],
    business_type: str | None,
) -> list[dict[str, str]]:
    """系统提示词 + 本次召回片段；片段 ID 是引用校验的唯一白名单。"""
    knowledge = [
        {
            "chunk_id": str(chunk.chunk_id),
            "version_id": str(chunk.version_id),
            "title": chunk.title,
            "source_type": chunk.source_type,
            "business_type": chunk.business_type,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    user_payload = {
        # 防御性截断：即使调用方绕过 HTTP 入口，也不把无界问题塞给模型。
        "query": truncate_text(query, settings.MAX_PROMPT_CHARS),
        "query_truncated": len(query) > settings.MAX_PROMPT_CHARS,
        "business_type": business_type,
        "knowledge": knowledge,
    }
    return [
        {"role": "system", "content": SUGGESTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请依据下列知识片段生成候选答复。引用只能使用给定的 chunk_id 与 "
                "version_id。\n"
                + json.dumps(user_payload, ensure_ascii=False)
            ),
        },
    ]


def _run_fields(
    gateway: ModelGateway,
    *,
    index_version: str | None = None,
    knowledge_snapshot: Sequence[Any] | None = None,
    candidate: CandidateAnswer | None = None,
) -> dict[str, Any]:
    """整理 run 元数据；模型未调用时 model/provider 为 none。"""
    if candidate is not None:
        return {
            "provider": candidate.provider or "none",
            "model": candidate.model or "none",
            "model_version": candidate.model or "none",
            "prompt_version": SUGGESTION_PROMPT_VERSION,
            "knowledge_snapshot": list(knowledge_snapshot or []),
            "index_version": index_version,
            "mode": candidate.mode,
        }
    return {
        "provider": "none",
        "model": "none",
        "model_version": "none",
        "prompt_version": SUGGESTION_PROMPT_VERSION,
        "knowledge_snapshot": list(knowledge_snapshot or []),
        "index_version": index_version,
        "mode": gateway.mode,
    }


__all__ = [
    "DEFAULT_MAX_DISTANCE",
    "DEFAULT_TOP_K",
    "GatewayQueryRewriter",
    "QueryRewriter",
    "RecalledChunk",
    "RewriteOutcome",
    "SUGGESTION_PROMPT_VERSION",
    "SUGGESTION_SYSTEM_PROMPT",
    "SuggestionError",
    "SuggestionInputError",
    "SuggestionNotFound",
    "SuggestionOutcome",
    "SuggestionService",
    "build_suggestion_messages",
    "current_code_sha",
]
