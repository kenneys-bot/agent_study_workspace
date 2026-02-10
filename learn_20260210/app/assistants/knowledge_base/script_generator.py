"""
话术生成器
生成电话话术和电催话术
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, ScriptTypes, CustomerTypes

logger = logging.getLogger(__name__)


class CallScript:
    """
    电话话术数据模型
    """
    
    def __init__(
        self,
        greeting: str,
        main_content: str,
        closing: str,
        scenario: str,
        customer_type: str = None
    ):
        self.greeting = greeting          # 问候语
        self.main_content = main_content  # 主要内容
        self.closing = closing            # 结束语
        self.scenario = scenario          # 适用场景
        self.customer_type = customer_type  # 适用客户类型
        self.timestamp = datetime.now()   # 创建时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "greeting": self.greeting,
            "main_content": self.main_content,
            "closing": self.closing,
            "scenario": self.scenario,
            "customer_type": self.customer_type,
            "timestamp": self.timestamp.isoformat()
        }
    
    def format_full_script(self) -> str:
        """格式化完整话术"""
        return f"""
{greeting}
{main_content}
{closing}
        """.strip()


class CollectionScript:
    """
    电催话术数据模型
    """
    
    def __init__(
        self,
        opening: str,
        negotiation: str,
        commitment_request: str,
        risk_level: str = None,
        overdue_days: int = None
    ):
        self.opening = opening                    # 开场白
        self.negotiation = negotiation            # 协商内容
        self.commitment_request = commitment_request  # 承诺请求
        self.risk_level = risk_level              # 风险等级
        self.overdue_days = overdue_days          # 逾期天数
        self.timestamp = datetime.now()           # 创建时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "opening": self.opening,
            "negotiation": self.negotiation,
            "commitment_request": self.commitment_request,
            "risk_level": self.risk_level,
            "overdue_days": self.overdue_days,
            "timestamp": self.timestamp.isoformat()
        }
    
    def format_full_script(self) -> str:
        """格式化完整话术"""
        return f"""
{opening}
{negotiation}
{commitment_request}
        """.strip()


class ScriptGenerator(LoggerMixin):
    """
    话术生成器
    生成电话话术和电催话术
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化话术生成器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
        self.logger.info("话术生成器初始化完成")
    
    def generate_call_scripts(
        self,
        scenario: str,
        customer_type: str = CustomerTypes.REGULAR_CUSTOMER,
        count: int = 3,
        tone: str = "professional"
    ) -> List[CallScript]:
        """
        生成电话话术
        
        Args:
            scenario (str): 场景描述
            customer_type (str): 客户类型
            count (int): 生成数量
            tone (str): 话术风格
            
        Returns:
            List[CallScript]: 电话话术列表
        """
        try:
            prompt = PromptTemplates.SCRIPT_GENERATE.format(
                scenario=scenario,
                customer_type=customer_type
            )
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析返回结果
            scripts = self._parse_call_scripts(
                response, scenario, customer_type
            )
            
            self.logger.info(f"生成了 {len(scripts)} 个电话话术")
            return scripts
            
        except Exception as e:
            self.logger.error(f"电话话术生成失败: {str(e)}")
            return []
    
    def generate_collection_scripts(
        self,
        overdue_days: int,
        customer_risk: str = CustomerTypes.LOW_RISK,
        count: int = 3
    ) -> List[CollectionScript]:
        """
        生成电催话术
        
        Args:
            overdue_days (int): 逾期天数
            customer_risk (str): 客户风险等级
            count (int): 生成数量
            
        Returns:
            List[CollectionScript]: 电催话术列表
        """
        try:
            prompt = f"""
请根据以下信息生成电催话术：

逾期天数：{overdue_days}天
客户风险等级：{customer_risk}

要求：
1. 开场白应礼貌且专业
2. 协商内容应清晰说明还款要求
3. 承诺请求应明确还款期限

请生成{count}个不同风格的话术：
            """
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析返回结果
            scripts = self._parse_collection_scripts(
                response, overdue_days, customer_risk
            )
            
            self.logger.info(f"生成了 {len(scripts)} 个电催话术")
            return scripts
            
        except Exception as e:
            self.logger.error(f"电催话术生成失败: {str(e)}")
            return []
    
    def generate_complaint_scripts(
        self,
        complaint_type: str,
        customer_emotion: str,
        count: int = 3
    ) -> List[CallScript]:
        """
        生成投诉处理话术
        
        Args:
            complaint_type (str): 投诉类型
            customer_emotion (str): 客户情绪
            count (int): 生成数量
            
        Returns:
            List[CallScript]: 话术列表
        """
        try:
            prompt = f"""
请生成针对以下投诉的处理话术：

投诉类型：{complaint_type}
客户情绪：{customer_emotion}

要求：
1. 表达歉意和理解
2. 说明解决方案
3. 保持专业和耐心

请生成{count}个话术：
            """
            
            response = self.llm_client.generate_text(prompt)
            scripts = self._parse_call_scripts(
                response, complaint_type, customer_emotion
            )
            
            self.logger.info(f"生成了 {len(scripts)} 个投诉处理话术")
            return scripts
            
        except Exception as e:
            self.logger.error(f"投诉处理话术生成失败: {str(e)}")
            return []
    
    def personalize_script(
        self,
        script: str,
        customer_name: str = None,
        customer_info: Dict[str, Any] = None
    ) -> str:
        """
        个性化话术
        
        Args:
            script (str): 原始话术
            customer_name (str): 客户姓名
            customer_info (Dict[str, Any]): 客户信息
            
        Returns:
            str: 个性化后的话术
        """
        try:
            name = customer_name or customer_info.get("name", "客户")
            
            personalized = script.replace("{客户姓名}", name)
            personalized = personalized.replace("{客户}", name)
            
            # 根据客户信息进一步个性化
            if customer_info:
                for key, value in customer_info.items():
                    personalized = personalized.replace(
                        f"{{{key}}}", str(value)
                    )
            
            return personalized
            
        except Exception as e:
            self.logger.error(f"话术个性化失败: {str(e)}")
            return script
    
    def _parse_call_scripts(
        self,
        response: str,
        scenario: str,
        customer_type: str
    ) -> List[CallScript]:
        """解析电话话术"""
        scripts = []
        lines = response.strip().split('\n\n')
        
        for block in lines:
            if not block.strip():
                continue
            
            parts = block.split('\n')
            greeting = ""
            main_content = ""
            closing = ""
            
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                if '问候' in part or '开场' in part or part.startswith('1.'):
                    greeting = part.split(':', 1)[1].strip() if ':' in part else part
                elif '主要' in part or '内容' in part or part.startswith('2.'):
                    main_content = part.split(':', 1)[1].strip() if ':' in part else part
                elif '结束' in part or '结尾' in part or part.startswith('3.'):
                    closing = part.split(':', 1)[1].strip() if ':' in part else part
            
            if greeting or main_content:
                scripts.append(CallScript(
                    greeting=greeting,
                    main_content=main_content,
                    closing=closing,
                    scenario=scenario,
                    customer_type=customer_type
                ))
        
        return scripts
    
    def _parse_collection_scripts(
        self,
        response: str,
        overdue_days: int,
        risk_level: str
    ) -> List[CollectionScript]:
        """解析电催话术"""
        scripts = []
        blocks = response.strip().split('\n\n')
        
        for block in blocks:
            if not block.strip():
                continue
            
            parts = block.split('\n')
            opening = ""
            negotiation = ""
            commitment_request = ""
            
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                if '开场' in part or '开场白' in part or part.startswith('1.'):
                    opening = part.split(':', 1)[1].strip() if ':' in part else part
                elif '协商' in part or '说明' in part or part.startswith('2.'):
                    negotiation = part.split(':', 1)[1].strip() if ':' in part else part
                elif '承诺' in part or '请求' in part or part.startswith('3.'):
                    commitment_request = part.split(':', 1)[1].strip() if ':' in part else part
            
            if opening or negotiation:
                scripts.append(CollectionScript(
                    opening=opening,
                    negotiation=negotiation,
                    commitment_request=commitment_request,
                    risk_level=risk_level,
                    overdue_days=overdue_days
                ))
        
        return scripts
