"""
应用配置管理类
通过环境变量或配置文件加载配置参数
"""

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from typing import Optional
import os
from functools import lru_cache


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
    DASHSCOPE_API_KEY: str = Field(..., validation_alias="DASHSCOPE_API_KEY")
    DASHSCOPE_BASE_URL: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        validation_alias="DASHSCOPE_BASE_URL"
    )
    
    # Chroma向量数据库配置
    CHROMA_HOST: str = Field(default="localhost", validation_alias="CHROMA_HOST")
    CHROMA_PORT: int = Field(default=8000, validation_alias="CHROMA_PORT")
    CHROMA_COLLECTION_NAME: str = Field(
        default="customer_service_knowledge",
        validation_alias="CHROMA_COLLECTION_NAME"
    )
    
    # PostgreSQL数据库配置
    DATABASE_URL: str = Field(..., validation_alias="DATABASE_URL")
    
    # Redis配置
    REDIS_HOST: str = Field(default="localhost", validation_alias="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, validation_alias="REDIS_PORT")
    REDIS_DB: int = Field(default=0, validation_alias="REDIS_DB")
    
    # 日志配置
    LOG_LEVEL: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    LOG_FILE: Optional[str] = Field(default=None, validation_alias="LOG_FILE")
    
    # 安全配置
    SECRET_KEY: str = Field(..., validation_alias="SECRET_KEY")
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
    
    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """验证数据库URL格式"""
        if not v.startswith(("postgresql://", "mysql://", "sqlite:///")):
            raise ValueError("DATABASE_URL必须以有效的数据库方案开头")
        return v
    
    @model_validator(mode="after")
    def validate_production_settings(self):
        """验证生产环境配置"""
        if self.APP_ENV == "production" and self.DEBUG:
            raise ValueError("生产环境不应启用DEBUG模式")
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
