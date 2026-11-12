# M6 条件性微调报告：4-bit QLoRA 意图分类（T28 / #29）

**状态：`NOT_RUN`（未执行真实训练）**
**决策：保留 T27 基线；不发布微调模型。**
**报告日期：2026-11-12（Asia/Shanghai）**
**代码基线：`ticket/T28`；T28 提交 `55cc799`，合并 `feat/bank-assistant` 后 `22ffe5e`（dry-run 即在此 SHA 的干净工作树上运行）**

这是一份 **model card 式的诚实报告**，不是道歉信。T28 是计划 M6-05/M6-07 的
**条件任务**：只有确认错误可由学习改善、数据足够、资源与费用明确时才训练。
本次资源条件不满足，因此交付完整训练/评测 harness、配置与测试，并把真实训练
明确记为 `NOT_RUN`，同时给出可复核的证据与重新执行所需的全部条件。

---

## 1. 结论

| 项 | 结果 |
|---|---|
| 训练 harness / 配置 / 测试 | **已交付**（`scripts/train_intent_lora.py`、`configs/train/*.yaml`、`src/bank_assistant/training/`） |
| dry-run（配置/数据/哈希/分词/微型 CPU 前反向） | **已执行并通过**，状态 `NOT_RUN`（dry-run 不训练） |
| 真实 QLoRA 训练 | **`NOT_RUN`** |
| 冻结 test 上 baseline vs tuned 对比 | **不可用**（训练未执行）；仅给出多数类参考下限 |
| 是否改善 | **未知**；不能声称"微调有效"或"微调无效" |
| 生产是否使用微调模型 | **否**；生产发布不以微调为前提（计划 M6-07） |

---

## 2. 资源事实（2026-11-12 18:48 +08:00，SSH 只读采集）

`ssh my-ubuntu-server` 实际输出：

```text
2026-11-12T18:48:25+08:00
=== nvidia-smi ===
NVIDIA-SMI 580.178.04     Driver Version: 580.178.04     CUDA Version: 13.0
GPU 0: NVIDIA GeForce RTX 3060 Ti
  0%   33C    P8   14W / 225W   1457MiB / 8192MiB   0%   Default

Processes:
|  0  N/A  N/A   1942   G   /usr/lib/xorg/Xorg                       108MiB |
|  0  N/A  N/A   2325   G   /usr/bin/gnome-shell                      24MiB |
|  0  N/A  N/A  14219   C   ...nvs/dingtalk-embed/bin/python         1280MiB |

=== query ===
NVIDIA GeForce RTX 3060 Ti, 580.178.04, 8192, 1457, 6361, 0
=== compute-apps ===
14219, /home/jack/.venvs/dingtalk-embed/bin/python, 1280
=== df ===
/dev/sda3  327G  164G  147G  53% /
=== free ===
内存 62Gi，已用 5.4Gi，可用 57Gi；交换 8.0Gi，已用 0B
=== hf cache ===
1.4M /home/jack/.cache/huggingface（有 Qwen3-0.6B/1.7B/4B/8B 目录，但无权重）
=== deps ===
{'torch': False, 'transformers': False, 'peft': False, 'bitsandbytes': False}
=== dingtalk procs ===
14219 /home/jack/.venvs/dingtalk-embed/bin/python .../embed_server.py \
  --model-dir /home/jack/opt/dpa-embed/models/bge-m3 --device cuda --dtype float16 ...
```

解读：

- GPU **被无关的 `dingtalk-embed` 服务占用 1280 MiB**（总占用 1457 MiB，空闲
  6361 MiB ≈ 6.21 GiB）；utilization 0% 但不代表可以抢占。
- 这是负责人的另一个项目进程，**本次没有 kill、没有暂停、没有排队**。
  ADR-0004 明确要求训练前确认空闲；与 `dingtalk-*` 共享需负责人明确同意。
  配置里 `hardware.allow_time_sharing: false`，harness 见到其他 compute
  process 会直接写 `NOT_RUN`。
