"""
话术推荐器
基于RAG技术推荐合适的客服话术
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Sequence
from datetime import datetime

from bank_assistant.core.llm_client import LLMClient
from bank_assistant.core.vector_db import VectorDBClient
from bank_assistant.utils.logger import LoggerMixin
from bank_assistant.config.constants import PromptTemplates, DEFAULT_TOP_K

logger = logging.getLogger(__name__)

# 重排结果状态：必须显式暴露，不能把失败伪装成成功
RERANK_STATUS_APPLIED = "applied"
RERANK_STATUS_SKIPPED = "skipped"
RERANK_STATUS_FAILED = "failed"

# 跳过重排的可记录原因
RERANK_REASON_NOT_RUN = "not_run"
RERANK_REASON_MISSING_CONTEXT = "missing_context"
RERANK_REASON_TOO_FEW_CANDIDATES = "candidate_count_lt_2"
RERANK_REASON_NO_CANDIDATES = "no_candidates"
RERANK_REASON_NO_VECTOR_DB = "vector_db_unavailable"
RERANK_REASON_COUNT_NOT_POSITIVE = "count_not_positive"

# 机器可读错误码
RERANK_ERROR_INTERNAL = "RERANK_INTERNAL_ERROR"
RERANK_ERROR_INVALID_CONTEXT = "RERANK_INVALID_CONTEXT"
RERANK_ERROR_INVALID_OUTPUT = "RERANK_INVALID_OUTPUT"
RETRIEVAL_ERROR_CODE = "RETRIEVAL_FAILED"


class RerankError(Exception):
    """重排组件显式失败，携带机器可读错误码。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class RerankOutcome:
    """一次重排的可观测结果。"""

    status: str
    error_code: Optional[str] = None
    reason: str = RERANK_REASON_NOT_RUN


class DeterministicReranker:
    """本地确定性重排，不依赖网络重排服务（ADR-0004：`/v1/rerank` 不可用）。

    排序键依次为：情境词命中数降序、向量相关性分数降序、原候选序号升序。
    相同输入永远得到相同顺序，便于测试与回放。
    """

    def rerank(
        self,
        scripts: Sequence["RecommendedScript"],
        context: "ConversationContext"
    ) -> List["RecommendedScript"]:
        if context is None:
            raise RerankError(
                RERANK_ERROR_INVALID_CONTEXT, "缺少对话情境，无法重排"
            )

        topic = getattr(context, "topic", None)
        if not isinstance(topic, str) or not topic.strip():
            raise RerankError(
                RERANK_ERROR_INVALID_CONTEXT, "对话情境缺少有效主题"
            )

        terms = [
            term
            for term in (
                topic,
                getattr(context, "stage", ""),
                getattr(context, "emotion", ""),
            )
            if isinstance(term, str) and term.strip()
        ]

        def sort_key(indexed):
            index, script = indexed
            text = script.content or ""
            hits = sum(1 for term in terms if term in text)
            try:
                score = float(script.relevance_score or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            return (-hits, -score, index)

        return [script for _, script in sorted(enumerate(scripts), key=sort_key)]


class RecommendedScript:
    """
    推荐话术数据模型
    """
    
    def __init__(
        self,
        script_id: str,
        content: str,
        title: str = None,
        relevance_score: float = 0.0,
        usage_count: int = 0,
        success_rate: float = 0.0,
        metadata: Dict[str, Any] = None
    ):
        self.script_id = script_id                  # 话术ID
        self.content = content                       # 话术内容
        self.title = title or "推荐话术"              # 话术标题
        self.relevance_score = relevance_score       # 相关性分数
        self.usage_count = usage_count               # 使用次数
        self.success_rate = success_rate             # 成功率
        self.metadata = metadata or {}               # 元数据
        self.timestamp = datetime.now()              # 推荐时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "script_id": self.script_id,
            "content": self.content,
            "title": self.title,
            "relevance_score": self.relevance_score,
            "usage_count": self.usage_count,
            "success_rate": self.success_rate,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat()
        }


