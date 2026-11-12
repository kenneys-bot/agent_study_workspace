"""
话术推荐助手API路由
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import List

from core.llm_client import get_llm_client, LLMClient
from core.vector_db import get_vector_db_client, VectorDBClient
from api.models.script_recommender import (
    AnalyzeContextRequest,
    AnalyzeContextResponse,
    RecognizeIntentRequest,
    RecognizeIntentResponse,
    RecommendScriptsRequest,
    RecommendScriptsResponse,
    PersonalizeScriptRequest,
    PersonalizeScriptResponse,
    CustomerProfileRequest,
    CustomerProfileResponse
)
from assistants.script_recommender import (
    ContextAnalyzer,
    IntentRecognizer,
    ScriptRecommender,
    PersonalizationAdapter,
    CustomerProfile,
    ConversationContext,
    UserIntent
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/script-recommender", tags=["话术推荐助手"])


def get_context_analyzer() -> ContextAnalyzer:
    """获取情境分析器"""
    llm_client = get_llm_client()
    return ContextAnalyzer(llm_client)


def get_intent_recognizer() -> IntentRecognizer:
    """获取意图识别器"""
    llm_client = get_llm_client()
    return IntentRecognizer(llm_client)


def get_script_recommender() -> ScriptRecommender:
    """获取话术推荐器"""
    llm_client = get_llm_client()
    vector_db = get_vector_db_client()
    return ScriptRecommender(llm_client, vector_db)


def get_personalization_adapter() -> PersonalizationAdapter:
    """获取个性化适配器"""
    llm_client = get_llm_client()
    return PersonalizationAdapter(llm_client)


@router.post("/analyze-context", response_model=AnalyzeContextResponse)
async def analyze_context(
    request: AnalyzeContextRequest,
    analyzer: ContextAnalyzer = Depends(get_context_analyzer)
):
    """
    分析对话情境
    
    - conversation_history: 对话历史
    - session_id: 会话ID（可选）
    """
    try:
        conversation_history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.conversation_history
        ]
        
        context = analyzer.analyze_context(conversation_history)
        
        return AnalyzeContextResponse(
            topic=context.topic,
            stage=context.stage,
            complexity=context.complexity,
            customer_satisfaction=context.customer_satisfaction,
            key_points=context.key_points,
            emotion=context.emotion
        )
        
    except Exception as e:
        logger.error(f"情境分析失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recognize-intent", response_model=RecognizeIntentResponse)
async def recognize_intent(
    request: RecognizeIntentRequest,
    recognizer: IntentRecognizer = Depends(get_intent_recognizer)
):
    """
    识别用户意图
    
    - current_query: 当前用户查询
    - context: 对话情境（可选）
    """
    try:
        result = recognizer.recognize_intent(
            request.current_query,
            None  # 可以传入ConversationContext对象
        )
        
        return RecognizeIntentResponse(
            intent_type=result.intent_type,
            sub_intent=result.sub_intent,
            confidence=result.confidence,
            required_info=result.required_info,
            suggested_actions=result.suggested_actions
        )
        
    except Exception as e:
        logger.error(f"意图识别失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommend-scripts", response_model=RecommendScriptsResponse)
async def recommend_scripts(
    request: RecommendScriptsRequest,
    recommender: ScriptRecommender = Depends(get_script_recommender)
):
    """
    推荐话术
    
    - context: 对话情境
    - intent: 用户意图
    - count: 推荐数量
    - filters: 过滤条件（可选）
    """
    try:
        context = ConversationContext(
            topic=request.context.get("topic", "未知"),
            stage=request.context.get("stage", "主阶段"),
            complexity=request.context.get("complexity", "中等"),
            customer_satisfaction=request.context.get("customer_satisfaction", 0.5),
            emotion=request.context.get("emotion", "中立")
        )
        
        intent = UserIntent(
            intent_type=request.intent.get("intent_type", "未知"),
            confidence=request.intent.get("confidence", 0.0)
        )
        
        scripts = recommender.recommend_scripts(
            context=context,
            intent=intent,
            count=request.count
        )
        
        return RecommendScriptsResponse(
            scripts=[
                {
                    "script_id": s.script_id,
                    "content": s.content,
                    "title": s.title,
                    "relevance_score": s.relevance_score,
                    "usage_count": s.usage_count,
                    "success_rate": s.success_rate
                }
                for s in scripts
            ],
            context_summary=f"主题: {context.topic}, 阶段: {context.stage}",
            intent_summary=f"意图: {intent.intent_type}, 置信度: {intent.confidence:.2f}"
        )
        
    except Exception as e:
        logger.error(f"话术推荐失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/personalize-script", response_model=PersonalizeScriptResponse)
async def personalize_script(
    request: PersonalizeScriptRequest,
    adapter: PersonalizationAdapter = Depends(get_personalization_adapter)
):
    """
    个性化适配话术
    
    - script: 原始话术内容
    - customer_profile: 客户画像
    - context: 对话情境（可选）
    """
    try:
        customer_profile = CustomerProfile(
            customer_id=request.customer_profile.get("customer_id"),
            name=request.customer_profile.get("name"),
            age=request.customer_profile.get("age"),
            gender=request.customer_profile.get("gender"),
            customer_type=request.customer_profile.get("customer_type"),
            risk_level=request.customer_profile.get("risk_level"),
            preference=request.customer_profile.get("preference")
        )
        
        personalized = adapter.adapt_script(
            script=request.script,
            customer_profile=customer_profile
        )
        
        return PersonalizeScriptResponse(
            personalized_script=personalized,
            original_script=request.script,
            customer_id=customer_profile.customer_id
        )
        
    except Exception as e:
        logger.error(f"话术个性化失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-greeting")
async def generate_greeting(
    request: CustomerProfileRequest,
    adapter: PersonalizationAdapter = Depends(get_personalization_adapter)
):
    """
    生成个性化问候语
    
    - customer_id: 客户ID
    - name: 客户姓名
    - customer_type: 客户类型
    """
    try:
        customer_profile = CustomerProfile(
            customer_id=request.customer_id,
            name=request.name,
            age=request.age,
            gender=request.gender,
            customer_type=request.customer_type,
            risk_level=request.risk_level,
            preference=request.preference
        )
        
        greeting = adapter.generate_personalized_greeting(customer_profile)
        
        return {"greeting": greeting}
        
    except Exception as e:
        logger.error(f"问候语生成失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
