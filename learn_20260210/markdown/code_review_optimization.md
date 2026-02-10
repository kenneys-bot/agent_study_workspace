# 代码审查和优化指南

## 概述

本文档定义了大模型客服助手项目的代码审查标准和优化策略，确保代码质量、可维护性和性能。

## 代码审查标准

### 1. 代码风格和规范

#### Python代码规范
- 遵循PEP 8代码风格指南
- 使用类型提示（Type Hints）
- 适当的文档字符串（Docstrings）
- 合理的命名约定

#### 示例
```python
# 好的命名
class CustomerServiceAssistant:
    """客服助手类"""
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化客服助手
        
        Args:
            llm_client: 大模型客户端
        """
        self.llm_client = llm_client

# 不好的命名
class CSA:
    def __init__(self, client):
        self.client = client
```

### 2. 架构设计审查

#### 模块化设计
- 高内聚、低耦合
- 单一职责原则
- 合理的接口设计
- 依赖注入

#### 示例
```python
# 好的设计：依赖注入
class ScriptRecommender:
    def __init__(self, llm_client: LLMClient, vector_db: VectorDBClient):
        self.llm_client = llm_client
        self.vector_db = vector_db

# 不好的设计：硬编码依赖
class ScriptRecommender:
    def __init__(self):
        self.llm_client = LLMClient()  # 硬编码依赖
        self.vector_db = VectorDBClient()
```

### 3. 错误处理审查

#### 异常处理
- 适当的异常捕获和处理
- 自定义异常类型
- 错误信息清晰明确
- 资源清理（try-finally或上下文管理器）

#### 示例
```python
# 好的异常处理
class VectorDBException(Exception):
    """向量数据库异常"""
    pass

def query_similar_scripts(self, query: str) -> List[RecommendedScript]:
    try:
        results = self.vector_db.query([query])
        return self._process_results(results)
    except Exception as e:
        logger.error(f"查询相似话术失败: {str(e)}")
        raise VectorDBException(f"数据库查询失败: {str(e)}") from e

# 不好的异常处理
def query_similar_scripts(self, query: str):
    results = self.vector_db.query([query])  # 可能抛出异常但未处理
    return results
```

### 4. 安全性审查

#### 输入验证
- 所有外部输入都需要验证
- 防止SQL注入、XSS等攻击
- 敏感信息处理

#### 示例
```python
# 好的安全实践
from pydantic import BaseModel, validator

class UserInput(BaseModel):
    query: str
    session_id: str
    
    @validator('query')
    def validate_query(cls, v):
        # 长度限制
        if len(v) > 1000:
            raise ValueError('查询内容过长')
        # 特殊字符过滤
        if any(char in v for char in ['<', '>', '"', "'"]):
            raise ValueError('包含非法字符')
        return v

# 不好的安全实践
def process_query(query: str):
    # 直接使用未经验证的输入
    return self.llm_client.generate_text(query)
```

## 性能优化策略

### 1. 算法优化

#### 时间复杂度优化
```python
# 优化前：O(n²)复杂度
def find_similar_questions_slow(questions: List[str], target: str) -> List[str]:
    similar = []
    for q1 in questions:
        for q2 in questions:
            if similarity(q1, q2) > 0.8:
                similar.append(q2)
    return similar

# 优化后：使用向量数据库，O(log n)复杂度
def find_similar_questions_fast(self, target: str) -> List[str]:
    return self.vector_db.query([target], n_results=5)
```

### 2. 缓存优化

#### 缓存策略
```python
from functools import lru_cache
import redis

class CacheManager:
    def __init__(self):
        self.redis_client = redis.Redis(host='localhost', port=6379, db=0)
    
    @lru_cache(maxsize=128)
    def get_standard_questions(self, category: str) -> List[str]:
        """使用LRU缓存标准问题"""
        # 缓存未命中时从数据库获取
        return self._fetch_from_db(category)
    
    def cache_conversation_result(self, session_id: str, result: dict, ttl: int = 3600):
        """缓存对话结果"""
        key = f"conversation:{session_id}"
        self.redis_client.setex(key, ttl, json.dumps(result))
```

### 3. 数据库优化

