#!/usr/bin/env bash
# make restore-check：把最近一次备份恢复到独立验证库并核对行数/关键哈希（M7-05/T29）。
#
# 只在 Ubuntu 服务器执行；Mac 的 `make restore-check` 通过 SSH 调用本脚本。
#
# 安全边界（ADR-0006）：
# - 目标库固定为 `<源库>_restore_check`（如 bank_assistant_demo_restore_check），
#   闸门在 scripts/_ops.sh：不匹配 ^bank_assistant_[a-z0-9_]+_restore_check$ 直接拒绝；
# - 只对目标库做 DROP/CREATE/restore，源库只读；
# - 源库在备份后发生变化时本次演练 FAIL（先重新 make backup），不会把旧备份
#   说成“与当前源一致”。
#
# 校验内容：每个数据库的全表行数、关键表（含 content_hash 列）的行数 +
# max(content_hash) + content_hash 聚合 md5、alembic head。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/_ops.sh
source "$REPO_ROOT/scripts/_ops.sh"

PROJECT_ROOT="${PROJECT_ROOT:-$HOME/workspace/bank-assistant}"
BACKUP_ROOT="${BACKUP_ROOT:-$PROJECT_ROOT/backups}"

require_db_container

if [ -n "${BACKUP_DIR:-}" ]; then
  TARGET_DIR="$BACKUP_DIR"
else
  TARGET_DIR="$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1)"
fi
[ -n "$TARGET_DIR" ] && [ -d "$TARGET_DIR" ] || ops_fail "找不到备份目录（先 make backup）"
[ -f "$TARGET_DIR/meta.txt" ] || ops_fail "备份目录缺少 meta.txt：$TARGET_DIR"

echo "-- restore-check 使用备份：$TARGET_DIR"

fail=0
checked=0

for dump in "$TARGET_DIR"/dumps/*.dump; do
  [ -f "$dump" ] || ops_fail "备份里没有 dumps/*.dump"
  source_db="$(basename "$dump" .dump)"
  target_db="${source_db}_restore_check"

  require_restore_target "$target_db" "$source_db" || exit 1
  ops_database_exists "$source_db" || ops_fail "源库不存在：$source_db"

  echo
  echo "== $source_db → ${target_db}（源库只读）"

  expected_counts="$TARGET_DIR/counts/${source_db}.tsv"
  expected_hashes="$TARGET_DIR/hashes/${source_db}.tsv"
  [ -s "$expected_counts" ] || ops_fail "备份缺少 counts/${source_db}.tsv"
  [ -f "$expected_hashes" ] || ops_fail "备份缺少 hashes/${source_db}.tsv"

  # 先确认源库与备份快照一致，否则“恢复后与源一致”无从谈起。
  if diff -u "$expected_counts" <(ops_table_counts "$source_db") >/dev/null \
    && diff -u "$expected_hashes" <(ops_content_hashes "$source_db") >/dev/null; then
    echo "   源库自备份后未变化"
  else
    echo "FAIL: 源库在备份后被修改；请重新 make backup 再做恢复演练" >&2
    fail=1
    continue
  fi

  echo "-- 重建独立验证库（只允许 *_restore_check）"
  recreate_restore_database "$target_db" "$source_db" \
    || { echo "FAIL: 重建 $target_db 被拒绝" >&2; fail=1; continue; }

  echo "-- pg_restore → $target_db"
  if ! docker exec -i "$OPS_DB_CONTAINER" \
    pg_restore -U "$OPS_DB_USER" -d "$target_db" \
    --no-owner --no-privileges --exit-on-error < "$dump"; then
    echo "FAIL: pg_restore 失败（${source_db}）" >&2
    fail=1
    continue
  fi

  echo "-- 校验行数"
  if diff -u "$expected_counts" <(ops_table_counts "$target_db"); then
    echo "   OK: $(wc -l < "$expected_counts" | tr -d ' ') 张表行数一致"
  else
    echo "FAIL: 行数不一致（${source_db}）" >&2
    fail=1
  fi

  echo "-- 校验关键哈希"
  if diff -u "$expected_hashes" <(ops_content_hashes "$target_db"); then
    while IFS='|' read -r table rows max_hash md5; do
      [ -n "$table" ] || continue
      echo "   OK: ${table} rows=${rows} max_content_hash=${max_hash} md5=${md5}"
    done < "$expected_hashes"
  else
    echo "FAIL: content_hash 不一致（${source_db}）" >&2
    fail=1
  fi

  echo "-- 校验 alembic head"
  expected_alembic="$(cat "$TARGET_DIR/alembic/${source_db}.txt" 2>/dev/null || true)"
  actual_alembic="$(ops_alembic_version "$target_db")"
  if [ "$expected_alembic" = "$actual_alembic" ] && [ -n "$expected_alembic" ]; then
    echo "   OK: alembic=$actual_alembic"
  else
    echo "FAIL: alembic 不一致（期望 '$expected_alembic'，实际 '$actual_alembic'）" >&2
    fail=1
  fi

  checked=$((checked + 1))
done

echo
if [ "$fail" -ne 0 ]; then
  echo "RESULT: FAIL（${TARGET_DIR}）" >&2
  exit 1
fi
echo "RESULT: PASS（backup=$(basename "$TARGET_DIR")，$checked 个库恢复到 *_restore_check 并核对一致）"
