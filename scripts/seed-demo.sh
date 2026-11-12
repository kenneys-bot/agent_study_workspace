#!/usr/bin/env bash
# make seed-demo：只向 bank_assistant_demo 写入演示数据（ADR-0006）。
#
# - 目标库由 DATABASE_URL（.env 或环境变量）派生，固定替换为 bank_assistant_demo；
#   可用 DEMO_DATABASE_URL 整串覆盖，但库名仍必须以 _demo 结尾。
# - 目标必须以 _demo 结尾，拒绝 dev/test/restore；不触达测试库与恢复验证库。
# - 四个种子脚本本身幂等；重复执行不会新增版本。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/_db.sh
source "$REPO_ROOT/scripts/_db.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || fail "需要 uv（ADR-0001）"
require_tunnel

if [ -n "${DEMO_DATABASE_URL:-}" ]; then
  DEMO_URL="$DEMO_DATABASE_URL"
else
  BASE_URL="$(load_database_url)" || fail "缺少 DATABASE_URL（环境变量或 .env）"
  DEMO_URL="$(db_url_with_database "$BASE_URL" bank_assistant_demo)" \
    || fail "DATABASE_URL 带查询参数，无法安全派生演示库 URL"
fi

require_database_suffix "$DEMO_URL" "_demo" \
  || fail "演示数据只允许写入 *_demo 库（ADR-0006）"

DEMO_DB="$(database_name_of "$DEMO_URL")"
if [ "$DEMO_DB" != "bank_assistant_demo" ]; then
  echo "WARN: 目标库是 ${DEMO_DB}（不是默认的 bank_assistant_demo）" >&2
fi

export DATABASE_URL="$DEMO_URL"
export APP_ENV="${APP_ENV:-development}"
export DATA_ROOT="${DATA_ROOT:-.local-data}"
mkdir -p "$DATA_ROOT"

echo "-- seed-demo 目标：$(database_endpoint_of "$DEMO_URL")/${DEMO_DB}（不含凭据）"
uv run --frozen python scripts/seed_demo_accounts.py
uv run --frozen python scripts/seed_demo.py
uv run --frozen python scripts/seed_demo_rules.py
uv run --frozen python scripts/seed_demo_conversations.py
