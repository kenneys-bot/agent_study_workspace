# 8–10 分钟演示脚本（M5-04 / T25）

面向：内部坐席、质检员、面试/汇报观众。  
目标：在**本地工作台**上走完「登录 → 坐席辅助 → 质检任务 → 人工复核（含驳回一次误报）→ 报告导出」。
全程只使用**合成演示数据**；不连接任何银行真实系统，不产生真实工单或办理结果。

> 先读边界：候选答复不是已发送消息；风险命中不是正式违规认定；证据不足不是合格；
> 导出报告是某个 `inspection_id + review_revision` 的固定快照，后续改判不改写旧报告。

---

## 0. 演示前准备（约 2 分钟，不计入 8–10 分钟）

### 0.1 配置与演示库

```bash
cd <repo>
cp .env.example .env          # 填 DEEPSEEK_API_KEY 等；.env 不进 Git
# 演示库只允许 bank_assistant_demo；dev/test/restore 一律不碰
export DATABASE_URL='postgresql+psycopg://<user>:<password>@127.0.0.1:15432/bank_assistant_demo'
```

数据库唯一实例在 Ubuntu，Mac 先建 SSH 隧道（ADR-0006）：

```bash
ssh -N -L 15432:127.0.0.1:5432 my-ubuntu-server
```

### 0.2 重置 → 种子 → 索引 → 启动

```bash
# 预演：只打印目标，不删除任何东西
make demo-reset DB=bank_assistant_demo CONFIRM=yes DRY_RUN=1

# 真重置：只清空 bank_assistant_demo（必须显式指定目标 + 二次确认）
make demo-reset DB=bank_assistant_demo CONFIRM=yes

# 幂等导入：账号 + 32 条知识 + 6 条规则 + 六段会话 + 3 个待复核质检任务
make seed-demo
make index-rebuild

# 后端与前端
make up                       # API → http://localhost:8000
make web-dev                  # 前端 → http://localhost:5173
```

`make seed-demo` 里的质检任务由**真实 worker + 显式回放**执行（不调用云模型），
因此会立刻得到 3 个 `pending` 复核任务；控制台会打印复核 ID。
演示状态清单在 `$DATA_ROOT/demo-seed-state.json`（已 gitignore）。

演示账号（公开的 demo-only 密码，不是秘密）：

| 账号 | 角色 | 用途 |
|---|---|---|
| `demo-author` | agent | 坐席工作台 |
| `demo-reviewer` | reviewer | 质检复核 |

密码：`demo-only-correct-horse-battery`。系统版本页会显示真实 SHA、模型、索引版本和
`mode`（`live` 或 `replay`）。

### 0.3 建议的分辨率与数据

- 浏览器窗口 ≥ 1440×900；`web/e2e/screenshots/` 有 e2e 自动截图可作备份。
- 三段故事原文在 `data/demo/conversations/`；演示时直接复制 JSON 里的 `content` 粘贴，
  不要手工改数字或加入真实客户信息。

---

## 1. 时间轴总览

| 时间 | 界面/操作 | 模型调用？ | 说明 |
|---|---|---|---|
| 0:00–1:00 | 登录 → 系统版本页 | 否 | 展示真实 SHA、模型、索引、`live/replay` |
| 1:00–3:00 | S1 挂失：坐席工作台导入正常会话 → 生成候选答复 → 打开引用 → 采纳 | **live 时调用真实云模型**；replay 时不调用 | 只提供咨询，不声称已办理 |
| 3:00–4:40 | S2 费用：导入正常会话 → 生成 → 引用 → 采纳 | 同上 | 无条款支持不编造费率/减免 |
| 4:40–6:00 | S3 投诉：导入风险会话 → 生成 → 引用（不采纳也可） | 同上 | 为下一步质检准备同一段会话 |
| 6:00–6:30 | 质检任务：打开「质检复核」列表 | **seed 阶段是回放**；后台 worker 已执行 | 不伪造“实时跑模型”；回放明确标记 |
| 6:30–8:30 | 复核：打开 S3 风险任务 → 确认“阻碍投诉” → **驳回“侮辱性表达”误报** → 完成 | 否 | 机器判断只读，人工决定追加 |
| 8:30–9:30 | 报告导出：JSON（可再演示 HTML） | 否 | 固定 revision + 内容哈希 |

