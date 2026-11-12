#!/usr/bin/env bash
# make deploy：把当前提交构建成不可变镜像并部署到 Ubuntu（M7-01/T29）。
#
# 步骤：
# 1. 工作树必须干净；用仓库根作为 Docker 构建上下文（服务器不重建源码）；
# 2. 镜像标签固定为 bank-assistant:api-<code_sha7>，记录 image id / 传输 sha256；
# 3. `docker save | ssh docker load` 传到服务器，比对两端 image id；
# 4. 用 git bundle 把服务器 checkout 精确重置到同一 SHA（不 push、不给服务器写凭据）；
# 5. 服务器运行仓库外副本 deploy/compose.app.yml（与 compose.db.yml 同模式），
#    配置从仓库外 .env.app 读取，端口只绑 127.0.0.1；
# 6. 记录上一版镜像/SHA 到 ~/workspace/bank-assistant/deploy/state.env 供 rollback 使用；
# 7. 校验 /health/live、/health/ready、端口回环绑定与版本页 SHA。
#
# 可用环境变量：
#   DEPLOY_HOST=my-ubuntu-server  REMOTE_ROOT=~/workspace/bank-assistant
#   API_PORT=8000                 APP_ENV_FILE=<REMOTE_ROOT>/.env.app
#   API_IMAGE=bank-assistant:api-<sha7>（默认按当前 SHA 生成）
#   GIT_SHA_OVERRIDE / GIT_BRANCH_OVERRIDE  显式部署某个提交（用于回滚演练基线）
#   BUILD_CONTEXT=<目录>         自定义 Docker 构建上下文（默认仓库根）
#   DOCKER_PLATFORM=linux/amd64  目标平台（服务器 x86_64；Mac arm64 交叉构建）
#   SKIP_BUILD=1                 镜像已在服务器上时跳过构建/传输
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_HOST="${DEPLOY_HOST:-my-ubuntu-server}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/jack/workspace/bank-assistant}"
REMOTE_REPO="$REMOTE_ROOT/repo"
API_PORT="${API_PORT:-8000}"
APP_ENV_FILE="${APP_ENV_FILE:-$REMOTE_ROOT/.env.app}"
STATE_FILE="$REMOTE_ROOT/deploy/state.env"
LOCAL_DATA="$REPO_ROOT/.local-data"

fail() { echo "FAIL: $*" >&2; exit 1; }
remote() { ssh "$DEPLOY_HOST" "$@"; }

mkdir -p "$LOCAL_DATA"

CODE_SHA="${GIT_SHA_OVERRIDE:-$(git rev-parse HEAD)}"
BRANCH="${GIT_BRANCH_OVERRIDE:-$(git branch --show-current || true)}"
[ -n "$BRANCH" ] || BRANCH="detached"
SHA7="${CODE_SHA:0:7}"
IMAGE="${API_IMAGE:-bank-assistant:api-$SHA7}"
BUILD_CONTEXT="${BUILD_CONTEXT:-$REPO_ROOT}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
# Docker Desktop 的 buildx 缓存默认写 ~/.docker；沙箱/CI 里用仓库内可写目录。
export BUILDX_CONFIG="${BUILDX_CONFIG:-$REPO_ROOT/.docker-config/buildx}"
mkdir -p "$BUILDX_CONFIG"

# 只允许部署提交，不允许把未提交改动打进镜像（否则镜像 SHA 无法自证）。
if [ "$BUILD_CONTEXT" = "$REPO_ROOT" ] && [ -n "$(git status --porcelain)" ]; then
  fail "工作树不干净，拒绝构建不可变镜像：先提交或 git stash"
fi

echo "== 部署 $BRANCH@$SHA7 → $DEPLOY_HOST（镜像 $IMAGE）"

echo "-- 预检服务器"
remote "test -f '$APP_ENV_FILE'" || fail "服务器缺少 $APP_ENV_FILE（见 deploy/env.app.example）"
remote "test -f '$REMOTE_ROOT/.env.pg'" || fail "服务器缺少 $REMOTE_ROOT/.env.pg（数据库凭据）"
remote "docker inspect bank-assistant-db-db-1 >/dev/null 2>&1" \
  || fail "数据库容器 bank-assistant-db-db-1 未运行"
