# 执行进度

当前里程碑：M0–M7 全部 **ACCEPTED**（负责人 2026-11-13 明确签收）；继续推进 M7 后续风险项与真实微调实验。M6 已交付（T28 harness、T35 真实 QLoRA 已执行；T36 已把 test 核心诉求 Recall@5 提到 0.944）
当前任务：T35 真实微调已完成（2026-11-13）；T36、T37 已完成（T37 已发布 v0.1.1，检索改进落到生产链路）；T39 已完成质检 P95 实测与供应商故障演练
当前状态：M1–M5 已交付（等待负责人验收）；M6 已交付（T26/T27 完成、T28 harness 交付、**T35 真实训练已执行：tuned 0.76 vs baseline 0.64 / macro-F1 0.7441 vs 0.6138，n=50**；T36 检索改进 test R@5 0.944；引用支持度 NOT_MEASURED）；**M7-01～M7-06 已交付**（不可变镜像部署、备份恢复/回滚/索引重建、指标与脱敏日志、限流/输入上限/安全测试、发布清单与 `make release-verify`；2026-11-12）。T37 已把 title_chunk + 确定性重排落到生产并重建线上索引（v0.1.1）。计划 §7 的 10 并发压测已由 T38 实测达标（P95 最大 43.224ms、0 错误），DB 停机/RTO 已实测；T39 实测质检接收 P95 **245.244ms**、完成 P95 **41.879s**（n=60），但成功率仅 **10/60**（50 次 `EVIDENCE_INVALID`），并完成 429/504/503/502 四类供应商故障映射演练。Recall@5/引用支持度/24h 满窗/质检成功率仍未全部达成，见 [M7 报告](milestones/M7-report.md)、[M7 性能报告](milestones/M7-performance-report.md) 与 [风险清单](ops/risk-register.md)
本机代码分支：`feat/bank-assistant`（集成分支；T31 在 `ticket/T31` 打包，T35 在 `ticket/T35` 执行真实微调，T37 在 `ticket/T37` 落地检索改进）
发布代码SHA：`3a04952`（`v0.1.1` 镜像身份；后续文档提交不改变镜像）
Ubuntu代码SHA：`3a04952`（服务镜像与服务器 checkout 一致；镜像 `bank-assistant:api-3a04952`；T35 微调执行 SHA `1746d16` 仅用于训练产物，不是服务镜像）
最近更新：2026-11-13（Asia/Shanghai）

## 2026-11-13 质检延迟与供应商故障演练（T39）

真实 DeepSeek、`bank_assistant_test`、60 次：接收 P95 245.2ms（达标）、完成 P50/P95/P99 24.00/41.88/46.30s（P95 ≤60s 达标）、成功任务 P95 32.15s；**成功率仅 16.7%（10/60），50 次 EVIDENCE_INVALID** → 风险 R-19（功能不健康），正在由 #40 修复。故障演练：429/504/503/502 四类映射全部正确、无假成功、无零分报告。

## 2026-11-13 性能与稳定性

`docs/milestones/M7-performance-report.md`：10 并发 × 10 分钟压测（344,734 请求、574.54 rps、0 错误，非模型 API P95 ≤43.2ms，§7 达标）；replay 建议接口 P95 464ms；受控故障演练 API 重启 1.863s / DB 重启 503→200 1.820s 恢复，演练后无重复任务/报告；T39 质检 P95 接收 245.244ms / 完成 41.879s、成功率 10/60，供应商 429/504/503/502 映射全部通过；24h 探测已启动（2026-11-13T03:28Z，PID 114078），计划 2026-11-14 汇总。

## 2026-11-13 继续推进（签收后）

