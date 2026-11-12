# 部署与运维手册（M7-01 / M7-05，T29 / #30）

本手册是 Ubuntu 服务器上 API 部署、索引重建、备份恢复与回滚的**可执行步骤**。
所有命令都在 Mac 的仓库根执行（除标注“服务器”的只读检查）；**不包含任何密码、
密钥或连接串**。部署形态是个人项目的生产就绪演练，不是任何机构的正式上线。

## 1. 形态与约束

| 项 | 值 |
|---|---|
| 服务器 | `my-ubuntu-server`（`jack@192.168.1.27`；有线 `192.168.5.10`） |
| 项目根 | `~/workspace/bank-assistant/`（`repo`、`data`、`artifacts`、`backups`、`deploy`） |
| 数据库 | 已有 PostgreSQL 18 容器 `bank-assistant-db-db-1`，只绑 `127.0.0.1:5432` |
| API 容器 | `bank-assistant-api`（compose 项目 `bank-assistant-app`） |
| API 端口 | **只绑服务器回环** `127.0.0.1:8000`；外部访问走 SSH |
| 镜像 | `bank-assistant:api-<code_sha7>`，不可变；服务器不重建源码 |
| 配置 | 仓库外 `~/workspace/bank-assistant/.env.app`（chmod 600），模板 `deploy/env.app.example` |
| compose | 仓库 `deploy/compose.app.yml`；服务器运行副本 `~/workspace/bank-assistant/deploy/compose.app.yml` |
| 索引/模型缓存 | `~/workspace/bank-assistant/data/{index,models}`（可重建，不随镜像发布） |
| 备份 | `~/workspace/bank-assistant/backups/<UTC timestamp>/` |
| 部署状态 | `~/workspace/bank-assistant/deploy/state.env`（当前/上一版镜像与 SHA，非秘密） |

**不做的事**：不 push、不在服务器保存 GitHub 写凭据、不把端口映射到 `0.0.0.0`、
不重启服务器整机、不触碰服务器上的 `dingtalk-*` 容器、不降数据库 schema。

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
gh issue view 30          # 确认本票验收
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
calibration_vector_sha256=...
embedding_model_version=BAAI/bge-small-zh-v1.5@512
chunk_count=...
```

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
- `docker port` 与 `ss -tln` 都只出现 `127.0.0.1:8000`。

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
make rollback
```

步骤：读取 `state.env` 的上一版 → 用**上一版不可变镜像** `up -d --wait` →
校验 `/health/live`、`/health/ready`、版本页 `code_sha`=上一版 SHA →
再切回当前镜像并校验。全程不重启服务器、不降 schema、不动数据库容器。

- 上一版镜像必须仍在服务器上（部署脚本保留 tag，不自动清理）；
- 若 `state.env` 没有上一版（首次部署），用 `BACK_TO=<镜像> make rollback` 指定；
- 回滚后 `state.env` 不改写：下一次 `make deploy` 仍会记录正确的回滚点。

## 8. 故障排查

| 现象 | 处理 |
|---|---|
| `make deploy` 报工作树不干净 | 先提交；部署只允许提交，脏树会让镜像 SHA 不可信 |
| `test -f .env.app` 失败 | 按 §2.1 创建，chmod 600；不要从仓库复制真实值 |
| `compose up --wait` 超时 | `ssh my-ubuntu-server 'docker logs --tail 100 bank-assistant-api'` 看启动错误 |
| `/health/ready` 503 | 数据库容器未起/凭据不对：`docker ps`、`docker inspect bank-assistant-db-db-1` |
| `/system/version` 的 `index_version` 为 null | 没建索引：`make deploy-index` |
| 索引构建报 embedding 下载失败 | 服务器网络受限时重试；模型缓存在 `data/models`，成功后不重复下载 |
| 端口出现在 `0.0.0.0` | 立即 `docker compose -f ~/workspace/bank-assistant/deploy/compose.app.yml down`，检查 compose 是否被改 |
| 回滚后旧版启动失败 | 旧镜像与当前 schema 不兼容：先恢复到当前镜像，再评估兼容性（不要降 schema） |

## 9. 日常检查命令

```bash
make sync-check                                        # 两端 SHA 与未提交状态
make verify-deploy                                     # live/ready/版本页/回环端口
ssh my-ubuntu-server 'docker ps --filter name=bank-assistant --format "{{.Names}} {{.Status}} {{.Ports}}"'
ssh my-ubuntu-server 'docker inspect -f "{{.State.Health.Status}}" bank-assistant-api'
ssh my-ubuntu-server 'ss -tln | grep -E ":(8000|5432) "'
ssh my-ubuntu-server 'du -sh ~/workspace/bank-assistant/{data,backups}'
```

## 10. 与 ADR 的关系

- ADR-0002：PostgreSQL 是事实源，Chroma 索引可重建；备份校验以数据库哈希为准。
- ADR-0003：服务器只读拉取；部署用 `git bundle` 精确对齐 SHA，不需要服务器写凭据。
- ADR-0006：数据库只在服务器回环；Mac 开发用 SSH 隧道，部署时同机直连。
