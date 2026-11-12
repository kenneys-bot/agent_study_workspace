# M7 监控、日志与安全测试报告（T30 / #31）

日期：2026-11-12（Asia/Shanghai）｜状态：READY_FOR_ACCEPTANCE
范围：M7-02 观测/脱敏日志、M7-03 认证/越权/审核分离/输入上限/限流/提示注入/恶意文档、M7-04 并发/模型限流与超时/数据库不可用/worker 中断/重复提交。

## 1. 代码与部署身份

| 项 | 值 |
|---|---|
| 实现提交 | `f27c2165e29db35e198b5ccfd22d1e08aa9ea0d6` |
| 合并提交（服务器实际运行） | `59a586976093b2e9359fec97baec6d2f1ee4d6ad`（merge `feat/bank-assistant`） |
| 部署镜像 | `bank-assistant:api-59a5869`（image id `sha256:1c7dc47e…`；两端 image id 一致） |
| 服务器 | `my-ubuntu-server`，容器 `bank-assistant-api`，仅 `127.0.0.1:8000` |
| 版本页 | `code_sha=59a5869…`，`git_branch=ticket/T30`，`mode=live` |

本报告与手册只记录非秘密信息；数据库连接串、模型 key、令牌均未写入仓库。

## 2. 变更文件

新增：

- `src/bank_assistant/observability/{__init__,metrics,http,redaction}.py`：轻量 Prometheus 指标、请求观测中间件、日志脱敏；
- `src/bank_assistant/security/{rate_limit,input_limits}.py`：令牌桶限流、请求体/会话/提示词上限；
- `tests/test_observability.py`、`tests/test_rate_limit.py`、`tests/test_security_m7.py`、`tests/integration/test_security_m7.py`。

修改（要点）：

- `main.py`：`/metrics`、中间件顺序（TraceId → 观测 → 大小上限）、ready 指标；
- `api/errors.py`：数据库连接级异常 → 503（不泄露 SQL/连接串）；
- `api/routers/{auth,suggestions,inspections,reports,reviews}.py`：登录/昂贵端点限流，复核/导出角色分离；
- `api/routers/{knowledge_base,quality_inspector,script_recommender}.py`：旧版 M1 原型端点补认证；
- `core/model_gateway.py`：记录模型 outcome/token/耗时指标，结果增加 usage 字段；
- `infra/task_queue.py`：claim/reclaim/success/retry/fail/renew/lease_lost/sweep 事件指标；
- `utils/logger.py`：root logger 安装脱敏过滤器；
- `docs/ops/runbook.md`：新增 §10 指标、告警阈值、日志检查、限流/上限配置与恢复边界。

## 3. 执行结果

### 3.1 自动化测试（真实执行）

| 命令 | 结果 |
|---|---|
| `uv sync --frozen` | 120 packages，无变更 |
| `uv run pytest tests -m "not integration"` | **650 passed**，138 deselected，0 failed |
| `make test-integration`（经 SSH 隧道，`bank_assistant_test`，alembic head `0011_report_exports`） | **138 passed**，0 failed |

新增/覆盖的关键用例：

| 契约/任务 | 用例 |
|---|---|
| M7-02 指标 | 计数/仪表/直方图文本格式与标签转义；`/metrics` 含 HTTP 计数/延迟、DB readiness、队列深度；DB 不可达时抓取仍 200 且 `bank_db_ready=0`；模型 outcome/token 指标；`METRICS_TOKEN` 可选鉴权 |
| M7-02 日志 | 访问日志为单行 JSON、带 `trace_id`；不记录请求体/查询串/Authorization；连接串密码、`sk-…`、Bearer/JWT、`password=…` 脱敏 |
| M7-03 认证/越权 | 契约 §5 全部业务端点未认证 401；旧原型端点补认证后同样 401；跨范围会话/任务 404 且不泄露正文；声明他人 scope 403 |
| M7-03 审核/导出 | agent 创建复核/提交决定/导出报告 403；reviewer 通过门禁后进入服务层（资源不存在 404） |
| M7-03 输入上限 | 请求体超限 413 且不读 body；会话原文超限 413 不截断；问题提示词超限 422；提示词构造防御性截断带标记 |
| M7-03 限流 | 令牌桶容量/补充/Retry-After；`/auth/login` 按 IP 429 + Retry-After；`/suggestions` 路由级依赖在模型初始化前 429 |
| M7-03 注入/恶意文档 | 注入文本只出现在 user 数据区，system 提示词不变、无 tool 角色；控制字符/代码围栏/超长知识分块坐标自洽且不崩溃 |
| M7-04 依赖故障 | DB OperationalError → 503 `DEPENDENCY_UNAVAILABLE`，不回传 "connection refused"/SQL；模型限流/超时映射 429/504 且保留 failed 行、不伪造答复 |
| M7-04 幂等/并发/崩溃 | 同 Idempotency-Key 同输入返回同一 task（200 + `Idempotent-Replayed`），不同输入 409；CT-07 并发复核 409、CT-11 worker 崩溃后租约恢复且只有一份最终结果由既有集成用例覆盖（`test_reviews.py` / `test_inspections.py`） |
| M7-04 指标采集 | 真实 DB 上任务 claim/success 事件计数与队列深度出现在 `/metrics`，且无用户 id/令牌 |

