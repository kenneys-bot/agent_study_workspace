# M7 性能与稳定性实测报告（T38 / #38；T39 / #39 补测）

日期：2026-11-13（Asia/Shanghai）｜状态：**READY_FOR_ACCEPTANCE**
（10 并发压测、受控故障演练、质检 P95 与供应商故障映射已实测；
24 小时探测仍未满 24 小时，如实标注）
代码/镜像：`3a04952`／`bank-assistant:api-3a04952`（发布 `v0.1.1`）
相关需求：FR-10；计划书 §7、M7-04；风险登记册 R-04/R-05/R-13/R-19

> 本报告把 M7 报告中 **NOT_RUN** 的容量、长跑与真实故障项换成可复算的数字。
> 测量对象是**个人展示项目的单机部署演练**，不是任何机构的容量承诺或 SLA；
> 全部业务数据为合成演示数据。

## 1. 环境与部署身份

| 项 | 值 |
|---|---|
| 服务器 | `my-ubuntu-server`（`jack-MS-7C94`，Ubuntu 24.04.3，Ryzen 7 5800X3D 8C/16T，62.7 GiB RAM） |
| API | 容器 `bank-assistant-api`，镜像 `bank-assistant:api-3a04952`，`network_mode=host`，仅监听 `127.0.0.1:8000` |
| 数据库 | 容器 `bank-assistant-db-db-1`（PostgreSQL 18，仅回环 5432） |
| 代码 SHA / 分支 | `3a04952ceb228fe2838ea2203508f4f25db63f78`（`ticket/T37` 发布提交，服务器实际运行） |
| 生成模型模式 | `live`（`MODEL_MODE=live`）；候选答复回放阶段另起 `replay` 实例，见 §4 |
| 活动索引 | `idx-20261113T025706Z-f7974a6a`（`title_chunk`；校准 sha256 `136e030a…`） |
| 压测时间（UTC） | 非模型：2026-11-13T03:12:41Z–03:22:41Z；回放：03:25:56Z–03:27:56Z |
| 压测客户端 | 同一镜像内 Python 3.13 + httpx 0.28.1，`docker run --network host`，从服务器回环发起 |
| 负载隔离 | 仅本机回环；不暴露 LAN/公网；未调用真实云模型（非模型阶段），回放阶段不发真实模型请求 |

## 2. 方法

### 2.1 请求构成与并发

- 10 个 worker，各自**顺序**发请求；同一时刻最多 10 个在途请求（`--concurrency 10`）。
- 4 个非模型端点按 worker 轮转等权混合，每个端点约占 1/4：

| 端点 | 方法 | 认证 | 说明 |
|---|---|---|---|
| `/health/live` | GET | 否 | 进程存活 |
| `/health/ready` | GET | 否 | 真实 `SELECT 1` 依赖探测 |
| `/api/v1/system/version` | GET | 是 | 版本/索引/模型身份 |
| `/api/v1/inspections/{inspection_id}` | GET | 是 | 已存在的演示质检详情（UUID `c0d9ef04-…`） |

> 说明：契约 §5 只定义 `GET /inspections/{id}`，**没有** `GET /inspections` 列表端点。
> 因此按真实 API 使用详情端点，而不是请求一个会 405 的路径；请求构成仍覆盖
> “health + 版本 + 已认证业务只读”三类。

### 2.2 度量口径

- 延迟：客户端 `perf_counter` 端到端耗时，**只统计成功响应**（2xx 且状态码符合预期）；
  失败与传输错误单独计数，不把快速 5xx 混进 P95。
- 分位：线性插值（与 numpy 默认 `linear` 一致），单位毫秒。
- 错误率：`(总请求 - 成功) / 总请求`；连接失败算错误。
- 吞吐：`成功请求数 / 实际墙钟时长`。
- 认证：压测前用公开演示账号 `demo-author` 登录一次；令牌 401 时刷新后重试一次。
- 原始逐请求记录不落盘（百万级），落盘 10 秒桶 + 汇总 JSON；报告数字均由
  `summary.json` 复算。

## 3. 非模型 API：10 并发 × 10 分钟

实际执行 600.017 秒，共 **344,734** 个请求，**0 错误**，成功吞吐 **574.54 req/s**。