- 服务器系统 Python **没有 torch/transformers/peft/bitsandbytes**；HF 缓存只有
  1.4 MB，候选权重**没有落地**。真实训练还需要安装 CUDA 版依赖并下载权重。
- 磁盘 147 GiB 可用、RAM 57 GiB 可用，硬件本身足够 0.6B/1.7B 4-bit QLoRA；
  不足的是"可以独占的 GPU 窗口 + 已批准/已缓存的模型"。

---

## 3. 为什么是 `NOT_RUN`（具体原因）

1. **GPU 被占用且未获准 time-sharing**：`dingtalk-embed` 占用 1280 MiB；
   任务书要求不得 kill 用户其他训练/推理任务，时间共享需要用户同意。
2. **模型权重未缓存**：Ubuntu HF 缓存 1.4 MB，只有空目录；下载
   Qwen3-0.6B 约 1.5 GB。任务书明确"下载多 GB 权重前先检查磁盘/网络并报告"，
   本次没有在服务器上下载。
3. **训练依赖未安装**：服务器没有 CUDA 版 torch/transformers/peft/bitsandbytes；
   安装体积约 2–3 GB，属于需要负责人确认的环境变更。
4. **本机（Mac）没有 NVIDIA GPU**：只能做 CPU 微型前反向验证机制，
   不能执行 4-bit bitsandbytes QLoRA，也不能产出任何质量结论。

因此，**不是"跑失败了"，而是"按条件任务的规则，不该在未获准的资源上开跑"**。

---

## 4. 实际执行了什么（dry-run 证据）

命令（Mac，2026-11-12，离线/本地缓存）：

```bash
uv sync --frozen --group train
HF_HOME=$PWD/.hf-cache uv run --frozen --group train \
  python -m scripts.train_intent_lora \
  --config configs/train/qlora-intent-qwen3-0.6b.yaml \
  --dry-run --tiny-smoke --run-id dry-run-20261112T1105Z
# exit code 0；status=NOT_RUN
```

dry-run 逐步结果：

| 步骤 | 结果 | 证据 |
|---|---|---|
| 配置校验 | SUCCEEDED | `configs/train/qlora-intent-qwen3-0.6b.yaml`；batch=1、4-bit、梯度检查点、seed=42 |
| 硬件探测 | GPU 不可用 | Mac 无 `nvidia-smi`；Ubuntu 侧证据见 §2 |
| train 数据加载 | SUCCEEDED | 202 条检索查询；sha256 `538b3847bada550a5fff7ea8d858a89afc4384f7f3eba43bf9653ae7cd58588a` |
| 冻结 test 加载 | SUCCEEDED | 50 条；sha256 `869a8bd02b231d071fe1cff3299bd587e6ccd4771675b2485327685463c70096` |
| 真实分词 | SUCCEEDED | Qwen2TokenizerFast；按 **完整 prompt + 标签 + EOS** 统计：train mean 108.7 / p95 115 / max 119；test mean 108.0 / p95 114 / max 116；`max_seq_length=512` 下越界 0 条 |
| 微型 CPU 前反向 | SUCCEEDED | 随机初始化 2 层 Qwen2 + LoRA；`loss=4.889397`，`trainable_params=896`，device=cpu；不下载权重、不用 GPU |
| 真实训练 | SKIPPED | dry-run 明确不启动 |
| 冻结 test 评测 | SKIPPED | 训练未执行，对比不可用 |

dry-run 的一个重要实测结论：**完整训练序列最长 119 token**，配置的
`max_seq_length: 512` 是 4 倍冗余。真实训练时若想省显存/时间，可降到 256
（仍保留一倍余量）；本次保留 512 作为 ADR-0004 的保守默认，并把实测值记录在
`dry-run.json` 中，供真实运行前调整。

产物（gitignore，不提交；可本地复核）：

