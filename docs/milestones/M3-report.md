# M3 验收报告：有依据的坐席辅助

状态：READY_FOR_ACCEPTANCE
实现代码SHA：见 `feat/bank-assistant` HEAD（T16 合并后为 `324 passed` 的提交）
本机/Ubuntu一致性：本机已验证；Ubuntu 在 M3 收尾同步（M1 已证明双端 144 用例一致）
相关需求：FR-02、FR-04、FR-05；计划书 M3

## 交付内容与文件

| 提交 | 工单 | 内容 |
|---|---|---|
| `f61a636` | #15 T14 | 32 条演示知识、chunk 码点偏移、embedding 抽象（fastembed bge-small-zh-v1.5@512 + Fake）、索引版本化 + 校准向量哈希 + 原子指针、`make seed-demo`/`index-rebuild`/`index-info` |
| `2eb4fbb` | #16 T15 | `POST /api/v1/suggestions` 全链：过滤→校准校验→显式改写→检索→回库复核→结构化生成→引用校验→状态机；`model_runs` 记录模型/索引/SHA |
| `57f54d1` | #17 T16 | `POST /suggestions/{id}/feedback`（adopt/edit/reject + 理由）、append-only、`GET /suggestions/{id}` 读路径 |
| ADR-0005 | — | embedding 选型：fastembed（无 torch）、索引版本化、文件指针；ADR-0004 加取代指针 |

## 验收逐条结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| 每场景 ≥5 条端到端用例 | PASS | normal 6 / missing 5 / expired 5 / insufficient 6 / conflicting 5（`tests/test_suggestions.py`） |
| 含正常、知识缺失、过期版本、条件不足、冲突知识 | PASS | 上述五类各有用例；冲突知识→`needs_clarification` |
| 费率、时间、办理结果不由模型自行补足 | PASS | 无依据→`needs_human`（不调模型）；引用必须来自本次召回且有效版本，否则拒绝并记 `rejected_citations` |
| 候选答复可以打开引用原文 | PASS | 引用携带 chunk/version id 与原文 quote，`GET` 可回查；chunk 偏移用码点（中文/emoji/换行有测试） |
| 原问题与改写版本均可运行并留痕 | PASS | `query` 与 `rewritten_query` 同时落库，检索快照记录所用改写 |
| 不预写"提升"结论 | PASS | 未做检索质量结论；对比实验在 M6 |
| 草稿/过期/未来/跨 scope 不出现在候选答复 | PASS | SQL 层过滤 + 索引前过滤，均有测试 |
| 生成期间知识退役（CT-02） | PASS | 重新检索并重生成一次，否则 `needs_human` |
| `failed` 仅表示系统失败 | PASS | 模型/索引故障→`failed`（HTTP 5xx 且保留 failed 行）；业务状态不伪装成 failed |
| 演示知识全部合成/公开来源 | PASS | 32 条：`demo_only` 虚构制度正文注明；公开条文仅引用 4 个 gov.cn 链接（200 校验） |

## 指标与样本量

- 单元测试：**324 passed / 0 failed**（69 integration 默认排除）。
- 集成测试：**69 passed**（真实 PostgreSQL 18 + Chroma 本地索引 + 真实 fastembed 冒烟 1 passed）。
- 演示知识：32 条（三场景各 10 + 2 条边界）；索引 30 版本/30 分块，索引版本 `idx-20261112T084715Z-b6bb3496`，校准 sha256 `cffa0671…`。
- 迁移 head：单一 `0008_suggestion_feedback`。

## 失败、跳过、环境限制

- 候选答复链路在测试中使用假模型网关（不烧钱、可复现）；**真实模型链路**在 M5 演示与 M6 评测中验证，M3 不声称模型效果。
- 索引活动指针目前是文件（`data/index/active.json`），多实例部署时需改为共享存储或数据库指针（M7 处理）。
- 语音转写不在首版。

## 数据/模型/知识/规则/索引版本

- embedding：`BAAI/bge-small-zh-v1.5`（512 维，ONNX/CPU，无 torch）。
- 索引：`idx-20261112T084715Z-b6bb3496`，清单含知识快照与校准向量哈希。
- 生成模型：DeepSeek 官方 `deepseek-chat`（ADR-0004）；测试不调用。

## 兼容性与迁移

- 新增迁移：`0006_knowledge_chunks`、`0007_suggestions`、`0008_suggestion_feedback`，线性单一 head。
- 契约文档已同步 `GET /suggestions/{id}` 读路径（T16）。

## 遗留风险和外部依赖

1. 真实模型链路尚未在建议接口上跑过（M5 演示必须验一次并留痕）。
2. 索引指针为单机文件，M7 需评估多实例共享方案。
3. 演示知识里的监管条链接只验证了可访问性，未逐条核对正文（M6 评测前人工复核）。

## 下一阶段入口

M4：T17（规则版本与演示规则集，#18）即将完成 → T18（质检任务执行）→ T19（证据定位）→ T20（复核）→ T21（导出）。

## 负责人验收

待负责人填写结果与时间；执行模型不代填。
