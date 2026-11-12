"""
话术推荐器
基于RAG技术推荐合适的客服话术
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from core.vector_db import VectorDBClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, DEFAULT_TOP_K

logger = logging.getLogger(__name__)


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
        vector_db: VectorDBClient = None
    ):
        """
        初始化话术推荐器
        
        Args:
            llm_client (LLMClient): 大模型客户端
            vector_db (VectorDBClient): 向量数据库客户端
        """
        self.llm_client = llm_client
        self.vector_db = vector_db
        self.logger.info("话术推荐器初始化完成")
    
    def recommend_scripts(
        self,
        context: "ConversationContext",
        intent: "UserIntent",
        count: int = 3
    ) -> List[RecommendedScript]:
        """
        推荐话术
        
        Args:
            context (ConversationContext): 对话情境
            intent (UserIntent): 用户意图
            count (int): 推荐数量
            
        Returns:
            List[RecommendedScript]: 推荐的话术列表
        """
        try:
            # 构建查询文本
            query_text = f"""
情境: {context.topic}, {context.stage}
意图: {intent.intent_type}
客户情绪: {context.emotion}
            """.strip()
            
            # 如果有向量数据库，进行检索增强
            if self.vector_db:
                scripts = self._rag_recommend(query_text, count)
            else:
                scripts = self._llm_recommend(context, intent, count)
            
            self.logger.info(f"推荐了 {len(scripts)} 个话术")
            return scripts
            
        except Exception as e:
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
                query_text=query,
                n_results=count,
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
            
            return scripts
            
        except Exception as e:
            self.logger.error(f"话术搜索失败: {str(e)}")
            return []
    
    def rank_scripts(
        self,
        scripts: List[RecommendedScript],
        context: "ConversationContext"
    ) -> List[RecommendedScript]:
        """
        对话术进行排序
        
        Args:
            scripts (List[RecommendedScript]): 话术列表
            context (ConversationContext): 对话情境
            
        Returns:
            List[RecommendedScript]: 排序后的话术列表
        """
        try:
            # 使用LLM对话术进行重排序
            if len(scripts) <= 1:
                return scripts
            
            script_list = "\n".join([
                f"{i+1}. {s.content[:100]}..."
                for i, s in enumerate(scripts)
            ])
            
            prompt = f"""
请对话术进行相关性排序，选出最适合当前情境的话术：

当前情境：
- 主题: {context.topic}
- 阶段: {context.stage}
- 客户情绪: {context.emotion}
- 客户满意度: {context.customer_satisfaction}

话术列表：
{script_list}

请按照最佳到最差的顺序输出话术编号（用逗号分隔）：
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析排序结果
            import re
            numbers = re.findall(r'\d+', response)
            
            if numbers:
                try:
                    ranking = [int(n) - 1 for n in numbers[:len(scripts)]]
                    valid_ranking = [n for n in ranking if 0 <= n < len(scripts)]
                    
                    # 如果有未提及的话术，将它们放在最后
                    ranked_set = set(valid_ranking)
                    remaining = [i for i in range(len(scripts)) if i not in ranked_set]
                    
                    sorted_scripts = [scripts[i] for i in valid_ranking + remaining]
                    return sorted_scripts
                except (ValueError, IndexError):
                    pass
            
            return scripts
            
        except Exception as e:
            self.logger.error(f"话术排序失败: {str(e)}")
            return scripts
    
    def _rag_recommend(
        self,
        query_text: str,
        count: int
    ) -> List[RecommendedScript]:
        """RAG推荐"""
        try:
            results = self.vector_db.query_with_score(
                query_text=[query_text],
                n_results=count * 2  # 获取更多候选
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
            
            # 重排序
            return self.rank_scripts(scripts, None)
            
        except Exception as e:
            self.logger.error(f"RAG推荐失败: {str(e)}")
            return []
    
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