| 文件 | sha256 |
|---|---|
| `artifacts/training/dry-run-20261112T1105Z/manifest.json` | `e20b982d797710a73a9fc0368ba19f89121bba6a88d597bd84766c38d13341ba` |
| `artifacts/training/dry-run-20261112T1105Z/model-card.md` | `0cf5a77b72d476037b71b5413cd230d6d2927de4f9e42bac8c2fa32381ccfa28` |
| `artifacts/training/dry-run-20261112T1105Z/config.json` | `f90bf0acab13d997108bd7a539288d4d1db680bd85a052692dcf0d894463355f` |
| `artifacts/training/dry-run-20261112T1105Z/dry-run.json` | `34811a930f5c30bff3e3a8aef24aba08b0eb1baa7244322d388356e87c02eda0` |

manifest 字段（节选）：`status=NOT_RUN`、`mode=dry_run`、`code_sha=22ffe5e…`（worktree clean=true）、
`seed=42`、`train_items=202`、`test_items=50`、`wall_time_seconds=11.784`、
`vram_peak_gib=null`（未训练，如实为空）、`evaluation.available=false`。

复现：`make train`（默认就是 dry-run）；`make dataset-build` 先构建冻结数据集。

---

## 5. 冻结 test 评测

**baseline vs tuned 对比不可用**：训练没有执行，没有 adapter，也没有
zero-shot 基线推理。按 T28 要求，这里明确写"不可用"，不拿 dry-run 的
随机微型模型或多数类冒充模型效果。

关于 "#28 同一评测 harness"：T27/#28 的 runner 只支持检索/质检，指标是
Recall@5 与规则级 macro-F1，不能直接套到意图分类。这里复用的是**同一套冻结
闸门与产物口径**：`verify_dataset` 复算数据哈希、`split_guard` 只允许冻结
test、manifest 记录同一组字段并做凭据扫描。意图 accuracy/macro-F1 由
`training/evaluate.py` 纯函数计算。没有把意图指标混进 #28 的检索/质检结果表。

唯一可离线计算的参考值是**多数类下限**（不是基线模型）：

| 指标 | 多数类参考 |
|---|---|
| 预测标签 | `complaint`（train 中最多） |
| accuracy | 0.30（15/50） |
| macro-F1 | 0.1538 |
| n | 50（冻结 test） |

即使未来训练后 accuracy 高于 0.30，也必须同时看 macro-F1 与逐类召回；
test 只有 50 条，**不宣称统计显著**，也不把 test 用于挑 checkpoint
（harness 不保存中间 checkpoint，`save_strategy="no"`）。

---

## 6. 交付物

| 文件 | 作用 |
|---|---|
| `scripts/train_intent_lora.py` | CLI：`--dry-run` / `--execute`；退出码 0/1/2 |
| `configs/train/qlora-intent-qwen3-0.6b.yaml` | 主配置：Qwen3-0.6B，4-bit NF4，r=16，seq=512，batch=1，grad accum=8，3 epochs，seed=42 |
| `configs/train/qlora-intent-qwen3-1.7b.yaml` | 备用候选：Qwen3-1.7B，grad accum=16，2 epochs，lr=1e-4 |
| `src/bank_assistant/training/config.py` | 严格配置校验；训练 split 只允许 train，评测只允许冻结 test |
| `src/bank_assistant/training/dataset.py` | 数据集哈希复算、split 文件哈希校验、标签集合检查 |
| `src/bank_assistant/training/trainer.py` | dry-run / 资源闸门 / QLoRA / 冻结 test 评测编排 |
| `src/bank_assistant/training/hardware.py` | `nvidia-smi` 探针；忙碌判定；只读，不杀进程 |
| `src/bank_assistant/training/manifest.py` | manifest 字段校验 + 由 manifest 生成 model card + 凭据扫描 |
| `src/bank_assistant/training/evaluate.py` | accuracy/macro-F1/逐标签/混淆矩阵/无效输出 |
| `tests/test_train_*.py`（7 个文件） | 67 个离线测试：配置、split 守卫、哈希、manifest、model card、退出码、GPU 不可用路径、指标 |
| `docs/eval/README.md` | 增加"意图分类评测"口径说明 |
| `Makefile` | `make train` 从 NOT_IMPLEMENTED 改为默认 dry-run 的真实入口 |

测试：`uv run pytest tests` → **617 passed, 130 deselected**（含既有 550 个），
不联网、不下载、不需要 GPU。