| 端点 | 请求数 | 吞吐 req/s | P50 (ms) | P95 (ms) | P99 (ms) | 最大 (ms) | 错误率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `GET /health/live` | 86,181 | 143.63 | 2.192 | 3.848 | 4.683 | 143.996 | 0.00% |
| `GET /health/ready` | 86,184 | 143.64 | 14.135 | 21.446 | 25.876 | 133.788 | 0.00% |
| `GET /api/v1/system/version` | 86,185 | 143.64 | 20.935 | 30.265 | 35.809 | 142.736 | 0.00% |
| `GET /api/v1/inspections/{id}` | 86,184 | 143.64 | 30.520 | 43.224 | 49.768 | 141.313 | 0.00% |
| **合计/整体** | **344,734** | **574.54** | — | — | — | — | **0.00%** |

**计划 §7 判定（非模型 API）**：门槛为 **P95 ≤ 500ms、错误率 < 1%**（10 并发、
10 分钟，记录环境与请求构成）。四个端点最大 P95 为 **43.224ms**，整体错误率
**0.00%** → **PASS**。

测量条件必须一起引用：单 uvicorn worker、host 网络、压测客户端与 API 同机回环、
无跨机网络、合成演示数据、无模型调用。**不能**据此推断公网/LAN 访问、多实例、
或含模型链路的延迟。

## 4. 候选答复（replay）2 分钟补充

计划 §7 对含模型链路另设门槛（候选答复 P95 ≤ 15s，5 并发）。为避免烧钱与
供应商波动，按 issue 要求用 `MODEL_MODE=replay` 的**独立临时实例**测量：

- 临时实例：同镜像、`127.0.0.1:8011`、`MODEL_MODE=replay`、`RATE_LIMIT_ENABLED=false`；
  数据库为 `bank_assistant_loadtest`（从 `bank_assistant_demo` 克隆，测后删除），
  不修改线上 `bank-assistant-api` 的配置或环境文件；
- 5 并发、120.25 秒、1514 个 `POST /api/v1/suggestions`，全部 201。

| 端点 | 请求数 | 吞吐 req/s | P50 (ms) | P95 (ms) | P99 (ms) | 最大 (ms) | 错误率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `POST /api/v1/suggestions`（replay） | 1,514 | 12.59 | 396.264 | 464.291 | 502.127 | 614.327 | 0.00% |

**判定**：P95 464ms ≤ 15s → **PASS（仅工程延迟）**。回放由确定性 responder 产生，
**不是模型质量证据**；每次请求仍走真实检索、引用校验与持久化链路。延迟中包含
当前实现每次请求构造 fastembed provider 的开销，不能外推为 live 模型端到端时延。

## 5. 资源与指标前后对比（无持续泄漏迹象）

| 指标 | 压测前（03:12Z） | 压测后（03:22Z） | 观察 |
|---|---|---|---|
| API 容器内存 | 88.47 MiB | 96.14 MiB | +7.67 MiB，结束后空闲 CPU 0.09% |
| DB 容器内存 | 80.13 MiB | 110.80 MiB | +30.67 MiB（连接/缓存），空闲 CPU 2.07% |
| API CPU（运行中快照 03:17Z） | — | 135.66% | 单机多线程回环压测峰值 |
| DB CPU（运行中快照 03:17Z） | — | 51.57% | 同上 |
| `bank_http_requests_total` | 基线 | +344,906 | 344,734 为压测请求，其余为登录/健康/指标抓取 |
| `bank_db_ready` | 1 | 1 | 依赖始终可用 |
| `bank_db_probe_errors_total` | 0 | 0 | 无探测失败 |
| 非模型端点 5xx | 0 | 0 | 压测期间无 5xx |

结论：10 分钟满负载后进程仍健康，内存增长在连接池/缓存量级，未见随时间线性增长
或重启；但**这不是 24 小时内存泄漏证明**，长跑结论只能引用 §6 的探测窗口。

## 6. 24 小时稳定性探测（已启动，未满 24h）

