# M6 条件性微调报告：4-bit QLoRA 意图分类（T28 / #29 → T35 / #35 真实执行）

**状态：`SUCCEEDED`（2026-11-13 在 Ubuntu RTX 3060 Ti 上真实训练 + 冻结 test 评测）**
**决策：冻结 test 上 tuned 优于 zero-shot baseline（accuracy 0.64 → 0.76）；作为实验结论保留，不接生产、不发布模型。**
**报告日期：2026-11-13（Asia/Shanghai）**
**执行代码 SHA：`1746d16`（`ticket/T35`，服务器工作树 `clean=true`；训练前基线 `45f2a4e` = `feat/bank-assistant`）**
**运行 ID：`t35-qwen3-1.7b-20261113T1007Z`**

这是一份 **model card 式的诚实报告**。T28（#29）是条件任务，2026-11-12 因资源不足
写 `NOT_RUN`；2026-11-13 负责人停止另一项目并**明确授权使用服务器 GPU** 后，
T35（#35）执行了真实训练。本文档把结论从"未执行"改成实测结果，并保留 T28 的
资源证据与未执行理由，便于复核当时判断没有造假。

---

## 1. 结论

| 项 | 结果 |
|---|---|
| 训练 harness / 配置 / 测试 | 已交付（T28）；T35 补齐评测确定性、loss/步数记录 |
| dry-run（真实 tokenizer + GPU 探测） | **SUCCEEDED**（Ubuntu，2026-11-13） |
| 真实 4-bit QLoRA 训练 | **SUCCEEDED**（Qwen3-1.7B，26 optimizer steps，97.9 s 训练段） |
| 冻结 test 上 baseline vs tuned | **可比较**：accuracy 0.64 → **0.76**（+0.12），macro-F1 0.6138 → **0.7441**（+0.1304），n=50 |
| 配对检验（McNemar 精确检验） | baseline 对 / tuned 错 **0** 条，baseline 错 / tuned 对 **6** 条；双侧 p=0.0312 |
| 是否改善 | **有提升**；但样本量小（n=50）、合成数据、主残差仍在 `lost_card` 类，**不宣称统计显著/业务有效** |
| 生产是否使用微调模型 | **否**；adapter 只作为实验产物，不进入发布包 |

---

## 2. 历史：T28 为什么是 `NOT_RUN`（2026-11-12，保留证据）

2026-11-12 18:48 SSH 只读采集：

```text
RTX 3060 Ti 8192 MiB，已用 1457 MiB / 空闲 6361 MiB，util 0%
compute app: pid 14219, /home/jack/.venvs/dingtalk-embed/bin/python, 1280 MiB
deps: torch/transformers/peft/bitsandbytes 均未安装；HF 缓存 1.4 MB（无权重）
```

当时的结论是"按条件任务规则不该在未获准的资源上开跑"：GPU 有无关进程、依赖与
权重未就绪、未获 time-sharing 授权。harness、配置、测试、dry-run 均已交付，
manifest 记 `NOT_RUN`（`dry-run-20261112T1105Z`，manifest sha256 `e20b982d…`）。
本节保留原判断，不做事后改写。

---

## 3. 本次资源与 GPU 争用证据（2026-11-13）

负责人授权后**没有 kill 任何进程、没有停服务、没有重启服务器**：

| 时刻 | 事实 |
|---|---|
| 10:36 只读探测 | 8192 MiB 总显存；已用 1771 MiB，空闲 6047 MiB；`dingtalk-embed`（pid 1903）仍占 1280 MiB；磁盘 141 GiB 可用；RAM 57 GiB 可用 |
| 训练前 manifest `gpu` | `busy=true`，`compute_processes=[dingtalk-embed pid=1903 1280 MiB]`，`free_vram_gib=6.21`，util 0% |
| 训练期 2 s 采样（独立 `nvidia-smi` 循环） | **全卡占用峰值 4670 MiB / 8192 MiB**；训练进程峰值约 **3198 MiB** |
| torch 分配器 | `vram_peak_allocated=2.706 GiB`，`vram_peak_reserved=2.949 GiB`（manifest `vram_peak_gib` + `notes.vram_peak_reserved_gib`） |
| 结果 | 1.7B 4-bit QLoRA 在 6.0 GiB 空闲下**没有 OOM**，无需回退到 0.6B |

