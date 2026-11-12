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

| 里程碑 | 状态 | 证据 |
|---|---|---|
| M0 基线与双机环境 | IN_PROGRESS | [进度文件](docs/implementation-status.md) |
| M1–M7 | NOT_STARTED | [计划书 v1.1](docs/plan/bank-customer-service-plan.md) |

唯一进度状态源是 [docs/implementation-status.md](docs/implementation-status.md)；阶段报告在 `docs/milestones/`。

## 快速开始

```bash
cp .env.example .env      # 填入自己的模型 key；.env 已被 gitignore
make doctor               # 检查依赖、配置、端口（不打印秘密值）
make build                # 按 uv.lock 安装依赖
make test-unit            # 无网络、无真实凭据的单元测试
make sync-check           # 本机与 Ubuntu 的 SHA 对比（只读）
```

尚未实现的命令（`up/down/smoke/seed-demo/eval/train/backup/restore-check/test-e2e/test-integration`）
会**非零退出并说明属于哪个里程碑**，不会假装成功。

## 设计与决策

- 需求与验收：[计划书 v1.1](docs/plan/bank-customer-service-plan.md)、[系统契约](docs/plan/bank-customer-service-contracts.md)、[执行交接](docs/plan/bank-customer-service-execution.md)、[现状分析](docs/plan/bank-customer-service-assessment.md)
- 架构决策：[ADR-0001 仓库布局](docs/adr/0001-repository-layout.md)、[ADR-0002 数据库与索引](docs/adr/0002-database-and-index.md)、[ADR-0003 双机同步](docs/adr/0003-two-machine-sync.md)、[ADR-0004 模型与微调](docs/adr/0004-model-and-tuning.md)
- 领域术语：[GLOSSARY.md](GLOSSARY.md)（写 issue / 测试名 / 文档一律使用其中文术语）

## 目录结构

```text
src/bank_assistant/   后端（FastAPI）
tests/                单元 / 集成 / 契约 / 端到端测试
web/                  前端（M5 起）
deploy/               容器与部署（M1/M7）
migrations/           数据库迁移（M2 起）
docs/                 计划、ADR、设计、阶段报告、进度
scripts/              doctor / sync-check 等
archive/              学习历史与原设计文档（不参与构建）
```

## 与历史仓库的关系

本仓库早期是个人 AI Agent 学习项目（`learn_20260205/`、`learn_20260210/`、notebook、面试笔记）。
这些内容已归档到 `archive/`，仅作历史保留，不参与构建、不被测试收集。
