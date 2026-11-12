# 执行进度

当前里程碑：M7 部署与恢复演练已交付（待负责人验收）；M6 已交付（T28 真实训练 NOT_RUN）
当前任务：T29 部署与恢复演练（#30）已执行；T28 条件性微调（#29）READY_FOR_ACCEPTANCE；T33 ADR-0006 数据库落地（#34）已执行
当前状态：M1–M5 已交付（等待负责人验收）；M6 已交付（T26/T27 完成、T28 harness 交付但真实训练 NOT_RUN）；**M7-01/M7-05 已真实执行**（不可变镜像部署、备份恢复演练、回滚演练、索引重建；2026-11-12）
本机代码分支：`feat/bank-assistant`（集成分支）
本机代码SHA：以 `git log -1` 为准（T29 合并后刷新）
Ubuntu代码SHA：`1fb91b6`（合并 `feat/bank-assistant` 后的部署提交；镜像 `bank-assistant:api-1fb91b6`）
最近更新：2026-11-12（Asia/Shanghai）

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
| #31 T30 监控/日志/安全测试 | IN_PROGRESS | worktree 中进行 |
| #32 T31 发布包与运维手册 | BLOCKED | 等 #31 |

## M6 验收

M6 报告：`docs/milestones/M6-report.md`（READY_FOR_ACCEPTANCE，含未达门槛与 NOT_RUN 明示）。数据集 330/186 冻结；replay 全量 + live 20 条；Recall@5 replay 0.833 **未达 0.85**；引用支持度/人工相关性 NOT_MEASURED；微调 NOT_RUN（GPU 未获授权）。

## M6 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #27 T26 数据集构建与冻结 | DONE | 330 检索/186 质检、按组 60/20/20、manifest 哈希两次一致 |
| #28 T27 评测脚本与基线对比 | READY_FOR_ACCEPTANCE | `make eval` 全量 replay（50 检索/48 质检，1014 条预测）；live 20 条真实 DeepSeek（p50 1.23s、p95 1.66s、¥0.338 估算）；产物 `artifacts/evals/eval-replay-…103010Z`、`eval-live-…102704Z` |
| #34 T33 数据库迁到 Ubuntu + SSH 隧道 | DONE（部署已执行） | PG18 容器 healthy；四库 owner=bank；`make test-integration` 130 passed；`make doctor` 隧道缺失失败/建立通过；`make seed-demo` 幂等且不触达 dev/test；LAN 直连失败 |
| #29 T28 条件性微调 | READY_FOR_ACCEPTANCE | `make train` 默认 dry-run（train 202/test 50，数据哈希校验通过）；真实训练 `NOT_RUN`（GPU 1457 MiB 被 `dingtalk-embed` 占用、模型权重未缓存）；见 [M6 微调报告](milestones/M6-tuning-report.md) |
| #30 T29 部署与恢复演练 | READY_FOR_ACCEPTANCE | 镜像 `api-1fb91b6`；备份恢复/回滚/索引重建全部实测；`docs/milestones/M7-deploy-report.md` |

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
