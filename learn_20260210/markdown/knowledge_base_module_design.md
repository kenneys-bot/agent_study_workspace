# 客服知识库助手模块设计

## 模块概述

客服知识库助手模块负责处理客户问题抽取、意图识别与改写、标准问/相似问生成、话术提炼等功能。该模块基于大模型和向量数据库，为客服人员提供智能化的知识管理支持。

## 核心功能设计

### 1. 客户问题抽取

```python
class QuestionExtractor:
    """
    客户问题抽取器
    从客户对话中自动抽取关键问题
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化问题抽取器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def extract_questions(self, conversation: str) -> List[ExtractedQuestion]:
        """
        从对话中抽取问题
        
        Args:
            conversation (str): 客户对话内容
            
        Returns:
            List[ExtractedQuestion]: 抽取的问题列表
        """
        pass
    
    def extract_key_info(self, conversation: str) -> Dict[str, Any]:
        """
        抽取关键信息
        
        Args:
            conversation (str): 客户对话内容
            
        Returns:
            Dict[str, Any]: 关键信息字典
        """
        pass

class ExtractedQuestion:
    """
    抽取的问题数据模型
    """
    
    def __init__(self, question: str, context: str, emotion: str, priority: int):
        self.question = question          # 问题内容
        self.context = context            # 问题上下文
        self.emotion = emotion            # 客户情绪
        self.priority = priority          # 问题优先级
        self.timestamp = datetime.now()   # 时间戳
```

### 2. 客户意图识别与改写

```python
class IntentClassifier:
    """
    客户意图分类器
    识别和改写客户意图
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化意图分类器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def classify_intent(self, query: str) -> IntentClassification:
        """
        分类客户意图
        
        Args:
            query (str): 客户查询
            
        Returns:
            IntentClassification: 意图分类结果
        """
        pass
    
    def rewrite_query(self, query: str) -> str:
        """
        改写客户查询，使其更准确
        
        Args:
            query (str): 原始查询
            
        Returns:
            str: 改写后的查询
        """
        pass

class IntentClassification:
    """
    意图分类结果数据模型
    """
    
    def __init__(self, primary_intent: str, secondary_intent: str = None, 
                 confidence: float = 0.0, categories: List[str] = None):
        self.primary_intent = primary_intent        # 主要意图
        self.secondary_intent = secondary_intent    # 次要意图
        self.confidence = confidence                # 置信度
        self.categories = categories or []          # 意图分类列表
```

### 3. 客服标准问/相似问生成

```python
class QuestionGenerator:
    """
    标准问/相似问生成器
    自动生成标准问题和相似问题
    """
    
    def __init__(self, llm_client: LLMClient, vector_db: VectorDBClient):
        """
        初始化问题生成器
        
        Args:
            llm_client (LLMClient): 大模型客户端
            vector_db (VectorDBClient): 向量数据库客户端
        """
        self.llm_client = llm_client
        self.vector_db = vector_db
    
    def generate_standard_questions(self, topic: str, count: int = 5) -> List[str]:
        """
        生成标准问题
        
        Args:
            topic (str): 主题
            count (int): 生成数量
            
        Returns:
            List[str]: 标准问题列表
        """
        pass
    
    def generate_similar_questions(self, question: str, count: int = 5) -> List[str]:
        """
        生成相似问题
        
        Args:
            question (str): 原始问题
            count (int): 生成数量
            
        Returns:
            List[str]: 相似问题列表
        """
        pass
    
    def validate_questions(self, questions: List[str]) -> List[QuestionValidation]:
        """
        验证问题质量
        
        Args:
            questions (List[str]): 问题列表
            
        Returns:
            List[QuestionValidation]: 验证结果列表
        """
        pass

class QuestionValidation:
    """
    问题验证结果数据模型
    """
    
    def __init__(self, question: str, is_valid: bool, reason: str = None, score: float = 0.0):
        self.question = question      # 问题内容
        self.is_valid = is_valid      # 是否有效
        self.reason = reason          # 无效原因
        self.score = score            # 质量评分
```

