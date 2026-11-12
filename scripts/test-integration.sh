#!/usr/bin/env bash
# make test-integration：经 SSH 隧道在 Ubuntu 的 bank_assistant_test 上验证
# “空库可迁移”与真实数据库连通（ADR-0006）。
#
# - 隧道不通：非零退出并打印 ADR-0006 操作步骤；绝不回退本机 PG/SQLite。
# - 每次运行只重建 *_test 库的 public schema，然后 alembic upgrade head + pytest。
# - 不触碰 bank_assistant_dev / *_demo / *_restore_check。
#
# 注意：全组共用同一个 bank_assistant_test；不要并行跑两个 test-integration。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/_db.sh
source "$REPO_ROOT/scripts/_db.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || fail "需要 uv（ADR-0001）"
# 先证明隧道存在，再谈任何数据库操作。
require_tunnel || exit 1

BASE_URL="$(load_database_url)" || fail "缺少 DATABASE_URL（环境变量或 .env）"
TEST_URL="$(db_url_with_database "$BASE_URL" bank_assistant_test)" \
  || fail "DATABASE_URL 带查询参数，无法安全派生测试库 URL"
require_database_suffix "$TEST_URL" "_test" \
  || fail "集成测试只允许连接 *_test 库（ADR-0006）"

# 不沿用外部 DATABASE_URL 指向的 dev/demo；settings 以环境变量优先。
export DATABASE_URL="$TEST_URL"
export APP_ENV="testing"
export SECRET_KEY="${SECRET_KEY:-integration-test-only-not-a-real-secret-0123456789}"

TEST_DB="$(database_name_of "$TEST_URL")"
echo "-- 目标：$(database_endpoint_of "$TEST_URL")/${TEST_DB}（经隧道；不含凭据）"

echo "-- 重建 ${TEST_DB} 的 public schema（只影响 *_test）"
uv run --frozen python scripts/db_tool.py reset-test

echo "-- 空库迁移：alembic upgrade head"
uv run --frozen alembic upgrade head
uv run --frozen alembic current

echo "-- 集成测试（pytest -m integration）"
uv run --frozen pytest tests/integration -m integration
