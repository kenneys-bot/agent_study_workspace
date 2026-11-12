"""
质检助手API路由
"""

import logging
from fastapi import APIRouter, Depends, Request

from bank_assistant.core.llm_client import get_llm_client
from bank_assistant.api.errors import ApiError, ErrorResponse, not_implemented_response
from bank_assistant.api.models.quality_inspector import (
    ParseConversationRequest,
    ParseConversationResponse,
    TranscribeRequest,
    TranscribeResponse,
    InspectRequest,
    InspectResponse,
    GenerateReportRequest,
    SubmitReviewRequest,
    ApproveRejectRequest,
)
from bank_assistant.assistants.quality_inspector import (
    ConversationParser,
    SpeechToTextProcessor,
    AutoInspector,
    ParsedConversation
)
from bank_assistant.assistants.quality_inspector.conversation_parser import (
    ConversationParseError,
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


@router.post("/parse-conversation", response_model=ParseConversationResponse)
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
            request.format,
            request.session_id
        )
        
        format_detected = parser.detect_format(request.content)
        
        return ParseConversationResponse(
            session_id=parsed.session_id,
            participants=parsed.participants,
            turns=[
                {
                    "ordinal": turn.ordinal,
                    "speaker": turn.speaker,
                    "content": turn.content,
                    "timestamp": turn.timestamp,
                    "emotion": turn.emotion,
                    "intent": turn.intent
                }
                for turn in parsed.turns
            ],
            metadata=parsed.metadata,
            format_detected=format_detected,
            raw_text=parsed.raw_text
        )
        
    except ConversationParseError as e:
        # 非法输入：4xx + 稳定错误码；不把用户原文或异常细节回传。
        logger.warning(f"对话解析输入非法 [{e.code}]: {e.message}")
        raise ApiError(
            status_code=e.http_status,
            code=e.code,
            message=e.message,
            retryable=False,
            details={},
        ) from e
    except Exception as e:
        logger.error(f"对话解析失败: {str(e)}")
        raise ApiError(status_code=500) from e


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
        raise ApiError(status_code=500) from e


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
        from bank_assistant.assistants.quality_inspector import DialogueTurn, ParsedConversation
        
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
        raise ApiError(status_code=500) from e


@router.post(
    "/generate-report",
    status_code=501,
    responses={501: {"model": ErrorResponse, "description": "报告持久化尚未实现"}},
)
async def generate_report(
    request: GenerateReportRequest,
    http_request: Request,
):
    """
    导出质检报告

    尚未实现：系统还没有真实的质检报告持久化，不能凭空生成分数和总结。
    M4 接通报告持久化后再提供真实导出。

    - report_id: 报告ID或会话ID
    - format: 导出格式（json/text/html）
    - report_type: 报告类型（detailed/summary）
    """
    logger.info("报告导出不可用：尚无真实报告持久化 [report_id=%s]", request.report_id)
    return not_implemented_response(
        http_request,
        operation="generate_report",
        message="报告导出尚未实现：系统尚未持久化质检报告，无法返回真实内容",
    )


@router.post(
    "/submit-review",
    status_code=501,
    responses={501: {"model": ErrorResponse, "description": "复核持久化尚未实现"}},
)
async def submit_for_review(
    request: SubmitReviewRequest,
    http_request: Request,
):
    """
    提交复核

    尚未实现：系统还没有真实的复核记录持久化，不能返回假的 review_id 或时间戳。
    M4 接通复核流程后再提供真实提交。

    - report_id: 报告ID
    - reviewer: 复核人
    - comments: 复核意见（可选）
    """
    logger.info("复核提交不可用：尚无真实复核持久化 [report_id=%s]", request.report_id)
    return not_implemented_response(
        http_request,
        operation="submit_review",
        message="复核提交尚未实现：系统尚未持久化复核记录，无法返回真实复核结果",
    )


@router.post(
    "/approve",
    status_code=501,
    responses={501: {"model": ErrorResponse, "description": "复核持久化尚未实现"}},
)
async def approve_report(
    request: ApproveRejectRequest,
    http_request: Request,
):
    """
    批准报告

    尚未实现：没有持久化的复核记录可批准，不能凭进程内状态返回成功。

    - review_id: 复核ID
    - approver: 操作人
    - comments: 意见（可选）
    """
    logger.info("报告批准不可用：尚无真实复核持久化 [review_id=%s]", request.review_id)
    return not_implemented_response(
        http_request,
        operation="approve_review",
        message="报告批准尚未实现：系统尚未持久化复核记录，无法返回真实状态",
    )


@router.post(
    "/reject",
    status_code=501,
    responses={501: {"model": ErrorResponse, "description": "复核持久化尚未实现"}},
)
async def reject_report(
    request: ApproveRejectRequest,
    http_request: Request,
):
    """
    拒绝报告

    尚未实现：没有持久化的复核记录可拒绝，不能凭进程内状态返回成功。

    - review_id: 复核ID
    - approver: 操作人
    - comments: 意见（可选）
    - reasons: 拒绝原因
    """
    logger.info("报告拒绝不可用：尚无真实复核持久化 [review_id=%s]", request.review_id)
    return not_implemented_response(
        http_request,
        operation="reject_review",
        message="报告拒绝尚未实现：系统尚未持久化复核记录，无法返回真实状态",
    )


@router.get(
    "/pending-reviews",
    status_code=501,
    responses={501: {"model": ErrorResponse, "description": "复核持久化尚未实现"}},
)
async def get_pending_reviews(http_request: Request):
    """
    获取待复核列表

    尚未实现：不存在跨请求存活的复核记录，不能返回进程内实例列表。
    """
    logger.info("待复核列表不可用：尚无真实复核持久化")
    return not_implemented_response(
        http_request,
        operation="list_pending_reviews",
        message="待复核列表尚未实现：系统尚未持久化复核记录",
    )
