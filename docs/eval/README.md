# M6 固定评测与基线对比（T27 / #28）

本目录说明 `make eval` 的口径、产物与已知限制。评测对象是 T26 冻结的
合成数据集（`data/datasets/bank-cs-m6-synthetic/v1/manifest.json`）中的
**test split**；`dev` 用于调参、`train` 用于训练，二者都会被
`bank_assistant.datasets.split_guard.require_frozen_test_split` 拒绝。

## 快速开始

```bash
# 1) 构建/冻结数据集（若 ARTIFACT_ROOT 下还没有产物）
make dataset-build
make dataset-verify        # 复算 manifest 与文件哈希

# 2) 回放评测：全量 test，确定性替身，无网络、无模型调用、无成本
make eval

# 3) live 小样本：真实 DeepSeek 调用，20 条样本 + 2 元预算上限
make eval EVAL_CONFIG=configs/eval/live-subset.yaml
```

其他入口：

```bash
uv run python -m scripts.run_eval --config configs/eval/replay.yaml
uv run python -m scripts.run_eval --config configs/eval/live-subset.yaml --run-id my-run
```

退出码：`0` = succeeded；`1` = failed / budget_exceeded / blocked；`2` = 配置或
前置条件错误。CLI 的 stdout 只打印不含秘密的摘要 JSON。

## 两种模式严格分离

| 模式 | 模型 | embedding | 成本 | 用途 |
|---|---|---|---|---|
| `replay` | 确定性替身（`ReplayGateway` / `ReplayQueryRewriter`） | `replay-lexical-char2gram-v1`（离线词法，非生产） | 0 | 验证流水线、哈希闸门、产物结构、可复现性 |
| `live` | 真实 DeepSeek（`.env` 凭据） | 真实 BGE（fastembed，缓存/下载） | 按配置价目估算 | 真实延迟、token、成本 |

manifest 同时写 `mode`（live/replay）与 `model_mode`（契约 §6 的
cloud/local_model/replay）。**replay 结果不得混进 live 报告**；replay 的
`metrics.acceptance.gates_applied=false`，分数由词法 embedding 与替身模型产生，
只用于流程验证，不代表模型质量。

live 模式的 `max_items` 采用**分层抽样**：按场景轮转、场景内困难/正常交替，
保证三场景都进入子集且优先覆盖无答案/信息不足等困难样本；抽样确定性只依赖
`item_id`，不使用随机数。样本 ID 会写进 `metrics.run.sample`。

live 模式必须在配置里给出 `max_items`（样本上限）与 `budget_cny`（预算上限），
且价目表必须能匹配 `provider/model`。预算在每次调用前按估算 token 拦截、
调用后再按实际估算累计；触发上限时停止后续调用，manifest 记为
`budget_exceeded`，已完成结果保留。

## 检索对比（M6-04）

三个变体共用同一 `RetrievalHarness`（同一知识快照、同一索引版本）：

| 变体 | 查询 | 业务过滤 | 有效期过滤 |
|---|---|---|---|
| `original` | 原问题 | 否 | 否 |
| `contextual_concat` | 旧「情境拼接」形态：业务范围 + 原问题 | 否 | 否 |
| `core_intent_filter` | 核心诉求改写（live 用 DeepSeek；replay 用确定性剥离） | 是 | 是 |

报告：Recall@5（版本/文档级，主口径；分块级作为附加诊断）、MRR、按场景分母、
无答案/信息不足子集的处理率、答案样本误拒率、过期知识泄漏率、延迟 p50/p95。
无相关样本的样本不进入 Recall/@MRR 分母，单独计入无答案子集。

## 质检对比（M6-04）

判定单元是「会话 × 适用规则」，同一规则快照下比较：

- `rule_baseline`：确定性关键词 + 否定/转述语境 + 规则适用范围；
- `llm_baseline`：真实质检提示词与 schema 校验（replay 用确定性朴素模型）；
- `combined`：规则优先、模型补位；模型失败时降级为规则结论。

报告逐规则 Precision/Recall/F1（含 TP/FP/FN/support 分母）、macro-F1（只在有
支持或有预测的规则上平均并记录参与规则数）、高风险规则 Recall、证据定位
（预测区间落在金标区间内/完全一致的比例）、四态 verdict 与 `failed` 的计数和
占比。**没有一条判定会被静默删除**：模型失败记为 `failed`，在
`system_failures` 与逐规则 `failed` 中保留。

## 指标与诚实性约定

- **引用合法性**：只接受本次授权快照内的引用；其余显式丢弃并计入
  `invalid_citations_dropped`，最终 `citation_validity=1.0` 由构造保证并在代码中
  断言（`run_inspection` / `runner._assert_citations`）。
- **引用支持度**：本数据集没有人工语义支持标签，报告显式写
  `citation.support.status = "NOT_MEASURED"`，不用合法性冒充支持度。
- **token/成本为估算**：CJK 每字 1 token、其他非空白字符每 4 字符 1 token；
  成本 = token/1e6 × 配置单价。价目表必须带 `source` 与 `as_of`；`live-subset.yaml`
  使用 DeepSeek 官方 **历史价目快照**（2025-02-09），2026 年可能已调整，报告中的
  成本一律标注 `estimated`，不是账单。relay 没有公开价目表，故意不配置。