| 工单 | 状态 | 证据 |
|---|---|---|
| #36 T36 complaint 检索召回改进 | DONE | test R@5 0.9444（原 0.833）、complaint 0.8667（原 0.600）；冻结测试集哈希未变 |
| #35 T35 真实 QLoRA 微调 | DONE | 2026-11-13 Ubuntu：status=SUCCEEDED、26 steps、wall 130.4s、vram_peak 2.706 GiB；冻结 test 0.64 → 0.76（macro-F1 0.6138 → 0.7441，n=50）；不 kill `dingtalk-embed`；见 [M6 微调报告](milestones/M6-tuning-report.md) |
| #37 T37 检索改进落地生产 | DONE | `title_chunk` + α=0.2 重排已上生产；镜像 `api-3a04952`，索引 `idx-20261113T025706Z-f7974a6a`（内容身份 `08542afd…`，两次重建一致）；`make release-verify` PASS；会话与固定查询端到端引用打开原文 6/6；镜像/索引回滚演练 PASS。见 [v0.1.1 发布记录](releases/v0.1.1.md) |

## 负责人验收（2026-11-13）

负责人在对话中明确「已签收」，因此 M0–M7 全部报告状态记为 ACCEPTED。执行模型只记录该事实，未代填验收结论。签收后负责人授权继续推进，并说明另一项目已停止、GPU 资源可交由本项目使用。

## 任务状态

| 任务 | 状态 | 验收证据 | 阻碍/下一步 |
|---|---|---|---|
| M0-01 基线 | DONE | 分支/SHA 记录；`pyproject.toml` + `uv.lock` 唯一依赖入口；`docs/environment.md` | — |
| M0-02 配置模板 | DONE | `.env.example`（仅占位）、`.gitignore`、`docs/environment.md` | — |
| M0-03 双机同步 | READY_FOR_ACCEPTANCE | Ubuntu `~/workspace/bank-assistant/repo` 检出同一 SHA；`~/workspace/bank-assistant/{data,artifacts,backups}` 可写；`make sync-check` | 负责人验收 |
| M0-04 状态与ADR | DONE | 本文件、`docs/environment.md`、`docs/adr/0001..0004` | — |
| M0-05 统一入口 | DONE | `Makefile`（doctor/test-unit/build/sync-check 已实现；其余非零退出并标注里程碑）、`scripts/doctor.sh`、`scripts/sync-check.sh` | — |

## M1 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #4 T03 会话导入闭环 | DONE | 解析三缺陷回归；见 `docs/milestones/M1-report.md` |
| #5 T04 检索闭环 | DONE | 扁平查询/count/重排状态/filters |
| #6 T05 模型适配层 | DONE | system 消息、失败分类、无隐式 mock |
| #7 T06 停止伪造 | DONE | 501 NOT_IMPLEMENTED |
| #8 T07 容器与冒烟 | DONE | docker build + make smoke 全通过 |
| #10 T09 PG 与迁移 | DONE | make test-integration 通过 |
| #33 T32 知识/话术修通 | DONE | test_classify_intent 通过 |
| #9 T08 错误码契约 | DONE | 144 passed / 0 failed |
| #11 T10 认证 | DONE | 163 passed；integration 13 passed；alembic 0001→0002 |
| #12 T11 知识生命周期 | DONE | 迁移 0003；集成 53 passed |
| #13 T12 会话持久化与审计 | DONE | 迁移 0004 + merge revision；integration 40 passed |
| #14 T13 幂等与任务表 | DONE | 迁移 0005；幂等/租约集成测试通过 |

## 数据库集中到 Ubuntu（ADR-0006，2026-11-12 部署已执行）

**执行结果**（密码不写入任何文档/提交/日志）：