- 脚本：`scripts/probe_uptime.py`（仅标准库；服务器宿主 `python3` 运行，不进入 API 容器）
- 启动（UTC `2026-11-13T03:28:12Z`，Asia/Shanghai 11:28:12）：

  ```bash
  ssh my-ubuntu-server 'cd ~/workspace/bank-assistant/artifacts/probe && \
    nohup python3 probe_uptime.py --out-dir . \
      --base-url http://127.0.0.1:8000 > probe.log 2>&1 </dev/null &'
  ```

- 停止：

  ```bash
  ssh my-ubuntu-server 'kill "$(cat ~/workspace/bank-assistant/artifacts/probe/probe.pid)"'
  ```

- 频率：每 5 分钟 `/health/live` + `/health/ready`；每 30 分钟 `/metrics` 原文快照 +
  `docker inspect` API/DB 状态，并用只读方式记录 `redis-redis-1` 作为“未触碰”参照。
- 原始文件（服务器）：
  - `/home/jack/workspace/bank-assistant/artifacts/probe/probe.jsonl`
  - `/home/jack/workspace/bank-assistant/artifacts/probe/summary.json`
  - `/home/jack/workspace/bank-assistant/artifacts/probe/metrics/metrics-*.txt`
  - `/home/jack/workspace/bank-assistant/artifacts/probe/probe.log`
- 脚本 sha256：`79984b0e4939aa6715f1b94d1c91bc71c7423c2f979e1ea94b04535bb4ffb293`

**已观察窗口**：截至 `2026-11-13T03:53:13Z`，已运行 **25 分钟**，
`live` 与 `ready` 各 6 次检查、0 次失败，
指标快照 1 次。窗口内按 §7 做了两次**受控**容器重启
（API `03:29:27Z`、DB `03:29:52Z`）：探测的 5 分钟采样没有落在数秒级瞬断上，
因此 health 记录未出现 503；瞬断由 §7 的 0.2s 轮询单独记录。redis 起始时间
`02:33:03Z` 全程未变，两次演练后未再重启，其余时间无未解释崩溃。

| 探测目标 | 检查次数 | 失败 | 可用率 | 平均延迟 | 最大延迟 |
|---|---:|---:|---:|---:|---:|
| `/health/live` | 6 | 0 | 100% | 3.535ms | 15.025ms |
| `/health/ready` | 6 | 0 | 100% | 2.535ms | 2.595ms |

首个 metrics 快照（`03:28:12Z`，12,952 B）：`bank_db_ready=1`、任务队列
`running=0/queued=0/succeeded=3`；API/DB/redis 均 `running+healthy`，重启计数 0
（redis 只作参照，未被触碰）。`summary.json` 的 `last_error=null`；探测进程与 pidfile 均正常。

状态：**IN_PROGRESS**——24 小时未满，计划 §7“24 小时定时探测”门槛**不能**标记 PASS；
本报告只声明已观察的 25 分钟。探测进程会继续运行到 24 小时或人工停止；
停止后用同一 `summary.json` 复算完整窗口。

## 7. 受控故障演练

硬限制全程遵守：**未重启服务器整机、未触碰 `redis-redis-1` / `dingtalk-*`、未降 schema**。
重启前后均用 `docker inspect` 记录起始时间与重启计数；参照容器 redis 起始时间不变。

### 7.1 只重启 API 容器（`bank-assistant-api`）

| 项 | 值 |
|---|---|
| 演练时刻（UTC） | 2026-11-13T03:29:27Z 起 |
| 命令 | `ssh my-ubuntu-server 'docker restart bank-assistant-api'` |
| `docker restart` 命令耗时 | 1.167 s |
| readiness 观察 | `/health/ready` 轮询 0.2s：`000`（连接拒绝）→ `200`；**未出现 503** |
| 恢复耗时 | 首次失败到首个 200：**1.863 s**；从命令发起到 200：**2.166 s** |
| 恢复后状态 | live/ready 200、容器 `healthy`、`StartedAt=03:29:27.585Z` |
| 日志 | 重启前后 4 分钟窗口内 `trace_id` 行 114、`ERROR` 0、`Traceback` 0 |
| 其它容器 | DB/redis 未重启（起始时间不变） |

