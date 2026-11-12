"""候选答复路由（契约 §5 ``POST /suggestions``，M3-04/T15；反馈见 T16）。

- 身份与 ``scope_id`` 只来自服务端（T10）；请求体不能放宽范围。
- 输入是 ``conversation_id`` 或原始 ``query``，业务类型可选。
- 业务状态（answered / needs_clarification / needs_human）以 201 返回并持久化；
  模型或索引等**系统失败**记录 ``failed`` 行后返回明确的 5xx 错误，不以
  200 + failed 伪装成功（与 :mod:`bank_assistant.api.errors` 的模型错误契约一致）。
- ``POST /suggestions/{id}/feedback`` 追加坐席采纳/编辑/拒绝反馈；
  ``GET /suggestions/{id}`` 返回该条建议及其反馈史（比另建反馈列表接口更小）。
  反馈**不是正确性标签**，不得作为评测的 ground-truth。
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError, model_failure_error
from bank_assistant.api.models.suggestions import (
    SuggestionCreateRequest,
    SuggestionDetail,
    SuggestionFeedbackPublic,
    SuggestionFeedbackRequest,
    SuggestionPublic,
    feedback_to_public,
    to_detail,
    to_public,
)
from bank_assistant.assistants.candidate_answer import CandidateAnswerStatus
from bank_assistant.assistants.suggestion_feedback import (
    SuggestionFeedbackInputError,
    SuggestionFeedbackService,
)
from bank_assistant.assistants.suggestions import (
    GatewayQueryRewriter,
    QueryRewriter,
    SuggestionInputError,
    SuggestionNotFound,
    SuggestionService,
)
from bank_assistant.config.settings import settings
from bank_assistant.core.model_gateway import (
    ModelGateway,
    ModelProvider,
    ModelResult,
)
from bank_assistant.infra.db import get_db_session
from bank_assistant.knowledge.embedding import (
    EmbeddingProvider,
    create_embedding_provider,
)
from bank_assistant.knowledge.indexing import (
    ChunkVectorStore,
    ChromaVectorStore,
)
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.rate_limit import enforce_expensive_rate_limit
from bank_assistant.security.identity import Identity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["候选答复"])


def get_model_gateway() -> ModelGateway:
    """统一模型适配层；测试通过 dependency_overrides 注入假网关。"""
    return ModelGateway.from_settings()


def get_embedding_provider() -> EmbeddingProvider:
    """与索引构建使用同一 provider；加载索引前会重算校准哈希。"""
    return create_embedding_provider(
        settings.EMBEDDING_PROVIDER, model_name=settings.EMBEDDING_MODEL_NAME
    )


def get_vector_store() -> ChunkVectorStore:
    """Chroma 持久化向量库；集合按 index_version 命名。"""
    return ChromaVectorStore(Path(settings.INDEX_DIR) / "chroma")


def get_index_dir() -> Path:
    """活动指针与清单所在目录；测试可覆盖为 tmp_path。"""
    return Path(settings.INDEX_DIR)


def get_query_rewriter(
    gateway: ModelGateway = Depends(get_model_gateway),
) -> QueryRewriter:
    """默认启用核心诉求改写；改写结果与失败都会写入 retrieval_snapshot。"""
    return GatewayQueryRewriter(gateway)


def get_suggestion_service(
    gateway: ModelGateway = Depends(get_model_gateway),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    vector_store: ChunkVectorStore = Depends(get_vector_store),
    index_dir: Path = Depends(get_index_dir),
    rewriter: QueryRewriter = Depends(get_query_rewriter),
) -> SuggestionService:
    return SuggestionService(
        gateway=gateway,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        index_dir=index_dir,
        rewriter=rewriter,
    )


@router.post(
    "/suggestions",
    response_model=SuggestionPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_expensive_rate_limit)],
)
def create_suggestion_endpoint(
    payload: SuggestionCreateRequest,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
    service: SuggestionService = Depends(get_suggestion_service),
) -> SuggestionPublic:
    """生成一条候选答复；系统失败保留 failed 记录并返回显式错误。"""
    try:
        outcome = service.create_suggestion(
            session,
            identity=identity,
            conversation_id=payload.conversation_id,
            query=payload.query,
            business_type=payload.business_type,
        )
    except SuggestionNotFound as exc:
        raise ApiError(
            status_code=404,
            code="NOT_FOUND",
            message="会话不存在",
        ) from exc
    except SuggestionInputError as exc:
        raise ApiError(
            status_code=422,
            code=exc.code,
            message=str(exc),
            retryable=False,
        ) from exc

    details = {
        "suggestion_id": str(outcome.suggestion.suggestion_id),
        "run_id": str(outcome.run.run_id),
        "status": outcome.suggestion.status,
        "reason": outcome.error_code,
    }
    if outcome.status is CandidateAnswerStatus.FAILED:
        if outcome.error_code == "model_failure" and outcome.model_status is not None:
            provider = None
            if outcome.provider:
                try:
                    provider = ModelProvider(outcome.provider)
                except ValueError:
                    provider = None
            error = model_failure_error(
                ModelResult(
                    status=outcome.model_status,
                    provider=provider,
                    model=outcome.model or "",
                    mode=outcome.mode,
                )
            )
            error.details.update(details)
            raise error
        raise ApiError(
            status_code=503,
            code="SUGGESTION_UNAVAILABLE",
            message="候选答复所需的知识索引或依赖不可用",
            retryable=False,
            details=details,
        )

    return to_public(outcome.suggestion)


def get_suggestion_feedback_service() -> SuggestionFeedbackService:
    """反馈服务；测试可通过 dependency_overrides 注入固定时钟。"""
    return SuggestionFeedbackService()


@router.post(
    "/suggestions/{suggestion_id}/feedback",
    response_model=SuggestionFeedbackPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_suggestion_feedback_endpoint(
    suggestion_id: UUID,
    payload: SuggestionFeedbackRequest,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
    service: SuggestionFeedbackService = Depends(get_suggestion_feedback_service),
) -> SuggestionFeedbackPublic:
    """追加一条坐席反馈；反馈是反馈，不是正确性标签。"""
    try:
        feedback = service.record_feedback(
            session,
            identity=identity,
            suggestion_id=suggestion_id,
            action=payload.action.value,
            edited_text=payload.edited_text,
            reason=payload.reason,
        )
    except SuggestionNotFound as exc:
        # 不存在与跨范围返回同一个 404，不泄露资源存在性。
        raise ApiError(
            status_code=404,
            code="NOT_FOUND",
            message="建议不存在",
        ) from exc
    except SuggestionFeedbackInputError as exc:
        raise ApiError(
            status_code=422,
            code=exc.code,
            message=str(exc),
            retryable=False,
        ) from exc

    return feedback_to_public(feedback)


@router.get("/suggestions/{suggestion_id}", response_model=SuggestionDetail)
def get_suggestion_endpoint(
    suggestion_id: UUID,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
    service: SuggestionFeedbackService = Depends(get_suggestion_feedback_service),
) -> SuggestionDetail:
    """单条建议 + 反馈史；一次请求即可追溯该建议版本的全部反馈。"""
    try:
        suggestion, feedback = service.suggestion_history(
            session, identity=identity, suggestion_id=suggestion_id
        )
    except SuggestionNotFound as exc:
        raise ApiError(
            status_code=404,
            code="NOT_FOUND",
            message="建议不存在",
        ) from exc

    return to_detail(suggestion, feedback)


__all__ = [
    "create_suggestion_endpoint",
    "create_suggestion_feedback_endpoint",
    "get_embedding_provider",
    "get_index_dir",
    "get_model_gateway",
    "get_query_rewriter",
    "get_suggestion_endpoint",
    "get_suggestion_feedback_service",
    "get_suggestion_service",
    "get_vector_store",
    "router",
]