| 项 | 实际值 |
|---|---|
| 实例 | Ubuntu `my-ubuntu-server`，PostgreSQL 18（`postgres:18-alpine`），容器 `bank-assistant-db-db-1`，状态 healthy |
| 端口 | 宿主仅 `127.0.0.1:5432`（`ss -tln` 仅回环；`docker port` 为 `5432/tcp -> 127.0.0.1:5432`） |
| 数据目录 | `~/workspace/bank-assistant/data/pg`（bind mount，属主 uid 70；不再用匿名卷） |
| 凭据 | 服务器 `~/workspace/bank-assistant/.env.pg`（仓库外，chmod 600）；Mac `.env`（gitignore，600）；未进 Git/日志 |
| 角色/库 | 角色 `bank`；库 `bank_assistant_dev` / `bank_assistant_demo` / `bank_assistant_test` / `bank_assistant_restore_check`，owner 均为 `bank` |
| 部署文件 | 源文件 `deploy/compose.db.yml`；服务器使用仓库外部署副本 `~/workspace/bank-assistant/deploy/compose.db.yml`（服务器工作树保持干净） |

**配置/脚本迁移（已完成）**：

| 项 | 现在 |
|---|---|
| `scripts/pg-tunnel.sh` + `make db-tunnel` / `db-tunnel-stop` / `db-tunnel-status` | 幂等建立/停止 `127.0.0.1:15432 → 服务器 127.0.0.1:5432`，pidfile 在 `.local-data/`，不杀未知进程 |
| `scripts/doctor.sh` | 检查隧道端口 + `SELECT 1` on `bank_assistant_test`；隧道缺失时非零退出并打印 ADR-0006 步骤；不再要求本机 Docker/PG |
| `scripts/test-integration.sh` | 隧道 + 仅重建 `*_test` 的 public schema，`alembic upgrade head`，再跑 `pytest -m integration`；无隧道非零退出 |
| `scripts/seed-demo.sh` + `make seed-demo` | 固定写 `bank_assistant_demo`（可由 `DEMO_DATABASE_URL` 覆盖但必须以 `_demo` 结尾），幂等，拒绝 test/dev/restore |
| `.env.example` / `.env` | `DATABASE_URL=postgresql+psycopg://bank:<password>@127.0.0.1:15432/bank_assistant_dev`；文档说明四个库 |
| `deploy/compose.db.yml` | 仅服务器使用；`PG_DATA_DIR` 必须显式、`POSTGRES_PASSWORD` 必须由 env-file 提供，端口保持回环 |
| `make test-unit` | 仍为无数据库依赖（conftest 显式 sqlite 替身） |
| `make backup` / `restore-check` | M7/T29 已实现：服务器侧 pg_dump dev/demo；恢复只写 `*_restore_check` 并核对行数/哈希/alembic |

**验证证据（2026-11-12，Mac 侧经隧道）**：

- `make test-integration`：`130 passed`，目标 `127.0.0.1:15432/bank_assistant_test`；迁移 head `0011_report_exports`。
- `alembic upgrade head`：`bank_assistant_dev` 与 `bank_assistant_demo` 均到 `0011_report_exports (head)`。
- `make seed-demo` 两次：第一次建 32 知识/32 版本/6 规则/6 会话；第二次 0 新建、全部哈希一致跳过；`bank_assistant_dev` 仍为 0 行，`bank_assistant_test` 计数不变。
- `make doctor`：隧道建立时通过；`make db-tunnel-stop` 后非零退出并打印 ADR-0006 步骤。
- LAN 隔离：服务器 `ss -tln` 仅 `127.0.0.1:5432`；Mac 直连 `192.168.5.10:5432` 返回 `Connection refused`（不能从局域网直连）。
- Mac 无本项目本地 PG：`docker ps` 无 `bank-assistant-db`/`bank-assistant-test` 项目容器，本机 5432 无监听。

**边界**：`make test-e2e` 仍使用一次性本机 compose 数据库（T25 遗留的隔离路径），切到隧道需另立工单；服务器上其它容器与 `dingtalk-*` 未触碰。

## 服务器目录整理（2026-11-12，已执行）

按负责人要求，服务器上本项目的全部内容从 home 根目录收拢到 `~/workspace/`：

