"""
对话情境分析器
实时分析对话情境和上下文
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, ConversationStages, CustomerEmotions

logger = logging.getLogger(__name__)


class ConversationContext:
    """
    对话情境数据模型
    """
    
    def __init__(
        self,
        topic: str,
        stage: str,
        complexity: str = "中等",
        customer_satisfaction: float = 0.5,
        key_points: List[str] = None,
        emotion: str = CustomerEmotions.NEUTRAL
    ):
        self.topic = topic                          # 对话主题
        self.stage = stage                          # 对话阶段
        self.complexity = complexity                # 复杂度
        self.customer_satisfaction = customer_satisfaction  # 客户满意度
        self.key_points = key_points or []          # 关键点
        self.emotion = emotion                      # 客户情绪
        self.timestamp = datetime.now()              # 时间戳
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "topic": self.topic,
            "stage": self.stage,
            "complexity": self.complexity,
            "customer_satisfaction": self.customer_satisfaction,
            "key_points": self.key_points,
            "emotion": self.emotion,
            "timestamp": self.timestamp.isoformat()
        }


class EmotionAnalysis:
    """
    情绪分析结果数据模型
    """
    
    def __init__(
        self,
        emotion: str,
        confidence: float = 0.0,
        intensity: float = 0.5,
        emotional_traits: List[str] = None
    ):
        self.emotion = emotion                      # 情绪类型
        self.confidence = confidence                # 置信度
        self.intensity = intensity                  # 情绪强度
        self.emotional_traits = emotional_traits or []  # 情绪特征
        self.timestamp = datetime.now()              # 时间戳
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "emotion": self.emotion,
            "confidence": self.confidence,
            "intensity": self.intensity,
            "emotional_traits": self.emotional_traits,
            "timestamp": self.timestamp.isoformat()
        }


class ContextAnalyzer(LoggerMixin):
    """
    对话情境分析器
    实时分析对话情境和上下文
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化情境分析器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
        self.logger.info("对话情境分析器初始化完成")
    
    def analyze_context(
        self,
        conversation_history: List[Dict[str, str]]
    ) -> ConversationContext:
        """
        分析对话情境
        
        Args:
            conversation_history (List[Dict[str, str]]): 对话历史
            
        Returns:
            ConversationContext: 对话情境分析结果
        """
        try:
            # 将对话历史转换为文本
            conversation_text = self._format_conversation(conversation_history)
            
            prompt = PromptTemplates.CONTEXT_ANALYZE.format(
                conversation_history=conversation_text
            )
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析返回结果
            context = self._parse_context_response(response)
            
            self.logger.info(f"对话情境分析完成: 主题={context.topic}, 阶段={context.stage}")
            return context
            
        except Exception as e:
            self.logger.error(f"对话情境分析失败: {str(e)}")
            return ConversationContext(
                topic="未知",
                stage=ConversationStages.MAIN_TOPIC
            )
    
    def extract_emotion(self, text: str) -> EmotionAnalysis:
        """
        提取文本情绪
        
        Args:
            text (str): 文本内容
            
        Returns:
            EmotionAnalysis: 情绪分析结果
        """
        try:
            prompt = f"""
请分析以下文本的情绪：

文本：{text}

请按照以下JSON格式输出：
{{
    "emotion": "情绪类型（积极/中立/消极/愤怒/焦虑）",
    "confidence": 置信度（0-1）,
    "intensity": 情绪强度（0-1）,
    "emotional_traits": ["情绪特征列表"]
}}
            """
            
            response = self.llm_client.generate_text(prompt)
            result = self._parse_json_response(response)
            
            return EmotionAnalysis(
                emotion=result.get("emotion", CustomerEmotions.NEUTRAL),
                confidence=result.get("confidence", 0.0),
                intensity=result.get("intensity", 0.5),
                emotional_traits=result.get("emotional_traits", [])
            )
            
        except Exception as e:
            self.logger.error(f"情绪分析失败: {str(e)}")
            return EmotionAnalysis(emotion=CustomerEmotions.NEUTRAL)
    
    def identify_key_points(self, conversation: str) -> List[str]:
        """
        识别对话关键点
        
        Args:
            conversation (str): 对话内容
            
        Returns:
            List[str]: 关键点列表
        """
        try:
            prompt = f"""
请从以下对话中识别关键点：

对话内容：
{conversation}

请列出5个最重要的关键点：
1.
2.
3.
4.
5.
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析关键点
            key_points = []
            lines = response.strip().split('\n')
            for line in lines:
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith('-')):
                    if '.' in line:
                        key_points.append(line.split('.', 1)[1].strip())
                    else:
                        key_points.append(line)
            
            return key_points
            
        except Exception as e:
            self.logger.error(f"关键点识别失败: {str(e)}")
            return []
    
    def detect_stage(self, conversation: str) -> str:
        """
        检测对话阶段
        
        Args:
            conversation (str): 对话内容
            
        Returns:
            str: 对话阶段
        """
        try:
            prompt = f"""
请判断以下对话所处的阶段：

对话内容：
{conversation}

可选阶段：
1. {ConversationStages.GREETING} - 刚开始对话，正在问候
2. {ConversationStages.IDENTIFICATION} - 正在确认身份
3. {ConversationStages.MAIN_TOPIC} - 正在进行主要话题交流
4. {ConversationStages.PROBLEM_SOLVING} - 正在解决问题
5. {ConversationStages.CLOSING} - 对话即将结束
6. {ConversationStages.FOLLOW_UP} - 需要后续跟进

请直接输出阶段名称：
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 匹配阶段
            for stage in ConversationStages.__dict__.values():
                if isinstance(stage, str) and stage in response:
                    return stage
            
            return ConversationStages.MAIN_TOPIC
            
        except Exception as e:
            self.logger.error(f"对话阶段检测失败: {str(e)}")
            return ConversationStages.MAIN_TOPIC
    
    def _format_conversation(
        self,
        conversation_history: List[Dict[str, str]]
    ) -> str:
        """格式化对话历史"""
        lines = []
        for turn in conversation_history:
            role = turn.get("role", "未知")
            content = turn.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
    
    def _parse_context_response(
        self,
        response: str
    ) -> ConversationContext:
        """解析情境分析响应"""
        import re
        
        topic = "未知"
        stage = ConversationStages.MAIN_TOPIC
        satisfaction = 0.5
        key_points = []
        
        # 提取主题
        topic_match = re.search(r'主题[：:]\s*(.+)', response)
        if topic_match:
            topic = topic_match.group(1).strip()
        
        # 提取阶段
        stage_match = re.search(r'阶段[：:]\s*(.+)', response)
        if stage_match:
            stage_text = stage_match.group(1).strip()
            for s in ConversationStages.__dict__.values():
                if isinstance(s, str) and s in stage_text:
                    stage = s
                    break
        
        # 提取满意度
        sat_match = re.search(r'满意度[：:]\s*([\d.]+)', response)
        if sat_match:
            try:
                satisfaction = float(sat_match.group(1))
            except ValueError:
                pass
        
        # 提取关键点
        points_match = re.search(r'关键点[：:]([\s\S]+)', response)
        if points_match:
            points_text = points_match.group(1)
            points = re.findall(r'\d+\.\s*(.+)', points_text)
            if points:
                key_points = points
        
        return ConversationContext(
            topic=topic,
            stage=stage,
            customer_satisfaction=satisfaction,
            key_points=key_points
        )
    
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
