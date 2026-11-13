# M7 验收报告：生产就绪验证与发布包（v0.1.0 / T29+T30+T31）

日期：2026-11-12（Asia/Shanghai）｜状态：**READY_FOR_ACCEPTANCE（交付完整，但计划 §7
工程门槛未全部达成；不得标 ACCEPTED）**
发布代码 SHA：`59a586976093b2e9359fec97baec6d2f1ee4d6ad`（镜像 `bank-assistant:api-59a5869`）
发布记录：[v0.1.0](../releases/v0.1.0.md)｜机器可读清单：[release.json](../../release.json)
相关需求：FR-10；计划书 M7-01～M7-06、§7、§8

> 本报告同时是 T31（#32）的交付报告。**M7 交付完整 ≠ 工程门槛全部通过**：Recall@5、
> 引用支持度、24h 长跑与并发压测仍未达标/未执行；本报告逐项写清 PASS / FAIL / NOT_RUN。

## 1. 本轮交付（T31 / #32）

| 交付物 | 文件 |
|---|---|
| 不可变发布记录 | [docs/releases/v0.1.0.md](../releases/v0.1.0.md) |
| 机器可读发布清单 | [release.json](../../release.json) |
| 依赖漏洞处理记录 | [docs/ops/dependency-audit.md](../ops/dependency-audit.md) |
| 风险清单 | [docs/ops/risk-register.md](../ops/risk-register.md) |
| 运维手册整合 | [docs/ops/runbook.md](../ops/runbook.md)（发布校验、事故第一响应、worker、命令索引） |
| 发布校验入口 | `make release-verify`（`scripts/release_verify.sh`） |
| 状态与 README | `docs/implementation-status.md`、`README.md`（Release v0.1.0 段） |

## 2. #32 验收对照

| 验收项 | 结果 | 证据 |
|---|---|---|
| 版本可追溯到 SHA 与镜像 | **PASS** | `release.json` 记录 code SHA `59a5869…`、image tag/id/digest、传输 tar sha256、Alembic head、索引版本/校准 sha、embedding 与生成模型；`make release-verify` 对照线上实例全部一致（本轮实跑） |
| 手册可照做执行 | **PASS（复用实测 + 命令核对）** | 手册 §12 逐条核对 `make` 目标均存在；部署/索引/备份/恢复/回滚命令已在 T29/T30 实际执行；本轮实跑 `make release-verify` 与只读监控/日志/版本页命令。未重新执行会改变线上状态的 deploy/backup/rollback（避免无意义重复） |
| 风险清单记录未解决项 | **PASS** | `docs/ops/risk-register.md`：18 项（High 7 / Medium 8 / Low 3），每项含来源、影响、缓解、负责人；含 Recall 未达标、引用支持度未测、微调 NOT_RUN、e2e 过渡 DB、metrics 回环、单进程限流、凭据轮换、Chroma 公告、无 worker 编排等 |
| 依赖漏洞处理记录 | **PASS** | 实际运行 `pip-audit 2.10.1`（111 个运行时依赖）与 `npm audit`（prod 0、全部 0）；Python 侧 `chromadb 1.5.9` 有 4 个唯一公告且无修复版本，已如实记录，不写“无漏洞” |
| 发布清单不提交大二进制 | **PASS** | 只提交 JSON/文档；镜像与 `web/dist` 不入库 |

## 3. M7-01～M7-06 逐项

