# M4 验收报告：证据质检与人工复核闭环

状态：READY_FOR_ACCEPTANCE
实现代码SHA：`feat/bank-assistant` HEAD（含 #18–#22）
本机/Ubuntu一致性：本机已验证；Ubuntu 在 M5 收尾同步
相关需求：FR-06、FR-07、FR-08；计划书 M4

## 交付内容与文件

| 提交 | 工单 | 内容 |
|---|---|---|
| `af8046b` | #18 T17 | 6 条 `demo_only` 演示规则、规则版本生命周期、发布者分离、窗口重叠显式退役 |
| `866b334` | #19 T18 | `inspection_runs`/`findings`、`POST /inspections` 202、异步 worker + 租约、重试上限 3、幂等终态、CT-11 |
| `6e149ac` | #20 T19 | 码点证据校验、错误区间拒绝、emoji/换行、否定/转述不算命中、四态判定、UTF-16 换算 |
| `c066ad6` | #21 T20 | ReviewCase/ReviewDecision、逐项 confirm/reject/modify、`expected_revision` 409、完成门禁、reopen |
| `2c23e69` | #22 T21 | ReportExport 快照绑定、`content_hash`、静态 HTML 转义、权限与审计 |

迁移链：`0009_inspections → 0010_reviews → 0011_report_exports`（单一 head）。

## 验收逐条结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| 每条规则有正例/反例/否定/转述/证据不足样例 | PASS | 6/6 规则各 1 组（`tests/test_demo_rule_samples.py`）；deterministic 11 例 |
| 错误证据区间被拒绝 | PASS | 无效区间 → `EVIDENCE_INVALID`、0 findings（unit 5 + integration 2） |
| 提交→待复核→逐项处理→完成→导出→重启查询全链 | PASS（API 级） | review/export 集成测试 + 真实 PG 重启一致性；浏览器级全链在 M5/T25 覆盖 |
| 两个复核员并发提交 → 一成功一 409 | PASS | 两条真实 PG 连接行锁；`REVISION_CONFLICT` + `current_revision`；DB 仅 1 条 decision |
| 系统故障不伪装成零分/100 分 | PASS | 非法模型输出 → `INVALID_MODEL_OUTPUT`、重试 3 次后 `failed`，findings 为空但显式失败 |
| 证据不足/不适用独立于命中与合格 | PASS | 四态端到端独立；`insufficient_evidence` 完成时需显式"待业务确认"，不转 clear |
| 报告绑定固定快照（CT-12） | PASS | revision 2 导出后再改判/完成，旧产物字节与 hash 不变；新导出哈希不同 |
| 导出 HTML 安全 | PASS | 无外链/脚本；`<script>` 转义；手机号等长 `*` 遮蔽；无凭据泄漏 |
| worker 崩溃可恢复、无重复最终结果（CT-11） | PASS | 租约过期重领 attempts=2、findings 恰 1 份、旧 worker 不能补写；上限后 `MAX_ATTEMPTS_EXCEEDED` |

## 指标与样本量

- 单元测试：**448 passed / 0 failed**（130 integration 默认排除）。
- 集成测试：**130 passed**（真实 PostgreSQL 18，空库迁移到 `0011_report_exports`）。
- 演示规则：6 条、覆盖 M4-02 六个风险类别、5 条 high、全部 `demo_only`。
- 迁移 head：单一 `0011_report_exports`。

## 失败、跳过、环境限制

- 质检判定在测试中使用假模型网关（不烧钱）；真实模型链路在 M5 演示 / M6 评测验证。
- 浏览器级全链（提交→复核→导出→重启查询）不在本里程碑，留给 M5 的 T25。
- 数据库访问方式仍是"本机 compose 容器"的过渡实现：ADR-0006 要求改为 Ubuntu 唯一实例 + SSH 隧道，脚本/配置改造在待办清单中（见 `docs/implementation-status.md`）。

## 数据/模型/知识/规则/索引版本

- 规则：6 条 `demo_only` 规则版本（`data/demo/rules/`）。
- 知识/索引：沿用 M3 的 32 条演示知识与索引版本；本里程碑未重建索引。
- 生成模型：DeepSeek 官方 `deepseek-chat`（测试不调用真实模型）。

## 兼容性与迁移

- 新增迁移 `0009`–`0011`，线性单 head；`alembic upgrade head` 在空库上验证。
- `ReportExport` 只追加：UPDATE/DELETE 显式失败。

## 遗留风险和外部依赖

1. 报告导出目前写 `ARTIFACT_ROOT` 下的本地文件路径，多实例部署时需共享存储（M7）。
2. 规则判定质量只在合成样例上验证；真实效果证据属 M6。
3. 契约中"不可见资源 404"与 issue 原文"403"的差异已按契约执行并在 issue 中说明。

## 下一阶段入口

M5 收尾：T25（演示脚本与浏览器端到端，#26）→ M5 验收 → M6 评测。

## 负责人验收

负责人已确认验收：**2026-11-13 已签收（对话中明确「已签收」）**。执行模型仅记录该事实，未代填。

> 验收记录续：随后负责人授权继续推进，并要求使用已释放的 GPU 资源；复盘结论以各报告正文为准。

