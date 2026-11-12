"""
API模块初始化
"""

from .routers import (
    knowledge_base_router,
    script_recommender_router,
    quality_inspector_router
)
from .models import *

__all__ = [
    "knowledge_base_router",
    "script_recommender_router",
    "quality_inspector_router"
]