| 旧位置 | 新位置（最终） |
|---|---|
| `~/projects/agent_study_workspace` | `~/workspace/bank-assistant/repo` |
| `~/bank-assistant-data` | `~/workspace/bank-assistant/data` |
| `~/bank-assistant-artifacts` | `~/workspace/bank-assistant/artifacts` |
| `~/bank-assistant-backups` | `~/workspace/bank-assistant/backups` |

第一次整理把内容从 home 根目录挪到 `~/workspace/*`（四个平级目录）；按负责人要求又收拢成**单独一个项目目录** `~/workspace/bank-assistant/`，内部再分 `repo/ data/ artifacts/ backups/`。执行后 `~/projects` 已删除，home 根目录无本项目痕迹，`~/workspace/` 顶层只有 `bank-assistant/` 与本项目无关的 `dingtalk-project-assistant/`（未触碰）。移动后仓库仍是同一 SHA、工作树干净、三个数据目录可写。

文档与脚本同步更新：`docs/environment.md`、计划书 §4.2 布局、`scripts/sync-check.sh`（默认远端路径）、`scripts/doctor.sh`（提示文案）。

## 本次变更（2026-11-12）

- 仓库重整为产品根布局（ADR-0001）：`src/bank_assistant`、`tests/`、`docs/plan`、`archive/learning`、`archive/design-v0`。
- 计划书升到 v1.1；新增 ADR-0001～0004；GLOSSARY 补 17 个词条。
- 依赖改为 uv 单一入口；包名 `app` → `bank_assistant`；测试从包内移到仓库根。
- ticket 已发布到 GitHub Issues：#1 总览、#2–#32 共 31 张（含 46 条原生依赖边）。

## M1 双机一致性

Ubuntu 与 Mac 同 SHA；两端 `uv run pytest tests -q` 均 144 用例全过（Ubuntu: Python 3.13.14 / fastapi 0.162.5）。详见 `docs/milestones/M1-report.md`。

## 已执行验证

- `uv sync --locked`：成功（uv 0.10.11，Python 3.13.12）。
- `uv run pytest tests -q`：无 ImportError/ModuleNotFoundError；剩余 2 个失败已映射到工单（#4 意图常量、#9 truncate 契约）。
- `make doctor`：见 M0 报告。
- `make sync-check`：见 M0 报告。

## 未执行验证与原因

- 真实云模型链路冒烟：等 M1 的适配层（#6）完成后执行，避免把一次调用当成能力证明。
- 容器构建、冒烟：进行中（#8）。

## 外部依赖和负责人

- Ubuntu 整机重启：需负责人逐次准许。
- GPU 分时：M6 微调（#29）前需确认 `dingtalk-train` 空闲；与 `dingtalk-*` 共享需负责人明确同意。

## M5 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #23 T22 前端骨架+登录+版本页 | DONE | web build/test 通过；GET /system/version 真实数据、无密钥 |
| #24 T23 坐席工作台页 | DONE | web 24 tests；引用可打开原文 |
| #25 T24 质检复核页 | DONE | web 47 tests；409 冲突刷新 + 完成门禁 |
| #26 T25 演示脚本与端到端 | DONE | 6 段合成会话 + 3 个回放待复核任务；demo-reset 安全单测；Playwright 1/1；真实 chat+embedding 冒烟通过；见 [M5 报告](milestones/M5-report.md) |

## M5 T25 验收证据（2026-11-12）

