"""
用户意图识别器
识别用户在当前对话中的具体意图
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates

logger = logging.getLogger(__name__)


class UserIntent:
    """
    用户意图数据模型
    """
    
    def __init__(
        self,
        intent_type: str,
        sub_intent: str = None,
        confidence: float = 0.0,
        required_info: List[str] = None,
        suggested_actions: List[str] = None
    ):
        self.intent_type = intent_type              # 意图类型
        self.sub_intent = sub_intent                # 子意图
        self.confidence = confidence                # 置信度
        self.required_info = required_info or []     # 需要的信息
        self.suggested_actions = suggested_actions or []  # 建议行动
        self.timestamp = datetime.now()              # 时间戳
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "intent_type": self.intent_type,
            "sub_intent": self.sub_intent,
            "confidence": self.confidence,
            "required_info": self.required_info,
            "suggested_actions": self.suggested_actions,
            "timestamp": self.timestamp.isoformat()
        }


class IntentRecognizer(LoggerMixin):
    """
    用户意图识别器
    识别用户在当前对话中的具体意图
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化意图识别器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
        self.logger.info("用户意图识别器初始化完成")
    
    def recognize_intent(
        self,
        current_query: str,
        context: "ConversationContext" = None
    ) -> UserIntent:
        """
        识别用户意图
        
        Args:
            current_query (str): 当前用户查询
            context (ConversationContext): 对话情境
            
        Returns:
            UserIntent: 用户意图识别结果
        """
        try:
            context_info = ""
            if context:
                context_info = f"""
当前对话情境：
- 主题：{context.topic}
- 阶段：{context.stage}
- 客户满意度：{context.customer_satisfaction}
- 关键点：{', '.join(context.key_points)}
            """
            
            prompt = f"""
请识别用户的当前意图：

当前用户查询：{current_query}
{context_info}

请按照以下JSON格式输出：
{{
    "intent_type": "意图类型",
    "sub_intent": "子意图",
    "confidence": 置信度（0-1）,
    "required_info": ["需要获取的信息"],
    "suggested_actions": ["建议的客服行动"]
}}
            """
            
            response = self.llm_client.generate_text(prompt)
            result = self._parse_json_response(response)
            
            return UserIntent(
                intent_type=result.get("intent_type", "未知"),
                sub_intent=result.get("sub_intent"),
                confidence=result.get("confidence", 0.0),
                required_info=result.get("required_info", []),
                suggested_actions=result.get("suggested_actions", [])
            )
            
        except Exception as e:
            self.logger.error(f"意图识别失败: {str(e)}")
            return UserIntent(
                intent_type="未知",
                confidence=0.0
            )
    
    def predict_next_intent(
        self,
        conversation_history: List[Dict[str, str]],
        max_predictions: int = 3
    ) -> List[UserIntent]:
        """
        预测用户下一步意图
        
        Args:
            conversation_history (List[Dict[str, str]]): 对话历史
            max_predictions (int): 最大预测数量
            
        Returns:
            List[UserIntent]: 预测的意图列表
        """
        try:
            conversation_text = "\n".join([
                f"{turn.get('role', '未知')}: {turn.get('content', '')}"
                for turn in conversation_history
            ])
            
            prompt = f"""
请根据以下对话历史预测用户下一步可能的意图：

对话历史：
{conversation_text}

请预测{max_predictions}个最可能的用户意图，并按照JSON数组格式输出：
[
    {{
        "intent_type": "意图1",
        "confidence": 置信度,
        "reason": "预测理由"
    }}
]
            """
            
            response = self.llm_client.generate_text(prompt)
            results = self._parse_json_array_response(response)
            
            intents = []
            for result in results[:max_predictions]:
                intents.append(UserIntent(
                    intent_type=result.get("intent_type", "未知"),
                    confidence=result.get("confidence", 0.0)
                ))
            
            return intents
            
        except Exception as e:
            self.logger.error(f"意图预测失败: {str(e)}")
            return []
    
    def analyze_intent_history(
        self,
        conversation_history: List[Dict[str, str]]
    ) -> List[UserIntent]:
        """
        分析对话历史中的所有意图
        
        Args:
            conversation_history (List[Dict[str, str]]): 对话历史
            
        Returns:
            List[UserIntent]: 意图历史列表
        """
        intents = []
        
        for i, turn in enumerate(conversation_history):
            if turn.get("role") == "customer":
                intent = self.recognize_intent(turn.get("content", ""))
                intents.append(intent)
        
        return intents
    
    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """解析JSON响应"""
        import re
        import json
        
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {}
    
    def _parse_json_array_response(self, response: str) -> List[Dict[str, Any]]:
        """解析JSON数组响应"""
        import re
        import json
        
        array_match = re.search(r'\[[\s\S]*\]', response)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass
        return []
