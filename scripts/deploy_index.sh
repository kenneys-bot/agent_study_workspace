#!/usr/bin/env bash
# make deploy-index：在服务器用已部署的不可变镜像重建知识索引（M7-05/T29）。
#
# 为什么用一次性容器而不是在服务器装 Python 环境：
# - 服务器无 node、pypi 慢；镜像里的依赖与线上完全一致（uv.lock 锁定）；
# - 只挂载仓库与数据根，不写源码树；不安装/升级任何宿主依赖；
# - 模型缓存落在 $REMOTE_ROOT/data/models，重建/重启不重复下载 ~100MB。
#
# 产物：活动索引指针 data/index/active.json + manifests/<index_version>.json；
# 重建成功后打印 index_version / calibration sha256 / embedding 模型，供报告引用。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_HOST="${DEPLOY_HOST:-my-ubuntu-server}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/jack/workspace/bank-assistant}"
REMOTE_REPO="$REMOTE_ROOT/repo"
APP_ENV_FILE="${APP_ENV_FILE:-$REMOTE_ROOT/.env.app}"
STATE_FILE="$REMOTE_ROOT/deploy/state.env"
API_PORT="${API_PORT:-8000}"

fail() { echo "FAIL: $*" >&2; exit 1; }
remote() { ssh "$DEPLOY_HOST" "$@"; }

STATE="$(remote "cat '$STATE_FILE' 2>/dev/null" || true)"
[ -n "$STATE" ] || fail "没有部署状态 ${STATE_FILE}；先 make deploy"
field() { printf '%s\n' "$STATE" | sed -n "s/^$1=//p" | head -1; }
IMAGE="$(field CURRENT_IMAGE)"
CODE_SHA="$(field CURRENT_SHA)"
[ -n "$IMAGE" ] || fail "state 缺少 CURRENT_IMAGE"
remote "docker image inspect '$IMAGE' >/dev/null 2>&1" || fail "镜像不在服务器：$IMAGE"
remote "cd '$REMOTE_REPO' && test \"\$(git rev-parse HEAD)\" = '$CODE_SHA'" \
  || fail "服务器仓库 HEAD 与已部署 SHA 不一致"

APP_UID="$(remote id -u)"
APP_GID="$(remote id -g)"
remote "mkdir -p '$REMOTE_ROOT/data/index' '$REMOTE_ROOT/data/models' '$REMOTE_ROOT/data/home'"

run_in_image() {
  remote "docker run --rm --network host --user '$APP_UID:$APP_GID' \
    --env-file '$APP_ENV_FILE' \
    -e INDEX_DIR=/app/data/index \
    -e EMBEDDING_PROVIDER=fastembed \
    -e FASTEMBED_CACHE_PATH=/app/data/models \
    -e HOME=/app/data/home \
    -v '$REMOTE_REPO':/app/repo \
    -v '$REMOTE_ROOT/data':/app/data \
    -w /app/repo '$IMAGE' $*"
}

echo "== 服务器重建索引（镜像 ${IMAGE}，SHA ${CODE_SHA}）"
echo "-- 重建（fastembed 首次会下载模型到 data/models）"
run_in_image python scripts/index_rebuild.py

echo "-- 活动索引清单"
run_in_image python scripts/index_info.py

echo "-- 指针与清单摘要（非秘密，供报告引用）"
remote "python3 - <<'PY'
import json, pathlib
root = pathlib.Path('$REMOTE_ROOT/data/index')
pointer = json.loads((root / 'active.json').read_text())
manifest = json.loads((root / pointer['manifest']).read_text())
print('index_version=' + pointer['index_version'])
print('calibration_vector_sha256=' + pointer['calibration_vector_sha256'])
print('embedding_model_version=' + pointer['embedding_model_version'])
print('chunk_count=' + str(manifest['chunk_count']))
print('knowledge_snapshot_versions=' + str(len(manifest.get('knowledge_snapshot', []))))
print('created_at=' + manifest['created_at'])
PY"

DEPLOY_HOST="$DEPLOY_HOST" API_PORT="$API_PORT" EXPECT_CODE_SHA="$CODE_SHA" REQUIRE_INDEX=1 \
  bash "$REPO_ROOT/scripts/verify_deployment.sh"

echo "OK: index rebuilt and reported by /api/v1/system/version"
