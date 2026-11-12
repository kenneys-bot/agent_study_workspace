# 大模型客服助手项目目录结构

## 项目根目录结构

```
customer_service_ai/
├── README.md                           # 项目说明文档
├── requirements.txt                    # 项目依赖
├── docker-compose.yml                  # Docker编排文件
├── Dockerfile                          # Docker构建文件
├── .env                                # 环境变量配置文件
├── .gitignore                          # Git忽略文件配置
├── app/                                # 应用主目录
│   ├── __init__.py                     # Python包初始化文件
│   ├── main.py                         # 应用入口文件
│   ├── config/                         # 配置管理模块
│   │   ├── __init__.py
│   │   ├── settings.py                 # 应用配置
│   │   └── constants.py                # 常量定义
│   ├── core/                           # 核心模块
│   │   ├── __init__.py
│   │   ├── llm_client.py               # 大模型客户端
│   │   └── vector_db.py                # 向量数据库客户端
│   ├── assistants/                     # 助手模块
│   │   ├── __init__.py
│   │   ├── knowledge_base/             # 客服知识库助手
│   │   │   ├── __init__.py
│   │   │   ├── question_extractor.py   # 客户问题抽取
│   │   │   ├── intent_classifier.py    # 客户意图识别
│   │   │   ├── question_generator.py   # 标准问/相似问生成
│   │   │   └── script_generator.py     # 话术生成
│   │   ├── script_recommender/         # 话术推荐助手
│   │   │   ├── __init__.py
│   │   │   ├── context_analyzer.py     # 对话情境理解
│   │   │   ├── intent_recognizer.py    # 用户意图识别
│   │   │   └── script_recommender.py   # 话术推荐
│   │   └── quality_inspector/          # 质检助手
│   │       ├── __init__.py
│   │       ├── conversation_parser.py  # 对话内容解析
│   │       ├── speech_to_text.py       # 语音转文字
│   │       └── auto_inspector.py       # 自动化质检
│   ├── api/                            # API接口模块
│   │   ├── __init__.py
│   │   ├── routers/                    # 路由定义
│   │   │   ├── __init__.py
│   │   │   ├── knowledge_base.py       # 知识库助手API
│   │   │   ├── script_recommender.py   # 话术推荐API
│   │   │   └── quality_inspector.py    # 质检助手API
│   │   └── models/                     # 数据模型定义
│   │       ├── __init__.py
│   │       ├── knowledge_base.py       # 知识库数据模型
│   │       ├── script_recommender.py   # 话术推荐数据模型
│   │       └── quality_inspector.py    # 质检数据模型
│   ├── utils/                          # 工具模块
│   │   ├── __init__.py
│   │   ├── logger.py                   # 日志工具
│   │   └── helpers.py                  # 通用工具函数
│   └── tests/                          # 测试模块
│       ├── __init__.py
│       ├── test_knowledge_base/        # 知识库助手测试
│       ├── test_script_recommender/    # 话术推荐助手测试
│       ├── test_quality_inspector/     # 质检助手测试
│       └── conftest.py                 # 测试配置
└── docs/                               # 文档目录
    ├── architecture.md                 # 架构设计文档
    ├── api_docs.md                     # API文档
    └── deployment.md                   # 部署文档
```

## 目录结构说明

### 根目录文件
- `README.md`: 项目概述和使用说明
- `requirements.txt`: Python依赖包列表
- `docker-compose.yml`: 多容器应用编排配置
- `Dockerfile`: Docker镜像构建配置
- `.env`: 环境变量配置文件
- `.gitignore`: Git版本控制忽略规则

### app/ 应用主目录
- `main.py`: 应用启动入口
- `config/`: 配置管理模块，包含应用配置和常量定义
- `core/`: 核心模块，包含大模型客户端和向量数据库客户端
- `assistants/`: 各个助手功能模块
- `api/`: REST API接口模块
- `utils/`: 通用工具模块
- `tests/`: 测试模块

### 各助手模块说明

#### knowledge_base/ 客服知识库助手
- `question_extractor.py`: 客户问题抽取功能
- `intent_classifier.py`: 客户意图识别与改写功能
- `question_generator.py`: 客服标准问/相似问生成功能
- `script_generator.py`: 电话话术和电催话术提炼功能

#### script_recommender/ 话术推荐助手
- `context_analyzer.py`: 实时对话情境理解功能
- `intent_recognizer.py`: 用户意图识别功能
- `script_recommender.py`: RAG话术推荐和个性化适配功能

#### quality_inspector/ 质检助手
- `conversation_parser.py`: 对话内容上传与解析功能
- `speech_to_text.py`: 语音转文字处理功能
- `auto_inspector.py`: 大模型自动化质检和结果生成功能

### API模块说明
- `routers/`: 各个功能模块的API路由定义
- `models/`: API数据传输对象定义

### 测试模块说明
- 各个功能模块对应的测试用例
- `conftest.py`: pytest测试框架配置文件

### 文档目录
- `architecture.md`: 系统架构设计文档
- `api_docs.md`: API接口文档
- `deployment.md`: 部署和运维文档