模型选择依据：ADR-0004 允许 0.6B–7B；1.7B 是候选里较大的一档，4-bit 权重约
1.1 GiB + bf16 embedding 约 0.6 GiB，实测峰值远低于可用显存，因此**优先执行
1.7B**；0.6B 仅作为若 OOM 的备用候选（本次未使用）。

配置里 `hardware.allow_time_sharing: true` 是本次**唯一**相对 T28 候选配置的
差异，理由与授权写在 `configs/train/qlora-intent-qwen3-1.7b-t35.yaml` 头部；
`min_free_vram_gib: 2.0` 闸门保留（低于 2 GiB 仍拒绝启动）。

---

## 4. 环境与模型准备

| 项 | 值 |
|---|---|
| 主机 / Python | Ubuntu `jack-MS-7C94`；uv 托管 CPython **3.13.14**；uv **0.11.28** |
| 依赖安装 | `uv sync --frozen --group train`，**耗时 6 分 35 秒**（含 2–3 GB wheel 解包）；`.venv` 共 **5.9 GB** |
| 版本 | torch **2.15.0+cu132**、transformers **4.57.6**、peft **0.21.2**、bitsandbytes **0.50.2**、accelerate **1.15.0** |
| 驱动 | 580.178.04（CUDA 13.0 驱动，torch 为 cu132 构建，实测 `torch.cuda.is_available()=True`） |
| 模型下载 | `Qwen/Qwen3-1.7B` @ `main`（snapshot `70d244cc…`），**3.8 GB，耗时 1 分 55 秒** |
| HF endpoint | `HF_ENDPOINT=https://huggingface.co`（直连可用：tokenizer 实测 2.37 MB/s，**未使用镜像**；`hf-mirror.com` 实测 1.88 MB/s 仅作对比） |
| 数据集 | `uv run python -m scripts.build_dataset --artifact-root artifacts` → `artifacts/data/bank-cs-m6-synthetic/v1`，`--verify ok=true`；train/test 哈希见 §5 |

未使用任何云 API，无 API 费用；本次只有电费量级成本。

---

## 5. 实际执行（dry-run → `--execute`）

```bash
cd ~/workspace/bank-assistant/repo
uv run python -m scripts.train_intent_lora \
  --config configs/train/qlora-intent-qwen3-1.7b-t35.yaml --dry-run --allow-download \
  --tiny-smoke --run-id t35-dryrun-20261113T1005Z
uv run python -m scripts.train_intent_lora \
  --config configs/train/qlora-intent-qwen3-1.7b-t35.yaml --execute --allow-download \
  --run-id t35-qwen3-1.7b-20261113T1007Z
```

dry-run（4.3 s，退出码 0，manifest 记 `NOT_RUN`）：

| 步骤 | 结果 |
|---|---|
| 硬件探测 | GPU 可用，`busy=true`，空闲 6.21 GiB |
| 数据加载 | train **202** 条 / test **50** 条，哈希复算通过 |
| 真实分词 | `Qwen2TokenizerFast`；完整训练序列（prompt+标签+EOS）train mean 108.74 / p95 115 / **max 119**；test mean 107.98 / max 116；`max_seq_length=512` 越界 **0** |
| 微型 CPU 前反向 | SUCCEEDED（随机 2 层 Qwen2+LoRA，loss 4.889397，trainable 896） |
| 依赖 | torch/transformers/peft/bitsandbytes/accelerate 全部 present |

真实执行（2026-11-13 10:46:58 → 10:49:09 +08:00）：

