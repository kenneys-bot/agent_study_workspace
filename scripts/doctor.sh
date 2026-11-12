#!/usr/bin/env bash
# make doctor：检查本机依赖、配置存在性、目录与端口。绝不打印秘密值。
set -u
cd "$(dirname "$0")/.."

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }

echo "== 工具链 =="
python3 -V >/dev/null 2>&1 && ok "python3 $(python3 -V 2>&1 | awk '{print $2}')" || bad "python3 不可用"
command -v uv >/dev/null && ok "uv $(uv --version 2>/dev/null | awk '{print $2}')" || bad "uv 未安装（见 ADR-0001）"
command -v docker >/dev/null && ok "docker $(docker --version 2>/dev/null | sed 's/Docker version //;s/,.*//')" || warn "docker 未安装（M2 起需要 PostgreSQL）"
command -v psql >/dev/null && ok "psql $(psql --version 2>/dev/null | awk '{print $3}')" || warn "psql 未安装（可选，用容器即可）"
command -v make >/dev/null && ok "make $(make -v 2>/dev/null | head -1 | awk '{print $3}')" || bad "make 未安装"

echo "== 配置 =="
if [ -f .env ]; then
  ok ".env 存在（不显示内容）"
  for k in DEEPSEEK_API_KEY LLM_API_KEY; do
    grep -q "^${k}=..*" .env && ok "$k 已配置" || warn "$k 为空（对应链路不可用）"
  done
else
  bad ".env 不存在：cp .env.example .env 后填写凭据"
fi
[ -f .env.example ] && ok ".env.example 存在" || bad ".env.example 缺失"

echo "== 仓库状态 =="
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
echo "  分支 $(git branch --show-current 2>/dev/null || echo '?') / SHA $(git rev-parse --short HEAD 2>/dev/null || echo '?') / 未提交条目 $dirty"
[ "$dirty" -eq 0 ] && ok "工作树干净" || warn "工作树有 $dirty 项未提交（不自动处理）"

echo "== 端口 =="
for p in 8000 5173 5432; do
  if command -v lsof >/dev/null && lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1; then
    warn "端口 $p 已被占用"
  else
    ok "端口 $p 空闲"
  fi
done

echo "== 数据目录（仓库外） =="
for d in "$HOME/bank-assistant-data" "$HOME/bank-assistant-artifacts" "$HOME/bank-assistant-backups"; do
  if [ -d "$d" ]; then ok "$d 存在"; else warn "$d 不存在（M0-03 创建）"; fi
done

echo
[ "$fail" -eq 0 ] && echo "doctor: 通过（警告项不阻断）" || { echo "doctor: 存在失败项" >&2; exit 1; }
