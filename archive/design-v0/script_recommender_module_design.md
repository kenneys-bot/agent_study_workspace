# 话术推荐助手模块设计

## 模块概述

话术推荐助手模块负责实时对话情境理解、用户意图识别、RAG话术推荐和个性化话术适配等功能。该模块基于大模型和向量数据库，为客服人员提供实时的话术推荐支持。

## 核心功能设计

### 1. 实时对话情境理解

```python
class ContextAnalyzer:
    """
    对话情境分析器
    实时分析对话情境和上下文
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化情境分析器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def analyze_context(self, conversation_history: List[Dict[str, str]]) -> ConversationContext:
        """
        分析对话情境
        
        Args:
            conversation_history (List[Dict[str, str]]): 对话历史
            
        Returns:
            ConversationContext: 对话情境分析结果
        """
        pass
    
    def extract_emotion(self, text: str) -> EmotionAnalysis:
        """
        提取文本情绪
        
        Args:
            text (str): 文本内容
            
        Returns:
            EmotionAnalysis: 情绪分析结果
        """
        pass
    
    def identify_key_points(self, conversation: str) -> List[str]:
        """
        识别对话关键点
        
        Args:
            conversation (str): 对话内容
            
        Returns:
            List[str]: 关键点列表
        """
        pass

class ConversationContext:
    """
    对话情境数据模型
    """
    
    def __init__(self, topic: str, stage: str, complexity: str, 
                 customer_satisfaction: float, key_points: List[str]):
        self.topic = topic                          # 对话主题
        self.stage = stage                          # 对话阶段
        self.complexity = complexity                # 复杂度
        self.customer_satisfaction = customer_satisfaction  # 客户满意度
        self.key_points = key_points                # 关键点
        self.timestamp = datetime.now()             # 时间戳

class EmotionAnalysis:
    """
    情绪分析结果数据模型
    """
    
    def __init__(self, emotion: str, confidence: float, intensity: float, 
                 emotional_traits: List[str]):
        self.emotion = emotion              # 情绪类型
        self.confidence = confidence        # 置信度
        self.intensity = intensity          # 情绪强度
        self.emotional_traits = emotional_traits  # 情绪特征
```

### 2. 用户意图识别

```python
class IntentRecognizer:
    """
    用户意图识别器
    识别用户在当前对话中的具体意图
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化意图识别器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def recognize_intent(self, current_query: str, context: ConversationContext) -> UserIntent:
        """
        识别用户意图
        
        Args:
            current_query (str): 当前用户查询
            context (ConversationContext): 对话情境
            
        Returns:
            UserIntent: 用户意图识别结果
        """
        pass
    
    def predict_next_intent(self, conversation_history: List[Dict[str, str]]) -> List[UserIntent]:
        """
        预测用户下一步意图
        
        Args:
            conversation_history (List[Dict[str, str]]): 对话历史
            
        Returns:
            List[UserIntent]: 预测的意图列表
        """
        pass

class UserIntent:
    """
    用户意图数据模型
    """
    
    def __init__(self, intent_type: str, sub_intent: str, confidence: float,
                 required_info: List[str], suggested_actions: List[str]):
        self.intent_type = intent_type              # 意图类型
        self.sub_intent = sub_intent                # 子意图
        self.confidence = confidence                # 置信度
        self.required_info = required_info          # 需要的信息
        self.suggested_actions = suggested_actions  # 建议行动
        self.timestamp = datetime.now()             # 时间戳
```

### 3. RAG话术推荐

```python
class ScriptRecommender:
    """
    话术推荐器
    基于RAG技术推荐合适的客服话术
    """
    
    def __init__(self, llm_client: LLMClient, vector_db: VectorDBClient):
        """
        初始化话术推荐器
        
        Args:
            llm_client (LLMClient): 大模型客户端
            vector_db (VectorDBClient): 向量数据库客户端
        """
        self.llm_client = llm_client
        self.vector_db = vector_db
    
    def recommend_scripts(self, context: ConversationContext, intent: UserIntent, 
                         count: int = 3) -> List[RecommendedScript]:
        """
        推荐话术
        
        Args:
            context (ConversationContext): 对话情境
            intent (UserIntent): 用户意图
            count (int): 推荐数量
            
        Returns:
            List[RecommendedScript]: 推荐的话术列表
        """
        pass
    
    def search_similar_scripts(self, query: str, filters: dict = None, count: int = 5) -> List[RecommendedScript]:
        """
        搜索相似话术
        
        Args:
            query (str): 查询内容
            filters (dict): 过滤条件
            count (int): 返回数量
            
        Returns:
            List[RecommendedScript]: 相似话术列表
        """
        pass
    
    def rank_scripts(self, scripts: List[RecommendedScript], 
                    context: ConversationContext) -> List[RecommendedScript]:
        """
        对话术进行排序
        
        Args:
            scripts (List[RecommendedScript]): 话术列表
            context (ConversationContext): 对话情境
            
        Returns:
            List[RecommendedScript]: 排序后的话术列表
        """
        pass

class RecommendedScript:
    """
    推荐话术数据模型
    """
    
    def __init__(self, script_id: str, content: str, title: str, 
                 relevance_score: float, usage_count: int, success_rate: float):
        self.script_id = script_id          # 话术ID
        self.content = content              # 话术内容
        self.title = title                  # 话术标题
        self.relevance_score = relevance_score  # 相关性分数
        self.usage_count = usage_count      # 使用次数
        self.success_rate = success_rate    # 成功率
        self.timestamp = datetime.now()     # 推荐时间
```

