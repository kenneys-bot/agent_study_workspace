#!/usr/bin/env bash
# make release-verify（T31/#32）：把发布清单 release.json 与**正在运行的部署**逐项核对。
#
# 只读：SSH 回环 HTTP + docker inspect + psql SELECT；不改配置、不重启、不写数据。
# 任一不一致非零退出，避免“文档写 A、线上跑 B”的静默漂移。
#
# 核对项（与 issue #32 对应）：
#   - 运行容器 image id 与标签；
#   - 容器运行/健康状态；
#   - Alembic head；
#   - /health/live、/health/ready；
#   - /api/v1/system/version 的 code_sha、活动索引版本、校准 sha256、
#     索引 embedding 模型、生成 provider/模型。
#
# 环境变量：RELEASE_MANIFEST、DEPLOY_HOST、API_PORT、DEMO_USER、DEMO_PASSWORD。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

MANIFEST="${RELEASE_MANIFEST:-$REPO_ROOT/release.json}"
DEPLOY_HOST="${DEPLOY_HOST:-my-ubuntu-server}"
API_PORT="${API_PORT:-8000}"
DEMO_USER="${DEMO_USER:-demo-author}"
# 与 scripts/seed_demo_accounts.py 一致的公开演示密码，不是任何环境的秘密。
DEMO_PASSWORD="${DEMO_PASSWORD:-demo-only-correct-horse-battery}"
BASE_URL="http://127.0.0.1:$API_PORT"

fail() { echo "FAIL: $*" >&2; exit 1; }
remote() { ssh "$DEPLOY_HOST" "$@"; }

[ -f "$MANIFEST" ] || fail "找不到发布清单：$MANIFEST"

# 从 JSON 提取本脚本需要的字段；用 shlex.quote 安全导入 shell，避免手工解析。
eval "$(
  MANIFEST="$MANIFEST" python3 - <<'PY'
import json, os, shlex, sys

path = os.environ["MANIFEST"]
try:
    data = json.load(open(path, encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"FAIL: 无法读取发布清单 {path}: {exc}")

def get(dotted, default=""):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node

fields = {
    "REL_VERSION": "release.version",
    "CODE_SHA": "code.git_sha",
    "IMAGE_TAG": "image.tag",
    "IMAGE_ID": "image.id",
    "CONTAINER": "deployment.container",
    "DEPLOY_PORT": "deployment.port",
    "DB_CONTAINER": "database.container",
    "DB_NAME": "database.name",
    "DB_USER": "database.user",
    "ALEMBIC_HEAD": "database.alembic_head",
    "INDEX_VERSION": "index.index_version",
    "INDEX_CALIB": "index.calibration_sha256",
    "INDEX_EMBED": "index.embedding_model_version",
    "MODEL_PROVIDER": "models.generation_provider",
    "MODEL_CHAT": "models.generation_model",
}
missing = [dotted for dotted in fields.values() if get(dotted, None) in (None, "")]
if missing:
    sys.exit("FAIL: 发布清单缺少字段：" + ", ".join(missing))

for name, dotted in fields.items():
    print(f"{name}={shlex.quote(str(get(dotted)))}")
PY
)"

[ -n "${IMAGE_ID:-}" ] && [ -n "${ALEMBIC_HEAD:-}" ] && [ -n "${INDEX_VERSION:-}" ] \
  || fail "release.json 字段解析失败（见上方错误）"
[ -n "${DEPLOY_PORT:-}" ] && [ "$DEPLOY_PORT" != "null" ] && API_PORT="$DEPLOY_PORT"
BASE_URL="http://127.0.0.1:$API_PORT"

echo "== release-verify ${REL_VERSION}（code ${CODE_SHA:0:7}，image ${IMAGE_TAG}）→ ${DEPLOY_HOST}"

echo "-- 1) 容器与镜像身份"
CONTAINER_INFO="$(remote "docker inspect -f '{{.State.Status}}|{{.State.Health.Status}}|{{.Image}}|{{.Config.Image}}' '$CONTAINER'")" \
  || fail "无法读取容器 $CONTAINER"
