#!/usr/bin/env bash
# make rollback：回滚演练（M7-05/T29）——切回上一版不可变镜像，验证，再切回当前版。
#
# 不是盲目降级：
# - 只回滚应用镜像，不降数据库 schema（本项目迁移采用向后兼容的扩展方式；
#   回滚前确认 head 未变，本次演练不执行 alembic downgrade）；
# - 不重启服务器、不停止数据库、不触碰其它容器；
# - 每一步都验证 /health/live、/health/ready 与版本页 code_sha；
# - 结束时必须回到部署前记录的当前镜像，服务恢复新版本。
#
# 回滚点来自部署脚本写入的 $REMOTE_ROOT/deploy/state.env（非秘密）。
# 可通过 BACK_TO=<镜像> 显式指定目标，但默认使用 state 里的 PREVIOUS_IMAGE。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_HOST="${DEPLOY_HOST:-my-ubuntu-server}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/jack/workspace/bank-assistant}"
REMOTE_REPO="$REMOTE_ROOT/repo"
API_PORT="${API_PORT:-8000}"
APP_ENV_FILE="${APP_ENV_FILE:-$REMOTE_ROOT/.env.app}"
STATE_FILE="$REMOTE_ROOT/deploy/state.env"
COMPOSE_FILE="$REMOTE_ROOT/deploy/compose.app.yml"

fail() { echo "FAIL: $*" >&2; exit 1; }
remote() { ssh "$DEPLOY_HOST" "$@"; }

STATE="$(remote "cat '$STATE_FILE' 2>/dev/null" || true)"
[ -n "$STATE" ] || fail "没有部署状态 $STATE_FILE；先 make deploy"

field() { printf '%s\n' "$STATE" | sed -n "s/^$1=//p" | head -1; }
CURRENT_IMAGE="$(field CURRENT_IMAGE)"
CURRENT_SHA="$(field CURRENT_SHA)"
CURRENT_BRANCH="$(field CURRENT_BRANCH)"
[ -n "$CURRENT_BRANCH" ] || CURRENT_BRANCH="integration"
PREVIOUS_IMAGE="${BACK_TO:-$(field PREVIOUS_IMAGE)}"
PREVIOUS_SHA="$(field PREVIOUS_SHA)"
[ -n "$PREVIOUS_IMAGE" ] || fail "没有上一版镜像可回滚（首次部署）；用 BACK_TO=<镜像> 指定"
remote "docker image inspect '$PREVIOUS_IMAGE' >/dev/null 2>&1" \
  || fail "上一版镜像不在服务器上：$PREVIOUS_IMAGE"
[ -n "$CURRENT_IMAGE" ] || fail "state 缺少 CURRENT_IMAGE"

APP_UID="$(remote id -u)"
APP_GID="$(remote id -g)"

deploy_image() { # deploy_image <image> <sha> <branch> <label> <require_index>
  local image="$1" sha="$2" branch="$3" label="$4" require_index="$5"
  echo "-- $label：up $image"
  remote "cd '$REMOTE_REPO' && \
    APP_ENV_FILE='$APP_ENV_FILE' \
    APP_DATA_DIR='$REMOTE_ROOT/data' \
    APP_ARTIFACT_DIR='$REMOTE_ROOT/artifacts' \
    APP_UID='$APP_UID' APP_GID='$APP_GID' API_PORT='$API_PORT' \
    API_IMAGE='$image' APP_GIT_SHA='$sha' APP_GIT_BRANCH='$branch' \
    docker compose -f '$COMPOSE_FILE' up -d --wait --wait-timeout 180"
  DEPLOY_HOST="$DEPLOY_HOST" API_PORT="$API_PORT" \
    EXPECT_CODE_SHA="$sha" REQUIRE_INDEX="$require_index" \
    bash "$REPO_ROOT/scripts/verify_deployment.sh"
}

echo "== 回滚演练：$CURRENT_IMAGE（$CURRENT_SHA）→ $PREVIOUS_IMAGE（$PREVIOUS_SHA）"
remote "docker inspect -f '{{.State.Health.Status}}' bank-assistant-db-db-1"

# 确认 schema 未变化：回滚旧代码前检查 alembic head，不盲目降库。
echo "-- 记录当前 alembic head（回滚不执行 downgrade）"
remote "docker exec bank-assistant-db-db-1 psql -U bank -d bank_assistant_demo -At -c 'select version_num from alembic_version'"

deploy_image "$PREVIOUS_IMAGE" "$PREVIOUS_SHA" "rollback-to-${PREVIOUS_SHA:0:7}" "1/2 回滚到上一版" 0

echo "-- 2/2 恢复新版本"
deploy_image "$CURRENT_IMAGE" "$CURRENT_SHA" "$CURRENT_BRANCH" "恢复当前版" 1

echo "RESULT: PASS（已回滚到 $PREVIOUS_IMAGE 并通过 live/ready/version 校验，再恢复 $CURRENT_IMAGE）"
