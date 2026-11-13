# 风险清单（v0.1.2 / T38+T39+T40+T41+T42 / #38/#39/#40/#41/#42）

日期：2026-11-13（Asia/Shanghai）｜范围：M0–M7 全部阶段报告中**未解决**的风险与本轮
运维核对发现的问题。已解决项（如 144 用例双端一致、ADR-0006 数据库落地、备份恢复/回滚/
索引重建已演练）不再列入；T38/T39/T40/T41 的关闭/部分关闭证据单列于 §4。

严重度定义（个人项目口径，非机构标准）：

- **High**：影响发布可信度或可能造成安全/数据后果，负责人需在对外汇报前知情；
- **Medium**：当前单机演示可用，但扩展、故障或复现时会出问题；
- **Low**：文档/复现性/质量证据的已知局限，不阻塞演示。

计数：**High 5　Medium 8　Low 3　Critical 0**（共 16 项）；其中 T38/T39 关闭或部分关闭
4 项（R-04 的 DB 停机与供应商故障映射、R-05 的 10 并发压测、R-13 的 RTO），T40 关闭
**R-19**（live 质检成功率），T41 发布 v0.1.2 关闭 **R-07**（部署无 worker）。R-19、R-07
关闭后 High 从 7 降为 5。没有已确认的应用层高危越权/数据泄露缺陷；ChromaDB 依赖
公告属依赖侧 High（当前部署不可达），见 R-06。**不能据此声称「无高危风险」**。

## 1. High

| ID | 风险 | 来源 | 影响 | 缓解 / 下一步 | 负责人 |
|---|---|---|---|---|---|
| R-01 | 检索 Recall@5 = **0.833**，低于门槛 0.85；complaint 场景 0.600 | M6 报告 | 检索质量未达计划 §7 工程门槛，不能宣称检索达标 | 先修 complaint 知识覆盖与改写/过滤，再冻结新 test 复评；不先上微调 | 负责人 |
| R-02 | 引用支持度（人工抽检 ≥50 条）**标注包已就绪、标签为空（OPEN，可操作）** | M6 报告、T42 | 只能证明引用合法（100%），不能证明关键事实被引用支持 | 包 `docs/eval/citation-support-pack.md`（67 条引用 + 9 条无证据对照）；负责人填写 `labels/citation-support.csv` 的 `label`/`note`（SUPPORTED/NOT_SUPPORTED/UNKNOWN），≥50 条后运行 `uv run python scripts/score_citation_support.py --labels labels/citation-support.csv`；标签填完前只写等待人工标注，不宣称达标 | **负责人（user）** |
| R-03 | 凭据曾在执行会话中出现；项目跑通后**未执行轮换** | M0 报告、ADR-0004 | 若会话/终端记录被留存，模型 key（及曾粘贴过的口令）有泄露面 | 轮换 DeepSeek/中继 key；如曾提供数据库/sudo 口令一并更换；只存仓库外 600 文件 | 负责人 |
| R-05 | **24 小时长跑未满 24h**（10 并发 / 10 分钟压测已完成） | 计划 §7、M7-observability、T38 | 10 并发压测已 PASS（344,734 请求、0 错误、P95 最大 43.224ms）；24h 探测已启动，截至 2026-11-13T03:53Z 观察 25 分钟（6 次健康检查），未满 24h | 让探测继续跑到 24h；满窗后用同一 `summary.json` 更新结论；期间不改探测频率 | 负责人 |
| R-06 | `chromadb 1.5.9` 有 4 个唯一安全公告（含未认证 RCE），**上游无修复版本** | 本文件配套的依赖审计 | 若未来启用 Chroma Server / 暴露 `/api/v2`，可能被代码注入或跨 tenant 读写 | 保持嵌入式 `PersistentClient`，不暴露索引服务；关注上游修复后升级并重建索引；补充镜像 OS 扫描 | 负责人 |

## 2. Medium

