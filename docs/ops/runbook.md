# 部署与运维手册（v0.1.1 / M7；T29+T30+T31+T37+T38）

本手册是 Ubuntu 服务器上 API 部署、索引重建、备份恢复、回滚、监控排查与
**发布校验**的**可执行步骤**。所有命令都在 Mac 的仓库根执行（除标注“服务器”的
只读检查）；**不包含任何密码、密钥或连接串**。部署形态是个人项目的生产就绪演练，
不是任何机构的正式上线。

当前发布：`v0.1.1`，代码 `3a04952`，镜像 `bank-assistant:api-3a04952`，
活动索引 `idx-20261113T025706Z-f7974a6a`（`embedding_text=title_chunk`）。
发布记录 [docs/releases/v0.1.1.md](../releases/v0.1.1.md)（上一版
[v0.1.0](../releases/v0.1.0.md) 保留为回滚点）、机器可读清单
[release.json](../../release.json)、风险清单 [risk-register.md](risk-register.md)、
依赖审计 [dependency-audit.md](dependency-audit.md)。

## 1. 形态与约束

| 项 | 值 |
|---|---|
| 服务器 | `my-ubuntu-server`（`jack@192.168.1.27`；有线 `192.168.5.10`） |
| 项目根 | `~/workspace/bank-assistant/`（`repo`、`data`、`artifacts`、`backups`、`deploy`） |
| 数据库 | 已有 PostgreSQL 18 容器 `bank-assistant-db-db-1`，只绑 `127.0.0.1:5432` |
| API 容器 | `bank-assistant-api`（compose 项目 `bank-assistant-app`） |
| API 端口 | **只绑服务器回环** `127.0.0.1:8000`；外部访问走 SSH（`ss -tln` 判据） |
| 容器网络 | `network_mode: host`（让 `DATABASE_URL=127.0.0.1:5432` 指向宿主 PG）；端口隔离由 uvicorn `--host 127.0.0.1` 保证，不写 `ports:` |
| 镜像 | `bank-assistant:api-<code_sha7>`，不可变；服务器不重建源码；当前 `api-3a04952`（回滚点 `api-59a5869`） |
| 配置 | 仓库外 `~/workspace/bank-assistant/.env.app`（chmod 600），模板 `deploy/env.app.example` |
| compose | 仓库 `deploy/compose.app.yml`；服务器运行副本 `~/workspace/bank-assistant/deploy/compose.app.yml` |
| 索引/模型缓存 | `~/workspace/bank-assistant/data/{index,models}`（可重建，不随镜像发布）；当前索引 `idx-20261113T025706Z-f7974a6a`，上一版 `idx-20261112T110703Z-2334d03e` 保留 |
| 备份 | `~/workspace/bank-assistant/backups/<UTC timestamp>/` |
| 部署状态 | `~/workspace/bank-assistant/deploy/state.env`（当前/上一版镜像与 SHA，非秘密） |
| 发布清单 | 仓库根 `release.json`；`make release-verify` 对照线上实例 |
| worker | **未编排进 compose**；只能手工用同一镜像启动（见 §8.2），队列会停留 `queued` |

### 1.1 文件与配置矩阵（别混用）

| 文件 | 用在哪 | 作用 | 是否进 Git |
|---|---|---|---|
| `deploy/compose.yml` | 仅 Mac 开发/冒烟 | 本机构建 `bank-assistant:dev`、端口映射 8000 | 是（无真实值） |
| `deploy/compose.app.yml` | 服务器 API | 不可变镜像、host 网络、仅回环、仓库外 env | 是 |
| `deploy/compose.db.yml` | 仅服务器数据库 | PostgreSQL 18、仅回环 5432、bind mount | 是 |
| `~/workspace/bank-assistant/.env.app` | 服务器 API | 模型 key、`DATABASE_URL`、`SECRET_KEY` | **否**（600） |
| `~/workspace/bank-assistant/.env.pg` | 服务器 DB compose | `POSTGRES_*`、`PG_DATA_DIR` | **否**（600） |
| 本机 `.env` | Mac 开发/评测 | 经隧道连 `15432`，模型 key | **否**（600，gitignore） |

**不做的事**：不 push、不在服务器保存 GitHub 写凭据、不把端口映射到 `0.0.0.0`、
不重启服务器整机、不触碰服务器上的 `dingtalk-*` 容器、不降数据库 schema。
`deploy/compose.db.yml` 只用于服务器；Mac 不部署数据库（ADR-0006）。

## 2. 首次部署（只做一次）

### 2.1 服务器目录与配置

```bash
ssh my-ubuntu-server 'mkdir -p ~/workspace/bank-assistant/{data,artifacts,backups,deploy}'
ssh my-ubuntu-server 'install -m 600 /dev/null ~/workspace/bank-assistant/.env.app'
ssh my-ubuntu-server '$EDITOR ~/workspace/bank-assistant/.env.app'   # 按 deploy/env.app.example 填写
```

`.env.app` 要点：

- `APP_ENV=staging`、`SECRET_KEY`（`openssl rand -hex 32` 生成，**每台机器独立**）；
- `DATABASE_URL` 指向 `127.0.0.1:5432/bank_assistant_demo`（同机回环，不需要隧道）；
- 模型 key 只写在这里；不写进仓库、不写进日志、不 `export` 到 shell 历史；
- `MODEL_MODE`、`MODEL_PROVIDER`、`DEEPSEEK_MODEL`、`EMBEDDING_MODEL_NAME` 是版本页会
  展示的**真实身份**，必须与活动索引清单一致。

