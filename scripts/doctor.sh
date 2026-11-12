#!/usr/bin/env bash
# make doctor：检查本机依赖、配置、SSH 隧道与目标数据库可达性。绝不打印秘密值。
#
# ADR-0006：Mac 不部署 PostgreSQL；数据库检查的是「隧道 127.0.0.1:15432 + bank_assistant_test」，
# 隧道未建立时必须失败并给出操作步骤，不做任何本地回退。
set -u
cd "$(dirname "$0")/.."
# shellcheck source=scripts/_db.sh
source scripts/_db.sh

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }

echo "== 工具链 =="
python3 -V >/dev/null 2>&1 && ok "python3 $(python3 -V 2>&1 | awk '{print $2}')" || bad "python3 不可用"
command -v uv >/dev/null && ok "uv $(uv --version 2>/dev/null | awk '{print $2}')" || bad "uv 未安装（见 ADR-0001）"
command -v make >/dev/null && ok "make $(make -v 2>/dev/null | head -1 | awk '{print $3}')" || bad "make 未安装"
command -v ssh >/dev/null && ok "ssh $(ssh -V 2>&1 | awk '{print $1}')" || bad "ssh 不可用（隧道依赖）"
command -v nc >/dev/null && ok "nc 可用（隧道探测）" || bad "nc 不可用（隧道探测依赖）"
# docker 只用于 API 容器与历史 e2e；数据库不再依赖本机 Docker（ADR-0006）。
command -v docker >/dev/null && ok "docker $(docker --version 2>/dev/null | sed 's/Docker version //;s/,.*//')（仅容器任务）" || warn "docker 未安装（不影响数据库；API 容器需要）"
command -v psql >/dev/null && ok "psql $(psql --version 2>/dev/null | awk '{print $3}')（可选客户端）" || warn "psql 未安装（可选，脚本不依赖）"

echo "== 配置 =="
if [ -f .env ]; then
  ok ".env 存在（不显示内容）"
  for k in DEEPSEEK_API_KEY LLM_API_KEY; do
    grep -q "^${k}=..*" .env && ok "$k 已配置" || warn "$k 为空（对应链路不可用）"
  done
else
  bad ".env 不存在：cp .env.example .env 后填写凭据"
fi
[ -f .env.example ] && ok ".env.example 存在" || bad ".env.example 缺失"

DB_URL="$(load_database_url || true)"
if [ -z "$DB_URL" ]; then
  bad "DATABASE_URL 未配置（环境变量或 .env）"
else
  DB_ENDPOINT="$(database_endpoint_of "$DB_URL")"
  DB_NAME="$(database_name_of "$DB_URL")"
  if [ "$DB_ENDPOINT" = "${DB_TUNNEL_HOST}:${DB_TUNNEL_PORT}" ]; then
    ok "DATABASE_URL 指向 ${DB_ENDPOINT}/${DB_NAME}（隧道入口）"
  else
    bad "DATABASE_URL 指向 ${DB_ENDPOINT}/${DB_NAME}，不是 ${DB_TUNNEL_HOST}:${DB_TUNNEL_PORT}（ADR-0006）"
  fi
fi

echo "== 数据库（ADR-0006：Ubuntu 唯一实例 + SSH 隧道） =="
if db_tunnel_ready; then
  ok "隧道 127.0.0.1:${DB_TUNNEL_PORT} 已监听"
  if [ -n "$DB_URL" ]; then
    TEST_URL="$(db_url_with_database "$DB_URL" bank_assistant_test || true)"
    if [ -z "$TEST_URL" ]; then
      bad "无法从 DATABASE_URL 派生 bank_assistant_test（URL 带查询参数）"
    elif DATABASE_URL="$TEST_URL" uv run --frozen python scripts/db_tool.py ping >/dev/null 2>&1; then
      ok "bank_assistant_test SELECT 1 通过"
    else
      bad "bank_assistant_test 不可达（检查容器与凭据；不要回退本机 PG）"
    fi
  fi
else
  bad "隧道未建立：127.0.0.1:${DB_TUNNEL_PORT} 无监听"
  adr0006_help
fi

echo "== 仓库状态 =="
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
echo "  分支 $(git branch --show-current 2>/dev/null || echo '?') / SHA $(git rev-parse --short HEAD 2>/dev/null || echo '?') / 未提交条目 $dirty"
[ "$dirty" -eq 0 ] && ok "工作树干净" || warn "工作树有 $dirty 项未提交（不自动处理）"

echo "== 本机端口 =="
for p in 8000 5173; do
  if command -v lsof >/dev/null && lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then
    warn "端口 $p 已被占用"
  else
    ok "端口 $p 空闲"
  fi
done
# ADR-0006：Mac 不应有本项目使用的本地 5432；15432 是隧道端口，由上面的数据库检查负责。
if command -v lsof >/dev/null && lsof -nP -iTCP:5432 -sTCP:LISTEN >/dev/null 2>&1; then
  warn "本机 5432 有监听：ADR-0006 要求 Mac 不部署本项目数据库，请确认不是本项目"
else
  ok "本机 5432 无监听（符合 ADR-0006：数据库只在 Ubuntu）"
fi

echo "== 数据目录（仓库外） =="
for d in .local-data .local-artifacts .local-backups; do
  if [ -d "$d" ]; then ok "$d 存在"; else warn "$d 不存在（mkdir -p ${d}）"; fi
done
if ssh -o BatchMode=yes -o ConnectTimeout=5 my-ubuntu-server true >/dev/null 2>&1; then
  ok "Ubuntu 可达（项目在远端 ~/workspace/bank-assistant/，见 docs/environment.md）"
else
  warn "Ubuntu 不可达（集成测试/迁移/seed 将无法进行）"
fi

echo
[ "$fail" -eq 0 ] && echo "doctor: 通过（警告项不阻断）" || { echo "doctor: 存在失败项" >&2; exit 1; }