class ScriptRecommender(LoggerMixin):
    """
    话术推荐器
    基于RAG技术推荐合适的客服话术
    """
    
    def __init__(
        self,
        llm_client: LLMClient,
        vector_db: VectorDBClient = None,
        reranker: "DeterministicReranker" = None
    ):
        """
        初始化话术推荐器
        
        Args:
            llm_client (LLMClient): 大模型客户端
            vector_db (VectorDBClient): 向量数据库客户端
            reranker: 可替换的重排组件，默认使用本地确定性重排
        """
        self.llm_client = llm_client
        self.vector_db = vector_db
        self.reranker = reranker or DeterministicReranker()
        self.last_rerank = RerankOutcome(
            RERANK_STATUS_SKIPPED, reason=RERANK_REASON_NOT_RUN
        )
        self.logger.info("话术推荐器初始化完成")
    
    def recommend_scripts(
        self,
        context: "ConversationContext",
        intent: "UserIntent",
        count: int = 3,
        filters: Dict[str, Any] = None
    ) -> List[RecommendedScript]:
        """
        推荐话术
        
        Args:
            context (ConversationContext): 对话情境
            intent (UserIntent): 用户意图
            count (int): 推荐数量
            filters (Dict[str, Any]): 过滤条件，透传到向量检索的 where
            
        Returns:
            List[RecommendedScript]: 推荐的话术列表，长度不超过 count
        """
        self.last_rerank = RerankOutcome(
            RERANK_STATUS_SKIPPED, reason=RERANK_REASON_NOT_RUN
        )
        try:
            # 防御非法数量：绝不返回超过请求数量的候选
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                self.last_rerank = RerankOutcome(
                    RERANK_STATUS_SKIPPED,
                    reason=RERANK_REASON_COUNT_NOT_POSITIVE,
                )
                return []

            # 构建查询文本
            query_text = f"""
情境: {context.topic}, {context.stage}
意图: {intent.intent_type}
客户情绪: {context.emotion}
            """.strip()
            
            # 如果有向量数据库，进行检索增强
            if self.vector_db:
                scripts = self._rag_recommend(query_text, count, context, filters)
            else:
                scripts = self._llm_recommend(context, intent, count)
                self.last_rerank = RerankOutcome(
                    RERANK_STATUS_SKIPPED, reason=RERANK_REASON_NO_VECTOR_DB
                )
            
            scripts = scripts[:count]
            self.logger.info(f"推荐了 {len(scripts)} 个话术")
            return scripts
            
        except Exception as e:
            self.last_rerank = RerankOutcome(
                RERANK_STATUS_FAILED,
                error_code=RETRIEVAL_ERROR_CODE,
                reason=str(e),
            )
            self.logger.error(f"话术推荐失败: {str(e)}")
            return []
    
    def search_similar_scripts(
        self,
        query: str,
        filters: Dict[str, Any] = None,
        count: int = DEFAULT_TOP_K
    ) -> List[RecommendedScript]:
        """
        搜索相似话术
        
        Args:
            query (str): 查询内容
            filters (Dict[str, Any]): 过滤条件
            count (int): 返回数量
            
        Returns:
            List[RecommendedScript]: 相似话术列表
        """
        try:
            if not self.vector_db:
                self.logger.warning("向量数据库未初始化，无法进行相似搜索")
                return []
            
            results = self.vector_db.query_with_score(
                query_text=[query],
                n_results=max(count, 0),
                where=filters
            )
            
            scripts = []
            for i, result in enumerate(results):
                scripts.append(RecommendedScript(
                    script_id=result.get("id", f"script_{i}"),
                    content=result.get("document", ""),
                    title=result.get("metadata", {}).get("title", "推荐话术"),
                    relevance_score=1.0 - (result.get("distance", 0) or 0),
                    metadata=result.get("metadata", {})
                ))
            
            return scripts[:count]
            
        except Exception as e:
            self.logger.error(f"话术搜索失败: {str(e)}")
            return []
    
    def rank_scripts(
        self,
        scripts: List[RecommendedScript],
        context: "ConversationContext"
    ) -> List[RecommendedScript]:
        """
        对话术进行重排，并显式记录结果状态
        
        Args:
            scripts (List[RecommendedScript]): 话术列表
            context (ConversationContext): 对话情境；为 None 时跳过重排并记录原因
            
        Returns:
            List[RecommendedScript]: 排序后的话术列表；失败时保持原顺序但记录错误码
        """
        if not scripts:
            self.last_rerank = RerankOutcome(
                RERANK_STATUS_SKIPPED, reason=RERANK_REASON_NO_CANDIDATES
            )
            return scripts

        if context is None:
            self.last_rerank = RerankOutcome(
                RERANK_STATUS_SKIPPED, reason=RERANK_REASON_MISSING_CONTEXT
            )
            self.logger.warning("缺少对话情境，跳过重排（missing_context）")
            return scripts

        if len(scripts) <= 1:
            self.last_rerank = RerankOutcome(
                RERANK_STATUS_SKIPPED, reason=RERANK_REASON_TOO_FEW_CANDIDATES
            )
            return scripts

        try:
            ordered = self.reranker.rerank(scripts, context)
            self._validate_rerank_output(scripts, ordered)
            self.last_rerank = RerankOutcome(RERANK_STATUS_APPLIED)
            return list(ordered)

        except RerankError as e:
            self.last_rerank = RerankOutcome(
                RERANK_STATUS_FAILED, error_code=e.code, reason=str(e)
            )
            self.logger.error(f"话术重排失败[{e.code}]: {e}")
            return scripts

        except Exception as e:
            self.last_rerank = RerankOutcome(
                RERANK_STATUS_FAILED,
                error_code=RERANK_ERROR_INTERNAL,
                reason=str(e),
            )
            self.logger.error(f"话术重排失败[{RERANK_ERROR_INTERNAL}]: {e}")
            return scripts

    @staticmethod
    def _validate_rerank_output(
        scripts: List[RecommendedScript],
        ordered: List[RecommendedScript]
    ):
        """重排组件不得增删或替换候选。"""
        if not isinstance(ordered, (list, tuple)):
            raise RerankError(
                RERANK_ERROR_INVALID_OUTPUT, "重排结果必须是列表"
            )
        if len(ordered) != len(scripts) or {id(s) for s in ordered} != {
            id(s) for s in scripts
        }:
            raise RerankError(
                RERANK_ERROR_INVALID_OUTPUT, "重排结果必须包含原候选集合"
            )
    
    def _rag_recommend(
        self,
        query_text: str,
        count: int,
        context: "ConversationContext",
        filters: Dict[str, Any] = None
    ) -> List[RecommendedScript]:
        """RAG推荐：检索候选、重排、按 count 裁剪。"""
        results = self.vector_db.query_with_score(
            query_text=[query_text],
            n_results=count * 2,  # 获取更多候选供重排
            where=filters
        )

        scripts = []
        for i, result in enumerate(results):
            score = 1.0 - (result.get("distance", 0) or 0)
            scripts.append(RecommendedScript(
                script_id=result.get("id", f"script_{i}"),
                content=result.get("document", ""),
                title=result.get("metadata", {}).get("title", "推荐话术"),
                relevance_score=score,
                metadata=result.get("metadata", {})
            ))

        # 用真实情境重排，并严格裁剪到 count
        return self.rank_scripts(scripts, context)[:count]
    
    def _llm_recommend(
        self,
        context: "ConversationContext",
        intent: "UserIntent",
        count: int
    ) -> List[RecommendedScript]:
        """纯LLM推荐"""
        try:
            prompt = PromptTemplates.SCRIPT_RECOMMEND.format(
                question=intent.intent_type,
                emotion=context.emotion,
                customer_type=context.complexity
            )
            
            response = self.llm_client.generate_text(prompt)
            
            scripts = []
            lines = response.strip().split('\n')
            
            for i, line in enumerate(lines[:count]):
                if line.strip():
                    scripts.append(RecommendedScript(
                        script_id=f"script_{i}",
                        content=line.strip(),
                        title=f"推荐话术 #{i+1}",
                        relevance_score=1.0 - (i * 0.1)
                    ))
            
            return scripts
            
        except Exception as e:
            self.logger.error(f"LLM推荐失败: {str(e)}")
            return []