检查权限与不泄漏：

```bash
ssh my-ubuntu-server 'stat -c "%a %n" ~/workspace/bank-assistant/.env.app'   # 期望 600
git status --porcelain        # 仓库里不应出现 .env.app
```

### 2.2 首次部署

```bash
gh issue view 32          # 确认发布票验收
git status --porcelain    # 必须为空；deploy 拒绝脏树
git rev-parse HEAD        # 部署的就是这个提交，镜像标签取前 7 位
make deploy
```

`make deploy` 依次做：构建 `bank-assistant:api-<sha7>` → `docker save | ssh docker load`
→ 用 `git bundle` 把服务器 checkout 精确重置到同一 SHA → 同步 compose 到仓库外副本
→ `docker compose up -d --wait` → 校验 live/ready、回环绑定与版本页 SHA。
首次部署没有上一版镜像，回滚点为空是预期的；下一次部署会记录回滚点。

### 2.3 建索引（首次与知识变更后）

```bash
make deploy-index
```

在服务器上用**已部署镜像**跑一次性容器（`--network host`、仓库与数据根挂载、以项目
用户运行），执行 `python scripts/index_rebuild.py`。首次会下载 BGE 模型（约 100MB）
到 `data/models`，之后复用。成功后 `active.json` 原子切换，打印：

```
index_version=idx-...
embedding_text=title_chunk
index_content_sha256=...
calibration_vector_sha256=...
embedding_model_version=BAAI/bge-small-zh-v1.5@512
chunk_count=...
```

索引文本形态由 `RETRIEVAL_EMBEDDING_TEXT` 决定（T37 起默认 `title_chunk`：标题拼入
**embedding 输入**，分块正文与码点区间不变，引用仍可逐字回验）。改这个值会改变
向量，必须新建/重建索引（ADR-0005），不能用旧集合冒充。`index_content_sha256`
是内容身份：同语料 + 同 provider + 同参数重建两次应完全一致；`index_version`
每次唯一（旧集合保留可回滚），两次重建的 `chunk_id` 会因包含 `index_version`
而不同，但区间与相似度分数一致。

索引是**可重建产物**，不随镜像发布；失败时旧指针保留。重建只写
`data/index/{chroma,manifests,active.json}`，不写源码树。

**重建前先备份活动指针**（回滚点）：

```bash
ssh my-ubuntu-server 'cd ~/workspace/bank-assistant && cp data/index/active.json   deploy/index-active-before-$(date -u +%Y%m%dT%H%M%SZ).json'
```

### 2.3.1 切回旧索引（回滚演练 / 索引事故）

索引版本只增不改，回滚不需要重建，只把活动指针指回旧清单：

```bash
make index-activate INDEX_VERSION=idx-20261112T110703Z-2334d03e
```

`scripts/index_activate.sh` 用**当前已部署镜像**跑一次性容器，执行
`python scripts/index_activate.py --index-version ...`：切换前用当前 provider
重算校准哈希并核对集合条数，校验不过就失败且不改指针；成功后自动跑
`verify_deployment.sh`（版本页 index_version 必须变成目标值）。再切回新索引：

```bash
make index-activate INDEX_VERSION=idx-20261113T025706Z-f7974a6a
make release-verify
```

索引指针是单机文件（风险 R-11），切换只影响 API 进程的读路径，不需要重启。

### 2.4 发布包与发布校验（每次发布必做）

```bash
make release-verify
```

该目标读取仓库根 `release.json`，通过 SSH 与服务器回环逐项核对：
运行容器 image id/标签、`/health/live`、`/health/ready`、Alembic head、
`/api/v1/system/version` 的 `code_sha`、活动索引版本、校准 sha256、索引 embedding
模型、生成 provider/模型。**任一不一致即非零退出**；不改配置、不重启服务。

发布记录/清单在版本发布时冻结（当前 [v0.1.0](../releases/v0.1.0.md)），
后续文档提交不改变已部署镜像身份。更新版本时先改 `release.json` 的
`release.version` 与部署字段，再执行 `make release-verify`，最后在
`docs/releases/<version>.md` 记录真实输出。

## 3. 日常发布

```bash
git status --porcelain     # 必须干净，deploy 会拒绝脏树
make deploy                # 新镜像 → 服务器，自动记录上一版为回滚点
make deploy-index          # 知识/embedding 变化时才需要
make verify-deploy         # 可选：随时复核线上真实状态
```

`make deploy` 的输出里应包含：

- 本地与服务器 **image id 相同**、`tar_sha256` 一致；
- 服务器 `HEAD=<sha>` 且工作树干净；
- `/health/live`、`/health/ready` 均 200；
- `ss -tln` 只出现 `127.0.0.1:8000`；host 网络下同时检查容器 `command` 含
  `--host 127.0.0.1`（不写 `0.0.0.0`），不依赖 `docker port`。

## 4. 备份（每次发布/迁移前）

```bash
make backup
```

在服务器侧执行，产物 `~/workspace/bank-assistant/backups/<timestamp>/`：