---

## 7. 要真正执行，还差什么

按最小可行顺序：

1. **GPU 窗口**：负责人确认可以暂停/错开 `dingtalk-embed`，或明确同意
   time-sharing；若同意，把配置 `hardware.allow_time_sharing` 改为 `true`，
   并确认剩余显存仍 ≥ 2 GiB。**不要**在未获准时 kill 该进程。
2. **环境**：在 Ubuntu 的项目 venv 执行 `uv sync --group train`（CUDA 版
   torch + transformers/peft/bitsandbytes，约 2–3 GB 下载；磁盘 147 GiB 足够）。
3. **模型**：下载 Qwen3-0.6B（约 1.5 GB；tokenizer 仅 16 MB）。若不允许下载，
   先把权重放到 HF 缓存，harness 默认 `local_files_only`，只有显式
   `--allow-download` 才联网。
4. **先 dry-run 再训练**：
   ```bash
   make train                    # 记录 Ubuntu 侧真实 tokenizer/硬件证据
   make train TRAIN_ARGS=--execute
   ```
   预期：0.6B、202 条、seq≤512、batch=1、grad accum=8、3 epochs，约 76 个
   optimizer step；3060 Ti 上约 **5–15 分钟**训练 + 50 条生成评测约 2–5 分钟。
   1.7B 预计 20–40 分钟。无 API 费用，只有电费（约 ¥0.1–0.3 量级）。
5. **决策规则**（预注册，避免看结果再改口径）：只在冻结 test 上比较
   baseline zero-shot 与 tuned 的 accuracy、macro-F1、逐类召回；test 只评一次；
   若没有改善，报告写 `no_improvement`，**保留基线**，不因为"训练过了"就上线。

风险与边界：8GB 上 7B 仍贴近上限；本次只选了 0.6B/1.7B。训练数据是合成数据、
单人按构造标注，不是人工/业务金标；即使指标提升，也只能说明"在这个合成任务
上有改善"，不能外推真实银行客服效果。

---

## 8. 验收标准 #29 对照

| 验收标准 | 结论 | 证据 |
|---|---|---|
| 记录模型/数据哈希/超参/随机种子/显存/耗时 | **PASS** | manifest 含 `model_*`、`dataset_manifest_sha256`、`train_split_sha256`、`test_split_sha256`、`label_schema_sha256`、`hyperparameters`、`seed`、`wall_time_seconds`、`vram_peak_gib`（NOT_RUN 时为 null，明确表示未测量）；model card 同步展示 |
| 测试集不参与训练或挑 checkpoint | **PASS** | `config.py` 加载时 `require_split_for("train", split)`；`dataset.py` 训练只接受 train、评测只接受 `require_frozen_test_split("test")`；`save_strategy="no"`，只训练一次；单测覆盖 dev/test 被拒绝 |
| 结论说明有无改善 | **PASS** | 本报告明确：训练未执行 → baseline vs tuned 不可用，不能声称改善；给出多数类参考下限并说明其局限；给出重新执行所需条件 |

补充：任务书的 dry-run 要求（配置校验、数据加载、tokenization、一个微型
forward/backward）全部实际执行；`--execute` 在 GPU 忙碌/缺失时写 `NOT_RUN`
并返回 1，不会静默开跑。

---

## 9. 已知限制

- 意图标签用 `business_type`（lost_card / credit_card_fee / complaint）：
  数据集细粒度 `intent_cluster` 在 train/dev/test 之间**不重叠**，不能作为
  跨 split 的监督目标；三分类是唯一无泄漏的监督信号。
- train 202 / test 50，样本量小；即使真实训练，统计区间也很宽。
- dry-run 的 tokenizer 统计是完整训练序列（prompt+标签+EOS），但只在 Mac
  本地缓存上做；Ubuntu 真实运行时应用同一代码再测一次。
- 本报告不包含任何真实客户数据；数据集 `contains_real_customer_data=false`。
- 未执行、也未声称执行：Ubuntu 上的真实训练、GPU 上的 4-bit 量化加载、
  微调后模型的业务效果。
