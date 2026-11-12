# 依赖漏洞处理记录（v0.1.0 / T31 / #32）

日期：2026-11-12（Asia/Shanghai）｜对象：`v0.1.0` 发布（代码 `59a5869`）
执行环境：macOS（Apple Silicon），Python 3.13.12，uv 0.10.11，Node v25.7.0 / npm 11.10.1。

本文件记录**实际执行**的依赖审计命令与结果。没有运行的检查会明确写「未执行」，
不用「人工看过、应该没问题」替代扫描结论；也不把「扫描通过」说成「无漏洞」。

## 1. 结论摘要

| 范围 | 工具 | 结果 |
|---|---|---|
| Python 运行时依赖（锁定） | `pip-audit 2.10.1`（PyPI 公告库） | **1 个包有 5 条公告记录（4 个唯一 advisory）**：`chromadb 1.5.9`；无修复版本 |
| 前端生产依赖 | `npm audit --omit=dev` | **0 漏洞**（8 个 prod 依赖） |
| 前端全部依赖（含 dev） | `npm audit` | **0 漏洞**（212 个依赖，含 205 dev / 54 optional） |
| 容器 OS 包 / 镜像扫描 | Trivy/Grype 等 | **未执行**（本机未安装；报告不声称镜像 OS 层无漏洞） |
| 本地项目包 `bank-assistant` | pip-audit | 跳过（不在 PyPI；这是预期行为，不代表有/无漏洞） |

**因此不能写「无漏洞」**：Python 侧有 4 个唯一的 ChromaDB 安全公告，且上游尚无修复版本。

## 2. 实际执行的命令

### 2.1 锁定依赖导出（Python）

```bash
export PATH=/Users/lww/.local/bin:$PATH
export UV_CACHE_DIR=<workspace>/.uv-cache

# 锁定环境审计（包含 pip-audit 自身注入的依赖）
uv run --with pip-audit pip-audit \
  --cache-dir "$PWD/.local-data/pip-audit-cache" \
  --progress-spinner off --format=json

# 仅审计 uv.lock 的运行时依赖（排除 dev/train 与本地项目）
uv export --frozen --no-dev --no-hashes --format requirements-txt -o /tmp/bank-req.txt
grep -E '^[A-Za-z0-9_.-]+==' /tmp/bank-req.txt > /tmp/bank-req-clean.txt
uv run --with pip-audit pip-audit -r /tmp/bank-req-clean.txt --no-deps \
  --cache-dir "$PWD/.local-data/pip-audit-cache" \
  --progress-spinner off --format=json
```

说明：沙箱禁止写 `~/Library/Caches/pip-audit`，首次直接运行失败；以上命令通过
`--cache-dir` 指向仓库内可写目录后成功。失败命令与原因保留在此，不隐藏。

### 2.2 前端

```bash
cd web
npm audit --omit=dev --json   # 生产依赖
npm audit --json              # 全部依赖（含 dev）
```

## 3. 结果明细

### 3.1 Python（`uv.lock` 运行时，111 个解析依赖）

```text
deps audited: 111
vulnerable packages: 1
chromadb 1.5.9 — 5 条公告记录（4 个唯一 advisory），fix_versions = []
```

| Advisory | CVE / GHSA | 简述 | 修复版本 |
|---|---|---|---|
| PYSEC-2026-311 | CVE-2026-45829 / GHSA-f4j7-r4q5-qw2c | 未认证代码注入；`/api/v2/.../collections` 配合 `trust_remote_code=true` 可远程执行代码 | 无 |
| PYSEC-2026-3813 | CVE-2026-45830 / GHSA-2wm9-hf6c-p5cr | 已认证用户可跨 tenant 任意读写/更新/删除集合数据 | 无 |
| PYSEC-2026-3814 | CVE-2026-45833 / GHSA-36p7-vc44-83pf | 已认证代码注入（恶意模型仓库 + `trust_remote_code`） | 无 |
| PYSEC-2026-3815 | CVE-2026-45831 / GHSA-xph7-9rjv-w5fr | `SimpleRBACAuthorizationProvider` 不校验权限所属 tenant/database/collection | 无 |

严重度：pip-audit 输出不含数值评分；按 OSV 记录的 CVSS 向量，CVE-2026-45829 为
`AV:N/AC:L/PR:N/UI:N/...` 的未认证 RCE，CVE-2026-45833 为已认证 RCE，均属高危。
（本项目**不自行给出 CVSS 数值**。）

**当前是否可利用**：本项目用 Chroma 的**嵌入式 `PersistentClient`**（进程内、索引目录
`data/index`）做可重建检索索引，**没有启动 Chroma HTTP 服务**，也没有把 `/api/v2`
暴露到回环之外；上述公告的触发面（Chroma Server 的 HTTP 端点 / 多 tenant 授权）在
当前部署形态下不可达。风险仍登记为 High（依赖侧），残余风险见
[风险清单](risk-register.md) R-06。

**处置**：

- 不引入 Chroma Server 模式，不把 `data/index` 暴露为网络服务；
- 关注上游修复版本；有修复后升级 `chromadb`、重建索引并回归检索评测；
- 本发布不冻结 `chromadb` 之外的版本选择；`uv.lock` sha256 见 `release.json`。

### 3.2 前端（`web/package-lock.json`）

```text
npm audit --omit=dev : prod 8, optional 54, peer 8 → 0 vulnerabilities
npm audit            : total 212 → 0 vulnerabilities（info/low/moderate/high/critical 均为 0）
```

局限：npm audit 只查 npm 公告库；前端**未部署**（`web/dist` 不入库），所以这不是
服务器资产扫描。`package-lock.json` sha256 见 `release.json`。

## 4. 未执行与原因

| 检查 | 状态 | 原因 / 后续 |
|---|---|---|
| 容器镜像 OS 包扫描（Trivy/Grype） | NOT_RUN | 本机未安装；需要拉取漏洞库。记入风险 R-06 的后续项 |
| Python 依赖实时哈希校验（`uv sync --frozen` 已锁版本） | 已由 `uv.lock` 保证 | 未做 `--require-hashes` 额外供应链校验 |
| Chroma 公告的本地可利用性验证（PoC） | NOT_RUN | 不启动 Chroma Server 即无攻击面；不做攻击性验证 |
| 服务器侧依赖清单差异 | 未比对 | 服务器只运行同一不可变镜像；镜像内依赖即 `uv.lock` 解析结果 |

## 5. 复现与证据

- 命令与输出摘要见上；完整 JSON 未入库（不含敏感信息，但体积大，按计划书 §8 只留摘要）。
- `uv.lock` sha256：`240e6b61ce42b130e4db92cb8b80938b51a69bf2cbd54b9ea7c9d7753b7e2238`
- `web/package-lock.json` sha256：`4fa58b2216e52b48f7539f84738b2af7f58b6c055eced74fce3a9c2cce243a3e`
- 复跑时间：2026-11-12（Asia/Shanghai）。
