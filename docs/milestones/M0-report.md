# M0 验收报告：基线与双机环境

> 历史注记（2026-11-12 追加）：本报告成文时数据库方案是「Mac 本地 Docker PG + 两机同版本」（ADR-0002）。该部署方式已被 [ADR-0006](../adr/0006-postgres-on-ubuntu-only.md) 取代：PostgreSQL 仅部署在 Ubuntu，Mac 经 SSH 隧道访问。报告内容保留原样作为历史证据，不回填改写。

状态：READY_FOR_ACCEPTANCE
实现代码SHA：`979b076e0f472790a10cd85af60b1f397f706675`（分支 `feat/bank-assistant`）
本机/Ubuntu一致性：一致（见 M0-03 证据）
相关需求：FR-10（Ubuntu 部署、代码一致性）；计划书 v1.1 M0

## 交付内容与文件

| 文件 | 内容 |
|---|---|
| `pyproject.toml`、`uv.lock` | 唯一依赖入口（uv 0.10.11，Python 3.13，锁文件 724 KB） |
| `Makefile` | 计划全部目标命令；未实现者非零退出并标注所属里程碑 |
| `scripts/doctor.sh` | 依赖/配置/端口/目录检查，不打印秘密值 |
| `scripts/sync-check.sh` | 本机与 Ubuntu 的 SHA 与未提交状态，只读 |
| `scripts/publish-tickets.py` | 把 31 张执行切片发布为 GitHub Issues（含 46 条依赖边） |
| `.env.example`、`.gitignore` | 配置模板（仅占位）；`.env`/`.venv`/`.uv-cache`/`.worktrees`/运行产物不入库 |
| `docs/environment.md` | 两端实测环境清单 |
| `docs/implementation-status.md` | 唯一进度状态源 |
| `docs/adr/0001..0004` | 仓库布局、数据库与索引、双机同步、模型与微调 |
| `src/bank_assistant/`、`tests/` | 产品根布局；学习内容归档到 `archive/` |

## 验收逐条结果

| 验收项 | 结果 | 执行命令 / 证据 |
|---|---|---|
| 两端报告同一 SHA | PASS | 本机 `979b076e…`；Ubuntu `979b076e…`（`make sync-check` 输出「两端 SHA 一致」） |
| 没有覆盖现存改动 | PASS | 用户未提交的 `constants.py` 重命名按原样单独提交（`1524ee7`），未 reset、未改写 |
| 凭据不在 Git | PASS | `git check-ignore -v .env` 命中；全仓 grep 两把 key 仅存在于本地 `.env`（600 权限）；`git status` 不含 `.env` |
| Ubuntu 数据目录可读写 | PASS | `~/bank-assistant-{data,artifacts,backups}` → `WRITABLE`（2026-11-12 起迁移到 `~/workspace/bank-assistant-*`，见 `docs/environment.md`）；`/` 剩 149 G。本机改用仓库内忽略目录 `.local-{data,artifacts,backups}`（沙箱禁止写仓库外） |
| 未知条件有负责人与影响范围 | PASS | `docs/environment.md`「已知环境限制」4 条（GPU 分时、无 node、pypi 慢、重启需逐次准许） |
| 锁定依赖方案 | PASS | `uv sync --locked` 成功；两份历史 `requirements.txt` 已合并后删除 |
| 统一运行入口 | PASS | `make doctor` 通过；README 命令与 Makefile 一致 |
| 状态与 ADR 文档 | PASS | `docs/implementation-status.md`；`docs/adr/` 4 篇 |
| 单元测试可运行 | PASS（范围内） | `uv run pytest tests -q`：无 ImportError/ModuleNotFoundError；剩余 2 个失败映射 #4、#9 |
| 未实现命令不假装成功 | PASS | `make smoke/eval/up` 等非零退出并打印所属里程碑 |

## 指标与样本量

- 依赖锁文件：`uv.lock` 724 KB。
- 单元测试：收集 35 个函数，剩余失败 2 个（均为行为缺陷，已在 issue 中跟踪）；原 14 个「缺依赖」失败已消除。
- 服务器：8C/16T、62 GiB、RTX 3060 Ti 8 GB、149 G 空闲、Docker 免 sudo。

## 失败、跳过、环境限制

- `make test-integration` / `test-e2e` / `smoke` 尚未实现（分别属 M2/M5/M1，非 M0 范围），当前按设计非零退出。
- 真实云模型链路未验证（属 M1，#6）。
- 前端未开始（M5）。
- Ubuntu 无 node，前端构建位置已记录在 ADR-0003。

## 数据/模型/知识/规则/索引版本

M0 无数据、无索引、无规则。模型选择已记录于 ADR-0004：生成用 DeepSeek 官方 `deepseek-chat`，embedding 自托管 `BAAI/bge-m3`（M3 落地）。

## 兼容性与迁移

- Git 历史完整保留：`archive/` 内所有文件均为 `git mv`（rename），原文可追溯。
- 移除 18 个历史上误提交的构建产物（`__pycache__`、`egg-info`、langgraph checkpoint）出索引，文件仍保留在 `archive/` 工作区并已 gitignore。
- 前端、数据库、迁移尚未引入，无迁移风险。

## 遗留风险和外部依赖

1. Ubuntu 是负责人日常桌面机，GPU 与另一训练任务共享 → M6 微调前需确认空闲窗口。
2. `uv` 在 Ubuntu 上尚未安装（M0 只需 fetch/检出，实际跑测试前需装 uv 或使用 `uvx`）——列入 M1 前置。
3. 两把模型 key 已在会话中出现，建议项目跑通后轮换（ADR-0004）。
4. 仓库为公开仓库：issue 与文档中已约定不出现凭据、服务器地址统一写 `my-ubuntu-server`。

## 下一阶段入口

M1（#4/#5/#6/#7/#8/#9 为当前 frontier），集成分支 `feat/bank-assistant`。

## 负责人验收

待负责人填写结果与时间；执行模型不代填。