| ID | 风险 | 来源 | 影响 | 缓解 / 下一步 | 负责人 |
|---|---|---|---|---|---|
| R-08 | `make test-e2e` 仍用**本机一次性 compose 数据库**，不是 ADR-0006 的隧道 + `bank_assistant_test` | M5 报告、README | e2e 依赖 Mac Docker；与正式数据库路径不一致 | 立隧道 e2e 工单；在那之前报告明确标注过渡做法 | 负责人 |
| R-09 | `/metrics` 仅回环可抓取；**无跨机抓取/看板/告警投递**；`METRICS_TOKEN` 未配置 | M7-observability | 只有阈值文档，没有真正告警通道；故障依赖人工发现 | 跨机抓取前配置 token 或走 SSH；接入 Prometheus/Grafana 属后续工作 | 负责人 |
| R-10 | 限流是**单进程内存**令牌桶，重启清零，多实例可绕过 | M7-observability | 水平扩展后限流失效；当前单 worker 可用 | 多实例前换 Redis/网关门限流；手册已注明，不声称生产级 | 负责人 |
| R-11 | 活动索引指针 `data/index/active.json`、报告导出均为**本地文件** | M3/M4 报告、ADR-0005 | 多实例/多副本时指针与导出不共享，可能读到不一致索引 | 保持单实例；扩展前改共享存储/数据库指针 | 负责人 |
| R-12 | 「服务器」是负责人**个人 Ubuntu 工作站**，GPU 与 `dingtalk-*` 共享，重启需逐次批准；无 HA | M0/M6、环境清单 | 不可当作专用生产环境；训练/GPU 任务可能被抢占 | 对外只称「个人部署演练」；重启/GPU 使用逐次批准 | 负责人 |
| R-13 | 备份与数据同盘，**无独立故障域**；RPO 未计时、RTO 仅有容器重启值 | M7-deploy、T38 | 磁盘/主机故障会同时丢源与备份；容器重启 RTO 已实测（API 2.166s、DB 2.127s 到 ready），但 RPO 与整机/磁盘故障未测 | 增加异机/对象存储副本；补恢复演练的 RPO 计时；当前只声称「恢复到独立库校验一致」+ 容器重启 RTO | 负责人 |
| R-14 | 旧 M1 原型端点已补认证，但**未纳入正式限流/审计** | M7-observability | 旧入口可能成为旁路；迁移期语义不一致 | 下线或迁入 `/api/v1` 正式路由；在迁移清单中跟踪 | 负责人 |
| R-15 | 条件性微调已由 T35 真实执行（2026-11-13），但**结论只有小样本支撑**（test n=50：accuracy 0.64→0.76、macro-F1 0.6138→0.7441，p=0.0312） | M6 微调报告 | 有提升但不能外推真实银行业务；adapter 未发布、未接生产 | 不把实验结论当生产能力；如要上线，需更大训练预算 + 新冻结 test + 人工抽检 | 负责人 |

## 3. Low

| ID | 风险 | 来源 | 影响 | 缓解 / 下一步 | 负责人 |
|---|---|---|---|---|---|
| R-16 | 数据集为**开发者单人标注**，test 仅 50 检索 / 48 质检、5 条 no_answer | M6 报告 | 统计区间宽，不能声称双人一致性或业务金标 | 报告如实写分母与 `human_gold=false`；扩样需人工成本 | 负责人 |
| R-17 | 前端 `web/dist` 未入库、**未部署到服务器**；只记录构建输入 | 发布记录 | 线上只有 API；界面需本机 `npm run build`/`vite` | 需要时按 `release.json` 的 Node/npm/lock 哈希重建；不声称已有在线前端 | 负责人 |
| R-18 | `deepseek-chat` 权重/服务版本**无法固定**，`uv.lock` 也不能锁定供应商侧行为 | ADR-0004、契约 §6 | 逐 token 不可复现；供应商升级可能改变输出 | 记录调用日期/参数与 provider 标识；不承诺完全复现 | 负责人 |

## 4. T38/T39/T40 关闭 / 部分关闭证据（2026-11-13）