- **质量门槛**：计划 §7 的门槛（Recall@5 ≥ 0.85、无答案处理 ≥ 0.90、
  macro-F1 ≥ 0.80、高风险 Recall ≥ 0.95 等）登记在报告里，但 replay 与
  20 条 live 子集都 `gates_applied=false`，不宣称达标。

## 产物

```
<ARTIFACT_ROOT>/evals/<run_id>/
├── manifest.json      # 契约 §6：run_id/code_sha/mode/数据与快照哈希/模型/索引/seed/状态/路径
├── metrics.json       # 检索/质检指标、失败案例、成本、门槛登记
└── predictions.jsonl  # 逐条检索与质检结果（含失败样本）
```

`metrics_path` / `predictions_path` 是相对 `ARTIFACT_ROOT` 的路径；manifest 写入前
会递归扫描键名与值，命中疑似凭据（`sk-…`、`*_API_KEY=`、`api_key` 字段等）
直接拒绝。失败实验也保留目录，不静默覆盖。

## 意图分类评测（T28 / #29，条件任务）

T28 的意图分类**不复用** #28 的检索/质检 runner（那套只认
`retrieval`/`inspection`，指标是 Recall/规则 P-R-F1），但复用同一套冻结口径、
哈希闸门、manifest/凭据扫描约定与产物目录约定。这是有意的：意图 accuracy
不能混进检索/质检指标表；复用"闸门"而不是复制"指标"。

- 数据集先经 `bank_assistant.datasets.build.verify_dataset` 复算 manifest 与
  文件哈希；test 文件哈希必须与 manifest 的 `frozen.test_split_sha256` 一致；
- 训练只接受 `train` split，正式评测只接受冻结 `test`
  （`split_guard.require_split_for` / `require_frozen_test_split`）；
- 产物写到 `artifacts/training/<run_id>/{manifest.json,model-card.md,config.json}`，
  manifest 记录 code sha、数据/标签 schema 哈希、超参、seed、硬件、显存峰值、
  耗时与状态；
- baseline 与 tuned 使用**同一基础模型、同一 prompt、同一解码参数、同一 seed**：
  baseline 关闭 LoRA adapter（zero-shot），tuned 启用 adapter；test 只在训练后
  评一次，不参与训练或 checkpoint 选择。

指标：accuracy、macro-F1（只在有支持/预测的标签上平均）、逐标签
Precision/Recall/F1/support、混淆矩阵、无效输出计数、困难样本子集准确率。
样本量小（train 202 / test 50），报告只给实测值与分母，不宣称统计显著。

```bash
make train                                        # 默认 dry-run：不训练，写 NOT_RUN 报告
make train TRAIN_ARGS=--execute                   # 真实训练（需空闲 8GB GPU）
make train TRAIN_CONFIG=configs/train/qlora-intent-qwen3-1.7b.yaml
uv run python -m scripts.train_intent_lora --config configs/train/qlora-intent-qwen3-0.6b.yaml --dry-run
```

dry-run 退出码 `0`（报告可以是 `NOT_RUN`）；配置/split/数据集哈希错误退出 `2`；
真实训练因资源不足而 `NOT_RUN` 退出 `1` 并保留报告。详见
[M6 微调报告](../milestones/M6-tuning-report.md)。

## 数据库与 ADR-0006（重要）

评测与运行时共享同一份**快照口径**：应用从应用数据库读取 published 的
知识/规则版本；本评测从冻结数据集绑定的合成语料快照读取，并把同一组
`knowledge_snapshot` / `rule_snapshot` 哈希写进 manifest 与指标。两者不一致时
`run_eval` 会拒绝运行（快照漂移闸门）。

因此：

- **本评测运行器不直接连接应用数据库**；它需要的是与 DB 一致的冻结快照。
  正式在 Ubuntu 复跑时，必须先确认应用 DB 已由同一份语料/规则种子导入，
  且快照哈希与数据集 manifest 一致（当前没有自动 DB 对账，属于已知限制）。
- 应用运行、`make seed-demo`、集成测试仍然需要数据库。**当前基础设施是过渡性
  的本机容器**，不是 ADR-0006 的「PostgreSQL 18 只部署在 Ubuntu + SSH 隧道」
  方案；ADR-0006 **尚未实现**，本项目**不声称** ADR-0006 合规。
- ADR-0006 要求数据库不可达时显式失败/BLOCKED，不得静默切 SQLite。本评测在
  replay 模式不依赖 DB；live 模式只依赖模型凭据与网络，凭据/能力缺失时由
  `runner._blocked_run` 写 `status: blocked` 的 manifest，不伪造成功。

## 已知限制

- 回放 embedding 是词法哈希，不是 BGE；回放分数与 live 分数不可直接比较。
- test split 只有 50 条检索、48 条质检（无 `insufficient_info`、仅 5 条
  `no_answer`）；live 子集更小，统计区间很宽，报告只给实测值与分母。
- `min_similarity` 在 replay（0.10）与 live（0.55）不同：前者是流水线校验阈值，
  后者对应生产默认；任何跨模式比较都必须先说明这一点。
- 标注来源见数据集 manifest：`developer_single_annotator`、`human_gold=false`；
  这不是人工金标，也不是双人一致性。
