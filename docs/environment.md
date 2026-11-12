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
| 数据库客户端 | PostgreSQL 18.3（Homebrew） |
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
| 数据目录 | `~/bank-assistant-data`、`~/bank-assistant-artifacts`、`~/bank-assistant-backups`（已创建，可写） |
| 项目 checkout | `~/projects/agent_study_workspace`（HTTPS clone，按 SHA 检出） |

## 数据与产物目录

| 位置 | 路径 | 说明 |
|---|---|---|
| 本机 | `.local-data/`、`.local-artifacts/`、`.local-backups/`（仓库内，已 gitignore） | 只放小型合成夹具与冒烟产物；本机沙箱不允许在仓库外写文件 |
| Ubuntu | `~/bank-assistant-data/`、`~/bank-assistant-artifacts/`、`~/bank-assistant-backups/` | 数据集、评测产物、模型权重、备份（仓库外，不进 Git） |

## 端口约定

| 端口 | 用途 | 本机 | Ubuntu |
|---|---|---|---|
| 8000 | API | 空闲 | 空闲 |
| 5173 | Vite 开发服务器（M5） | 空闲 | 空闲（不在此构建） |
| 5432 | PostgreSQL | 空闲（M2 用容器） | 空闲（M2 用容器） |

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