### 4. 话术提炼

```python
class ScriptGenerator:
    """
    话术生成器
    生成电话话术和电催话术
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化话术生成器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def generate_call_scripts(self, scenario: str, customer_type: str, count: int = 3) -> List[CallScript]:
        """
        生成电话话术
        
        Args:
            scenario (str): 场景描述
            customer_type (str): 客户类型
            count (int): 生成数量
            
        Returns:
            List[CallScript]: 电话话术列表
        """
        pass
    
    def generate_collection_scripts(self, overdue_days: int, customer_risk: str, count: int = 3) -> List[CollectionScript]:
        """
        生成电催话术
        
        Args:
            overdue_days (int): 逾期天数
            customer_risk (str): 客户风险等级
            count (int): 生成数量
            
        Returns:
            List[CollectionScript]: 电催话术列表
        """
        pass

class CallScript:
    """
    电话话术数据模型
    """
    
    def __init__(self, greeting: str, main_content: str, closing: str, scenario: str):
        self.greeting = greeting          # 问候语
        self.main_content = main_content  # 主要内容
        self.closing = closing            # 结束语
        self.scenario = scenario          # 适用场景
        self.timestamp = datetime.now()   # 创建时间

class CollectionScript:
    """
    电催话术数据模型
    """
    
    def __init__(self, opening: str, negotiation: str, commitment_request: str, 
                 risk_level: str, overdue_days: int):
        self.opening = opening                    # 开场白
        self.negotiation = negotiation            # 协商内容
        self.commitment_request = commitment_request  # 承诺请求
        self.risk_level = risk_level              # 风险等级
        self.overdue_days = overdue_days          # 逾期天数
        self.timestamp = datetime.now()           # 创建时间
```

## 模块间交互设计

### 与大模型模块交互
- 调用LLMClient进行文本生成和意图识别
- 使用PromptTemplate管理提示词模板

### 与向量数据库模块交互
- 将生成的标准问和相似问存储到向量数据库
- 从向量数据库检索相关问题

### 与话术推荐模块交互
- 为话术推荐提供标准问题库
- 共享客户意图识别结果

## API接口设计

### 问题抽取接口
```python
POST /api/v1/knowledge-base/extract-questions
{
  "conversation": "客户对话内容",
  "session_id": "会话ID"
}

Response:
{
  "questions": [
    {
      "question": "问题内容",
      "context": "上下文",
      "emotion": "客户情绪",
      "priority": 1
    }
  ],
  "key_info": {
    "customer_id": "客户ID",
    "product": "产品类型"
  }
}
```

### 意图识别接口
```python
POST /api/v1/knowledge-base/classify-intent
{
  "query": "客户查询内容"
}

Response:
{
  "primary_intent": "主要意图",
  "secondary_intent": "次要意图",
  "confidence": 0.95,
  "rewritten_query": "改写后的查询"
}
```

### 问题生成接口
```python
POST /api/v1/knowledge-base/generate-questions
{
  "topic": "主题",
  "type": "standard|similar",
  "count": 5
}

Response:
{
  "questions": ["问题1", "问题2", "问题3"],
  "validations": [
    {
      "question": "问题1",
      "is_valid": true,
      "score": 0.9
    }
  ]
}
```

### 话术生成接口
```python
POST /api/v1/knowledge-base/generate-scripts
{
  "type": "call|collection",
  "parameters": {
    "scenario": "场景描述",
    "customer_type": "客户类型",
    "overdue_days": 30,
    "risk_level": "高风险"
  },
  "count": 3
}

Response:
{
  "scripts": [
    {
      "greeting": "问候语",
      "main_content": "主要内容",
      "closing": "结束语"
    }
  ]
}
```

## 数据存储设计

### 问题存储结构
```python
# 问题文档元数据
QUESTION_METADATA = {
  "type": "standard_question",      # 问题类型
  "category": "账户查询",            # 分类
  "subcategory": "余额查询",         # 子分类
  "source": "generated",            # 来源
  "created_at": "2024-01-01",       # 创建时间
  "validated": true,                # 是否已验证
  "validation_score": 0.95          # 验证分数
}
```