| 文件 | 内容 |
|---|---|
| `dumps/<db>.dump` | `pg_dump --format=custom --no-owner`（dev、demo 各一份） |
| `counts/<db>.tsv` | 备份时所有 public 表行数（恢复校验基准） |
| `hashes/<db>.tsv` | 关键表行数 + `max(content_hash)` + 聚合 md5 |
| `alembic/<db>.txt` | 备份时 alembic head |
| `artifacts.tar.gz` | 产物目录；为空时写 `artifacts=skipped-empty` |
| `SHA256SUMS` / `meta.txt` | dump 校验和与不含凭据的元数据 |

**迁移前必须先备份**：`make backup` 之后才执行 `alembic upgrade head`（见 §5）。
备份只读源库，不删除旧备份。

## 5. 迁移（服务器，仓库同 SHA）

**顺序不可交换**：先备份 → 再迁移 → 再校验。

```bash
make backup             # 迁移前快照；记录 backup_id
# 确认备份成功（dumps/counts/hashes/alembic 都有内容）后再执行下面命令
```

服务器没有 node、pypi 慢，因此迁移也用已部署镜像的一次性容器执行，不装宿主依赖：

```bash
IMAGE=$(ssh my-ubuntu-server "sed -n 's/^CURRENT_IMAGE=//p' ~/workspace/bank-assistant/deploy/state.env")
ssh my-ubuntu-server "docker run --rm --network host \
  --env-file ~/workspace/bank-assistant/.env.app \
  -v ~/workspace/bank-assistant/repo:/app/repo -w /app/repo \
  $IMAGE alembic upgrade head"
```

记录版本：

```bash
ssh my-ubuntu-server "docker exec bank-assistant-db-db-1 \
  psql -U bank -d bank_assistant_demo -At -c 'select version_num from alembic_version'"
```

迁移策略：**向后兼容的扩展**（加表/加列/加索引），不做会破坏旧代码的收缩；
回滚旧代码前先确认 head 未变、旧代码能读新 schema。**不盲目 `alembic downgrade`**。

## 6. 恢复演练（不覆盖源库）

```bash
make backup           # 取最新快照（源库此后不要再写入）
make restore-check
```

`scripts/restore_check.sh` 的安全闸门与校验：

1. 目标库只能是 `<源库>_restore_check`（正则 `^bank_assistant_[a-z0-9_]+_restore_check$`），
   等于源库或名字不合规直接拒绝；
2. 只对目标库 `DROP/CREATE`，源库只读；
3. 若源库在备份后发生变化 → **FAIL**，要求重新备份，不许拿旧备份冒充“与当前源一致”；
4. 恢复后逐项比对：全表行数、`content_hash` 行数/`max`/聚合 md5、alembic head；
5. 全部一致打印 `RESULT: PASS`，否则非零退出。

恢复只在独立库验证，**不切换线上数据库**；线上数据库的灾难恢复需要负责人逐次批准。

## 7. 回滚演练

```bash
# 1) 索引指针回退（先切索引，再切镜像，顺序可换但两步都要做）
make index-activate INDEX_VERSION=idx-20261112T110703Z-2334d03e

# 2) 镜像回退演练：上一版 → 校验 → 自动恢复当前版
make rollback

# 3) 指针切回新索引并复核
make index-activate INDEX_VERSION=idx-20261113T025706Z-f7974a6a
make release-verify
```

`make rollback` 步骤：读取 `state.env` 的上一版 → 用**上一版不可变镜像**
`up -d --wait` → 校验 `/health/live`、`/health/ready`、版本页 `code_sha`
（=上一版 SHA）与当时的活动索引版本 → 再切回当前镜像并校验。全程不重启
服务器、不降 schema、不动数据库/`dingtalk-*`/redis 容器。

- 上一版镜像必须仍在服务器上（部署脚本保留 tag，不自动清理）；
- 若 `state.env` 没有上一版（首次部署），用 `BACK_TO=<镜像> make rollback` 指定；
- 回滚后 `state.env` 不改写：下一次 `make deploy` 仍会记录正确的回滚点；
- 索引回滚点 = 旧清单仍存在（`data/index/manifests/<旧 index_version>.json`）
  且旧集合未被删除；指针备份在 `deploy/index-active-before-*.json`；
- **镜像与索引要配对回滚**：旧镜像可以读旧索引（T37 前清单没有
  `embedding_text`，按正文口径），新镜像也能读旧索引，但检索收益会退回
  T36 之前；回滚演练必须同时验证版本页的 `code_sha` 与 `index_version`。

## 8. 故障排查与事故第一响应

### 8.1 常见故障

| 现象 | 处理 |
|---|---|
| `make deploy` 报工作树不干净 | 先提交；部署只允许提交，脏树会让镜像 SHA 不可信 |
| `test -f .env.app` 失败 | 按 §2.1 创建，chmod 600；不要从仓库复制真实值 |
| `compose up --wait` 超时 | `ssh my-ubuntu-server 'docker logs --tail 100 bank-assistant-api'` 看启动错误 |
| `/health/ready` 503 | 数据库容器未起/凭据不对：`docker ps`、`docker inspect bank-assistant-db-db-1` |
| `/system/version` 的 `index_version` 为 null | 没建索引：`make deploy-index` |
| 索引构建报 embedding 下载失败 | 服务器网络受限时重试；模型缓存在 `data/models`，成功后不重复下载 |
| 端口出现在 `0.0.0.0` | 立即 `docker compose -f ~/workspace/bank-assistant/deploy/compose.app.yml down`，检查 compose 的 `command` 是否丢了 `--host 127.0.0.1` |
| `/health/ready` 503 且日志无异常 | 先确认容器是 host 网络；bridge 网络下 `127.0.0.1:5432` 是容器自己，连不到宿主 PG |
| 回滚后旧版启动失败 | 旧镜像与当前 schema 不兼容：先恢复到当前镜像，再评估兼容性（不要降 schema） |
| `make release-verify` 失败 | 不要改清单迁就线上；先查 `state.env`、容器 image id、Alembic head 与索引指针，确认是部署漂移还是清单过期 |

