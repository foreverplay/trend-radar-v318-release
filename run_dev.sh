#!/usr/bin/env bash
# 一键启动 FastAPI 后端 + Vite 前端 + 盘中刷榜（sample 模式，Ctrl+C 同时退出）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

API_PID=""
FRONT_PID=""
HUB_PID=""
LIVE_PID=""
LIVE_DIV_PID=""

SKIP_LIVE_BOARD="${SKIP_LIVE_BOARD:-0}"
SKIP_LIVE_DIVIDEND="${SKIP_LIVE_DIVIDEND:-0}"
SKIP_A_PLUS_BACKFILL="${SKIP_A_PLUS_BACKFILL:-0}"
LIVE_BOARD_INTERVAL="${LIVE_BOARD_INTERVAL:-}"

for arg in "$@"; do
  case "$arg" in
    --no-live-board) SKIP_LIVE_BOARD=1 ;;
    --no-live-dividend) SKIP_LIVE_DIVIDEND=1 ;;
    --no-a-plus-backfill) SKIP_A_PLUS_BACKFILL=1 ;;
    --help|-h)
      cat <<'HELP'
用法: ./run_dev.sh [--no-live-board] [--no-live-dividend]

一键启动 API(8765) + Vite(5173) + 可选行情 hub(8790) + Growth/红利盘中刷榜。

环境变量:
  SKIP_LIVE_BOARD=1          不启动 Growth 盘中刷榜（同 --no-live-board）
  SKIP_LIVE_DIVIDEND=1       不启动红利盘中刷榜（同 --no-live-dividend）
  SKIP_A_PLUS_BACKFILL=1     不启动 A+ 历史回补后台（同 --no-a-plus-backfill）
  LIVE_BOARD_INTERVAL=1500   Growth 刷榜间隔秒数，默认 1500（约 25 分钟）
HELP
      exit 0
      ;;
  esac
done

cleanup() {
  local p
  for p in "$API_PID" "$FRONT_PID" "$HUB_PID" "$LIVE_PID" "$LIVE_DIV_PID"; do
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
echo "[run_dev] 按 Ctrl+C 停止前后端与刷榜"

env_get() {
  local key="$1"
  [[ -f "$ROOT/.env" ]] || return 0
  sed -n "s/^${key}=//p" "$ROOT/.env" | tail -1
}

MARKET_HUB_URL="${MARKET_HUB_URL:-$(env_get MARKET_HUB_URL)}"
MARKET_HUB_DIR="${MARKET_HUB_DIR:-$(env_get MARKET_HUB_DIR)}"
MARKET_DB_PATH="${MARKET_DB_PATH:-$(env_get MARKET_DB_PATH)}"
export MARKET_HUB_URL MARKET_DB_PATH

kick_hub_refresh() {
  local url="${MARKET_HUB_URL%/}"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if curl -sf --max-time 2 "${url}/api/health" >/dev/null; then
      if curl -sf --max-time 5 -X POST "${url}/api/qfq/sync" >/dev/null; then
        echo "[run_dev] 已让行情后台立即拉一轮最新行情"
      else
        echo "[run_dev] 行情后台在监听，但立即刷新没有成功。交易时段里它仍会按约 2 分钟一轮自己拉"
      fi
      return 0
    fi
    sleep 1
  done
  echo "[run_dev] 行情后台还没就绪，当日行情要等它起来之后才会拉"
}

if [[ -n "${MARKET_HUB_URL}" && -n "${MARKET_HUB_DIR}" ]]; then
  HUB_ABS="$(cd "$ROOT" && cd "$MARKET_HUB_DIR" && pwd)"
  HUB_PORT="${HUB_PORT:-8790}"
  if ! lsof -nP -iTCP:"$HUB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[run_dev] 行情后台: ${MARKET_HUB_URL}（起来后会按约 2 分钟拉一轮当日行情）"
    (cd "$HUB_ABS" && ./run_hub.sh) &
    HUB_PID=$!
  else
    echo "[run_dev] 行情后台已在 ${HUB_PORT} 监听，补一次最新行情"
    kick_hub_refresh
  fi
else
  echo "[run_dev] 未配置 MARKET_HUB_URL / MARKET_HUB_DIR，不会启动行情后台。盘中榜要等共享库里已有当日 K 线"
fi

bash "$ROOT/backend/scripts/run_api.sh" &
API_PID=$!

(cd "$ROOT/frontend" && npm run dev) &
FRONT_PID=$!

# 盘中刷榜：默认开启，写入 data/sample/boards_v318_*.json；非交易时段空转，收盘后再排一轮
if [[ "$SKIP_LIVE_BOARD" == "1" ]]; then
  echo "[run_dev] 已跳过 Growth 盘中刷榜（SKIP_LIVE_BOARD=1 或 --no-live-board）"
else
  LIVE_ARGS=(backend/scripts/live_board_v318.py)
  if [[ -n "$LIVE_BOARD_INTERVAL" ]]; then
    LIVE_ARGS+=(--interval "$LIVE_BOARD_INTERVAL")
  fi
  INTERVAL_MSG="${LIVE_BOARD_INTERVAL:-1500}"
  echo "[run_dev] Growth 盘中刷榜: live_board_v318.py（启动即重算最新交易日；间隔 ${INTERVAL_MSG}s）"
  echo "[run_dev] 导出完成前，页面仍显示 data/sample 里上次的榜单文件"
  python -u "${LIVE_ARGS[@]}" &
  LIVE_PID=$!
fi

if [[ "$SKIP_LIVE_DIVIDEND" == "1" ]]; then
  echo "[run_dev] 已跳过红利盘中刷榜（SKIP_LIVE_DIVIDEND=1 或 --no-live-dividend）"
else
  echo "[run_dev] 红利盘中刷榜: live_board_dividend_v319.py（启动即重算最新交易日 → 红利趋势三区）"
  python -u backend/scripts/live_board_dividend_v319.py &
  LIVE_DIV_PID=$!
fi

if [[ "$SKIP_A_PLUS_BACKFILL" == "1" ]]; then
  echo "[run_dev] 已跳过 A+ 历史回补（SKIP_A_PLUS_BACKFILL=1 或 --no-a-plus-backfill）"
elif [[ -n "${MARKET_DB_PATH:-$(env_get MARKET_DB_PATH)}" ]]; then
  echo "[run_dev] A+ 历史回补: backfill_a_plus_history.py（交易时段暂停，结果写入 history/a_plus_v318_backfill_by_date）"
  python -u backend/scripts/backfill_a_plus_history.py --parallel-days 1 --workers 3 &
else
  echo "[run_dev] 未配置 MARKET_DB_PATH，不启动 A+ 历史回补"
fi

wait "$API_PID" "$FRONT_PID"
