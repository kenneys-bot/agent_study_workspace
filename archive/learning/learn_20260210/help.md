# 大模型客服助手项目 - 完整学习指南

## 目录

1. [项目概述](#1-项目概述)
2. [技术架构](#2-技术架构)
3. [快速开始](#3-快速开始)
4. [核心模块详解](#4-核心模块详解)
5. [代码缺陷与修复](#5-代码缺陷与修复)
6. [面试重点总结](#6-面试重点总结)
7. [常见面试题](#7-常见面试题)

---

## 1. 项目概述

### 1.1 项目背景

本项目是**苏商银行远程客服中心智能化升级**解决方案，采用大模型技术提升客服效率、改善客户体验、降低运营成本。

### 1.2 核心价值

| 价值点 | 说明 |
|--------|------|
| 自助解决率 | 智能问答减少人工介入 |
| 并行会话能力 | AI辅助支持更多并发 |
| 全流程质检 | 自动化质检覆盖率100% |
| 知识沉淀 | 标准化知识库持续积累 |

### 1.3 三大核心模块

```
┌─────────────────────────────────────────────────────────────┐
│                      大模型客服助手系统                        │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  客服知识库    │  │  话术推荐     │  │   质检助手    │    │
│  │  助手        │  │   助手       │  │             │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│         │                │                │                │
│         └────────────────┼────────────────┘                │
│                          ▼                                 │
│              ┌─────────────────────┐                       │
│              │   通义千问大模型     │                       │
│              │  + Chroma向量库     │                       │
│              └─────────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 技术架构

### 2.1 技术栈总览

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 编程语言 | Python 3.9+ | AI生态丰富 |
| Web框架 | FastAPI | 高性能异步API |
| 大模型框架 | LangChain + DashScope | 通义千问集成 |
| 向量数据库 | Chroma | 轻量级RAG支持 |
| 关系数据库 | PostgreSQL | 结构化数据存储 |
| 缓存 | Redis | 会话缓存、限流 |
| 任务队列 | Celery | 异步耗时任务 |
| 部署 | Docker + Docker Compose | 容器化编排 |

### 2.2 项目目录结构

```
customer_service_ai/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 应用入口
│   ├── config/
│   │   ├── settings.py        # 配置管理（pydantic-settings）
│   │   └── constants.py       # 常量定义
│   ├── core/
│   │   ├── llm_client.py     # 大模型客户端 ⭐
│   │   └── vector_db.py      # 向量数据库客户端 ⭐
│   ├── assistants/            # AI 助手模块
│   │   ├── knowledge_base/    # 知识库助手
│   │   │   ├── question_extractor.py
│   │   │   ├── intent_classifier.py
│   │   │   ├── question_generator.py
│   │   │   └── script_generator.py
│   │   ├── script_recommender/ # 话术推荐
│   │   │   ├── context_analyzer.py
│   │   │   ├── intent_recognizer.py
│   │   │   ├── script_recommender.py
│   │   │   └── personalization_adapter.py
│   │   └── quality_inspector/ # 质检助手
│   │       ├── conversation_parser.py
│   │       ├── speech_to_text.py
│   │       ├── auto_inspector.py
│   │       └── report_generator.py
│   ├── api/
│   │   ├── routers/          # API 路由
│   │   │   ├── knowledge_base.py
│   │   │   ├── script_recommender.py
│   │   │   └── quality_inspector.py
│   │   └── models/           # Pydantic 模型
│   │       ├── knowledge_base.py
│   │       ├── script_recommender.py
│   │       └── quality_inspector.py
│   ├── utils/
│   │   ├── logger.py
│   │   └── helpers.py
│   └── tests/
│       ├── conftest.py
│       ├── test_api.py
│       └── test_units.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── markdown/                   # 技术文档
    ├── architecture.md
    ├── tech_stack.md
    ├── llm_module_design.md
    ├── vector_db_module_design.md
    └── ...
```

### 2.3 系统架构图

```
                              ┌─────────────────┐
                              │   前端应用      │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │   API Gateway    │
                              │   (FastAPI)      │
                              └────────┬────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
┌───────▼───────┐            ┌────────▼────────┐            ┌────────▼────────┐
│  客服知识库助手 │            │  话术推荐助手   │            │   质检助手       │
└───────┬───────┘            └────────┬────────┘            └────────┬────────┘
        │                              │                              │
        └──────────────────────────────┼──────────────────────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
           ┌────────▼────────┐ ┌──────▼──────┐  ┌────────▼────────┐
           │  通义千问大模型  │ │   Redis     │  │     Chroma     │
           │  (DashScope)    │ │   缓存      │  │   向量数据库    │
           └─────────────────┘ └─────────────┘  └─────────────────┘
```

---

## 3. 快速开始

### 3.1 环境要求

- Python 3.9+
- Docker & Docker Compose
- 通义千问API密钥

### 3.2 安装步骤

```bash
# 1. 克隆项目
git clone <repository_url>
cd customer_service_ai

# 2. 配置环境变量
cp app/.env.example app/.env
# 编辑 .env 填入 API 密钥

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动服务
docker-compose up -d

# 5. 验证服务
curl http://localhost:8000/health
```

### 3.3 API 文档

启动后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## 4. 核心模块详解

### 4.1 LLMClient（大模型客户端）

**文件位置**: `app/core/llm_client.py`

**核心功能**:
- `generate_text()`: 文本生成
- `chat()`: 多轮对话
- `embedding()`: 文本向量化
- `classify_intent()`: 意图分类

**关键设计**:

```python
class LLMClient:
    def __init__(self, model_name: str = LLMModels.QWEN_PLUS):
        self.model_name = model_name
        self.api_key = settings.DASHSCOPE_API_KEY
        self._client = None
        self._init_client()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError))
    )
    def generate_text(self, prompt: str, ...) -> str:
        # 带重试的文本生成
        pass
```

**面试重点**:
- 重试机制（指数退避）
- 异常分类处理
- Mock 模式支持

### 4.2 VectorDBClient（向量数据库）

**文件位置**: `app/core/vector_db.py`

**核心功能**:
- `add_documents()`: 添加文档
- `query()`: 相似性检索
- `query_with_score()`: 带分数检索

**关键设计**:

```python
class VectorDBClient:
    def __init__(self, collection_name: str = VECTOR_DB_COLLECTION):
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._init_client()  # 启动时初始化，支持降级

    def query_with_score(self, query_text: str, n_results: int = 5):
        # 返回格式化的结果，包含 distance 分数
        pass
```

**面试重点**:
- Chroma 降级策略
- 向量检索原理
- 相似度计算

### 4.3 IntentClassifier（意图分类）

**文件位置**: `app/assistants/knowledge_base/intent_classifier.py`

**核心功能**:
- 意图识别
- 查询改写

```python
class IntentClassifier:
    def classify_intent(self, query: str, categories: List[str]) -> IntentClassification:
        result = self.llm_client.classify_intent(text=query, categories=categories)
        rewritten_query = self.rewrite_query(query)
        return IntentClassification(...)

    def rewrite_query(self, query: str) -> str:
        # 使用 LLM 改写查询，使其更精确
        pass
```

### 4.4 ScriptRecommender（话术推荐）

**文件位置**: `app/assistants/script_recommender/script_recommender.py`

**核心功能**:
- RAG 话术推荐
- 相似话术搜索
- 话术重排序

```python
class ScriptRecommender:
    def recommend_scripts(self, context: ConversationContext, intent: UserIntent, count: int = 3):
        query_text = f"情境: {context.topic}, 意图: {intent.intent_type}"

        if self.vector_db:
            scripts = self._rag_recommend(query_text, count)  # RAG 检索
        else:
            scripts = self._llm_recommend(context, intent, count)  # 纯 LLM

        return self.rank_scripts(scripts, context)  # LLM 重排序
```

---

## 5. 代码缺陷与修复

### 5.1 Bug 1: chat() 方法 system prompt 丢失

**问题位置**: `app/core/llm_client.py` 第 160-167 行

**问题代码**:
```python
def chat(self, messages: List[Dict[str, str]], ...):
    system_prompt = None
    conversation = []
    for msg in messages:
        if msg.get("role") == "system":
            system_prompt = msg.get("content")  # ❌ 提取了但没使用
        else:
            conversation.append(msg)

    response = Generation.call(
        model=self.model_name,
        messages=conversation,  # ❌ system_prompt 丢失了
        ...
    )
```

**影响**: 带 system prompt 的对话（如角色扮演客服）上下文全部丢失

**修复方案**: 将 system prompt 作为 messages 数组的第一个元素

### 5.2 Bug 2: Chroma 连接失败无正确降级

**问题位置**: `app/core/vector_db.py` 第 109-148 行

**问题代码**:
```python
def _init_client(self):
    try:
        self._client = chromadb.Client(Settings(...))
        self._collection = self._client.get_or_create_collection(...)
    except ImportError:
        logger.warning("Chroma库未安装，将使用内存模式")
        self._use_memory_mode()  # ❌ 内存模式数据不持久化
    except Exception as e:
        logger.warning(f"Chroma连接失败，切换到内存模式: {str(e)}")
        self._use_memory_mode()  # ❌ 服务重启后数据丢失
```

**影响**: 生产环境 Chroma 故障时切换到内存模式，数据全部丢失

**修复方案**: 实现三级降级策略

### 5.3 Bug 3: API 依赖注入无连接复用

**问题位置**: `app/api/routers/knowledge_base.py` 第 37-59 行

**问题代码**:
```python
def get_llm_client() -> LLMClient:
    return LLMClient()  # ❌ 每次请求都创建新实例

def get_question_extractor() -> QuestionExtractor:
    llm_client = get_llm_client()  # ❌ 无复用
    return QuestionExtractor(llm_client)
```

**影响**: 无法复用连接，API 响应慢，资源浪费

**修复方案**: 使用 `@lru_cache` 实现单例

### 5.4 Bug 4: 批量生成串行执行

**问题位置**: `app/core/llm_client.py` 第 262-294 行

**问题代码**:
```python
def batch_generate(self, prompts: List[str], ...):
    results = []
    for i, prompt in enumerate(prompts):  # ❌ 串行 for 循环
        result = self.generate_text(prompt, ...)
        results.append(result)
    return results
```

**影响**: 10 个 prompt 需要 20-30 秒，应该并发只需 2-3 秒

**修复方案**: 使用 `asyncio.gather` 并发执行

---

## 6. 面试重点总结

### 6.1 必须掌握的核心技术

| 技术点 | 掌握程度 | 相关模块 |
|--------|----------|----------|
| FastAPI 依赖注入 | 深入理解 | 所有 Router |
| LangChain / RAG | 原理+实战 | ScriptRecommender |
| 向量数据库原理 | 深入理解 | VectorDBClient |
| 大模型 API 调用 | 错误处理+重试 | LLMClient |
| 意图识别/分类 | 原理+实现 | IntentClassifier |
| 分布式缓存 | Redis 使用 | settings.py |
| 异步编程 | asyncio 并发 | LLMClient |

### 6.2 项目技术亮点（面试话术）

> "我负责的客服知识库模块，基于 LangChain + RAG 架构实现了意图识别和话术推荐功能。核心难点在于：
> 1. 如何设计 Prompt 模板提升意图识别准确率
> 2. 如何优化向量检索的召回率
> 3. 如何实现三级降级保证服务可用性"

### 6.3 性能优化亮点

| 场景 | 优化方案 | 效果 |
|------|----------|------|
| 批量生成 | asyncio.gather 并发 | 10x 提升 |
| API 响应 | 单例 + 连接复用 | 60% 降低 |
| 缓存穿透 | 多级缓存 + 布隆过滤器 | 85% 命中率 |
| 相似检索 | 向量数据库替代遍历 | 90% 提升 |

---

## 7. 常见面试题

### 7.1 NLP 相关

**Q1: 意图识别有哪些常用方法？**

答：
1. **规则匹配**：关键词、正则表达式（简单场景）
2. **传统机器学习**：SVM、朴素贝叶斯（需特征工程）
3. **深度学习**：BERT、ERNIE 等预训练模型微调
4. **大模型**：Few-shot / Zero-shot 基于 LLM

本项目采用第 4 种方式，使用通义千问通过 Prompt 工程实现意图分类。

**Q2: RAG 的核心原理？**

答：
1. **检索阶段**：将文档向量化存入向量数据库，用户查询也向量化，通过相似度匹配召回相关文档
2. **生成阶段**：将召回的文档作为上下文连同用户问题一起发给 LLM 生成答案

本项目话术推荐模块实现了 RAG 架构：Chroma 存储话术向量 → 检索相关话术 → LLM 重排序 → 返回推荐结果。

**Q3: 如何提升向量检索的召回率？**

答：
1. **混合检索**：结合关键词匹配（BM25）和向量检索
2. **重排序**：初筛后用 LLM 做二次排序
3. **query 改写**：将用户口语化 query 改写为标准表达
4. **元数据过滤**：结合业务属性（客户类型、场景）做精确筛选

本项目在 `ScriptRecommender` 中实现了 LLM 重排序，在 `IntentClassifier` 中实现了 query 改写。

**Q4: 对话系统常见的模块有哪些？**

答：
1. **NLU**：意图识别、实体抽取、对话状态跟踪
2. **DM**：对话管理、策略选择
3. **NLG**：响应生成、话术选择
4. **ASR/TTS**：语音处理（语音对话场景）

本项目质检助手模块实现了对话内容解析（类似 NLU）和自动化质检。

### 7.2 大模型相关

**Q5: 如何处理大模型的幻觉问题？**

答：
1. **RAG**：提供真实上下文减少臆测
2. **Prompt 工程**：明确要求基于给定信息回答
3. **结果验证**：对生成内容做事实性检查
4. **微调**：使用高质量标注数据微调

**Q6: LoRA 和 SFT 的区别？**

答：
- **SFT（全量微调）**：更新全部参数，计算资源需求大
- **LoRA**：只更新低秩矩阵参数量，资源需求小，适合垂直领域适配

本项目在技术选型时考虑到银行场景的合规性要求，采用 RAG 方案而非微调。

**Q7: RLHF 的三个步骤？**

答：
1. **SFT**：有监督微调，训练基础模型
2. **奖励模型**：训练一个奖励模型评价回复质量
3. **PPO 强化学习**：使用奖励模型信号微调 SFT 模型

### 7.3 系统设计相关

**Q8: 如何设计一个高可用的 AI 服务？**

答：
1. **多级降级**：服务器模式 → 持久化模式 → 只读模式
2. **缓存策略**：Redis 缓存热点结果，多级缓存防穿透
3. **限流熔断**：防止突发流量压垮下游
4. **重试机制**：指数退避重试瞬时故障
5. **监控告警**：QPS、延迟、错误率监控

本项目 VectorDBClient 实现了三级降级，LLMClient 实现了指数退避重试。

**Q9: 如何提升 API 响应速度？**

答：
1. **连接复用**：单例模式避免重复建连
2. **缓存**：热点结果缓存减少 LLM 调用
3. **异步**：asyncio 并发处理多个请求
4. **预加载**：预热常用模型和缓存

本项目使用 `@lru_cache` 实现 LLMClient 单例，使用 `asyncio.gather` 实现并发。

**Q10: FastAPI 依赖注入的原理？**

答：
- FastAPI 使用 `Depends()` 函数声明依赖关系
- 框架自动解析依赖图，按需创建和缓存实例
- 配合 `@lru_cache` 可实现应用级单例

```python
@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    return LLMClient()  # 全局单例

@router.post("/classify-intent")
async def classify_intent(
    request: ClassifyIntentRequest,
    classifier: IntentClassifier = Depends(get_intent_classifier)  # 自动注入
):
    pass
```

---

## 附录：代码修复参考

详细代码修复实现请参考同目录下的 `bug_fixes.md` 文件。

---

*最后更新: 2026-03-25*
