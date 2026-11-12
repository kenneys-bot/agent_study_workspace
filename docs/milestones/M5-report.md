# M5 报告：本地工作台与演示端到端（T25 / #26）

日期：2026-11-12（Asia/Shanghai）  
分支：`ticket/T25`（基于 `feat/bank-assistant`，并合入 T21 报告导出）  
范围：M5-04「演示启动/停止/种子导入命令与 8–10 分钟演示脚本；重置数据命令只允许显式
demo 环境、再次指定目标，不接触生产库」以及 M5 验收中的浏览器端到端与真实模型链路证据。

## 1. 验收对照

| #26 验收项 | 结果 | 证据 |
|---|---|---|
| 干净 checkout 按 README 可启动 | PASS | README「演示与浏览器端到端（M5）」；`make seed-demo` + `make index-rebuild` + `make up` + `make web-dev`；e2e 用同一命令链的隔离变体 |
| 三个业务故事可完成 | PASS | `data/demo/conversations/` 六段合成会话；Playwright 覆盖坐席工作台、质检、复核、导出 |
| 至少一条真实云模型链路成功 | PASS | `make demo-real-model-smoke`：DeepSeek chat `succeeded` + 真实 fastembed embedding 512 维；见 §4 |
| 回放演示单独标识 | PASS | `MODEL_MODE=replay` 时 `suggestion.mode`、`model_runs.mode`、系统版本页均标记 `replay`；本报告与演示脚本不把回放当模型质量证据 |

## 2. 交付物

- `data/demo/conversations/*.json`：S1 挂失 / S2 费用 / S3 投诉，各 normal + risk 一段；
  全部合成、无真实客户数据，正文刻意不含数字（避免号码形态）。
- `make seed-demo` 扩展：账号 + 32 条知识 + 6 条规则 + 6 段会话；`create_inspection`
  的 3 段风险会话由**真实 worker + 显式回放**生成 3 个待复核任务（不是伪造 DB 行）。
  新增 `make seed-demo-conversations` 只导入会话与质检任务。
- `make demo-reset DB=bank_assistant_demo CONFIRM=yes`：安全闸门在
  `src/bank_assistant/demo/reset.py`；只允许精确的 `bank_assistant_demo`，拒绝
  dev/test/restore 与 staging/production；`DRY_RUN=1` 只打印计划。
- `docs/demo-script.md`：8–10 分钟逐步脚本，标注 live/replay、故障分支与真实链路证据。
- `web/e2e/demo-flow.spec.ts` + `web/playwright.config.ts` + `make test-e2e`：
  一次性 compose PostgreSQL + 隔离后端 + 前端 preview + Playwright。
- `make demo-real-model-smoke` + `scripts/demo_real_model_smoke.py`。
- 复核页新增「报告导出」按钮（JSON/HTML），展示 `export_id`、revision 与内容哈希；
  依赖 T21 的 `POST/GET /api/v1/reports`。

## 3. 浏览器端到端

执行：

```bash
cd web && npx playwright install chromium
cd .. && make test-e2e
```

`make test-e2e` 的实际步骤：

1. `docker compose -p bank-assistant-e2e-<pid> -f deploy/compose.db.yml up -d --wait`，
   库名 `bank_assistant_demo`，随机回环端口与独立卷；
2. `alembic upgrade head` + `seed_demo_accounts` + `seed_demo` + `seed_demo_rules` +
   `seed_demo_conversations` + `index_rebuild`（fastembed，本地模型）；
3. `uvicorn`（`MODEL_MODE=replay`，`INDEX_DIR=.e2e/index`）+ `vite preview`；
4. Playwright：登录 → 坐席工作台导入 S3 风险会话 → 回放生成候选答复 → 打开引用原文 →
   采纳；随后通过**真实 API 创建质检任务 + 真实 worker `--once`**，再创建复核；
   浏览器确认 `DEMO-CMP-01` 真命中、驳回 `DEMO-ATT-01` 误报、完成复核、导出 JSON 并
   校验报告中的 `inspection_id/review_revision/机器判断+人工结论`；
5. trap 清理 API/前端进程与 compose 项目（`down -v`）。

