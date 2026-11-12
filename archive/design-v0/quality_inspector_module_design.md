# 质检助手模块设计

## 模块概述

质检助手模块负责对话内容上传与解析、语音转文字处理、大模型自动化质检、结果生成与复核流程等功能。该模块基于大模型技术，为客服质量监控提供自动化的质检解决方案。

## 核心功能设计

### 1. 对话内容解析

```python
class ConversationParser:
    """
    对话内容解析器
    解析和结构化对话内容
    """
    
    def __init__(self):
        """初始化对话解析器"""
        pass
    
    def parse_conversation(self, raw_content: str, format_type: str = "text") -> ParsedConversation:
        """
        解析对话内容
        
        Args:
            raw_content (str): 原始对话内容
            format_type (str): 内容格式类型(text, json, xml等)
            
        Returns:
            ParsedConversation: 解析后的对话结构
        """
        pass
    
    def extract_dialogue_turns(self, conversation: str) -> List[DialogueTurn]:
        """
        提取对话轮次
        
        Args:
            conversation (str): 对话内容
            
        Returns:
            List[DialogueTurn]: 对话轮次列表
        """
        pass
    
    def validate_conversation_format(self, content: str) -> bool:
        """
        验证对话格式
        
        Args:
            content (str): 对话内容
            
        Returns:
            bool: 格式是否有效
        """
        pass

class ParsedConversation:
    """
    解析后的对话数据模型
    """
    
    def __init__(self, session_id: str, participants: List[str], 
                 turns: List[DialogueTurn], metadata: dict = None):
        self.session_id = session_id            # 会话ID
        self.participants = participants        # 参与者列表
        self.turns = turns                      # 对话轮次
        self.metadata = metadata or {}          # 元数据
        self.parsed_at = datetime.now()         # 解析时间

class DialogueTurn:
    """
    对话轮次数据模型
    """
    
    def __init__(self, speaker: str, content: str, timestamp: datetime = None,
                 emotion: str = None, intent: str = None):
        self.speaker = speaker          # 发言者
        self.content = content          # 发言内容
        self.timestamp = timestamp or datetime.now()  # 时间戳
        self.emotion = emotion          # 情绪标签
        self.intent = intent            # 意图标签
```

### 2. 语音转文字处理

```python
class SpeechToTextProcessor:
    """
    语音转文字处理器
    将语音文件转换为文字内容
    """
    
    def __init__(self, stt_client=None):
        """
        初始化语音转文字处理器
        
        Args:
            stt_client: 语音识别客户端(可选)
        """
        self.stt_client = stt_client or self._init_default_client()
    
    def _init_default_client(self):
        """初始化默认语音识别客户端"""
        pass
    
    def transcribe_audio(self, audio_file_path: str, language: str = "zh-CN") -> TranscriptionResult:
        """
        转录音频文件
        
        Args:
            audio_file_path (str): 音频文件路径
            language (str): 语言代码
            
        Returns:
            TranscriptionResult: 转录结果
        """
        pass
    
    def transcribe_audio_stream(self, audio_stream: bytes, language: str = "zh-CN") -> TranscriptionResult:
        """
        转录音频流
        
        Args:
            audio_stream (bytes): 音频流数据
            language (str): 语言代码
            
        Returns:
            TranscriptionResult: 转录结果
        """
        pass
    
    def detect_language(self, audio_data: bytes) -> str:
        """
        检测音频语言
        
        Args:
            audio_data (bytes): 音频数据
            
        Returns:
            str: 检测到的语言代码
        """
        pass

class TranscriptionResult:
    """
    转录结果数据模型
    """
    
    def __init__(self, text: str, confidence: float, segments: List[AudioSegment] = None,
                 language: str = "zh-CN"):
        self.text = text                    # 转录文本
        self.confidence = confidence        # 置信度
        self.segments = segments or []      # 音频片段
        self.language = language            # 语言
        self.transcribed_at = datetime.now() # 转录时间
```

### 3. 大模型自动化质检

