"""
标准问/相似问生成器
自动生成标准问题和相似问题
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from bank_assistant.core.llm_client import LLMClient
from bank_assistant.core.vector_db import VectorDBClient, QueryError
from bank_assistant.utils.logger import LoggerMixin
from bank_assistant.config.constants import PromptTemplates, QuestionTypes

logger = logging.getLogger(__name__)

# 新生成问题与知识库最近邻的距离低于该阈值时，视为知识库已覆盖该问题。
SIMILAR_QUESTION_DUPLICATE_DISTANCE = 0.9


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
            
            # 如果有向量数据库，校验新生成的问题是否已被知识库覆盖
            if self.vector_db:
                questions = self._filter_similar_questions(
                    questions, count
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
        questions: List[str],
        count: int
    ) -> List[str]:
        """校验新生成的问题，剔除知识库中已覆盖的重复问题。

        逐个用新生成的问题查询知识库：最近邻距离低于阈值表示该问题已存在，
        应过滤掉；知识库为空时没有可比较的对象，保留全部新问题。
        """
        filtered = []
        seen = set()

        for question in questions:
            question = question.strip()
            if not question or question in seen:
                continue
            seen.add(question)

            neighbors = self.vector_db.query_with_score(
                query_text=question,
                n_results=1
            )
            if self._is_covered_by_knowledge_base(neighbors):
                continue

            filtered.append(question)
            if len(filtered) >= count:
                break

        return filtered

    @staticmethod
    def _is_covered_by_knowledge_base(neighbors: List[Dict[str, Any]]) -> bool:
        """判断最近邻是否已覆盖该问题；缺少距离分数时显式报错。"""
        if not neighbors:
            return False

        distance = neighbors[0].get("distance")
        if distance is None:
            raise QueryError("相似问校验缺少距离分数，无法判断是否重复")

        return distance < SIMILAR_QUESTION_DUPLICATE_DISTANCE