说明：API 进程重启时端口不可达，因此观察到的是**连接拒绝 `000` → 200**，不是 503；
503 只在依赖（数据库）不可用时出现，见 §7.2。这是真实语义，不能为了对齐门槛
把 `000` 改写成 `503`。

### 7.2 只重启数据库容器（`bank-assistant-db-db-1`）

| 项 | 值 |
|---|---|
| 演练时刻（UTC） | 2026-11-13T03:29:52Z 起 |
| 命令 | `ssh my-ubuntu-server 'docker restart bank-assistant-db-db-1'` |
| `docker restart` 命令耗时 | 1.634 s |
| readiness 观察 | 0.2s 轮询：**`503` → `200`**（状态集合 `{200, 503}`） |
| 恢复耗时 | 首个 503 到首个 200：**1.820 s**；从命令发起到 200：**2.127 s** |
| 恢复后状态 | ready 200、`bank_db_ready 1`、DB 容器 `healthy`、`StartedAt=03:29:52.293Z` |
| API 容器 | `StartedAt` 未变（03:29:27Z），未被重启 |
| 503 计数 | `/metrics` 中 `route=/health/ready,status=503` 共 10 次（演练轮询），其余路由无 5xx |

### 7.3 任务不变量（重启后真实数据库查询）

演练时数据库中**没有进行中的 worker 任务**（3 个历史质检任务均 `succeeded`），
因此“进行中任务不丢”只能验证“未被破坏”，不能证明长任务在断电级故障下的恢复。
重启后查询结果：

| 检查 | 结果 |
|---|---|
| `inspection_runs` 状态 `running` | 0 |
| `tasks` 状态 `running` | 0 |
| `tasks` 状态 `queued` | 0 |
| `tasks` 状态 `succeeded` | 3（与演练前一致） |
| 同一 `task_id` 的重复质检运行 | 0 |
| 同一 inspection 的重复 findings | 0 |
| 同一会话的重复 succeeded 运行 | 0 |

命令与结果见 §10；租约恢复/幂等语义仍由既有 CT-07/CT-11 集成测试覆盖，本演练
不伪造“长任务恢复已实测”。

## 8. 计划 §7 判定汇总（不粉饰）

| 门槛 | 要求 | 本轮实测 | 结论 |
|---|---|---|---|
| 普通 API | P95 ≤500ms、错误率 <1%（10 并发 10 分钟） | P95 最大 43.224ms、错误率 0.00% | **PASS（条件见 §3）** |
| 候选答复 | P95 ≤15s（5 并发） | replay P95 464.291ms | **PASS（仅 replay 工程延迟）** |
| 质检任务接收/完成 | ≤1s / ≤60s（5 并发） | POST P95 **245.244ms**（n=30）；完成 P95 **41.879s**（n=60，其中成功 10 条 P95 32.146s）；成功率仅 10/60（见 §12） | **PASS（延迟）；成功率另列 R-19** |
| 可恢复性 | RPO≤24h、RTO≤2h | API 重启 2.166s、DB 重启 2.127s 恢复到 ready；RPO/独立故障域未测 | **RTO 部分 PASS；RPO NOT_RUN** |
| 长时间运行 | 24h 定时探测，无未解释崩溃 | T38 报告时已运行 25 分钟、0 失败；T39 补测时仍在运行 | **IN_PROGRESS（未满 24h）** |
| 真实供应商限流/超时 | 有真实故障演练 | 受控假供应商 4 类故障映射全部正确：429/504/503/502（见 §13） | **PASS（映射）；未人为制造真实 DeepSeek 429** |

## 9. 未测量与边界

1. **24h 未满**：T38 部分覆盖截至 2026-11-13T03:53Z 的 25 分钟；T39 补测时
   探测仍在运行，仍未满 24 小时；满窗后应用同一 `summary.json` 更新结论。
2. **RPO 未计时**：只测了容器重启的 RTO；备份恢复的 RPO、独立故障域（R-13）未变。
3. **供应商故障为受控演练**：§13 用本地假供应商验证 429/504/503/502 映射；
   未在真实 DeepSeek 上人为制造 429/超时，也未在服务器断网。
4. **单实例**：限流是进程内令牌桶（R-10），本压测只有一个 uvicorn worker；
   多实例/网关限流未验证。