### 4. 个性化话术适配

```python
class PersonalizationAdapter:
    """
    个性化适配器
    根据客户特征和历史交互个性化话术
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化个性化适配器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
    
    def adapt_script(self, script: str, customer_profile: CustomerProfile, 
                    context: ConversationContext) -> str:
        """
        个性化适配话术
        
        Args:
            script (str): 原始话术
            customer_profile (CustomerProfile): 客户画像
            context (ConversationContext): 对话情境
            
        Returns:
            str: 个性化后的话术
        """
        pass
    
    def generate_personalized_greeting(self, customer_profile: CustomerProfile) -> str:
        """
        生成个性化问候语
        
        Args:
            customer_profile (CustomerProfile): 客户画像
            
        Returns:
            str: 个性化问候语
        """
        pass

class CustomerProfile:
    """
    客户画像数据模型
    """
    
    def __init__(self, customer_id: str, name: str, age: int, gender: str,
                 customer_type: str, risk_level: str, preference: dict):
        self.customer_id = customer_id      # 客户ID
        self.name = name                    # 姓名
        self.age = age                      # 年龄
        self.gender = gender                # 性别
        self.customer_type = customer_type  # 客户类型
        self.risk_level = risk_level        # 风险等级
        self.preference = preference        # 偏好设置
        self.history = []                   # 历史交互记录
```

## 模块间交互设计

### 与大模型模块交互
- 调用LLMClient进行对话理解和生成
- 使用PromptTemplate管理提示词模板

### 与向量数据库模块交互
- 从向量数据库检索相关话术
- 将新生成的话术存储到向量数据库

### 与客服知识库模块交互
- 获取标准问题和知识库信息
- 共享对话情境和意图识别结果

## API接口设计

### 情境分析接口
```python
POST /api/v1/script-recommender/analyze-context
{
  "conversation_history": [
    {"role": "customer", "content": "客户话语"},
    {"role": "agent", "content": "客服话语"}
  ],
  "session_id": "会话ID"
}

Response:
{
  "topic": "对话主题",
  "stage": "对话阶段",
  "complexity": "复杂度",
  "customer_satisfaction": 0.8,
  "key_points": ["关键点1", "关键点2"]
}
```

### 意图识别接口
```python
POST /api/v1/script-recommender/recognize-intent
{
  "current_query": "当前用户查询",
  "context": {
    "topic": "对话主题",
    "stage": "对话阶段"
  }
}

Response:
{
  "intent_type": "意图类型",
  "sub_intent": "子意图",
  "confidence": 0.95,
  "required_info": ["需要的信息1", "需要的信息2"],
  "suggested_actions": ["建议行动1", "建议行动2"]
}
```

### 话术推荐接口
```python
POST /api/v1/script-recommender/recommend-scripts
{
  "context": {
    "topic": "对话主题",
    "stage": "对话阶段",
    "customer_satisfaction": 0.8
  },
  "intent": {
    "intent_type": "意图类型",
    "confidence": 0.95
  },
  "count": 3
}

Response:
{
  "scripts": [
    {
      "script_id": "话术ID",
      "content": "话术内容",
      "title": "话术标题",
      "relevance_score": 0.95
    }
  ]
}
```

### 个性化适配接口
```python
POST /api/v1/script-recommender/personalize-script
{
  "script": "原始话术内容",
  "customer_profile": {
    "customer_id": "客户ID",
    "name": "客户姓名",
    "age": 30,
    "customer_type": "VIP客户"
  },
  "context": {
    "topic": "对话主题"
  }
}

Response:
{
  "personalized_script": "个性化后的话术内容"
}
```

## 数据存储设计

### 话术存储结构
```python
# 话术文档元数据
SCRIPT_METADATA = {
  "type": "recommendation_script",  # 话术类型
  "category": "账户查询",            # 分类
  "subcategory": "余额查询",         # 子分类
  "scenario": "手机银行查询",         # 适用场景
  "customer_type": "个人客户",        # 适用客户类型
  "complexity": "简单",              # 复杂度
  "created_at": "2024-01-01",       # 创建时间
  "updated_at": "2024-01-01",       # 更新时间
  "usage_count": 100,               # 使用次数
  "success_rate": 0.95              # 成功率
}
```