结果：`1 passed (4.3s)`。截图与下载产物：

- `web/e2e/screenshots/01-logged-in.png`
- `web/e2e/screenshots/02-console-suggestion.png`
- `web/e2e/screenshots/03-review-decisions.png`
- `web/e2e/screenshots/04-review-completed.png`
- `web/e2e/screenshots/05-report-exported.png`
- `web/e2e/screenshots/report-export.json`

截图目录与 `.e2e/` 均已 gitignore；不提交运行产物。

### ADR-0006 过渡做法（必须记录）

- 正式目标：Mac 经 SSH 隧道访问 Ubuntu 的 `bank_assistant_test` 库；
- 当前 T25 过渡：`make test-e2e` 使用**本机 compose PostgreSQL**，独立项目名/端口/卷，
  库名固定 `bank_assistant_demo`；**不接触** `bank_assistant_dev` / `bank_assistant_test` /
  `bank_assistant_restore_check`；
- 这是为了先打通浏览器 e2e 的临时做法，不代表 ADR-0006 已落地；
- **TODO（数据库访问工单）**：隧道 + `*_test` 方案落地后，移除
  `scripts/test-e2e.sh` 对本机 compose 的依赖，并改用 `bank_assistant_test`；
  届时同步更新 README 与演示脚本。

这一限制不影响 e2e 的真实链路性质：数据库、HTTP API、worker、Playwright、报告产物都是
真实执行；只有模型调用是显式回放，且已在结果/报告中标明。

## 4. 真实模型链路证据

执行（使用仓库外 `.env`，不打印密钥）：

```bash
ENV_FILE=../../.env make demo-real-model-smoke
```

输出摘要（2026-11-12）：

```text
demo-real-model-smoke: 真实链路（不会回放）
  mode：live
  chat：mode=live provider=deepseek model=deepseek-chat status=succeeded attempts=1 finish_reason=stop text_len=4
  embedding：provider=local model=BAAI/bge-small-zh-v1.5@512 dim=512 norm=1.000000
  结果：通过（chat + embedding 均成功；未打印任何密钥）
```

说明：

- `provider=local` 是 `.env` 的历史别名，代码中映射到本机 `fastembed`（不是云 embedding）；
  该别名已在 `create_embedding_provider` 中显式支持；
- chat 是真实 DeepSeek 云调用；未记录请求头、密钥或完整响应正文；
- 回放 e2e 不能替代这一条证据，反之真实链路也不证明检索/质检质量（属 M6 评测范围）。

## 5. 测试结果

```text
# T25 分支（合入 feat 之前）
uv run --frozen pytest tests      → 474 passed, 130 deselected

# 合并 feat/bank-assistant（含 T26 数据集）后复验
uv run --frozen pytest tests      → 499 passed, 130 deselected
cd web && npm run build           → tsc + vite 构建通过
cd web && npm run test            → 8 files / 48 tests passed
make test-e2e                     → 1 Playwright test passed
make demo-real-model-smoke        → chat + embedding 通过
```

新增单测覆盖：演示会话六段与 S1/S2/S3×normal/risk 完整性、禁止数字、回放处理器
（候选答复/质检命中/证据逐字回验/误报语境）、`MODEL_MODE=replay` 接线、
`demo-reset` 目标保护（缺确认、错误库名、dev/test/restore、staging/production、非 PG）。

## 6. 已知限制与后续

- 质检任务创建仍走 API/seed；M5 前端只提供复核页，没有新增“创建质检”页面。e2e 用真实
  API + worker 覆盖该段，浏览器覆盖复核与导出；若后续要求全 UI 操作，需要单独加页面。
- 报告导出页目前只有本轮新增的 JSON/HTML 下载按钮；没有历史导出列表（T21 只提供
  按 `export_id` 读取）。
- `bank_assistant_demo` 里的回放任务不是模型质量证据；M6 的评测需要真实模型与冻结测试集。
- ADR-0006 过渡做法见 §3；后续数据库访问工单必须替换本机 compose e2e。
- `.env` 的 `EMBEDDING_PROVIDER=local` 历史别名已兼容；新配置建议写 `fastembed`。
