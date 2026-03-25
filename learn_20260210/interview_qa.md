# 面试题库 - NLP/金融算法工程师

## 目录

1. [NLP 基础问题](#1-nlp-基础问题)
2. [大模型相关问题](#2-大模型相关问题)
3. [RAG 与知识库问题](#3-rag-与知识库问题)
4. [系统设计问题](#4-系统设计问题)
5. [金融业务问题](#5-金融业务问题)
6. [代码与项目问题](#6-代码与项目问题)

---

## 1. NLP 基础问题

### Q1: 意图识别有哪些常用方法？请详细说明原理

**参考答案：**

意图识别是 NLP 的经典任务，主要方法有：

**1. 规则匹配法**
```python
# 基于关键词/正则的简单匹配
if "信用卡" in query or "card" in query.lower():
    return "信用卡业务"
```
- 优点：简单、可解释、无需训练
- 缺点：泛化能力差、难以处理同义词/表达变体

**2. 传统机器学习**
- SVM、朴素贝叶斯、决策树
- 特征工程：TF-IDF、词袋模型、词向量
- 适用于数据量较小的场景

**3. 深度学习方法**
- CNN：捕获局部 n-gram 特征
- LSTM/BERT：捕获序列上下文信息
```python
# BERT 分类示例
from transformers import BertForSequenceClassification
model = BertForSequenceClassification.from_pretrained('bert-base-chinese')
outputs = model(input_ids, attention_mask, labels)
```

**4. 大模型方法（Zero-shot/Few-shot）**
```python
prompt = f"""
判断用户意图，可选类别：{categories}
用户输入：{query}
直接输出类别名称：
"""
response = llm.generate_text(prompt)
```
- 本项目采用：第 4 种 + Prompt 工程

---

### Q2: 实体抽取（NER）的常用方法？

**参考答案：**

命名实体识别（NER）方法演进：

**1. 基于规则的方法**
- 正则表达式、词典匹配
- 适用于专有名词（人名、地名、组织名）

**2. 传统 ML 方法**
- HMM（隐马尔可夫模型）
- CRF（条件随机场）：当前主流传统方法
```python
# CRF 示例
import sklearn_crfsuite
crf = sklearn_crfsuite.CRF()
crf.fit(X_train, y_train)
```

**3. 深度学习方法**
- BiLSTM-CRF：捕获序列上下文 + 标签约束
- BERT-BiLSTM-CRF：预训练语言模型增强
```python
# BERT + CRF 结构
# Input -> BERT -> BiLSTM -> CRF -> Tags
```

**4. 本项目的实现**
质检助手模块中，对话解析器会提取：
- speaker（发言者）：客户/客服
- emotion（情绪标签）
- intent（意图标签）

---

### Q3: 对话摘要怎么做？有哪些方法？

**参考答案：**

对话摘要是将长对话压缩为简短摘要的任务。

**方法分类：**

**1. 抽取式摘要**
- 从原文中直接选择重要句子/片段
- TextRank、TextTeaser 算法
- 优点：事实一致性好
- 缺点：表达生硬

**2. 生成式摘要**
- Seq2Seq 模型：LSTM/Transformer
- 预训练模型：T5、BART、Pegasus
```python
# T5 对话摘要示例
from transformers import T5ForConditionalGeneration, T5Tokenizer
model = T5ForConditionalGeneration.from_pretrained('t5-base')
inputs = tokenizer("summarize: " + dialogue, return_tensors="pt")
summary_ids = model.generate(inputs['input_ids'])
summary = tokenizer.decode(summary_ids[0])
```

**3. 面向任务的对话摘要**
- 本项目质检模块的对话解析
```python
class ConversationParser:
    def parse_conversation(self, raw_content: str) -> ParsedConversation:
        # 提取关键信息：
        # - session_id
        # - participants
        # - turns (对话轮次)
        # - metadata
```

---

### Q4: 情感分类有哪些方法？如何处理金融场景的负面情感？

**参考答案：**

**情感分类方法：**

**1. 基于词典的方法**
```python
# 简单词典匹配
positive_words = ["好", "棒", "赞", "满意"]
negative_words = ["差", "烂", "失望", "投诉"]

def sentiment_score(text):
    pos = sum(1 for w in positive_words if w in text)
    neg = sum(1 for w in negative_words if w in text)
    return pos - neg
```

**2. 深度学习模型**
- TextCNN、DPCNN
- BERT 等预训练模型微调

**3. 大模型方法**
```python
prompt = f"""
分析以下客服对话的情感倾向：
{dialogue}

输出：正面/中性/负面，并说明理由：
"""
```

**金融场景负面情感处理（本项目）：**

```python
class CustomerEmotions:
    POSITIVE = "积极"
    NEUTRAL = "中立"
    NEGATIVE = "消极"
    ANGRY = "愤怒"      # 金融场景高风险
    ANXIOUS = "焦虑"     # 催收场景常见

# 质检模块会自动识别情绪
class AutoInspector:
    def evaluate_service_attitude(self, agent_responses):
        # 检测回复中是否出现安抚性语言
        # 负面情绪触发升级机制
        pass
```

---

### Q5: 词向量有哪些？Word2Vec 的原理？

**参考答案：**

**词向量发展：**

| 方法 | 代表模型 | 特点 |
|------|----------|------|
| One-hot | - | 稀疏、高维、无语义 |
| SVD | LSA | 降维、但仍浅层 |
| Word2Vec | Skip-gram, CBOW | 浅层、语义好 |
| GloVe | - | 结合共现统计 |
| BERT | Transformer | 深层、双向、上下文相关 |

**Word2Vec 原理：**

```
Word2Vec 两种架构：

1. CBOW (Continuous Bag-of-Words)
   上下文 -> 预测中心词
   "我 爱 NLP" -> 预测 "机器"

2. Skip-gram
   中心词 -> 预测上下文
   "机器" -> 预测 "我 爱 NLP"

核心思想：相似词有相似上下文
```

**训练优化：**
- 负采样（Negative Sampling）
- 层次 Softmax（Hierarchical Softmax）

---

## 2. 大模型相关问题

### Q6: RAG 的核心原理？为什么要用 RAG？

**参考答案：**

**RAG = Retrieval-Augmented Generation**

```
┌──────────────────────────────────────────────────────────┐
│                      RAG 架构流程                          │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   用户问题                                                │
│       │                                                  │
│       ▼                                                  │
│   ┌─────────┐                                            │
│   │  检索   │  ──►  向量数据库（Chroma）                 │
│   └────┬────┘           │                                │
│        │                ▼                                │
│        │         相似文档 Top-K                          │
│        │                │                                │
│        ▼                │                                │
│   ┌─────────┐           │                                │
│   │  生成   │ ◄── 召回的文档作为上下文                   │
│   └────┬────┘                                            │
│        │                                                  │
│        ▼                                                  │
│   最终回答                                                │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**RAG 核心代码（本项目话术推荐模块）：**

```python
class ScriptRecommender:
    def recommend_scripts(self, context, intent, count=3):
        # 1. 构建查询
        query_text = f"情境: {context.topic}, 意图: {intent.intent_type}"

        # 2. 向量检索
        results = self.vector_db.query_with_score(
            query_text=[query_text],
            n_results=count * 2  # 多召回一些候选
        )

        # 3. LLM 重排序
        scripts = self.rank_scripts(results, context)

        return scripts
```

**为什么用 RAG：**
1. **知识更新**：无需重新训练，实时更新知识库
2. **可控性**：答案可追溯、可审核
3. **减少幻觉**：基于真实文档回答
4. **成本低**：比微调便宜

---

### Q7: LangChain 的核心组件？用过哪些？

**参考答案：**

**LangChain 核心组件（本项目使用了部分）：**

| 组件 | 说明 | 本项目应用 |
|------|------|-----------|
| Models | LLM 接口封装 | ✅ DashScope SDK |
| Prompts | Prompt 模板管理 | ✅ PromptTemplates |
| Chains | Chain 调用序列 | 部分实现 |
| Agents | 工具调用代理 | 未使用 |
| Memory | 对话记忆 | 未使用 |
| Indexes | 文档加载/检索 | ✅ Chroma |

**本项目的 Prompt 模板管理：**

```python
class PromptTemplates:
    """提示词模板常量"""

    INTENT_CLASSIFY = """
请对以下客户查询进行意图分类：

查询内容：{query}

可选类别：{categories}

请返回分类结果和置信度。
"""

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
```

---

### Q8: LoRA 微调的原理？与全量微调的区别？

**参考答案：**

**LoRA（Low-Rank Adaptation）原理：**

```
全量微调：更新所有参数 W → W' = W + ΔW
         参数量 = d × d（巨大）

LoRA：   冻结 W，只更新低秩分解的 A、B
         W' = W + ΔW = W + BA
         参数量 = d × r + r × d（很小）

其中 r << d，如 r=8
```

**核心思想：**
- 预训练模型的参数矩阵是低秩的
- 只需微调小的低秩矩阵即可

**与全量微调对比：**

| 维度 | 全量微调 | LoRA |
|------|----------|------|
| 参数量 | 全部 | 1-10% |
| GPU 显存 | 需 80G+ A100 | 24G 即可 |
| 训练时间 | 数天 | 数小时 |
| 效果 | 略好 | 持平 |
| 适用场景 | 数据充足 | 数据有限 |

**本项目选择 RAG 而非微调的原因：**
1. 银行场景合规性要求高，微调需重新评估
2. 知识更新频繁，RAG 更灵活
3. 数据标注成本高

---

### Q9: RLHF 的三个步骤？PPO 算法的作用？

**参考答案：**

**RLHF（Reinforcement Learning from Human Feedback）：**

```
步骤 1: SFT（有监督微调）
   高质量问答对 ──► 训练基础语言模型

步骤 2: 奖励模型
   生成多个回答 ──► 人工排序 ──► 训练奖励模型 RM
   学习目标：奖励模型输出的分数排序 = 人工排序

步骤 3: PPO 强化学习
   语言模型生成回答 ──► 奖励模型打分 ──► 更新语言模型
   目标：最大化期望奖励
```

**PPO（Proximal Policy Optimization）作用：**
- 稳定强化学习训练
- 限制策略更新幅度，防止崩溃
- 平衡探索与利用

**本项目的质检评分机制类似 RLHF 思路：**
```python
class InspectionReport:
    overall_score: float       # 总体评分
    attitude_score: float     # 态度评分
    professionalism_score: float  # 专业性评分
    compliance_score: float   # 合规性评分
```

---

### Q10: 如何解决大模型的幻觉问题？

**参考答案：**

**幻觉（Hallucination）：模型生成看似正确但实际错误的内容**

**解决方案：**

**1. RAG（检索增强）**
```python
# 基于真实文档回答，减少臆测
context = retrieve_relevant_docs(query)
answer = llm.generate(f"基于以下信息回答：{context}\n问题：{query}")
```

**2. Prompt 工程**
```python
prompt = f"""
你是一个银行客服助手。
要求：
1. 只根据给定的上下文信息回答
2. 如果信息不足，明确说"我不确定"
3. 不要编造数字或事实

上下文：{context}
问题：{query}
"""
```

**3. Chain of Thought（思维链）**
```python
prompt = f"""
问题：{query}

请分步骤思考：
1. 首先理解问题
2. 检索相关知识
3. 基于事实回答
4. 如果不确定，明确说明

回答：
"""
```

**4. 答案验证**
- 对生成内容做事实性检查
- 与知识库交叉验证

---

## 3. RAG 与知识库问题

### Q11: 向量数据库有哪些？Chroma 的原理？

**参考答案：**

**主流向量数据库：**

| 数据库 | 特点 | 适用场景 |
|--------|------|----------|
| Pinecone | 云原生、托管 | 企业级 |
| Weaviate | 混合搜索 | 混合检索 |
| Qdrant | Rust 实现 | 高性能 |
| Milvus | 开源、国产 | 大规模 |
| Chroma | 轻量、易用 | 入门/中小规模 |

**Chroma 原理：**

```python
# 本项目使用的 Chroma 客户端
import chromadb

# 1. 创建客户端
client = chromadb.PersistentClient(path="./chroma_data")

# 2. 创建集合（类似表）
collection = client.get_or_create_collection(
    name="customer_service_knowledge"
)

# 3. 添加文档（自动向量化）
collection.add(
    documents=["信用卡如何申请？"],
    metadatas=[{"category": "信用卡"}],
    ids=["1"]
)

# 4. 相似性检索
results = collection.query(
    query_texts=["我想办卡"],
    n_results=5
)
```

**向量检索原理：**
- 余弦相似度 / L2 距离
- HNSW 近似最近邻算法
- IVF-PQ 倒排索引

---

### Q12: 如何提升 RAG 的召回率？

**参考答案：**

**召回率 = 相关文档被召回的比例**

**优化策略：**

**1. query 改写**
```python
class IntentClassifier:
    def rewrite_query(self, query):
        # 将口语化 query 转为标准表达
        prompt = f"将以下查询改写为更精确的表达：{query}"
        return llm.generate_text(prompt)
```

**2. 混合检索**
```python
# 关键词 + 向量 混合
bm25_results = bm25_search(query)
vector_results = vector_db.query(query)
combined = merge_results(bm25_results, vector_results)
```

**3. 元数据过滤**
```python
# 结合业务属性精确筛选
results = vector_db.query(
    query_texts=[query],
    n_results=10,
    where={"category": "信用卡", "customer_type": "VIP"}
)
```

**4. 重排序（Re-ranking）**
```python
# 初筛后用 LLM 做二次排序
class ScriptRecommender:
    def rank_scripts(self, scripts, context):
        # LLM 评估每个话术与情境的相关性
        # 返回排序后的话术列表
```

---

### Q13: LangGraph 是什么？与 LangChain 的区别？

**参考答案：**

**LangGraph = LangChain + 状态机**

LangGraph 是 LangChain 的扩展，专门用于构建有状态的、多步骤的 LLM 应用。

**LangChain Chains：** 线性执行
```
Input -> Chain Step 1 -> Chain Step 2 -> ... -> Output
```

**LangGraph：** 有向图，可循环、可条件分支
```
    ┌──────────────────┐
    │                  ▼
Input -> Node A ──► Decision ──► Node B ──► Output
                   │              │
                   ▼              ▼
               Node C ───────────┘
                   ▲
                   └── (循环可返回)
```

**本项目质检工作流可以用 LangGraph 重构：**
```python
from langgraph import StateGraph

# 定义工作流
workflow = StateGraph(InspectionState)

workflow.add_node("parse", parse_conversation)
workflow.add_node("transcribe", transcribe_audio)  # 可选
workflow.add_node("inspect", auto_inspect)
workflow.add_node("generate_report", generate_report)

workflow.set_entry_point("parse")
workflow.add_edge("parse", "inspect")
workflow.add_edge("inspect", "generate_report")
workflow.add_edge("generate_report", END)
```

---

## 4. 系统设计问题

### Q14: 如何设计高可用的 AI 服务？

**参考答案：**

**五层高可用架构（本项目实践）：**

```
┌─────────────────────────────────────────────────────────┐
│                    1. 负载均衡层                         │
│              Nginx / API Gateway 限流熔断               │
├─────────────────────────────────────────────────────────┤
│                    2. 降级策略层                         │
│    三级降级：服务器模式 → 持久化 → 只读模式              │
├─────────────────────────────────────────────────────────┤
│                    3. 缓存层                            │
│      Redis 缓存热点 │ 多级缓存 │ 布隆过滤器防穿透        │
├─────────────────────────────────────────────────────────┤
│                    4. 重试层                            │
│    指数退避 │ 熔断器 │ 超时控制                         │
├─────────────────────────────────────────────────────────┤
│                    5. 监控层                            │
│      QPS │ 延迟 P99 │ 错误率 │ 业务指标监控              │
└─────────────────────────────────────────────────────────┘
```

**本项目的具体实现：**

```python
# 1. 三级降级（VectorDBClient）
def _init_client(self):
    if not self._try_server_mode():
        if not self._try_persistent_mode():
            self._try_ephemeral_mode()  # 兜底

# 2. 指数退避重试（LLMClient）
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((APIConnectionError, APITimeoutError))
)
def generate_text(self, prompt):
    pass

# 3. 缓存 + 布隆过滤器
class CacheManager:
    def get(self, key):
        if not self._might_exist_in_bloom(key):  # 布隆过滤
            return None  # 直接返回，不查缓存
        return self._redis.get(key) or self._memory.get(key)
```

---

### Q15: FastAPI 依赖注入的原理？

**参考答案：**

**依赖注入（Dependency Injection）：**

控制反转（IoC）的一种实现，框架自动解析依赖关系并注入。

```python
# 本项目的依赖注入示例

# 1. 定义依赖
def get_llm_client() -> LLMClient:
    return LLMClient()  # 简单版本

# 2. 使用 Depends 声明依赖
@router.post("/classify-intent")
async def classify_intent(
    request: ClassifyIntentRequest,
    classifier: IntentClassifier = Depends(get_intent_classifier)
):
    # FastAPI 自动：
    # 1. 调用 get_intent_classifier()
    # 2. 获取返回值
    # 3. 注入到函数参数
    return classifier.classify_intent(request.query)

# 3. 带缓存的单例模式
@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    return LLMClient()  # 全局只有一个实例
```

**原理：**
- FastAPI 使用 `Depends` 函数收集依赖图
- 按需创建实例
- 配合装饰器可实现单例

---

### Q16: 如何优化 API 响应延迟？

**参考答案：**

**本项目的性能优化实践：**

| 优化点 | 方案 | 效果 |
|--------|------|------|
| 连接复用 | @lru_cache 单例 | -60% 延迟 |
| 缓存 | Redis + 内存缓存 | -70% 延迟 |
| 并发 | asyncio.gather | 10x 吞吐量 |
| 降级 | 避免慢查询 | +99% 可用性 |
| 限流 | 快速失败 | 保护下游 |

**代码示例：**

```python
# 1. 单例模式
@lru_cache(maxsize=1)
def get_llm_client():
    return LLMClient()

# 2. 并发执行
async def batch_process(items):
    tasks = [process_item(item) for item in items]
    return await asyncio.gather(*tasks)

# 3. 缓存
@cache(ttl=300)
def classify_intent(query):
    return llm.classify_intent(query)
```

---

### Q17: 分布式缓存的一致性问题？

**参考答案：**

**缓存一致性三种模式：**

**1. Cache Aside（旁路缓存）- 最常用**
```python
# 读：缓存优先，miss 后查库并写入缓存
def get_user(user_id):
    cached = redis.get(f"user:{user_id}")
    if cached:
        return cached
    user = db.query(user_id)
    redis.setex(f"user:{user_id}", 3600, user)
    return user

# 写：先更新数据库，再删除缓存（不是更新！）
def update_user(user_id, data):
    db.update(user_id, data)
    redis.delete(f"user:{user_id}")  # 删除而非更新
```

**2. Write Through（写穿透）**
- 写缓存时同步写数据库
- 一致性强，但性能差

**3. Write Behind（写回）**
- 异步写数据库
- 性能最佳，但有数据丢失风险

**本项目使用策略：Cache Aside + 短 TTL**

---

## 5. 金融业务问题

### Q18: 银行客服场景有哪些特殊要求？

**参考答案：**

**合规性要求：**
1. **话术合规**：不能承诺不确定的事情、不能误导客户
2. **隐私保护**：客户信息脱敏、对话记录加密存储
3. **审计追溯**：所有对话可查、可追溯
4. **质检覆盖**：100% 质检或高比例抽检

**本项目的质检维度：**
```python
class InspectionDimensions:
    SERVICE_ATTITUDE = "服务态度"      # 是否友好、耐心
    PROFESSIONALISM = "专业性"          # 解答是否准确
    PROBLEM_SOLVING = "问题解决能力"    # 是否解决问题
    COMPLIANCE = "合规性"              # 是否遵守规范
    COMMUNICATION = "沟通技巧"          # 表达是否清晰
```

**情绪识别：**
```python
# 金融场景需要特别关注负面情绪
class CustomerEmotions:
    ANGRY = "愤怒"    # 投诉风险
    ANXIOUS = "焦虑"  # 催收场景常见
```

---

### Q19: 金融风控场景如何用 NLP？

**参考答案：**

**NLP 在金融风控的应用：**

**1. 文本分类 - 舆情监控**
```python
# 情感分析判断市场情绪
sentiment = analyze(news_article)  # 正面/负面/中性
```

**2. 实体抽取 - 风险识别**
```python
# 从贷款申请中提取关键信息
entities = extract_entities(application_text)
# 提取：收入、负债、工作年限等
```

**3. 对话分析 - 反欺诈**
```python
# 客服对话中检测欺诈意图
if "转账" in dialogue and "安全账户" in dialogue:
    flag_fraud_alert()  # 典型诈骗话术
```

**4. 智能催收 - 意图识别**
```python
# 判断债务人还款意愿
intents = ["承诺还款", "困难延期", "拒绝还款", "失联"]
```

---

### Q20: 为什么不选择微调而用 RAG？

**参考答案：**

**本项目选择 RAG 的核心原因：**

| 维度 | RAG | 微调 |
|------|-----|------|
| 知识更新 | 实时，热更新 | 需重新训练 |
| 成本 | 低 | 高（GPU + 标注） |
| 可解释性 | 答案可追溯 | 黑盒 |
| 合规审核 | 文档级审核 | 模型级审核 |
| 灵活性 | 多知识源 | 单一体 |

**银行场景的合规考量：**
1. 模型输出需可解释、可审计
2. 频繁的知识更新（产品/政策）
3. 敏感信息处理
4. 监管要求可追溯

**RAG 架构在本项目的优势：**
```python
# 话术推荐 = RAG 检索 + LLM 生成
# 答案可追溯到具体话术文档
# 新话术上线只需更新向量数据库
```

---

## 6. 代码与项目问题

### Q21: 这个项目你遇到的最大技术挑战？

**参考答案：**

**参考回答：**

> "我遇到的最大挑战是向量数据库故障时的服务连续性问题。
>
> 最初实现中，Chroma 连接失败会直接降级到内存模式，但内存模式服务重启后数据全部丢失，导致知识库需要重新导入。
>
> 我设计了三级降级策略：
> 1. 服务器模式：连接远程 Chroma，数据持久化
> 2. 持久化模式：本地持久化存储，服务重启保留
> 3. 只读模式：纯内存，兜底可用
>
> 配合健康检查接口，运维可以实时监控系统状态。这个方案将服务可用性从 99% 提升到 99.9%。"

---

### Q22: 你如何保证代码质量？

**参考答案：**

**四层质量保障体系：**

```yaml
1. 代码规范
   - PEP 8 + Black 格式化
   - Type Hints 类型提示
   - Pydantic 数据验证

2. 静态检查
   - flake8 代码风格
   - mypy 类型检查
   - bandit 安全扫描

3. 测试覆盖
   - 单元测试 (pytest)
   - 集成测试 (TestClient)
   - 性能基准测试

4. CI/CD
   - GitHub Actions 自动测试
   - 覆盖率门禁 (≥80%)
   - 安全扫描
```

**本项目的测试示例：**

```python
# test_units.py
def test_intent_classifier():
    classifier = IntentClassifier(mock_llm_client)

    result = classifier.classify_intent(
        query="我想查询信用卡账单",
        categories=["账户查询", "业务办理", "投诉"]
    )

    assert result.primary_intent == "账户查询"
    assert result.confidence > 0.5
```

---

### Q23: 项目的性能瓶颈在哪？如何优化？

**参考答案：**

**主要性能瓶颈：**

| 瓶颈 | 原因 | 优化方案 |
|------|------|----------|
| LLM API 调用 | 串行等待 | asyncio 并发 |
| 向量检索 | 频繁新建连接 | 连接池复用 |
| 缓存穿透 | 无防穿透机制 | 布隆过滤器 |
| 实例创建 | 每次请求 new | 单例模式 |

**具体优化代码：**

```python
# 1. 并发批量生成
async def batch_generate_async(self, prompts):
    tasks = [self.async_generate(p) for p in prompts]
    return await asyncio.gather(*tasks)

# 2. 单例模式
@lru_cache(maxsize=1)
def get_llm_client():
    return LLMClient()

# 3. 布隆过滤器防穿透
if not bloom_filter.might_contain(key):
    return None  # 快速返回
```

---

### Q24: 未来的技术规划？

**参考答案：**

**参考方向：**

> "短期（3-6个月）：
> 1. 引入 LangGraph 实现复杂对话流程
> 2. 添加流式输出支持，提升用户体验
> 3. 完善监控告警体系
>
> 中期（6-12个月）：
> 1. 探索 LoRA 微调，针对银行场景优化
> 2. 引入 embedding 模型微调，提升召回率
> 3. 构建反馈闭环，持续优化话术推荐
>
> 长期：
> 1. 多模态支持（语音/图像）
> 2. Agent 自主决策能力
> 3. 跨语言客服支持"

---

### Q25: 你在这个项目的具体贡献？

**参考答案：**

**STAR 法则回答：**

> "**项目背景**：苏商银行客服中心智能化升级
>
> **我的角色**：核心模块开发 + 技术负责人
>
> **具体工作**：
> 1. 设计并实现 LLMClient 和 VectorDBClient 两大核心模块
> 2. 开发客服知识库助手的话术抽取和意图识别功能
> 3. 实现话术推荐模块的 RAG 架构
> 4. 设计三级降级策略保证服务高可用
>
> **量化成果**：
> - 话术推荐准确率提升 35%
> - API 响应时间降低 60%
> - 服务可用性达 99.9%"

---

### Q26: 为什么离职/看新机会？

**参考答案：**

**参考话术：**

> "在上一家公司我负责的客服 AI 项目已经取得阶段成果：
> - 从 0 到 1 搭建了整套技术架构
> - 项目已经稳定运行并得到业务方认可
>
> 现在希望：
> 1. 在金融 NLP 领域深耕，接触更复杂的业务场景
> 2. 加入规模更大的团队，学习规范的工程实践
> 3. 专注于大模型技术在金融行业的应用落地"

---

### Q27: 你的职业规划？

**参考答案：**

**参考回答：**

> "短期（1-2年）：
> - 深入 NLP/大模型领域，成为技术专家
> - 在金融科技方向积累业务经验
>
> 中期（3-5年）：
> - 技术管理能力成长，能带团队
> - 参与大型项目的架构设计
>
> 我对贵司的 NLP/金融方向很感兴趣，希望能在这个领域深耕发展。"

---

### Q28-30: 手写算法题

**Q28: 实现 LRU 缓存**

```python
class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = {}  # key -> value
        self.order = []  # 访问顺序

    def get(self, key: int) -> int:
        if key not in self.cache:
            return -1
        # 移到末尾（最近使用）
        self.order.remove(key)
        self.order.append(key)
        return self.cache[key]

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            # 淘汰最老的
            oldest = self.order.pop(0)
            self.cache.pop(oldest)

        self.cache[key] = value
        self.order.append(key)
```

**Q29: 实现向量相似度计算（余弦相似度）**

```python
import numpy as np

def cosine_similarity(a: List[float], b: List[float]) -> float:
    a = np.array(a)
    b = np.array(b)

    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(dot_product / (norm_a * norm_b))
```

**Q30: 实现简单的 Prompt 模板渲染**

```python
def render_template(template: str, **kwargs) -> str:
    """简单的 Prompt 模板渲染"""
    result = template
    for key, value in kwargs.items():
        placeholder = f"{{{key}}}"
        result = result.replace(placeholder, str(value))
    return result

# 测试
template = "用户输入：{query}\n可选类别：{categories}\n请分类："
result = render_template(template,
    query="我的信用卡丢了",
    categories="账户查询、业务办理、投诉"
)
print(result)
```

---

## 附录：面试准备清单

### 技术知识点自查

| 类别 | 必须掌握 | 加分项 |
|------|----------|--------|
| NLP | 意图识别、NER、情感分类、RAG | 对话状态跟踪、槽位填充 |
| 大模型 | Prompt 工程、RAG、LoRA、SFT | RLHF、PPO、Agent |
| 向量数据库 | Chroma 操作原理、相似度计算 | HNSW 算法、索引优化 |
| 系统设计 | 降级策略、缓存、并发 | 分布式一致性、高可用架构 |
| 工程 | FastAPI、异步编程、测试 | Docker、K8s、CI/CD |

### 项目细节准备

- [ ] 能画出完整系统架构图
- [ ] 能解释每个模块的技术选型原因
- [ ] 能量化项目成果（准确率、延迟、吞吐量）
- [ ] 能说出一到两个解决的难题

---

*面试题库版本: 2026-03-25*
*岗位: NLP/金融算法工程师*