5. **同机回环**：客户端与 API 同机，没有跨机网络 RTT；不能外推为 LAN/WAN 容量。
6. **回放非质量证据**：replay 只证明链路时延与稳定性，不代表 live 模型质量或成本。
7. **压测写入了演示库**：非模型阶段只读（登录审计与 health/metrics 计数除外）；
   回放阶段写入克隆库并在测后删除，未污染 `bank_assistant_demo`。
8. **T39 质检样本是混合会话**：60 次中风险会话占一半；成功率 16.7% 是当前 live
   模型的实测结果，不是只挑成功样本；P95 延迟只说明“完成/失败都在 60s 内”。

## 10. 证据文件与复跑命令

服务器目录 `/home/jack/workspace/bank-assistant/artifacts/loadtest/T38/`：

| 路径 | 内容 |
|---|---|
| `nonmodel/summary.json` | 非模型压测汇总（本报告 §3 的唯一数字来源） |
| `nonmodel/buckets.jsonl` | 10 秒桶（逐端点请求/错误/延迟和） |
| `nonmodel/metrics-before.txt` / `metrics-after.txt` | 压测前后 `/metrics` 原文 |
| `nonmodel/stats-before.txt` / `stats-after.txt` | `docker stats` 快照 |
| `replay/summary.json` | replay 候选答复阶段汇总 |
| `drill-api.log` / `drill-api.txt` / `drill-api.restart-*` | API 重启 readiness 轮询与状态 |
| `drill-db.log` / `drill-db.txt` / `drill-db.restart-*` | DB 重启 readiness 轮询与状态 |
| 探测目录 | 见 §6 |

复跑（服务器；脚本与服务器镜像同 SHA 时）：

```bash
# 非模型 10 并发 10 分钟
ssh my-ubuntu-server 'cd ~/workspace/bank-assistant/artifacts/loadtest/T38 && \
  docker run --rm --network host --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  -e LOADTEST_PASSWORD=demo-only-correct-horse-battery \
  --entrypoint python bank-assistant:api-3a04952 /work/load_test.py \
  --base-url http://127.0.0.1:8000 --duration-seconds 600 --concurrency 10 \
  --inspection-id c0d9ef04-75bd-416f-800d-5f0e3cf42838 --output-dir /work/nonmodel'
```

受控故障演练与探测命令见 [运维手册 §13](../ops/runbook.md)。

## 11. 与风险清单的联动

- R-04：DB 停机（§7.2）与供应商 4 类故障映射（§13）均已实测 → **CLOSED（受控演练）**；
  未在真实 DeepSeek 上人为制造 429/超时，边界保留。
- R-05：10 并发/10 分钟压测部分已关闭并达标（§3）；24h 长跑仍未满（§6）。
- R-13：容器重启 RTO 已实测（API 2.166s、DB 2.127s）；RPO/独立故障域仍未测。
- **R-19（新）**：live 质检 60 次中 50 次 `EVIDENCE_INVALID`（成功率 16.7%）；
  §7 延迟 PASS 不等于质检功能健康。
- 详细的关闭证据与剩余项见 [风险登记册](../ops/risk-register.md)。

## 12. 质检任务接收 / 完成 P95（T39 / #39，真实模型）

### 12.1 测量条件