### 客户画像存储结构
```python
# 客户画像元数据
CUSTOMER_PROFILE_METADATA = {
  "customer_id": "客户ID",
  "profile_version": "1.0",         # 画像版本
  "last_updated": "2024-01-01",     # 最后更新时间
  "interaction_count": 50,          # 交互次数
  "preferred_channels": ["电话", "网银"], # 偏好渠道
  "communication_style": "直接型"     # 沟通风格
}
```

## 使用示例

### 完整流程示例
```python
from assistants.script_recommender.context_analyzer import ContextAnalyzer
from assistants.script_recommender.intent_recognizer import IntentRecognizer
from assistants.script_recommender.script_recommender import ScriptRecommender
from assistants.script_recommender.personalization_adapter import PersonalizationAdapter

# 初始化各组件
context_analyzer = ContextAnalyzer(llm_client)
intent_recognizer = IntentRecognizer(llm_client)
script_recommender = ScriptRecommender(llm_client, vector_db)
personalization_adapter = PersonalizationAdapter(llm_client)

# 对话历史
conversation_history = [
    {"role": "customer", "content": "我想查一下我的信用卡账单"},
    {"role": "agent", "content": "请问您是通过什么方式查询？网银、手机银行还是电话查询？"},
    {"role": "customer", "content": "手机银行查不到，总是提示系统维护"}
]

# 1. 分析对话情境
context = context_analyzer.analyze_context(conversation_history)
print(f"对话主题: {context.topic}")
print(f"客户满意度: {context.customer_satisfaction}")

# 2. 识别用户意图
current_query = "手机银行查不到，总是提示系统维护"
intent = intent_recognizer.recognize_intent(current_query, context)
print(f"识别意图为: {intent.intent_type}")

# 3. 推荐话术
recommended_scripts = script_recommender.recommend_scripts(context, intent, 3)
print("推荐的话术:")
for script in recommended_scripts:
    print(f"  - {script.title} (相关性: {script.relevance_score})")

# 4. 个性化适配
customer_profile = CustomerProfile(
    customer_id="CUST001",
    name="张三",
    age=35,
    gender="男",
    customer_type="VIP客户",
    risk_level="低风险",
    preference={"communication_style": "正式"}
)

personalized_script = personalization_adapter.adapt_script(
    recommended_scripts[0].content, customer_profile, context)
print(f"个性化后的话术: {personalized_script}")
```

## 性能优化

### 缓存机制
```python
class ScriptRecommendationCache:
    """
    话术推荐缓存管理
    """
    
    def cache_context_analysis(self, conversation_history: str, context: ConversationContext):
        """缓存情境分析结果"""
        pass
    
    def cache_intent_recognition(self, query: str, context: str, intent: UserIntent):
        """缓存意图识别结果"""
        pass
    
    def cache_script_recommendations(self, context: str, intent: str, scripts: List[RecommendedScript]):
        """缓存话术推荐结果"""
        pass
```

### 预加载机制
```python
def preload_frequently_used_scripts(self, category: str, count: int = 100):
    """
    预加载常用话术到内存
    
    Args:
        category (str): 话术分类
        count (int): 预加载数量
    """
    pass
```

## 监控与日志

### 推荐效果统计
- 话术推荐点击率
- 推荐话术成功率
- 个性化适配效果
- 用户满意度反馈

### 性能监控
- 各功能响应时间
- 向量数据库查询性能
- 缓存命中率统计

## 安全考虑

### 数据安全
- 客户画像数据加密存储
- 对话内容脱敏处理
- 敏感信息过滤机制

### 内容安全
- 推荐话术合规性检查
- 不当内容过滤机制
- 话术更新审批流程

## 错误处理

### 异常类型
```python
class ScriptRecommenderException(Exception):
    """话术推荐助手异常基类"""
    pass

class ContextAnalysisError(ScriptRecommenderException):
    """情境分析异常"""
    pass

class IntentRecognitionError(ScriptRecommenderException):
    """意图识别异常"""
    pass

class ScriptRecommendationError(ScriptRecommenderException):
    """话术推荐异常"""
    pass
```

### 降级机制
```python
def fallback_recommendation(self, context: ConversationContext, 
                          intent: UserIntent) -> List[RecommendedScript]:
    """
    降级推荐机制
    
    Args:
        context (ConversationContext): 对话情境
        intent (UserIntent): 用户意图
        
    Returns:
        List[RecommendedScript]: 降级推荐的话术列表
    """
    pass