```python
class AutoInspector:
    """
    自动化质检器
    基于大模型进行客服对话质量检查
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化自动化质检器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def inspect_conversation(self, conversation: ParsedConversation) -> InspectionReport:
        """
        检查对话质量
        
        Args:
            conversation (ParsedConversation): 解析后的对话
            
        Returns:
            InspectionReport: 质检报告
        """
        pass
    
    def evaluate_service_attitude(self, agent_responses: List[str]) -> AttitudeScore:
        """
        评估服务态度
        
        Args:
            agent_responses (List[str]): 客服回复列表
            
        Returns:
            AttitudeScore: 态度评分
        """
        pass
    
    def check_compliance(self, conversation: ParsedConversation) -> ComplianceReport:
        """
        合规性检查
        
        Args:
            conversation (ParsedConversation): 解析后的对话
            
        Returns:
            ComplianceReport: 合规报告
        """
        pass
    
    def generate_improvement_suggestions(self, issues: List[QualityIssue]) -> List[str]:
        """
        生成改进建议
        
        Args:
            issues (List[QualityIssue]): 质量问题列表
            
        Returns:
            List[str]: 改进建议列表
        """
        pass

class InspectionReport:
    """
    质检报告数据模型
    """
    
    def __init__(self, session_id: str, overall_score: float, 
                 attitude_score: float, professionalism_score: float,
                 compliance_score: float, issues: List[QualityIssue]):
        self.session_id = session_id                # 会话ID
        self.overall_score = overall_score          # 总体评分
        self.attitude_score = attitude_score        # 态度评分
        self.professionalism_score = professionalism_score  # 专业性评分
        self.compliance_score = compliance_score    # 合规性评分
        self.issues = issues                        # 发现的问题
        self.generated_at = datetime.now()          # 生成时间

class QualityIssue:
    """
    质量问题数据模型
    """
    
    def __init__(self, issue_type: str, description: str, severity: str,
                 location: str, suggestion: str = None):
        self.issue_type = issue_type        # 问题类型
        self.description = description      # 问题描述
        self.severity = severity            # 严重程度(低/中/高)
        self.location = location            # 问题位置
        self.suggestion = suggestion        # 改进建议
        self.detected_at = datetime.now()   # 检测时间
```

### 4. 结果生成与复核流程

```python
class ReportGenerator:
    """
    报告生成器
    生成质检报告和统计分析
    """
    
    def __init__(self):
        """初始化报告生成器"""
        pass
    
    def generate_detailed_report(self, inspection_result: InspectionReport) -> str:
        """
        生成详细质检报告
        
        Args:
            inspection_result (InspectionReport): 质检结果
            
        Returns:
            str: 详细报告内容
        """
        pass
    
    def generate_summary_report(self, results: List[InspectionReport]) -> SummaryReport:
        """
        生成汇总报告
        
        Args:
            results (List[InspectionReport]): 质检结果列表
            
        Returns:
            SummaryReport: 汇总报告
        """
        pass
    
    def export_report(self, report: InspectionReport, format_type: str = "pdf") -> bytes:
        """
        导出报告
        
        Args:
            report (InspectionReport): 质检报告
            format_type (str): 导出格式(pdf, excel, html等)
            
        Returns:
            bytes: 导出的文件数据
        """
        pass

class ReviewWorkflow:
    """
    复核工作流
    管理质检结果的复核流程
    """
    
    def __init__(self):
        """初始化复核工作流"""
        pass
    
    def submit_for_review(self, report: InspectionReport, reviewer: str) -> bool:
        """
        提交复核
        
        Args:
            report (InspectionReport): 质检报告
            reviewer (str): 复核人
            
        Returns:
            bool: 提交是否成功
        """
        pass
    
    def approve_report(self, report_id: str, approver: str, comments: str = None) -> bool:
        """
        批准报告
        
        Args:
            report_id (str): 报告ID
            approver (str): 批准人
            comments (str): 批准意见
            
        Returns:
            bool: 批准是否成功
        """
        pass
    
    def reject_report(self, report_id: str, rejector: str, reasons: List[str]) -> bool:
        """
        拒绝报告
        
        Args:
            report_id (str): 报告ID
            rejector (str): 拒绝人
            reasons (List[str]): 拒绝原因
            
        Returns:
            bool: 拒绝是否成功
        """
        pass
```

## 模块间交互设计

### 与大模型模块交互
- 调用LLMClient进行对话质量评估
- 使用PromptTemplate管理质检提示词模板

### 与客服知识库模块交互
- 获取标准服务流程和规范
- 对比客服实际表现与标准要求

### 与话术推荐模块交互
- 获取推荐话术作为质检参考标准
- 分析客服话术与推荐话术的差异

## API接口设计

### 对话解析接口
```python
POST /api/v1/quality-inspector/parse-conversation
{
  "content": "对话内容",
  "format": "text",
  "session_id": "会话ID"
}

Response:
{
  "session_id": "会话ID",
  "participants": ["客户", "客服"],
  "turns": [
    {
      "speaker": "客户",
      "content": "客户话语",
      "timestamp": "2024-01-01T10:00:00"
    }
  ]
}
```

### 语音转文字接口
```python
POST /api/v1/quality-inspector/transcribe
{
  "audio_file": "音频文件路径或base64编码",
  "language": "zh-CN"
}

Response:
{
  "text": "转录后的文本内容",
  "confidence": 0.95,
  "language": "zh-CN"
}
```

### 自动质检接口
```python
POST /api/v1/quality-inspector/inspect
{
  "conversation": {
    "session_id": "会话ID",
    "participants": ["客户", "客服"],
    "turns": [
      {
        "speaker": "客户",
        "content": "客户话语"
      }
    ]
  }
}

Response:
{
  "session_id": "会话ID",
  "overall_score": 85.5,
  "attitude_score": 90.0,
  "professionalism_score": 80.0,
  "compliance_score": 95.0,
  "issues": [
    {
      "issue_type": "服务态度",
      "description": "客服回应过于简短",
      "severity": "中",
      "suggestion": "建议提供更详细的解答"
    }
  ]
}
```

