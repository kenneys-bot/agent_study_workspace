"""
客服知识库助手API路由
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import List

from core.llm_client import get_llm_client, LLMClient
from core.vector_db import get_vector_db_client, VectorDBClient
from api.models.knowledge_base import (
    ExtractQuestionsRequest,
    ExtractQuestionsResponse,
    ClassifyIntentRequest,
    ClassifyIntentResponse,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    GenerateScriptsRequest,
    GenerateScriptsResponse,
    AddKnowledgeRequest,
    AddKnowledgeResponse,
    SearchKnowledgeRequest,
    SearchKnowledgeResponse
)
from assistants.knowledge_base import (
    QuestionExtractor,
    IntentClassifier,
    QuestionGenerator,
    ScriptGenerator
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["客服知识库助手"])


def get_question_extractor() -> QuestionExtractor:
    """获取问题抽取器"""
    llm_client = get_llm_client()
    return QuestionExtractor(llm_client)


def get_intent_classifier() -> IntentClassifier:
    """获取意图分类器"""
    llm_client = get_llm_client()
    return IntentClassifier(llm_client)


def get_question_generator() -> QuestionGenerator:
    """获取问题生成器"""
    llm_client = get_llm_client()
    vector_db = get_vector_db_client()
    return QuestionGenerator(llm_client, vector_db)


def get_script_generator() -> ScriptGenerator:
    """获取话术生成器"""
    llm_client = get_llm_client()
    return ScriptGenerator(llm_client)


@router.post("/extract-questions", response_model=ExtractQuestionsResponse)
async def extract_questions(
    request: ExtractQuestionsRequest,
    extractor: QuestionExtractor = Depends(get_question_extractor)
):
    """
    从客户对话中抽取关键问题
    
    - conversation: 客户对话内容
    - session_id: 会话ID（可选）
    - max_questions: 最大抽取问题数量（默认5）
    """
    try:
        questions = extractor.extract_questions(
            request.conversation,
            request.max_questions
        )
        
        key_info = extractor.extract_key_info(request.conversation)
        
        return ExtractQuestionsResponse(
            questions=[
                {
                    "question": q.question,
                    "context": q.context,
                    "emotion": q.emotion,
                    "priority": q.priority
                }
                for q in questions
            ],
            key_info=key_info,
            session_id=request.session_id
        )
        
    except Exception as e:
        logger.error(f"问题抽取失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/classify-intent", response_model=ClassifyIntentResponse)
async def classify_intent(
    request: ClassifyIntentRequest,
    classifier: IntentClassifier = Depends(get_intent_classifier)
):
    """
    识别客户意图
    
    - query: 客户查询内容
    - categories: 意图分类列表（可选）
    """
    try:
        result = classifier.classify_intent(
            request.query,
            request.categories
        )
        
        return ClassifyIntentResponse(
            primary_intent=result.primary_intent,
            secondary_intent=result.secondary_intent,
            confidence=result.confidence,
            rewritten_query=result.rewritten_query
        )
        
    except Exception as e:
        logger.error(f"意图分类失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-questions", response_model=GenerateQuestionsResponse)
async def generate_questions(
    request: GenerateQuestionsRequest,
    generator: QuestionGenerator = Depends(get_question_generator)
):
    """
    生成标准问题或相似问题
    
    - topic: 主题
    - question_type: 问题类型（standard/similar/faq）
    - count: 生成数量
    - category: 分类（可选）
    """
    try:
        if request.question_type == "faq":
            questions = generator.generate_faq_questions(
                request.topic,
                request.count
            )
        elif request.question_type == "similar":
            questions = generator.generate_similar_questions(
                request.topic,
                request.count
            )
        else:
            questions = generator.generate_standard_questions(
                request.topic,
                request.count
            )
        
        # 验证问题
        validations = generator.validate_questions(questions, request.topic)
        
        return GenerateQuestionsResponse(
            questions=questions,
            validations=[
                {
                    "question": v.question,
                    "is_valid": v.is_valid,
                    "score": v.score,
                    "reason": v.reason
                }
                for v in validations
            ],
            topic=request.topic
        )
        
    except Exception as e:
        logger.error(f"问题生成失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-scripts", response_model=GenerateScriptsResponse)
async def generate_scripts(
    request: GenerateScriptsRequest,
    generator: ScriptGenerator = Depends(get_script_generator)
):
    """
    生成话术
    
    - script_type: 话术类型（call/collection/complaint）
    - parameters: 生成参数
    - count: 生成数量
    - tone: 话术风格（可选）
    """
    try:
        params = request.parameters
        
        if request.script_type == "collection":
            scripts = generator.generate_collection_scripts(
                overdue_days=params.get("overdue_days", 30),
                customer_risk=params.get("customer_risk", "低风险"),
                count=request.count
            )
            response_scripts = [
                {
                    "opening": s.opening,
                    "negotiation": s.negotiation,
                    "commitment_request": s.commitment_request,
                    "risk_level": s.risk_level
                }
                for s in scripts
            ]
        else:
            scripts = generator.generate_call_scripts(
                scenario=params.get("scenario", ""),
                customer_type=params.get("customer_type", "普通客户"),
                count=request.count,
                tone=request.tone
            )
            response_scripts = [
                {
                    "greeting": s.greeting,
                    "main_content": s.main_content,
                    "closing": s.closing,
                    "scenario": s.scenario
                }
                for s in scripts
            ]
        
        return GenerateScriptsResponse(
            scripts=response_scripts,
            script_type=request.script_type,
            count=len(response_scripts)
        )
        
    except Exception as e:
        logger.error(f"话术生成失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add-knowledge", response_model=AddKnowledgeResponse)
async def add_knowledge(
    request: AddKnowledgeRequest,
    vector_db: VectorDBClient = Depends(get_vector_db_client)
):
    """
    添加知识库文档
    
    - documents: 文档列表
    - metadatas: 元数据列表（可选）
    - collection_name: 集合名称（可选）
    """
    try:
        if request.collection_name:
            vector_db = get_vector_db_client(request.collection_name)
        
        ids = vector_db.add_documents(
            documents=request.documents,
            metadatas=request.metadatas
        )
        
        return AddKnowledgeResponse(
            ids=ids,
            count=len(ids)
        )
        
    except Exception as e:
        logger.error(f"添加知识失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search-knowledge", response_model=SearchKnowledgeResponse)
async def search_knowledge(
    request: SearchKnowledgeRequest,
    vector_db: VectorDBClient = Depends(get_vector_db_client)
):
    """
    搜索知识库
    
    - query: 查询文本
    - n_results: 返回结果数量
    - filters: 过滤条件（可选）
    """
    try:
        results = vector_db.query(
            query_texts=[request.query],
            n_results=request.n_results,
            where=request.filters
        )
        
        return SearchKnowledgeResponse(
            documents=results.get("documents", [[]])[0] if results.get("documents") else [],
            metadatas=results.get("metadatas", [[]])[0] if results.get("metadatas") else [],
            distances=results.get("distances", [[]])[0] if results.get("distances") else [],
            count=len(results.get("documents", [[]])[0]) if results.get("documents") else 0
        )
        
    except Exception as e:
        logger.error(f"知识搜索失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
