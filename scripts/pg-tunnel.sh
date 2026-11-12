#!/usr/bin/env bash
# make db-tunnel / db-tunnel-stop：管理到 Ubuntu PostgreSQL 的 SSH 本地转发（ADR-0006）。
#
#   ssh -N -L 15432:127.0.0.1:5432 my-ubuntu-server
#
# 幂等：已在运行则直接返回；pidfile 落在仓库内 .local-data/（已 gitignore）。
# 只管理自己启动的 ssh 进程；端口被其它进程占用时明确失败，不杀未知进程。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

HOST="${UBUNTU_HOST:-my-ubuntu-server}"
PORT="${DB_TUNNEL_PORT:-15432}"
LOCAL_DATA="$REPO_ROOT/.local-data"
PIDFILE="$LOCAL_DATA/pg-tunnel.pid"
LOGFILE="$LOCAL_DATA/pg-tunnel.log"

mkdir -p "$LOCAL_DATA"

port_listening() {
  nc -z -w 2 127.0.0.1 "$PORT" >/dev/null 2>&1
}

pid_alive() {
  [ -n "${1:-}" ] && kill -0 "$1" >/dev/null 2>&1
}

read_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid=$(tr -d '[:space:]' < "$PIDFILE")
  [ -n "$pid" ] && printf '%s' "$pid"
}

status() {
  local pid
  if pid=$(read_pid) && pid_alive "$pid" && port_listening; then
    echo "pg-tunnel: 运行中 pid=${pid} 127.0.0.1:$PORT → $HOST:127.0.0.1:5432"
    return 0
  fi
  if port_listening; then
    echo "pg-tunnel: 端口 127.0.0.1:$PORT 有监听，但不是本脚本记录的进程" >&2
    return 1
  fi
  echo "pg-tunnel: 未运行"
  return 1
}

start() {
  local pid
  if pid=$(read_pid) && pid_alive "$pid" && port_listening; then
    echo "pg-tunnel: 已在运行 pid=${pid}（127.0.0.1:$PORT → ${HOST}）"
    return 0
  fi
  # 清理过期 pidfile
  if [ -f "$PIDFILE" ]; then
    rm -f "$PIDFILE"
  fi
  if port_listening; then
    echo "FAIL: 127.0.0.1:$PORT 已被其它进程占用；未做任何处理。" >&2
    echo "      用 lsof -nP -iTCP:$PORT -sTCP:LISTEN 查看后手工处理。" >&2
    return 1
  fi

  echo "-- 建立隧道 127.0.0.1:$PORT → $HOST:127.0.0.1:5432"
  # BatchMode：缺密钥时直接失败而不是卡在密码提示。
  nohup ssh -N \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L "127.0.0.1:${PORT}:127.0.0.1:5432" \
    "$HOST" >>"$LOGFILE" 2>&1 &
  pid=$!
  printf '%s\n' "$pid" > "$PIDFILE"

  local i
  for i in $(seq 1 40); do
    if port_listening; then
      echo "pg-tunnel: 已建立 pid=${pid}；DATABASE_URL 指向 127.0.0.1:$PORT"
      return 0
    fi
    if ! pid_alive "$pid"; then
      break
    fi
    sleep 0.25
  done

  echo "FAIL: 隧道未能在 10 秒内建立在 127.0.0.1:$PORT" >&2
  if [ -s "$LOGFILE" ]; then
    echo "-- ssh 日志（不含凭据）：" >&2
    tail -20 "$LOGFILE" >&2 || true
  fi
  pid_alive "$pid" && kill "$pid" >/dev/null 2>&1 || true
  rm -f "$PIDFILE"
  echo "检查：ssh $HOST true；服务器上 PostgreSQL 容器是否健康。" >&2
  return 1
}

stop() {
  local pid
  if pid=$(read_pid); then
    if pid_alive "$pid"; then
      echo "-- 停止隧道 pid=${pid}"
      kill "$pid" >/dev/null 2>&1 || true
      local i
      for i in $(seq 1 20); do
        pid_alive "$pid" || break
        sleep 0.1
      done
      pid_alive "$pid" && kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$PIDFILE"
  else
    rm -f "$PIDFILE"
  fi

  # 等待端口释放（ssh 退出到内核回收监听有短暂延迟）
  local i
  for i in $(seq 1 20); do
    port_listening || break
    sleep 0.1
  done
  if port_listening; then
    echo "WARN: 127.0.0.1:$PORT 仍有监听，可能不是本脚本启动的进程；未处理。" >&2
    return 1
  fi
  echo "pg-tunnel: 已停止（127.0.0.1:$PORT 已释放）"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "用法: $0 {start|stop|status}" >&2; exit 2 ;;
esac
