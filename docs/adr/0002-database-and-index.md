# PostgreSQL 是业务事实源，Chroma 只是可重建索引

状态：superseded（自 2026-11-12 起，**数据库部署位置部分由 [ADR-0006](0006-postgres-on-ubuntu-only.md) 取代**；「PostgreSQL 是业务事实源、Chroma 只是可重建索引、SQLite 只做单元测试替身」仍然有效）

契约要求验证幂等（CT-05/CT-06）、两人并发复核冲突（CT-07）、重启后记录一致、备份恢复后关键哈希一致。这些在 SQLite 上要么测不出来，要么测出来的结果不能当证据。决定：PostgreSQL 18 是唯一业务事实源，**只部署在 Ubuntu**（Mac 不安装、不启动本地 PostgreSQL，经 SSH 隧道访问，见 ADR-0006）；SQLite 只作为单元测试替身，不承载任何验收场景。Chroma 只作可重建的向量索引，索引构建用版本化集合 + 活动指针切换，重建失败保留旧版本并报错。

## 考虑过的方案

- **全程 SQLite**（现状分析建议的轻量路线）：省事，但 M2/M4/M7 的并发、恢复、备份验收全部失去意义——等于把最想证明的工程能力删掉。
- **SQLite 起步、M4 再迁**：迁移成本没有省掉，只是推迟，并叠加在质检闭环的关键路径上。

## 后果

- `make test-integration` 依赖 **SSH 隧道 + Ubuntu 上的 PostgreSQL**（不再启动本机容器）；`make test-unit` 不依赖数据库。
- 数据库版本只有一处（18.x），写入 `docs/environment.md`；备份/恢复演练只允许在独立目标库（`*_test` / `*_restore_check`）做，不碰演示库。
- 索引必须能从已发布知识版本完整重建，禁止把 Chroma 当事实源；embedding 模型变更必须新建索引版本。
