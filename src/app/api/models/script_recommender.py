"""
话术推荐API数据模型
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class ConversationMessage(BaseModel):
    """对话消息"""
    role: str = Field(..., description="角色: customer/agent")
    content: str = Field(..., description="消息内容")


class AnalyzeContextRequest(BaseModel):
    """情境分析请求"""
    conversation_history: List[ConversationMessage] = Field(..., description="对话历史")
    session_id: Optional[str] = Field(None, description="会话ID")


class AnalyzeContextResponse(BaseModel):
    """情境分析响应"""
    topic: str = Field(..., description="对话主题")
    stage: str = Field(..., description="对话阶段")
    complexity: str = Field("中等", description="复杂度")
    customer_satisfaction: float = Field(..., description="客户满意度")
    key_points: List[str] = Field(default_factory=list, description="关键点")
    emotion: str = Field("中立", description="客户情绪")


class RecognizeIntentRequest(BaseModel):
    """意图识别请求"""
    current_query: str = Field(..., description="当前用户查询")
    context: Optional[Dict[str, Any]] = Field(None, description="对话情境")


class RecognizeIntentResponse(BaseModel):
    """意图识别响应"""
    intent_type: str = Field(..., description="意图类型")
    sub_intent: Optional[str] = Field(None, description="子意图")
    confidence: float = Field(..., description="置信度")
    required_info: List[str] = Field(default_factory=list, description="需要的信息")
    suggested_actions: List[str] = Field(default_factory=list, description="建议行动")


class RecommendScriptsRequest(BaseModel):
    """话术推荐请求"""
    context: Dict[str, Any] = Field(..., description="对话情境")
    intent: Dict[str, Any] = Field(..., description="用户意图")
    count: int = Field(3, description="推荐数量")
    filters: Optional[Dict[str, Any]] = Field(None, description="过滤条件")


class RecommendedScriptItem(BaseModel):
    """推荐话术项"""
    script_id: str = Field(..., description="话术ID")
    content: str = Field(..., description="话术内容")
    title: Optional[str] = Field(None, description="话术标题")
    relevance_score: float = Field(..., description="相关性分数")
    usage_count: int = Field(0, description="使用次数")
    success_rate: float = Field(0.0, description="成功率")


class RecommendScriptsResponse(BaseModel):
    """话术推荐响应"""
    scripts: List[RecommendedScriptItem] = Field(..., description="推荐的话术列表")
    context_summary: str = Field(..., description="情境摘要")
    intent_summary: str = Field(..., description="意图摘要")


class PersonalizeScriptRequest(BaseModel):
    """个性化适配请求"""
    script: str = Field(..., description="原始话术内容")
    customer_profile: Dict[str, Any] = Field(..., description="客户画像")
    context: Optional[Dict[str, Any]] = Field(None, description="对话情境")


class PersonalizeScriptResponse(BaseModel):
    """个性化适配响应"""
    personalized_script: str = Field(..., description="个性化后的话术")
    original_script: str = Field(..., description="原始话术")
    customer_id: Optional[str] = Field(None, description="客户ID")


class CustomerProfileRequest(BaseModel):
    """客户画像请求"""
    customer_id: str = Field(..., description="客户ID")
    name: Optional[str] = Field(None, description="客户姓名")
    age: Optional[int] = Field(None, description="年龄")
    gender: Optional[str] = Field(None, description="性别")
    customer_type: Optional[str] = Field(None, description="客户类型")
    risk_level: Optional[str] = Field(None, description="风险等级")
    preference: Optional[Dict[str, Any]] = Field(None, description="偏好设置")


class CustomerProfileResponse(BaseModel):
    """客户画像响应"""
    customer_id: str = Field(..., description="客户ID")
    profile: Dict[str, Any] = Field(..., description="客户画像详情")
    interaction_count: int = Field(0, description="交互次数")
    last_updated: Optional[str] = Field(None, description="最后更新时间")
