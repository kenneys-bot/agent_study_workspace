# M7 部署报告：不可变镜像、备份恢复与回滚演练（T29 / #30）

日期：2026-11-12（Asia/Shanghai）  
分支：`ticket/T29`（基于 `feat/bank-assistant`）  
范围：M7-01（锁定提交/镜像部署、配置与数据分离、进程自动恢复、依赖 readiness、
迁移前备份、固定推理/embedding/索引版本）与 M7-05（备份恢复演练、应用回滚、
索引重建）。M7-02/03/04/06 不在本票范围。

## 1. 验收对照（#30）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 备份恢复后记录数与关键哈希一致 | **PASS** | 备份 `20261112T110714Z`；dev/demo 各 21 张表行数一致；demo `knowledge_versions` 32 行、`max_content_hash=fea41b4d…`、聚合 md5=`de01cec6…`；`conversations` 6 行 md5=`aa1eb51f…`；alembic 均为 `0011_report_exports` |
| 回滚演练成功 | **PASS** | `api-f4f1346` → 上一版 `api-54fa344`（`/health/live`、`/health/ready` 200，版本页 `code_sha=54fa344…`）→ 恢复 `api-f4f1346`（版本页 `code_sha=f4f1346…`），未降 schema、未重启服务器 |
| 索引可重建 | **PASS** | 服务器用已部署镜像重建：`idx-20261112T110703Z-2334d03e`，30 版本/30 分块，校准 sha256 `136e030a…`，embedding `BAAI/bge-small-zh-v1.5@512`；`/api/v1/system/version` 返回同值 |

## 2. 部署身份（不可变）

| 项 | 实际值 |
|---|---|
| 代码 SHA / 分支 | `f4f1346427b72b940afdaabf60b5fd31cdc2fe97` / `ticket/T29` |
| 镜像 | `bank-assistant:api-f4f1346`，id `sha256:30d11a865f3f6801e2293a709a9a5c06f9fbe4ac5da8fe19aecc3a5f8baf6fcf`，`linux/amd64`，210,998,212 B |
| 传输校验 | `docker save | ssh docker load`，tar sha256 `27cb18c2892674b46d6fb13d84b6421420a5b2ba90fd5560ca15bbca6022a6fd`；两端 image id 相同 |
| 服务器 checkout | `~/workspace/bank-assistant/repo` 重置到同一 SHA，工作树干净（`git status --porcelain` 为空） |
| 容器 | `bank-assistant-api`，`restart=unless-stopped`，health `healthy`，`network_mode=host` |
| 端口 | 服务器 `ss -tln` 仅 `127.0.0.1:8000`（应用 `--host 127.0.0.1`）与 `127.0.0.1:5432`（PG） |
| 配置分离 | 仓库外 `~/workspace/bank-assistant/.env.app`（chmod 600）；镜像/仓库内无真实凭据 |
| 数据/产物 | 挂载 `~/workspace/bank-assistant/{data,artifacts}`；`data/{index,models,home}` 持久化 |
| 服务器重建源码 | **未发生**：服务器上没有 pypi 安装、没有 `docker build`，只用已传输镜像 |

说明：容器使用 host 网络是刻意的——`DATABASE_URL` 按约定为 `127.0.0.1:5432`，
bridge 网络里的 `127.0.0.1` 是容器自己，连不到宿主 PostgreSQL。端口隔离改由
uvicorn `--host 127.0.0.1` 保证（compose 不写 `ports:`），`ss` 与 Mac 侧直连
都验证过没有 LAN 暴露。

## 3. 索引与模型固定

| 项 | 实际值 |
|---|---|
| 活动 `index_version` | `idx-20261112T110703Z-2334d03e` |
| 校准向量 sha256 | `136e030a6da8397756dd2d2547891ee66797d595a9b61d2d14611f89659c4004` |
| 索引 embedding 模型 | `BAAI/bge-small-zh-v1.5@512`（fastembed，ONNX CPU） |
| 知识快照 / 分块 | 30 个已发布版本 / 30 个分块（DB `knowledge_chunks` 校验 30 行） |
| 模型 provider / chat | `deepseek` / `deepseek-chat`；`MODEL_MODE=live` |
| 版本页 | `/api/v1/system/version` 返回上述 `index_version`、`index_calibration_sha256`、`index_embedding_model`、`code_sha` |

重建方式：服务器上 `docker run --rm --network host --user <项目用户> --env-file .env.app
-v repo:/app/repo -v data:/app/data -w /app/repo bank-assistant:api-f4f1346
python scripts/index_rebuild.py`（`make deploy-index`）。模型缓存落在
`data/models`（`models--Qdrant--bge-small-zh-v1.5`），重建不重复下载。

## 4. 迁移前备份与迁移

顺序执行（先备份、后迁移）：

1. `make backup` → `20261112T110515Z`（迁移前快照，dev 61,210 B、demo 89,997 B）；
2. 服务器内一次性容器执行 `alembic upgrade head && alembic current`
   → 两个库均 `0011_report_exports (head)`（本次无新迁移，是 no-op；仍保留迁移前备份）；
