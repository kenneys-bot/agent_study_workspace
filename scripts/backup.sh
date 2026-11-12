#!/usr/bin/env bash
# make backup：服务器侧备份 dev/demo 数据库与产物（M7-01/T29）。
#
# 只在 Ubuntu 服务器执行；Mac 的 `make backup` 通过 SSH 调用本脚本。
# 产物：~/workspace/bank-assistant/backups/<timestamp>/
#   dumps/<db>.dump             pg_dump custom 格式（可被 pg_restore 读取）
#   counts/<db>.tsv             备份时所有 public 表行数（恢复校验的基准）
#   hashes/<db>.tsv             关键表 content_hash 的行数/max/md5
#   alembic/<db>.txt            备份时 alembic head
#   artifacts.tar.gz            产物目录（为空时跳过）
#   SHA256SUMS                  dump 的 sha256
#   meta.txt                    时间、代码 SHA、容器、大小等（不含凭据）
#
# 纪律：不修改源库、不删除旧备份、不打印密码；只读 pg_dump 与 SELECT。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/_ops.sh
source "$REPO_ROOT/scripts/_ops.sh"

PROJECT_ROOT="${PROJECT_ROOT:-$HOME/workspace/bank-assistant}"
BACKUP_ROOT="${BACKUP_ROOT:-$PROJECT_ROOT/backups}"
BACKUP_ID="${BACKUP_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
TARGET_DIR="$BACKUP_ROOT/$BACKUP_ID"

require_db_container

if [ -e "$TARGET_DIR" ]; then
  ops_fail "备份目录已存在：$TARGET_DIR（不覆盖旧备份；换个 BACKUP_ID）"
fi

mkdir -p "$TARGET_DIR"/{dumps,counts,hashes,alembic}

CODE_SHA="unknown"
if [ -d "$PROJECT_ROOT/repo/.git" ]; then
  CODE_SHA="$(git -C "$PROJECT_ROOT/repo" rev-parse HEAD 2>/dev/null || echo unknown)"
fi

{
  echo "backup_id=$BACKUP_ID"
  echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=$(hostname)"
  echo "project_root=$PROJECT_ROOT"
  echo "code_sha=$CODE_SHA"
  echo "db_container=$OPS_DB_CONTAINER"
  echo "databases=$OPS_DB_DATABASES"
} > "$TARGET_DIR/meta.txt"

echo "-- backup $BACKUP_ID → $TARGET_DIR（源库只读）"

for database in $OPS_DB_DATABASES; do
  [[ "$database" =~ ^bank_assistant_[a-z0-9_]+$ ]] \
    || ops_fail "拒绝非本项目库名：$database"
  ops_database_exists "$database" || ops_fail "数据库不存在：$database"

  dump="$TARGET_DIR/dumps/${database}.dump"
  echo "-- pg_dump $database"
  docker exec "$OPS_DB_CONTAINER" \
    pg_dump -U "$OPS_DB_USER" --format=custom --no-owner --no-privileges \
    --file="/tmp/${database}.dump" "$database"
  docker cp "$OPS_DB_CONTAINER:/tmp/${database}.dump" "$dump" >/dev/null
  docker exec "$OPS_DB_CONTAINER" rm -f "/tmp/${database}.dump"

  ops_table_counts "$database" > "$TARGET_DIR/counts/${database}.tsv"
  ops_content_hashes "$database" > "$TARGET_DIR/hashes/${database}.tsv"
  ops_alembic_version "$database" > "$TARGET_DIR/alembic/${database}.txt"

  bytes="$(wc -c < "$dump" | tr -d ' ')"
  echo "   dump=${bytes}B tables=$(wc -l < "$TARGET_DIR/counts/${database}.tsv" | tr -d ' ') alembic=$(cat "$TARGET_DIR/alembic/${database}.txt")"
  echo "database=$database dump=dumps/${database}.dump bytes=$bytes alembic=$(cat "$TARGET_DIR/alembic/${database}.txt")" >> "$TARGET_DIR/meta.txt"
done

# 产物目录很小（报告导出）；为空则明确跳过，不制造空 tar。
ARTIFACT_DIR="$PROJECT_ROOT/artifacts"
if [ -d "$ARTIFACT_DIR" ] && [ -n "$(find "$ARTIFACT_DIR" -type f -print -quit 2>/dev/null)" ]; then
  echo "-- 打包产物目录"
  tar -czf "$TARGET_DIR/artifacts.tar.gz" -C "$ARTIFACT_DIR" .
  echo "artifacts=artifacts.tar.gz" >> "$TARGET_DIR/meta.txt"
else
  echo "-- 产物目录为空，跳过 artifacts.tar.gz"
  echo "artifacts=skipped-empty" >> "$TARGET_DIR/meta.txt"
fi

(
  cd "$TARGET_DIR"
  sha256sum dumps/*.dump > SHA256SUMS
)
echo "-- SHA256SUMS"
sed 's/^/   /' "$TARGET_DIR/SHA256SUMS"

echo "OK: backup=$BACKUP_ID path=$TARGET_DIR"