remote "test -d '$REMOTE_REPO/.git'" || fail "服务器缺少仓库 checkout：$REMOTE_REPO"
if [ -n "$(remote "cd '$REMOTE_REPO' && git status --porcelain")" ]; then
  fail "服务器工作树有未提交改动；拒绝覆盖（先人工处理 $REMOTE_REPO）"
fi
APP_UID="$(remote id -u)"
APP_GID="$(remote id -g)"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "-- docker build（上下文 $BUILD_CONTEXT，平台 $PLATFORM）"
  if docker buildx version >/dev/null 2>&1; then
    docker buildx build --platform "$PLATFORM" --load \
      -f "$REPO_ROOT/deploy/docker/Dockerfile.api" -t "$IMAGE" "$BUILD_CONTEXT"
  else
    DOCKER_BUILDKIT=1 docker build --platform "$PLATFORM" \
      -f "$REPO_ROOT/deploy/docker/Dockerfile.api" -t "$IMAGE" "$BUILD_CONTEXT"
  fi
  IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
  IMAGE_SIZE="$(docker image inspect --format '{{.Size}}' "$IMAGE")"
  IMAGE_PLATFORM="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE")"
  [ "$IMAGE_PLATFORM" = "$PLATFORM" ] \
    || fail "镜像平台 $IMAGE_PLATFORM 与目标 $PLATFORM 不一致（会造成 exec format error）"
  echo "   本地 image id=$IMAGE_ID size=${IMAGE_SIZE}B platform=$IMAGE_PLATFORM"

  echo "-- 传输镜像（docker save | ssh docker load）"
  REMOTE_TAR="/tmp/bank-assistant-image-$SHA7.tar"
  TRANSFER_SHA="$(docker save "$IMAGE" \
    | ssh "$DEPLOY_HOST" "tee '$REMOTE_TAR' | docker load >/dev/null && sha256sum '$REMOTE_TAR' | cut -d' ' -f1")"
  remote "rm -f '$REMOTE_TAR'"
  REMOTE_IMAGE_ID="$(remote "docker image inspect --format '{{.Id}}' '$IMAGE'")"
  [ "$IMAGE_ID" = "$REMOTE_IMAGE_ID" ] \
    || fail "两端 image id 不一致（本地 $IMAGE_ID，服务器 $REMOTE_IMAGE_ID）"
  echo "   服务器 image id=$REMOTE_IMAGE_ID tar_sha256=$TRANSFER_SHA"
else
  REMOTE_IMAGE_ID="$(remote "docker image inspect --format '{{.Id}}' '$IMAGE'" 2>/dev/null || true)"
  [ -n "$REMOTE_IMAGE_ID" ] || fail "SKIP_BUILD=1 但服务器没有镜像 $IMAGE"
  TRANSFER_SHA="skipped"
  echo "   跳过构建/传输（服务器已有 $REMOTE_IMAGE_ID）"
fi

echo "-- 服务器 checkout 对齐到 $CODE_SHA"
REMOTE_HAS_SHA="$(remote "cd '$REMOTE_REPO' && git cat-file -e '$CODE_SHA^{commit}' 2>/dev/null && echo yes || echo no")"
if [ "$REMOTE_HAS_SHA" != "yes" ]; then
  BUNDLE="$LOCAL_DATA/deploy-$SHA7.bundle"
  rm -f "$BUNDLE"
  git bundle create "$BUNDLE" "$BRANCH" >/dev/null \
    || fail "无法生成 git bundle（分支 $BRANCH）"
  scp -q "$BUNDLE" "$DEPLOY_HOST:/tmp/bank-assistant-deploy-$SHA7.bundle"
  remote "cd '$REMOTE_REPO' && git fetch -q /tmp/bank-assistant-deploy-$SHA7.bundle 'refs/heads/$BRANCH' && rm -f /tmp/bank-assistant-deploy-$SHA7.bundle"
  remote "cd '$REMOTE_REPO' && git reset --hard FETCH_HEAD"
  rm -f "$BUNDLE"
