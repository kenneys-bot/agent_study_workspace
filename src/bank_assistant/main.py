"""
大模型客服助手 - 应用入口
基于FastAPI的Web服务
"""

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from bank_assistant.config.settings import settings
from bank_assistant.config.constants import API_PREFIX
from bank_assistant.infra.db import ping_database
from bank_assistant.utils.logger import LoggerConfig
from bank_assistant.api.errors import (
    ApiError,
    TraceIdMiddleware,
    error_response,
    install_error_handlers,
    resolve_trace_id,
)
from bank_assistant.observability.http import RequestObservabilityMiddleware
from bank_assistant.observability.metrics import (
    METRICS,
    TASK_QUEUE_DEPTH,
    collect_task_queue_depth,
    record_db_probe,
)
from bank_assistant.security.input_limits import RequestSizeLimitMiddleware
from bank_assistant.api.routers import (
    auth_router,
    conversations_router,
    inspections_router,
    install_knowledge_error_handler,
    install_rules_error_handler,
    knowledge_base_router,
    knowledge_router,
    script_recommender_router,
    quality_inspector_router,
    reports_router,
    reviews_router,
    rules_router,
    suggestions_router,
    system_router,
    tasks_router,
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


# 中间件：trace_id 必须覆盖所有响应（含错误与 CORS 预检），因此放在最外层；
# CORS 同时暴露 x-trace-id，便于前端把错误与后端日志关联。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为特定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-trace-id"],
)
# add_middleware 是“后加的更靠外”。目标顺序（由外到内）：
# TraceId → 请求观测（指标/结构化日志）→ 大小上限 → CORS → 路由。
# 观测层放在 TraceId 内层才能读到 trace_id；大小上限放在观测层内层，使 413
# 也会被计数和记录（但不读取 body）。
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RequestObservabilityMiddleware)
app.add_middleware(TraceIdMiddleware)


# 异常处理：所有端点统一使用契约 §1 的错误结构（T08）
install_error_handlers(app)
# 知识生命周期领域错误（状态冲突、自审、revision 冲突）也走同一错误契约。
install_knowledge_error_handler(app)
# 规则生命周期领域错误（状态冲突、已发布内容不可改、内容非法）走同一错误契约。
install_rules_error_handler(app)


# 注册路由
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(conversations_router, prefix=API_PREFIX)
app.include_router(knowledge_router, prefix=API_PREFIX)
app.include_router(rules_router, prefix=API_PREFIX)
app.include_router(knowledge_base_router, prefix=API_PREFIX)
app.include_router(script_recommender_router, prefix=API_PREFIX)
app.include_router(quality_inspector_router, prefix=API_PREFIX)
app.include_router(tasks_router, prefix=API_PREFIX)
app.include_router(inspections_router, prefix=API_PREFIX)
app.include_router(reviews_router, prefix=API_PREFIX)
app.include_router(reports_router, prefix=API_PREFIX)
app.include_router(suggestions_router, prefix=API_PREFIX)
app.include_router(system_router, prefix=API_PREFIX)


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


# 存活探针：只看进程，不依赖数据库等外部资源（T09）
@app.get("/health/live")
async def health_live():
    """进程存活"""
    return {"status": "alive"}


# 就绪探针：真实 SELECT 1 成功才 200，依赖不可用返回标准 503 错误结构（T09）
@app.get("/health/ready")
async def health_ready(request: Request):
    """必需依赖（PostgreSQL）是否可用"""
    trace_id = resolve_trace_id(request)
    try:
        await run_in_threadpool(ping_database)
    except Exception as exc:  # 连接失败、超时、驱动错误都视为不可用
        record_db_probe(False)
        logger.warning("readiness 检查失败: %s", type(exc).__name__)
        return error_response(
            status_code=503,
            code="DEPENDENCY_UNAVAILABLE",
            message="数据库暂不可用",
            request=request,
            retryable=True,
            details={"dependency": "postgresql"},
        )
    record_db_probe(True)
    return {
        "status": "ready",
        "checks": {"database": "ok"},
        "trace_id": trace_id,
    }


# 指标抓取：Prometheus 文本格式。只绑服务器回环（compose 的 --host 127.0.0.1），
# 默认是内部端点；需要跨主机抓取时配置 METRICS_TOKEN 并要求 Bearer 令牌。
# 指标只含聚合计数/延迟/队列深度/数据库就绪，不含会话内容、用户 id 或凭据。
@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus 文本指标；数据库不可达时仍可抓取（bank_db_ready=0）。"""
    if settings.METRICS_TOKEN:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(
            supplied, f"Bearer {settings.METRICS_TOKEN}"
        ):
            raise ApiError(
                status_code=401,
                code="UNAUTHENTICATED",
                message="指标端点需要令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )

    try:
        await run_in_threadpool(ping_database)
    except Exception:  # 不因数据库故障让监控端点失败
        record_db_probe(False)
    else:
        record_db_probe(True)

    depth = await run_in_threadpool(collect_task_queue_depth)
    # 固定输出契约 §3 的全部状态，队列为空时也暴露 0，便于告警区分
    # “没有任务”和“指标缺失”。
    for status_name in ("queued", "running", "succeeded", "failed", "cancelled"):
        TASK_QUEUE_DEPTH.set(
            depth.get(status_name, 0),
            {"task_type": "inspection", "status": status_name},
        )

    return PlainTextResponse(
        METRICS.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


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