### 8.2 事故第一响应（503 / 429 / 504 / worker 卡住）

**先做（任何一类都一样）**：

1. 记录时间、影响端点、`x-trace-id`；不贴请求体/会话原文/令牌；
2. 只读诊断：`/health/live`、`/health/ready`、`/metrics`、容器状态与最近日志；
3. 只允许重启 `bank-assistant-api`（§10.5）；**不重启服务器整机**，数据库/`dingtalk-*`
   容器未经负责人逐次批准不动；
4. 不为了“恢复”关闭限流、放宽输入上限、删除失败任务或手工改 `tasks.status`；
5. 诊断证据（计数/状态/时间线）保留在事故记录中，不删除容器日志。

诊断命令（服务器回环，经 SSH 执行；只输出计数/状态）：

```bash
ssh my-ubuntu-server 'curl -fsS http://127.0.0.1:8000/health/live; echo; curl -fsS http://127.0.0.1:8000/health/ready; echo'
ssh my-ubuntu-server 'curl -fsS http://127.0.0.1:8000/metrics | grep -E "bank_(db_ready|http_requests_total|http_request_duration_seconds|rate_limit_rejections_total|model_calls_total|task_events_total|task_queue_depth)" | head -40'
ssh my-ubuntu-server 'docker inspect -f "{{.State.Status}} {{.State.Health.Status}} {{.HostConfig.RestartPolicy.Name}}" bank-assistant-api'
ssh my-ubuntu-server 'docker logs --since 30m --tail 300 bank-assistant-api 2>&1 | grep -E "trace_id|ERROR|error|503|429|504" | tail -40'
```

**503（依赖不可用，不是“模型答不出”）**：

- 先看 `bank_db_ready`：0 表示真实 `SELECT 1` 失败，不是空库；看
  `docker inspect bank-assistant-db-db-1` 与 `pg_isready`；
- 隧道/数据库不可达时**显式 503**，禁止切 SQLite/mock 后宣称恢复；
- 数据库容器重启需负责人批准；恢复后确认 `/health/ready` 200、`bank_db_ready 1`，
  再跑 `make release-verify`；
- 503 持续且 API 日志有连接串/SQL 细节 → 按泄露事件处理并立即停止外发日志。

**429（限流）**：先区分本地限流与供应商限流：

- `bank_rate_limit_rejections_total{scope="login"}` 突增：登录/凭据填充或误配置；
  返回带 `Retry-After`，按客户端退避，**不要**调大上限掩盖攻击；
- `scope="expensive"` 突增：`/suggestions`、`/inspections`、`/reports` 被高频调用；
  核对调用方与 `RATE_LIMIT_EXPENSIVE_*`；
- `bank_model_calls_total{status="rate_limited"}` 增长：供应商配额问题，按
  `Retry-After` 退避，保留 failed 行；不是本地限流失效；
- 单进程令牌桶重启清零（R-10），不能据此判断“攻击已停止”。

**504（模型超时）**：

- 看 `bank_model_calls_total{status="timeout"}` 与
  `bank_model_request_duration_seconds`；504 是显式失败，不允许重试成假成功；
- 检查供应商状态/网络出口；确认 `model_runs`/inspection 行保留失败原因与 trace；
- 不把超时任务改成 `succeeded`、不补写 findings；重试由业务重提生成新运行记录。

**worker 卡住 / 队列积压**：

- 事实：`deploy/compose.app.yml` **只编排 API，没有 worker 服务**（R-07）；
  服务器上新质检任务会停在 `queued`，必须手工启动同一镜像的 worker；
- 先只读确认：

  ```bash
  ssh my-ubuntu-server 'curl -fsS http://127.0.0.1:8000/metrics | grep -E "bank_task_(queue_depth|events_total)"'
  ssh my-ubuntu-server 'docker exec bank-assistant-db-db-1 psql -U bank -d bank_assistant_demo -At -F "|" \
    -c "select status, count(*), min(created_at) from tasks group by status order by status"'
  ```

- 一次性排空（**用已部署不可变镜像**，不装宿主依赖）：

  ```bash
  APP_UID=$(ssh my-ubuntu-server id -u); APP_GID=$(ssh my-ubuntu-server id -g)
  IMAGE=$(ssh my-ubuntu-server "sed -n 's/^CURRENT_IMAGE=//p' ~/workspace/bank-assistant/deploy/state.env")
  ssh my-ubuntu-server "docker run --rm --network host \
    --env-file ~/workspace/bank-assistant/.env.app \
    --user $APP_UID:$APP_GID \
    -e INDEX_DIR=/app/data/index -e DATA_ROOT=/app/data -e ARTIFACT_ROOT=/app/artifacts \
    -v ~/workspace/bank-assistant/data:/app/data \
    -v ~/workspace/bank-assistant/artifacts:/app/artifacts \
    $IMAGE python -m bank_assistant.worker --once"
  ```