---

## 2. 逐步操作

### 2.1 登录与全局状态（0:00–1:00）

1. 打开 `http://localhost:5173`，自动跳到登录页。
2. 用 `demo-author` / `demo-only-correct-horse-battery` 登录。
3. 点顶部「系统版本」，确认：
   - `code_sha` 是当前提交（不是静态假数据）；
   - 模型显示 `deepseek-chat`（或你配置的模型）；
   - embedding 显示 `BAAI/bge-small-zh-v1.5`；
   - `mode` 明确显示 `live` 或 `replay`。
4. 讲一句边界：**回放会明确标记，不把回放冒充真实模型结论。**

### 2.2 S1 挂失咨询（1:00–3:00）

1. 进入「坐席工作台」。
2. 打开 `data/demo/conversations/s1-lost-card-normal.json`，把 `content` 整段粘贴到
   「会话原文」。
3. 点「导入会话」：界面出现解析后的轮次、会话 ID、数据分级 `synthetic_demo`。
4. 点「生成候选答复」：
   - **live 模式**：此步调用一次真实云模型（DeepSeek chat），产生少量费用；
   - **replay 模式**：不调用真实模型，结果标记 `mode=replay`。
5. 看到状态「已生成答复」后：
   - 读候选答复：只提供官方渠道与后续步骤；
   - 点引用里的「打开原文」，展示知识版本、来源、正文；
   - 点「采纳」→「确认采纳」，展示反馈记录。
6. 讲解：**不会声称已办理挂失**；引用必须能打开原文；反馈只是反馈，不是正确性标签。

### 2.3 S2 信用卡费用（3:00–4:40）

1. 清空「会话原文」，粘贴 `s2-fee-normal.json` 的 `content`。
2. 导入 → 生成候选答复（同上，live 时真实调用）。
3. 展示：坐席先追问卡种/账单月份，费用说明指向有效合同与公告，**不承诺减免结果**。
4. 打开一条引用，展示条款原文。
5. 可选：在业务类型下拉框选「信用卡费用/还款咨询」，强调过滤来自服务端身份与业务范围，
   不是前端随便传角色。

### 2.4 S3 投诉受理（4:40–6:00）

1. 粘贴 `s3-complaint-risk.json` 的 `content`，导入。
2. 生成候选答复（live 时真实调用；replay 时标记回放）。
3. 展示候选答复与引用；这段会话同时是下一步质检的输入。
4. 说明：**候选答复不是工单**；坐席不能据此声称已经建立银行正式工单。

### 2.5 质检任务（6:00–6:30）

1. 点顶部「质检复核」。
2. 列表里应有 3 个 `pending` 任务（S1/S2/S3 风险会话），来自 `make seed-demo` 的
   **真实 worker + 显式回放**；每条的质检 ID、复核 ID、revision 都可见。
3. 明确说：这一步的模型判断是 **replay**，不是真实云模型质量证据；真实云链路证据
   由独立命令 `make demo-real-model-smoke` 提供（见第 4 节）。
4. 选择「质检 ID」对应 S3 风险会话的那一条（`demo-seed-state.json` 里
   `reviews.s3-complaint-risk.review_id`），点「打开复核」。

### 2.6 人工复核：确认 + 驳回误报（6:30–8:30）

1. 左侧是不可变原文，右侧是机器判断与证据；证据高亮按 Unicode 码点换算。
2. `DEMO-CMP-01`「不得阻碍客户投诉」：机器命中，证据引用“不可能处理”等原文。
   - 点「确认」→ 理由填「坐席用不可能处理、投诉也没用直接阻碍投诉，证据与原文一致」→「提交结论」。
3. `DEMO-ATT-01`「不得使用明显侮辱性表达」：机器命中“你别再打了”。
   - 这是回放夹具**刻意制造的一次误报**（强硬劝阻不是明显侮辱）；
   - 点「驳回」→ 理由填「你别再打了是强硬劝阻，不构成明显侮辱性表达，属于误报」→「提交结论」。
