# 大模型客服助手项目测试策略

## 概述

本文档定义了大模型客服助手项目的测试策略，包括单元测试、集成测试、性能测试和安全测试等方面，确保项目质量和稳定性。

## 测试目标

1. 验证各模块功能的正确性和完整性
2. 确保模块间接口的正确交互
3. 验证系统性能满足业务要求
4. 确保系统安全性和数据保护
5. 提高代码质量和可维护性

## 测试层次

### 1. 单元测试

#### 测试范围
- 各模块核心功能函数
- 数据模型和数据处理逻辑
- 配置管理功能
- 异常处理机制

#### 测试工具
- pytest: Python测试框架
- pytest-asyncio: 异步测试支持
- unittest.mock: 模拟对象库

#### 测试示例

##### 配置管理模块测试
```python
import pytest
from config.settings import Settings

def test_settings_loading():
    """测试配置加载"""
    settings = Settings()
    assert settings.APP_HOST is not None
    assert settings.DASHSCOPE_API_KEY is not None

def test_settings_validation():
    """测试配置验证"""
    with pytest.raises(ValueError):
        settings = Settings()
        settings.DASHSCOPE_API_KEY = None  # 应该触发验证错误
```

##### 大模型集成模块测试
```python
import pytest
from unittest.mock import Mock, patch
from core.llm_client import LLMClient

def test_llm_client_initialization():
    """测试大模型客户端初始化"""
    client = LLMClient(model_name="qwen-plus")
    assert client.model_name == "qwen-plus"

@patch('core.llm_client.dashscope.Generation.call')
def test_text_generation(mock_call):
    """测试文本生成功能"""
    mock_call.return_value = Mock(output={'text': '测试响应'})
    
    client = LLMClient()
    result = client.generate_text("测试提示")
    assert result == "测试响应"
```

##### 向量数据库模块测试
```python
import pytest
from unittest.mock import Mock, patch
from core.vector_db import VectorDBClient

def test_vector_db_client_initialization():
    """测试向量数据库客户端初始化"""
    client = VectorDBClient(collection_name="test_collection")
    assert client.collection_name == "test_collection"

@patch('core.vector_db.chromadb.Client')
def test_add_documents(mock_client):
    """测试添加文档功能"""
    mock_collection = Mock()
    mock_client.return_value.get_or_create_collection.return_value = mock_collection
    
    client = VectorDBClient()
    documents = ["测试文档1", "测试文档2"]
    ids = client.add_documents(documents)
    
    assert len(ids) == 2
    mock_collection.add.assert_called_once()
```

### 2. 集成测试

#### 测试范围
- 模块间接口调用
- API接口功能
- 数据库交互
- 外部服务调用

#### 测试工具
- pytest: 测试框架
- requests: HTTP请求库
- docker-compose: 测试环境管理

#### 测试示例

##### API接口集成测试
```python
import pytest
import requests
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_knowledge_base_extraction():
    """测试知识库问题抽取接口"""
    response = client.post(
        "/api/v1/knowledge-base/extract-questions",
        json={
            "conversation": "客户：我想查询账户余额\n客服：请问您的账户号码是多少？",
            "session_id": "test_session_001"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "questions" in data
    assert len(data["questions"]) > 0

def test_script_recommendation():
    """测试话术推荐接口"""
    response = client.post(
        "/api/v1/script-recommender/recommend-scripts",
        json={
            "context": {
                "topic": "账户查询",
                "stage": "初始阶段",
                "customer_satisfaction": 0.8
            },
            "intent": {
                "intent_type": "账户查询",
                "confidence": 0.95
            },
            "count": 3
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "scripts" in data
    assert len(data["scripts"]) <= 3
```

##### 模块间接口测试
```python
import pytest
from assistants.knowledge_base.question_extractor import QuestionExtractor
from assistants.script_recommender.context_analyzer import ContextAnalyzer
from core.llm_client import LLMClient

def test_knowledge_to_script_integration():
    """测试知识库到话术推荐的集成"""
    # 初始化组件
    llm_client = LLMClient()
    extractor = QuestionExtractor(llm_client)
    analyzer = ContextAnalyzer(llm_client)
    
    # 测试数据
    conversation = "客户：我的信用卡账单查不到\n客服：请问您是通过什么方式查询的？"
    
    # 问题抽取
    questions = extractor.extract_questions(conversation)
    assert len(questions) > 0
    
    # 情境分析
    conversation_history = [
        {"role": "customer", "content": "我的信用卡账单查不到"},
        {"role": "agent", "content": "请问您是通过什么方式查询的？"}
    ]
    context = analyzer.analyze_context(conversation_history)
    assert context.topic is not None
```

### 3. 性能测试

#### 测试范围
- API响应时间
- 并发处理能力
- 大模型调用性能
- 数据库查询性能

#### 测试工具
- locust: 负载测试工具
- pytest-benchmark: 性能基准测试
- Apache Bench (ab): HTTP性能测试

#### 测试示例

