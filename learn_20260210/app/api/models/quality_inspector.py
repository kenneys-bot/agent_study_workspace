"""
质检助手API数据模型
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class DialogueTurnItem(BaseModel):
    """对话轮次项"""
    speaker: str = Field(..., description="发言者")
    content: str = Field(..., description="发言内容")
    timestamp: Optional[str] = Field(None, description="时间戳")
    emotion: Optional[str] = Field(None, description="情绪标签")
    intent: Optional[str] = Field(None, description="意图标签")


class ParseConversationRequest(BaseModel):
    """对话解析请求"""
    content: str = Field(..., description="对话内容")
    format: str = Field("text", description="内容格式: text/json")
    session_id: Optional[str] = Field(None, description="会话ID")


class ParsedConversationResponse(BaseModel):
    """对话解析响应"""
    session_id: str = Field(..., description="会话ID")
    participants: List[str] = Field(..., description="参与者列表")
    turns: List[DialogueTurnItem] = Field(..., description="对话轮次")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    format_detected: str = Field(..., description="检测到的格式")


class TranscribeRequest(BaseModel):
    """语音转文字请求"""
    audio_file: str = Field(..., description="音频文件路径或base64编码")
    language: str = Field("zh-CN", description="语言代码")
    format: Optional[str] = Field(None, description="音频格式")


class AudioSegmentItem(BaseModel):
    """音频片段项"""
    text: str = Field(..., description="文本内容")
    start_time: float = Field(0.0, description="开始时间")
    end_time: float = Field(0.0, description="结束时间")
    confidence: float = Field(0.0, description="置信度")


class TranscribeResponse(BaseModel):
    """语音转文字响应"""
    text: str = Field(..., description="转录文本")
    confidence: float = Field(..., description="置信度")
    language: str = Field(..., description="语言")
    segments: List[AudioSegmentItem] = Field(default_factory=list, description="音频片段")
    duration: float = Field(0.0, description="音频时长")


class InspectRequest(BaseModel):
    """自动质检请求"""
    conversation: Dict[str, Any] = Field(..., description="对话内容")
    session_id: Optional[str] = Field(None, description="会话ID")
    check_compliance: bool = Field(True, description="是否检查合规性")
    detailed: bool = Field(True, description="是否返回详细信息")


class QualityIssueItem(BaseModel):
    """质量问题项"""
    issue_type: str = Field(..., description="问题类型")
    description: str = Field(..., description="问题描述")
    severity: str = Field("中", description="严重程度")
    location: Optional[str] = Field(None, description="问题位置")
    suggestion: Optional[str] = Field(None, description="改进建议")
    evidence: Optional[str] = Field(None, description="证据")


class InspectResponse(BaseModel):
    """自动质检响应"""
    session_id: str = Field(..., description="会话ID")
    overall_score: float = Field(..., description="总体评分")
    attitude_score: float = Field(..., description="态度评分")
    professionalism_score: float = Field(..., description="专业性评分")
    compliance_score: float = Field(..., description="合规性评分")
    issues: List[QualityIssueItem] = Field(default_factory=list, description="发现问题")
    summary: str = Field("", description="总结")
    generated_at: str = Field(..., description="生成时间")


class GenerateReportRequest(BaseModel):
    """生成报告请求"""
    report_id: str = Field(..., description="报告ID或会话ID")
    format: str = Field("json", description="导出格式: json/text/html")
    report_type: str = Field("detailed", description="报告类型: detailed/summary")


class GenerateReportResponse(BaseModel):
    """生成报告响应"""
    report_id: str = Field(..., description="报告ID")
    content: str = Field(..., description="报告内容")
    format: str = Field(..., description="报告格式")
    size: int = Field(0, description="内容大小")


class SubmitReviewRequest(BaseModel):
    """提交复核请求"""
    report_id: str = Field(..., description="报告ID")
    reviewer: str = Field(..., description="复核人")
    comments: Optional[str] = Field(None, description="复核意见")


class SubmitReviewResponse(BaseModel):
    """提交复核响应"""
    review_id: str = Field(..., description="复核ID")
    status: str = Field(..., description="状态")
    submitted_at: str = Field(..., description="提交时间")


class ApproveRejectRequest(BaseModel):
    """批准/拒绝请求"""
    review_id: str = Field(..., description="复核ID")
    approver: str = Field(..., description="操作人")
    comments: Optional[str] = Field(None, description="意见")
    reasons: Optional[List[str]] = Field(None, description="拒绝原因")


class ApproveRejectResponse(BaseModel):
    """批准/拒绝响应"""
    review_id: str = Field(..., description="复核ID")
    status: str = Field(..., description="状态")
    updated_at: str = Field(..., description="更新时间")


class PendingReviewsResponse(BaseModel):
    """待复核列表响应"""
    count: int = Field(..., description="数量")
    reviews: List[Dict[str, Any]] = Field(default_factory=list, description="复核列表")
