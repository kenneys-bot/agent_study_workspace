"""
通用工具函数模块
提供项目中常用的工具函数
"""

import re
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import aiofiles


class DateTimeUtils:
    """日期时间工具类"""
    
    @staticmethod
    def now() -> datetime:
        """获取当前时间"""
        return datetime.now()
    
    @staticmethod
    def utc_now() -> datetime:
        """获取当前UTC时间"""
        return datetime.utcnow()
    
    @staticmethod
    def format_datetime(
        dt: datetime = None,
        format: str = "%Y-%m-%d %H:%M:%S"
    ) -> str:
        """
        格式化日期时间
        
        Args:
            dt (datetime): 日期时间对象
            format (str): 格式字符串
            
        Returns:
            str: 格式化后的日期时间字符串
        """
        dt = dt or datetime.now()
        return dt.strftime(format)
    
    @staticmethod
    def parse_datetime(
        datetime_str: str,
        format: str = "%Y-%m-%d %H:%M:%S"
    ) -> datetime:
        """
        解析日期时间字符串
        
        Args:
            datetime_str (str): 日期时间字符串
            format (str): 格式字符串
            
        Returns:
            datetime: 日期时间对象
        """
        return datetime.strptime(datetime_str, format)
    
    @staticmethod
    def add_days(dt: datetime = None, days: int = 0) -> datetime:
        """
        添加天数
        
        Args:
            dt (datetime): 起始日期
            days (int): 添加的天数
            
        Returns:
            datetime: 结果日期
        """
        dt = dt or datetime.now()
        return dt + timedelta(days=days)
    
    @staticmethod
    def get_timestamp() -> float:
        """获取时间戳"""
        return datetime.now().timestamp()


class StringUtils:
    """字符串工具类"""
    
    @staticmethod
    def is_empty(s: str) -> bool:
        """检查字符串是否为空"""
        return s is None or len(s.strip()) == 0
    
    @staticmethod
    def truncate(
        s: str,
        max_length: int,
        suffix: str = "..."
    ) -> str:
        """
        截断字符串
        
        Args:
            s (str): 原始字符串
            max_length (int): 最大长度
            suffix (str): 后缀
            
        Returns:
            str: 截断后的字符串
        """
        if len(s) <= max_length:
            return s
        return s[:max_length - len(suffix)] + suffix
    
    @staticmethod
    def normalize_whitespace(s: str) -> str:
        """标准化空白字符"""
        return re.sub(r'\s+', ' ', s).strip()
    
    @staticmethod
    def remove_special_chars(s: str) -> str:
        """移除特殊字符"""
        return re.sub(r'[^\w\s]', '', s)
    
    @staticmethod
    def to_camel_case(s: str) -> str:
        """
        转换为驼峰命名
        
        Args:
            s (str): 下划线分隔的字符串
            
        Returns:
            str: 驼峰命名字符串
        """
        components = s.split('_')
        return components[0] + ''.join(x.title() for x in components[1:])
    
    @staticmethod
    def to_snake_case(s: str) -> str:
        """
        转换为下划线命名
        
        Args:
            s (str): 驼峰命名字符串
            
        Returns:
            str: 下划线命名字符串
        """
        return re.sub(r'(?<!^)(?=[A-Z])', '_', s).lower()
    
    @staticmethod
    def mask_sensitive_info(
        s: str,
        pattern: str = None,
        mask_char: str = '*'
    ) -> str:
        """
        脱敏敏感信息
        
        Args:
            s (str): 原始字符串
            pattern (str): 正则表达式模式
            mask_char (str): 掩码字符
            
        Returns:
            str: 脱敏后的字符串
        """
        if pattern is None:
            # 默认脱敏手机号和身份证号
            patterns = [
                (r'(\d{3})\d{4}(\d{4})', r'\1****\2'),  # 手机号
                (r'(\d{3})\d{14}(\w)', r'\1**************\2'),  # 身份证号
            ]
            for p, r in patterns:
                s = re.sub(p, r, s)
        else:
            s = re.sub(pattern, mask_char * len(re.findall(pattern, s)[0]) if re.findall(pattern, s) else '', s)
        return s


