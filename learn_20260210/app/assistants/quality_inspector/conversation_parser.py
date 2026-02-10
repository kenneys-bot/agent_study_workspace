"""
对话内容解析器
解析和结构化对话内容
"""

import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime

from utils.logger import LoggerMixin

logger = logging.getLogger(__name__)


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
        intent: str = None
    ):
        self.speaker = speaker                  # 发言者
        self.content = content                   # 发言内容
        self.timestamp = timestamp or datetime.now()  # 时间戳
        self.emotion = emotion                   # 情绪标签
        self.intent = intent                     # 意图标签
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
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
        metadata: Dict[str, Any] = None
    ):
        self.session_id = session_id                    # 会话ID
        self.participants = participants or []          # 参与者列表
        self.turns = turns or []                        # 对话轮次
        self.metadata = metadata or {}                  # 元数据
        self.parsed_at = datetime.now()                 # 解析时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "participants": self.participants,
            "turns": [turn.to_dict() for turn in self.turns],
            "metadata": self.metadata,
            "parsed_at": self.parsed_at.isoformat()
        }


class ConversationParser(LoggerMixin):
    """
    对话内容解析器
    解析和结构化对话内容
    """
    
    def __init__(self):
        """初始化对话解析器"""
        self.logger.info("对话内容解析器初始化完成")
    
    def parse_conversation(
        self,
        raw_content: str,
        format_type: str = "text"
    ) -> ParsedConversation:
        """
        解析对话内容
        
        Args:
            raw_content (str): 原始对话内容
            format_type (str): 内容格式类型
            
        Returns:
            ParsedConversation: 解析后的对话结构
        """
        try:
            if format_type == "json":
                return self._parse_json(raw_content)
            else:
                return self._parse_text(raw_content)
                
        except Exception as e:
            self.logger.error(f"对话解析失败: {str(e)}")
            return ParsedConversation(
                session_id="parse_error",
                metadata={"error": str(e)}
            )
    
    def extract_dialogue_turns(self, conversation: str) -> List[DialogueTurn]:
        """
        提取对话轮次
        
        Args:
            conversation (str): 对话内容
            
        Returns:
            List[DialogueTurn]: 对话轮次列表
        """
        turns = []
        
        # 匹配常见的对话格式
        patterns = [
            # 格式: 客户: xxx 客服: xxx
            r'(客户|用户|Customer|User)[：:]\s*(.+?)(?=(客户|用户|Customer|User)[：:]|$)',
            # 格式: [发言者] xxx
            r'\[(.*?)\]\s*(.+?)(?=\[|$)',
            # 格式: xxx (xxx)
            r'(\w+)[（(](.+?)[）)]\s*(.+)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, conversation, re.DOTALL)
            if matches:
                for match in matches:
                    if len(match) >= 2:
                        speaker = match[0].strip()
                        content = match[-1].strip()
                        if content:
                            turns.append(DialogueTurn(
                                speaker=speaker,
                                content=content
                            ))
                break
        
        # 如果没有匹配到，使用简单分割
        if not turns:
            lines = conversation.split('\n')
            current_speaker = "未知"
            current_content = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                if ':' in line:
                    if current_content:
                        turns.append(DialogueTurn(
                            speaker=current_speaker,
                            content=' '.join(current_content)
                        ))
                        current_content = []
                    
                    parts = line.split(':', 1)
                    current_speaker = parts[0].strip()
                    current_content.append(parts[1].strip() if len(parts) > 1 else "")
                else:
                    current_content.append(line)
            
            if current_content:
                turns.append(DialogueTurn(
                    speaker=current_speaker,
                    content=' '.join(current_content)
                ))
        
        return turns
    
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
                import json
                json.loads(content)
                return "json"
            except json.JSONDecodeError:
                pass
        
        if ':' in content or '：' in content:
            return "text"
        
        return "unknown"
    
    def _parse_text(self, raw_content: str) -> ParsedConversation:
        """解析文本格式对话"""
        turns = self.extract_dialogue_turns(raw_content)
        
        # 提取参与者
        participants = list(set(turn.speaker for turn in turns))
        
        # 生成会话ID
        import hashlib
        session_id = hashlib.md5(raw_content.encode()).hexdigest()[:12]
        
        return ParsedConversation(
            session_id=session_id,
            participants=participants,
            turns=turns,
            metadata={"format": "text"}
        )
    
    def _parse_json(self, raw_content: str) -> ParsedConversation:
        """解析JSON格式对话"""
        import json
        
        try:
            data = json.loads(raw_content)
            
            # 处理不同格式的JSON
            if isinstance(data, list):
                turns = [
                    DialogueTurn(
                        speaker=turn.get("speaker", "未知"),
                        content=turn.get("content", ""),
                        timestamp=turn.get("timestamp")
                    )
                    for turn in data
                ]
            elif isinstance(data, dict):
                turns = [
                    DialogueTurn(
                        speaker=turn.get("speaker", "未知"),
                        content=turn.get("content", ""),
                        timestamp=turn.get("timestamp")
                    )
                    for turn in data.get("turns", data.get("conversation", []))
                ]
                session_id = data.get("session_id", "json_session")
            else:
                return ParsedConversation(
                    session_id="parse_error",
                    metadata={"error": "无效的JSON格式"}
                )
            
            participants = list(set(turn.speaker for turn in turns))
            
            return ParsedConversation(
                session_id=session_id or "json_session",
                participants=participants,
                turns=turns,
                metadata={"format": "json"}
            )
            
        except json.JSONDecodeError as e:
            return ParsedConversation(
                session_id="parse_error",
                metadata={"error": f"JSON解析错误: {str(e)}"}
            )