else
  remote "cd '$REMOTE_REPO' && git reset --hard '$CODE_SHA'"
fi
remote "cd '$REMOTE_REPO' && test \"\$(git rev-parse HEAD)\" = '$CODE_SHA' && test -z \"\$(git status --porcelain)\"" \
  || fail "服务器 checkout 未能对齐到 $CODE_SHA（或有未提交文件）"
echo "   服务器 HEAD=$CODE_SHA 且工作树干净"

echo "-- 同步部署文件（仓库外副本，与数据库 compose 同模式）"
scp -q "$REPO_ROOT/deploy/compose.app.yml" "$DEPLOY_HOST:$REMOTE_ROOT/deploy/compose.app.yml"
remote "mkdir -p '$REMOTE_ROOT/deploy'"

# 上一版镜像/SHA：优先沿用已有 state 里的 PREVIOUS，
# 只有当前运行镜像与新镜像不同才把它提升为 previous，避免重复部署同一镜像时丢失回滚点。
CURRENT_IMAGE="$(remote "docker inspect -f '{{.Config.Image}}' bank-assistant-api 2>/dev/null || true")"
CURRENT_SHA="$(remote "docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' bank-assistant-api 2>/dev/null | sed -n 's/^GIT_SHA=//p' | head -1" || true)"
PREVIOUS_IMAGE="$(remote "sed -n 's/^CURRENT_IMAGE=//p' '$STATE_FILE' 2>/dev/null" || true)"
PREVIOUS_SHA="$(remote "sed -n 's/^CURRENT_SHA=//p' '$STATE_FILE' 2>/dev/null" || true)"
if [ -n "$CURRENT_IMAGE" ] && [ "$CURRENT_IMAGE" != "$IMAGE" ]; then
  PREVIOUS_IMAGE="$CURRENT_IMAGE"
  PREVIOUS_SHA="$CURRENT_SHA"
fi
[ -n "$PREVIOUS_IMAGE" ] || echo "   提示：暂无上一版镜像（首次部署，回滚点为空）"

echo "-- docker compose up（restart=unless-stopped，127.0.0.1:$API_PORT）"
remote "mkdir -p '$REMOTE_ROOT/deploy' && umask 022 && cat > '$STATE_FILE'" <<EOF
DEPLOYED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CURRENT_IMAGE=$IMAGE
CURRENT_SHA=$CODE_SHA
CURRENT_BRANCH=$BRANCH
CURRENT_IMAGE_ID=$REMOTE_IMAGE_ID
PREVIOUS_IMAGE=$PREVIOUS_IMAGE
PREVIOUS_SHA=$PREVIOUS_SHA
API_PORT=$API_PORT
EOF

remote "cd '$REMOTE_REPO' && \
  APP_ENV_FILE='$APP_ENV_FILE' \
  APP_DATA_DIR='$REMOTE_ROOT/data' \
  APP_ARTIFACT_DIR='$REMOTE_ROOT/artifacts' \
  APP_UID='$APP_UID' APP_GID='$APP_GID' API_PORT='$API_PORT' \
  API_IMAGE='$IMAGE' APP_GIT_SHA='$CODE_SHA' APP_GIT_BRANCH='$BRANCH' \
  docker compose -f '$REMOTE_ROOT/deploy/compose.app.yml' up -d --wait --wait-timeout 180"

echo "-- 校验部署"
EXPECT_CODE_SHA="$CODE_SHA" REQUIRE_INDEX="${REQUIRE_INDEX:-0}" \
  bash "$REPO_ROOT/scripts/verify_deployment.sh"

echo "OK: deployed $IMAGE（code_sha=$CODE_SHA image_id=$REMOTE_IMAGE_ID）"
echo "    rollback: make rollback（上一版 ${PREVIOUS_IMAGE:-无}）"
