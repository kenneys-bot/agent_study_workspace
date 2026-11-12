# 执行进度

当前里程碑：M1（已交付）→ M2（进行中）
当前任务：M2 T11/T12/T13 并行（#12/#13/#14）
当前状态：M1 READY_FOR_ACCEPTANCE；M2 IN_PROGRESS
本机代码分支：`feat/bank-assistant`
本机代码SHA：`979b076`（后续提交见 git log）
Ubuntu代码SHA：见下方 M0-03 证据（同一 SHA）
最近更新：2026-11-12（Asia/Shanghai）

## 任务状态

| 任务 | 状态 | 验收证据 | 阻碍/下一步 |
|---|---|---|---|
| M0-01 基线 | DONE | 分支/SHA 记录；`pyproject.toml` + `uv.lock` 唯一依赖入口；`docs/environment.md` | — |
| M0-02 配置模板 | DONE | `.env.example`（仅占位）、`.gitignore`、`docs/environment.md` | — |
| M0-03 双机同步 | READY_FOR_ACCEPTANCE | Ubuntu `~/projects/agent_study_workspace` 检出同一 SHA；`~/bank-assistant-*` 可写；`make sync-check` | 负责人验收 |
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
| #12 T11 知识生命周期 | IN_PROGRESS | worktree 中进行（迁移 0003） |
| #13 T12 会话持久化与审计 | IN_PROGRESS | worktree 中进行（迁移 0004） |
| #14 T13 幂等与任务表 | IN_PROGRESS | worktree 中进行（迁移 0005） |

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

## 下一次执行入口

M1 报告：`docs/milestones/M1-report.md`。M2 按 GitHub Issues 的 frontier 执行：无未关闭阻塞的 ticket 即为可领取任务（当前 #4/#8/#10 进行中）。
集成分支 `feat/bank-assistant`；每张 ticket 一个 worktree 分支 `ticket/Txx`，合并后关闭 issue。