3. 部署、建索引；
4. `make backup` → `20261112T110714Z`（恢复演练使用的快照，demo 96,098 B；
   比迁移前多 30 行 `knowledge_chunks`，来自第 3 步建索引）。

## 5. 备份 / 恢复演练

```text
backup_id=20261112T110714Z
code_sha=f4f1346…
databases=bank_assistant_dev bank_assistant_demo
dumps: bank_assistant_dev.dump  sha256 d41b7cd9f5914f4b2590533c8cb9d17d9d4d9b18154927213f3c75d6332277b8
       bank_assistant_demo.dump sha256 539faa59bd9ba2bebbb4eb1b80c36a4976d4747dc2dfdbec4957d9b067cbf578
artifacts=skipped-empty
```

恢复目标**只允许** `*_restore_check`（`^bank_assistant_[a-z0-9_]+_restore_check$`），
且拒绝与源库同名；源库只读。本次把 `bank_assistant_{dev,demo}` 分别恢复到
`bank_assistant_{dev,demo}_restore_check`，结果：

| 校验 | demo | dev |
|---|---|---|
| 全表行数（21 张） | 一致 | 一致 |
| `alembic_version` | `0011_report_exports` | `0011_report_exports` |
| `knowledge_versions` | 32 行；max `fea41b4d…`；md5 `de01cec6…` | 0 行 |
| `conversations` | 6 行；max `e0dcd6b3…`；md5 `aa1eb51f…` | 0 行 |
| `rule_versions` | 6 行；max `fb4f6988…`；md5 `b8cc5d18…` | 0 行 |
| `report_exports` | 0 行 | 0 行 |

`RESULT: PASS（backup=20261112T110714Z，2 个库恢复到 *_restore_check 并核对一致）`。
恢复前后另校验“源库自备份后未变化”，因此比较的是同一时点快照，不是旧备份对当前源。
守卫反向验证：`require_restore_target bank_assistant_demo bank_assistant_demo`
非零退出并打印拒绝原因。

## 6. 回滚演练

上一版保留为 `bank-assistant:api-54fa344`（id `sha256:e3c642e5…`，即 T29 之前的
集成分支提交），当前版 `api-f4f1346`。演练命令：

```bash
BACK_TO=bank-assistant:api-54fa344 BACK_TO_SHA=54fa344a151c314432f300c40c678aa7ad6a31e9 make rollback
```

过程：回滚前记录 alembic head → 用旧镜像 `up -d --wait` → 校验 live/ready/版本页
`code_sha=54fa344…`（旧版没有新增的索引校准字段，按预期不要求）→ 再切回
`api-f4f1346` → 校验 `code_sha=f4f1346…` 与索引身份。全程未重启服务器、
未执行 `alembic downgrade`、未停止数据库容器。旧版与当前 schema（0011）兼容。

## 7. 代码回归

| 命令 | 结果 |
|---|---|
| `uv run pytest tests -q`（默认排除 integration，合并 `feat/bank-assistant` 后） | **621 passed, 130 deselected** |
| `make test-integration`（隧道 + `bank_assistant_test` 空库迁移，合并后） | **130 passed**，alembic `0011_report_exports (head)` |

新增回归测试：`tests/test_deploy_config.py`（compose 回环/不可变镜像/restart/探针、
恢复闸门只接受 `*_restore_check`）、`tests/test_system_version.py`（索引校准/模型字段）。

## 8. 未执行 / 边界

- 未做 M7-02 可观测性（trace/看板/告警）、M7-03 权限攻防、M7-04 并发与故障注入、
  M7-06 发布包/漏洞清单——不属于本票。
- 未做数据库灾难恢复切换（只做独立库恢复验证）；未重启服务器整机。
- 未在服务器上重新构建镜像/安装 Python 依赖；服务器不使用 `make index-rebuild`
  （那需要宿主 uv），改用一次性容器。
- 未 push、未开 PR；服务器不持有 GitHub 写凭据。
- 备份不包含 Chroma 索引（可重建）与 PG 物理文件；只含逻辑 dump、行数/哈希快照
  与产物 tar（本次产物为空）。
- `make test-e2e` 仍使用 Mac 本机 compose 数据库（T25 遗留路径），不在本票范围。

## 9. 复现与证据位置

- 部署：`make deploy`；索引：`make deploy-index`；备份：`make backup`；
  恢复：`make restore-check`；回滚：`make rollback`；校验：`make verify-deploy`。
- 运维手册：`docs/ops/runbook.md`。
- 服务器证据：`~/workspace/bank-assistant/{backups/<ts>,deploy/state.env,data/index}`。
- 部署状态（非秘密）：`CURRENT_IMAGE=bank-assistant:api-f4f1346`、
  `CURRENT_SHA=f4f1346…`、`PREVIOUS_IMAGE=bank-assistant:api-b294668`。