##### API性能测试
```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

@pytest.mark.benchmark
def test_knowledge_base_api_performance(benchmark):
    """测试知识库API性能"""
    def api_call():
        return client.post(
            "/api/v1/knowledge-base/classify-intent",
            json={"query": "测试查询"}
        )
    
    result = benchmark(api_call)
    assert result.status_code == 200

def test_concurrent_requests():
    """测试并发请求处理"""
    import concurrent.futures
    import time
    
    def make_request():
        return client.post(
            "/api/v1/script-recommender/analyze-context",
            json={
                "conversation_history": [
                    {"role": "customer", "content": "测试内容"}
                ]
            }
        )
    
    # 并发10个请求
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request) for _ in range(10)]
        results = [future.result() for future in futures]
    
    # 验证所有请求都成功
    assert all(r.status_code == 200 for r in results)
```

### 4. 安全测试

#### 测试范围
- 输入验证和过滤
- 身份认证和授权
- 数据加密和保护
- API安全防护

#### 测试工具
- pytest: 测试框架
- bandit: 安全漏洞扫描
- safety: 依赖安全检查

#### 测试示例

##### 输入验证测试
```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_sql_injection_prevention():
    """测试SQL注入防护"""
    malicious_input = "'; DROP TABLE users; --"
    
    response = client.post(
        "/api/v1/knowledge-base/classify-intent",
        json={"query": malicious_input}
    )
    
    # 应该正常处理而不是执行恶意代码
    assert response.status_code == 200

def test_xss_prevention():
    """测试XSS防护"""
    malicious_input = "<script>alert('xss')</script>"
    
    response = client.post(
        "/api/v1/knowledge-base/extract-questions",
        json={
            "conversation": malicious_input,
            "session_id": "test_session"
        }
    )
    
    # 应该正常处理而不是执行脚本
    assert response.status_code == 200
```

## 测试环境

### 开发环境测试
- 本地开发环境运行单元测试
- 使用mock对象模拟外部依赖
- 验证基本功能逻辑

### 集成环境测试
- Docker容器化部署测试环境
- 真实的大模型和数据库连接
- 完整的API接口测试

### 生产环境测试
- 灰度发布前的预生产环境测试
- 性能基准测试
- 安全扫描和漏洞检查

## 测试数据管理

### 测试数据准备
```python
# test_data.py
import json

# 标准测试对话数据
TEST_CONVERSATIONS = {
    "account_inquiry": {
        "customer": "我想查询我的账户余额",
        "agent": "请问您是通过什么方式查询？网银、手机银行还是电话查询？",
        "expected_intent": "账户查询"
    },
    "credit_card_issue": {
        "customer": "我的信用卡在POS机上刷不了",
        "agent": "请问您的信用卡是否在有效期内？",
        "expected_intent": "卡片问题"
    }
}

# 测试配置数据
TEST_CONFIG = {
    "valid_api_key": "test_api_key_123456",
    "invalid_api_key": "invalid_key",
    "test_collection": "test_knowledge_base"
}
```

### 测试数据清理
```python
import pytest

@pytest.fixture(scope="function")
def clean_test_data():
    """测试数据清理fixture"""
    # 测试前准备
    yield
    # 测试后清理
    # 清理测试数据库记录
    # 清理测试文件
    # 重置mock对象
```

## 测试执行计划

### 持续集成测试
```yaml
# .github/workflows/test.yml
name: Tests
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
    - name: Run unit tests
      run: pytest tests/unit/ -v
    - name: Run integration tests
      run: pytest tests/integration/ -v
    - name: Run security checks
      run: |
        bandit -r app/
        safety check
```

### 测试覆盖率要求
- 单元测试覆盖率: ≥ 80%
- 集成测试覆盖率: ≥ 70%
- 核心业务逻辑覆盖率: 100%

### 测试报告
- 生成HTML格式的测试报告
- 集成到CI/CD流程中
- 定期发送测试报告邮件

## 测试质量保证

### 代码审查检查点
- 测试用例是否覆盖主要功能路径
- 测试数据是否合理且具有代表性
- 异常情况是否得到充分测试
- 测试代码是否遵循最佳实践

### 测试维护
- 定期更新测试用例以匹配功能变更
- 清理过时的测试数据和代码
- 优化测试执行时间
- 监控测试稳定性

## 风险与应对

### 测试风险
1. **大模型调用成本高**: 使用mock对象模拟大模型响应
2. **外部服务依赖**: 使用测试桩(mock)替代真实服务
3. **测试数据隐私**: 使用脱敏的测试数据
4. **测试环境不一致**: 使用Docker标准化测试环境

### 应对措施
1. **分层测试策略**: 优先保证核心功能的单元测试覆盖率
2. **自动化测试**: 建立完整的CI/CD测试流程
3. **测试数据管理**: 建立测试数据生成和清理机制
4. **监控告警**: 建立测试失败告警机制

## 后续改进

1. **测试工具优化**: 引入更先进的测试工具和框架
2. **测试数据工厂**: 建立测试数据自动生成工厂
3. **性能基准线**: 建立性能测试基准线并持续监控
4. **安全测试增强**: 增加更全面的安全测试覆盖