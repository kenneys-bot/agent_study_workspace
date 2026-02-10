"""
知识库助手API数据模型
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class ExtractQuestionsRequest(BaseModel):
    """问题抽取请求"""
    conversation: str = Field(..., description="客户对话内容")
    session_id: Optional[str] = Field(None, description="会话ID")
    max_questions: int = Field(5, description="最大抽取问题数量")


class ExtractedQuestionItem(BaseModel):
    """抽取的问题项"""
    question: str = Field(..., description="问题内容")
    context: str = Field("", description="问题上下文")
    emotion: str = Field("中立", description="客户情绪")
    priority: int = Field(1, description="问题优先级")


class ExtractQuestionsResponse(BaseModel):
    """问题抽取响应"""
    questions: List[ExtractedQuestionItem] = Field(..., description="抽取的问题列表")
    key_info: Dict[str, Any] = Field(default_factory=dict, description="关键信息")
    session_id: Optional[str] = Field(None, description="会话ID")


class ClassifyIntentRequest(BaseModel):
    """意图识别请求"""
    query: str = Field(..., description="客户查询内容")
    categories: Optional[List[str]] = Field(None, description="意图分类列表")


class ClassifyIntentResponse(BaseModel):
    """意图识别响应"""
    primary_intent: str = Field(..., description="主要意图")
    secondary_intent: Optional[str] = Field(None, description="次要意图")
    confidence: float = Field(..., description="置信度")
    rewritten_query: Optional[str] = Field(None, description="改写后的查询")


class GenerateQuestionsRequest(BaseModel):
    """问题生成请求"""
    topic: str = Field(..., description="主题")
    question_type: str = Field("standard", description="问题类型: standard/similar/faq")
    count: int = Field(5, description="生成数量")
    category: Optional[str] = Field(None, description="分类")


class QuestionValidationItem(BaseModel):
    """问题验证项"""
    question: str = Field(..., description="问题内容")
    is_valid: bool = Field(..., description="是否有效")
    score: float = Field(..., description="质量评分")
    reason: Optional[str] = Field(None, description="无效原因")


class GenerateQuestionsResponse(BaseModel):
    """问题生成响应"""
    questions: List[str] = Field(..., description="生成的问题列表")
    validations: List[QuestionValidationItem] = Field(default_factory=list, description="验证结果")
    topic: str = Field(..., description="主题")


class GenerateScriptsRequest(BaseModel):
    """话术生成请求"""
    script_type: str = Field(..., description="话术类型: call/collection/complaint")
    parameters: Dict[str, Any] = Field(..., description="生成参数")
    count: int = Field(3, description="生成数量")
    tone: Optional[str] = Field("professional", description="话术风格")


class CallScriptItem(BaseModel):
    """电话话术项"""
    greeting: str = Field(..., description="问候语")
    main_content: str = Field(..., description="主要内容")
    closing: str = Field(..., description="结束语")
    scenario: str = Field(..., description="适用场景")


class CollectionScriptItem(BaseModel):
    """电催话术项"""
    opening: str = Field(..., description="开场白")
    negotiation: str = Field(..., description="协商内容")
    commitment_request: str = Field(..., description="承诺请求")
    risk_level: Optional[str] = Field(None, description="风险等级")


class GenerateScriptsResponse(BaseModel):
    """话术生成响应"""
    scripts: List = Field(..., description="生成的话术列表")
    script_type: str = Field(..., description="话术类型")
    count: int = Field(..., description="生成数量")


class AddKnowledgeRequest(BaseModel):
    """添加知识请求"""
    documents: List[str] = Field(..., description="文档列表")
    metadatas: Optional[List[Dict[str, Any]]] = Field(None, description="元数据列表")
    collection_name: Optional[str] = Field(None, description="集合名称")


class AddKnowledgeResponse(BaseModel):
    """添加知识响应"""
    ids: List[str] = Field(..., description="文档ID列表")
    count: int = Field(..., description="添加数量")


class SearchKnowledgeRequest(BaseModel):
    """搜索知识请求"""
    query: str = Field(..., description="查询文本")
    n_results: int = Field(5, description="返回结果数量")
    filters: Optional[Dict[str, Any]] = Field(None, description="过滤条件")


class SearchKnowledgeResponse(BaseModel):
    """搜索知识响应"""
    documents: List[str] = Field(..., description="匹配的文档")
    metadatas: List[Dict[str, Any]] = Field(default_factory=list, description="元数据")
    distances: List[float] = Field(default_factory=list, description="距离分数")
    count: int = Field(..., description="结果数量")
