# M1 验收报告：修复后端并消除伪成功

状态：READY_FOR_ACCEPTANCE
实现代码SHA：`7ca525a`（分支 `feat/bank-assistant`，含 T03/T04/T05/T06/T07/T08/T09/T32）
本机/Ubuntu一致性：本机 `7ca525a`；Ubuntu 需在 M1 收尾同步（见「遗留」）
相关需求：FR-03、FR-04、FR-05、FR-10；计划书 M1

## 交付内容与文件

| 提交 | 工单 | 内容 |
|---|---|---|
| `13d6b6f` | #4 T03 | 会话导入闭环：解析器三处缺陷、`ParseConversationResponse`、稳定 4xx |
| `8dadd9c` | #5 T04 | 检索闭环：扁平查询、count 裁剪、重排失败显式、filters 透传、确定性本地 reranker |
| `02ef34b` | #6 T05 | 模型适配层：system 消息保留、schema 校验、失败分类、移除随机 embedding/隐式 mock、显式 replay |
| `d77cf2a` | #7 T06 | 质检报告与复核停止伪造：501 NOT_IMPLEMENTED、删除实例字典工作流、统一错误结构 |
| `a65109e` | #8 T07 | 容器：Dockerfile.api（uv 多阶段、非 root）、compose、smoke 脚本与 Makefile 目标 |
| `4371394` | #10 T09 | PostgreSQL 18 + Alembic 基线迁移 + readiness 真实探测 |
| `7aa4461` | #33 T32 | 知识整理/话术生成：意图常量引用、`NameError`、数量/语气参数、相似问过滤 |
| `3e179ee` | #9 T08 | 错误码契约、trace_id、truncate 契约、启动配置不再要求未使用凭据 |

## 验收逐条结果

| 验收项 | 结果 | 执行命令 / 证据 |
|---|---|---|
| 干净环境 `make test-unit` 全部通过 | PASS | `uv run pytest tests -q` → **144 passed, 0 failed, 1 deselected**（integration 默认排除） |
| `make build` 成功 | PASS | `uv sync --locked` exit 0 |
| `make smoke` 覆盖启动/正常/失败路径 | PASS | live 200 `alive`；ready 503（PG 不可用，含 code/trace_id）；未知路由 404；容器 healthcheck healthy；端口占用时 exit 1 |
| 容器可构建且真实启动 API | PASS | `docker build -f deploy/docker/Dockerfile.api` 成功（978 MB）；`uid=10001(app)`；`uvicorn bank_assistant.main:app` |
| 候选数不超过 count | PASS | stub 返回 6、请求 3 → 返回 3（#5 回归测试） |
| system 消息传给适配器 | PASS | MockTransport 捕获 payload 断言（#6） |
| 解析样例不丢轮次 | PASS | 中文+emoji+换行用例，ordinal `[1,2]`、原文保留（#4） |
| 模型不可用时不返回合格结论 | PASS | `length`+空 content→`empty_content`；timeout→504；无凭据→能力不可用；质检导出→501（#6/#7/#8） |
| 不返回伪造报告/固定分数 | PASS | 导出/复核/审批均 501，无 85/90/85/95、无 2024-01-01（#7） |
| 空库可迁移（M2 前置） | PASS | `make test-integration`：PG18 healthy、`alembic upgrade` 到 `0001_schema_meta`、1 passed（#10） |
| 错误码说明 | PASS | 统一结构 + 400/401/403/404/409/429/502/503/504 映射（#9） |

## 指标与样本量

- 单元测试：144 passed / 0 failed / 1 deselected（基线为「6 passed / 14 failed 缺依赖」）。
- 集成测试：1 passed（PG 迁移可达性），`down -v` 清理干净。
- 容器：978 MB，非 root，健康检查健康。
- 真实模型链路冒烟（2026-11-12）：`ModelGateway.from_settings()` → `mode=live`、`primary=deepseek`、`status=succeeded`、`model=deepseek-chat`，返回真实中文回答（未写入凭据）。

## 失败、跳过、环境限制

- 未跑真实模型评测（属 M6）；M1 只验证链路可调用与失败分类。
- 沙箱内 `docker build` 需要 `DOCKER_CONFIG` 指向仓库内目录并复制 cli-plugins；普通 shell 不需要（已记入环境文档待补）。
- 前端未开始（M5）。

## 数据/模型/知识/规则/索引版本

无知识、无索引。模型：DeepSeek 官方 `deepseek-chat`（ADR-0004）。

## 兼容性与迁移

- 包名 `app` → `bank_assistant`；导入路径、`uvicorn` 目标、容器 CMD 已同步。
- 两份历史 `requirements.txt` 已合并进 `pyproject.toml` 后删除。
- 新增 Alembic 基线 `0001_schema_meta`；M2 起按业务实体递增。

## 遗留风险和外部依赖

1. Ubuntu 检出点已同步到 `2c5e0d7`（与本机一致、工作树干净，`git reset --hard` + `git fetch`）；Ubuntu 上的 `uv sync` 因 pypi 约 9 s 较慢，单元测试在 M5 验收前于 Ubuntu 复跑一次并记录。
2. 语音转写仍未实现（首版不含，计划明确排除）。
3. `git worktree` 并行开发产生 9 个 worktree，收尾时清理。

## 下一阶段入口

M2：T10（认证，进行中）→ T11（知识生命周期）/T12（会话持久化与审计）/T13（幂等与任务表）。

## 负责人验收

待负责人填写结果与时间；执行模型不代填。