| 项 | 值 |
|---|---|
| 时间（UTC） | batch1 2026-11-13T04:01:38Z–04:02:26Z；batch2 04:02:40Z–04:03:27Z |
| 被测对象 | 本机临时 API `127.0.0.1:18080`（单 uvicorn 进程），**未触碰**线上 `bank-assistant-api` |
| 数据库 | `bank_assistant_test`（经 `127.0.0.1:15432` 隧道）；空库 `alembic upgrade head` → `0011_report_exports`，导入 32 条演示知识、6 条规则、6 段会话 |
| 索引 | `.local-data/index-t39`，`idx-20261113T035753Z-4e8139e2`（`title_chunk`，30 版本/30 分块，校准 sha256 `cffa0671…`） |
| 模型 | `MODEL_MODE=live`，`deepseek-chat`（DeepSeek 官方），`LLM_TIMEOUT_SECONDS=60`、`LLM_MAX_RETRIES=2` |
| worker | 5 个独立进程，同一测试库，租约 60s；每条质检 2 条适用规则 → 最多 2 次模型调用/尝试 |
| 负载 | 5 并发提交；2 批各 30 次，共 **60 次**真实质检；batch1 与 batch2 环境相同 |
| 输入 | 6 段合成演示会话轮转（S1/S2/S3 × normal/risk），每条质检 2 条规则 |
| 接收延迟口径 | 客户端 `perf_counter` 测得 `POST /api/v1/inspections` 的 202 响应时间（只算 HTTP 202） |
| 完成时间口径 | 服务端 `finished_at - created_at`（秒）；终态含 `succeeded` / `failed`，未终态不计入 |
| 分位口径 | 线性插值（与 numpy 默认 `linear` 一致）；空样本记 `n/a`，不填 0 |
| token / 成本 | worker 日志 `model_call` 的 `prompt_tokens`/`completion_tokens`；成本按仓库 `configs/eval/live-subset.yaml` 的 **2025-02-09 官方价目快照**（¥2/百万输入、¥8/百万输出）估算，**不是账单** |

> batch1 的客户端 POST 计时因脚本轮询缺陷丢失（不影响服务端时间戳、终态与 token），
> 因此 **POST 分位只用 batch2（n=30）**；完成时间、失败率与 token 用两批合并 **n=60**。

### 12.2 延迟与结果

| 指标 | n | P50 | P95 | P99 | 最大 |
|---|---:|---:|---:|---:|---:|
| `POST /inspections` 接收（ms） | 30 | 179.125 | **245.244** | 253.667 | 256.993 |
| 完成时间，全部终态（s） | 60 | 23.9996 | **41.8787** | 46.3018 | 46.3618 |
| 完成时间，仅 `succeeded`（s） | 10 | 18.3547 | **32.1456** | 33.0140 | 33.2311 |
| 完成时间，仅 `failed`（s） | 50 | 25.5991 | 42.1171 | 46.3119 | 46.3618 |

| 项 | 值 |
|---|---|
| 成功 / 失败 / 未终态 | **10 / 50 / 0** |
| 失败率 | **83.3%**，全部为 `EVIDENCE_INVALID` |
| 失败任务的 findings | **0**（失败绝不写“零分报告”） |
| 成功任务的 findings | 每条 2 条，共 20 条 |
| 模型调用 | 235 次，**全部** `status=succeeded`（不是限流/超时/空内容） |
| token | 输入 **256,621** / 输出 **37,309** |
| 估算成本 | **¥0.8117**（两批合计；预算 ¥5） |

失败原因不是供应商故障，而是真实模型的结构化输出精度：证据区间 `end` 少算 1 个码点、
`end` 超出原文（如 `end=39` vs 32）、或 `quote` 补了原文没有的句号。典型诊断：

- `DEMO-FEE-01`：原文 `…肯定没问题`，声明 `…肯定没问题。` → 区间不匹配；
- `DEMO-CLAIM-01`：`end=39`，原文共 32 个码点；
- `DEMO-ATT-01`：`quote` 多一个句号；
- `DEMO-FEE-02`：`quote` 是整句，声明的区间只覆盖前半句。

这是**证据校验按契约拒绝无效证据**的正确行为（`EVIDENCE_INVALID`），不是把失败静默成
`clear`/零分；但也说明当前 live 质检的可用成功率只有 **16.7%**，必须作为独立风险跟踪。

### 12.3 计划 §7 判定

| 门槛 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 接收响应 | ≤1s | P95 **245.244ms**（n=30） | **PASS** |
| 完成时间 | P95 ≤60s（5 并发） | 全部终态 P95 **41.879s**；成功任务 P95 **32.146s** | **PASS（延迟）** |
| 失败率 / 假成功 | 不能伪造结果 | 50 条 `EVIDENCE_INVALID`，findings=0；无 2xx 假成功 | **行为正确，但成功率 16.7% → R-19** |

**边界**：本结果只说明“任务在 5 并发、2 条规则/任务的条件下能在 60s 内完成”，
**不能**外推为质检质量或高成功率；83.3% 的失败率意味着当前 live 质检不足以支撑
“默认可用的质检演示”，对外只能展示已成功的 10 条或明确标注失败率。