IFS='|' read -r RUN_STATUS RUN_HEALTH RUN_IMAGE RUN_TAG <<< "$CONTAINER_INFO"
[ "$RUN_STATUS" = "running" ] || fail "容器状态是 $RUN_STATUS，不是 running"
[ "$RUN_HEALTH" = "healthy" ] || fail "容器健康状态是 $RUN_HEALTH，不是 healthy"
check_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" != "$actual" ]; then
    fail "$label 不一致：清单=$expected 线上=$actual"
  fi
  echo "   OK $label=$actual"
}
check_eq "image id" "$IMAGE_ID" "$RUN_IMAGE"
check_eq "image tag" "$IMAGE_TAG" "$RUN_TAG"
TAG_IMAGE_ID="$(remote "docker image inspect -f '{{.Id}}' '$IMAGE_TAG'")" \
  || fail "服务器上没有镜像 $IMAGE_TAG"
check_eq "image tag id" "$IMAGE_ID" "$TAG_IMAGE_ID"

echo "-- 2) 健康探针（服务器回环）"
LIVE="$(remote "curl -fsS --max-time 5 '$BASE_URL/health/live'")" \
  || fail "/health/live 不可用"
READY="$(remote "curl -fsS --max-time 5 '$BASE_URL/health/ready'")" \
  || fail "/health/ready 不可用（数据库依赖未就绪）"
case "$LIVE" in
  *'"status":"alive"'*) echo "   OK /health/live → $LIVE" ;;
  *) fail "/health/live 返回非预期：$LIVE" ;;
esac
case "$READY" in
  *'"status":"ready"'*) echo "   OK /health/ready → database ok" ;;
  *) fail "/health/ready 返回非预期：$READY" ;;
esac

echo "-- 3) Alembic head"
ACTUAL_HEAD="$(remote "docker exec '$DB_CONTAINER' psql -U '$DB_USER' -d '$DB_NAME' -At -c 'select version_num from alembic_version'")" \
  || fail "无法读取 $DB_NAME 的 alembic_version"
ACTUAL_HEAD="$(printf '%s' "$ACTUAL_HEAD" | tr -d '[:space:]')"
check_eq "alembic head" "$ALEMBIC_HEAD" "$ACTUAL_HEAD"

echo "-- 4) /api/v1/system/version（登录令牌不打印）"
LOGIN_JSON="$(python3 - "$DEMO_USER" "$DEMO_PASSWORD" <<'PY'
import json, sys
print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))
PY
)"
TOKEN="$(remote "curl -fsS --max-time 10 -X POST '$BASE_URL/api/v1/auth/login' \
  -H 'Content-Type: application/json' --data-binary '$LOGIN_JSON'" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')" \
  || fail "演示账号登录失败"
[ -n "$TOKEN" ] || fail "登录未返回令牌"
VERSION_JSON="$(remote "curl -fsS --max-time 10 -H 'Authorization: Bearer $TOKEN' '$BASE_URL/api/v1/system/version'")" \
  || fail "/api/v1/system/version 不可用"

VERSION_JSON="$VERSION_JSON" \
CODE_SHA="$CODE_SHA" INDEX_VERSION="$INDEX_VERSION" INDEX_CALIB="$INDEX_CALIB" \
INDEX_EMBED="$INDEX_EMBED" MODEL_PROVIDER="$MODEL_PROVIDER" MODEL_CHAT="$MODEL_CHAT" \
REL_VERSION="$REL_VERSION" python3 - <<'PY'
import json, os, sys

data = json.loads(os.environ["VERSION_JSON"])
checks = {
    "code_sha": ("CODE_SHA", data.get("code_sha")),
    "index_version": ("INDEX_VERSION", data.get("index_version")),
    "index_calibration_sha256": ("INDEX_CALIB", data.get("index_calibration_sha256")),
    "index_embedding_model": ("INDEX_EMBED", data.get("index_embedding_model")),
    "model_provider": ("MODEL_PROVIDER", data.get("model_provider")),
    "model_chat": ("MODEL_CHAT", data.get("model_chat")),
}
errors = []
for label, (env, actual) in checks.items():
    expected = os.environ.get(env, "")
    if actual != expected:
        errors.append(f"{label} 不一致：清单={expected} 线上={actual}")
    else:
        print(f"   OK {label}={actual}")
print(f"   版本页模式 mode={data.get('mode')} app_version={data.get('app_version')}")
if errors:
    print("FAIL: " + "; ".join(errors), file=sys.stderr)
    sys.exit(1)
PY

echo "RESULT: PASS（release ${REL_VERSION}：image id/标签、容器健康、Alembic head、健康探针、版本页字段全部一致）"