| 记录项 | 实测值 |
|---|---|
| run_id | `t35-qwen3-1.7b-20261113T1007Z` |
| code_sha / worktree | `1746d16f…` / `clean=true` |
| 模型 | `Qwen/Qwen3-1.7B`，revision `main`，Apache-2.0 |
| train / test | 202 / 50；train sha256 `538b3847…`，test sha256 `869a8bd0…` |
| dataset/split manifest | `32a43ff9…` / `aba5375a…`；label schema 见 manifest |
| 超参 | 4-bit NF4 + double quant + bf16；LoRA r=16 α=32 dropout=0.05（q/k/v/o）；seq=512；batch=1；grad accum=16；2 epochs；lr=1e-4 cosine；warmup 0.03；gradient checkpointing |
| optimizer steps | **26**（404 micro-steps = 202×2） |
| 训练 token 量 | 约 **44k**（202×2×108.74 平均序列长度，未打包） |
| seed | 42（train/data/eval 同一 seed） |
| 训练段耗时 | **97.9 s**（`train_runtime`，trainer 报告的纯训练时间） |
| 总 wall time | **130.4 s**（manifest `wall_time_seconds`，含加载/保存/评测） |
| loss | 逐次 logging：10.1466 → 6.1494 → 2.705 → 1.1334 → **0.3179**（末次，epoch 1.95）；全程平均 `train_loss=3.9522` |
| vram_peak | **2.706 GiB**（torch allocated）；reserved 2.949 GiB；nvidia-smi 全卡峰值 4670 MiB |
| 保存策略 | `save_strategy="no"`；`checkpoints/` 为空目录（可证明 test 未用于挑 checkpoint） |

产物 sha256（`artifacts/training/t35-qwen3-1.7b-20261113T1007Z/`，gitignore 不提交）：

| 文件 | sha256 |
|---|---|
| `manifest.json` | `ab2dc2646b0d3c6632d662a343136ba6f4b5ba7eb4056936abe3950eb00700a9` |
| `model-card.md` | `9761ab9211166770a398d21ad09b6b83d64db5a47a1f84cec65a5a2d35eab838` |
| `training-log.json` | `39a0069f24326de85efb29a730922f55525fd6e6383df555975dd6c20bca762a` |
| `eval/comparison.json` | `bf5c36d02e0903c550e3d1595dabe5b7f5b08ae6bc621b3d94dd74c34a1e8b7d` |
| `adapter/adapter_model.safetensors` | `412920c245833169edf7b4fe73fd954004b95ae955c1efa0e34583f307f9ebde` |

---

## 6. 冻结 test 评测（baseline vs tuned）

协议：同一基础模型、同一 prompt（`intent-routing-v1`）、同一解码参数
（greedy，`max_new_tokens=8`）、同一 seed；baseline 关闭 LoRA adapter（zero-shot），
tuned 启用 adapter；**test 只在训练结束后评一次**，不参与训练、不参与 checkpoint
选择（`save_strategy="no"`、只调用一次 `trainer.train()`、`checkpoints/` 为空）。
评测前显式 `model.eval()`，关闭 LoRA dropout，baseline/tuned 都是确定性解码。

| 指标 | baseline（zero-shot） | tuned（LoRA） | Δ |
|---|---|---|---|
| accuracy | 0.64（32/50） | **0.76（38/50）** | **+0.12** |
| macro-F1 | 0.6138（3 类） | **0.7441（3 类）** | **+0.1304** |
| 无效输出 | 1（`lost_card` 1 条未解析） | 0 | −1 |
| 困难样本子集 | 7/14 | 9/14 | +2 |
| 多数类参考下限 | accuracy 0.30 / macro-F1 0.1538（`complaint`，15/50） | — | — |

逐标签（tuned / baseline）：