- 若需要常驻（仍不写进 compose）：把上面 `--rm` 换成
  `-d --name bank-assistant-worker --restart unless-stopped`，去掉 `--once`；
  停止用 `ssh my-ubuntu-server 'docker rm -f bank-assistant-worker'`；
- 卡在 `running`：看 `bank_task_events_total{event="lease_lost"|"reclaim"}` 与 worker
  日志；租约到期会被重新领取（CT-11），**不要手工改状态或删除任务**；
- 处理后确认同一质检只有一份最终结果、`failed` 有稳定 `error_code`，再关闭事故。

## 9. 日常检查命令

```bash
make sync-check                                        # 两端 SHA 与未提交状态
make verify-deploy                                     # live/ready/版本页/回环端口
make release-verify                                    # 发布清单 vs 线上（image/alembic/index/version）
make db-tunnel-status                                  # Mac 侧隧道状态（开发/集成测试用）
ssh my-ubuntu-server 'docker ps --filter name=bank-assistant --format "{{.Names}} {{.Status}} {{.Ports}}"'
ssh my-ubuntu-server 'docker inspect -f "{{.State.Health.Status}}" bank-assistant-api'
ssh my-ubuntu-server 'ss -tln | grep -E ":(8000|5432) "'
ssh my-ubuntu-server 'du -sh ~/workspace/bank-assistant/{data,backups}'
ssh my-ubuntu-server 'curl -fsS http://127.0.0.1:8000/metrics | grep -E "bank_(db_ready|task_queue_depth)"'
```

Mac 侧需要数据库时（开发、集成测试、seed；**不是部署步骤**）：

```bash
make db-tunnel          # 幂等：127.0.0.1:15432 → 服务器 127.0.0.1:5432
make doctor             # 隧道 + bank_assistant_test SELECT 1；缺失时非零退出
make test-integration   # 只重建 *_test 后迁移 + 集成测试
```

隧道不可用时集成测试/迁移/seed/备份恢复必须显式失败或标 BLOCKED，**不得**切
SQLite/mock 后宣称通过（ADR-0006）。

## 10. 监控、日志与安全观测（M7-02/T30）

### 10.1 `/metrics` 的暴露面

- 端点：`GET /metrics`（Prometheus 文本格式），由 API 进程直接提供；
- **内部端点**：API 只绑服务器回环 `127.0.0.1:8000`（§1），跨机抓取必须经
  `ssh` 或 SSH 隧道；不要为它单独映射 `0.0.0.0`；
- 可选令牌：若将来要跨主机抓取，在 `.env.app` 配置 `METRICS_TOKEN`，之后
  请求必须带 `Authorization: Bearer <token>`；未配置时依赖回环绑定作为访问控制；
- 指标只含聚合值，不含用户 id、会话原文、请求体、trace_id 或凭据。

主要序列（全部以 `bank_` 开头）：

| 指标 | 类型 | 含义 |
|---|---|---|
| `bank_http_requests_total{method,route,status}` | counter | 请求数；route 是路由模板，不是实际 UUID 路径 |
| `bank_http_request_duration_seconds{method,route}` | histogram | 请求耗时（非模型 API 门槛 P95 ≤ 500ms） |
| `bank_http_inflight_requests` | gauge | 正在处理的请求数 |
| `bank_model_calls_total{provider,model,status,mode}` | counter | 模型调用结果（含 timeout/rate_limited/invalid_structure） |
| `bank_model_tokens_total{provider,model,kind}` | counter | prompt/completion token 用量 |
| `bank_model_request_duration_seconds{provider,mode}` | histogram | 模型调用耗时 |
| `bank_task_events_total{event,task_type}` | counter | claim/reclaim/success/retry/fail/renew/lease_lost/sweep |
| `bank_task_queue_depth{task_type,status}` | gauge | 抓取时按数据库真实任务状态计数 |
| `bank_db_ready` | gauge | 1=真实 `SELECT 1` 成功；0=不可用（不伪造深度） |
| `bank_rate_limit_rejections_total{scope}` | counter | login/expensive 限流拒绝 |
| `bank_input_rejections_total{kind}` | counter | 请求体/会话原文等输入上限拒绝 |
| `bank_db_probe_errors_total` / `bank_task_queue_depth_collect_errors_total` | counter | 依赖探测失败；用于区分“值为 0”和“指标采集本身失败” |

### 10.2 告警阈值（初始工程门槛，非业务 SLA）

