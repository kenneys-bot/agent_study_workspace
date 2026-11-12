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
| M0 基线与双机环境 | READY_FOR_ACCEPTANCE | [M0 报告](docs/milestones/M0-report.md) |
| M1 修通后端 | READY_FOR_ACCEPTANCE | [M1 报告](docs/milestones/M1-report.md) |
| M2 存储与权限 | READY_FOR_ACCEPTANCE | [M2 报告](docs/milestones/M2-report.md) |
| M3 坐席辅助 | READY_FOR_ACCEPTANCE | [M3 报告](docs/milestones/M3-report.md) |
| M4 质检闭环 / M5 工作台 | READY_FOR_ACCEPTANCE | [M5 报告](docs/milestones/M5-report.md) |
| M6 数据集与评测 | READY_FOR_ACCEPTANCE | [评测说明](docs/eval/README.md)；T28 微调为条件任务 BLOCKED |
| M7 部署与恢复演练 | READY_FOR_ACCEPTANCE | [M7 部署报告](docs/milestones/M7-deploy-report.md)、[运维手册](docs/ops/runbook.md) |

唯一进度状态源是 [docs/implementation-status.md](docs/implementation-status.md)；阶段报告在 `docs/milestones/`。

## 快速开始

```bash
cp .env.example .env      # 填入自己的模型 key；.env 已被 gitignore
make doctor               # 检查依赖、配置、SSH 隧道与目标库（不打印秘密值）
make build                # 按 uv.lock 安装依赖
make test-unit            # 无网络、无真实凭据的单元测试
make sync-check           # 本机与 Ubuntu 的 SHA 对比（只读）
```

### 数据库（只在 Ubuntu 上）

PostgreSQL 18 只部署在服务器；**Mac 不安装、不启动本地数据库**。需要数据库的命令，先建立 SSH 隧道：

```bash
make db-tunnel            # 幂等：127.0.0.1:15432 → my-ubuntu-server:127.0.0.1:5432
make db-tunnel-status     # 查看状态；make db-tunnel-stop 停止
make doctor               # 隧道 + bank_assistant_test 可达性
make test-integration     # 经隧道在 bank_assistant_test 上做空库迁移 + 集成测试
make seed-demo            # 经隧道写入 bank_assistant_demo（幂等）
```

`DATABASE_URL=postgresql+psycopg://bank:<password>@127.0.0.1:15432/bank_assistant_dev` 写在
本机 `.env`（gitignore，600）；密码同时只存在服务器 `~/workspace/bank-assistant/.env.pg`。

`localhost` 指 Mac 自己；只有建立转发后 `127.0.0.1:15432` 才指向服务器。数据库按用途隔离：`bank_assistant_dev` / `bank_assistant_demo` / `bank_assistant_test` / `bank_assistant_restore_check`——自动化测试只碰 `*_test`，`seed-demo` 只写 `*_demo`，恢复验证只写 `*_restore_check`，不明原因连不上就**显式失败**，不回退 SQLite/mock。详见 [ADR-0006](docs/adr/0006-postgres-on-ubuntu-only.md) 与 [docs/environment.md](docs/environment.md)。

`eval / backup / restore-check / deploy / rollback` 均已实现；条件任务 `train` 仍未实现，
会**非零退出**，不会假装成功。

### 部署与运维（M7，Ubuntu）

部署是**个人项目的生产就绪演练**，不是任何机构的生产上线。完整步骤见
[docs/ops/runbook.md](docs/ops/runbook.md)，要点：

```bash
make deploy          # 构建 bank-assistant:api-<sha7> → 传输 → 服务器精确 SHA → up
make deploy-index    # 用已部署镜像在服务器重建索引（fastembed，模型缓存复用）
make backup          # 服务器 pg_dump dev/demo 到仓库外 backups/<ts>/
make restore-check   # 只恢复到 *_restore_check，核对行数/alembic/关键哈希
make rollback        # 回滚演练：切上一版 → 校验 → 恢复当前版
make verify-deploy   # live/ready、回环端口、版本页真实 SHA/索引/模型
```

- API 容器 `bank-assistant-api` 只绑服务器 `127.0.0.1:8000`，不暴露局域网；
  配置在仓库外 `.env.app`（600），镜像不可变，服务器不重建源码。
- `/api/v1/system/version` 返回代码 SHA、模型、活动 `index_version`、
  索引校准 `sha256` 与索引实际 embedding 模型，部署身份可核对。
- 数据库仍只在服务器回环 `127.0.0.1:5432`；Mac 开发走 SSH 隧道。

### 本地界面（M5）

先让 API 在 `http://localhost:8000` 运行（可用 `make up`），再启动前端：

```bash
make web-install   # 安装 web/ 依赖（npm ci / npm install）
make web-dev       # 启动 Vite 开发服务器 → http://localhost:5173
```

登录使用后端已创建的账号；系统版本页展示真实的代码 SHA、模型、嵌入模型、索引版本与
运行模式，不内置静态假数据。接口基地址默认 `http://localhost:8000`，可在
`web/.env.local` 用 `VITE_API_BASE_URL` 覆盖（模板见 `web/.env.example`）。
前端组件测试用 mock fetch，不需要后端：`make web-test`。

### 演示知识与版本化索引（M3）

