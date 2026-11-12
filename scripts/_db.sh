#!/usr/bin/env bash
# 共享的数据库/隧道辅助函数（ADR-0006）。只被其它脚本 source，不单独执行。
#
# 纪律：任何函数都不得把含密码的 DATABASE_URL 打印到终端或日志；
# 对外只展示 host:port/库名。

DB_TUNNEL_HOST="${DB_TUNNEL_HOST:-127.0.0.1}"
DB_TUNNEL_PORT="${DB_TUNNEL_PORT:-15432}"

# 隧道端口是否已有进程监听（nc 在 macOS 自带）。
db_tunnel_ready() {
  nc -z -w 2 "$DB_TUNNEL_HOST" "$DB_TUNNEL_PORT" >/dev/null 2>&1
}

# ADR-0006 的标准操作提示；隧道缺失时必须显式失败而不是回退。
adr0006_help() {
  cat >&2 <<'EOF'
按 ADR-0006，数据库只在 Ubuntu，Mac 必须经 SSH 隧道访问：
  1) make db-tunnel      # 建立 127.0.0.1:15432 → 服务器 127.0.0.1:5432
  2) make doctor         # 复检隧道与 bank_assistant_test 可达性
  3) 仍失败时检查：ssh my-ubuntu-server true、服务器容器健康、~/.ssh/config
没有隧道时不存在本地回退：不会启动本机 PostgreSQL，也不会改用 SQLite。
EOF
}

require_tunnel() {
  if db_tunnel_ready; then
    return 0
  fi
  echo "FAIL: 无法连接 ${DB_TUNNEL_HOST}:${DB_TUNNEL_PORT}（SSH 隧道未建立）" >&2
  adr0006_help
  return 1
}

# 从环境变量或 .env 读取 DATABASE_URL；不回显内容。
load_database_url() {
  if [ -n "${DATABASE_URL:-}" ]; then
    printf '%s' "$DATABASE_URL"
    return 0
  fi
  [ -f .env ] || return 1
  local line
  line=$(grep -E '^DATABASE_URL=' .env | tail -n 1 || true)
  [ -n "$line" ] || return 1
  printf '%s' "${line#DATABASE_URL=}"
}

# 把 URL 的库名替换为目标库；带查询参数时拒绝做字符串替换。
db_url_with_database() {
  local url="$1" db="$2"
  case "$url" in
    *\?*) return 1 ;;
  esac
  printf '%s' "${url%/*}/${db}"
}

database_name_of() {
  printf '%s' "${1##*/}"
}

# 只输出 host:port，不包含用户名/密码。
database_endpoint_of() {
  local rest="${1#*://}"
  rest="${rest#*@}"
  printf '%s' "${rest%%/*}"
}

# 目标库必须以指定后缀结尾（_test / _demo），否则拒绝执行。
require_database_suffix() {
  local url="$1" suffix="$2" db
  db="$(database_name_of "$url")"
  if [ -z "$db" ] || [ "${db%$suffix}" = "$db" ]; then
    echo "FAIL: 目标库 '${db}' 不以 ${suffix} 结尾，拒绝执行" >&2
    return 1
  fi
}
