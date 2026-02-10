"""
常量定义模块
定义项目中的常量配置
"""

# ==================== 大模型配置 ====================

# 通义千问模型名称
class LLMModels:
    QWEN_TURBO = "qwen-turbo"
    QWEN_PLUS = "qwen-plus"
    QWEN_MAX = "qwen-max"
    QWEN_AIR = "qwen-air"
    QWEN_72B_CHAT = "qwen-72b-chat"


# 默认模型参数
class LLMDefaultParams:
    TEMPERATURE = 0.7
    MAX_TOKENS = 2048
    TOP_P = 0.9
    TOP_K = 50
    REPETITION_PENALTY = 1.1


# ==================== 向量数据库配置 ====================

# 默认集合名称
VECTOR_DB_COLLECTION = "customer_service_knowledge"

# 向量维度配置
EMBEDDING_MODEL = "text-embedding-v1"
EMBEDDING_DIMENSION = 1536

# 相似性搜索配置
DEFAULT_TOP_K = 5
MAX_TOP_K = 20

# ==================== API配置 ====================

# API版本
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

# 分页配置
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# ==================== 日志配置 ====================

# 日志格式
LOG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>"

# 日志文件配置
LOG_ROTATION = "500 MB"
LOG_RETENTION = "10 days"
LOG_COMPRESSION = "gz"

# ==================== 缓存配置 ====================

# Redis键前缀
REDIS_KEY_PREFIX = "customer_service_ai:"

# 缓存过期时间（秒）
CACHE_TTL_DEFAULT = 3600  # 1小时
CACHE_TTL_LONG = 86400    # 24小时
CACHE_TTL_SHORT = 300     # 5分钟

# ==================== 客服知识库配置 ====================

# 问题类型
class QuestionTypes:
    STANDARD = "standard_question"    # 标准问题
    SIMILAR = "similar_question"       # 相似问题
    FAQ = "faq"                        # 常见问题

# 意图分类
class IntentCategories:
    ACCOUNT_QUERY = "账户查询"
    BUSINESS办理 = "业务办理"
    COMPLAINT = "投诉建议"
    FEEDBACK = "意见反馈"
    TECHNICAL_SUPPORT = "技术支持"
    OTHER = "其他"

# 客户情绪
class CustomerEmotions:
    POSITIVE = "积极"
    NEUTRAL = "中立"
    NEGATIVE = "消极"
    ANGRY = "愤怒"
    ANXIOUS = "焦虑"

# ==================== 话术配置 ====================

# 话术类型
class ScriptTypes:
    CALL_SCRIPT = "call_script"           # 电话话术
    COLLECTION_SCRIPT = "collection_script"  # 电催话术
    RECOMMENDATION_SCRIPT = "recommendation_script"  # 推荐话术

# 客户类型
class CustomerTypes:
    NEW_CUSTOMER = "新客户"               # 新客户
    REGULAR_CUSTOMER = "老客户"           # 老客户
    VIP_CUSTOMER = "VIP客户"              # VIP客户
    HIGH_RISK = "高风险客户"              # 高风险客户
    LOW_RISK = "低风险客户"               # 低风险客户

# 话术复杂度
class ScriptComplexity:
    SIMPLE = "简单"
    MEDIUM = "中等"
    COMPLEX = "复杂"

# ==================== 质检配置 ====================

# 质检维度
class InspectionDimensions:
    SERVICE_ATTITUDE = "服务态度"
    PROFESSIONALISM = "专业性"
    PROBLEM_SOLVING = "问题解决能力"
    COMPLIANCE = "合规性"
    COMMUNICATION = "沟通技巧"

# 问题严重程度
class IssueSeverity:
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    CRITICAL = "严重"

# 质检状态
class InspectionStatus:
    PENDING = "待复核"
    APPROVED = "已批准"
    REJECTED = "已拒绝"
    IN_PROGRESS = "进行中"

# ==================== 对话阶段 ====================

class ConversationStages:
    GREETING = "问候阶段"
    IDENTIFICATION = "身份确认阶段"
    MAIN_TOPIC = "主题交流阶段"
    PROBLEM_SOLVING = "问题解决阶段"
    CLOSING = "结束阶段"
    FOLLOW_UP = "跟进阶段"

# ==================== 提示词模板 ====================

class PromptTemplates:
    """提示词模板常量"""
    
    # 知识库模块
    KNOWLEDGE_BASE_EXTRACT = """
请从以下客户对话中提取关键问题：

对话内容：
{conversation}

请按照以下格式输出：
1. 主要问题：
2. 相关信息：
3. 客户情绪：
"""
    
    INTENT_CLASSIFY = """
请对以下客户查询进行意图分类：

查询内容：{query}

可选类别：{categories}

请返回分类结果和置信度。
"""
    
    QUESTION_GENERATE = """
请生成关于以下主题的标准问题：

主题：{topic}
数量：{count}

请生成{count}个标准问题。
"""
    
    SCRIPT_GENERATE = """
根据以下场景生成电话话术：

场景：{scenario}
客户类型：{customer_type}

请生成合适的话术，包括问候语、主要内容和结束语。
"""
    
    # 话术推荐模块
    CONTEXT_ANALYZE = """
请分析以下对话的情境：

对话历史：
{conversation_history}

请分析：
1. 对话主题
2. 当前阶段
3. 客户情绪
4. 客户满意度
"""
    
    SCRIPT_RECOMMEND = """
根据以下情况推荐合适的客服话术：

客户问题：{question}
客户情绪：{emotion}
客户类型：{customer_type}

请推荐3条合适的客服话术，并说明推荐理由。
"""
    
    # 质检模块
    QUALITY_INSPECT = """
请对以下客服对话进行质量检查：

对话内容：
{conversation}

检查要点：
1. 服务态度
2. 问题解决能力
3. 专业性
4. 合规性

请给出评分（满分100分）和改进建议。
"""
    
    COMPLIANCE_CHECK = """
请检查以下客服对话的合规性：

对话内容：
{conversation}

请检查是否存在违规内容，并给出评分。
"""


# ==================== 异常消息 ====================

class ErrorMessages:
    """错误消息常量"""
    
    # 通用错误
    INTERNAL_ERROR = "内部服务器错误"
    VALIDATION_ERROR = "数据验证失败"
    NOT_FOUND = "资源不存在"
    UNAUTHORIZED = "未授权访问"
    FORBIDDEN = "禁止访问"
    
    # LLM相关
    LLM_CONNECTION_ERROR = "大模型连接失败"
    LLM_TIMEOUT_ERROR = "大模型响应超时"
    LLM_RATE_LIMIT_ERROR = "大模型请求频率超限"
    LLM_INVALID_REQUEST = "无效的大模型请求"
    
    # 向量数据库相关
    VECTOR_DB_CONNECTION_ERROR = "向量数据库连接失败"
    VECTOR_DB_QUERY_ERROR = "向量数据库查询失败"
    VECTOR_DB_STORAGE_ERROR = "向量数据库存储失败"
    
    # 客服知识库相关
    EXTRACTION_ERROR = "问题抽取失败"
    CLASSIFICATION_ERROR = "意图分类失败"
    GENERATION_ERROR = "内容生成失败"
    
    # 话术推荐相关
    CONTEXT_ANALYSIS_ERROR = "情境分析失败"
    INTENT_RECOGNITION_ERROR = "意图识别失败"
    RECOMMENDATION_ERROR = "话术推荐失败"
    
    # 质检相关
    PARSING_ERROR = "对话解析失败"
    TRANSCRIPTION_ERROR = "语音转文字失败"
    INSPECTION_ERROR = "质检失败"