class FileUtils:
    """文件工具类"""
    
    @staticmethod
    def read_file(file_path: str, encoding: str = "utf-8") -> str:
        """
        读取文件
        
        Args:
            file_path (str): 文件路径
            encoding (str): 编码
            
        Returns:
            str: 文件内容
        """
        with open(file_path, 'r', encoding=encoding) as f:
            return f.read()
    
    @staticmethod
    async def read_file_async(
        file_path: str,
        encoding: str = "utf-8"
    ) -> str:
        """
        异步读取文件
        
        Args:
            file_path (str): 文件路径
            encoding (str): 编码
            
        Returns:
            str: 文件内容
        """
        async with aiofiles.open(file_path, 'r', encoding=encoding) as f:
            return await f.read()
    
    @staticmethod
    def write_file(
        file_path: str,
        content: str,
        encoding: str = "utf-8",
        mode: str = "w"
    ) -> bool:
        """
        写入文件
        
        Args:
            file_path (str): 文件路径
            content (str): 内容
            encoding (str): 编码
            mode (str): 写入模式
            
        Returns:
            bool: 是否成功
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, mode, encoding=encoding) as f:
            f.write(content)
        return True
    
    @staticmethod
    def ensure_dir(dir_path: str) -> bool:
        """
        确保目录存在
        
        Args:
            dir_path (str): 目录路径
            
        Returns:
            bool: 是否成功
        """
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        return True
    
    @staticmethod
    def get_file_extension(file_path: str) -> str:
        """获取文件扩展名"""
        return Path(file_path).suffix.lower()
    
    @staticmethod
    def get_file_size(file_path: str) -> int:
        """获取文件大小（字节）"""
        return Path(file_path).stat().st_size


class JSONUtils:
    """JSON工具类"""
    
    @staticmethod
    def to_json(obj: Any, ensure_ascii: bool = False) -> str:
        """
        转换为JSON字符串
        
        Args:
            obj: 对象
            ensure_ascii (bool): 是否保留非ASCII字符
            
        Returns:
            str: JSON字符串
        """
        return json.dumps(
            obj,
            ensure_ascii=ensure_ascii,
            indent=2,
            default=str
        )
    
    @staticmethod
    def from_json(json_str: str) -> Any:
        """
        从JSON字符串解析
        
        Args:
            json_str (str): JSON字符串
            
        Returns:
            Any: 解析后的对象
        """
        return json.loads(json_str)
    
    @staticmethod
    def to_dict(obj: Any) -> Dict[str, Any]:
        """
        转换为字典
        
        Args:
            obj: 对象
            
        Returns:
            Dict[str, Any]: 字典
        """
        if isinstance(obj, dict):
            return obj
        return json.loads(json.dumps(obj, default=str))


class CryptoUtils:
    """加密工具类"""
    
    @staticmethod
    def md5(s: str) -> str:
        """MD5加密"""
        return hashlib.md5(s.encode()).hexdigest()
    
    @staticmethod
    def sha256(s: str) -> str:
        """SHA256加密"""
        return hashlib.sha256(s.encode()).hexdigest()
    
    @staticmethod
    def generate_uuid() -> str:
        """生成UUID"""
        return str(uuid.uuid4())
    
    @staticmethod
    def generate_short_id(length: int = 8) -> str:
        """生成短ID"""
        return uuid.uuid4().hex[:length]


class ValidationUtils:
    """验证工具类"""
    
    @staticmethod
    def is_valid_phone(phone: str) -> bool:
        """验证手机号"""
        pattern = r'^1[3-9]\d{9}$'
        return re.match(pattern, phone) is not None
    
    @staticmethod
    def is_valid_email(email: str) -> bool:
        """验证邮箱"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    @staticmethod
    def is_valid_id_card(id_card: str) -> bool:
        """验证身份证号"""
        pattern = r'^[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[1-2]\d|3[0-1])\d{3}(\d|X|x)$'
        return re.match(pattern, id_card) is not None
    
    @staticmethod
    def is_valid_url(url: str) -> bool:
        """验证URL"""
        pattern = r'^https?://[\w\-_]+(\.[\w\-_]+)+(/[\w\-_./?%&=]*)?$'
        return re.match(pattern, url) is not None


class ListUtils:
    """列表工具类"""
    
    @staticmethod
    def chunk(lst: List[Any], size: int) -> List[List[Any]]:
        """
        分割列表
        
        Args:
            lst (List[Any]): 原始列表
            size (int): 每个子列表的大小
            
        Returns:
            List[List[Any]]: 分割后的列表
        """
        return [lst[i:i + size] for i in range(0, len(lst), size)]
    
    @staticmethod
    def remove_duplicates(
        lst: List[Any],
        key: callable = None
    ) -> List[Any]:
        """
        去重
        
        Args:
            lst (List[Any]): 原始列表
            key (callable): 去重key函数
            
        Returns:
            List[Any]: 去重后的列表
        """
        seen = set()
        result = []
        for item in lst:
            k = key(item) if key else item
            if k not in seen:
                seen.add(k)
                result.append(item)
        return result
    
    @staticmethod
    def group_by(
        lst: List[Any],
        key: callable
    ) -> Dict[Any, List[Any]]:
        """
        分组
        
        Args:
            lst (List[Any]): 原始列表
            key (callable): 分组key函数
            
        Returns:
            Dict[Any, List[Any]]: 分组后的字典
        """
        result = {}
        for item in lst:
            k = key(item)
            if k not in result:
                result[k] = []
            result[k].append(item)
        return result
