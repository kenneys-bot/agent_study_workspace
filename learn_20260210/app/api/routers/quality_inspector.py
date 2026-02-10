"""
质检助手API路由
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import List

from core.llm_client import get_llm_client, LLMClient
from api.models.quality_inspector import (
    ParseConversationRequest,
    ParsedConversationResponse,
    TranscribeRequest,
    TranscribeResponse,
    InspectRequest,
    InspectResponse,
    GenerateReportRequest,
    GenerateReportResponse,
    SubmitReviewRequest,
    SubmitReviewResponse,
    ApproveRejectRequest,
    ApproveRejectResponse,
    PendingReviewsResponse
)
from assistants.quality_inspector import (
    ConversationParser,
    SpeechToTextProcessor,
    AutoInspector,
    ReportGenerator,
    ReviewWorkflow,
    ParsedConversation
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality-inspector", tags=["质检助手"])


def get_conversation_parser() -> ConversationParser:
    """获取对话解析器"""
    return ConversationParser()


def get_speech_to_text_processor() -> SpeechToTextProcessor:
    """获取语音转文字处理器"""
    return SpeechToTextProcessor()


def get_auto_inspector() -> AutoInspector:
    """获取自动化质检器"""
    llm_client = get_llm_client()
    return AutoInspector(llm_client)


def get_report_generator() -> ReportGenerator:
    """获取报告生成器"""
    return ReportGenerator()


def get_review_workflow() -> ReviewWorkflow:
    """获取复核工作流"""
    return ReviewWorkflow()


@router.post("/parse-conversation", response_model=ParsedConversationResponse)
async def parse_conversation(
    request: ParseConversationRequest,
    parser: ConversationParser = Depends(get_conversation_parser)
):
    """
    解析对话内容
    
    - content: 对话内容
    - format: 内容格式（text/json）
    - session_id: 会话ID（可选）
    """
    try:
        parsed = parser.parse_conversation(
            request.content,
            request.format
        )
        
        format_detected = parser.detect_format(request.content)
        
        return ParsedConversationResponse(
            session_id=parsed.session_id,
            participants=parsed.participants,
            turns=[
                {
                    "speaker": turn.speaker,
                    "content": turn.content,
                    "timestamp": turn.timestamp.isoformat() if turn.timestamp else None,
                    "emotion": turn.emotion,
                    "intent": turn.intent
                }
                for turn in parsed.turns
            ],
            metadata=parsed.metadata,
            format_detected=format_detected
        )
        
    except Exception as e:
        logger.error(f"对话解析失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    request: TranscribeRequest,
    processor: SpeechToTextProcessor = Depends(get_speech_to_text_processor)
):
    """
    语音转文字
    
    - audio_file: 音频文件路径或base64编码
    - language: 语言代码
    - format: 音频格式（可选）
    """
    try:
        result = processor.transcribe_audio(
            request.audio_file,
            request.language
        )
        
        return TranscribeResponse(
            text=result.text,
            confidence=result.confidence,
            language=result.language,
            segments=[
                {
                    "text": seg.text,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "confidence": seg.confidence
                }
                for seg in result.segments
            ],
            duration=result.duration
        )
        
    except Exception as e:
        logger.error(f"语音转文字失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/inspect", response_model=InspectResponse)
async def inspect_conversation(
    request: InspectRequest,
    inspector: AutoInspector = Depends(get_auto_inspector)
):
    """
    自动质检对话
    
    - conversation: 对话内容
    - session_id: 会话ID（可选）
    - check_compliance: 是否检查合规性
    - detailed: 是否返回详细信息
    """
    try:
        from assistants.quality_inspector import DialogueTurn, ParsedConversation
        
        # 构建对话对象
        turns = [
            DialogueTurn(
                speaker=turn.get("speaker", "未知"),
                content=turn.get("content", "")
            )
            for turn in request.conversation.get("turns", [])
        ]
        
        parsed_conversation = ParsedConversation(
            session_id=request.session_id or request.conversation.get("session_id", "unknown"),
            participants=request.conversation.get("participants", []),
            turns=turns
        )
        
        report = inspector.inspect_conversation(parsed_conversation)
        
        return InspectResponse(
            session_id=report.session_id,
            overall_score=report.overall_score,
            attitude_score=report.attitude_score,
            professionalism_score=report.professionalism_score,
            compliance_score=report.compliance_score,
            issues=[
                {
                    "issue_type": issue.issue_type,
                    "description": issue.description,
                    "severity": issue.severity,
                    "location": issue.location,
                    "suggestion": issue.suggestion
                }
                for issue in report.issues
            ],
            summary=report.summary,
            generated_at=report.generated_at.isoformat()
        )
        
    except Exception as e:
        logger.error(f"质检失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-report", response_model=GenerateReportResponse)
async def generate_report(
    request: GenerateReportRequest,
    inspector: AutoInspector = Depends(get_auto_inspector),
    report_generator: ReportGenerator = Depends(get_report_generator)
):
    """
    生成质检报告
    
    - report_id: 报告ID或会话ID
    - format: 导出格式（json/text/html）
    - report_type: 报告类型（detailed/summary）
    """
    try:
        # 这里需要从数据库获取质检结果
        # 模拟返回
        from assistants.quality_inspector import InspectionReport
        
        mock_report = InspectionReport(
            session_id=request.report_id,
            overall_score=85.0,
            attitude_score=90.0,
            professionalism_score=85.0,
            compliance_score=95.0,
            summary="客服表现良好"
        )
        
        content = report_generator.generate_detailed_report(mock_report)
        
        return GenerateReportResponse(
            report_id=request.report_id,
            content=content,
            format=request.format,
            size=len(content)
        )
        
    except Exception as e:
        logger.error(f"报告生成失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit-review", response_model=SubmitReviewResponse)
async def submit_for_review(
    request: SubmitReviewRequest,
    workflow: ReviewWorkflow = Depends(get_review_workflow)
):
    """
    提交复核
    
    - report_id: 报告ID
    - reviewer: 复核人
    - comments: 复核意见（可选）
    """
    try:
        from assistants.quality_inspector import InspectionReport
        
        # 模拟报告对象
        mock_report = InspectionReport(
            session_id=request.report_id,
            overall_score=85.0
        )
        
        success = workflow.submit_for_review(mock_report, request.reviewer)
        
        if success:
            return SubmitReviewResponse(
                review_id=f"review_{request.report_id}",
                status="pending",
                submitted_at="2024-01-01T00:00:00"
            )
        else:
            raise HTTPException(status_code=500, detail="提交失败")
        
    except Exception as e:
        logger.error(f"复核提交失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve", response_model=ApproveRejectResponse)
async def approve_report(
    request: ApproveRejectRequest,
    workflow: ReviewWorkflow = Depends(get_review_workflow)
):
    """
    批准报告
    
    - review_id: 复核ID
    - approver: 操作人
    - comments: 意见（可选）
    """
    try:
        success = workflow.approve_report(
            request.review_id,
            request.approver,
            request.comments
        )
        
        if success:
            return ApproveRejectResponse(
                review_id=request.review_id,
                status="approved",
                updated_at="2024-01-01T00:00:00"
            )
        else:
            raise HTTPException(status_code=500, detail="批准失败")
        
    except Exception as e:
        logger.error(f"批准失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reject", response_model=ApproveRejectResponse)
async def reject_report(
    request: ApproveRejectRequest,
    workflow: ReviewWorkflow = Depends(get_review_workflow)
):
    """
    拒绝报告
    
    - review_id: 复核ID
    - approver: 操作人
    - comments: 意见（可选）
    - reasons: 拒绝原因
    """
    try:
        success = workflow.reject_report(
            request.review_id,
            request.approver,
            request.reasons or []
        )
        
        if success:
            return ApproveRejectResponse(
                review_id=request.review_id,
                status="rejected",
                updated_at="2024-01-01T00:00:00"
            )
        else:
            raise HTTPException(status_code=500, detail="拒绝失败")
        
    except Exception as e:
        logger.error(f"拒绝失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending-reviews", response_model=PendingReviewsResponse)
async def get_pending_reviews(
    workflow: ReviewWorkflow = Depends(get_review_workflow)
):
    """
    获取待复核列表
    """
    try:
        reviews = workflow.get_pending_reviews()
        
        return PendingReviewsResponse(
            count=len(reviews),
            reviews=reviews
        )
        
    except Exception as e:
        logger.error(f"获取待复核列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
