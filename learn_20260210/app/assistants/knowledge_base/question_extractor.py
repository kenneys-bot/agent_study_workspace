"""
客户问题抽取器
从客户对话中自动抽取关键问题
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, CustomerEmotions

logger = logging.getLogger(__name__)


class ExtractedQuestion:
    """
    抽取的问题数据模型
    """
    
    def __init__(
        self,
        question: str,
        context: str = "",
        emotion: str = CustomerEmotions.NEUTRAL,
        priority: int = 1
    ):
        self.question = question          # 问题内容
        self.context = context            # 问题上下文
        self.emotion = emotion            # 客户情绪
        self.priority = priority          # 问题优先级
        self.timestamp = datetime.now()    # 时间戳
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "question": self.question,
            "context": self.context,
            "emotion": self.emotion,
            "priority": self.priority,
            "timestamp": self.timestamp.isoformat()
        }


class QuestionExtractor(LoggerMixin):
    """
    客户问题抽取器
    从客户对话中自动抽取关键问题
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化问题抽取器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
        self.logger.info("问题抽取器初始化完成")
    
    def extract_questions(
        self,
        conversation: str,
        max_questions: int = 5
    ) -> List[ExtractedQuestion]:
        """
        从对话中抽取问题
        
        Args:
            conversation (str): 客户对话内容
            max_questions (int): 最大抽取问题数量
            
        Returns:
            List[ExtractedQuestion]: 抽取的问题列表
        """
        try:
            prompt = PromptTemplates.KNOWLEDGE_BASE_EXTRACT.format(
                conversation=conversation
            )
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析返回结果
            questions = self._parse_extraction_response(response)
            
            self.logger.info(f"从对话中抽取了 {len(questions)} 个问题")
            return questions
            
        except Exception as e:
            self.logger.error(f"问题抽取失败: {str(e)}")
            return []
    
    def extract_key_info(self, conversation: str) -> Dict[str, Any]:
        """
        抽取关键信息
        
        Args:
            conversation (str): 客户对话内容
            
        Returns:
            Dict[str, Any]: 关键信息字典
        """
        try:
            prompt = f"""
请从以下客户对话中提取关键信息：

对话内容：
{conversation}

请按照以下JSON格式输出：
{{
    "customer_id": "客户ID（如果有）",
    "product": "涉及的产品类型",
    "main_topic": "主要话题",
    "key_entities": ["关键实体列表"],
    "emotion": "客户情绪"
}}
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析JSON结果
            return self._parse_json_response(response)
            
        except Exception as e:
            self.logger.error(f"关键信息提取失败: {str(e)}")
            return {}
    
    def batch_extract(
        self,
        conversations: List[str],
        max_questions: int = 5
    ) -> List[List[ExtractedQuestion]]:
        """
        批量处理对话
        
        Args:
            conversations (List[str]): 对话列表
            max_questions (int): 最大抽取问题数量
            
        Returns:
            List[List[ExtractedQuestion]]: 抽取的问题列表
        """
        results = []
        for i, conversation in enumerate(conversations):
            self.logger.info(f"处理第 {i+1}/{len(conversations)} 个对话")
            questions = self.extract_questions(conversation, max_questions)
            results.append(questions)
        return results
    
    def _parse_extraction_response(
        self,
        response: str
    ) -> List[ExtractedQuestion]:
        """解析抽取响应"""
        questions = []
        lines = response.strip().split('\n')
        
        current_question = None
        current_context = ""
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('1.') or line.startswith('主要问题'):
                if current_question:
                    questions.append(ExtractedQuestion(
                        question=current_question,
                        context=current_context
                    ))
                current_question = line.split(':', 1)[1].strip() if ':' in line else line
                current_context = ""
            elif line.startswith('2.') or line.startswith('相关信息'):
                current_context = line.split(':', 1)[1].strip() if ':' in line else ""
            elif line.startswith('3.') or line.startswith('客户情绪'):
                emotion = line.split(':', 1)[1].strip() if ':' in line else ""
                if current_question:
                    questions.append(ExtractedQuestion(
                        question=current_question,
                        context=current_context,
                        emotion=emotion
                    ))
                    current_question = None
                    current_context = ""
        
        # 添加最后一个问题
        if current_question:
            questions.append(ExtractedQuestion(
                question=current_question,
                context=current_context
            ))
        
        return questions
    
    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """解析JSON响应"""
        import re
        import json as json_lib
        
        # 提取JSON内容
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json_lib.loads(json_match.group())
            except json_lib.JSONDecodeError:
                pass
        return {}