| 原风险 | 证据（可复核） | 状态 |
|---|---|---|
| R-04 真实数据库停机 | 只重启 `bank-assistant-db-db-1`：readiness `503→200`，首个 503 到 200 **1.820s**（命令发起到 200 2.127s）；重启后 `running_tasks=0`、`dup_task_runs=0`、`dup_findings=0`；API/redis 未重启 | **CLOSED（DB 部分）** |
| R-04 供应商故障映射 | T39 `drill2`：429→**429 `MODEL_RATE_LIMITED`**、2s 读超时→**504 `MODEL_TIMEOUT`**、连接被拒→**503 `MODEL_UNAVAILABLE`**、非法 JSON→**502 `MODEL_INVALID_STRUCTURE`**；4 条建议 `failed`/answer 空/citations 0，4 条质检 `failed`/findings 0；trace_id `t39-drill-drill2-*`；线上容器未触碰 | **CLOSED（受控假供应商）** |
| 计划 §7 质检接收/完成 | T39：60 次真实质检，POST P95 **245.244ms**（n=30）；完成 P95 **41.879s**（n=60）、成功任务 P95 **32.146s**（n=10）；token 256,621 in / 37,309 out，估算 ¥0.8117；成功 10/60，失败 50/60 均 `EVIDENCE_INVALID` 且 findings=0 | **PASS（延迟）/ PARTIAL（成功率，R-19）** |
| R-19 live 质检成功率 | T40：提示词 `inspection-v2` + 后端确定性定位器（精确→NFKC/空白/标点归一化→映射回原码点；找不到降级 finding，不失败 run）。60 次真实质检（2×30，5 并发）：**60/60 succeeded（100%）**、0 failed；POST P95 **454.871ms**、完成 P95 **15.042s**；findings 120 条（hit 41 / clear 49 / insufficient 30）；**hit 证据 100/100 精确回验、全部 spans 221/221**；0 条 `EVIDENCE_NOT_LOCATABLE`；120 次 live 调用全部 succeeded；估算 **¥0.4202**；`prompt_version=inspection-v2:f9740dfe9eb83145` | **CLOSED（合成演示会话；真实银行数据/长会话仍待抽检）** |
| R-07 部署无 worker | v0.1.2（T41）`deploy/compose.app.yml` 增加 `worker` 服务：同一不可变镜像、`python -m bank_assistant.worker`、`restart: unless-stopped`、无发布端口、同一 env/挂载、`--healthcheck`；`make deploy` 输出 worker `running/healthy`，`make release-verify` 核对 worker image/restart/端口；回滚演练先停 worker 再恢复 api+worker；线上 6/6 真实质检由常驻 worker 完成 | **CLOSED（单副本）** |
| R-05 10 并发 / 10 分钟压测 | 344,734 请求、0 错误、574.54 req/s、四端点 P95 最大 **43.224ms**；`/metrics` 前后无 db probe 错误、无非模型 5xx | **CLOSED（压测部分）** |
| R-13 RTO | API 容器重启：000→200 **1.863s**（命令→200 2.166s）；DB 容器重启：503→200 **1.820s**（命令→200 2.127s）；未重启服务器 | **PARTIAL（RTO 有值；RPO/独立故障域仍开放）** |

原始文件在服务器 `/home/jack/workspace/bank-assistant/artifacts/loadtest/T38/` 与
`.../artifacts/probe/`；T39 本机原始文件在 `.local-data/t39-artifacts/`，T40 在
`.local-data/t40-artifacts/`（均 gitignore）。
汇总见 [M7 性能报告](../milestones/M7-performance-report.md)。
**不**把部分关闭写成全部关闭：R-05 的 24h 满窗、R-13 的 RPO 仍是开放项；
R-04 的“真实 DeepSeek 429 未人为制造”只作为边界说明。

## 5. 与验收门槛的关系（不粉饰）

- 计划 §7 的 Recall@5 未达标（R-01）、引用支持度仍待负责人标注（R-02：包已就绪、标签为空）、24h 未满（R-05），
  因此**不能声称 M7 工程门槛全部通过**；T38/T39 关闭了 10 并发压测、DB 停机/RTO、
  供应商故障映射与质检延迟的实测缺口，T40 关闭了 live 质检成功率（60/60），
  T41 把该修复部署到线上并关闭「部署无 worker」（6/6 线上真实质检）。
- R-02 已从「没有标注材料」变为**标注包已就绪、等待负责人填写**（OPEN，可操作）：
  `docs/eval/citation-support-pack.md` 含 67 条引用 + 9 条无证据对照；在 **≥50 条**
  标签填完并运行 `scripts/score_citation_support.py` 前，不写支持率、不宣称 §7 达标。
  包内 5 条 live + 62 条 replay 的构成已逐条注明；replay 是抽取式答复，**不得当模型质量证据**。
- 「未处理的高危访问控制/数据泄露缺陷为 0」仅对**应用层已实现路径**成立（M7-03 测试
  通过）；凭据轮换（R-03）与依赖公告（R-06）仍是 High，整体风险不为 0。
- R-19 已在**合成演示会话**上收口（T40 60/60；T41 线上 6/6、`hit` 证据 9/9 回验）；
  它不等于真实银行数据的质检质量，误报/漏报仍需人工抽检（R-02 仍 NOT_MEASURED；
  T42 标注包已就绪但标签为空）。
  模型/提示词/供应商变更后应复测证据定位，不把一次性 100% 当成永久保证。
- R-07 关闭的是「compose 缺少 worker」这一编排缺口；worker 仍是**单副本**，进程存活
  探针不判断业务成功，被强杀的在途任务靠租约回收（可能重跑），这些是 R-10/R-11/R-12
  单机边界的延续，不因 R-07 关闭而消失。其余风险不阻塞**个人演示**，但必须在对外
  汇报与简历表述中如实标注边界。

## 6. 复核触发条件

1. 每次发布（`make release-verify`）后复核 R-06/R-11，并确认 `bank-assistant-worker`
   与 api 同镜像、`running/healthy`、无发布端口；
2. 修改检索/知识/规则/模型后复核 R-01/R-02，并复测质检证据定位（R-19 回归）；
3. 增加副本、跨机抓取或对外端口前复核 R-09/R-10/R-11/R-12；
4. 负责人完成凭据轮换后关闭 R-03。
