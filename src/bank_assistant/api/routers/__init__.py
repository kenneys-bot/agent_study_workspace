"""
API路由模块初始化
"""

from .auth import router as auth_router
from .knowledge import install_knowledge_error_handler
from .knowledge import router as knowledge_router
from .knowledge_base import router as knowledge_base_router
from .script_recommender import router as script_recommender_router
from .quality_inspector import router as quality_inspector_router

__all__ = [
    "auth_router",
    "install_knowledge_error_handler",
    "knowledge_base_router",
    "knowledge_router",
    "script_recommender_router",
    "quality_inspector_router",
]