### 报告生成接口
```python
POST /api/v1/quality-inspector/generate-report
{
  "report_id": "报告ID",
  "format": "pdf"
}

Response:
{
  "report_content": "报告内容或下载链接",
  "format": "pdf"
}
```

## 数据存储设计

### 质检报告存储结构
```python
# 质检报告元数据
INSPECTION_METADATA = {
  "report_id": "报告ID",
  "session_id": "会话ID",
  "inspector": "质检员",
  "status": "待复核",              # 状态: 待复核/已批准/已拒绝
  "generated_at": "2024-01-01",   # 生成时间
  "reviewed_at": null,            # 复核时间
  "overall_score": 85.5,          # 总体评分
  "issue_count": 3                # 问题数量
}
```

### 质量问题存储结构
```python
# 质量问题元数据
ISSUE_METADATA = {
  "issue_id": "问题ID",
  "report_id": "报告ID",
  "issue_type": "服务态度",         # 问题类型
  "severity": "中",                # 严重程度
  "category": "沟通技巧",           # 问题分类
  "detected_by": "AI质检",         # 检测方式
  "created_at": "2024-01-01"      # 创建时间
}
```

## 使用示例

### 完整质检流程示例
```python
from assistants.quality_inspector.conversation_parser import ConversationParser
from assistants.quality_inspector.speech_to_text import SpeechToTextProcessor
from assistants.quality_inspector.auto_inspector import AutoInspector
from assistants.quality_inspector.report_generator import ReportGenerator

# 初始化各组件
parser = ConversationParser()
stt_processor = SpeechToTextProcessor()
inspector = AutoInspector(llm_client)
report_generator = ReportGenerator()

# 1. 解析对话内容
raw_conversation = """
客户：我想查一下我的信用卡账单
客服：请问您是通过什么方式查询？网银、手机银行还是电话查询？
客户：手机银行查不到，总是提示系统维护
客服：非常抱歉给您带来不便，我这边帮您查询一下具体情况。
"""

parsed_conversation = parser.parse_conversation(raw_conversation)
print(f"解析了 {len(parsed_conversation.turns)} 个对话轮次")

# 2. 如果是语音文件，先进行转录
# audio_file_path = "conversation.wav"
# transcription_result = stt_processor.transcribe_audio(audio_file_path)
# parsed_conversation = parser.parse_conversation(transcription_result.text)

# 3. 进行质检
inspection_report = inspector.inspect_conversation(parsed_conversation)
print(f"总体评分: {inspection_report.overall_score}")
print(f"发现问题数量: {len(inspection_report.issues)}")

# 4. 生成报告
detailed_report = report_generator.generate_detailed_report(inspection_report)
print("详细质检报告:")
print(detailed_report)

# 5. 导出报告
# pdf_report = report_generator.export_report(inspection_report, "pdf")
# with open("质检报告.pdf", "wb") as f:
#     f.write(pdf_report)
```

## 性能优化

### 批量处理
```python
def batch_inspect_conversations(self, conversations: List[ParsedConversation]) -> List[InspectionReport]:
    """
    批量质检对话
    
    Args:
        conversations (List[ParsedConversation]): 对话列表
        
    Returns:
        List[InspectionReport]: 质检报告列表
    """
    pass
```

### 缓存机制
```python
class QualityInspectionCache:
    """
    质检缓存管理
    """
    
    def cache_inspection_result(self, session_id: str, result: InspectionReport):
        """缓存质检结果"""
        pass
    
    def get_cached_result(self, session_id: str) -> Optional[InspectionReport]:
        """获取缓存的质检结果"""
        pass
```

## 监控与日志

### 质检统计
- 质检准确率统计
- 各类问题分布统计
- 客服表现趋势分析
- 复核结果统计

### 性能监控
- 质检处理时间
- 语音转文字准确率
- 大模型调用耗时
- 系统资源使用情况

## 安全考虑

### 数据安全
- 对话内容加密存储
- 敏感信息脱敏处理
- 访问权限控制
- 数据备份机制

### 隐私保护
- 客户信息匿名化
- 符合数据保护法规
- 审计日志记录
- 数据保留策略

## 错误处理

### 异常类型
```python
class QualityInspectorException(Exception):
    """质检助手异常基类"""
    pass

class ParsingError(QualityInspectorException):
    """对话解析异常"""
    pass

class TranscriptionError(QualityInspectorException):
    """语音转文字异常"""
    pass

class InspectionError(QualityInspectorException):
    """质检异常"""
    pass
```

### 容错机制
```python
def inspect_with_fallback(self, conversation: ParsedConversation) -> InspectionReport:
    """
    带降级处理的质检
    
    Args:
        conversation (ParsedConversation): 解析后的对话
        
    Returns:
        InspectionReport: 质检报告
    """
    pass