"""
对话内容解析器
解析和结构化对话内容
"""

import hashlib
import json
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from bank_assistant.utils.logger import LoggerMixin

logger = logging.getLogger(__name__)


class ConversationParseError(ValueError):
    """非法会话输入；携带稳定错误码，由 API 层映射为 4xx。"""

    http_status = 400

    def __init__(self, code: str, message: str, raw_text: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_text = raw_text


def ensure_utc_datetime(value: Any) -> datetime:
    """把输入统一为带时区的 UTC datetime。

    None/空串取当前时间；naive datetime 与无时区 ISO 字符串按 UTC 解释。
    无法解析或类型不支持时抛 ConversationParseError（对应 4xx），
    绝不把 str 留给 `to_dict()` 触发 AttributeError。
    """
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ConversationParseError(
                "CONVERSATION_INVALID_TIMESTAMP",
                f"无法解析时间戳: {value}"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ConversationParseError(
                "CONVERSATION_INVALID_TIMESTAMP",
                f"无法解析时间戳: {value}"
            ) from exc
    raise ConversationParseError(
        "CONVERSATION_INVALID_TIMESTAMP",
        f"时间戳类型无效: {type(value).__name__}"
    )


class DialogueTurn:
    """
    对话轮次数据模型
    """
    
    def __init__(
        self,
        speaker: str,
        content: str,
        timestamp: datetime = None,
        emotion: str = None,
        intent: str = None,
        ordinal: int = None
    ):
        self.speaker = speaker                  # 发言者
        self.content = content                   # 发言内容
        self.timestamp = ensure_utc_datetime(timestamp)  # 带时区UTC时间戳
        self.emotion = emotion                   # 情绪标签
        self.intent = intent                     # 意图标签
        self.ordinal = ordinal                   # 会话内轮次序号（1 起）
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "ordinal": self.ordinal,
            "speaker": self.speaker,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "emotion": self.emotion,
            "intent": self.intent
        }


class ParsedConversation:
    """
    解析后的对话数据模型
    """
    
    def __init__(
        self,
        session_id: str,
        participants: List[str] = None,
        turns: List[DialogueTurn] = None,
        metadata: Dict[str, Any] = None,
        raw_text: str = ""
    ):
        self.session_id = session_id                    # 会话ID
        self.participants = participants or []          # 参与者列表
        self.turns = turns or []                        # 对话轮次
        self.metadata = metadata or {}                  # 元数据
        self.raw_text = raw_text                        # 原始输入原文（不可变）
        self.parsed_at = datetime.now(timezone.utc)     # 解析时间（UTC）
        self._assign_ordinals()
    
    def _assign_ordinals(self) -> None:
        """会话内 ordinal 从 1 开始唯一且连续，保证多次解析稳定。"""
        for ordinal, turn in enumerate(self.turns, start=1):
            turn.ordinal = ordinal

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "participants": self.participants,
            "turns": [turn.to_dict() for turn in self.turns],
            "metadata": self.metadata,
            "raw_text": self.raw_text,
            "parsed_at": self.parsed_at.isoformat()
        }


