"""
大模型客户端模块
提供与通义千问大模型的统一交互接口
"""

import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from bank_assistant.config.settings import settings
from bank_assistant.core.errors import CapabilityUnavailableError
from bank_assistant.config.constants import (
    LLMModels,
    LLMDefaultParams,
    PromptTemplates,
    ErrorMessages
)

logger = logging.getLogger(__name__)


class LLMException(Exception):
    """大模型异常基类"""
    pass


class APIConnectionError(LLMException):
    """API连接异常"""
    pass


class APITimeoutError(LLMException):
    """API超时异常"""
    pass


class RateLimitError(LLMException):
    """速率限制异常"""
    pass


class InvalidRequestError(LLMException):
    """无效请求异常"""
    pass


class LLMClient:
    """
    通义千问大模型客户端
    提供与通义千问大模型交互的统一接口
    """
    
    def __init__(
        self,
        model_name: str = LLMModels.QWEN_PLUS,
        api_key: Optional[str] = None
    ):
        """
        初始化大模型客户端
        
        Args:
            model_name (str): 模型名称，默认为qwen-plus
            api_key (Optional[str]): 凭据；缺省从 settings 读取。
                显式传空字符串会触发 capability unavailable（用于测试缺失凭据）。
        """
        self.model_name = model_name
        self.api_key = settings.DASHSCOPE_API_KEY if api_key is None else api_key
        self.base_url = settings.DASHSCOPE_BASE_URL
        self._client = None
        self._init_client()
    
    def _init_client(self):
        """初始化DashScope客户端。

        凭据或 SDK 缺失时显式失败：不提供隐式 mock，也不返回随机向量
        （ADR-0004 / M1-04）。
        """
        if not self.api_key:
            raise CapabilityUnavailableError(
                "capability unavailable: DASHSCOPE_API_KEY 未配置，无法调用 DashScope"
            )
        try:
            from dashscope import Generation
        except ImportError as exc:
            raise CapabilityUnavailableError(
                "capability unavailable: DashScope SDK 未安装（uv sync 安装依赖后重试）"
            ) from exc
        self._client = Generation()
        logger.info(f"大模型客户端初始化成功，使用模型: {self.model_name}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError))
    )
    def generate_text(
        self,
        prompt: str,
        temperature: float = LLMDefaultParams.TEMPERATURE,
        max_tokens: int = LLMDefaultParams.MAX_TOKENS,
        **kwargs
    ) -> str:
        """
        文本生成接口
        
        Args:
            prompt (str): 输入提示词
            temperature (float): 温度参数，控制随机性
            max_tokens (int): 最大生成token数
            **kwargs: 其他参数
            
        Returns:
            str: 生成的文本
        """
        try:
            if self._client is None:
                raise CapabilityUnavailableError(
                    "capability unavailable: 大模型客户端未初始化"
                )
            
            from dashscope import Generation
            
            response = Generation.call(
                model=self.model_name,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self.api_key,
                **kwargs
            )
            
            if response.status_code == 200:
                return response.output.text
            elif response.status_code == 429:
                raise RateLimitError(ErrorMessages.LLM_RATE_LIMIT_ERROR)
            elif response.status_code >= 400:
                raise InvalidRequestError(f"请求失败: {response.message}")
            else:
                raise APIConnectionError(f"API返回错误: {response.status_code}")
                
        except Exception as e:
            if isinstance(e, CapabilityUnavailableError):
                raise
            logger.error(f"文本生成失败: {str(e)}")
            if isinstance(e, LLMException):
                raise
            raise APIConnectionError(f"文本生成异常: {str(e)}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError))
    )
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = LLMDefaultParams.TEMPERATURE,
        max_tokens: int = LLMDefaultParams.MAX_TOKENS,
        **kwargs
    ) -> str:
        """
        对话接口
        
        Args:
            messages (List[Dict[str, str]]): 对话历史消息列表
            temperature (float): 温度参数
            max_tokens (int): 最大生成token数
            **kwargs: 其他参数
            
        Returns:
            str: 模型回复
        """
        try:
            if self._client is None:
                raise CapabilityUnavailableError(
                    "capability unavailable: 大模型客户端未初始化"
                )
            
            from dashscope import Generation
            
            # 保留完整消息（含 system）。此前把 system 抽出来后就再没发送，
            # 导致真正下发的请求丢失系统提示词（M1-02 / #6）。
            api_messages = [
                {"role": msg.get("role"), "content": msg.get("content")}
                for msg in messages
            ]
            
            response = Generation.call(
                model=self.model_name,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self.api_key,
                **kwargs
            )
            
            if response.status_code == 200:
                return response.output.text
            elif response.status_code == 429:
                raise RateLimitError(ErrorMessages.LLM_RATE_LIMIT_ERROR)
            else:
                raise APIConnectionError(f"对话失败: {response.message}")
                
        except Exception as e:
            if isinstance(e, CapabilityUnavailableError):
                raise
            logger.error(f"对话生成失败: {str(e)}")
            if isinstance(e, LLMException):
                raise
            raise APIConnectionError(f"对话异常: {str(e)}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError))
    )
    def embedding(self, text: str) -> List[float]:
        """
        文本向量化接口
        
        Args:
            text (str): 输入文本
            
        Returns:
            List[float]: 文本向量表示
        """
        # DeepSeek 官方不提供 embedding；正式索引使用自托管 BGE（ADR-0004，M3 接入）。
        # 在接入之前显式报能力不可用，绝不返回随机/占位向量污染索引。
        raise CapabilityUnavailableError(
            "capability unavailable: embedding 端点未配置；正式索引需自托管 BGE"
            "（ADR-0004），当前不提供随机向量或占位向量"
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError))
    )
    def classify_intent(
        self,
        text: str,
        categories: List[str]
    ) -> Dict[str, Any]:
        """
        意图分类接口
        
        Args:
            text (str): 输入文本
            categories (List[str]): 意图分类列表
            
        Returns:
            Dict[str, Any]: 分类结果，包含intent和confidence
        """
        try:
            prompt = PromptTemplates.INTENT_CLASSIFY.format(
                query=text,
                categories="、".join(categories)
            )
            
            response = self.generate_text(prompt)
            
            # 解析返回结果
            return self._parse_intent_response(response, categories)
            
        except Exception as e:
            logger.error(f"意图分类失败: {str(e)}")
            raise
    
    def batch_generate(
        self,
        prompts: List[str],
        temperature: float = LLMDefaultParams.TEMPERATURE,
        max_tokens: int = LLMDefaultParams.MAX_TOKENS,
        **kwargs
    ) -> List[str]:
        """
        批量文本生成
        
        Args:
            prompts (List[str]): 提示词列表
            temperature (float): 温度参数
            max_tokens (int): 最大生成token数
            **kwargs: 其他参数
            
        Returns:
            List[str]: 生成结果列表
        """
        results = []
        for i, prompt in enumerate(prompts):
            try:
                result = self.generate_text(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs
                )
                results.append(result)
            except Exception as e:
                logger.error(f"批量生成第{i}个失败: {str(e)}")
                results.append("")
        return results
    
    async def async_generate_text(
        self,
        prompt: str,
        temperature: float = LLMDefaultParams.TEMPERATURE,
        max_tokens: int = LLMDefaultParams.MAX_TOKENS,
        **kwargs
    ) -> str:
        """
        异步文本生成
        
        Args:
            prompt (str): 输入提示词
            temperature (float): 温度参数
            max_tokens (int): 最大生成token数
            **kwargs: 其他参数
            
        Returns:
            str: 生成的文本
        """
        import asyncio
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.generate_text,
            prompt,
            temperature,
            max_tokens
        )
    
    def _parse_intent_response(
        self,
        response: str,
        categories: List[str]
    ) -> Dict[str, Any]:
        """解析意图分类返回结果"""
        result = {
            "intent": categories[0] if categories else "未知",
            "confidence": 0.0,
            "raw_response": response
        }
        
        # 简单解析 - 实际应用中可以使用更复杂的解析逻辑
        for category in categories:
            if category in response:
                result["intent"] = category
                result["confidence"] = 0.8
                break
        
        return result
    
    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "model_name": self.model_name,
            "api_url": self.base_url,
            "temperature": settings.DEFAULT_TEMPERATURE,
            "max_tokens": settings.DEFAULT_MAX_TOKENS
        }


# 便捷函数
def get_llm_client(model_name: str = LLMModels.QWEN_PLUS) -> LLMClient:
    """
    获取大模型客户端实例
    
    Args:
        model_name (str): 模型名称
        
    Returns:
        LLMClient: 大模型客户端实例
    """
    return LLMClient(model_name=model_name)
