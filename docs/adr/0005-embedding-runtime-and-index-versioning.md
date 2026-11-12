# embedding 用 fastembed + bge-small-zh-v1.5；索引按版本构建并原子切换

状态：accepted（2026-11-12）

ADR-0004 已决定 embedding 自托管 BGE，并用校准向量哈希自证索引身份。本 ADR
补充运行时选型、索引版本化与切换方式。

## 决定

1. **运行时用 `fastembed`**（ONNX Runtime，CPU 推理，**不依赖 torch**），模型
   `BAAI/bge-small-zh-v1.5`（512 维）。理由是两台机器都能装、依赖可锁定、不需要
   CUDA/torch；`bge-m3`（1024 维）保留为 M6 对照，换模型必须新建 `index_version`。
2. **离线实现 `FakeEmbeddingProvider`**：由 `sha256(model_version, text)` 派生确定性
   向量，不联网、不下载模型。单元测试与无模型环境的重建演练只用它；真实
   fastembed 路径保留一个显式冒烟测试，模型/网络不可用时如实 SKIP，不伪造成功。
3. **索引版本化**：每次构建生成新的 `index_version`，Chroma 集合命名为
   `knowledge_<index_version>`；清单写入
   `data/index/manifests/<index_version>.json`，记录 `index_version`、
   `embedding_model_version`、知识快照（`version_id` + `content_hash`）、
   校准向量哈希与创建时间。
4. **活动指针用文件** `data/index/active.json`（二选一中的文件方案）。写入用
   同目录临时文件 + `os.replace` 原子替换并 fsync；构建失败时删除临时集合、
   回滚 `knowledge_chunks` 行、保留旧清单与旧指针，并抛出错误。选择文件而不是
   DB 行：单机单部署下读路径不需要数据库，M7 需要时再迁移。
5. **校准**：固定校准串 → 向量 → IEEE-754 双精度大端序打包 → SHA-256，写入清单与
   指针；加载索引前用当前 provider 重算，不一致即 `EmbeddingDrift`，拒绝使用并要求
   重建。哈希只证明向量未漂移，不证明检索质量；质量仍由 M6 评测。
6. **分块落库**：`knowledge_chunks` 记录 `chunk_id`、`version_id`、`text`、码点区间
   `start_offset/end_offset`、`embedding_model_version`、`index_version`；每个索引
   版本的分块独立保存，重建不覆盖旧索引，失败可回退。

## 考虑过的方案

- **torch + sentence-transformers**：依赖重（数百 MB）、两机安装成本高，且
  Chroma 已依赖 onnxruntime，等于同一能力装两套。
- **第三方中继 embedding**：身份不可验证，运营方换模型会让索引无声劣化
  （ADR-0004 已否决）。
- **索引原地覆盖**：构建失败就没有旧索引可用，违反契约 §3「失败保留旧版本并
  报告错误」。
- **活动指针存数据库行**：需要新迁移，且检索读路径多一个 DB 依赖；文件指针在
  当前单机部署足够，原子替换也简单。

## 后果

- 首次真实重建需要下载模型（约 90MB，缓存在 fastembed 默认目录）；离线环境
  只能跑 fake 索引，报告必须注明「fake / 未验证真实语义质量」。
- 维度固定 512；`bge-m3` 等不同维度模型必须新建索引版本并重新校准。
- `data/index/` 已加入 `.gitignore`：索引可由知识库重建，但备份/迁移时要同时
  保留清单与 `active.json`，否则活动索引会指向不存在的集合。
- 知识状态/有效期过滤在 SQL 层完成（复用 M2 的 `retrievable_versions_query`），
  索引只包含构建时刻可检索的版本；退役或过期后仍需在返回候选前复核数据库状态。
