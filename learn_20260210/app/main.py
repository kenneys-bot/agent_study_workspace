"""
大模型客服助手 - 应用入口
基于FastAPI的Web服务
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.settings import settings
from config.constants import API_PREFIX
from utils.logger import LoggerConfig
from api.routers import (
    knowledge_base_router,
    script_recommender_router,
    quality_inspector_router
)


# 配置日志
LoggerConfig.setup_logger()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 60)
    logger.info("大模型客服助手服务启动中...")
    logger.info(f"应用名称: {settings.APP_NAME}")
    logger.info(f"应用版本: {settings.APP_VERSION}")
    logger.info(f"运行环境: {settings.APP_ENV}")
    logger.info("=" * 60)
    
    yield
    
    # 关闭时执行
    logger.info("大模型客服助手服务关闭")


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="大模型客服助手 - 基于通义千问和Chroma向量数据库的智能客服解决方案",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)


# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为特定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理器"""
    logger.error(f"未捕获的异常: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "内部服务器错误",
            "error": str(exc) if settings.DEBUG else "请稍后重试"
        }
    )


# 注册路由
app.include_router(knowledge_base_router, prefix=API_PREFIX)
app.include_router(script_recommender_router, prefix=API_PREFIX)
app.include_router(quality_inspector_router, prefix=API_PREFIX)


# 根路径
@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs"
    }


# 健康检查
@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION
    }


# 应用信息
@app.get("/info")
async def app_info():
    """应用信息"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "debug": settings.DEBUG,
        "api_prefix": API_PREFIX
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