```bash
make seed-demo       # 幂等导入账号 + 32 条知识 + 6 条规则 + 六段会话与待复核质检
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
make seed-demo       # 幂等导入：演示账号 + 32 条知识 + 6 条规则 + 六段会话与待复核质检
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

### 质检任务执行（M4-03，T18）

```bash
make worker          # 常驻 worker：领取租约任务、按规则快照执行质检（需要模型凭据 + PostgreSQL）
uv run python -m bank_assistant.worker --once   # 本地验证：抽干当前队列后退出
```

- `POST /api/v1/inspections`（需 `Idempotency-Key`）返回 **202 + inspection_id + task_id**；
  创建时把当前范围内**已发布且在有效期**的规则版本固化为 `rule_snapshot`，可按
  `business_type` 与 `rule_ids` 收窄规则范围。没有适用规则时返回 422，不创建空跑质检。
- `GET /api/v1/inspections/{id}` 返回状态、逐规则判断（`hit / clear /
  insufficient_evidence / not_applicable`）、证据 JSON、知识引用与失败原因；跨范围与
  不存在统一 404。机器判断 `machine_verdict` 写入后不可覆盖，人工复核（后续票）只能追加。
- worker 使用既有租约队列：处理前续租，模型调用在事务外；收尾用
  `UPDATE ... WHERE status IN ('queued','running')` 条件更新 + findings
  `(inspection_id, rule_version_id)` 唯一约束，保证**同一质检只有一个最终结果**、重复投递
  不产生重复判断（CT-11）。模型/校验失败写稳定 `error_code` 与安全诊断，重试上限
  `TASK_MAX_ATTEMPTS`（默认 3）后终结为 `failed`；不会用空 findings 冒充“无问题”。

## 演示与浏览器端到端（M5）

8–10 分钟演示脚本见 [docs/demo-script.md](docs/demo-script.md)。所有演示数据为合成数据；
候选答复不是办理结果，风险命中不是违规认定。

```bash
# 演示库只允许 bank_assistant_demo；dev/test/restore 一律不碰
# 宿主机命令（uv/集成测试）用 127.0.0.1；make up 容器内改用 host.docker.internal
export DATABASE_URL='postgresql+psycopg://<user>:<password>@127.0.0.1:15432/bank_assistant_demo'

make demo-reset DB=bank_assistant_demo CONFIRM=yes DRY_RUN=1   # 先预演，不删数据
make demo-reset DB=bank_assistant_demo CONFIRM=yes             # 只清空 demo 库
make seed-demo                                                 # 幂等种子（含 3 个待复核任务）
make index-rebuild

# 演示建议用宿主机进程：直接使用宿主机的索引与 fastembed 缓存
uv run uvicorn bank_assistant.main:app --host 127.0.0.1 --port 8000
make web-dev                                                   # 前端 → :5173
```

`make up` 仍可用于只验证 API 容器健康（`/health/ready`）；若要在容器内跑完整候选答复，
数据库地址需写成 `host.docker.internal:15432`，并让容器能加载 fastembed 模型缓存。

`make demo-reset` 的安全闸门：必须显式 `DB=bank_assistant_demo` + `CONFIRM=yes`，
`DATABASE_URL` 的库名必须恰好是 `bank_assistant_demo`；dev/test/restore 与
staging/production 一律拒绝（单测见 `tests/test_demo_reset.py`）。

### 浏览器 e2e（Playwright）

```bash
cd web && npx playwright install chromium   # 首次需要浏览器
cd .. && make test-e2e   # 等价别名：make e2e
```

`make test-e2e` 会启动**一次性的本机 compose PostgreSQL**（独立项目名/端口/卷，库名
`bank_assistant_demo`）、隔离后端（`MODEL_MODE=replay`）与前端 preview，覆盖
登录 → 坐席工作台生成候选答复（回放，不烧钱）→ 质检任务 → 复核确认/驳回误报 → 报告导出。
截图与下载产物在 `web/e2e/screenshots/`（已 gitignore）。

> **ADR-0006 过渡做法（必须在报告中标注）**：正式方案是 Mac 经 SSH 隧道访问 Ubuntu 的
> `bank_assistant_test` 库。T25 先用本机 compose 数据库把 e2e 跑通；自动化测试**只**操作
> 独立项目的一次性 `bank_assistant_demo`，绝不接触 dev/test/restore 库。
> TODO(数据库访问工单)：隧道 + `*_test` 方案落地后移除本机 compose 依赖。

### 真实云模型链路证据

```bash
make demo-real-model-smoke          # 读取 .env；缺凭据则明确失败
```

该目标只做一次真实 chat + 一次真实 embedding，打印 `mode/provider/model/status` 与
embedding 的模型/维度/范数，**不打印密钥**。回放运行不能替代这条证据。

## 设计与决策

- 需求与验收：[计划书 v1.1](docs/plan/bank-customer-service-plan.md)、[系统契约](docs/plan/bank-customer-service-contracts.md)、[执行交接](docs/plan/bank-customer-service-execution.md)、[现状分析](docs/plan/bank-customer-service-assessment.md)
- 架构决策：[ADR-0001 仓库布局](docs/adr/0001-repository-layout.md)、[ADR-0002 数据库与索引](docs/adr/0002-database-and-index.md)、[ADR-0003 双机同步](docs/adr/0003-two-machine-sync.md)、[ADR-0004 模型与微调](docs/adr/0004-model-and-tuning.md)、[ADR-0005 embedding 运行时与索引版本化](docs/adr/0005-embedding-runtime-and-index-versioning.md)、[ADR-0006 PostgreSQL 只部署在 Ubuntu（取代 ADR-0002 部署部分）](docs/adr/0006-postgres-on-ubuntu-only.md)
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
