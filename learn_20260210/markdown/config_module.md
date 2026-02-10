# 配置管理模块设计

## 概述

配置管理模块负责管理大模型客服助手应用的所有配置参数，包括环境变量加载、配置验证、动态更新等功能。该模块基于python-dotenv和Pydantic实现，确保配置的安全性和可靠性。

## 设计原则

1. **安全性**: 敏感信息通过环境变量管理，不在代码中硬编码
2. **灵活性**: 支持多种配置源（环境变量、配置文件、默认值）
3. **可验证性**: 配置参数类型和值范围验证
4. **可扩展性**: 易于添加新的配置项
5. **文档化**: 配置项有清晰的文档说明

## 核心类设计

### Settings 类

```python
from pydantic import BaseSettings, Field, validator
from typing import Optional
import os

class Settings(BaseSettings):
    """
    应用配置管理类
    通过环境变量或配置文件加载配置参数
    """
    
    # 应用配置
    APP_NAME: str = "Customer Service AI"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = Field(default="development", env="APP_ENV")
    DEBUG: bool = Field(default=False, env="DEBUG")
    
    # 服务器配置
    APP_HOST: str = Field(default="0.0.0.0", env="APP_HOST")
    APP_PORT: int = Field(default=8000, env="APP_PORT")
    
    # 通义千问大模型配置
    DASHSCOPE_API_KEY: str = Field(..., env="DASHSCOPE_API_KEY")
    DASHSCOPE_BASE_URL: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1", 
        env="DASHSCOPE_BASE_URL"
    )
    
    # Chroma向量数据库配置
    CHROMA_HOST: str = Field(default="localhost", env="CHROMA_HOST")
    CHROMA_PORT: int = Field(default=8000, env="CHROMA_PORT")
    CHROMA_COLLECTION_NAME: str = Field(
        default="customer_service_knowledge", 
        env="CHROMA_COLLECTION_NAME"
    )
    
    # PostgreSQL数据库配置
    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    
    # Redis配置
    REDIS_HOST: str = Field(default="localhost", env="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, env="REDIS_PORT")
    REDIS_DB: int = Field(default=0, env="REDIS_DB")
    
    # 日志配置
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FILE: Optional[str] = Field(default=None, env="LOG_FILE")
    
    # 安全配置
    SECRET_KEY: str = Field(..., env="SECRET_KEY")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30, 
        env="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    
    # 大模型参数配置
    DEFAULT_MODEL: str = Field(default="qwen-plus", env="DEFAULT_MODEL")
    DEFAULT_TEMPERATURE: float = Field(default=0.7, env="DEFAULT_TEMPERATURE")
    DEFAULT_MAX_TOKENS: int = Field(default=2048, env="DEFAULT_MAX_TOKENS")
    DEFAULT_TOP_P: float = Field(default=0.9, env="DEFAULT_TOP_P")
    
    class Config:
        # 环境变量文件
        env_file = ".env"
        # 环境变量文件编码
        env_file_encoding = "utf-8"
        # 配置项大小写敏感
        case_sensitive = False
    
    @validator("APP_ENV")
    def validate_app_env(cls, v):
        """验证应用环境配置"""
        allowed_envs = ["development", "testing", "staging", "production"]
        if v not in allowed_envs:
            raise ValueError(f"APP_ENV must be one of {allowed_envs}")
        return v
    
    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        """验证日志级别配置"""
        allowed_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed_levels:
            raise ValueError(f"LOG_LEVEL must be one of {allowed_levels}")
        return v.upper()
    
    @validator("DATABASE_URL")
    def validate_database_url(cls, v):
        """验证数据库URL格式"""
        if not v.startswith(("postgresql://", "mysql://", "sqlite:///")):
            raise ValueError("DATABASE_URL must start with a valid database scheme")
        return v

# 全局配置实例
settings = Settings()
```

## 配置加载机制

### 环境变量优先级
1. 系统环境变量（最高优先级）
2. `.env` 文件中的环境变量
3. 默认值（最低优先级）

### 配置文件示例

#### .env 文件
```env
# 应用配置
APP_ENV=development
DEBUG=true

# 服务器配置
APP_HOST=0.0.0.0
APP_PORT=8000

# 通义千问大模型配置
DASHSCOPE_API_KEY=your_dashscope_api_key_here
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1

# Chroma向量数据库配置
CHROMA_HOST=localhost
CHROMA_PORT=8000
CHROMA_COLLECTION_NAME=customer_service_knowledge

# 数据库配置
DATABASE_URL=postgresql://postgres:password@localhost:5432/customer_service

# Redis配置
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=/var/log/customer_service_ai.log

# 安全配置
SECRET_KEY=your_secret_key_here
ACCESS_TOKEN_EXPIRE_MINUTES=30

# 大模型参数配置
DEFAULT_MODEL=qwen-plus
DEFAULT_TEMPERATURE=0.7
DEFAULT_MAX_TOKENS=2048
DEFAULT_TOP_P=0.9
```

## 配置验证

### 必需配置项
- `DASHSCOPE_API_KEY`: 通义千问API密钥
- `DATABASE_URL`: 数据库连接URL
- `SECRET_KEY`: 应用密钥

### 类型验证
- 端口号必须是1-65535之间的整数
- 布尔值配置项必须是有效的布尔值
- URL配置项必须符合URL格式

