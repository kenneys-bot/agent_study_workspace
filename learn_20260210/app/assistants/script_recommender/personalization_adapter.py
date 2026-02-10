"""
个性化适配器
根据客户特征和历史交互个性化话术
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import CustomerTypes

logger = logging.getLogger(__name__)


class CustomerProfile:
    """
    客户画像数据模型
    """
    
    def __init__(
        self,
        customer_id: str = None,
        name: str = None,
        age: int = None,
        gender: str = None,
        customer_type: str = CustomerTypes.REGULAR_CUSTOMER,
        risk_level: str = CustomerTypes.LOW_RISK,
        preference: Dict[str, Any] = None,
        history: List[Dict[str, Any]] = None
    ):
        self.customer_id = customer_id or ""       # 客户ID
        self.name = name or ""                      # 姓名
        self.age = age                              # 年龄
        self.gender = gender                        # 性别
        self.customer_type = customer_type          # 客户类型
        self.risk_level = risk_level                # 风险等级
        self.preference = preference or {}          # 偏好设置
        self.history = history or []                 # 历史交互记录
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "customer_id": self.customer_id,
            "name": self.name,
            "age": self.age,
            "gender": self.gender,
            "customer_type": self.customer_type,
            "risk_level": self.risk_level,
            "preference": self.preference,
            "history": self.history
        }


class PersonalizationAdapter(LoggerMixin):
    """
    个性化适配器
    根据客户特征和历史交互个性化话术
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化个性化适配器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
        self.logger.info("个性化适配器初始化完成")
    
    def adapt_script(
        self,
        script: str,
        customer_profile: CustomerProfile,
        context: "ConversationContext" = None
    ) -> str:
        """
        个性化适配话术
        
        Args:
            script (str): 原始话术
            customer_profile (CustomerProfile): 客户画像
            context (ConversationContext): 对话情境
            
        Returns:
            str: 个性化后的话术
        """
        try:
            # 基本个性化
            personalized = self._basic_personalize(script, customer_profile)
            
            # 高级个性化
            if customer_profile.age or customer_profile.gender:
                personalized = self._advanced_personalize(
                    personalized, customer_profile, context
                )
            
            return personalized
            
        except Exception as e:
            self.logger.error(f"话术个性化失败: {str(e)}")
            return script
    
    def adapt_script_batch(
        self,
        scripts: List[str],
        customer_profile: CustomerProfile,
        context: "ConversationContext" = None
    ) -> List[str]:
        """
        批量个性化话术
        
        Args:
            scripts (List[str]): 原始话术列表
            customer_profile (CustomerProfile): 客户画像
            context (ConversationContext): 对话情境
            
        Returns:
            List[str]: 个性化后的话术列表
        """
        return [
            self.adapt_script(script, customer_profile, context)
            for script in scripts
        ]
    
    def generate_personalized_greeting(
        self,
        customer_profile: CustomerProfile
    ) -> str:
        """
        生成个性化问候语
        
        Args:
            customer_profile (CustomerProfile): 客户画像
            
        Returns:
            str: 个性化问候语
        """
        try:
            name = customer_profile.name or "客户"
            
            prompt = f"""
请为以下客户生成个性化问候语：

客户姓名：{name}
客户类型：{customer_profile.customer_type}
客户性别：{customer_profile.gender or "未知"}
客户年龄：{customer_profile.age or "未知"}

要求：
1. 礼貌且专业
2. 体现对客户的尊重
3. 适合客服场景

个性化问候语：
            """
            
            response = self.llm_client.generate_text(prompt)
            return response.strip()
            
        except Exception as e:
            self.logger.error(f"个性化问候语生成失败: {str(e)}")
            return f"您好，{customer_profile.name or '客户'}！"
    
    def analyze_customer_preference(
        self,
        history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        分析客户偏好
        
        Args:
            history (List[Dict[str, Any]]): 历史交互记录
            
        Returns:
            Dict[str, Any]: 客户偏好
        """
        try:
            history_text = "\n".join([
                f"交互{turn.get('time', i+1)}: {turn.get('content', '')}"
                for i, turn in enumerate(history)
            ])
            
            prompt = f"""
请分析以下客户的历史交互记录，总结客户偏好：

{history_text}

请按照JSON格式输出偏好分析：
{{
    "preferred_channel": "偏好的沟通渠道",
    "communication_style": "偏好的沟通风格",
    "concerns": ["关注的方面"],
    "response_patterns": ["响应模式"]
}}
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析JSON结果
            import re
            import json
            
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            
            return {}
            
        except Exception as e:
            self.logger.error(f"客户偏好分析失败: {str(e)}")
            return {}
    
    def _basic_personalize(
        self,
        script: str,
        customer_profile: CustomerProfile
    ) -> str:
        """基本个性化"""
        personalized = script
        
        # 替换客户姓名
        if customer_profile.name:
            personalized = personalized.replace("{客户姓名}", customer_profile.name)
            personalized = personalized.replace("{name}", customer_profile.name)
        
        # 替换客户类型
        if customer_profile.customer_type:
            personalized = personalized.replace(
                "{客户类型}",
                customer_profile.customer_type
            )
        
        return personalized
    
    def _advanced_personalize(
        self,
        script: str,
        customer_profile: CustomerProfile,
        context: "ConversationContext" = None
    ) -> str:
        """高级个性化"""
        try:
            # 根据年龄调整语言风格
            age_adjustment = ""
            if customer_profile.age:
                if customer_profile.age < 30:
                    age_adjustment = "使用简洁、活力的话语"
                elif customer_customer_profile.age < 50:
                    age_adjustment = "使用专业、清晰的话语"
                else:
                    age_adjustment = "使用耐心、详细的话语"
            
            # 根据性别调整
            gender_adjustment = ""
            if customer_profile.gender:
                if customer_profile.gender == "男":
                    gender_adjustment = "使用直接、简洁的话语"
                elif customer_profile.gender == "女":
                    gender_adjustment = "使用温和、细致的话语"
            
            prompt = f"""
请根据客户特征个性化以下话术：

原始话术：
{script}

客户特征：
- 年龄：{customer_profile.age or "未知"}（{age_adjustment}）
- 性别：{customer_profile.gender or "未知"}（{gender_adjustment}）
- 客户类型：{customer_profile.customer_type}
- 风险等级：{customer_profile.risk_level}

请直接输出个性化后的话术：
            """
            
            response = self.llm_client.generate_text(prompt)
            return response.strip()
            
        except Exception as e:
            self.logger.warning(f"高级个性化失败: {str(e)}")
            return script
