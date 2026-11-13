# M6 验收报告：效果证据与模型优化

状态：READY_FOR_ACCEPTANCE（含明确的未达门槛与 NOT_RUN 项）
实现代码SHA：`feat/bank-assistant`（T26/T27/T28 合并后）
本机/Ubuntu一致性：Ubuntu 上**未复跑评测**（待负责人给 GPU/时间窗后补，见遗留）
相关需求：FR-09；计划书 M6、§7 验收门槛

## 交付内容

| 工单 | 内容 | 提交 |
|---|---|---|
| #27 T26 | 合成数据集构建 + 分组拆分 + 冻结 manifest | `b4c9839`、`89fc32d` |
| #28 T27 | 评测 harness、replay 全量与 live 小样本、run manifest 落 ARTIFACT_ROOT | `716ff47`、`b5926a6` |
| #29 T28 | 4-bit QLoRA 训练 harness + 资源探测 + dry-run + `NOT_RUN` 报告 | `55cc799` |

## 数据集（已冻结）

| 项 | 值 |
|---|---|
| 检索查询 | 330 条（110/场景） |
| 质检会话 | 186 段（62/场景） |
| 困难样本 | 每场景 48 条（**27.91%**，覆盖无答案/信息不足/过期知识/否定/引述/歧义） |
| 拆分 | 按 151 个模板族/意图簇分组：train/dev/test = 318/100/98（61.6/19.4/19.0），同源改写不跨 split |
| 冻结哈希 | `dataset_manifest=32a43ff9…`、`split_manifest=aba5375a…`、`test_split=869a8bd0…` |
| 可复现 | 固定 `SOURCE_DATE_EPOCH`，两次构建逐字节一致；`make dataset-verify` ok |
| 标注口径 | `developer_single_annotator`、`double_annotated_pct=0`、`human_gold=false`、`business_gold=false`（诚实标注，不冒充人工金标） |

## 评测结果

### 检索（冻结 test，50 条）

| 变体 | Recall@5 | MRR |
|---|---|---|
| 原始问题 | 0.800 | 0.575 |
| 情境拼接（现状实现） | 0.556 | 0.340 |
| **核心诉求改写 + 业务过滤** | **0.833** | **0.660** |
| 核心诉求分场景 | lost 0.962 / fee 0.941 / complaint 0.600 | — |
| 无答案处理（分母 5） | original 2 / contextual 0 / core 5 | — |

### 质检（replay，替身模型）

| 基线 | macro-F1 | 高风险 Recall | 证据定位 |
|---|---|---|---|
| 规则 | 1.0 | 1.0 | 1.0 |
| LLM | 1.0 | 1.0 | 1.0 |
| 组合 | 1.0 | 1.0 | 1.0 |

> **这组 1.0 来自确定性替身模型，不是质量证据**，只证明管线与指标计算可用。

### 质检（live 小样本，20 条分层，真实 DeepSeek）

| 基线 | macro-F1 | 高风险 Recall |
|---|---|---|
| 规则 | 1.0 | 1.0 |
| LLM | 0.333 | 0.286 |
| 组合 | 1.0 | 1.0 |

- 8 次 LLM 系统失败（如实计入，不剔除）。
- 120 次模型调用：p50 **1234.9 ms**、p95 **1659.1 ms**；token 107,007 in / 15,532 out（估算）；成本 **¥0.33827**（估算，预算 ¥2.0）。

## 与 §7 门槛的对照（不粉饰）

| 门槛 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 检索 Recall@5 | ≥ 0.85（固定 test，按场景） | replay 全量核心诉求 0.833；live 20 条 1.0（样本过小） | **未达标**（0.833 < 0.85），complaint 0.600 为主要拖累 |
| 无答案处理 | ≥ 0.90 | 5 条子集，core 5/5、original 2/5 | 分母过小，**不足以判定** |
| 引用合法性 | 100% | 100%（构造性保证 + 断言） | 达标 |
| 引用支持度 | ≥ 0.95（人工抽检 ≥50） | 未标注支持度标签 | **NOT_MEASURED**，不宣称 |
| 质检 macro-F1 | ≥ 0.80 | 仅有 20 条 live 小样本，规则/组合 1.0、LLM 0.333 | 小样本，**不作为达标准据** |
| 高风险规则 Recall | ≥ 0.95 | 同上，LLM 基线 0.286 | 小样本，**不足以判定** |
| 证据定位 | 精确原文 100% + 人工相关性 ≥0.95 | 精确 100%；人工相关性未标注 | 部分达标，人工部分 **NOT_MEASURED** |
| 候选答复 P95 | ≤ 15 s | live p95 1.66 s（120 次调用） | 达标（小样本） |
| 评测可复跑 | `make eval EVAL_CONFIG=` | replay 全量 + live 小样本均可复跑 | 达标（Ubuntu 复跑待补） |

## 条件性微调：NOT_RUN（有证据的未执行）

- GPU 事实（2026-11-12 18:48 SSH 只读）：RTX 3060 Ti 8192 MiB，已用 1457 / 空闲 6361 MiB，util 0%；唯一 compute app 是用户的 `dingtalk-embed`（1280 MiB），**未杀、未排队**。
- 依赖（train 组约 2–3 GB）与权重（Qwen3-0.6B 约 1.5 GB）未就绪；未获 GPU 分时授权。
- 已执行：dry-run（train 202 / test 50、哈希复算、token p95=115、CPU 微型 Qwen2+LoRA 一次 fwd/bwd loss=4.8894），manifest `e20b982d…`、`vram_peak=null` 且有说明。
- 不宣称效果：无 baseline vs tuned 对比；只给多数类参考（accuracy 0.30 / macro-F1 0.1538）。
- 若要执行：负责人给 GPU 时间窗/共享授权 + 允许装 2–3 GB 依赖并下载 1.5 GB 权重，0.6B 预估 5–15 分钟。

## 交付规则核对

- `docs/milestones/M6-tuning-report.md`：模型卡 + 资源证据 + NOT_RUN 决策与后续条件。
- run manifest 按契约 §6：`artifacts/evals/eval-replay-…`（sha256 `a98b2d11…`）、`artifacts/evals/eval-live-…`（sha256 `d1ab369e…`），均含 code_sha、worktree_clean、dataset/split 哈希、index/knowledge/rule 快照、模型标识与时间。
- 测试集未参与训练或调参（`split_guard` + 训练脚本拒收非 train split；有测试）。

## 遗留与下一步

1. **Ubuntu 复跑未执行**：`make eval` 目前在 Mac 上跑（DB 经隧道），需在 Ubuntu 上复跑一次才算完全满足计划书 M6 门槛。
2. **引用支持度/人工相关性未标注**：需要人工抽检 ≥50 条，属负责人/业务侧工作，模型不能自称完成。
3. **Recall@5 未达 0.85**：complaint 场景 0.600 是主要短板，下一步应先修检索/知识覆盖，而不是上微调。
4. 微调为条件任务，默认不阻塞 M7；如需执行，等待 GPU 窗口与安装授权。
5. relay 无公开价目表，成本为估算值，已在报告中标注。

## 负责人验收

负责人已确认验收：**2026-11-13 已签收（对话中明确「已签收」）**。执行模型仅记录该事实，未代填。

> 验收记录续：随后负责人授权继续推进，并要求使用已释放的 GPU 资源；复盘结论以各报告正文为准。

