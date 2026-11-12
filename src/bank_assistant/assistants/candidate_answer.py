"""候选答复：把模型输出映射为契约中的业务状态。

契约（docs/plan/bank-customer-service-contracts.md §3）：
`answered / needs_clarification / needs_human / failed`。系统或模型执行失败
只能产生 `failed`；系统失败不得伪装成追问或转人工。失败分类保留在
`model_status`，供日志与评测区分“模型没说话”和“业务上证据不足”。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bank_assistant.core.model_gateway import ModelCallStatus, ModelGateway, ModelResult

logger = logging.getLogger(__name__)


class CandidateAnswerStatus(str, Enum):
    """候选答复的业务状态。"""

    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class Citation(BaseModel):
    """指向具体知识版本与原文片段的引用。"""

    version_id: str
    chunk_id: str
    quote: str = ""

    model_config = ConfigDict(extra="ignore")


class CandidateAnswerPayload(BaseModel):
    """模型必须返回的结构化候选答复。"""

    status: Literal["answered", "needs_clarification", "needs_human"]
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def answered_needs_content(self) -> "CandidateAnswerPayload":
        if self.status == "answered" and not self.answer.strip():
            raise ValueError("status=answered 时 answer 不能为空")
        return self


@dataclass(frozen=True)
class CandidateAnswer:
    """供坐席检查/编辑的候选答复；不表示已发送或已办理。"""

    status: CandidateAnswerStatus
    answer: str = ""
    citations: tuple[Citation, ...] = ()
    missing_information: tuple[str, ...] = ()
    model_status: ModelCallStatus = ModelCallStatus.SUCCEEDED
    provider: Optional[str] = None
    model: Optional[str] = None
    mode: str = "live"
    error: Optional[str] = None


class CandidateAnswerService:
    """用统一适配层生成候选答复。"""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def generate(
        self,
        messages: Sequence[dict],
        *,
        max_tokens: Optional[int] = None,
    ) -> CandidateAnswer:
        result: ModelResult = self.gateway.structured(
            messages,
            schema=CandidateAnswerPayload,
            max_tokens=max_tokens,
        )
        if not result.ok:
            # 失败只落到 failed；保留 model_status 以区分超时/限流/结构/空内容。
            logger.warning(
                "candidate_answer_failed status=%s provider=%s model=%s mode=%s error=%s",
                result.status.value,
                result.provider.value if result.provider else None,
                result.model,
                result.mode,
                result.error,
            )
            return CandidateAnswer(
                status=CandidateAnswerStatus.FAILED,
                model_status=result.status,
                provider=result.provider.value if result.provider else None,
                model=result.model,
                mode=result.mode,
                error=result.error,
            )

        payload: CandidateAnswerPayload = result.data
        return CandidateAnswer(
            status=CandidateAnswerStatus(payload.status),
            answer=payload.answer,
            citations=tuple(payload.citations),
            missing_information=tuple(payload.missing_information),
            model_status=result.status,
            provider=result.provider.value if result.provider else None,
            model=result.model,
            mode=result.mode,
        )


__all__ = [
    "CandidateAnswer",
    "CandidateAnswerPayload",
    "CandidateAnswerService",
    "CandidateAnswerStatus",
    "Citation",
]
