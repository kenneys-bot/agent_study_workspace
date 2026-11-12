# 执行进度

当前里程碑：M5（进行中）；M1–M4 已交付
当前任务：M5 T25 演示脚本与端到端（#26）已完成；M6 T27 评测脚本（#28）并行
当前状态：M1–M4 READY_FOR_ACCEPTANCE；M5 T25 DONE（待负责人验收）；M6 IN_PROGRESS（数据库部署方案已于 2026-11-12 调整，见 ADR-0006；实现待办见下）
本机代码分支：`feat/bank-assistant`
本机代码SHA：`979b076`（后续提交见 git log）
Ubuntu代码SHA：`2c5e0d7`（M3 期间的同步点；集成分支已前进，下次合并后刷新）
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

## 架构调整：数据库集中到 Ubuntu（2026-11-12）

**已完成的文档修改**（本次，未执行任何数据库/容器操作）：新增 `docs/adr/0006-postgres-on-ubuntu-only.md`；`docs/adr/0002-database-and-index.md` 标记 superseded；计划书 v1.2（§4.1/§4.2/§4.3/M2 验收）、执行交接（命令前置条件与 BLOCKED 语义）、README、`docs/environment.md`、M0–M3 历史报告加注。

**待实现的配置/脚本/测试入口调整**（属后续工单，未做）：

| 项 | 现状（旧方案） | 目标（ADR-0006） |
|---|---|---|
| `scripts/doctor.sh` | 检查本机 docker 与 5432 端口 | 检查 SSH 隧道与 `*_test` 库可达性；本机不再要求数据库 |
| `scripts/test-integration.sh` | 本机起 compose PG（`deploy/compose.db.yml`） | 连隧道 `127.0.0.1:15432` 的 `bank_assistant_test`；隧道不通则非零退出并说明 |
| `.env.example` / `.env` | `DATABASE_URL=...@localhost:5432/...` | `...@127.0.0.1:15432/bank_assistant_dev`；新增 `*_demo`/`*_test`/`*_restore_check` 说明 |
| `make seed-demo` | 连本机库 | 连隧道写 `bank_assistant_demo`，幂等，不触达测试库 |
| `make backup` / `restore-check` | 未实现 | 在 Ubuntu 上 `pg_dump` 到 `~/workspace/bank-assistant/backups`；恢复到 `bank_assistant_restore_check`，不得覆盖源库 |
| `deploy/compose.db.yml` | 本机与服务器都用 | 仅服务器使用，保持 `127.0.0.1:5432` 回环绑定 |
| CI/复现说明 | 默认本机有 PG | 需自备服务器 + 隧道；无数据库时只跑 `make test-unit` |

**边界**：以上是计划调整，不代表部署已改变。服务器上的数据库、容器、凭据本次均未动。

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
- GPU 分时：M6 微调（#28）前需确认 `dingtalk-train` 空闲。

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
- **ADR-0006 过渡做法**：e2e 暂用本机 compose 的独立 `bank_assistant_demo`，不是隧道 + `*_test`；TODO 指向后续数据库访问工单，落地后移除本机 compose 依赖。

## M6 工单状态

| 工单 | 状态 | 证据 |
|---|---|---|
| #27 T26 数据集构建与冻结 | DONE | 330 检索/186 质检、按组 60/20/20、manifest 哈希两次一致 |
| #28 T27 评测脚本与基线对比 | IN_PROGRESS | worktree 中进行 |
| #29 T28 条件性微调 | BLOCKED | 等 #28 |

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