### 3.2 真实服务器检查（执行于部署 `api-59a5869` 后）

| 检查 | 结果 |
|---|---|
| `GET /health/live`（服务器回环） | 200 `{"status":"alive"}` |
| `GET /health/ready` | 200，`checks.database=ok`，含 trace_id |
| `GET /metrics` | 200，8081 bytes；含 `bank_db_ready 1`、`bank_http_requests_total`、`bank_http_request_duration_seconds_count`、`bank_task_queue_depth{status=queued/running/succeeded/failed/cancelled}`、`bank_model_calls_total`/`bank_task_events_total` 指标族 |
| 端口暴露 | `ss -tln` 仅 `127.0.0.1:8000`；Mac 直连 `192.168.1.27:8000/metrics` 超时/不可达（/metrics 为内部端点） |
| 日志 trace_id | `docker logs --since 2h bank-assistant-api` 中 `trace_id` 行数 10（访问日志 JSON 可读） |
| 日志秘密扫描 | 同一日志窗口内 `sk-…`/带密码连接串/`Bearer …`/`DEEPSEEK_API_KEY=…` 匹配计数 **0**（只输出计数，未打印密钥） |
| 日志会话原文 | 演示会话关键词（如“肯定免息”“七个工作日”）计数 **0** |
| 进程恢复 | 仅 `docker restart bank-assistant-api`；重启后 live/ready 均 200、health `healthy`、restart policy `unless-stopped`；未重启服务器、未触碰数据库/dingtalk 容器 |

### 3.3 已执行 vs 未执行

| 项 | 状态 | 说明 |
|---|---|---|
| 单元/集成安全与观测测试 | 已执行 | 650 + 138 passed |
| 真实部署后的 live/ready/metrics/日志检查 | 已执行 | 见 §3.2 |
| API 容器重启恢复 | 已执行 | 只重启 `bank-assistant-api` |
| 真实模型供应商限流/超时 | 未执行（真实） | 不在真实服务器上人为打断付费供应商；由假网关端到端测试证明 429/504 与无假成功 |
| 真实数据库停机 | 未执行 | 共享 PG 上人为停库会影响 dev/demo/其他任务；由单元测试与 `OperationalError→503` 映射证明，隧道/DB 异常语义见 ADR-0006 |
| 24 小时长时间运行探测 | 未执行 | 计划第 7 节门槛；需单独定时探测窗口 |
| 多进程/多实例限流 | 未实现 | 当前单 uvicorn worker 内存令牌桶；横向扩展需换 Redis/网关门限流（手册已注明） |
| 真实 10 并发 / 10 分钟 P95 压测 | 未执行 | 本票只建指标与安全语义；容量报告属于 M7-06/后续 |

## 4. 验收对照（#31）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 日志不含敏感原文与密钥 | **PASS** | 访问日志不记录 body/查询串/Authorization；脱敏过滤器单测；真实容器日志秘密匹配 0、会话关键词 0 |
| 提示注入不改变系统约束 | **PASS** | `test_suggestion_injection_stays_in_user_data...`、`test_inspection_injection...`；注入只在 user 数据区、无 tool 角色；既有 `test_inspections_unit.py` CT-13 用例 |
| 超限返回 429 | **PASS** | 令牌桶单测 + login 429/Retry-After + `/suggestions` 429；上限类错误按语义返回 413/422 |
| 附加：指标/日志/恢复 | **PASS（本票范围）** | 650 unit + 138 integration；真实 `/metrics`、trace_id 日志、API 重启恢复 |

## 5. 风险与后续

1. `/metrics` 的访问控制依赖回环绑定；跨机抓取前必须配置 `METRICS_TOKEN` 或走 SSH，不能直接映射端口。
2. 限流是进程内实现，重启清零、单 worker 有效；多实例前必须换成共享存储，否则限流可被水平扩展绕过。
3. 真实供应商限流/超时与真实 DB 停机未在服务器上人为演练；已有假网关/异常映射覆盖，但真实故障演练应在获准的维护窗口单独执行。
4. 旧版 M1 原型端点已补认证但未纳入契约路由的限流/审计；后续迁移时应下线或迁入正式路由。
5. 未执行 24h 探测与并发 P95 压测；这是生产就绪门槛的剩余缺口，不能用本票的确定性测试替代。
