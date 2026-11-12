# M6 合成评测数据集 v1（bank-cs-m6-synthetic）

本目录只保存**小文件**：`manifest.json` 与每个 split 的 5 条样本；完整拆分（大文件）
落在 `ARTIFACT_ROOT/data/bank-cs-m6-synthetic/v1/`，已被 `.gitignore` 忽略，可用构建
脚本重建。全部数据为**合成数据**：三场景虚构演示知识与公开监管要点摘要的组合，
由确定性模板生成，不含任何真实银行内部资料、真实客户或真实通话。

## 规模与冻结事实

| 项目 | 值 |
|---|---|
| 数据集 | `bank-cs-m6-synthetic` / `v1` |
| 生成种子 | `42` |
| 检索查询 | 330（每场景 110） |
| 质检会话 | 186（每场景 62） |
| 总计 | 516（每场景 172） |
| 困难样本 | 每场景 48 条（27.9%），每个 split 均 ≥20% |
| split | train 318 / dev 100 / test 98（约 61.6% / 19.4% / 19.0%） |
| 分组 | 151 组；按组拆分，同组不跨 split |

精确计数、分组、每文件 sha256、`dataset_manifest_sha256`、
`split_manifest_sha256` 与 `test_split_sha256` 以 `manifest.json` 为准。

## 复现与校验

```bash
# 构建（默认写 $ARTIFACT_ROOT/data/...，并发布本目录的 manifest 与样本）
uv run python -m scripts.build_dataset --seed 42
# 或 make dataset-build

# 复算 manifest 与全部文件哈希（不重新构建）
uv run python -m scripts.build_dataset \
  --verify "${ARTIFACT_ROOT:-artifacts}/data/bank-cs-m6-synthetic/v1"
# 或 make dataset-verify

# 固定构建时间可让 manifest 逐字节一致
SOURCE_DATE_EPOCH=1762948800 uv run python -m scripts.build_dataset --no-committed
```

同一 `--seed` 与 `SOURCE_DATE_EPOCH` 下两次构建的文件与 manifest 完全一致。
`dataset_manifest_sha256` 覆盖数据、标签、拆分与标注口径，**不覆盖** `created_at`、
`code_sha`、`worktree_clean`，因此同一份数据在 Mac/Ubuntu 的不同 checkout 上哈希相同；
代码状态仍记录在 manifest 里供审计。

## 分组与拆分口径

- 检索：`ret:<场景>:<知识来源 doc_id>:<意图簇>`。同一知识来源、同一意图的不同问法
  属于同源改写，必须同组。
- 质检：`ins:<场景>:<会话模板家族>:<意图簇>`。同一模板家族的变体同组。
- 先按 `场景 × 正常/困难` 分层，再按组做 60/20/20 贪心分配；**从不按单条样本拆**，
  因此不存在同源改写跨 train/dev/test 的泄漏。
- 困难样本六类：`no_answer`、`insufficient_info`、`expired_knowledge`、`negation`、
  `quoted_speech`、`ambiguous_intent`。每场景每类都有独立模板（检索 5 条、质检 3 条）。

## 标签规范

### 检索

```json
"labels": {
  "relevant_doc_ids": ["lost-card-demo-03"],
  "relevant_chunk_ids": ["lost-card-demo-03#c0"],
  "chunk_spans": [{"chunk_id": "lost-card-demo-03#c0", "doc_id": "lost-card-demo-03",
                   "start": 12, "end": 210, "text_sha256": "..."}],
  "answerable": true,
  "excluded_doc_ids": [],
  "unanswerable_reason": null
}
```

- `doc_id` 是 `data/demo/knowledge/` 里的知识条目 id；
- `chunk_id` 是数据集内稳定键 `<doc_id>#c<序号>`，与
  `bank_assistant.knowledge.chunking.chunk_text` 使用同一套切分；评测时按
  `(doc_id, start, end)` 映射到真实索引的 chunk（UUID）；
- 无答案样本 `answerable=false`：`no_answer` / `insufficient_info` 的
  `unanswerable_reason` 即原因，`expired_knowledge` 另给出 `excluded_doc_ids`
  （已过期或尚未生效、不得进入正式检索的版本）。

### 质检

每段会话对 6 条演示规则逐条给出 `verdict`（`hit` / `clear` /
`insufficient_evidence` / `not_applicable`）、`rationale` 与可逐字回验的
`evidence`（`turn_id` + 码点区间 + 原文）。跨场景规则按 `business_type`
标为 `not_applicable`，理由引用规则 `applicability`，不用“没出现关键词”冒充。
`hit` 必须有证据且必须来自坐席发言；否定/引用语境不能按关键词判命中。

## 标注记录（如实，不夸大）

- `annotation.method = "developer_single_annotator"`，只有一名开发者；
  `double_annotated_pct = 0`，**没有第二标注人，不声称双人一致性**；
- 标签由确定性合成模板按构造产生（`label_origin = "synthetic_by_construction"`），
  不是人工金标、不是业务金标（`human_gold=false`、`business_gold=false`）；
- 开发者做过 10% 抽样复核，但没有独立的人工标注记录可当作 gold；
- `self_consistency` 是**确定性重标注自检**：对 10% 样本用同一生成器重标注两次并
  比较，另用 M4 的否定/转述确定性检查复核可复算标签。它是健全性指标，
  **明确不是标注者间一致性（IAA）**，也不替代人工验收。

## 冻结与版本纪律

- test split 与 manifest 绑定 `test_split_sha256`；同 `dataset_version` 下若内容变化，
  `write_dataset` 会拒绝覆盖并抛 `FrozenSplitError`，必须提升 `dataset_version`
  重新冻结（并保留旧版本 manifest 供审计）。
- 训练/评测脚本必须调用 `bank_assistant.datasets.split_guard.require_split_for`：
  训练只允许 `train`，评测只允许 `dev`/`test`；否则显式失败。
- 测试集一旦参与调参，就必须按 M6 要求建立新的独立验收集，不能继续把旧 test
  当留出集。

## 已知限制

- 内容与话术是模板合成，覆盖面受模板设计限制；不能据此推断真实业务分布。
- 标签是生成约束的产物，无法替代人工复核；本项目没有业务人员可做验收。
- 检索标签在 doc/chunk 层，不包含相关性等级（只分 relevance 与无答案）；
  引用支持度、Recall@5、MRR 等指标由 M6 评测脚本消费本数据集计算。
