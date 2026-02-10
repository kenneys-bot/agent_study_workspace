# 大模型集成模块设计（通义千问）

## 模块概述

大模型集成模块负责与通义千问大模型进行交互，提供统一的接口供其他模块调用。该模块基于LangChain框架和DashScope SDK实现。

## 核心类设计

### LLMClient 类

```python
class LLMClient:
    """
    通义千问大模型客户端
    提供与通义千问大模型交互的统一接口
    """
    
    def __init__(self, model_name: str = QWEN_PLUS):
        """
        初始化大模型客户端
        
        Args:
            model_name (str): 模型名称，默认为qwen-plus
        """
        pass
    
    def generate_text(self, prompt: str, **kwargs) -> str:
        """
        文本生成接口
        
        Args:
            prompt (str): 输入提示词
            **kwargs: 其他参数（temperature, max_tokens等）
            
        Returns:
            str: 生成的文本
        """
        pass
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        对话接口
        
        Args:
            messages (List[Dict[str, str]]): 对话历史消息列表
            **kwargs: 其他参数
            
        Returns:
            str: 模型回复
        """
        pass
    
    def embedding(self, text: str) -> List[float]:
        """
        文本向量化接口
        
        Args:
            text (str): 输入文本
            
        Returns:
            List[float]: 文本向量表示
        """
        pass
    
    def classify_intent(self, text: str, categories: List[str]) -> str:
        """
        意图分类接口
        
        Args:
            text (str): 输入文本
            categories (List[str]): 意图分类列表
            
        Returns:
            str: 分类结果
        """
        pass
```

## 模型管理

### 支持的模型类型

1. **Qwen-Turbo**: 轻量级模型，适合简单任务
2. **Qwen-Plus**: 平衡性能与成本的模型，适合大多数场景
3. **Qwen-Max**: 高性能模型，适合复杂任务

### 模型参数配置

```python
# 默认模型参数
DEFAULT_PARAMETERS = {
    "temperature": 0.7,        # 温度参数，控制随机性
    "max_tokens": 2048,        # 最大生成token数
    "top_p": 0.9,             # 核采样参数
    "top_k": 50,              # top-k采样参数
    "repetition_penalty": 1.1 # 重复惩罚参数
}
```

## Prompt管理

### Prompt模板设计

```python
class PromptTemplate:
    """
    Prompt模板管理类
    """
    
    KNOWLEDGE_BASE_EXTRACT = """
    请从以下客户对话中提取关键问题：
    
    对话内容：
    {conversation}
    
    请按照以下格式输出：
    1. 主要问题：
    2. 相关信息：
    3. 客户情绪：
    """
    
    SCRIPT_RECOMMEND = """
    根据以下客户情况推荐合适的客服话术：
    
    客户问题：{question}
    客户情绪：{emotion}
    客户类型：{customer_type}
    
    请推荐3条合适的客服话术，并说明推荐理由。
    """
    
    QUALITY_INSPECT = """
    请对以下客服对话进行质量检查：
    
    对话内容：
    {conversation}
    
    检查要点：
    1. 服务态度
    2. 问题解决能力
    3. 专业性
    4. 合规性
    
    请给出评分（满分100分）和改进建议。
    """
```

## 错误处理与重试机制

### 异常类型定义

```python
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
```

### 重试机制

```python
# 重试配置
RETRY_CONFIG = {
    "max_retries": 3,          # 最大重试次数
    "retry_delay":  # 重试次数
    BACKOFF_FACTOR = 2      # 退避因子
```

## 缓存机制

### 响应缓存设计

```python
class ResponseCache:
    """
    大模型响应缓存
    """
    
    def get_cached_response(self, prompt: str, parameters: dict) -> Optional[str]:
        """
        获取缓存的响应
        
        Args:
            prompt (str): 提示词
            parameters (dict): 模型参数
            
        Returns:
            Optional[str]: 缓存的响应，如果不存在则返回None
        """
        pass
    
    def cache_response(self, prompt: str, parameters: dict, response: str, ttl: int = 3600):
        """
        缓存响应结果
        
        Args:
            prompt (str): 提示词
            parameters (dict): 模型参数
            response (str): 模型响应
            ttl (int): 缓存过期时间（秒）
        """
        pass
```

## 使用示例

### 基本文本生成
```python
from core.llm_client import LLMClient

# 初始化客户端
llm_client = LLMClient(model_name="qwen-plus")

# 文本生成
prompt = "请写一篇关于银行客服智能化的文章"
response = llm_client.generate_text(prompt, temperature=0.8, max_tokens=1000)
print(response)
```

### 对话交互
```python
# 对话交互
messages = [
    {"role": "user", "content": "我想查询我的账户余额"},
    {"role": "assistant", "content": "请问您是通过什么方式查询？ATM、网银还是手机银行？"},
    {"role": "user", "content": "手机银行"}
]

response = llm_client.chat(messages, temperature=0.7)
print(response)
```

### 意图分类
```python
# 意图分类
text = "我想要申请信用卡"
categories = ["账户查询", "业务办理", "投诉建议", "其他"]
intent = llm_client.classify_intent(text, categories)
print(f"识别的意图为: {intent}")
```

## 性能优化

### 批量处理
```python
def batch_generate(self, prompts: List[str], **kwargs) -> List[str]:
    """
    批量文本生成
    
    Args:
        prompts (List[str]): 提示词列表
        **kwargs: 模型参数
        
    Returns:
        List[str]: 生成结果列表
    """
    pass
```

### 异步处理
```python
async def async_generate_text(self, prompt: str, **kwargs) -> str:
    """
    异步文本生成
    
    Args:
        prompt (str): 输入提示词
        **kwargs: 其他参数
        
    Returns:
        str: 生成的文本
    """
    pass
```

## 监控与日志

### 调用统计
- API调用次数统计
- 响应时间监控
- 错误率统计
- Token消耗统计

### 日志记录
- 请求日志（脱敏处理）
- 响应日志
- 错误日志
- 性能日志

## 安全考虑

### 输入验证
- 提示词长度限制
- 特殊字符过滤
- 敏感词过滤

### 输出过滤
- 敏感信息脱敏
- 不当内容过滤
- 合规性检查

### 访问控制
- API密钥权限管理
- 调用频率限制
- 用户身份验证