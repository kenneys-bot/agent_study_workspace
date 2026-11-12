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

## 数据库（唯一实例在 Ubuntu，ADR-0006）

| 项 | 内容 |
|---|---|
| 部署位置 | **仅 Ubuntu**：PostgreSQL 18 容器（`deploy/compose.db.yml`），宿主端口绑定 `127.0.0.1:5432`（仅回环，不暴露到局域网/公网） |
| Mac | 不安装、不创建、不启动本地 PostgreSQL；只保留可选的 `psql` 客户端 / DBX 等查看工具 |
| 访问方式 | SSH 本地端口转发：`ssh -N -L 15432:127.0.0.1:5432 my-ubuntu-server` |
| 连接参数 | 主机 `127.0.0.1`、端口 `15432`、`jdbc`/libpq 均可；DBX 填同样参数。**`localhost` 指 Mac 自己**，未建转发时连不上 |
| 连接串 | `postgresql+psycopg://<user>:<password>@127.0.0.1:15432/<database>` |

数据库按用途隔离（同一实例）：

| 数据库 | 用途 | 允许清空 |
|---|---|---|
| `bank_assistant_dev` | 日常开发 | 开发者显式命令 |
| `bank_assistant_demo` | 演示数据（`make seed-demo`） | 仅 seed 幂等写入，自动化测试不得清空 |
| `bank_assistant_test` | 集成测试 | 测试脚本 |
| `bank_assistant_restore_check` | 备份恢复验证目标 | 恢复演练 |

服务器或隧道不可达时：单元测试与前端可继续；集成测试、迁移、seed、备份恢复必须**显式失败或标记 BLOCKED**，不得静默改用 SQLite/mock 并宣称验收通过。

## 端口约定

| 端口 | 用途 | 本机 | Ubuntu |
|---|---|---|---|
| 8000 | API | 空闲 | 空闲 |
| 5173 | Vite 开发服务器（M5） | 空闲 | 空闲（不在此构建） |
| 5432 | PostgreSQL（服务器回环） | **不使用**（Mac 不部署数据库） | 容器绑定 `127.0.0.1:5432`，不对外 |
| 15432 | SSH 本地转发端口 | 需要数据库时占用 | — |

## 凭据与配置

- `.env`（本机，已 gitignore，权限 600）：DeepSeek 官方 key、第三方中继 key、模型标识、预算上限。
- `.env.example`：只有占位值，随仓库提交。
- Ubuntu 上的 `.env` 由负责人另行放置，**不进 Git**；服务器不持有 GitHub 写凭据。
- 本文件与任何提交中**不得出现**密钥、令牌、sudo 密码。

## 已知环境限制

1. Ubuntu GPU 与另一训练任务共享，微调/推理必须等空闲窗口（ADR-0004）。
2. Ubuntu 无 node，前端构建只在 Mac 或容器内做。
3. pypi 慢 → Ubuntu 上安装依赖使用 uv 缓存并避免反复重建；Docker 拉镜像预留时间。
4. 重启 Ubuntu 整机需要负责人逐次准许（本人日常使用的桌面机）。
5. 数据库只在 Ubuntu：Ubuntu 或 SSH 隧道不可达时，集成测试/迁移/seed/备份恢复全部受阻（单元测试不受影响）。他人复现本项目需要自备服务器与隧道。