4. 两条都结论后，点「完成复核」；状态变为「已完成」，revision 增加。
5. 讲解：
   - 机器判断只读，人工结论追加保存；
   - 每次提交携带 `expected_revision`，并发冲突显示 409 并刷新；
   - 证据不足会保留为「待业务确认」，不会自动变 clear。

### 2.7 报告导出（8:30–9:30）

1. 在复核详情页找到「报告导出」。
2. 点「导出 JSON 报告」：浏览器下载 `report-<export_id>.json`，页面显示
   `export_id`、复算的 `review_revision` 与 **内容哈希**。
3. 打开 JSON，指出：
   - `inspection` 绑定固定质检与会话哈希；
   - `review.review_revision` 是本次快照 revision；
   - `findings[]` 保留机器判断与人工最终结论；
   - 报告已经落盘，**之后再改判不会改变这份报告**。
4. 可选：点「导出 HTML 报告」，展示同一快照的静态可读版本。
5. 收尾：回到系统版本页，再次强调 SHA/模型/索引/模式都是真实查询结果。

---

## 3. 故障分支（必须演示或至少说明）

### 3.1 云模型不可用 / 超时 / 限流

- 坐席工作台不会伪造一个“合格答复”。候选答复状态为 **`failed`**，界面显示明确的
  错误与重试入口；这是系统失败，不是业务上的转人工。
- 演示兜底：设置 `MODEL_MODE=replay` 重启 API（或直接使用已配置的 replay 环境），
  结果为 `mode=replay`，必须口头标注“这是回放，不是真实模型”。
- **不要**把 `failed` 说成 `needs_human`：契约 §3 明确区分系统失败与业务状态。

### 3.2 证据不足 / 需要人工（`needs_human`）

- 与上一条相反：当知识缺失、业务条件不足或没有可校验引用时，候选答复返回
  `needs_human`（或 `needs_clarification`），并列出缺失信息；界面不展示无依据答复。
- 可现场演示：断开知识索引（`INDEX_DIR` 指向不存在目录）或用一段无关会话，
  观察 `needs_human` 分支；恢复索引后重试。
- 质检侧：模型失败会终结任务并写稳定 `error_code`，**不会**用空 findings 冒充
  “无问题”；需要人工处理时如实显示失败/重试，不把失败报告导出。

### 3.3 数据库/隧道不可达

- `make seed-demo`、`make demo-reset`、集成测试等需要真实 PostgreSQL 的命令必须
  **显式失败**，不得静默切 SQLite 或 mock 后宣称通过。
- 先恢复 SSH 隧道或使用已存在的 `bank_assistant_demo`；不要在 dev/test/restore
  库上重试。

---

## 4. 真实云模型链路证据（不计入 8–10 分钟）

```bash
# 使用 .env 里的真实凭据；缺凭据会明确失败
make demo-real-model-smoke
```

输出只包含 `mode/provider/model/status/attempts` 与 embedding 的
`model/dim/norm`，**不打印任何密钥**。示例（摘要）：

```text
chat：mode=live provider=deepseek model=deepseek-chat status=succeeded ...
embedding：provider=... model=BAAI/bge-small-zh-v1.5@512 dim=512 norm=1.000000
结果：通过（chat + embedding 均成功；未打印任何密钥）
```

完整结果与日期记录在 `docs/milestones/M5-report.md`。回放运行不能替代这条证据。

---

## 5. 演示后清理

```bash
make down                 # 停止 API 容器
# 如使用本机 compose 数据库（ADR-0006 过渡期/e2e），再 down 对应项目：
docker compose -p <project> -f deploy/compose.db.yml down -v
```

不要删除 Ubuntu 上的演示库；如需重置，重复第 0.2 节的
`make demo-reset DB=bank_assistant_demo CONFIRM=yes`，并重新 seed + index。

---

## 6. 相关文档

- 浏览器 e2e：`web/e2e/demo-flow.spec.ts`，`make test-e2e`（隔离后端+前端，replay）。
- 验收证据：`docs/milestones/M5-report.md`。
- 需求/契约：[计划书 §3.3](../plan/bank-customer-service-plan.md)、
  [系统契约 §3/§5](../plan/bank-customer-service-contracts.md)。
- 数据库决策：[ADR-0006](../adr/0006-postgres-on-ubuntu-only.md)。