| 告警 | 条件 | 处置 |
|---|---|---|
| API 错误率 | 5xx（排除 429）占 `bank_http_requests_total` > 1%，持续 10 分钟 | 看 `/health/ready`、容器日志与 `bank_db_ready` |
| 普通 API 延迟 | 非模型路由 P95 > 500ms，持续 10 分钟 | 查数据库/事件循环；模型路由单独看 |
| 模型限流 | `status="rate_limited"` 任意增长或 > 5%/10 分钟 | 检查供应商配额；429 会带 `Retry-After`，调用方按提示退避 |
| 模型超时 | `status="timeout"` 增长或 P95 > 30s | 查供应商状态/网络；超时映射 504，不重试成假成功 |
| 数据库不可用 | `bank_db_ready == 0` 持续 1 分钟 | `docker ps`、`docker inspect bank-assistant-db-db-1`，按 §8 排查；API 端点应返回 503 |
| 队列积压 | `bank_task_queue_depth{status="queued"} > 50` 或 oldest queued > 5 分钟 | 启动/检查 worker；确认没有永久 running |
| 租约回收 | `event="reclaim"` 或 `lease_lost` 增长 | 可能 worker 崩溃/租约过短；查 worker 日志，确认无重复最终结果 |
| 任务失败 | `event="fail"` 增长 / `MAX_ATTEMPTS_EXCEEDED` | 查 `error_code` 与模型输出校验日志；不要清空问题列表 |
| 限流/输入拒绝 | `bank_rate_limit_rejections_total`、`bank_input_rejections_total` 突增 | 可能是攻击/误配置；核对 `RATE_LIMIT_*`、`MAX_REQUEST_BYTES` |

### 10.3 结构化日志与脱敏

每个请求一行 JSON（stdout，`docker logs` 可读）：

```json
{"event":"http_request","trace_id":"...","method":"POST","route":"/suggestions","status":201,"duration_ms":12.3,"request_bytes":42,"user_id":"..."}
```

- 不记录请求体、查询串、Authorization、cookie、会话原文、令牌或连接串；
- `bank_assistant.observability.redaction` 作为第二道防线，把连接串密码、
  `sk-...`/`pk-...` key、Bearer/JWT、`password=`/`token=` 等替换为 `***`；
- 响应头 `x-trace-id` 与日志 `trace_id` 相同，便于把前端错误关联到后端日志。

日常检查（**只输出计数/状态，不打印密钥**）：

```bash
# 指标（服务器回环；需要时经 SSH 执行）
ssh my-ubuntu-server 'curl -fsS http://127.0.0.1:8000/metrics | grep -E "bank_(db_ready|http_requests_total|task_queue_depth|model_calls_total)" | head -20'
ssh my-ubuntu-server 'curl -fsS http://127.0.0.1:8000/metrics | grep -c "^bank_"'
# 最近日志里 trace_id 行数（应 > 0）
ssh my-ubuntu-server 'docker logs --since 1h bank-assistant-api 2>&1 | grep -c "trace_id"'
# 秘密形态计数（期望 0；只打印计数，不打印匹配内容）
ssh my-ubuntu-server 'docker logs --since 24h bank-assistant-api 2>&1 | grep -Ec "sk-[A-Za-z0-9]|postgresql(\+psycopg)?://[^:[:space:]]+:[^@[:space:]]+@|Bearer [A-Za-z0-9._-]{8,}"'
# 会话原文/敏感样例不得出现在日志（把 <marker> 换成一次性测试串；期望 0）
ssh my-ubuntu-server 'docker logs --since 24h bank-assistant-api 2>&1 | grep -c "<marker>"'
```

### 10.4 限流与输入上限配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | 关闭仅用于本地压测，生产不得关闭 |
| `RATE_LIMIT_LOGIN_PER_MINUTE` / `_BURST` | 30 / 10 | `/auth/login` 按客户端 IP 令牌桶 |
| `RATE_LIMIT_EXPENSIVE_PER_MINUTE` / `_BURST` | 60 / 20 | `/suggestions`、`/inspections`、`/reports` 按用户令牌桶 |
| `MAX_REQUEST_BYTES` | 2,000,000 | 超过 Content-Length 直接 413，不读 body |
| `MAX_CONVERSATION_CHARS` | 200,000 | 会话原文超限 413；不截断后当完整会话 |
| `MAX_PROMPT_CHARS` | 4,000 | 候选答复问题超限 422；构造提示词时再防御性截断 |
| `METRICS_TOKEN` | 空 | 可选；配置后 `/metrics` 需要 Bearer 令牌 |

超限返回：429 带 `Retry-After` 与 `retryable=true`；413/422 带稳定错误码与
`trace_id`。限流是**单进程内存**实现，与当前一个 uvicorn worker 的部署匹配；
重启会清零计数，多实例前必须换成共享存储（Redis/网关门限流）。

### 10.5 故障恢复边界

- 需要重启容器验证恢复时，**只允许** `ssh my-ubuntu-server 'docker restart bank-assistant-api'`；
- 不重启服务器整机、不重启/不触碰 `bank-assistant-db-db-1` 以外的数据库容器，
  更不触碰 `dingtalk-*` 容器；
- 重启 API 会清空进程内指标与限流桶；日志/数据库不受影响。

## 11. 与 ADR 的关系

- ADR-0002：PostgreSQL 是事实源，Chroma 索引可重建；备份校验以数据库哈希为准。
- ADR-0003：服务器只读拉取；部署用 `git bundle` 精确对齐 SHA，不需要服务器写凭据。
- ADR-0006：数据库只在服务器回环；Mac 开发用 SSH 隧道，部署时同机直连。
- ADR-0005：索引版本化 + 校准哈希 + 文件指针；指针单机有效（风险 R-11）。

## 12. 命令索引与实现核对（T31）

手册出现的每条 `make` 命令都必须能在仓库找到实现；本轮逐条核对如下
（`make -n <target>` 可验证目标存在，实际部署/备份/恢复/回滚已在 T29/T30 执行）：

