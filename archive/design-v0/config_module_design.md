# 核心配置管理模块设计

## 模块概述

核心配置管理模块负责管理大模型客服助手应用的所有配置参数，包括：
- 大模型API密钥和端点配置
- 向量数据库连接配置
- 应用运行参数
- 日志配置
- 安全配置

## 配置类设计

### Settings 类

```python
class Settings:
    """
    应用配置管理类
    通过环境变量或配置文件加载配置参数
    """
    
    # 大模型配置
    DASHSCOPE_API_KEY: str          # 通义千问API密钥
    DASHSCOPE_BASE_URL: str         # 通义千问API基础URL
    
    # 向量数据库配置
    CHROMA_HOST: str                # Chroma数据库主机地址
    CHROMA_PORT: int                # Chroma数据库端口
    CHROMA_COLLECTION_NAME: str     # Chroma集合名称
    
    # 数据库配置
    DATABASE_URL: str               # PostgreSQL数据库连接URL
    
    # Redis配置
    REDIS_HOST: str                 # Redis主机地址
    REDIS_PORT: int                 # Redis端口
    REDIS_DB: int                   # Redis数据库编号
    
    # 应用配置
    APP_HOST: str                   # 应用监听主机地址
    APP_PORT: int                   # 应用监听端口
    DEBUG: bool                     # 调试模式开关
    
    # 日志配置
    LOG_LEVEL: str                  # 日志级别
    LOG_FILE: str                   # 日志文件路径
    
    # 安全配置
    SECRET_KEY: str                 # 应用密钥
    ACCESS_TOKEN_EXPIRE_MINUTES: int # 访问令牌过期时间(分钟)
```

## 配置加载机制

### 环境变量优先级
1. 系统环境变量
2. `.env` 文件中的环境变量
3. 默认值

### 配置验证
- 必需配置项验证
- 配置值类型验证
- 配置值范围验证

## 常量定义

### 大模型相关常量
```python
# 模型名称常量
QWEN_PLUS = "qwen-plus"
QWEN_TURBO = "qwen-turbo"
QWEN_MAX = "qwen-max"

# 默认模型参数
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TOP_P = 0.9
```

### 向量数据库相关常量
```python
# 默认集合配置
DEFAULT_COLLECTION_NAME = "customer_service_knowledge"
DEFAULT_EMBEDDING_MODEL = "text-embedding-ada-002"

# 距离度量方式
DISTANCE_COSINE = "cosine"
DISTANCE_L2 = "l2"
```

### 应用相关常量
```python
# API版本
API_VERSION = "v1"

# 默认分页参数
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# 缓存过期时间
CACHE_EXPIRE_TIME = 3600  # 1小时
```

## 配置文件示例

### .env 文件示例
```env
# 大模型配置
DASHSCOPE_API_KEY=your_dashscope_api_key_here
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1

# 向量数据库配置
CHROMA_HOST=localhost
CHROMA_PORT=8000
CHROMA_COLLECTION_NAME=customer_service_knowledge

# 数据库配置
DATABASE_URL=postgresql://user:password@localhost:5432/customer_service

# Redis配置
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# 应用配置
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=True

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=/var/log/customer_service_ai.log

# 安全配置
SECRET_KEY=your_secret_key_here
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

## 使用示例

### 在代码中使用配置
```python
from config.settings import settings

# 使用大模型配置
api_key = settings.DASHSCOPE_API_KEY
base_url = settings.DASHSCOPE_BASE_URL

# 使用向量数据库配置
chroma_host = settings.CHROMA_HOST
chroma_port = settings.CHROMA_PORT
```

## 配置更新机制

### 动态配置更新
- 支持运行时更新部分配置参数
- 配置变更通知机制
- 配置版本管理

### 配置持久化
- 配置变更历史记录
- 配置备份与恢复
- 配置导出与导入

## 安全考虑

### 敏感信息保护
- API密钥等敏感信息加密存储
- 环境变量方式注入敏感配置
- 配置文件权限控制

### 配置访问控制
- 不同环境的配置隔离
- 配置项访问权限控制
- 配置变更审计日志