# 环境清单（两端实测，2026-11-12）

本文件记录**实测到的事实**，不是计划。凭据一律不写入本文件。

## 本机（Mac，唯一推送方）

| 项 | 实测值 |
|---|---|
| 工作目录 | `/Users/lww/workspace/agent_study_workspace` |
| 系统 | macOS（Apple Silicon），时区 Asia/Shanghai |
| Python | 3.13.12（Homebrew `/opt/homebrew/opt/python@3.13`） |
| 依赖管理 | uv 0.10.11；`pyproject.toml` + `uv.lock` 为唯一入口 |
| 容器 | Docker 28.5.2、Docker Compose v2.40.3 |
| 数据库客户端 | PostgreSQL 18.3（Homebrew，**仅客户端**；本机不部署 PostgreSQL 服务、不跑数据库容器） |
| 前端工具链 | Node 25.7.0 / npm（**无 pnpm**） |
| 其它 | make、git、gh 2.92.0（已登录 kenneys-bot） |
| 既有虚拟环境 | 仓库根 `.venv`（uv 管理，Python 3.13） |

## Ubuntu（评测/数据/部署演练，`my-ubuntu-server`）

SSH 别名 `my-ubuntu-server`（Wi-Fi `192.168.1.27`，有线 `192.168.5.10`，与本机 `192.168.5.32` 同网段），用户 `jack`，密钥登录。

| 项 | 实测值 |
|---|---|
| 主机 | `jack-MS-7C94`，Ubuntu 24.04.3 LTS（noble），内核 7.0.0-38，x86_64 |
| CPU / RAM | AMD Ryzen 7 5800X3D 8C/16T；62 GiB RAM，8 GiB swap |
| GPU | RTX 3060 Ti 8 GB，驱动 580.178.04，CUDA 13.0；**常态被 `dingtalk-train` 占用**，本项目视为分时借用 |
| 磁盘 | `/` 327 G ext4，实测剩余 149 G（`/home` 未单独挂载） |
| 容器 | Docker 29.0.1 + Compose v2.40.3，`jack` 在 docker 组（免 sudo） |
| Python | 3.12.3（/usr/bin）；本项目用 3.13 venv，由 uv 管理（uv 需在 M0-03 安装或使用 uvx） |
| 前端 | **无 node/npm/pnpm** → 前端在 Mac 或容器内构建（ADR-0003） |
| 网络 | 出口经 Mihomo/Clash TUN；GitHub HTTPS 可用（约 2.7 s），**SSH 推送不可用**；pypi 索引约 9 s（慢，安装需批量 + 缓存） |
| 时区 | Asia/Shanghai，NTP 同步 |
| 项目根目录 | `~/workspace/bank-assistant/`（本项目在服务器上的唯一目录，下面全部内容都在这里） |
| 项目 checkout | `~/workspace/bank-assistant/repo`（HTTPS clone，按 SHA 检出） |
| 数据/产物/备份 | `~/workspace/bank-assistant/{data,artifacts,backups}`（已创建，可写） |

## 数据与产物目录

| 位置 | 路径 | 说明 |
|---|---|---|
| 本机 | `.local-data/`、`.local-artifacts/`、`.local-backups/`（仓库内，已 gitignore） | 只放小型合成夹具与冒烟产物；本机沙箱不允许在仓库外写文件 |
| Ubuntu | `~/workspace/bank-assistant/{data,artifacts,backups}/` | 数据集、评测产物、模型权重、备份（仓库外，不进 Git）；本项目在服务器上只占 `~/workspace/bank-assistant/` 一个目录，与其它项目（如 `dingtalk-project-assistant/`）平级 |

## 数据库（唯一实例在 Ubuntu，ADR-0006；2026-11-12 已部署）

| 项 | 内容 |
|---|---|
| 部署位置 | **仅 Ubuntu**：PostgreSQL 18（`postgres:18-alpine`），compose 项目 `bank-assistant-db`，容器 `bank-assistant-db-db-1`（healthy） |
| 宿主绑定 | `127.0.0.1:5432`（仅回环；`docker port` = `5432/tcp -> 127.0.0.1:5432`，`ss -tln` 只有 `127.0.0.1:5432`） |
| 数据目录 | `~/workspace/bank-assistant/data/pg`（bind mount 到容器 `/var/lib/postgresql`，PGDATA 在 `/var/lib/postgresql/18/docker`；属主 uid 70） |
| 凭据文件 | 服务器 `~/workspace/bank-assistant/.env.pg`（仓库外，chmod 600）；Mac `.env`（gitignore，600）。**本文件不记录密码** |
| compose 文件 | 源文件 `deploy/compose.db.yml`；服务器运行副本 `~/workspace/bank-assistant/deploy/compose.db.yml`（服务器 checkout 保持干净） |
| 角色 | `bank`（该实例的管理角色）；四个库 owner 均为 `bank` |
| Mac | 不安装、不创建、不启动本地 PostgreSQL；保留 `psql` 18.3 客户端仅用于查看 |
| 访问方式 | SSH 本地端口转发：`make db-tunnel`（等价 `ssh -N -L 15432:127.0.0.1:5432 my-ubuntu-server`） |
| 连接参数 | 主机 `127.0.0.1`、端口 `15432`、DBX/libpq 同参数。**`localhost` 指 Mac 自己**，未建转发时连不上 |
| 连接串 | `postgresql+psycopg://bank:<password>@127.0.0.1:15432/bank_assistant_dev` |

数据库按用途隔离（同一实例）：

