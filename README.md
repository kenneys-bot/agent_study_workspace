# 银行智能客服助手（坐席辅助 + 会后文本质检）

> **这是个人展示项目**，用于求职汇报。没有真实业务方、没有银行系统接入、没有生产部署许可。
> 全部业务数据为合成数据；业务知识只来自**公开监管条文**与**自造的虚构演示制度**（标注 `demo_only`），
> 不含任何机构的真实内部资料。不得据此声称"生产上线""服务多少坐席""效率提升多少"。

## 这是什么

面向银行**内部坐席**与**质检人员**的两个能力，首版覆盖挂失咨询、信用卡费用/还款咨询、投诉受理：

- **坐席辅助**：载入客户会话 → 提取核心诉求 → 检索有效期内的知识版本 → 生成带引用的**候选答复**（由坐席确认后使用）。
- **会后质检**：对已结束会话按**质检规则**逐项判断服务风险 → 定位原文**证据** → 质检员逐项**人工复核** → 导出报告。

关键约束（写在契约里，也写在这里）：候选答复不是自动办理结果；**风险线索**不是已认定违规；
**证据不足**不等于合格；报告导出对应固定快照，后续复核不篡改历史。

## 当前状态

**M0–M7 全部 ACCEPTED**（负责人 2026-11-13 明确签收）。签收后继续推进的改进见下方发布记录与风险清单。

| 里程碑 | 状态 | 证据 |
|---|---|---|
| M0 基线与双机环境 | ACCEPTED | [M0 报告](docs/milestones/M0-report.md) |
| M1 修通后端 | ACCEPTED | [M1 报告](docs/milestones/M1-report.md) |
| M2 存储与权限 | ACCEPTED | [M2 报告](docs/milestones/M2-report.md) |
| M3 坐席辅助 | ACCEPTED | [M3 报告](docs/milestones/M3-report.md) |
| M4 质检闭环 | ACCEPTED | [M4 报告](docs/milestones/M4-report.md) |
| M5 工作台与演示 | ACCEPTED | [M5 报告](docs/milestones/M5-report.md) |
| M6 数据集与评测 | ACCEPTED（门槛未全达，报告如实写明） | [M6 报告](docs/milestones/M6-report.md)、[评测说明](docs/eval/README.md)；Recall@5 已由 #36 提升到 0.944、真实 QLoRA 已执行（0.64→0.76，n=50）、引用支持度待人工标注（[标注包](docs/eval/citation-support-pack.md)） |
| M7 生产就绪与发布包 | ACCEPTED（§7 门槛部分达成，未达成项见风险清单） | [M7 报告](docs/milestones/M7-report.md)、[部署](docs/milestones/M7-deploy-report.md)、[观测](docs/milestones/M7-observability-report.md)、[性能](docs/milestones/M7-performance-report.md)、[运维手册](docs/ops/runbook.md) |

唯一进度状态源是 [docs/implementation-status.md](docs/implementation-status.md)；阶段报告在 `docs/milestones/`。

## 发布记录（个人展示发布，不是生产部署授权）

| 版本 | 内容 | 发布记录 |
|---|---|---|
| v0.1.0 | 首个可追溯发布：镜像 `api-59a5869`、索引 `idx-20261112T110703Z-2334d03e` | [v0.1.0](docs/releases/v0.1.0.md) |
| v0.1.1 | `title_chunk` + 确定性重排落到生产检索，重建索引 `idx-20261113T025706Z-f7974a6a`，镜像 `api-3a04952` | 见 release.json / M6 报告 |
| **v0.1.2（当前线上）** | 补常驻 worker 服务 + 部署 R-19 修复（quote-first 证据定位），镜像 `bank-assistant:api-7542309` | [v0.1.2](docs/releases/v0.1.2.md) |

当前线上身份（`make release-verify` 一条命令核对）：

| 项 | 值 |
|---|---|
| 代码 SHA | `7542309cbc9090d46a6af8278f2b42a1f43c1aa6`（`7542309`） |
| 镜像 | `bank-assistant:api-7542309`（`sha256:3fff5246…`，`linux/amd64`） |
| 部署 | `my-ubuntu-server`：`bank-assistant-api` + `bank-assistant-worker`，仅 `127.0.0.1:8000` |
| Alembic head | `0011_report_exports` |
| 索引 | `idx-20261113T025706Z-f7974a6a`，校准 `136e030a…`，`BAAI/bge-small-zh-v1.5@512` |
| 生成模型 | `deepseek` / `deepseek-chat`（`live`） |

- 机器可读清单：[release.json](release.json)
- 依赖审计：[docs/ops/dependency-audit.md](docs/ops/dependency-audit.md)（`chromadb 1.5.9` 有上游未修复公告，已登记）
- 风险清单：[docs/ops/risk-register.md](docs/ops/risk-register.md)
- 引用支持度人工标注：[标注包](docs/eval/citation-support-pack.md) + [CSV](labels/citation-support.csv)（标签为空，等待负责人填写；计分 `uv run python scripts/score_citation_support.py --labels labels/citation-support.csv`）