- `data/demo/conversations/*.json`：S1/S2/S3 × normal/risk 共 6 段，全部合成、正文不含数字。
- `make seed-demo`：幂等导入账号/知识/规则/会话，并用**真实 worker + 显式回放**生成 3 个待复核任务；重复执行 0 新建。
- `make demo-reset`：只允许 `DB=bank_assistant_demo CONFIRM=yes`，URL 库名必须精确匹配；拒绝 dev/test/restore 与 staging/production；dry-run 与真实清空均验证。
- `make test-e2e`：一次性本机 compose 数据库 + 隔离后端（replay）+ 前端 preview；Playwright 1 passed，截图在 `web/e2e/screenshots/`（gitignore）。
- `make demo-real-model-smoke`：DeepSeek chat `status=succeeded` + fastembed embedding 512 维成功，未打印密钥。
- **ADR-0006 过渡做法**：e2e 暂用本机一次性 compose 数据库的独立 `bank_assistant_demo`，不是隧道 + `*_test`；T33 只迁移正式数据库入口，e2e 切换另立工单。

## M5 验收

M5 报告：`docs/milestones/M5-report.md`（T25 交付）。web 48 tests、`make test-e2e` 1 passed（回放链路）、真实云模型冒烟成功（DeepSeek chat + BGE embedding）。工单 #23/#24/#25/#26 全部 DONE。

## M7 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #30 T29 部署与恢复演练 | DONE | 服务器 bank-assistant-api healthy、仅 127.0.0.1:8000；备份恢复行数与哈希一致；回滚演练通过 |
| #31 T30 监控/日志/安全测试 | READY_FOR_ACCEPTANCE | 650 unit + 138 integration；真实 `/metrics`/trace_id 日志/密文扫描 0；`docs/milestones/M7-observability-report.md` |
| #32 T31 发布包与运维手册 | READY_FOR_ACCEPTANCE | `docs/releases/v0.1.0.md` + `release.json`（SHA/镜像/传输校验/Alembic/索引/模型）；`make release-verify` PASS；`docs/ops/{dependency-audit,risk-register,runbook}.md`；[M7 验收报告](milestones/M7-report.md) |
| #38 T38 容量压测 / 24h 探测 / 受控故障演练 | DONE（24h 探测 IN_PROGRESS） | 10 并发×600s：344,734 请求、0 错误、P95 最大 43.224ms；探测已启动（observed 25 分钟）；API/DB 重启恢复 1.863s/1.820s、任务不变量 0 running/0 重复；见 [M7 性能报告](milestones/M7-performance-report.md) |
| #39 T39 质检 P95 与真实供应商故障演练 | DONE（成功率风险 R-19 开放） | 真实 DeepSeek、5 worker/5 并发、60 次质检：POST P95 245.244ms、完成 P95 41.879s、成功 P95 32.146s；成功 10/60、失败 50/60 均 `EVIDENCE_INVALID` 且 findings=0；token 256,621/37,309、估算 ¥0.8117；429/504/503/502 四类映射全部正确、无假成功；见 [M7 性能报告 §12/§13](milestones/M7-performance-report.md) |

## T38 容量压测、稳定性探测与受控故障演练（#38，2026-11-13）

| 项 | 实测/结果 |
|---|---|
| 非模型 API 压测 | 10 并发 × 600.017s，344,734 请求、0 错误、574.54 req/s；四端点 P95 最大 **43.224ms**（`/health/live` 3.848、`ready` 21.446、`version` 30.265、`inspections/{id}` 43.224）；门槛 PASS |
| 候选答复 replay | 独立 `127.0.0.1:8011` 临时实例、5 并发×120.25s、1,514 请求、0 错误、P95 **464.291ms**；非模型质量证据 |
| 资源 | 压测后 API 88.47→96.14 MiB、DB 80.13→110.80 MiB；`bank_db_probe_errors_total` 0、非模型 5xx 0 |
| 24h 探测 | 2026-11-13T03:28:12Z 启动，5 分钟 health / 30 分钟 metrics；脚本 `scripts/probe_uptime.py`，产物 `~/workspace/bank-assistant/artifacts/probe/`；截至报告 25 分钟、无失败；未满 24h |
| API 重启 | 只重启 `bank-assistant-api`；`000`→`200` 1.863s（命令→200 2.166s）；日志 ERROR/Traceback 0 |
| DB 重启 | 只重启 `bank-assistant-db-db-1`；`503`→`200` 1.820s（命令→200 2.127s）；`running_tasks=0`、`dup_task_runs=0`、`dup_findings=0` |
| 边界 | 未重启服务器、未触碰 redis/dingtalk、未降 schema；同机回环/单实例，不外推 LAN/多实例 |
| 测试 | `uv run pytest tests`：**681 passed, 138 deselected**（新增压测/探测纯逻辑 17 例） |
| 报告/脚本 | [M7 性能报告](milestones/M7-performance-report.md)；`scripts/load_test.py`、`scripts/probe_uptime.py`；运维手册 §13 |