### 12.4 测试库状态（可审计）

- `bank_assistant_test` 保持 `0011_report_exports`，含本轮 60 条实测运行 + 演练行；
  **所有 failed 运行的 findings 均为 0**。
- `bank_assistant_dev` / `bank_assistant_demo` 在同时段内 **T39 任务数 0**，未被污染。
- 需要清空时（测试库可丢弃）：
  `DATABASE_URL=…/bank_assistant_test uv run python scripts/db_tool.py reset-test`。
- 测试：`uv run pytest tests -q` → **699 passed, 138 deselected**（新增 18 个离线
  纯逻辑测试，覆盖分位/成本/token 解析/契约映射/“不得假成功”断言）。

## 13. 真实供应商故障演练（T39 / #39，受控）

### 13.1 条件

- 假供应商：`scripts/fake_supplier.py`，`127.0.0.1:19999`，OpenAI 兼容
  `POST /v1/chat/completions`，模式由 `GET /__mode?mode=…` 切换；
- 临时 API：`127.0.0.1:18081`，`DEEPSEEK_BASE_URL` 指向假供应商，
  `LLM_TIMEOUT_SECONDS=2`、`LLM_MAX_RETRIES=0`；与线上容器完全隔离；
- worker：`python -m bank_assistant.worker --once`，同样指向假供应商；
- 数据库：`bank_assistant_test`；连接被拒通过**停止假供应商**制造，不伪造异常；
- 演练脚本：`scripts/supplier_failure_drill.py`；正式结果为 `run_tag=drill2`。

### 13.2 四类映射（请求 → 响应 → DB）

| 上游故障 | API 状态 / 稳定码 / retryable | trace_id | DB 建议 | DB 质检 | findings |
|---|---|---|---|---|---|
| HTTP 429（RateLimitError） | **429 / `MODEL_RATE_LIMITED` / true** | `t39-drill-drill2-rate_limit` | `failed`，answer `""`，citations 0，`model_status=rate_limited` | `failed / MODEL_RATE_LIMITED` | **0** |
| 读超时（APITimeoutError，2s） | **504 / `MODEL_TIMEOUT` / true** | `t39-drill-drill2-timeout` | `failed`，answer `""`，citations 0，`model_status=timeout` | `failed / MODEL_TIMEOUT` | **0** |
| 连接被拒（APIConnectionError） | **503 / `MODEL_UNAVAILABLE` / true** | `t39-drill-drill2-connection_refused` | `failed`，answer `""`，citations 0，`model_status=provider_unavailable` | `failed / MODEL_UNAVAILABLE` | **0** |
| HTTP 200 + 非法 JSON | **502 / `MODEL_INVALID_STRUCTURE` / true** | `t39-drill-drill2-invalid_json` | `failed`，answer `""`，citations 0，`model_status=invalid_structure` | `failed / INVALID_MODEL_OUTPUT` | **0** |

细节：

- 假供应商统计：`rate_limit` / `timeout` / `invalid_json` 各 5 次请求
  （每类 = 2 次候选答复模型调用 + 3 次质检 worker 尝试），状态分别为 429/200/200；
- `connection_refused` 停止假供应商后请求在传输层失败，未经过假供应商计数；
- 4 条建议均 `status=failed`、`answer=''`、`citations=[]`；4 条质检均 `failed`、
  `findings=0`；**没有任何 2xx 假成功、没有零分报告**；
- 脚本退出码 0，`all_passed=true`；演练后临时 API（18081）、假供应商（19999）已停止，
  端口均已释放；线上 `bank-assistant-api` 与 demo/dev 库未被触碰。
- 说明：首次演练脚本的 worker 未指向假供应商（打到真实 DeepSeek），该次结果作废；
  测试库中保留其 failed 行，正式证据以上表 `drill2` 的 trace_id 为准。

### 13.3 复跑

```bash
. .local-data/t39-artifacts/run/env.sh   # 仅本地临时环境；DATABASE_URL 必须指向 *_test
.venv/bin/python scripts/supplier_failure_drill.py --port 18081 --fake-port 19999 \
  --output .local-data/t39-artifacts/supplier-failure-drill.json
```