| 命令 | 实现 | 说明 |
|---|---|---|
| `make deploy` | `scripts/deploy.sh` | 构建/传输/对齐 SHA/compose up/校验 |
| `make deploy-index` | `scripts/deploy_index.sh` | 服务器一次性容器重建索引（默认 `title_chunk`） |
| `make index-activate` | `scripts/index_activate.sh` | T37 新增：指针切回旧索引版本（`INDEX_VERSION=idx-...`） |
| `make verify-deploy` | `scripts/verify_deployment.sh` | live/ready/回环/版本页 |
| `make release-verify` | `scripts/release_verify.sh` | T31 新增：对照 `release.json` |
| `make backup` | `scripts/backup.sh`（经 SSH） | 只读源库，仓库外备份 |
| `make restore-check` | `scripts/restore_check.sh`（经 SSH） | 只写 `*_restore_check` |
| `make rollback` | `scripts/rollback.sh` | 切上一版 → 校验 → 恢复当前版 |
| `make sync-check` | `scripts/sync-check.sh` | 两端 SHA/工作树只读检查 |
| `make db-tunnel` / `db-tunnel-status` | `scripts/pg-tunnel.sh` | Mac 开发隧道，幂等 |
| `make doctor` | `scripts/doctor.sh` | 依赖/隧道/目标库检查，不打印秘密 |
| `make test-integration` | `scripts/test-integration.sh` | 隧道 + `*_test` 空库迁移 + 集成测试 |

手册中的服务器命令（`docker`/`curl`/`psql`/`ss`/`sed`/`python3`）均为 **Ubuntu 已安装的
系统命令 + 已部署镜像内命令**，不要求在服务器安装 Python 项目依赖或 node。

## 13. 容量压测、稳定性探测与受控故障演练（T38）

本节命令全部在 Mac 仓库根发起，实际动作在服务器执行；不暴露 LAN，不改线上
`.env.app`，不重启服务器整机，不触碰 `redis-redis-1` / `dingtalk-*`。

### 13.1 10 并发 / 10 分钟非模型 API 压测

脚本 `scripts/load_test.py`（纯逻辑有离线单测；只读 HTTP）。把脚本复制到服务器产物
目录，用**已部署镜像**运行，避免在宿主安装依赖：

```bash
ART=/home/jack/workspace/bank-assistant/artifacts/loadtest/T38
ssh my-ubuntu-server "mkdir -p $ART"
scp scripts/load_test.py my-ubuntu-server:$ART/load_test.py
ssh my-ubuntu-server "cd $ART && docker run --rm --network host \
  --user \"\$(id -u):\$(id -g)\" -v \"\$PWD:/work\" \
  -e LOADTEST_PASSWORD=\"\${DEMO_PASSWORD:?公开演示密码}\" \
  --entrypoint python bank-assistant:api-3a04952 /work/load_test.py \
  --base-url http://127.0.0.1:8000 --duration-seconds 600 --concurrency 10 \
  --inspection-id <已存在的演示质检 UUID> --output-dir /work/nonmodel"
```

输出：`nonmodel/summary.json`（报告数字来源）、`buckets.jsonl`（10 秒桶）、
`metrics-before.txt` / `metrics-after.txt`。复跑前先确认没有别的压测在跑；
不要用 `RATE_LIMIT_ENABLED=false` 绕过线上限流。

候选答复（replay，避免烧钱）用**临时实例**，不改线上容器：

```bash
# 1) 克隆演示库（测后删除；不碰 demo/dev/test）
ssh my-ubuntu-server 'docker exec bank-assistant-db-db-1 createdb -U bank bank_assistant_loadtest'
ssh my-ubuntu-server "docker exec bank-assistant-db-db-1 sh -c 'pg_dump -U bank bank_assistant_demo | psql -q -U bank bank_assistant_loadtest'"

# 2) 生成仓库外 replay env（600）：MODEL_MODE=replay + 指向 loadtest 库
ssh my-ubuntu-server 'cd ~/workspace/bank-assistant && \
  sed -e "s#^MODEL_MODE=.*#MODEL_MODE=replay#" \
      -e "s#^DATABASE_URL=\(.*\)/bank_assistant_demo#DATABASE_URL=\1/bank_assistant_loadtest#" \
      .env.app > .env.replay && chmod 600 .env.replay'

# 3) 临时 replay 实例（8011，仅回环；限流关闭只在本实例）
ssh my-ubuntu-server 'cd ~/workspace/bank-assistant && docker run -d --rm \
  --name bank-assistant-replay-t38 --network host --user 1000:1000 \
  --env-file .env.replay -e RATE_LIMIT_ENABLED=false \
  -e GIT_SHA=replay -e GIT_BRANCH=replay \
  -e INDEX_DIR=/app/data/index -e DATA_ROOT=/app/data -e ARTIFACT_ROOT=/app/artifacts \
  -e FASTEMBED_CACHE_PATH=/app/data/models -e HOME=/app/data/home \
  -v ~/workspace/bank-assistant/data:/app/data \
  -v ~/workspace/bank-assistant/artifacts:/app/artifacts \
  bank-assistant:api-3a04952 uvicorn bank_assistant.main:app --host 127.0.0.1 --port 8011'

# 4) 2 分钟 / 5 并发
ssh my-ubuntu-server "cd $ART && docker run --rm --network host --user \"\$(id -u):\$(id -g)\" \
  -v \"\$PWD:/work\" -e LOADTEST_PASSWORD=\"\${DEMO_PASSWORD}\" \
  --entrypoint python bank-assistant:api-3a04952 /work/load_test.py \
  --base-url http://127.0.0.1:8011 --phase suggestion --endpoints suggestion \
  --duration-seconds 120 --concurrency 5 --output-dir /work/replay"

# 5) 清理（顺序不能反：先删实例，再删库与 env）
ssh my-ubuntu-server 'docker rm -f bank-assistant-replay-t38'
ssh my-ubuntu-server 'docker exec bank-assistant-db-db-1 dropdb -U bank bank_assistant_loadtest'
ssh my-ubuntu-server 'rm -f ~/workspace/bank-assistant/.env.replay'
```

