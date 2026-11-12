# 生成用 DeepSeek 官方、第三方中继仅作对比；embedding 自托管 BGE

状态：accepted（2026-11-12，依据当日实测修订）

2026-11-12 对两条链路做了真实调用探测：

| 链路 | 结果 |
|---|---|
| DeepSeek 官方 `api.deepseek.com/v1` | `GET /models` 返回 `deepseek-flash`（V4.1-Flash）与 `deepseek-v4-pro`，上下文 1M；`deepseek-chat` 调用 **200 / 1.18s / 39 tokens / finish=stop**，无 reasoning 空转；`POST /embeddings` 返回 **404**（确认不提供 embedding） |
| 第三方中继 `llm.nangeagi.com/v1` | 328 个模型，chat 与 embeddings 可用；但 `gpt-5.5-pro`/`glm-4.5-air`/`/rerank` 返回 **503**，`deepseek-v4-flash-0731` 连接被断开；`gpt-5-nano`/`qwen3.6-plus` 在 `max_tokens` 偏小时返回 **200 + 空 content + finish_reason=length**；embedding `text-embedding-3-small` 实测 **1536 维** |

决定：

1. **生成主用 DeepSeek 官方**（`deepseek-chat`）。它更快（1.18s vs 4.7s）、没有 reasoning 空转、模型标识可核验；第三方中继的模型名（`gpt-5.5`、`qwen3.6-plus`、`claude-fable-5`）无法确认实际权重，只能算同一运营方下的**模型标签对比**，不是跨供应商验证，M6 报告必须这么写。
2. **embedding 自托管 BGE**（`BAAI/bge-m3`，1024 维；可用 `bge-large-zh-v1.5` 替代），在 Ubuntu 建库、Mac 用同一模型做本地开发。理由：DeepSeek 官方没有 embedding；中继的 embedding 身份不可验证，若运营方静默换模型，索引会**无声劣化**（维度相同、质量漂移），而 `index_version` 无法自证。中继 `text-embedding-3-small` 保留为对照与冒烟回退，不得作为正式索引来源。
3. **索引完整性自证**：建库时用一个固定校准字符串生成向量，把向量哈希写进索引清单；每次加载索引前校验，不一致即判定 embedding 模型已变、拒绝使用并要求重建。这是对第三方 embedding 的廉价兜底，也适用于自托管模型的版本漂移。

## 适配层必须处理的失败形态

- `finish_reason=length` 且 content 为空 → **失败**，绝不能当空答复往下传（否则"模型没说话"会被记成"无答案/证据不足"，污染评测指标）；`max_tokens` 默认 3000。
- 中继 503 / 连接断开 → 有限重试（次数与费用双上限）+ 显式失败状态，不得无上限重试；回退切换必须写进运行 manifest 的 `model_provider`，不能静默换模型。
- 中继响应含非标准字段（如 `latency_checkpoint`、`annotations`）→ 解析必须容忍额外字段。
- `/v1/rerank` 不可用 → M3 重排不依赖中继：优先本地 `BAAI/bge-reranker-v2-m3`，或先用确定性排序，把 rerank 做成可替换组件。

## 微调

Ubuntu 的 RTX 3060 Ti **8GB** + 62 GiB RAM 在 4-bit QLoRA 下**能跑 7B**，但贴着上限（约 6.5–8 GiB，需 seq 512–1024、batch=1、梯度检查点、梯度累积，seq 由 dry-run 实测决定）。8B 及以上、16-bit LoRA、全参微调均不可行。微调是绑定 M6 冻结测试集之后的**条件任务**，任务只选「意图分类」，候选限定 0.6B–7B（Qwen3-0.6B/1.7B/4B、Qwen2.5-1.5B/3B/7B、Llama-3.2-1B/3B），每次实验记录模型来源与许可、数据哈希、超参、随机种子、显存占用、耗时与代码 SHA；显存或时间不满足即写 `NOT_RUN` 并说明原因。

## 后果

- 凭据只放 `.env`（已 gitignore，600 权限），不进 Git、不进日志、不进文档；两把 key 已在对话中出现过，项目跑通后建议轮换。
- 每次模型调用记账（provider/model/token/成本）；中继没有公开价目表，成本按 token 估算并受 ¥200/月硬上限约束，测试脚本必须带样本上限。
- 演示前必须跑一次「真实模型链路」冒烟（一次 chat + 一次 embedding 成功），才允许声称"真实模型参与"。
- 服务器 GPU 与 `dingtalk-train` 共享，训练前确认空闲，禁止并发；8GB 上 7B 推理只能走 llama.cpp/GPTQ，不能上 vLLM。
