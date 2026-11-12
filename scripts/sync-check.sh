#!/usr/bin/env bash
# make sync-check：报告本机与 Ubuntu 的 SHA 与未提交状态。只读，绝不覆盖任何文件。
set -u
cd "$(dirname "$0")/.."

HOST="${UBUNTU_HOST:-my-ubuntu-server}"
REMOTE_DIR="${UBUNTU_DIR:-~/projects/agent_study_workspace}"

local_sha=$(git rev-parse HEAD 2>/dev/null || echo "无提交")
local_branch=$(git branch --show-current 2>/dev/null || echo "?")
local_dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
local_core_dirty=$(git status --porcelain -- src pyproject.toml Makefile 2>/dev/null | wc -l | tr -d ' ')

echo "== 本机 =="
echo "  branch=$local_branch sha=$local_sha dirty=$local_dirty (src/build dirty=$local_core_dirty)"

echo "== Ubuntu ($HOST:$REMOTE_DIR) =="
remote_out=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "
  cd $REMOTE_DIR 2>/dev/null || { echo MISSING; exit 0; }
  echo \"sha=\$(git rev-parse HEAD) branch=\$(git branch --show-current) dirty=\$(git status --porcelain | wc -l) head_msg=\$(git log -1 --format=%s)\"
" 2>&1) || { echo "  无法连接 $HOST（检查 ~/.ssh/config / 网络）"; exit 1; }

if [ "$remote_out" = "MISSING" ]; then
  echo "  远端尚无 checkout（M0-03 建立）"
else
  echo "  $remote_out"
fi

echo
if [ "$remote_out" != "MISSING" ] && echo "$remote_out" | grep -q "sha=$local_sha"; then
  echo "sync-check: 两端 SHA 一致"
else
  echo "sync-check: 两端 SHA 不一致或远端缺失（按 M0-03 处理，不自动覆盖）"
fi