#### 查询优化
```python
# 优化前：N+1查询问题
def get_conversation_with_turns_slow(session_id: str) -> Conversation:
    conversation = get_conversation(session_id)
    turns = []
    for turn_id in conversation.turn_ids:
        turn = get_turn(turn_id)  # 每次都查询数据库
        turns.append(turn)
    conversation.turns = turns
    return conversation

# 优化后：批量查询
def get_conversation_with_turns_fast(session_id: str) -> Conversation:
    conversation = get_conversation(session_id)
    # 一次性获取所有轮次
    turns = get_turns_batch(conversation.turn_ids)
    conversation.turns = turns
    return conversation
```

### 4. 异步处理优化

#### 异步编程
```python
import asyncio
from typing import List

class AsyncProcessor:
    async def process_conversations_async(self, conversations: List[str]) -> List[str]:
        """异步处理多个对话"""
        tasks = [
            self.process_single_conversation(conversation)
            for conversation in conversations
        ]
        return await asyncio.gather(*tasks)
    
    async def process_single_conversation(self, conversation: str) -> str:
        """处理单个对话"""
        # 模拟异步操作
        await asyncio.sleep(0.1)
        return self.llm_client.generate_text(conversation)
```

## 代码质量工具

### 1. 代码格式化
```bash
# 使用black格式化代码
black app/

# 使用isort整理导入
isort app/
```

### 2. 代码检查
```bash
# 使用flake8检查代码风格
flake8 app/

# 使用mypy检查类型提示
mypy app/
```

### 3. 安全检查
```bash
# 使用bandit检查安全问题
bandit -r app/

# 使用safety检查依赖安全
safety check
```

## 测试覆盖优化

### 1. 测试用例优化
```python
import pytest
from unittest.mock import Mock, patch

class TestScriptRecommender:
    """话术推荐器测试"""
    
    def test_recommend_scripts_success(self):
        """测试成功推荐话术"""
        # Arrange
        mock_llm = Mock()
        mock_vector_db = Mock()
        recommender = ScriptRecommender(mock_llm, mock_vector_db)
        
        # Mock返回值
        mock_vector_db.query.return_value = {
            'documents': [['话术1', '话术2']],
            'metadatas': [[{}, {}]]
        }
        
        # Act
        result = recommender.recommend_scripts("测试情境", "测试意图")
        
        # Assert
        assert len(result) == 2
        mock_vector_db.query.assert_called_once()
    
    def test_recommend_scripts_empty_result(self):
        """测试空结果情况"""
        # Arrange
        mock_vector_db = Mock()
        mock_vector_db.query.return_value = {'documents': [[]], 'metadatas': [[]]}
        
        # Act & Assert
        with pytest.raises(NoScriptsFoundError):
            recommender = ScriptRecommender(Mock(), mock_vector_db)
            recommender.recommend_scripts("测试情境", "测试意图")
```

### 2. 性能测试
```python
import pytest
from pytest_benchmark import fixture

def test_script_recommendation_performance(benchmark):
    """测试话术推荐性能"""
    recommender = ScriptRecommender(llm_client, vector_db)
    
    # 基准测试
    result = benchmark(
        recommender.recommend_scripts,
        "高优先级客户咨询",
        "账户查询意图"
    )
    
    # 性能断言
    assert benchmark.stats.stats.mean < 2.0  # 平均响应时间小于2秒
```

## 文档优化

### 1. API文档
```python
class KnowledgeBaseAPI:
    def extract_questions(self, conversation: str) -> List[ExtractedQuestion]:
        """
        从对话中抽取关键问题
        
        该接口使用大模型技术分析客户对话内容，自动识别和抽取客户的核心问题，
        并对问题进行分类和优先级排序。
        
        Args:
            conversation (str): 客户与客服的完整对话内容，建议包含至少3轮对话
            
        Returns:
            List[ExtractedQuestion]: 抽取的问题列表，按优先级排序
            每个问题包含：
            - question: 问题内容
            - context: 问题上下文
            - emotion: 客户情绪
            - priority: 问题优先级(1-5)
            
        Raises:
            ValueError: 当对话内容为空或格式不正确时
            LLMException: 当大模型调用失败时
            
        Example:
            >>> api = KnowledgeBaseAPI()
            >>> conversation = "客户：我的信用卡无法刷卡\\n客服：请问卡片是否在有效期内？"
            >>> questions = api.extract_questions(conversation)
            >>> print(questions[0].question)
            '信用卡无法刷卡'
        """
        pass
```