| 标签 | support | baseline P/R/F1 | tuned P/R/F1 |
|---|---|---|---|
| `complaint` | 15 | 1.000 / 0.800 / 0.889 | 0.875 / 0.933 / 0.903 |
| `credit_card_fee` | 17 | 0.500 / 1.000 / 0.667 | 0.654 / 1.000 / 0.791 |
| `lost_card` | 18 | 1.000 / 0.167 / 0.286 | 0.875 / 0.389 / 0.538 |

混淆矩阵要点（行=期望，列=预测）：

- `lost_card → credit_card_fee` 是最主要的错误：baseline **14/18**，tuned **9/18**；
  `lost_card` 召回 0.167 → 0.389，是 macro-F1 提升的主要来源。
- tuned 把 `complaint` 从 baseline 的 3 条误判改正（`complaint` 召回 0.800 → 0.933），
  代价是 1 条 `complaint` 被误判成 `lost_card`。
- `credit_card_fee` 召回保持 1.000（17/17），精确率 0.500 → 0.654。

配对检验：50 条逐条配对，baseline 对且 tuned 错 **0** 条、baseline 错且 tuned 对
**6** 条（McNemar 精确检验双侧 **p=0.0312**）。方向上没有"改对又改错"的抵消，
但 n=50 的置信区间依然很宽，**不宣称统计显著**，更不宣称业务有效。

数据泄漏检查：test 的 50 条文本与 train 检索集的 202 条**交集为 0**；训练代码只
编码 `train` split，`split_guard` 在配置加载阶段拒绝非 train 训练 split。

---

## 7. 交付物

| 文件 | 作用 |
|---|---|
| `scripts/train_intent_lora.py` | CLI：`--dry-run` / `--execute`；退出码 0/1/2 |
| `configs/train/qlora-intent-qwen3-0.6b.yaml` | T28 主配置（历史保留，未改动） |
| `configs/train/qlora-intent-qwen3-1.7b.yaml` | T28 备用候选（历史保留，未改动） |
| `configs/train/qlora-intent-qwen3-1.7b-t35.yaml` | **本次执行配置**：与 1.7B 候选超参一致，仅 `allow_time_sharing: true`（负责人 2026-11-13 授权） |
| `src/bank_assistant/training/trainer.py` | T35 补齐：评测前 `model.eval()`；`training-log.json`（loss/步数/显存峰值）；loss/步数写入 manifest |
| `src/bank_assistant/training/{config,dataset,evaluate,manifest,hardware}.py` | 配置校验、split 守卫、哈希复算、指标、manifest/model card、GPU 探针（T28 交付） |
| `tests/test_train_*.py`（7 个文件） | 离线测试：配置、split 守卫、哈希、manifest、model card、退出码、GPU 不可用路径、指标 |

测试：`uv run pytest tests -q` 见 §10；训练相关测试离线（不联网、不需要 GPU）。

---

## 8. 复现命令

```bash
# 0) 服务器（Ubuntu，RTX 3060 Ti 8GB；本实验使用 GPU 已获负责人授权）
ssh my-ubuntu-server
cd ~/workspace/bank-assistant/repo
git checkout ticket/T35        # 本次执行 SHA 1746d16

# 1) 依赖（约 2–3 GB，实测 6m35s；缓存可复用）
uv sync --frozen --group train

# 2) 冻结数据集（确定性构建 + 哈希校验）
uv run python -m scripts.build_dataset --artifact-root artifacts
uv run python -m scripts.build_dataset --verify artifacts/data/bank-cs-m6-synthetic/v1

# 3) 模型权重（3.8 GB；直连可用，不需要镜像）
HF_ENDPOINT=https://huggingface.co uv run python - <<'PY'
from huggingface_hub import snapshot_download
print(snapshot_download("Qwen/Qwen3-1.7B", revision="main"))
PY

# 4) dry-run（真实 tokenizer/硬件；写 NOT_RUN 报告，退出码 0）
uv run python -m scripts.train_intent_lora \
  --config configs/train/qlora-intent-qwen3-1.7b-t35.yaml --dry-run --allow-download --tiny-smoke

# 5) 真实训练 + 冻结 test 评测（约 2 分钟）
uv run python -m scripts.train_intent_lora \
  --config configs/train/qlora-intent-qwen3-1.7b-t35.yaml --execute --allow-download
```

