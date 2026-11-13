# M7 性能与稳定性实测报告（T38 / #38）

日期：2026-11-13（Asia/Shanghai）｜状态：**READY_FOR_ACCEPTANCE**
（10 并发压测与受控故障演练已实测达标；24 小时探测已启动并观察 25 分钟，
未满 24 小时，如实标注）
代码/镜像：`3a04952`／`bank-assistant:api-3a04952`（发布 `v0.1.1`）
相关需求：FR-10；计划书 §7、M7-04；风险登记册 R-04/R-05/R-13

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
| 质检任务接收/完成 | ≤1s / ≤60s（5 并发） | 未压测 | **NOT_RUN** |
| 可恢复性 | RPO≤24h、RTO≤2h | API 重启 2.166s、DB 重启 2.127s 恢复到 ready；RPO/独立故障域未测 | **RTO 部分 PASS；RPO NOT_RUN** |
| 长时间运行 | 24h 定时探测，无未解释崩溃 | 已运行 25 分钟，无失败 | **IN_PROGRESS（未满 24h）** |
| 真实供应商限流/超时 | 有真实故障演练 | 未做（不人为打断付费供应商） | **NOT_RUN（R-04 剩余部分）** |

## 9. 未测量与边界

1. **24h 未满**：本报告只覆盖 25 分钟；探测继续运行，满 24h 后应
   用同一 `summary.json` 更新结论。
2. **RPO 未计时**：只测了容器重启的 RTO；备份恢复的 RPO、独立故障域（R-13）未变。
3. **供应商故障未演练**：真实 429/超时/断网仍未在服务器人为制造（R-04 剩余部分）。
4. **单实例**：限流是进程内令牌桶（R-10），本压测只有一个 uvicorn worker；
   多实例/网关限流未验证。
5. **同机回环**：客户端与 API 同机，没有跨机网络 RTT；不能外推为 LAN/WAN 容量。
6. **回放非质量证据**：replay 只证明链路时延与稳定性，不代表 live 模型质量或成本。
7. **压测写入了演示库**：非模型阶段只读（登录审计与 health/metrics 计数除外）；
   回放阶段写入克隆库并在测后删除，未污染 `bank_assistant_demo`。

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

- R-04：真实数据库停机部分已关闭（本报告 §7.2）；真实供应商 429/超时仍未演练。
- R-05：10 并发/10 分钟压测部分已关闭并达标（§3）；24h 长跑仍在进行（§6）。
- R-13：容器重启 RTO 已实测（API 2.166s、DB 2.127s）；RPO/独立故障域仍未测。
- 详细的关闭证据与剩余项见 [风险登记册](../ops/risk-register.md)。
