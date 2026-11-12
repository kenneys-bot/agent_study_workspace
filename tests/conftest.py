"""测试配置：共享 fixture。

导入路径由 pyproject.toml 的 `pythonpath = ["src"]` 提供，不再手工改 sys.path。
"""

import os

import pytest

# 单元测试的隔离凭据：真实密钥只在本地 .env，测试绝不读它，也不联网。
os.environ.setdefault("DASHSCOPE_API_KEY", "test-dummy")
os.environ.setdefault("SECRET_KEY", "test-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("APP_ENV", "testing")


@pytest.fixture(scope="session")
def app_setup():
    """应用设置"""
    from fastapi.testclient import TestClient

    from bank_assistant.main import app

    return app, TestClient(app)


@pytest.fixture
def mock_llm_client(mocker):
    """模拟 LLM 客户端"""
    mock = mocker.patch("bank_assistant.core.llm_client.LLMClient")
    mock_instance = mock.return_value
    mock_instance.generate_text.return_value = "这是一个模拟的回复"
    mock_instance.chat.return_value = "模拟对话回复"
    mock_instance.classify_intent.return_value = {
        "intent": "账户查询",
        "confidence": 0.9,
    }
    mock_instance.embedding.return_value = [0.1] * 1536
    return mock_instance


@pytest.fixture
def mock_vector_db(mocker):
    """模拟向量数据库"""
    mock = mocker.patch("bank_assistant.core.vector_db.VectorDBClient")
    mock_instance = mock.return_value
    mock_instance.query.return_value = {
        "documents": [["测试文档1", "测试文档2"]],
        "metadatas": [[{}, {}]],
        "ids": [["id1", "id2"]],
        "distances": [[0.1, 0.2]],
    }
    mock_instance.add_documents.return_value = ["id1", "id2"]
    return mock_instance


pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(scope="session")
def frozen_dataset_dir(tmp_path_factory):
    """用 T26 构建器在临时目录生成一份可校验的冻结数据集（离线、确定性）。

    训练/评测测试不依赖仓库里 gitignore 的 ``artifacts/`` 产物，也不下载任何
    模型；这样 `uv run pytest` 在干净检出上也能跑。
    """
    from datetime import UTC, datetime

    from bank_assistant.datasets.build import build_dataset, write_dataset

    directory = tmp_path_factory.mktemp("frozen-bank-cs-m6-v1")
    build = build_dataset(
        seed=42, created_at=datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
    )
    write_dataset(build, directory)
    return directory
