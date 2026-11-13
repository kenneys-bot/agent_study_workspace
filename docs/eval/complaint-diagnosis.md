# complaint 检索失败诊断（T36 / #36）

状态：dev 诊断完成；test 只在最终评测报告一次（见 `docs/milestones/M6-report.md`）。
调参纪律：本文件所有统计均来自 **dev split**（23 条 answerable complaint 检索样本，
其中 7 条困难样本，全部保留、未剔除）；未读取 test 的逐条预测。

## 口径

- 数据：`artifacts/data/bank-cs-m6-synthetic/v1/dev.jsonl`，dev sha256
  `650fc9b2…c9f1`（与冻结 manifest 一致，调参脚本启动时校验）。
- 检索栈：replay 配置 `configs/eval/replay.yaml`；确定性词法 embedding
  `replay-lexical-char2gram-v1@512` + `ReplayQueryRewriter`；主变体
  `core_intent_filter`（核心诉求改写 + 硬业务过滤 + 有效期过滤）。
- 指标：文档级 Recall@5（主口径）与 MRR；分母 = answerable complaint 样本数 23。
- 复现：`uv run python -m scripts.tune_retrieval --scenario complaint \\
  --embedding-text chunk --rerank-alpha 0 --dump artifacts/t36/r0-baseline.dev.jsonl`
  （T36 新增入口；该脚本在参数层拒绝 test）。

dev complaint 基线：**Recall@5 = 0.8913（20.5 / 23）、MRR = 0.6210**。
失败 = 3 条；另有 8 条命中但相关文档不在第 1 位（MRR 损失，不改变召回）。

## 失败分类（基线：chunk + hard filter）

| 失败原因 | 条数 | 占失败 | 判定依据 |
|---|---:|---:|---|
| 业务过滤过严（跨业务相关文档不可达） | 3 | 100% | 金标相关文档中有 `credit_card_fee` 文档，硬过滤后候选只剩 complaint |
| chunk 边界 | 0 | 0% | complaint 11 篇知识全部只有 1 个 chunk（长度 121–146 字 < `max_chars=300`） |
| 查询改写丢失意图 | 0 | 0% | 3 条失败样本的改写只去外壳（标点/语气），核心词与金标意图一致 |
| 知识覆盖不足 | 0 | 0% | 全部金标文档存在、published 且在 `2026-06-01` 有效 |
| 标注不匹配 | 0 | 0% | 金标 chunk 存在，chunk 文本与查询意图一致（抽样复核 3/3） |

> 注：dev complaint 的 0.891 明显高于 M6 报告里 test complaint 的 0.600，
> 因此 dev 只能诊断“混合意图 + 硬业务过滤”这一类失败；test 上其余失败类型
> 需由最终 test 评测暴露，最终报告不据此反向调参。

## 样例（基线逐条预测）

### 1. `rq-complaint-h06-02`「我要投诉，顺便问下年费为什么收。」

- 金标：`complaint-demo-01`（投诉登记）、`fee-demo-01`（年费规则）→ 跨业务。
- 硬过滤后 Top5：`complaint-pub-02`、`complaint-pub-01`、`complaint-demo-05`、
  `complaint-pub-04`、`complaint-demo-02`；两个金标全不在。
- 不过滤时 `fee-demo-01` 排第 1。→ **业务过滤过严**。

### 2. `rq-complaint-h06-03`「投诉登记后能不能顺便查账单？」

- 金标：`complaint-demo-01`、`fee-demo-06`；硬过滤后只命中
  `complaint-demo-01`（第 2 位），`fee-demo-06` 因业务过滤不可达 → Recall 0.5。

### 3. `rq-complaint-h06-04`「我要投诉这个费用，算投诉还是算争议？」

- 金标：`complaint-demo-01`、`fee-demo-06`；硬过滤后 Top5 全为 complaint，
  两个金标均未进前 5 → Recall 0。

## 结构性观察（不改变本次失败分类，但解释了跨场景收益）

- embedding 输入原先只用 chunk 正文，**标题未参与**：例如
  `lost-card-pub-03` 标题含「挂失渠道」而正文把「挂失」「渠道」拆开；
  `fee-pub-03` 标题含「计息规则」而查询是「免息期和计息方式怎么算」。
- 这不是 complaint dev 的失败原因（该场景标题补充后召回不变），但它是
  fee/lost 场景 dev 失败的主因，也是 T36 的第一项改进（`embedding_text:
  title_chunk`）。
- 第二项改进是候选池内的确定性词面重排（`rerank_alpha`），dev 上 Recall@5
  不变、MRR 从 0.7248 升到 0.8044，用于压低“词面更贴近但语义无关”的排序噪声。
- 软业务过滤（`business_filter: soft` + 场景覆盖）已实现并可配置，但 dev 上
  complaint 只从 0.8913 升到 0.9130，同时整体 MRR 从 0.8044 降到 0.7824，
  且收益仅来自混合意图样本，因此**最终配置不启用**，保留为可回退开关。

## 改进后在 dev 上的位置

见 `docs/milestones/M6-report.md`「检索（T36 改进）」小节：dev 全量
Recall@5 0.8309 → 0.9485（分母 68），complaint 0.8913 → 0.8913（分母 23）。
complaint dev 的剩余 3 条失败全部是跨业务混合意图；硬过滤下的召回上限就是
20.5/23，除非启用软过滤。test 是否达到 ≥0.80 由最终一次 test 评测回答。
