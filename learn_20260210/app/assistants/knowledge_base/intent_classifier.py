"""
客户意图分类器
识别和改写客户意图
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, IntentCategories

logger = logging.getLogger(__name__)


class IntentClassification:
    """
    意图分类结果数据模型
    """
    
    def __init__(
        self,
        primary_intent: str,
        secondary_intent: str = None,
        confidence: float = 0.0,
        categories: List[str] = None,
        rewritten_query: str = None
    ):
        self.primary_intent = primary_intent        # 主要意图
        self.secondary_intent = secondary_intent    # 次要意图
        self.confidence = confidence                # 置信度
        self.categories = categories or []          # 意图分类列表
        self.rewritten_query = rewritten_query      # 改写后的查询
        self.timestamp = datetime.now()             # 时间戳
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "primary_intent": self.primary_intent,
            "secondary_intent": self.secondary_intent,
            "confidence": self.confidence,
            "categories": self.categories,
            "rewritten_query": self.rewritten_query,
            "timestamp": self.timestamp.isoformat()
        }


class IntentClassifier(LoggerMixin):
    """
    客户意图分类器
    识别和改写客户意图
    """
    
    def __init__(
        self,
        llm_client: LLMClient,
        categories: List[str] = None
    ):
        """
        初始化意图分类器
        
        Args:
            llm_client (LLMClient): 大模型客户端
            categories (List[str]): 意图分类列表
        """
        self.llm_client = llm_client
        self.categories = categories or [
            IntentCategories.ACCOUNT_QUERY,
            IntentCategories.BUSINESS办理,
            IntentCategories.COMPLAINT,
            IntentCategories.FEEDBACK,
            IntentCategories.TECHNICAL_SUPPORT,
            IntentCategories.OTHER
        ]
        self.logger.info("意图分类器初始化完成")
    
    def classify_intent(
        self,
        query: str,
        categories: List[str] = None
    ) -> IntentClassification:
        """
        分类客户意图
        
        Args:
            query (str): 客户查询
            categories (List[str]): 意图分类列表
            
        Returns:
            IntentClassification: 意图分类结果
        """
        try:
            categories = categories or self.categories
            
            result = self.llm_client.classify_intent(
                text=query,
                categories=categories
            )
            
            # 改写查询
            rewritten_query = self.rewrite_query(query)
            
            return IntentClassification(
                primary_intent=result.get("intent", categories[0]),
                confidence=result.get("confidence", 0.0),
                categories=categories,
                rewritten_query=rewritten_query
            )
            
        except Exception as e:
            self.logger.error(f"意图分类失败: {str(e)}")
            return IntentClassification(
                primary_intent=IntentCategories.OTHER,
                confidence=0.0,
                categories=categories or self.categories
            )
    
    def rewrite_query(self, query: str) -> str:
        """
        改写客户查询，使其更准确
        
        Args:
            query (str): 原始查询
            
        Returns:
            str: 改写后的查询
        """
        try:
            prompt = f"""
请将以下客户查询改写为更清晰、更准确的表达：

原始查询：{query}

要求：
1. 保持原意不变
2. 使表达更加简洁明确
3. 去除口语化和无关内容

改写后的查询：
            """
            
            response = self.llm_client.generate_text(prompt)
            return response.strip()
            
        except Exception as e:
            self.logger.error(f"查询改写失败: {str(e)}")
            return query
    
    def batch_classify(
        self,
        queries: List[str]
    ) -> List[IntentClassification]:
        """
        批量分类意图
        
        Args:
            queries (List[str]): 查询列表
            
        Returns:
            List[IntentClassification]: 分类结果列表
        """
        results = []
        for i, query in enumerate(queries):
            self.logger.info(f"处理第 {i+1}/{len(queries)} 个查询")
            result = self.classify_intent(query)
            results.append(result)
        return results
    
    def add_category(self, category: str):
        """
        添加意图分类
        
        Args:
            category (str): 新分类名称
        """
        if category not in self.categories:
            self.categories.append(category)
            self.logger.info(f"添加新分类: {category}")
    
    def remove_category(self, category: str):
        """
        移除意图分类
        
        Args:
            category (str): 要移除的分类名称
        """
        if category in self.categories:
            self.categories.remove(category)
            self.logger.info(f"移除分类: {category}")
    
    def get_categories(self) -> List[str]:
        """获取所有意图分类"""
        return self.categories.copy()
