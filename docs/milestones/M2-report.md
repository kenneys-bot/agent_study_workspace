# M2 验收报告：业务事实存储、权限与版本

> 历史注记（2026-11-12 追加）：本报告成文时数据库方案是「Mac 本地 Docker PG + 两机同版本」（ADR-0002）。该部署方式已被 [ADR-0006](../adr/0006-postgres-on-ubuntu-only.md) 取代：PostgreSQL 仅部署在 Ubuntu，Mac 经 SSH 隧道访问。报告内容保留原样作为历史证据，不回填改写。

状态：READY_FOR_ACCEPTANCE
实现代码SHA：`ee1cc48`（分支 `feat/bank-assistant`）
本机/Ubuntu一致性：本机 `ee1cc48`；Ubuntu 在 M3 收尾时同步（M1 已证明 144 用例双端一致）
相关需求：FR-01、FR-02、FR-03、FR-10；计划书 M2

## 交付内容与文件

| 提交 | 工单 | 内容 |
|---|---|---|
| `79a388c` | #11 T10 | users 表、Argon2 密码、JWT、`POST /auth/login`、`GET /me`、`require_scope/require_role` |
| `89e51f7` | #13 T12 | conversations/turns/audit_events、轮次不可变、脱敏展示、append-only 审计带 trace_id |
| `cfba558` | #14 T13 | tasks/idempotency_keys、`FOR UPDATE SKIP LOCKED` 租约、`Idempotency-Key` 语义 |
| `239a793` | #12 T11 | knowledge documents/versions、生命周期、发布者分离、SQL 层可检索查询 |
| `55554ee9`、`cf1edc53` | — | 两条 Alembic merge revision，收口并行分叉为单 head |

迁移链（单 head）：

```text
0001_schema_meta → 0002_users → 0003_knowledge ─┐
                              → 0004_conversations ─┼→ 55554ee9 → cf1edc53 (head)
                              → 0005_tasks ────────┘
```

## 验收逐条结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| 空库可迁移 | PASS | `make test-integration`：空库升级到 `cf1edc53`（head），真实 PG18 |
| 重启后记录存在 | PASS | 集成测试用新 engine/session 读取会话与审计记录 |
| 跨用户访问受控 | PASS | 跨 scope 403；不可见资源 404 且与不存在资源同码同文案 |
| 越权不泄露存在性 | PASS | 集成断言 foreign vs missing 完全一致的错误体 |
| 草稿不进入检索 | PASS | SQL 层过滤：status=published + 有效期 + 业务范围/scope |
| 同一幂等请求不生成重复业务记录 | PASS | 同键同输入返回原任务（`Idempotent-Replayed: true`）；不同输入 409 |
| PostgreSQL 并发测试不丢失更新 | PASS | 双 worker 用 `SKIP LOCKED` 抽干任务，各自 attempts=1；过期租约重领、旧 owner 写入被拒 |
| 迁移升级在测试库验证 | PASS | 每次 `make test-integration` 都从空库升级；downgrade/upgrade 往返验证（T13） |
| 密码非明文存储 | PASS | 读行断言 `$argon2` 前缀，明文不存在 |

## 指标与样本量

- 单元测试：**215 passed / 0 failed**（53 integration 默认排除）。
- 集成测试：**53 passed**（真实 PostgreSQL 18 容器，每次空库升级）。
- 迁移 head：单一 `cf1edc530a0c`。

## 失败、跳过、环境限制

- 备份恢复演练属 M7（`make backup/restore-check` 仍未实现，按设计非零退出）。
- 限流属 M7。
- 语音转写不在首版范围。

## 数据/模型/知识/规则/索引版本

M2 只建表与状态机，未导入演示知识、未建索引（M3 的 #15）。无真实银行资料。

## 兼容性与迁移

- 旧接口未包裹认证（契约要求新业务接口 `/api/v1` 走权限）；后续新路由一律使用 `Depends(require_scope/require_role)`。
- 并行迁移分支已用 merge revision 收口，禁止新增未收口的分叉 head。

## 遗留风险和外部依赖

1. `scripts/test-integration.sh` 已按 PID 隔离项目名/端口，但多个 worktree 同时跑仍会占用更多容器与端口（已可并行）。
2. 迁移断言中「某票是唯一 head」的过严写法已在两处修正为「单 head + 本票可达」，后续 ticket 沿用后者。
3. Ubuntu 完整集成测试（含 Docker）未在服务器上跑过；M5 验收前补一次。

## 下一阶段入口

M3：T14（演示知识与版本化索引，#15）→ T15（候选答复端到端，#16）→ T16（采纳反馈，#17）。

## 负责人验收

待负责人填写结果与时间；执行模型不代填。