### 2. 配置文档
```markdown
# 配置说明

## 环境变量配置

### 必需配置项
| 配置项 | 说明 | 示例 |
|-------|------|------|
| DASHSCOPE_API_KEY | 通义千问API密钥 | sk-xxxxxxxx |
| DATABASE_URL | 数据库连接URL | postgresql://user:pass@host:port/db |
| SECRET_KEY | 应用密钥 | random_string_at_least_32_chars |

### 可选配置项
| 配置项 | 默认值 | 说明 |
|-------|--------|------|
| APP_ENV | development | 应用环境(development/testing/staging/production) |
| DEBUG | False | 调试模式开关 |
| LOG_LEVEL | INFO | 日志级别(DEBUG/INFO/WARNING/ERROR) |
```

## 监控和日志优化

### 1. 结构化日志
```python
import logging
from loguru import logger

class StructuredLogger:
    def __init__(self):
        self.logger = logger
    
    def log_api_call(self, endpoint: str, duration: float, status: int, user_id: str = None):
        """记录API调用日志"""
        self.logger.info(
            "API调用",
            endpoint=endpoint,
            duration=duration,
            status=status,
            user_id=user_id,
            timestamp=datetime.now().isoformat()
        )
    
    def log_error(self, error_type: str, message: str, context: dict = None):
        """记录错误日志"""
        self.logger.error(
            "系统错误",
            error_type=error_type,
            message=message,
            context=context,
            timestamp=datetime.now().isoformat()
        )
```

### 2. 性能监控
```python
import time
from functools import wraps

def monitor_performance(metric_name: str):
    """性能监控装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                # 记录性能指标
                metrics.record(metric_name, duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                # 记录异常性能
                metrics.record_error(metric_name, duration, str(e))
                raise
        return wrapper
    return decorator

@monitor_performance("script_recommendation")
def recommend_scripts(self, context: ConversationContext, intent: UserIntent):
    """推荐话术"""
    # 实现代码
    pass
```

## 持续集成优化

### 1. CI/CD流水线
```yaml
# .github/workflows/ci.yml
name: CI/CD Pipeline

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: 3.9
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install -r requirements-test.txt
    - name: Code quality checks
      run: |
        black --check app/
        isort --check app/
        flake8 app/
        mypy app/
    - name: Security checks
      run: |
        bandit -r app/
        safety check
    - name: Run tests
      run: |
        pytest tests/ --cov=app/ --cov-report=xml
    - name: Upload coverage
      uses: codecov/codecov-action@v1
```

### 2. 代码质量门禁
```python
# quality_gate.py
class QualityGate:
    def __init__(self):
        self.min_coverage = 80
        self.max_complexity = 10
        self.allowed_errors = 0
    
    def check_coverage(self, coverage_report: dict) -> bool:
        """检查测试覆盖率"""
        current_coverage = coverage_report.get('total_coverage', 0)
        return current_coverage >= self.min_coverage
    
    def check_complexity(self, complexity_report: dict) -> bool:
        """检查代码复杂度"""
        max_file_complexity = max(
            file['complexity'] for file in complexity_report['files']
        )
        return max_file_complexity <= self.max_complexity
```

## 最佳实践总结

### 1. 代码可读性
- 使用有意义的变量名和函数名
- 保持函数简短（建议不超过50行）
- 添加适当的注释和文档
- 避免过深的嵌套

### 2. 可维护性
- 遵循SOLID原则
- 使用设计模式解决常见问题
- 编写可测试的代码
- 定期重构技术债务

### 3. 性能考虑
- 避免不必要的计算
- 合理使用缓存
- 优化数据库查询
- 异步处理耗时操作

### 4. 安全性
- 输入验证和过滤
- 防止常见安全漏洞
- 敏感信息保护
- 定期安全审计

通过遵循这些代码审查和优化指南，可以确保大模型客服助手项目的代码质量、性能和安全性达到高标准。