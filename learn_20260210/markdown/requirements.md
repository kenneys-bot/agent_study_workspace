# 项目依赖说明

## 核心依赖

### 大模型相关
- `langchain>=0.1.0`: 构建大模型应用的框架
- `langchain-community>=0.0.15`: LangChain社区组件
- `langchain-core>=0.1.10`: LangChain核心组件
- `dashscope>=1.14.0`: 阿里云通义千问API的Python SDK

### 向量数据库
- `chromadb>=0.4.0`: Chroma向量数据库客户端

### Web框架
- `fastapi>=0.100.0`: 现代、快速(高性能)的Web框架
- `uvicorn>=0.23.0`: ASGI服务器实现，用于运行FastAPI应用
- `pydantic>=2.0.0`: 数据验证和设置管理

### 数据库
- `sqlalchemy>=2.0.0`: Python SQL工具包和对象关系映射器
- `psycopg2-binary>=2.9.0`: PostgreSQL数据库适配器
- `redis>=4.5.0`: Redis Python客户端

### 异步任务处理
- `celery>=5.3.0`: 分布式任务队列

## 配置管理
- `python-dotenv>=1.0.0`: 从.env文件加载环境变量

## 工具库
- `numpy>=1.24.0`: Python科学计算基础库
- `pandas>=2.0.0`: 数据分析和操作库
- `requests>=2.31.0`: HTTP库，用于发送HTTP请求

## 异步处理
- `asyncio-mqtt>=0.16.0`: MQTT协议的异步客户端
- `aiofiles>=23.0.0`: 异步文件操作库

## 日志和监控
- `loguru>=0.7.0`: Python日志库
- `prometheus-client>=0.17.0`: Prometheus监控客户端

## 开发依赖

### 测试工具
- `pytest>=7.0.0`: Python测试框架
- `pytest-asyncio>=0.21.0`: pytest的异步支持插件
- `pytest-benchmark>=4.0.0`: pytest性能基准测试插件
- `httpx>=0.24.0`: 异步HTTP客户端，用于测试FastAPI应用
- `pytest-cov>=4.0.0`: pytest覆盖率插件
- `locust>=2.15.0`: 负载测试工具

### 代码质量工具
- `black>=23.0.0`: Python代码格式化工具
- `flake8>=6.0.0`: Python代码检查工具
- `mypy>=1.0.0`: Python静态类型检查工具
- `isort>=5.12.0`: Python导入语句排序工具

### 安全检查工具
- `bandit>=1.7.0`: Python安全漏洞扫描工具
- `safety>=2.0.0`: 检查依赖项安全漏洞的工具

## 部署依赖
- `gunicorn>=21.0.0`: Python WSGI HTTP服务器
- `docker>=6.0.0`: Docker SDK for Python

## 文档生成
- `mkdocs>=1.5.0`: 项目文档生成工具
- `mkdocs-material>=9.0.0`: Material Design主题的MkDocs

## 安装说明

### 安装所有依赖
```bash
pip install -r requirements.txt
```

### 分别安装核心依赖和开发依赖
```bash
# 安装核心依赖
pip install langchain langchain-community dashscope chromadb fastapi uvicorn pydantic sqlalchemy psycopg2-binary redis celery python-dotenv numpy pandas requests asyncio-mqtt aiofiles loguru prometheus-client

# 安装开发依赖
pip install pytest pytest-asyncio pytest-benchmark black flake8 mypy isort bandit safety gunicorn docker httpx pytest-cov locust mkdocs mkdocs-material
```

## 依赖版本管理

### 版本锁定
建议在生产环境中使用固定版本号以确保环境一致性。

### 安全更新
定期使用`safety check`命令检查依赖项的安全漏洞，并及时更新。

### 兼容性检查
在更新依赖版本前，应运行完整的测试套件以确保兼容性。