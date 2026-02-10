"""
测试配置
"""

import pytest
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session")
def app_setup():
    """应用设置"""
    from main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    return app, client


@pytest.fixture
def mock_llm_client(mocker):
    """模拟LLM客户端"""
    mock = mocker.patch('core.llm_client.LLMClient')
    mock_instance = mock.return_value
    mock_instance.generate_text.return_value = "这是一个模拟的回复"
    mock_instance.chat.return_value = "模拟对话回复"
    mock_instance.classify_intent.return_value = {
        "intent": "账户查询",
        "confidence": 0.9
    }
    mock_instance.embedding.return_value = [0.1] * 1536
    return mock_instance


@pytest.fixture
def mock_vector_db(mocker):
    """模拟向量数据库"""
    mock = mocker.patch('core.vector_db.VectorDBClient')
    mock_instance = mock.return_value
    mock_instance.query.return_value = {
        "documents": [["测试文档1", "测试文档2"]],
        "metadatas": [[{}, {}]],
        "ids": [["id1", "id2"]],
        "distances": [[0.1, 0.2]]
    }
    mock_instance.add_documents.return_value = ["id1", "id2"]
    return mock_instance


# 配置pytest
pytest_plugins = [
    "pytest_asyncio",
]
