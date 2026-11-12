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

尚未实现的命令（`up/down/smoke/eval/train/backup/restore-check/test-e2e`）
会**非零退出并说明属于哪个里程碑**，不会假装成功。

### 演示知识与版本化索引（M3）

```bash
make seed-demo       # 幂等导入 32 条合成演示知识（30 条在有效期内 + 2 条边界用例）
make index-rebuild   # 用 fastembed + BAAI/bge-small-zh-v1.5（512 维）重建索引并切换指针
make index-info      # 打印活动索引版本、校准向量哈希与清单摘要
```

- 演示知识在 `data/demo/knowledge/`：三场景各 10 条，另有 2 条「已过期 / 未生效」边界用例。
  公开监管条文只引用公开链接（正文自行组织）；虚构制度均标注 `demo_only` 并在正文注明
  「虚构演示制度」，不对应任何真实机构制度或话术。
- 两个演示账号由显式脚本创建（`make seed-demo-accounts`，`make seed-demo` 会先执行它；
  知识导入本身不建用户）：`demo-author`（agent）/ `demo-reviewer`（reviewer），
  同一 scope `demo-bank`，演示密码 `demo-only-correct-horse-battery`（仅本地演示，不是秘密）。
- 索引是**可重建产物**：`data/index/chroma`、`data/index/manifests/<index_version>.json`、
  活动指针 `data/index/active.json`，全部已 gitignore；清单记录 `index_version`、
  `embedding_model_version`、知识快照与**校准向量哈希**，加载前校验，模型漂移则拒绝使用。
- 离线无模型时可 `EMBEDDING_PROVIDER=fake make index-rebuild`（确定性假向量）验证构建/切换/
  失败保留路径；fake 索引没有真实语义质量，不能当作检索效果证据。

### 质检规则版本与演示规则集（M4-01）

```bash
make seed-demo       # 幂等导入：演示账号 + 32 条演示知识 + 6 条演示规则
```

- 演示规则在 `data/demo/rules/`：挂失 2 条、信用卡费用 2 条、投诉 2 条，覆盖
  M4-02 的六个默认类别（索取密码/验证码、无依据声称办理成功、无依据承诺减免费用、
  费用说明与有效条款冲突、阻碍投诉、明显侮辱性表达），其中 5 条为高风险。每条规则
  都带适用范围、证据要求、风险级别、正反例/证据不足/不适用示例与豁免条件。
- 示例统一使用契约 §4 证据 JSON，并附合成 `sample_turns`；证据区间按 Unicode
  码点计数（start 含、end 不含），可按 `content[start:end] == quote` 回验原文。
- 全部为合成演示规则（`demo_only=true`，说明注明「虚构演示制度」）；来源只用
  `demo.invalid` 占位地址与公开监管链接，不对应任何机构的真实规则。
- 规则版本走与知识相同的生命周期：`draft → in_review → published → retired`；
  发布必须是 reviewer/admin 且不能是作者本人；已发布内容不可覆盖，修改要新建版本。
- 适用范围窗口是半开区间 `[valid_from, valid_to)`。同一规则发布窗口重叠的新版本时，
  旧版本被**显式退役**并在响应 `superseded_rule_version_ids` 中回报——决策、理由与
  测试见 `src/bank_assistant/rules/service.py` 与 `tests/integration/test_rules.py`。
- 接口（`/api/v1`）：`POST /rules`、`POST /rules/versions/{id}/submit|publish|retire`、
  `GET /rules/{rule_id}`（返回生效的 published 版本 + applicability）。

## 设计与决策

- 需求与验收：[计划书 v1.1](docs/plan/bank-customer-service-plan.md)、[系统契约](docs/plan/bank-customer-service-contracts.md)、[执行交接](docs/plan/bank-customer-service-execution.md)、[现状分析](docs/plan/bank-customer-service-assessment.md)
- 架构决策：[ADR-0001 仓库布局](docs/adr/0001-repository-layout.md)、[ADR-0002 数据库与索引](docs/adr/0002-database-and-index.md)、[ADR-0003 双机同步](docs/adr/0003-two-machine-sync.md)、[ADR-0004 模型与微调](docs/adr/0004-model-and-tuning.md)、[ADR-0005 embedding 运行时与索引版本化](docs/adr/0005-embedding-runtime-and-index-versioning.md)
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
