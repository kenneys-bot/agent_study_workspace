# Learn 20260210 项目

本项目是一个大模型客服助手应用，基于FastAPI框架开发。

## 目录结构

```
learn_20260210/
├── README.md                 # 项目说明文档
├── architecture.md           # 架构设计文档
├── requirements.txt          # Python依赖
├── docker-compose.yml       # Docker Compose配置
├── app/                      # 应用主目录
│   ├── __init__.py
│   ├── main.py              # FastAPI应用入口
│   ├── requirements.txt      # Python依赖
│   ├── pyproject.toml        # Python项目配置
│   ├── Dockerfile            # Docker构建文件
│   ├── config/               # 配置模块
│   │   ├── __init__.py
│   │   ├── settings.py       # 应用配置
│   │   └── constants.py     # 常量定义
│   ├── core/                 # 核心模块
│   │   ├── __init__.py
│   │   ├── llm_client.py     # 大模型客户端
│   │   └── vector_db.py      # 向量数据库客户端
│   ├── utils/                 # 工具模块
│   │   ├── __init__.py
│   │   ├── logger.py         # 日志工具
│   │   └── helpers.py        # 通用工具函数
│   ├── assistants/            # 助手模块
│   │   ├── __init__.py
│   │   ├── knowledge_base/   # 客服知识库助手
│   │   ├── script_recommender/  # 话术推荐助手
│   │   └── quality_inspector/  # 质检助手
│   ├── api/                  # API接口模块
│   │   ├── __init__.py
│   │   ├── models/           # 数据模型
│   │   └── routers/          # API路由
│   └── tests/                # 测试模块
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_api.py
│       └── test_units.py
└── design_docs/              # 设计文档
    ├── architecture.md
    ├── llm_module_design.md
    ├── vector_db_module_design.md
    └── ...
```

## 快速开始

### 环境要求

- Python 3.9+
- Docker & Docker Compose

### 安装依赖

```bash
cd app
pip install -r requirements.txt
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑.env文件，设置必要的环境变量
```

### 启动服务

```bash
# 开发模式
python main.py

# 或使用uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Docker Compose
docker-compose up -d
```

### 运行测试

```bash
pytest tests/ -v
```

## API文档

启动服务后，访问 http://localhost:8000/docs 查看API文档。

## 主要功能模块

1. **客服知识库助手** - 问题抽取、意图识别、问题生成、话术生成
2. **话术推荐助手** - 情境分析、意图识别、话术推荐、个性化适配
3. **质检助手** - 对话解析、语音转文字、自动质检、报告生成

## 技术栈

- **Web框架**: FastAPI
- **大模型**: 通义千问 (DashScope)
- **向量数据库**: Chroma
- **数据库**: PostgreSQL, Redis
- **部署**: Docker
