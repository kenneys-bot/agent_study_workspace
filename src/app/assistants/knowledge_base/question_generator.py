"""
标准问/相似问生成器
自动生成标准问题和相似问题
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from core.vector_db import VectorDBClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, QuestionTypes

logger = logging.getLogger(__name__)


class QuestionValidation:
    """
    问题验证结果数据模型
    """
    
    def __init__(
        self,
        question: str,
        is_valid: bool = True,
        reason: str = None,
        score: float = 1.0
    ):
        self.question = question      # 问题内容
        self.is_valid = is_valid      # 是否有效
        self.reason = reason          # 无效原因
        self.score = score            # 质量评分
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "question": self.question,
            "is_valid": self.is_valid,
            "reason": self.reason,
            "score": self.score
        }


class QuestionGenerator(LoggerMixin):
    """
    标准问/相似问生成器
    自动生成标准问题和相似问题
    """
    
    def __init__(
        self,
        llm_client: LLMClient,
        vector_db: VectorDBClient = None
    ):
        """
        初始化问题生成器
        
        Args:
            llm_client (LLMClient): 大模型客户端
            vector_db (VectorDBClient): 向量数据库客户端
        """
        self.llm_client = llm_client
        self.vector_db = vector_db
        self.logger.info("问题生成器初始化完成")
    
    def generate_standard_questions(
        self,
        topic: str,
        count: int = 5,
        category: str = None
    ) -> List[str]:
        """
        生成标准问题
        
        Args:
            topic (str): 主题
            count (int): 生成数量
            category (str): 分类
            
        Returns:
            List[str]: 标准问题列表
        """
        try:
            prompt = PromptTemplates.QUESTION_GENERATE.format(
                topic=topic,
                count=count
            )
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析返回结果
            questions = self._parse_questions(response)
            
            self.logger.info(f"生成了 {len(questions)} 个标准问题")
            return questions
            
        except Exception as e:
            self.logger.error(f"标准问题生成失败: {str(e)}")
            return []
    
    def generate_similar_questions(
        self,
        question: str,
        count: int = 5,
        category: str = None
    ) -> List[str]:
        """
        生成相似问题
        
        Args:
            question (str): 原始问题
            count (int): 生成数量
            category (str): 分类
            
        Returns:
            List[str]: 相似问题列表
        """
        try:
            prompt = f"""
请根据以下问题生成{count}个相似问题，保持相同的语义：

原始问题：{question}

要求：
1. 相似问题应表达相同的意图
2. 使用不同的表达方式
3. 保持问题的简洁性

相似问题：
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析返回结果
            questions = self._parse_questions(response)
            
            # 如果有向量数据库，可以进行相似性验证
            if self.vector_db:
                questions = self._filter_similar_questions(
                    question, questions, count
                )
            
            self.logger.info(f"生成了 {len(questions)} 个相似问题")
            return questions
            
        except Exception as e:
            self.logger.error(f"相似问题生成失败: {str(e)}")
            return []
    
    def generate_faq_questions(
        self,
        topic: str,
        count: int = 10
    ) -> List[str]:
        """
        生成FAQ问题
        
        Args:
            topic (str): 主题
            count (int): 生成数量
            
        Returns:
            List[str]: FAQ问题列表
        """
        try:
            prompt = f"""
请生成关于"{topic}"的{count}个常见问题（FAQ）。

要求：
1. 问题应覆盖该主题的各个方面
2. 问题应简洁明了
3. 问题应具有代表性

FAQ问题列表：
            """
            
            response = self.llm_client.generate_text(prompt)
            questions = self._parse_questions(response)
            
            self.logger.info(f"生成了 {len(questions)} 个FAQ问题")
            return questions
            
        except Exception as e:
            self.logger.error(f"FAQ问题生成失败: {str(e)}")
            return []
    
    def validate_questions(
        self,
        questions: List[str],
        topic: str = None
    ) -> List[QuestionValidation]:
        """
        验证问题质量
        
        Args:
            questions (List[str]): 问题列表
            topic (str): 主题
            
        Returns:
            List[QuestionValidation]: 验证结果列表
        """
        validations = []
        
        for question in questions:
            validation = QuestionValidation(question=question)
            
            # 基本验证
            if not question or len(question.strip()) == 0:
                validation.is_valid = False
                validation.reason = "问题为空"
                validation.score = 0.0
            elif len(question) > 200:
                validation.is_valid = False
                validation.reason = "问题过长"
                validation.score = 0.5
            
            # 使用LLM进行质量评估
            try:
                prompt = f"""
请评估以下问题的质量（0-1分）：

问题：{question}
主题：{topic or "未指定"}

评估维度：
1. 清晰度 - 问题表达是否清晰明确
2. 完整性 - 问题是否包含必要信息
3. 简洁性 - 问题是否简洁

请直接输出评分（0-1之间的数字）：
                """
                
                response = self.llm_client.generate_text(prompt)
                score = self._parse_score(response)
                
                if score is not None:
                    validation.score = score
                    if score < 0.6:
                        validation.is_valid = False
                        validation.reason = f"质量评分过低 ({score:.2f})"
                        
            except Exception as e:
                self.logger.warning(f"问题质量评估失败: {str(e)}")
            
            validations.append(validation)
        
        self.logger.info(f"验证了 {len(validations)} 个问题")
        return validations
    
    def _parse_questions(self, response: str) -> List[str]:
        """解析问题列表"""
        questions = []
        lines = response.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 去除序号
            if line[0].isdigit() and '.' in line:
                line = line.split('.', 1)[1].strip()
            elif line[0] == '-' or line[0] == '*':
                line = line[1:].strip()
            
            if line:
                questions.append(line)
        
        return questions
    
    def _parse_score(self, response: str) -> Optional[float]:
        """解析评分"""
        import re
        numbers = re.findall(r'0\.\d+|\d+\.\d+|\d+', response)
        if numbers:
            try:
                score = float(numbers[0])
                if 0 <= score <= 1:
                    return score
            except ValueError:
                pass
        return None
    
    def _filter_similar_questions(
        self,
        original: str,
        questions: List[str],
        count: int
    ) -> List[str]:
        """过滤相似问题"""
        try:
            results = self.vector_db.query_with_score(
                query_text=original,
                n_results=count + 5
            )
            
            # 获取与原问题不太相似的问题
            filtered = []
            for result in results:
                if result.get("distance", 1.0) < 0.9:  # 相似度阈值
                    if result["document"] not in filtered:
                        filtered.append(result["document"])
            
            return filtered[:count]
            
        except Exception as e:
            self.logger.warning(f"相似问题过滤失败: {str(e)}")
            return questions