退出码：`0` 成功（或 dry-run 完成）；`1` 资源/依赖/执行失败（报告仍写出）；
`2` 配置、split 守卫、数据集哈希等前置条件错误。

---

## 9. 已知限制

- **26 个 optimizer step 是预注册的小预算**（2 epochs × grad accum 16）；没有为了
  好看结果加训或改超参。若做后续实验，应改 `num_train_epochs`/`max_steps` 后
  重新预注册，而不是在本次 test 上再调。
- test 只有 50 条，paired p=0.0312 只是一个窄结论；`lost_card` 召回 0.389 仍然低，
  主要混淆是 `lost_card → credit_card_fee`（9/18）。
- 标签用 `business_type`（lost_card / credit_card_fee / complaint）：数据集细粒度
  `intent_cluster` 在 train/dev/test 之间不重叠，不能作为跨 split 监督目标。
- 合成数据 + 单人按构造标注（`human_gold=false`、`business_gold=false`），不是
  人工/业务金标；结果不能外推到真实银行客服。
- 只用了一个基础模型（1.7B）；7B 未试，8GB 上仍贴近上限。
- adapter（run 目录 40 MB）只作实验产物，未发布、未接生产、未进入 `release.json`。
- dry-run 的 tokenization 在 Ubuntu 真实缓存上复测（与本机 T28 记录一致），不是
  沿用旧数字。

---

## 10. 验收标准 #35 对照

| 验收标准 | 结论 | 证据 |
|---|---|---|
| 训练真实执行；manifest 含 vram_peak、wall_time、code_sha、数据哈希、seed | **PASS** | `t35-qwen3-1.7b-20261113T1007Z/manifest.json`：`status=SUCCEEDED`、`vram_peak_gib=2.706`、`wall_time_seconds=130.385`、`code_sha=1746d16…`、`train_split_sha256=538b3847…`、`test_split_sha256=869a8bd0…`、`seed=42`；`training-log.json` 含 loss 曲线与 reserved 峰值 |
| 冻结 test 上 baseline vs tuned 对比表（含分母与混淆矩阵要点） | **PASS** | §6：accuracy 0.64(32/50) → 0.76(38/50)；macro-F1 0.6138 → 0.7441；逐标签 P/R/F1/support 与混淆矩阵要点；配对 McNemar p=0.0312 |
| 测试集未参与训练/调参（split_guard + 保存策略可证明） | **PASS** | `config.py` 加载时 `require_split_for("train", "train")` / `require_frozen_test_split("test")`；trainer 只编码 train 202 条；`save_strategy="no"`、只调用一次 `trainer.train()`、`checkpoints/` 为空；test 文本与 train 交集 0；test 只在训练后评一次 |
| 报告写明结论（含"无显著提升"也接受）与复现命令 | **PASS** | 本文件 §1/§6/§8；结论是"有提升但不宣称显著/业务有效" |
| 不 kill 其它项目进程；不重启服务器 | **PASS** | `dingtalk-embed` pid 1903 全程存活（训练前/中/后均见 1280 MiB）；未执行 reboot；只做只读 `nvidia-smi` 采样 |

---

## 11. 与 T28 `NOT_RUN` 的关系

T28 的结论没有错：当时确实没有可用的 GPU 窗口、依赖与权重。T35 只是把条件
补齐后执行同一条链路，所以本报告 §2 保留原 `NOT_RUN` 证据，§5/§6 换成真实
运行与对比。**不要在引用旧版本文档时把 `NOT_RUN` 当成"微调无效"**——旧版本
明确写了"不能声称微调有效或无效"。
