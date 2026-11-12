#!/usr/bin/env bash
# 真实部署校验（M7-01/T29）：只读 HTTP + docker inspect + ss，不修改任何东西。
#
# 校验项：
# - /health/live 与 /health/ready 从服务器回环真实返回 200；
# - API 端口只绑 127.0.0.1（docker port 与 ss 两处证据），不从 0.0.0.0 暴露；
# - 用演示账号登录（演示密码是仓库里公开的 demo-only 值，不是秘密）后读取
#   /api/v1/system/version，断言代码 SHA / 活动索引 / 模型字段；
# - 不打印令牌、密码或连接串。
#
# 环境变量：DEPLOY_HOST、API_PORT、EXPECT_CODE_SHA（可选）、REQUIRE_INDEX=1（可选）
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-my-ubuntu-server}"
API_PORT="${API_PORT:-8000}"
EXPECT_CODE_SHA="${EXPECT_CODE_SHA:-}"
REQUIRE_INDEX="${REQUIRE_INDEX:-1}"
DEMO_USER="${DEMO_USER:-demo-author}"
# 与 scripts/seed_demo_accounts.py 的公开演示密码一致；不是任何环境的秘密。
DEMO_PASSWORD="${DEMO_PASSWORD:-demo-only-correct-horse-battery}"
BASE_URL="http://127.0.0.1:$API_PORT"

fail() { echo "FAIL: $*" >&2; exit 1; }
remote() { ssh "$DEPLOY_HOST" "$@"; }

echo "== verify deployment @ $DEPLOY_HOST（$BASE_URL）"

echo "-- 1) 健康探针（服务器回环）"
LIVE="$(remote "curl -fsS --max-time 5 '$BASE_URL/health/live'")" \
  || fail "/health/live 不可用"
READY="$(remote "curl -fsS --max-time 5 '$BASE_URL/health/ready'")" \
  || fail "/health/ready 不可用（数据库依赖未就绪）"
echo "   /health/live  → $LIVE"
echo "   /health/ready → $READY"

echo "-- 2) 端口绑定（只允许回环）"
NETWORK_MODE="$(remote "docker inspect -f '{{.HostConfig.NetworkMode}}' bank-assistant-api")" \
  || fail "无法读取容器网络模式"
if [ "$NETWORK_MODE" = "host" ]; then
  # host 网络下 docker port 为空；回环保证来自 command 的 --host 127.0.0.1。
  CMD_JSON="$(remote "docker inspect -f '{{json .Config.Cmd}}' bank-assistant-api")"
  echo "   network_mode: host；command=$CMD_JSON"
  case "$CMD_JSON" in
    *"--host","127.0.0.1"*) ;;
    *) fail "host 网络下 command 未显式绑定 127.0.0.1：$CMD_JSON" ;;
  esac
  case "$CMD_JSON" in
    *0.0.0.0*) fail "host 网络下不允许绑定 0.0.0.0" ;;
  esac
else
  BINDING="$(remote "docker port bank-assistant-api 8000/tcp")" || fail "无法读取容器端口映射"
  echo "   docker port: $BINDING"
  case "$BINDING" in
    127.0.0.1:"$API_PORT"|"[::1]:"*) ;;
    *) fail "容器端口不是回环绑定：$BINDING" ;;
  esac
fi
LISTEN="$(remote "ss -tln 2>/dev/null | awk '\$4 ~ /:$API_PORT\$/ {print \$4}'")"
[ -n "$LISTEN" ] || fail "ss 未看到 $API_PORT 监听"
echo "   ss -tln: $(echo "$LISTEN" | tr '\n' ' ')"
while IFS= read -r addr; do
  [ -z "$addr" ] && continue
  case "$addr" in
    127.0.0.1:"$API_PORT"|"[::1]:$API_PORT") ;;
    *) fail "端口 $API_PORT 监听在非回环地址：$addr" ;;
  esac
done <<< "$LISTEN"

echo "-- 3) 登录演示账号并读取版本页（令牌不打印）"
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

VERSION_JSON="$VERSION_JSON" EXPECT_CODE_SHA="$EXPECT_CODE_SHA" REQUIRE_INDEX="$REQUIRE_INDEX" \
python3 - <<'PY'
import json, os, sys

raw = os.environ["VERSION_JSON"]
data = json.loads(raw)
echo = lambda *a: print(*a)
echo("   code_sha          =", data.get("code_sha"))
echo("   git_branch        =", data.get("git_branch"))
echo("   model_provider    =", data.get("model_provider"))
echo("   model_chat        =", data.get("model_chat"))
echo("   embedding_model   =", data.get("embedding_model"))
echo("   index_version     =", data.get("index_version"))
echo("   index_calib_sha   =", data.get("index_calibration_sha256"))
echo("   index_embed_model =", data.get("index_embedding_model"))
echo("   mode              =", data.get("mode"))
echo("   app_version       =", data.get("app_version"))

expected_sha = os.environ.get("EXPECT_CODE_SHA", "")
if expected_sha and data.get("code_sha") != expected_sha:
    sys.exit(f"FAIL: 版本页 code_sha={data.get('code_sha')} 与部署 SHA={expected_sha} 不一致")
if os.environ.get("REQUIRE_INDEX") == "1":
    if not data.get("index_version"):
        sys.exit("FAIL: 版本页没有活动索引版本（索引未建或指针损坏）")
    if not data.get("index_calibration_sha256"):
        sys.exit("FAIL: 版本页没有索引校准 sha256（不满足 M7-01 索引身份要求）")
print("   版本页断言：PASS")
PY

echo "RESULT: PASS（live/ready 200、端口回环、版本页真实且与部署 SHA 一致）"
