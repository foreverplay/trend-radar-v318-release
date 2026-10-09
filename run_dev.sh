#!/usr/bin/env bash
# 一键启动 FastAPI 后端 + Vite 前端（sample 模式，Ctrl+C 同时退出）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

API_PID=""
FRONT_PID=""
HUB_PID=""

cleanup() {
  local p
  for p in "$API_PID" "$FRONT_PID" "$HUB_PID"; do
    if [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null; then
      kill "$p" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[run_dev] 已创建 .env（来自 .env.example）"
fi

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
elif command -v python3 >/dev/null 2>&1; then
  echo "[run_dev] 未找到 .venv，使用当前 python3（建议先: python3 -m venv .venv && pip install -r requirements.txt）"
else
  echo "[run_dev] 需要 Python 3.11+" >&2
  exit 1
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "[run_dev] 正在安装前端依赖…"
  (cd "$ROOT/frontend" && npm install)
fi

echo "[run_dev] 后端 API: http://127.0.0.1:${API_PORT:-8765}/api/health"
echo "[run_dev] 前端页面: http://127.0.0.1:5173"
echo "[run_dev] 按 Ctrl+C 停止前后端"

env_get() {
  local key="$1"
  [[ -f "$ROOT/.env" ]] || return 0
  sed -n "s/^${key}=//p" "$ROOT/.env" | tail -1
}

MARKET_HUB_URL="${MARKET_HUB_URL:-$(env_get MARKET_HUB_URL)}"
MARKET_HUB_DIR="${MARKET_HUB_DIR:-$(env_get MARKET_HUB_DIR)}"
MARKET_DB_PATH="${MARKET_DB_PATH:-$(env_get MARKET_DB_PATH)}"
export MARKET_HUB_URL MARKET_DB_PATH

if [[ -n "${MARKET_HUB_URL}" && -n "${MARKET_HUB_DIR}" ]]; then
  HUB_ABS="$(cd "$ROOT" && cd "$MARKET_HUB_DIR" && pwd)"
  HUB_PORT="${HUB_PORT:-8790}"
  if ! lsof -nP -iTCP:"$HUB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[run_dev] 行情后台: ${MARKET_HUB_URL}"
    (cd "$HUB_ABS" && ./run_hub.sh) &
    HUB_PID=$!
  else
    echo "[run_dev] 行情后台已在 ${HUB_PORT} 监听"
  fi
fi

bash "$ROOT/backend/scripts/run_api.sh" &
API_PID=$!

(cd "$ROOT/frontend" && npm run dev) &
FRONT_PID=$!

wait "$API_PID" "$FRONT_PID"
