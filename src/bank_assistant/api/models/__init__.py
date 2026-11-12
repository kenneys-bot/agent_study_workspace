"""
API数据模型模块初始化
"""

from .knowledge_base import (
    ExtractQuestionsRequest,
    ExtractQuestionsResponse,
    ClassifyIntentRequest,
    ClassifyIntentResponse,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    GenerateScriptsRequest,
    GenerateScriptsResponse
)

from .script_recommender import (
    AnalyzeContextRequest,
    AnalyzeContextResponse,
    RecognizeIntentRequest,
    RecognizeIntentResponse,
    RecommendScriptsRequest,
    RecommendScriptsResponse,
    PersonalizeScriptRequest,
    PersonalizeScriptResponse
)

from .quality_inspector import (
    ParseConversationRequest,
    ParseConversationResponse,
    TranscribeRequest,
    TranscribeResponse,
    InspectRequest,
    InspectResponse,
    GenerateReportRequest,
    GenerateReportResponse
)

__all__ = [
    # 知识库助手
    "ExtractQuestionsRequest",
    "ExtractQuestionsResponse",
    "ClassifyIntentRequest",
    "ClassifyIntentResponse",
    "GenerateQuestionsRequest",
    "GenerateQuestionsResponse",
    "GenerateScriptsRequest",
    "GenerateScriptsResponse",
    # 话术推荐
    "AnalyzeContextRequest",
    "AnalyzeContextResponse",
    "RecognizeIntentRequest",
    "RecognizeIntentResponse",
    "RecommendScriptsRequest",
    "RecommendScriptsResponse",
    "PersonalizeScriptRequest",
    "PersonalizeScriptResponse",
    # 质检助手
    "ParseConversationRequest",
    "ParseConversationResponse",
    "TranscribeRequest",
    "TranscribeResponse",
    "InspectRequest",
    "InspectResponse",
    "GenerateReportRequest",
    "GenerateReportResponse"
]