| 任务 | 结果 | 说明 |
|---|---|---|
| M7-01 锁定提交/镜像部署、配置与数据分离、进程自动恢复、readiness、迁移前备份、固定推理/embedding/索引版本 | **PASS** | T29：不可变镜像 `api-59a5869`、服务器 checkout 同 SHA、仓库外 `.env.app`（600）、`restart=unless-stopped`、`/health/ready` 真实 DB 探测、迁移前备份、索引/模型身份可查询；本轮 `make release-verify` 复核 |
| M7-02 trace_id、脱敏日志、错误率/延迟指标、依赖故障告警 | **PASS（指标/日志）／NOT_RUN（告警投递与看板）** | T30：`/metrics` 真实序列、单行 JSON 带 trace_id、秘密/会话原文匹配 0；**无** Prometheus/Grafana/Alertmanager 接入，阈值只在手册 §10.2，属 R-09 |
| M7-03 认证、越权、审核分离、导出权限、输入上限、限流、提示注入、恶意文档 | **PASS（自动化）／NOT_RUN（真实供应商故障）** | T30：650 unit + 138 integration；跨范围 404、403、401、413/422、429、注入只在 user 数据区；真实供应商 429/超时未在服务器人为演练（R-04） |
| M7-04 并发、模型限流/断网、数据库重启、worker 中断、重复提交 | **PASS（自动化）／NOT_RUN（真实故障与压测）** | 幂等 200/409、CT-07 并发复核 409、CT-11 租约恢复只有一份最终结果；真实 DB 停机、供应商断网、10 并发/10 分钟压测未执行（R-04/R-05） |
| M7-05 备份恢复、应用回滚、索引重建 | **PASS** | T29：两库恢复到 `*_restore_check`，21 张表行数/关键哈希/alembic 一致；`api-1fb91b6 ↔ api-f4f1346` 回滚再恢复；服务器用已部署镜像重建 `idx-20261112T110703Z-2334d03e` |
| M7-06 发布包、部署/运维/应急手册、依赖漏洞记录、风险清单、保留 SSO 接入位 | **PASS** | 本轮 T31 交付；SSO 未实现（本项目无组织方），保留为未来业务方集成项，不声称已具备 |

## 4. 计划 §7 工程门槛（不粉饰）

| 门槛 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 必须通过的功能案例 | 100% | 单元 650 passed / 集成 138 passed（T30；本轮复跑单元见 §6） | PASS |
| 检索 Recall@5 | ≥ 0.85 | replay 全量核心诉求 **0.833**（complaint 0.600） | **FAIL** |
| 无答案处理 | ≥ 0.90 | 仅 5 条子集（core 5/5） | **NOT_RUN（分母不足）** |
| 引用合法性 | 100% | 100%（构造性保证 + 断言） | PASS |
| 引用支持度 | ≥ 0.95（人工 ≥50） | `NOT_MEASURED` | **NOT_RUN** |
| 质检 macro-F1 / 高风险 Recall | ≥0.80 / ≥0.95 | 仅 20 条 live（LLM 0.333/0.286），replay 1.0 不具质量含义 | **NOT_RUN（样本不足）** |
| 证据定位 | 精确 100% + 人工相关性 ≥0.95 | 精确 100%；人工相关性未标注 | 部分 PASS / 人工 **NOT_RUN** |
| 普通 API P95 / 错误率 | ≤500ms / <1%（10 并发 10 分钟） | 未压测 | **NOT_RUN** |
| 候选答复 P95 | ≤15s | live 20 条 p95 1.66s | PASS（小样本） |
| 质检任务接收/完成 P95 | ≤1s / ≤60s（5 并发） | 未压测 | **NOT_RUN** |
| 可恢复性 | RPO≤24h、RTO≤2h | 恢复一致已验证；耗时未计时、无独立故障域 | 部分 PASS / RTO **NOT_RUN** |
| 24 小时长跑 | 无未解释崩溃 | 未执行 | **NOT_RUN** |
| 高危访问控制/数据泄露缺陷 | 0 | 应用层已实现路径测试通过；凭据轮换（R-03）与 Chroma 依赖公告（R-06）仍为 High | 应用层 PASS；**整体不能称“零高危”** |

## 5. 里程碑状态表（M0–M7）

状态枚举按计划书 §8：`NOT_STARTED / IN_PROGRESS / BLOCKED / READY_FOR_ACCEPTANCE / ACCEPTED`。
**没有任何里程碑被标为 ACCEPTED**——负责人尚未逐项签收；执行模型不代填。

