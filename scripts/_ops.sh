#!/usr/bin/env bash
# 服务器侧运维辅助（M7-01/T29）：只被 backup.sh / restore_check.sh source，不单独执行。
#
# 约束：
# - 只操作本项目 compose 项目 `bank-assistant-db` 的容器；不触碰服务器上其它容器。
# - 不打印任何密码/连接串；psql 走容器内本地 socket（peer/trust），不需要密码。
# - 恢复目标必须匹配 `^bank_assistant_[a-z0-9_]+_restore_check$`，且不得等于源库。
# - 目标库只允许 `bank_assistant_*_restore_check`；DROP 只发生在这类库上。

OPS_DB_CONTAINER="${OPS_DB_CONTAINER:-bank-assistant-db-db-1}"
OPS_DB_USER="${OPS_DB_USER:-bank}"
OPS_DB_DATABASES="${OPS_DB_DATABASES:-bank_assistant_dev bank_assistant_demo}"

ops_fail() { echo "FAIL: $*" >&2; exit 1; }

require_db_container() {
  command -v docker >/dev/null 2>&1 || ops_fail "需要 docker（服务器侧执行）"
  docker inspect "$OPS_DB_CONTAINER" >/dev/null 2>&1 \
    || ops_fail "数据库容器 $OPS_DB_CONTAINER 不存在；先启动 compose 项目 bank-assistant-db"
  local health
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}' "$OPS_DB_CONTAINER")"
  [ "$health" = "healthy" ] || ops_fail "数据库容器 $OPS_DB_CONTAINER 状态为 ${health}，不是 healthy"
}

# psql 包装：-At 无表头、-F'|' 便于 diff；ON_ERROR_STOP 保证 SQL 错误不会被吞。
_ops_psql() {
  local database="$1" sql="$2"
  docker exec -i "$OPS_DB_CONTAINER" \
    psql -U "$OPS_DB_USER" -d "$database" -v ON_ERROR_STOP=1 -At -F'|' -c "$sql"
}

# 从 stdin 读 SQL（复杂 SQL 用 heredoc，避免多层引号）。
_ops_psql_stdin() {
  local database="$1"
  docker exec -i "$OPS_DB_CONTAINER" \
    psql -U "$OPS_DB_USER" -d "$database" -v ON_ERROR_STOP=1 -At -F'|'
}

ops_database_exists() {
  local database="$1"
  [ "$(_ops_psql postgres "select 1 from pg_database where datname = '$database'")" = "1" ]
}

ops_database_owner() {
  local database="$1"
  _ops_psql postgres "select pg_get_userbyid(datdba) from pg_database where datname = '$database'"
}

ops_alembic_version() {
  local database="$1"
  _ops_psql "$database" "select version_num from alembic_version" 2>/dev/null || true
}

# 所有 public 表的行数：表名|行数（按表名排序，可直接 diff）。
ops_table_counts() {
  local database="$1"
  _ops_psql_stdin "$database" <<'SQL'
select c.relname,
       (xpath('/row/cnt/text()', query_to_xml(
           format('select count(*) as cnt from %I.%I', n.nspname, c.relname),
           false, true, '')))[1]::text::bigint as rows
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r' and n.nspname = 'public'
order by c.relname;
SQL
}

# 带 content_hash 列的关键表：表名|行数|max_content_hash|content_hash_md5。
# md5 对按哈希排序后的全部 content_hash 求值，能发现“行数相同但内容不同”。
ops_content_hashes() {
  local database="$1" select_sql
  select_sql="$(_ops_psql_stdin "$database" <<'SQL'
select format($fmt$select %L as table_name, count(*) as rows, coalesce(max(content_hash), '') as max_content_hash, md5(coalesce(string_agg(content_hash, '|' order by content_hash), '')) as content_hash_md5 from %I$fmt$, table_name, table_name)
from information_schema.columns
where table_schema = 'public' and column_name = 'content_hash'
order by table_name;
SQL
)"
  [ -n "$select_sql" ] || return 0
  _ops_psql_stdin "$database" <<<"$select_sql"
}

# 恢复目标闸门：只允许独立验证库，绝不等于源库。
require_restore_target() {
  local target="$1" source="$2"
  [[ "$target" =~ ^bank_assistant_[a-z0-9_]+_restore_check$ ]] \
    || { echo "FAIL: 恢复目标 '$target' 不以 _restore_check 结尾或含非法字符，拒绝执行" >&2; return 1; }
  [ "$target" != "$source" ] \
    || { echo "FAIL: 恢复目标不能等于源库 '$source'" >&2; return 1; }
  [ "$target" != "bank_assistant_dev" ] \
    || { echo "FAIL: 拒绝覆盖开发库" >&2; return 1; }
  [ "$target" != "bank_assistant_demo" ] \
    || { echo "FAIL: 拒绝覆盖演示库" >&2; return 1; }
  return 0
}

# 重建恢复目标库：先校验闸门，再 DROP/CREATE，只影响 *_restore_check。
recreate_restore_database() {
  local target="$1" source="$2"
  require_restore_target "$target" "$source" || return 1
  _ops_psql_stdin postgres <<SQL
drop database if exists "$target" with (force);
create database "$target" owner "$OPS_DB_USER";
SQL
}

# 输出一行摘要，便于报告引用；不输出任何凭据。
ops_summary_line() {
  local label="$1" value="$2"
  printf '%s=%s\n' "$label" "$value"
}
