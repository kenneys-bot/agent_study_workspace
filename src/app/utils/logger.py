"""
日志工具模块
提供统一的日志配置和管理功能
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from loguru import logger
from pythonjsonlogger import jsonlogger

from config.settings import settings
from config.constants import LOG_FORMAT, LOG_ROTATION, LOG_RETENTION, LOG_COMPRESSION


class LoggerConfig:
    """日志配置类"""
    
    @staticmethod
    def setup_logger():
        """配置全局日志"""
        # 移除默认处理器
        logger.remove()
        
        # 添加控制台输出
        logger.add(
            sys.stdout,
            format=LOG_FORMAT,
            level=settings.LOG_LEVEL,
            colorize=True
        )
        
        # 添加文件输出（如果配置了日志文件）
        if settings.LOG_FILE:
            log_path = Path(settings.LOG_FILE)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.add(
                str(log_path),
                format=LOG_FORMAT,
                level=settings.LOG_LEVEL,
                rotation=LOG_ROTATION,
                retention=LOG_RETENTION,
                compression=LOG_COMPRESSION,
                encoding="utf-8"
            )
        
        # 配置标准日志记录器
        LoggerConfig._configure_standard_logger()
        
        logger.info(f"日志系统初始化完成，日志级别: {settings.LOG_LEVEL}")
    
    @staticmethod
    def _configure_standard_logger():
        """配置标准日志记录器"""
        standard_logger = logging.getLogger()
        standard_logger.setLevel(settings.LOG_LEVEL)
        
        # 清除现有处理器
        standard_logger.handlers.clear()
        
        # 添加控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(settings.LOG_LEVEL)
        
        # 设置格式
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)
        
        standard_logger.addHandler(console_handler)
    
    @staticmethod
    def add_file_logger(
        log_path: str,
        level: str = "INFO",
        rotation: str = "500 MB",
        retention: str = "10 days"
    ):
        """
        添加文件日志处理器
        
        Args:
            log_path (str): 日志文件路径
            level (str): 日志级别
            rotation (str): 轮转策略
            retention (str): 保留策略
        """
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.add(
            str(path),
            format=LOG_FORMAT,
            level=level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8"
        )
        
        logger.info(f"已添加文件日志: {log_path}")
    
    @staticmethod
    def add_json_logger(log_path: str, level: str = "INFO"):
        """
        添加JSON格式日志处理器
        
        Args:
            log_path (str): 日志文件路径
            level (str): 日志级别
        """
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.add(
            str(path),
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
            level=level,
            serialize=True,
            encoding="utf-8"
        )
        
        logger.info(f"已添加JSON日志: {log_path}")


def get_logger(name: str = None):
    """
    获取日志记录器
    
    Args:
        name (str): 日志记录器名称
        
    Returns:
        logger: 日志记录器
    """
    if name:
        return logger.bind(name=name)
    return logger


class LoggerMixin:
    """日志混入类，为类提供日志功能"""
    
    @property
    def logger(self):
        """获取日志记录器"""
        return get_logger(self.__class__.__name__)
