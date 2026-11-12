#!/usr/bin/env bash
# make smoke：真实构建并启动 API 容器，验证存活、就绪与失败路径。
# 任何断言失败都非零退出；无论成败都会清理冒烟用的容器（独立 compose 项目）。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE=(docker compose -p bank-assistant-smoke -f deploy/compose.yml)
IMAGE="${SMOKE_IMAGE:-bank-assistant:dev}"
PORT="${SMOKE_PORT:-${API_PORT:-8000}}"
BASE="http://127.0.0.1:${PORT}"
TMP_DIR="$(mktemp -d)"

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1" >&2; exit 1; }

cleanup() {
  "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# 冒烟只验证进程与路由，不调用真实模型；假值不是可用凭据。
export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-smoke-placeholder-not-a-secret}"
export SECRET_KEY="${SECRET_KEY:-smoke-placeholder-not-a-secret}"
# 默认指向关闭的端口：稳定验证 readiness 的 503 失败路径（真实 PostgreSQL 由
# `make test-integration` 覆盖）。若调用方显式导出可用 DATABASE_URL，则验证 200 路径。
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://smoke:smoke@127.0.0.1:1/smoke}"
export APP_ENV="${APP_ENV:-testing}"
export API_PORT="$PORT"

echo "smoke: 端口 ${PORT}，镜像 ${IMAGE}"

# 1) 镜像：没有就按 Compose 构建（make smoke 可以独立运行）。
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "smoke: 镜像不存在，先构建"
  "${COMPOSE[@]}" build api || fail "docker compose build 失败"
fi

# 2) 启动容器（只用已构建镜像，避免冒烟过程中隐式重建）。
if ! "${COMPOSE[@]}" up -d --no-build api >"$TMP_DIR/up.log" 2>&1; then
  cat "$TMP_DIR/up.log" >&2
  fail "容器启动失败（端口 ${PORT} 可能被占用，可用 SMOKE_PORT=其它端口重试）"
fi
pass "容器已启动"

# 3) 等存活探针返回 200（最多 60 秒）。
LIVE_CODE="000"
for _ in $(seq 1 60); do
  LIVE_CODE="$(curl -s -o "$TMP_DIR/live.json" -w '%{http_code}' --max-time 2 "$BASE/health/live" || true)"
  [ "$LIVE_CODE" = "200" ] && break
  sleep 1
done
if [ "$LIVE_CODE" != "200" ]; then
  "${COMPOSE[@]}" logs api >&2 || true
  fail "GET /health/live 未在 60 秒内返回 200（实际 ${LIVE_CODE}）"
fi
if ! python3 - "$TMP_DIR/live.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data.get("status") == "alive", f"/health/live 状态应为 alive，实际 {data!r}"
PY
then
  fail "/health/live 响应体不诚实"
fi
pass "GET /health/live -> 200，status=alive"

# 4) 就绪探针：只接受 200（依赖可用）或 503（标准错误结构），其它都是故障；
#    两个分支都必须如实反映 PostgreSQL 状态，不能空口声明就绪。
READY_CODE="$(curl -s -o "$TMP_DIR/ready.json" -w '%{http_code}' --max-time 8 "$BASE/health/ready" || true)"
if ! python3 - "$TMP_DIR/ready.json" "$READY_CODE" <<'PY'
import json, sys
code = int(sys.argv[2])
data = json.load(open(sys.argv[1], encoding="utf-8"))
if code == 200:
    assert data.get("status") == "ready", f"200 时 status 应为 ready，实际 {data!r}"
    assert data.get("checks", {}).get("database") == "ok", \
        f"200 时必须声明 database 检查通过，实际 {data!r}"
elif code == 503:
    assert data.get("code") == "DEPENDENCY_UNAVAILABLE", f"503 错误码不符：{data!r}"
    assert data.get("retryable") is True, f"503 必须标记 retryable：{data!r}"
    assert data.get("details", {}).get("dependency") == "postgresql", \
        f"503 必须指明失败依赖：{data!r}"
    assert data.get("trace_id"), f"503 必须带 trace_id：{data!r}"
    assert data.get("status") != "ready", f"503 时不能声称 ready：{data!r}"
else:
    raise AssertionError(f"/health/ready 只允许 200 或 503，实际 {code}：{data!r}")
PY
then
  fail "/health/ready 行为不诚实"
fi
pass "GET /health/ready -> ${READY_CODE}（与 PostgreSQL 实际可用性一致）"

# 5) 失败路径：不存在的路由必须是 4xx（FastAPI 默认 404），不能 200/5xx。
NOT_FOUND_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/definitely-not-a-route" || true)"
case "$NOT_FOUND_CODE" in
  4??) pass "GET /definitely-not-a-route -> ${NOT_FOUND_CODE}（4xx）" ;;
  *) fail "未知路由应返回 4xx，实际 ${NOT_FOUND_CODE}" ;;
esac

# 6) 容器自身 healthcheck 必须报告 healthy（证明 compose 的探测路径可用）。
HEALTH="unknown"
for _ in $(seq 1 60); do
  CID="$("${COMPOSE[@]}" ps -q api 2>/dev/null || true)"
  [ -n "$CID" ] || break
  HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CID" 2>/dev/null || echo unknown)"
  [ "$HEALTH" = "healthy" ] && break
  sleep 1
done
[ "$HEALTH" = "healthy" ] || fail "容器 healthcheck 未变为 healthy（实际 ${HEALTH}）"
pass "容器 healthcheck -> healthy"

echo "smoke: 全部通过"