### 13.2 24 小时稳定性探测

脚本 `scripts/probe_uptime.py`（仅标准库，宿主 `python3` 运行）：

```bash
# 启动
ssh my-ubuntu-server 'mkdir -p ~/workspace/bank-assistant/artifacts/probe && \
  cp ~/workspace/bank-assistant/artifacts/loadtest/T38/probe_uptime.py \
     ~/workspace/bank-assistant/artifacts/probe/ && \
  cd ~/workspace/bank-assistant/artifacts/probe && \
  nohup python3 probe_uptime.py --out-dir . \
    --base-url http://127.0.0.1:8000 > probe.log 2>&1 </dev/null &'

# 状态 / 摘要
ssh my-ubuntu-server 'cat ~/workspace/bank-assistant/artifacts/probe/probe.pid; \
  cat ~/workspace/bank-assistant/artifacts/probe/summary.json'

# 停止（SIGTERM 会补写 summary.json 并删除 pidfile）
ssh my-ubuntu-server 'kill "$(cat ~/workspace/bank-assistant/artifacts/probe/probe.pid)"'
```

- 频率：5 分钟 health，30 分钟 metrics + 容器状态；`probe.jsonl` 超过 5 MiB 轮转
  （`.1/.2/.3`）；metrics 原文保留 96 份。
- 单实例保护：`probe.pid` 存活时拒绝重复启动；换目录或先停止。
- 快速验证：`python3 scripts/probe_uptime.py --out-dir /tmp/probe-check --once`。
- 该探测**不是**业务可用性 SLA；`/metrics` 仅回环，跨机需 SSH（R-09）。

### 13.3 受控故障演练（只重启本项目容器）

先确认事实：`docker inspect` 记录 API/DB/redis 的 `StartedAt` 与重启计数；
演练期间不要跑压测或 worker。

```bash
# 通用 readiness 观察器：0.2s 一次，记录到 drill-*.log
cd ~/workspace/bank-assistant/artifacts/loadtest/T38   # 服务器上
rm -f drill-api.log drill-api.stop
( while [ ! -f drill-api.stop ]; do
    printf '%s %s\n' "$(date +%s.%N)" \
      "$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 http://127.0.0.1:8000/health/ready)"
    sleep 0.2
  done ) > drill-api.log 2>&1 &
watcher=$!; sleep 2
date +%s.%N > drill-api.restart-start
docker restart bank-assistant-api
date +%s.%N > drill-api.restart-end
until [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 http://127.0.0.1:8000/health/ready)" = 200 ]; do sleep 0.25; done
touch drill-api.stop; wait $watcher
```

数据库演练把 `bank-assistant-api` 换成 `bank-assistant-db-db-1`；预期 readiness
出现 `503` 后恢复 `200`。API 进程重启时端口不可达，观察到的是 `000`（连接拒绝）
→ `200`，不是 503——不要改写观测值。

演练后核对（真实库）：

```bash
ssh my-ubuntu-server "docker exec -i bank-assistant-db-db-1 psql -U bank -d bank_assistant_demo -At -F'|' -f - < /tmp/t38-invariants.sql"
```

期望：`running_tasks=0`、`running_runs=0`、`queued_tasks=0`、历史 `succeeded=3`、
`dup_task_runs=0`、`dup_findings=0`、`dup_final_runs=0`。若出现永久 `running`，
按 §8.2 查租约与 worker，**不要手工改状态**。

**硬限制**：不重启服务器；不动 `redis-redis-1`/`dingtalk-*`；不降 schema；不把
`000` 记成 `503`。T38 实测参考：API 重启 1.863s（首次失败→200，命令→200 2.166s），
DB 重启 1.820s（首次 503→200，命令→200 2.127s）；完整数据见
[M7 性能报告](../milestones/M7-performance-report.md)。

## 14. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-12（T29） | 初版：部署、索引、备份/恢复、回滚、日常检查 |
| 2026-11-12（T30） | 增补 §10 监控/日志/限流/安全观测 |
| 2026-11-12（T31） | 增补发布记录/清单与 `make release-verify`（§2.4）、配置矩阵（§1.1）、事故第一响应与 worker 运维（§8.2）、命令索引（§12）；核对全部 `make` 命令存在 |
| 2026-11-13（T37） | 发布 v0.1.1（`api-3a04952`，索引 `idx-20261113T025706Z-f7974a6a`）；§2.3 补索引文本形态/内容身份/指针备份与 `make index-activate`（§2.3.1）；§7 回滚演练补索引指针步骤与镜像/索引配对约束 |
| 2026-11-13（T38） | 新增 §13：10 并发压测、24h 探测启停、受控 API/DB 重启演练与任务不变量核对；§14 为原变更记录 |