| 数据库 | 用途 | 允许清空 |
|---|---|---|
| `bank_assistant_dev` | 日常开发 | 开发者显式命令 |
| `bank_assistant_demo` | 演示数据（`make seed-demo`） | 仅 seed 幂等写入，自动化测试不得清空 |
| `bank_assistant_test` | 集成测试 | 测试脚本（每次只重建 `public` schema） |
| `bank_assistant_restore_check` | 备份恢复验证目标 | 恢复演练 |

常用命令（Mac）：

```bash
make db-tunnel           # 建立隧道（幂等）；db-tunnel-status / db-tunnel-stop
make doctor              # 隧道 + bank_assistant_test SELECT 1；隧道缺失非零退出
make test-integration    # 重建 *_test schema → alembic upgrade head → pytest -m integration
make seed-demo           # 经隧道写入 bank_assistant_demo（幂等，拒绝非 *_demo）
make test-unit           # 无数据库依赖
```

服务器维护命令（只读/本容器，不触碰其它项目）：

```bash
cd ~/workspace/bank-assistant
docker compose -p bank-assistant-db --env-file .env.pg -f deploy/compose.db.yml ps
docker compose -p bank-assistant-db --env-file .env.pg -f deploy/compose.db.yml exec db pg_isready -U bank -d bank_assistant_dev
docker inspect -f '{{.State.Health.Status}}' bank-assistant-db-db-1
```

隔离实测（2026-11-12）：Mac 直连 `192.168.5.10:5432` 与服务器自连其两块网卡 IP 均 `Connection refused`；服务器只监听 `127.0.0.1:5432`。Wi-Fi IP `192.168.1.27` 在 Mac 上会被 Clash/Mihomo TUN 接管，不能作为 LAN 直连判据，以有线 `192.168.5.10` 与服务器侧 `ss` 为准。

服务器或隧道不可达时：单元测试与前端可继续；集成测试、迁移、seed、备份恢复必须**显式失败或标记 BLOCKED**，不得静默改用 SQLite/mock 并宣称验收通过。

## API 部署（M7-01 / M7-05 / v0.1.0，2026-11-12 已执行）

| 项 | 实测值 |
|---|---|
| 容器 | `bank-assistant-api`（compose 项目 `bank-assistant-app`），`healthy`，`restart=unless-stopped` |
| 镜像 | `bank-assistant:api-<code_sha7>`（不可变；当前发布 `api-59a5869`，`linux/amd64`）；服务器不重建源码 |
| 网络/端口 | `network_mode=host` + uvicorn `--host 127.0.0.1`；`ss -tln` 仅 `127.0.0.1:8000`，不写 `ports:` |
| 配置 | 仓库外 `~/workspace/bank-assistant/.env.app`（chmod 600）；模板 `deploy/env.app.example` |
| 数据/产物 | `~/workspace/bank-assistant/{data,artifacts}`；索引 `data/index`、模型缓存 `data/models` |
| 部署状态 | `~/workspace/bank-assistant/deploy/state.env`（当前/上一版镜像与 SHA，非秘密） |
| 部署/回滚 | `make deploy` / `make rollback`；上一版镜像保留在服务器，供回滚演练 |
| 索引 | `make deploy-index`（一次性容器，服务器侧重建）；活动版本见 `/api/v1/system/version` |
| 备份/恢复 | `make backup` → `backups/<UTC>/`；`make restore-check` 只写 `*_restore_check` |
| 运维手册 | `docs/ops/runbook.md`；演练报告 `docs/milestones/M7-deploy-report.md` |

## 端口约定

| 端口 | 用途 | 本机 | Ubuntu |
|---|---|---|---|
| 8000 | API | 开发用（`make up`，回环） | `127.0.0.1:8000`（API 容器，仅回环） |
| 5173 | Vite 开发服务器（M5） | 空闲 | 空闲（不在此构建） |
| 5432 | PostgreSQL（服务器回环） | **不使用**（Mac 不部署数据库） | 容器绑定 `127.0.0.1:5432`，不对外 |
| 15432 | SSH 本地转发端口 | 需要数据库时占用 | — |

## 凭据与配置

- `.env`（本机，已 gitignore，权限 600）：DeepSeek 官方 key、第三方中继 key、模型标识、预算上限、`DATABASE_URL`（隧道入口 + 数据库密码）。
- `.env.example`：只有占位值，随仓库提交（`DATABASE_URL` 的密码是 `<password>` 占位）。
- Ubuntu 上的 `.env` 由负责人另行放置，**不进 Git**；数据库密码另存 `~/workspace/bank-assistant/.env.pg`（仓库外，chmod 600），compose 用 `--env-file` 读取。
- 服务器不持有 GitHub 写凭据。
- 本文件与任何提交中**不得出现**密钥、令牌、sudo 密码、数据库密码。

## 已知环境限制

1. Ubuntu GPU 与另一训练任务共享，微调/推理必须等空闲窗口（ADR-0004）。
2. Ubuntu 无 node，前端构建只在 Mac 或容器内做。
3. pypi 慢 → Ubuntu 上安装依赖使用 uv 缓存并避免反复重建；Docker 拉镜像预留时间。
4. 重启 Ubuntu 整机需要负责人逐次准许（本人日常使用的桌面机）。
5. 数据库只在 Ubuntu：Ubuntu 或 SSH 隧道不可达时，集成测试/迁移/seed/备份恢复全部受阻（单元测试不受影响）。他人复现本项目需要自备服务器与隧道。
