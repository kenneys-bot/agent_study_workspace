#!/usr/bin/env bash
# make test-integration：在隔离的 compose 项目里验证“空库可迁移”与真实数据库连通。
# 只操作本项目（bank-assistant-test）的容器/卷，不触碰开发者其他容器；结束一律 down -v。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="deploy/compose.db.yml"
PROJECT_NAME="bank-assistant-test"
# 测试专用端口，避免撞上本机开发用的 5432；可用 POSTGRES_PORT 覆盖。
POSTGRES_PORT="${POSTGRES_PORT:-55432}"

# 一次性测试容器的凭据与占位密钥：不是任何环境的真实秘密。
# 显式 export，优先级高于开发者本机 .env，保证测试目标确定。
export POSTGRES_USER="${POSTGRES_USER:-bank_test}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-bank_test_local_only}"
export POSTGRES_DB="${POSTGRES_DB:-bank_assistant_test}"
export POSTGRES_PORT
# 不沿用外部 DATABASE_URL，避免误连开发库。
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-integration-test-placeholder}"
# 一次性测试密钥：长度 >= 32 字节，满足 HS256 最小建议长度；不是任何环境的秘密。
export SECRET_KEY="${SECRET_KEY:-integration-test-only-not-a-real-secret-0123456789}"
export APP_ENV="${APP_ENV:-testing}"

compose() { docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"; }

cleanup() {
  local status=$?
  echo "-- 清理测试项目 ${PROJECT_NAME}（down -v）" >&2
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 \
  || fail "需要 docker（ADR-0002：集成测试依赖 PostgreSQL 容器）"
docker compose version >/dev/null 2>&1 || fail "需要 docker compose v2"
command -v uv >/dev/null 2>&1 || fail "需要 uv（ADR-0001）"

echo "-- 启动 PostgreSQL 18（compose 项目 ${PROJECT_NAME}，127.0.0.1:${POSTGRES_PORT}）"
# 清掉上一次异常退出留下的同名项目残留；作用域仅限本项目。
compose down -v --remove-orphans >/dev/null 2>&1 || true
compose up -d --wait --wait-timeout 120

echo "-- 空库迁移：alembic upgrade head"
uv run --frozen alembic upgrade head
uv run --frozen alembic current

echo "-- 集成测试（pytest -m integration）"
uv run --frozen pytest tests/integration -m integration
