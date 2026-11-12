"""
应用配置管理类
通过环境变量或配置文件加载配置参数
"""

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

# 开发/测试专用的显式默认值：只用于本地启动，生产/预发布缺失 SECRET_KEY 必须失败。
# 名称本身带 dev-only，避免被误认为可用的生产密钥。
DEV_ONLY_SECRET_KEY = "dev-only-insecure-secret-key-change-me"


class Settings(BaseSettings):
    """
    应用配置管理类
    通过环境变量或配置文件加载配置参数
    """
    
    # 应用配置
    APP_NAME: str = "Customer Service AI"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = Field(default="development", validation_alias="APP_ENV")
    DEBUG: bool = Field(default=False, validation_alias="DEBUG")
    
    # 服务器配置
    APP_HOST: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    APP_PORT: int = Field(default=8000, validation_alias="APP_PORT")
    
    # 通义千问大模型配置
    # 旧 DashScope 适配层不是生成主链（ADR-0004 改为 DeepSeek 官方）：
    # 凭据可选；未配置只代表该能力不可用，不应阻止应用启动。
    DASHSCOPE_API_KEY: Optional[str] = Field(
        default=None, validation_alias="DASHSCOPE_API_KEY"
    )
    DASHSCOPE_BASE_URL: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        validation_alias="DASHSCOPE_BASE_URL"
    )
    
    # 统一模型适配层（T05）：生成主用 DeepSeek 官方，第三方中继仅作对比/回退
    DEEPSEEK_API_KEY: Optional[str] = Field(
        default=None, validation_alias="DEEPSEEK_API_KEY"
    )
    DEEPSEEK_BASE_URL: str = Field(
        default="https://api.deepseek.com/v1", validation_alias="DEEPSEEK_BASE_URL"
    )
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    LLM_BASE_URL: Optional[str] = Field(default=None, validation_alias="LLM_BASE_URL")
    LLM_API_KEY: Optional[str] = Field(default=None, validation_alias="LLM_API_KEY")
    LLM_MODEL_CHAT: Optional[str] = Field(default=None, validation_alias="LLM_MODEL_CHAT")
    MODEL_PROVIDER: str = Field(default="deepseek", validation_alias="MODEL_PROVIDER")
    MODEL_FALLBACK_PROVIDER: Optional[str] = Field(
        default=None, validation_alias="MODEL_FALLBACK_PROVIDER"
    )
    # 运行模式：live 调用真实模型；replay 只用于显式回放。版本页如实展示，
    # 不允许把回放结果伪装成真实模型结论。
    MODEL_MODE: str = Field(default="live", validation_alias="MODEL_MODE")
    LLM_TIMEOUT_SECONDS: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_SECONDS")
    LLM_MAX_RETRIES: int = Field(default=2, validation_alias="LLM_MAX_RETRIES")
    LLM_MAX_TOKENS_CHAT: int = Field(default=3000, validation_alias="LLM_MAX_TOKENS_CHAT")

    # Chroma向量数据库配置
    CHROMA_HOST: str = Field(default="localhost", validation_alias="CHROMA_HOST")
    CHROMA_PORT: int = Field(default=8000, validation_alias="CHROMA_PORT")
    CHROMA_COLLECTION_NAME: str = Field(
        default="customer_service_knowledge",
        validation_alias="CHROMA_COLLECTION_NAME"
    )
    
    # PostgreSQL数据库配置
    DATABASE_URL: str = Field(..., validation_alias="DATABASE_URL")

    # 演示知识与版本化索引（M3，ADR-0005）：索引产物默认落在仓库内 data/index，
    # 已被 .gitignore 忽略；脚本只读取这里，不硬编码路径。
    DEMO_SCOPE_ID: str = Field(default="demo-bank", validation_alias="DEMO_SCOPE_ID")
    INDEX_DIR: str = Field(default="data/index", validation_alias="INDEX_DIR")
    EMBEDDING_PROVIDER: str = Field(
        default="fastembed", validation_alias="EMBEDDING_PROVIDER"
    )
    EMBEDDING_MODEL_NAME: str = Field(
        default="BAAI/bge-small-zh-v1.5",
        validation_alias="EMBEDDING_MODEL_NAME",
    )

    # 任务队列（契约 §3）：重试上限默认 3；worker 租约默认 60 秒。
    TASK_MAX_ATTEMPTS: int = Field(
        default=3, ge=1, validation_alias="TASK_MAX_ATTEMPTS"
    )
    TASK_DEFAULT_LEASE_SECONDS: int = Field(
        default=60, ge=1, validation_alias="TASK_DEFAULT_LEASE_SECONDS"
    )
    
    # Redis配置
    REDIS_HOST: str = Field(default="localhost", validation_alias="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, validation_alias="REDIS_PORT")
    REDIS_DB: int = Field(default=0, validation_alias="REDIS_DB")
    
    # 日志配置
    LOG_LEVEL: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    LOG_FILE: Optional[str] = Field(default=None, validation_alias="LOG_FILE")
    
    # 安全配置
    # 仅在启用认证/签名的环境必填；development/testing 由下面的模型校验
    # 填入 DEV_ONLY_SECRET_KEY 并打警告，production/staging 缺失则启动失败。
    SECRET_KEY: Optional[str] = Field(default=None, validation_alias="SECRET_KEY")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    
    # 大模型参数配置
    DEFAULT_MODEL: str = Field(default="qwen-plus", validation_alias="DEFAULT_MODEL")
    DEFAULT_TEMPERATURE: float = Field(default=0.7, validation_alias="DEFAULT_TEMPERATURE")
    DEFAULT_MAX_TOKENS: int = Field(default=2048, validation_alias="DEFAULT_MAX_TOKENS")
    DEFAULT_TOP_P: float = Field(default=0.9, validation_alias="DEFAULT_TOP_P")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # M0/T02：本仓库 .env 含多套模型的变量名（DEEPSEEK_* / LLM_* / EMBEDDING_*），
        # 配置对象只声明自己关心的项，其余忽略；真正的模型配置在 T05 统一适配层里读取。
        extra = "ignore"
        validation_alias_generator = lambda x: x.upper()
    
    @field_validator("APP_ENV")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        """验证应用环境配置"""
        allowed_envs = ["development", "testing", "staging", "production"]
        if v not in allowed_envs:
            raise ValueError(f"APP_ENV必须为以下值之一: {allowed_envs}")
        return v
    
    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """验证日志级别配置"""
        allowed_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed_levels:
            raise ValueError(f"LOG_LEVEL必须为以下值之一: {allowed_levels}")
        return v.upper()

    @field_validator("MODEL_MODE")
    @classmethod
    def validate_model_mode(cls, v: str) -> str:
        """运行模式只允许 live / replay，避免版本页展示无法解释的值。"""
        allowed_modes = ["live", "replay"]
        if v.lower() not in allowed_modes:
            raise ValueError(f"MODEL_MODE必须为以下值之一: {allowed_modes}")
        return v.lower()
    
    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """验证数据库URL格式"""
        if not v.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+asyncpg://", "mysql://", "sqlite:///")):
            raise ValueError("DATABASE_URL必须以有效的数据库方案开头")
        return v
    
    @field_validator("DASHSCOPE_API_KEY", "SECRET_KEY", mode="before")
    @classmethod
    def empty_string_as_none(cls, v):
        """空字符串视为未配置，避免 "" 被当成有效凭据。"""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def validate_production_settings(self):
        """验证生产环境配置，并决定 SECRET_KEY 的必填/开发默认行为。"""
        if self.APP_ENV == "production" and self.DEBUG:
            raise ValueError("生产环境不应启用DEBUG模式")

        if not self.SECRET_KEY:
            if self.APP_ENV in ("development", "testing"):
                self.SECRET_KEY = DEV_ONLY_SECRET_KEY
                logger.warning(
                    "SECRET_KEY 未配置：APP_ENV=%s 使用显式 dev-only 默认值；"
                    "生产/预发布环境必须配置真实 SECRET_KEY",
                    self.APP_ENV,
                )
            else:
                raise ValueError(
                    f"SECRET_KEY 在 APP_ENV={self.APP_ENV} 下必须配置："
                    "请设置环境变量 SECRET_KEY（开发/测试环境才允许 dev-only 默认值）"
                )
        return self


@lru_cache()
def get_settings() -> Settings:
    """
    获取应用配置单例
    
    Returns:
        Settings: 应用配置实例
    """
    return Settings()


# 全局配置实例
settings = get_settings()