| 里程碑 | 状态 | 报告 | 关键未决 |
|---|---|---|---|
| M0 基线与双机环境 | READY_FOR_ACCEPTANCE | [M0](M0-report.md) | 凭据轮换（R-03） |
| M1 修通后端 | READY_FOR_ACCEPTANCE | [M1](M1-report.md) | — |
| M2 存储与权限 | READY_FOR_ACCEPTANCE | [M2](M2-report.md) | 历史报告保留 ADR-0002 注记（已加） |
| M3 坐席辅助 | READY_FOR_ACCEPTANCE | [M3](M3-report.md) | 索引指针单机（R-11） |
| M4 质检闭环 | READY_FOR_ACCEPTANCE | [M4](M4-report.md) | 报告导出本地文件（R-11） |
| M5 工作台与演示 | READY_FOR_ACCEPTANCE | [M5](M5-report.md) | e2e 仍用本机 compose DB（R-08）；前端未部署（R-17） |
| M6 数据评测与模型 | READY_FOR_ACCEPTANCE | [M6](M6-report.md)、[M6 微调](M6-tuning-report.md) | Recall 0.833 未达标（R-01）、引用支持度未测（R-02）、微调 NOT_RUN（R-15） |
| M7 生产就绪 | **READY_FOR_ACCEPTANCE（交付完整；§7 门槛未全部达成）** | 本报告、[M7 部署](M7-deploy-report.md)、[M7 观测](M7-observability-report.md) | 真实故障演练（R-04）、24h/压测（R-05）、依赖公告（R-06）、无 worker 编排（R-07） |

## 6. 本轮实际执行与结果（T31）

| 命令 | 结果 |
|---|---|
| `uv sync --frozen` | 成功（无依赖变更） |
| `uv run pytest tests -q` | **650 passed, 138 deselected**（默认排除 integration） |
| `make release-verify` | **PASS**：image id/标签、容器 healthy、Alembic `0011_report_exports`、live/ready 200、`code_sha/index_version/calibration/embedding/model` 全部与 `release.json` 一致 |
| `pip-audit`（`uv.lock` 运行时 111 个依赖） | 1 个包有 5 条公告记录 / 4 个唯一 advisory：`chromadb 1.5.9`，无修复版本 |
| `npm audit --omit=dev` / `npm audit` | 0 漏洞 / 0 漏洞 |
| `make test-integration`（经 SSH 隧道，`bank_assistant_test`，alembic `0011_report_exports`） | **138 passed**（94.93s，0 failed） |

## 7. 可以声称 / 不能声称

**可以声称（有证据）**：

- 完成了**个人展示项目**的本地部署与可复现演示，具备真实 DeepSeek 调用与自托管 BGE 索引；
- 发布可追溯到 git SHA + 不可变镜像 id/digest + 传输校验 + Alembic head + 索引校准哈希，
  并能用 `make release-verify` 对线上实例复核；
- 做过部署、备份/恢复到独立库、回滚、索引重建与安全/可观测性测试；
- 有固定数据集与评测 harness，如实报告了未达门槛与 NOT_RUN 项。

**不能声称**：

- 不能声称「生产上线」「生产就绪验收通过」「服务多少坐席」「效率提升」；
- 不能声称检索 Recall@5 达标、引用支持度达标、质检 macro-F1/高风险 Recall 达标；
- 不能声称已做真实故障演练、24h 长跑、10 并发容量验证、RTO 实测；
- 不能声称「无漏洞」「零高危」：ChromaDB 有未修复公告，凭据轮换未做；
- 不能声称已接入银行系统、真实工单、SSO/组织身份或真实业务数据；
- 不能把 replay 分数当模型质量，也不能把本次发布当任何机构的部署许可。

## 8. 剩余 NOT_RUN / 后续

1. Ubuntu 复跑 M6 评测（R-01/R-02 的改进验证）；
2. 真实供应商 429/超时与 DB 停机演练、24h 探测、10 并发压测（R-04/R-05）；
3. ChromaDB 上游修复后升级并重建索引；镜像 OS 层扫描（R-06）；
4. 把 worker 纳入 compose/systemd（R-07）；
5. e2e 切到隧道 + `*_test`（R-08）；告警通道与看板（R-09）；
6. 凭据轮换（R-03）；备份异机副本与 RTO 计时（R-13）。

## 9. 负责人验收

负责人已确认验收：**2026-11-13 已签收（对话中明确「已签收」）**。执行模型仅记录该事实，未代填。

> 验收记录续：随后负责人授权继续推进，并要求使用已释放的 GPU 资源；复盘结论以各报告正文为准。