## T39 质检任务 P95 实测与真实供应商故障演练（#39，2026-11-13）

| 项 | 实测/结果 |
|---|---|
| 环境 | Mac 临时 API `127.0.0.1:18080`（未触碰线上）、`bank_assistant_test`（隧道 15432，迁移 `0011`）、独立索引 `idx-20261113T035753Z-4e8139e2`、5 个真实 worker、`MODEL_MODE=live` / `deepseek-chat` |
| 样本 | 5 并发提交，2 批各 30 次，共 **60 次**；6 段合成会话轮转，每条 2 条规则 |
| 接收延迟 | POST P95 **245.244ms**（n=30，线性插值），≤1s **PASS** |
| 完成时间 | 全部终态 P95 **41.879s**（n=60）；成功任务 P95 **32.146s**（n=10）；均 ≤60s **PASS（延迟）** |
| 成功率 | **10/60 succeeded**；50 次 `EVIDENCE_INVALID`（83.3%），失败 findings 全为 0；无 2xx 假成功 |
| 失败原因 | 真实模型证据区间偏移/引文多句号（`end` 少 1、`end=39` vs 32 等），被契约证据校验拒绝；不是限流/超时 |
| token/成本 | 235 次 live 模型调用全部 `succeeded`；输入 256,621 / 输出 37,309；按 2025-02-09 官方价目快照估算 **¥0.8117**（预算 ¥5） |
| 供应商故障演练 | 本地假供应商 + 临时 API `18081`：429→`MODEL_RATE_LIMITED`、2s 超时→504 `MODEL_TIMEOUT`、连接被拒→503 `MODEL_UNAVAILABLE`、非法 JSON→502 `MODEL_INVALID_STRUCTURE`；4 条建议 `failed`/answer 空、4 条质检 `failed`/findings 0；trace `t39-drill-drill2-*` |
| 清理/边界 | 临时 API、假供应商、5 个 worker 已停止；`bank_assistant_dev`/`bank_assistant_demo` 本轮 T39 任务 0；测试库保留实测行（可 reset）；未重启服务器、未触碰线上容器 |
| 风险 | 新增 **R-19**（live 质检成功率 16.7%）；§7 延迟 PASS 不能代表功能健康 |
| 测试 | `uv run pytest tests -q`：**699 passed, 138 deselected**（新增 18 个离线纯逻辑测试：分位/成本/token 解析/契约映射/假成功断言） |
| 报告/脚本 | [M7 性能报告](milestones/M7-performance-report.md) §12/§13；`scripts/inspection_latency_measure.py`、`scripts/fake_supplier.py`、`scripts/supplier_failure_drill.py` |

## T31 发布包（#32，2026-11-12）

- 发布记录：[docs/releases/v0.1.0.md](releases/v0.1.0.md)；清单：[release.json](../release.json)
- 发布身份：代码 `59a5869`、镜像 `bank-assistant:api-59a5869`（id `1c7dc47e…`）、
  tar sha256 `779f9a9e…`、Alembic `0011_report_exports`、索引 `idx-20261112T110703Z-2334d03e`。
- 发布校验：`make release-verify` PASS（image id/标签、容器 healthy、live/ready、
  Alembic head、版本页 `code_sha`/索引/校准/embedding/生成模型全部一致）。