### 范围验证
- 温度参数必须在0-1之间
- top_p参数必须在0-1之间
- max_tokens参数必须是正整数

## 动态配置更新

### 配置热更新
```python
import os
from pydantic import BaseSettings

class DynamicSettings(Settings):
    """支持动态更新的配置类"""
    
    def reload(self):
        """重新加载配置"""
        # 重新加载环境变量
        if os.path.exists(".env"):
            from dotenv import load_dotenv
            load_dotenv(".env", override=True)
        
        # 重新初始化配置
        super().__init__()
    
    def update_setting(self, key: str, value):
        """更新单个配置项"""
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            raise AttributeError(f"Setting '{key}' not found")
```

### 配置变更通知
```python
from typing import Callable, List

class ConfigManager:
    """配置管理器"""
    
    def __init__(self):
        self.settings = Settings()
        self._listeners: List[Callable] = []
    
    def add_listener(self, listener: Callable):
        """添加配置变更监听器"""
        self._listeners.append(listener)
    
    def notify_listeners(self):
        """通知所有监听器"""
        for listener in self._listeners:
            listener(self.settings)
    
    def update_config(self, key: str, value):
        """更新配置并通知监听器"""
        self.settings.update_setting(key, value)
        self.notify_listeners()
```

## 配置分环境管理

### 开发环境配置 (development)
```env
APP_ENV=development
DEBUG=true
LOG_LEVEL=DEBUG
```

### 测试环境配置 (testing)
```env
APP_ENV=testing
DEBUG=true
LOG_LEVEL=INFO
```

### 生产环境配置 (production)
```env
APP_ENV=production
DEBUG=false
LOG_LEVEL=WARNING
```

## 安全最佳实践

### 敏感信息保护
1. API密钥等敏感信息通过环境变量注入
2. 配置文件加入.gitignore避免提交到版本控制
3. 使用加密存储敏感配置（生产环境）

### 配置访问控制
```python
class SecureSettings(Settings):
    """安全配置类"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 验证必需的安全配置
        self._validate_security_settings()
    
    def _validate_security_settings(self):
        """验证安全相关配置"""
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        
        if self.APP_ENV == "production" and self.DEBUG:
            raise ValueError("DEBUG should be False in production environment")
```

## 使用示例

### 基本使用
```python
from config.settings import settings

# 访问配置项
print(f"应用名称: {settings.APP_NAME}")
print(f"监听端口: {settings.APP_PORT}")
print(f"数据库URL: {settings.DATABASE_URL}")

# 条件逻辑
if settings.DEBUG:
    print("调试模式已启用")
```

### 在FastAPI中使用
```python
from fastapi import FastAPI
from config.settings import settings

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG
)

@app.get("/config")
async def get_config():
    return {
        "app_name": settings.APP_NAME,
        "app_env": settings.APP_ENV,
        "debug": settings.DEBUG
    }
```

### 在数据库连接中使用
```python
from sqlalchemy import create_engine
from config.settings import settings

# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True
)
```

## 监控和日志

### 配置访问日志
```python
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

def log_config_access():
    """记录配置访问日志"""
    logger.info(f"应用配置加载完成: {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"运行环境: {settings.APP_ENV}")
    logger.info(f"监听地址: {settings.APP_HOST}:{settings.APP_PORT}")
```

### 配置健康检查
```python
def check_config_health():
    """检查配置健康状态"""
    issues = []
    
    # 检查必需配置项
    if not settings.DASHSCOPE_API_KEY:
        issues.append("缺少DASHSCOPE_API_KEY配置")
    
    if not settings.DATABASE_URL:
        issues.append("缺少DATABASE_URL配置")
    
    # 检查环境配置
    if settings.APP_ENV == "production" and settings.DEBUG:
        issues.append("生产环境不应启用DEBUG模式")
    
    return issues
```

## 扩展功能

### 配置导出
```python
import json

def export_config():
    """导出当前配置"""
    config_dict = settings.dict()
    # 移除敏感信息
    sensitive_keys = ["DASHSCOPE_API_KEY", "SECRET_KEY", "DATABASE_URL"]
    for key in sensitive_keys:
        if key in config_dict:
            config_dict[key] = "***"  # 掩码敏感信息
    
    return json.dumps(config_dict, indent=2, ensure_ascii=False)
```

### 配置比较
```python
def compare_configs(config1: Settings, config2: Settings):
    """比较两个配置实例的差异"""
    diff = {}
    dict1 = config1.dict()
    dict2 = config2.dict()
    
    for key in dict1:
        if dict1[key] != dict2.get(key):
            diff[key] = {
                "old": dict1[key],
                "new": dict2.get(key)
            }
    
    return diff
```

## 故障排除

### 常见问题

1. **环境变量未加载**:
   - 检查.env文件是否存在且格式正确
   - 确认环境变量名称是否匹配

2. **配置验证失败**:
   - 检查配置值是否符合验证规则
   - 查看错误日志获取详细信息

3. **必需配置项缺失**:
   - 确认所有必需的环境变量已设置
   - 检查配置文件是否完整

### 调试技巧
```python
# 调试配置加载
import os
from config.settings import settings

# 打印所有环境变量
for key, value in os.environ.items():
    if any(keyword in key.upper() for keyword in ["APP", "API", "DATABASE", "REDIS"]):
        print(f"{key}: {value}")

# 打印Pydantic配置模型
print(settings.schema_json(indent=2))