class ConversationParser(LoggerMixin):
    """
    对话内容解析器
    解析和结构化对话内容
    """

    SUPPORTED_FORMATS = ("text", "json")
    UNKNOWN_SPEAKER = "未知"

    # 行首说话人标签：中文/英文冒号，标签不含空白、冒号与数字（避免把
    # "10:30" 之类的时间误识别为说话人），长度上限防止整句被当作标签。
    _SPEAKER_LINE_PATTERN = re.compile(
        r"^[ \t]*(?!\d)([^\s\d:：]{1,10})[ \t]*[：:][ \t]*(.*)$"
    )

    def __init__(self):
        """初始化对话解析器"""
        self.logger.info("对话内容解析器初始化完成")

    def parse_conversation(
        self,
        raw_content: str,
        format_type: str = "text",
        session_id: str = None
    ) -> ParsedConversation:
        """
        解析对话内容

        Args:
            raw_content (str): 原始对话内容
            format_type (str): 内容格式类型（text/json）
            session_id (str): 调用方指定的会话ID，优先于自动生成

        Returns:
            ParsedConversation: 解析后的对话结构

        Raises:
            ConversationParseError: 非法输入，携带稳定错误码（API 层映射为 4xx）
        """
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise ConversationParseError("CONVERSATION_EMPTY", "对话内容为空")

        normalized_format = (format_type or "text").strip().lower()
        if normalized_format not in self.SUPPORTED_FORMATS:
            raise ConversationParseError(
                "CONVERSATION_UNSUPPORTED_FORMAT",
                f"不支持的格式: {format_type}",
                raw_content
            )

        try:
            if normalized_format == "json":
                return self._parse_json(raw_content, session_id)
            return self._parse_text(raw_content, session_id)
        except ConversationParseError:
            raise
        except Exception as e:
            self.logger.error(f"对话解析失败: {str(e)}")
            raise ConversationParseError(
                "CONVERSATION_PARSE_FAILED",
                "对话解析失败",
                raw_content
            ) from e

    @staticmethod
    def _stable_session_id(raw_content: str) -> str:
        """由原文生成稳定会话ID，同一输入多次解析结果一致。"""
        return hashlib.md5(raw_content.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _ordered_participants(turns: List[DialogueTurn]) -> List[str]:
        """按首次出现顺序去重说话人，避免 set 顺序不稳定。"""
        return list(dict.fromkeys(turn.speaker for turn in turns))
    
    def extract_dialogue_turns(self, conversation: str) -> List[DialogueTurn]:
        """
        提取对话轮次

        逐行识别说话人标签（"客户："、"客服:"、"Agent :" 等），标签之后的内容
        属于该轮次；不以标签开头的行并入上一轮内容并保留换行。这里不使用
        `re.findall` 的前瞻分组，避免 speaker 与 content 同时取到说话人标签。

        Args:
            conversation (str): 对话内容

        Returns:
            List[DialogueTurn]: 对话轮次列表
        """
        turns: List[DialogueTurn] = []
        current_speaker: Optional[str] = None
        current_lines: List[str] = []

        for line in conversation.splitlines():
            match = self._SPEAKER_LINE_PATTERN.match(line)
            if match:
                if current_speaker is not None:
                    self._append_turn(turns, current_speaker, current_lines)
                current_speaker = match.group(1).strip()
                current_lines = [match.group(2)]
            else:
                if current_speaker is None:
                    # 首个说话人标签之前的文本也保留，避免静默丢原文
                    current_speaker = self.UNKNOWN_SPEAKER
                current_lines.append(line.rstrip())

        if current_speaker is not None:
            self._append_turn(turns, current_speaker, current_lines)

        return turns

    @staticmethod
    def _append_turn(
        turns: List[DialogueTurn],
        speaker: str,
        content_lines: List[str]
    ) -> None:
        """把累计的多行内容合成一轮；空内容不产生无意义轮次。"""
        content = "\n".join(content_lines).strip()
        if content:
            turns.append(DialogueTurn(speaker=speaker, content=content))
    
    def validate_conversation_format(self, content: str) -> bool:
        """
        验证对话格式
        
        Args:
            content (str): 对话内容
            
        Returns:
            bool: 格式是否有效
        """
        if not content or len(content.strip()) == 0:
            return False
        
        # 检查是否包含对话内容
        has_speaker_markers = bool(re.search(r'[：:\-\[\]]', content))
        has_multiple_lines = content.count('\n') >= 1
        
        return has_speaker_markers or has_multiple_lines
    
    def detect_format(self, content: str) -> str:
        """
        检测对话格式
        
        Args:
            content (str): 对话内容
            
        Returns:
            str: 格式类型 (json, text, unknown)
        """
        content = content.strip()
        
        if content.startswith('{') or content.startswith('['):
            try:
                json.loads(content)
                return "json"
            except json.JSONDecodeError:
                pass
        
        if ':' in content or '：' in content:
            return "text"
        
        return "unknown"
    
    def _parse_text(
        self,
        raw_content: str,
        session_id: str = None
    ) -> ParsedConversation:
        """解析文本格式对话"""
        turns = self.extract_dialogue_turns(raw_content)
        if not turns:
            raise ConversationParseError(
                "CONVERSATION_UNPARSEABLE",
                "未识别到任何对话轮次",
                raw_content
            )

        return ParsedConversation(
            session_id=session_id or self._stable_session_id(raw_content),
            participants=self._ordered_participants(turns),
            turns=turns,
            metadata={"format": "text"},
            raw_text=raw_content
        )

    def _parse_json(
        self,
        raw_content: str,
        session_id: str = None
    ) -> ParsedConversation:
        """解析JSON格式对话（对象或数组），校验结构并统一时间戳类型。"""
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            raise ConversationParseError(
                "CONVERSATION_INVALID_JSON",
                f"JSON解析错误: {e.msg}",
                raw_content
            ) from e

        if isinstance(data, dict):
            turns_data = data.get("turns", data.get("conversation"))
            session_id = session_id or data.get("session_id")
        elif isinstance(data, list):
            # 数组格式没有 session_id 字段，回退到原文哈希（此前 UnboundLocalError）
            turns_data = data
        else:
            raise ConversationParseError(
                "CONVERSATION_INVALID_STRUCTURE",
                "JSON 顶层必须是对象或数组",
                raw_content
            )

        if not isinstance(turns_data, list):
            raise ConversationParseError(
                "CONVERSATION_INVALID_STRUCTURE",
                "JSON 中缺少 turns 数组",
                raw_content
            )

        turns: List[DialogueTurn] = []
        for index, item in enumerate(turns_data, start=1):
            if not isinstance(item, dict):
                raise ConversationParseError(
                    "CONVERSATION_INVALID_TURN",
                    f"第 {index} 轮不是对象",
                    raw_content
                )
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ConversationParseError(
                    "CONVERSATION_INVALID_TURN",
                    f"第 {index} 轮缺少有效 content",
                    raw_content
                )
            turns.append(DialogueTurn(
                speaker=str(item.get("speaker") or self.UNKNOWN_SPEAKER),
                content=content,
                timestamp=item.get("timestamp"),
                emotion=self._optional_str(item.get("emotion")),
                intent=self._optional_str(item.get("intent")),
                ordinal=index
            ))

        if not turns:
            raise ConversationParseError(
                "CONVERSATION_UNPARSEABLE",
                "JSON 中没有对话轮次",
                raw_content
            )

        return ParsedConversation(
            session_id=session_id or self._stable_session_id(raw_content),
            participants=self._ordered_participants(turns),
            turns=turns,
            metadata={"format": "json"},
            raw_text=raw_content
        )

    @staticmethod
    def _optional_str(value: Any) -> Optional[str]:
        """把可选的标注字段收敛为 str/None，避免下游响应模型校验失败。"""
        if value is None:
            return None
        return str(value)
