#!/usr/bin/env bash
# make rollback：回滚演练（M7-05/T29）——切回上一版不可变镜像，验证，再切回当前版。
#
# 不是盲目降级：
# - 只回滚应用镜像，不降数据库 schema（本项目迁移采用向后兼容的扩展方式；
#   回滚前确认 head 未变，本次演练不执行 alembic downgrade）；
# - 不重启服务器、不停止数据库、不触碰其它容器；
# - 每一步都验证 /health/live、/health/ready 与版本页 code_sha；
# - 回滚段先停 worker、只起 api（旧镜像/旧编排没有常驻 worker，T41 起才有），
#   避免旧代码 worker 继续消费新任务；恢复段再一起起 api + worker；
# - 结束时必须回到部署前记录的当前镜像，服务恢复新版本。
#
# 回滚点来自部署脚本写入的 $REMOTE_ROOT/deploy/state.env（非秘密）。
# 可通过 BACK_TO=<镜像> / BACK_TO_SHA=<sha> 显式指定目标，默认使用 state 里的上一版。
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
[ -n "$STATE" ] || fail "没有部署状态 ${STATE_FILE}；先 make deploy"

field() { printf '%s\n' "$STATE" | sed -n "s/^$1=//p" | head -1; }
CURRENT_IMAGE="$(field CURRENT_IMAGE)"
CURRENT_SHA="$(field CURRENT_SHA)"
CURRENT_BRANCH="$(field CURRENT_BRANCH)"
[ -n "$CURRENT_BRANCH" ] || CURRENT_BRANCH="integration"
PREVIOUS_IMAGE="${BACK_TO:-$(field PREVIOUS_IMAGE)}"
PREVIOUS_SHA="${BACK_TO_SHA:-$(field PREVIOUS_SHA)}"
[ -n "$PREVIOUS_IMAGE" ] || fail "没有上一版镜像可回滚（首次部署）；用 BACK_TO=<镜像> 指定"
remote "docker image inspect '$PREVIOUS_IMAGE' >/dev/null 2>&1" \
  || fail "上一版镜像不在服务器上：$PREVIOUS_IMAGE"
[ -n "$CURRENT_IMAGE" ] || fail "state 缺少 CURRENT_IMAGE"

APP_UID="$(remote id -u)"
APP_GID="$(remote id -g)"

compose_env() { # compose_env <image> <sha> <branch>，供 up/stop 复用
  local image="$1" sha="$2" branch="$3"
  printf "cd '%s' && APP_ENV_FILE='%s' APP_DATA_DIR='%s' APP_ARTIFACT_DIR='%s' " \
    "$REMOTE_REPO" "$APP_ENV_FILE" "$REMOTE_ROOT/data" "$REMOTE_ROOT/artifacts"
  printf "APP_UID='%s' APP_GID='%s' API_PORT='%s' " "$APP_UID" "$APP_GID" "$API_PORT"
  printf "API_IMAGE='%s' APP_GIT_SHA='%s' APP_GIT_BRANCH='%s' " "$image" "$sha" "$branch"
}

deploy_image() { # deploy_image <image> <sha> <branch> <label> <require_index> <with_worker>
  local image="$1" sha="$2" branch="$3" label="$4" require_index="$5" with_worker="$6"
  local services="api"
  [ "$with_worker" = "1" ] && services="api worker"
  echo "-- ${label}：up ${services} @ $image"
  if [ "$with_worker" != "1" ]; then
    # 旧版没有 worker 服务：先停掉当前 worker，避免旧代码继续消费任务。
    remote "$(compose_env "$image" "$sha" "$branch") docker compose -f '$COMPOSE_FILE' stop worker 2>/dev/null || true"
  fi
  remote "$(compose_env "$image" "$sha" "$branch") docker compose -f '$COMPOSE_FILE' up -d --wait --wait-timeout 180 $services"
  DEPLOY_HOST="$DEPLOY_HOST" API_PORT="$API_PORT" \
    EXPECT_CODE_SHA="$sha" REQUIRE_INDEX="$require_index" EXPECT_WORKER="$with_worker" \
    bash "$REPO_ROOT/scripts/verify_deployment.sh"
}

echo "== 回滚演练：${CURRENT_IMAGE}（${CURRENT_SHA}）→ ${PREVIOUS_IMAGE}（${PREVIOUS_SHA}）"
remote "docker inspect -f '{{.State.Health.Status}}' bank-assistant-db-db-1"

# 确认 schema 未变化：回滚旧代码前检查 alembic head，不盲目降库。
echo "-- 记录当前 alembic head（回滚不执行 downgrade）"
remote "docker exec bank-assistant-db-db-1 psql -U bank -d bank_assistant_demo -At -c 'select version_num from alembic_version'"

# 回滚段：旧镜像 + 只起 api（worker 停下并断言未运行）。
deploy_image "$PREVIOUS_IMAGE" "$PREVIOUS_SHA" "rollback-to-${PREVIOUS_SHA:0:7}" "1/2 回滚到上一版（仅 api）" 0 0

echo "-- 2/2 恢复新版本（api + worker）"
deploy_image "$CURRENT_IMAGE" "$CURRENT_SHA" "$CURRENT_BRANCH" "恢复当前版" 1 1

echo "RESULT: PASS（已回滚到 $PREVIOUS_IMAGE 并通过 live/ready/version 校验，再恢复 ${CURRENT_IMAGE} 的 api+worker）"