### 话术存储结构
```python
# 话术文档元数据
SCRIPT_METADATA = {
  "type": "call_script",            # 话术类型
  "scenario": "信用卡申请",           # 适用场景
  "customer_type": "新客户",         # 客户类型
  "risk_level": "低风险",            # 风险等级
  "created_at": "2024-01-01",       # 创建时间
  "approved": true,                 # 是否已审批
  "usage_count": 100                # 使用次数
}
```

## 使用示例

### 完整流程示例
```python
from assistants.knowledge_base.question_extractor import QuestionExtractor
from assistants.knowledge_base.intent_classifier import IntentClassifier
from assistants.knowledge_base.question_generator import QuestionGenerator
from assistants.knowledge_base.script_generator import ScriptGenerator

# 初始化各组件
extractor = QuestionExtractor(llm_client)
classifier = IntentClassifier(llm_client)
generator = QuestionGenerator(llm_client, vector_db)
script_gen = ScriptGenerator(llm_client)

# 客户对话
conversation = """
客户：我想查一下我的信用卡账单
客服：请问您是通过什么方式查询？网银、手机银行还是电话查询？
客户：手机银行查不到，总是提示系统维护
"""

# 1. 抽取问题
questions = extractor.extract_questions(conversation)
print(f"抽取到 {len(questions)} 个问题")

# 2. 意图识别
intent = classifier.classify_intent("手机银行查不到信用卡账单")
print(f"识别意图为: {intent.primary_intent}")

# 3. 生成相似问题
similar_questions = generator.generate_similar_questions(
    "手机银行无法查询信用卡账单", 3)
print("生成的相似问题:")
for q in similar_questions:
    print(f"  - {q}")

# 4. 生成话术
scripts = script_gen.generate_call_scripts(
    "手机银行查询异常", "技术问题客户", 2)
print("生成的话术:")
for script in scripts:
    print(f"  问候: {script.greeting}")
    print(f"  内容: {script.main_content}")
    print(f"  结束: {script.closing}")
```

## 性能优化

### 缓存机制
```python
class KnowledgeBaseCache:
    """
    知识库缓存管理
    """
    
    def cache_extracted_questions(self, conversation: str, questions: List[ExtractedQuestion]):
        """缓存问题抽取结果"""
        pass
    
    def cache_intent_classification(self, query: str, intent: IntentClassification):
        """缓存意图分类结果"""
        pass
    
    def cache_generated_questions(self, topic: str, questions: List[str]):
        """缓存生成的问题"""
        pass
```

### 批量处理
```python
def batch_process_conversations(self, conversations: List[str]) -> List[List[ExtractedQuestion]]:
    """
    批量处理对话
    
    Args:
        conversations (List[str]): 对话列表
        
    Returns:
        List[List[ExtractedQuestion]]: 抽取的问题列表
    """
    pass
```

## 监控与日志

### 功能使用统计
- 问题抽取调用次数
- 意图识别准确率统计
- 问题生成质量评估
- 话术使用效果跟踪

### 性能监控
- 各功能响应时间
- 大模型调用耗时
- 向量数据库查询性能

## 安全考虑

### 数据安全
- 客户对话内容脱敏处理
- 敏感信息过滤机制
- 数据访问权限控制

### 内容安全
- 生成内容合规性检查
- 不当内容过滤机制
- 话术审批流程

## 错误处理

### 异常类型
```python
class KnowledgeBaseException(Exception):
    """知识库助手异常基类"""
    pass

class ExtractionError(KnowledgeBaseException):
    """问题抽取异常"""
    pass

class ClassificationError(KnowledgeBaseException):
    """意图分类异常"""
    pass

class GenerationError(KnowledgeBaseException):
    """问题生成异常"""
    pass
```

### 重试机制
```python
# 重试配置
RETRY_CONFIG = {
    "max_retries": 3,
    "retry_delay": 1,
    "backoff_factor": 2
}