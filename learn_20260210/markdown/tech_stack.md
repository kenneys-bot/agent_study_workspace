# 大模型客服助手项目技术栈

## 核心技术选型

### 1. 编程语言
- **Python 3.9+**: 作为主要开发语言，具有丰富的AI和数据处理库生态

### 2. 大模型框架
- **LangChain**: 用于构建大模型应用的框架，提供Prompt管理、Chain调用等功能
- **DashScope**: 阿里云通义千问API的Python SDK

### 3. 向量数据库
- **Chroma**: 轻量级向量数据库，易于集成和部署

### 4. Web框架
- **FastAPI**: 现代、快速(高性能)的Web框架，用于构建API接口

### 5. 异步处理
- **asyncio**: Python内置异步处理库
- **Celery**: 分布式任务队列，用于处理耗时任务

### 6. 数据存储
- **PostgreSQL**: 关系型数据库，存储结构化数据
- **Redis**: 内存数据库，用于缓存和会话管理

### 7. 部署与运维
- **Docker**: 容器化部署
- **Docker Compose**: 多容器应用编排
- **Nginx**: 反向代理和负载均衡

## 项目依赖库清单

### 核心依赖
```txt
langchain>=0.1.0
langchain-community>=0.0.15
langchain-core>=0.1.10
dashscope>=1.14.0
chromadb>=0.4.0
fastapi>=0.100.0
uvicorn>=0.23.0
pydantic>=2.0.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
redis>=4.5.0
celery>=5.3.0
```

### 开发依赖
```txt
pytest>=7.0.0
pytest-asyncio>=0.21.0
black>=23.0.0
flake8>=6.0.0
mypy>=1.0.0
```

### 部署依赖
```txt
gunicorn>=21.0.0
docker>=6.0.0
```

## 技术架构优势

1. **模块化设计**: 各功能模块独立，便于维护和扩展
2. **高性能**: FastAPI提供高并发处理能力
3. **可扩展性**: 微服务架构支持水平扩展
4. **易部署**: Docker容器化部署简化运维
5. **监控完善**: 集成日志和性能监控系统