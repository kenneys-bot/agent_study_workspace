#!/usr/bin/env bash
# make index-activate：把活动索引指针切回某个已存在的旧版本（回滚演练用）。
#
# 与 deploy_index.sh 同一模式：用**已部署的不可变镜像**跑一次性容器，只挂载
# 仓库与数据根；切换前用当前 embedding provider 重算校准哈希并核对集合条数，
# 校验不过就不改指针。索引版本只增不改（ADR-0005），回滚不需要重建。
#
# 用法：make index-activate INDEX_VERSION=idx-20261112T110703Z-2334d03e
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_HOST="${DEPLOY_HOST:-my-ubuntu-server}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/jack/workspace/bank-assistant}"
REMOTE_REPO="$REMOTE_ROOT/repo"
APP_ENV_FILE="${APP_ENV_FILE:-$REMOTE_ROOT/.env.app}"
STATE_FILE="$REMOTE_ROOT/deploy/state.env"
INDEX_VERSION="${INDEX_VERSION:-}"

fail() { echo "FAIL: $*" >&2; exit 1; }
remote() { ssh "$DEPLOY_HOST" "$@"; }

[ -n "$INDEX_VERSION" ] || fail "必须指定 INDEX_VERSION（见 data/index/manifests/ 或备份的 active.json）"

STATE="$(remote "cat '$STATE_FILE' 2>/dev/null" || true)"
[ -n "$STATE" ] || fail "没有部署状态 ${STATE_FILE}；先 make deploy"
field() { printf '%s\n' "$STATE" | sed -n "s/^$1=//p" | head -1; }
IMAGE="$(field CURRENT_IMAGE)"
CODE_SHA="$(field CURRENT_SHA)"
[ -n "$IMAGE" ] || fail "state 缺少 CURRENT_IMAGE"
remote "docker image inspect '$IMAGE' >/dev/null 2>&1" || fail "镜像不在服务器：$IMAGE"
remote "cd '$REMOTE_REPO' && test \"\$(git rev-parse HEAD)\" = '$CODE_SHA'" \
  || fail "服务器仓库 HEAD 与已部署 SHA 不一致"
remote "test -f '$REMOTE_ROOT/data/index/manifests/$INDEX_VERSION.json'" \
  || fail "清单不存在：data/index/manifests/$INDEX_VERSION.json"

APP_UID="$(remote id -u)"
APP_GID="$(remote id -g)"

echo "== 切换活动索引指针 → ${INDEX_VERSION}（镜像 ${IMAGE}）"
remote "docker run --rm --network host --user '$APP_UID:$APP_GID' \
  --env-file '$APP_ENV_FILE' \
  -e INDEX_DIR=/app/data/index \
  -e EMBEDDING_PROVIDER=fastembed \
  -e FASTEMBED_CACHE_PATH=/app/data/models \
  -e HOME=/app/data/home \
  -v '$REMOTE_REPO':/app/repo \
  -v '$REMOTE_ROOT/data':/app/data \
  -w /app/repo '$IMAGE' python scripts/index_activate.py --index-version '$INDEX_VERSION'"

DEPLOY_HOST="$DEPLOY_HOST" EXPECT_CODE_SHA="$CODE_SHA" REQUIRE_INDEX=1 \
  bash "$REPO_ROOT/scripts/verify_deployment.sh"

echo "OK: 活动索引指针已切到 ${INDEX_VERSION}"
