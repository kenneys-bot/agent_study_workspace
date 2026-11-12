#!/usr/bin/env bash
# make test-e2e：一次性 PostgreSQL 18 容器 + 隔离后端 + 前端 preview + Playwright。
#
# ADR-0006 过渡期说明（必须保留在报告里）：
# - 正式方案是 Mac 经 SSH 隧道访问 Ubuntu 的 bank_assistant_test 库；
# - 当前 T25 先把 e2e 跑通，使用**本机 compose 数据库**（独立项目名/端口/卷），
#   库名固定 bank_assistant_demo，绝不接触 dev/test/restore 库；
# - TODO(数据库访问工单)：切换为隧道 + bank_assistant_test 后移除本机 compose。
#
# 端到端全程 MODEL_MODE=replay：候选答复与质检 worker 都不调用真实云模型；
# 真实云链路证据由独立目标 make demo-real-model-smoke 提供。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "需要 docker（本机 compose 是 ADR-0006 过渡做法）"
docker compose version >/dev/null 2>&1 || fail "需要 docker compose v2"
command -v uv >/dev/null 2>&1 || fail "需要 uv"
command -v npm >/dev/null 2>&1 || fail "需要 npm"
command -v npx >/dev/null 2>&1 || fail "需要 npx"

pick_port() {
  python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

PROJECT_NAME="bank-assistant-e2e-$$"
POSTGRES_PORT="$(pick_port)"
API_PORT="$(pick_port)"
WEB_PORT="$(pick_port)"

# 一次性容器的本地凭据：明确不是任何环境秘密，也不进 Git。
export POSTGRES_USER="bank_e2e"
export POSTGRES_PASSWORD="bank_e2e_local_only"
export POSTGRES_DB="bank_assistant_demo"
export POSTGRES_PORT
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
export APP_ENV="testing"
export SECRET_KEY="e2e-only-not-a-real-secret-0123456789abcdef"
export MODEL_MODE="replay"
export EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER:-fastembed}"
export EMBEDDING_MODEL_NAME="${EMBEDDING_MODEL_NAME:-BAAI/bge-small-zh-v1.5}"
export INDEX_DIR="$REPO_ROOT/.e2e/index"
export ARTIFACT_ROOT="$REPO_ROOT/.e2e/artifacts"
export DATA_ROOT="$REPO_ROOT/.e2e/data"
export DEMO_SEED_STATE="$REPO_ROOT/.e2e/state/demo-seed.json"
export GIT_SHA="$(git rev-parse HEAD)"
export GIT_BRANCH="$(git branch --show-current)"
export E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}"
export E2E_API_BASE_URL="http://127.0.0.1:${API_PORT}"

compose() { docker compose -p "$PROJECT_NAME" -f deploy/compose.db.yml "$@"; }

API_PID=""
WEB_PID=""
cleanup() {
  local status=$?
  echo "-- 清理 e2e 环境（API/前端进程与 compose 项目 ${PROJECT_NAME}）" >&2
  if [ -n "$API_PID" ]; then kill "$API_PID" >/dev/null 2>&1 || true; fi
  if [ -n "$WEB_PID" ]; then kill "$WEB_PID" >/dev/null 2>&1 || true; fi
  # 兜底：uv/npx 可能派生子进程，按监听端口收口，避免遗留服务器。
  for port in "$API_PORT" "$WEB_PORT"; do
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "$pids" ]; then kill $pids >/dev/null 2>&1 || true; fi
  done
  wait "$API_PID" >/dev/null 2>&1 || true
  wait "$WEB_PID" >/dev/null 2>&1 || true
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

echo "-- 准备依赖（uv 锁定安装；前端依赖缺失时 npm ci）"
uv sync --frozen >/dev/null
if [ ! -d web/node_modules ]; then
  (cd web && npm ci --silent)
fi

echo "-- 检查 Playwright Chromium"
if ! (cd web && node -e "const {chromium}=require('@playwright/test'); const fs=require('fs'); process.exit(fs.existsSync(chromium.executablePath())?0:1)"); then
  echo "-- 本地没有 Chromium，执行 npx playwright install chromium（首次需要网络）"
  (cd web && npx playwright install chromium)
fi

mkdir -p "$DATA_ROOT"

echo "-- 启动一次性 PostgreSQL 18（项目 ${PROJECT_NAME}，127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}）"
compose up -d --wait --wait-timeout 120

echo "-- 迁移与种子（仅 bank_assistant_demo）"
uv run --frozen alembic upgrade head
uv run --frozen python scripts/seed_demo_accounts.py
uv run --frozen python scripts/seed_demo.py
uv run --frozen python scripts/seed_demo_rules.py
uv run --frozen python scripts/seed_demo_conversations.py
uv run --frozen python scripts/index_rebuild.py --provider "$EMBEDDING_PROVIDER"

echo "-- 启动隔离后端 http://127.0.0.1:${API_PORT}（MODEL_MODE=replay）"
uv run --frozen uvicorn bank_assistant.main:app \
  --host 127.0.0.1 --port "$API_PORT" --log-level warning \
  >"$DATA_ROOT/api.log" 2>&1 &
API_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${API_PORT}/health/ready" >/dev/null \
  || { tail -40 "$DATA_ROOT/api.log" >&2 || true; fail "后端未就绪"; }

echo "-- 构建并启动前端 preview http://127.0.0.1:${WEB_PORT}"
(cd web && VITE_API_BASE_URL="$E2E_API_BASE_URL" npm run build >/dev/null)
(
  cd web
  exec ./node_modules/.bin/vite preview --host 127.0.0.1 --port "$WEB_PORT" --strictPort
) >"$DATA_ROOT/web.log" 2>&1 &
WEB_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${WEB_PORT}/" >/dev/null \
  || { tail -40 "$DATA_ROOT/web.log" >&2 || true; fail "前端 preview 未就绪"; }

echo "-- Playwright（截图与下载产物在 web/e2e/screenshots/，已 gitignore）"
(cd web && DEMO_SEED_STATE="$DEMO_SEED_STATE" E2E_BASE_URL="$E2E_BASE_URL" \
  npx playwright test)

echo "test-e2e: PASS（隔离 compose 数据库 + replay；不是真实云模型证据）"