- 依赖审计：Python 111 个运行时依赖，`chromadb 1.5.9` 5 条公告记录（4 唯一，无修复）；
  npm prod/全部均 0 漏洞。见 [依赖记录](ops/dependency-audit.md)。
- 风险：18 项（High 7 / Medium 8 / Low 3），见 [风险清单](ops/risk-register.md)。
- 手册：`make release-verify`、事故第一响应（503/429/504/worker）与命令索引已并入
  [运维手册](ops/runbook.md)。
- 边界：**不是生产部署授权**；无 tag、未 push；Lead 合并后由负责人打 tag。

## M6 验收

M6 报告：`docs/milestones/M6-report.md`（READY_FOR_ACCEPTANCE，含未达门槛与 NOT_RUN 明示）。数据集 330/186 冻结；replay 全量 + live 20 条；Recall@5 replay 0.833 **未达 0.85**；引用支持度/人工相关性 NOT_MEASURED；微调：T28 `NOT_RUN`（2026-11-12，GPU 未获授权）→ **T35 真实执行（2026-11-13）**：Qwen3-1.7B 4-bit QLoRA，冻结 test accuracy 0.64 → 0.76、macro-F1 0.6138 → 0.7441（n=50，不宣称显著），见 [M6 微调报告](milestones/M6-tuning-report.md)。

## M6 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #27 T26 数据集构建与冻结 | DONE | 330 检索/186 质检、按组 60/20/20、manifest 哈希两次一致 |
| #28 T27 评测脚本与基线对比 | READY_FOR_ACCEPTANCE | `make eval` 全量 replay（50 检索/48 质检，1014 条预测）；live 20 条真实 DeepSeek（p50 1.23s、p95 1.66s、¥0.338 估算）；产物 `artifacts/evals/eval-replay-…103010Z`、`eval-live-…102704Z` |
| #34 T33 数据库迁到 Ubuntu + SSH 隧道 | DONE（部署已执行） | PG18 容器 healthy；四库 owner=bank；`make test-integration` 130 passed；`make doctor` 隧道缺失失败/建立通过；`make seed-demo` 幂等且不触达 dev/test；LAN 直连失败 |
| #29 T28 条件性微调 | READY_FOR_ACCEPTANCE | `make train` 默认 dry-run（train 202/test 50，数据哈希校验通过）；真实训练 `NOT_RUN`（GPU 1457 MiB 被 `dingtalk-embed` 占用、模型权重未缓存）；见 [M6 微调报告](milestones/M6-tuning-report.md) |
| #35 T35 真实 QLoRA 意图分类微调 | DONE | 2026-11-13 Ubuntu 3060 Ti：`status=SUCCEEDED`、26 steps、wall 130.4s、vram_peak 2.706 GiB、逐次 loss 0.3179；冻结 test baseline 0.64 → tuned 0.76、macro-F1 0.6138 → 0.7441（32/50 → 38/50，n=50）；`code_sha=1746d16`、数据哈希/seed 见 manifest；不 kill `dingtalk-embed`；见 [M6 微调报告](milestones/M6-tuning-report.md) |
| #30 T29 部署与恢复演练 | READY_FOR_ACCEPTANCE | 镜像 `api-1fb91b6`；备份恢复/回滚/索引重建全部实测；`docs/milestones/M7-deploy-report.md` |

## M7 监控/日志/安全测试（T30 / #31，2026-11-12 已执行）

| 项 | 实际值 |
|---|---|
| 自动化 | `uv run pytest tests -m "not integration"` 650 passed；`make test-integration` 138 passed |
| 真实服务器 | 部署 `api-59a5869`；live/ready 200；`/metrics` 含 HTTP/模型/任务/DB 序列；仅回环 |
| 日志 | 访问日志单行 JSON 带 trace_id；秘密匹配 0、演示会话关键词 0 |
| 恢复 | 仅重启 `bank-assistant-api` 后 live/ready 200、healthy |
| 报告 | `docs/milestones/M7-observability-report.md`；手册 `docs/ops/runbook.md` §10 |

