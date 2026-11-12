"""
API路由模块初始化
"""

from .knowledge_base import router as knowledge_base_router
from .script_recommender import router as script_recommender_router
from .quality_inspector import router as quality_inspector_router

__all__ = [
    "knowledge_base_router",
    "script_recommender_router",
    "quality_inspector_router"
]