## M7 部署与恢复演练（T29 / #30，2026-11-12 已执行）

| 项 | 实际值 |
|---|---|
| 镜像 | `bank-assistant:api-1fb91b6`，id `sha256:1c755209…`，`linux/amd64`；`docker save \| ssh docker load`，tar sha256 `7aeeca3b…` |
| 代码 SHA | `1fb91b6b22085dd8064ec6fe128420b2e598f755`（服务器 repo 重置到同一 SHA，工作树干净） |
| 容器 | `bank-assistant-api`，`restart=unless-stopped`，healthy；host 网络 + `--host 127.0.0.1`，`ss` 仅 `127.0.0.1:8000` |
| 配置 | 仓库外 `.env.app`（600）；数据/产物在 `~/workspace/bank-assistant/{data,artifacts}` |
| 索引 | `idx-20261112T110703Z-2334d03e`，校准 sha256 `136e030a…`，`BAAI/bge-small-zh-v1.5@512`，30 分块；版本页可查 |
| 迁移 | 迁移前备份 `20261112T110515Z` → `alembic upgrade head`（0011，no-op）→ 建索引 → 备份 `20261112T110714Z` |
| 恢复演练 | 两库恢复到 `*_restore_check`；21 张表行数一致；demo `knowledge_versions` 32 行 max `fea41b4d…` md5 `de01cec6…`；alembic 0011 |
| 回滚演练 | `api-1fb91b6` → `api-f4f1346`（live/ready/版本页通过）→ 恢复 `api-1fb91b6`；另做过 `api-f4f1346` ↔ `api-54fa344`；未降 schema、未重启服务器 |
| 隔离 | Mac 直连服务器 8000/5432 均拒绝；服务器仅回环监听 |
| 报告 | `docs/milestones/M7-deploy-report.md`；手册 `docs/ops/runbook.md` |

## M4 验收

M4 报告：`docs/milestones/M4-report.md`（READY_FOR_ACCEPTANCE）。迁移单 head `0011_report_exports`；448 unit / 130 integration passed。

## M4 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #18 T17 规则版本与演示规则集 | DONE | 6 条 demo_only 规则；360 unit / 78 integration |
| #19 T18 质检任务执行 | READY_FOR_ACCEPTANCE | inspection_runs/findings + 0009 迁移；202+幂等、worker 租约/条件最终化、失败 error_code；379 unit / 94 integration |
| #20 T19 证据定位与校验 | READY_FOR_ACCEPTANCE | 码点证据逐字校验+四态+否定/转述；422 unit / 101 integration |
| #21 T20 人工复核闭环 | DONE | 440 unit / 116 integration；并发 409 + 完成门禁 |
| #22 T21 报告导出 | DONE | CT-12 快照 hash 不变；HTML 转义；权限 404 |

## M3 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #15 T14 演示知识与版本化索引 | DONE | 32 条演示知识；真实 fastembed 建索引 idx-20261112T084715Z；246 unit / 58 integration passed |
| #16 T15 候选答复端到端 | DONE | 324 unit / 69 integration；引用校验+四态状态机 |
| #17 T16 采纳反馈 | DONE | 反馈 append-only；明确非正确性标签 |

## M2 双机与迁移

M2 报告：`docs/milestones/M2-report.md`。迁移单一 head `cf1edc530a0c`（两条 merge revision 收口并行分叉）。

## 下一次执行入口

M1 报告：`docs/milestones/M1-report.md`。M2 按 GitHub Issues 的 frontier 执行：无未关闭阻塞的 ticket 即为可领取任务（当前 #4/#8/#10 进行中）。
集成分支 `feat/bank-assistant`；每张 ticket 一个 worktree 分支 `ticket/Txx`，合并后关闭 issue。
