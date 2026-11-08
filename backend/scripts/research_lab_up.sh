#!/usr/bin/env bash
# 一条命令后台打开研究页。不重启 8765、5173、8790。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE_DIR="${RESEARCH_STATE_DIR:-$ROOT/backend/trend_breakout/research_lab/.run}"
PID_FILE="$STATE_DIR/research-api.pid"
PORT_FILE="$STATE_DIR/research-api.port"
LOG_FILE="$STATE_DIR/research-api.log"
URL_FILE="$STATE_DIR/research-url.txt"
HOST="${API_HOST:-127.0.0.1}"
FIRST_PORT="${RESEARCH_API_PORT:-8791}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"

usage() {
  cat <<'HELP'
用法: ./research [up|status|open|down|doctor]

  up      默认：后台启动研究 API（如未运行），必要时写预检，并打开浏览器
  status  查看是否在运行
  open    只打开浏览器（要求已在运行）
  down    停止本脚本启动的研究 API
  doctor  启动失败时看解释器、依赖、端口和日志
  verify  用本机 HTTP 检查 /research 是否可打开（给自动化/浏览器预检）

环境变量（可选，不写 .env）:
  RESEARCH_PYTHON          默认 <仓库>/.venv/bin/python
  RESEARCH_SQLITE_PATH     默认读 .env 的 MARKET_DB_PATH，再退回到 ../longtou-web/...
  RESEARCH_PARQUET_DIR     默认 <仓库>/data/kline_market/raw/daily
  RESEARCH_RESULTS_ROOT    默认 research_lab/results
  RESEARCH_API_PORT        首选端口，默认 8791
  OPEN_BROWSER=0           不自动打开浏览器
HELP
}

env_get() {
  local key="$1"
  [[ -f "$ROOT/.env" ]] || return 0
  sed -n "s/^${key}=//p" "$ROOT/.env" | tail -1 | sed 's/^"//;s/"$//'
}

resolve_python() {
  local candidate py
  for candidate in \
    "${RESEARCH_PYTHON:-}" \
    "$ROOT/.venv/bin/python" \
    "$ROOT/venv/bin/python" \
    "$ROOT/../.venv/bin/python" \
    "$(command -v python 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"
  do
    [[ -z "$candidate" ]] && continue
    if py="$("$candidate" -c 'import sys; print(sys.executable)' 2>/dev/null)" && [[ -n "$py" ]]; then
      echo "$py"
      return 0
    fi
  done
  echo ""
}

resolve_paths() {
  export RESEARCH_RESULTS_ROOT="${RESEARCH_RESULTS_ROOT:-$ROOT/backend/trend_breakout/research_lab/results}"
  export RESEARCH_SQLITE_PATH="${RESEARCH_SQLITE_PATH:-$(env_get MARKET_DB_PATH)}"
  if [[ -z "${RESEARCH_SQLITE_PATH:-}" ]]; then
    RESEARCH_SQLITE_PATH="$(cd "$ROOT/.." && pwd)/longtou-web/backend/data/longtou.sqlite3"
  fi
  if [[ "${RESEARCH_SQLITE_PATH}" != /* ]]; then
    RESEARCH_SQLITE_PATH="$(cd "$ROOT" && cd "$(dirname "$RESEARCH_SQLITE_PATH")" && pwd)/$(basename "$RESEARCH_SQLITE_PATH")"
  fi
  export RESEARCH_SQLITE_PATH
  export RESEARCH_PARQUET_DIR="${RESEARCH_PARQUET_DIR:-$ROOT/data/kline_market/raw/daily}"
  if [[ ! -d "$RESEARCH_PARQUET_DIR" ]]; then
    local kline
    kline="$(env_get ASHARE_KLINE_ROOT)"
    if [[ -n "$kline" && -d "$ROOT/$kline" ]]; then
      RESEARCH_PARQUET_DIR="$ROOT/$kline"
    fi
  fi
  export RESEARCH_PARQUET_DIR
}

research_url() {
  local port="$1"
  echo "http://${HOST}:${port}/research"
}

health_url() {
  local port="$1"
  echo "http://${HOST}:${port}/api/research/status"
}

is_research_api() {
  local port="$1"
  local body
  body="$(curl -sf --max-time 2 "$(health_url "$port")" 2>/dev/null || true)"
  [[ -n "$body" ]] && [[ "$body" == *"precheck_state"* ]] && [[ "$body" == *"run_button"* ]]
}

is_research_page() {
  local port="$1"
  local body
  body="$(curl -sf --max-time 3 "$(research_url "$port")" 2>/dev/null || true)"
  [[ -n "$body" ]] && [[ "$body" == *"研究实验室"* ]] && [[ "$body" == *"/api/research/status"* ]]
}

api_base_8765() {
  local base="${VITE_API_BASE:-$(env_get VITE_API_BASE)}"
  base="${base:-http://127.0.0.1:8765}"
  echo "${base%/}"
}

board_api_has_research() {
  local base
  base="$(api_base_8765)"
  base="${base%/}"
  local body
  body="$(curl -sf --max-time 2 "${base}/api/research/status" 2>/dev/null || true)"
  [[ -n "$body" ]] && [[ "$body" == *"run_button"* ]]
}

board_research_url() {
  echo "$(api_base_8765)/research"
}

open_browser() {
  local url="$1"
  if [[ "$OPEN_BROWSER" != "1" ]]; then
    echo "[research] $url"
    return 0
  fi
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  else
    echo "[research] 请打开 $url"
  fi
}

read_stored_port() {
  if [[ -f "$PORT_FILE" ]]; then
    cat "$PORT_FILE"
  fi
}

running_pid() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
    return 0
  fi
  return 1
}

ensure_files() {
  if [[ ! -f "$ROOT/backend/api/research_page.html" || ! -f "$ROOT/backend/api/research_reader.py" ]]; then
    echo "[research] 当前目录没有研究页代码（main 上通常没有）。请在独立工作目录检出 PR 分支，不要 reset 主工作目录：" >&2
    echo "  git fetch origin cursor/research-backtest-integration-f9c2" >&2
    echo "  git worktree add <独立目录> cursor/research-backtest-integration-f9c2" >&2
    exit 2
  fi
}

write_precheck_if_missing() {
  local py="$1"
  if [[ -f "$RESEARCH_RESULTS_ROOT/precheck.json" ]]; then
    return 0
  fi
  echo "[research] 还没有 precheck.json，正在只读预检…"
  mkdir -p "$RESEARCH_RESULTS_ROOT"
  if ! "$py" "$ROOT/backend/trend_breakout/research_lab/precheck_local.py"; then
    echo "[research] 预检未通过或数据不可成交；页面仍会打开并显示阻断原因。"
  fi
}

pick_port() {
  local py="$1"
  local port="$FIRST_PORT"
  local stored
  if stored="$(running_pid)"; then
    port="$(read_stored_port || echo "$FIRST_PORT")"
    if is_research_api "$port"; then
      echo "$port"
      return 0
    fi
    kill "$stored" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  while (( port < FIRST_PORT + 20 )); do
    if is_research_api "$port"; then
      echo "$port"
      return 0
    fi
    if "$py" - "$HOST" "$port" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
try:
    s.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
    then
      echo "$port"
      return 0
    fi
    port=$((port + 1))
  done
  echo "[research] 找不到可用端口（${FIRST_PORT} 起）。" >&2
  exit 1
}

start_daemon() {
  local py="$1"
  local port="$2"
  mkdir -p "$STATE_DIR" "$RESEARCH_RESULTS_ROOT"
  export RESEARCH_PYTHON="$py"
  export RESEARCH_API_PORT="$port"
  export API_HOST="$HOST"
  export RESEARCH_RESULTS_ROOT RESEARCH_SQLITE_PATH RESEARCH_PARQUET_DIR
  nohup "$ROOT/backend/scripts/run_research_api.sh" >>"$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" >"$PID_FILE"
  echo "$port" >"$PORT_FILE"
  local i
  for i in $(seq 1 40); do
    if is_research_api "$port" && is_research_page "$port"; then
      echo "$port"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[research] 进程异常退出。日志: $LOG_FILE" >&2
      tail -n 40 "$LOG_FILE" >&2 || true
      exit 1
    fi
    sleep 0.25
  done
  echo "[research] 启动超时（/research 无响应）。日志: $LOG_FILE" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  exit 1
}

cmd_status() {
  if board_api_has_research; then
    echo "running embedded $(board_research_url)"
    return 0
  fi
  local port pid
  if pid="$(running_pid)"; then
    port="$(read_stored_port || echo "$FIRST_PORT")"
    if is_research_api "$port"; then
      echo "running pid=$pid port=$port url=$(research_url "$port")"
      return 0
    fi
  fi
  for port in $(seq "$FIRST_PORT" $((FIRST_PORT + 5))); do
    if is_research_api "$port"; then
      echo "running port=$port url=$(research_url "$port") (外部进程)"
      return 0
    fi
  done
  echo "stopped"
  return 1
}

cmd_down() {
  local pid
  if pid="$(running_pid)"; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -f "$PID_FILE" "$PORT_FILE" "$URL_FILE"
    echo "[research] 已停止 pid=$pid"
    return 0
  fi
  echo "[research] 没有由本脚本管理的运行中进程"
}

cmd_open() {
  local url
  if board_api_has_research; then
    url="$(board_research_url)"
  else
    local port pid
    port="$(read_stored_port || true)"
    if [[ -z "$port" ]] || ! is_research_api "$port"; then
      for port in $(seq "$FIRST_PORT" $((FIRST_PORT + 10))); do
        if is_research_api "$port"; then break; fi
        port=""
      done
    fi
    if [[ -z "$port" ]]; then
      echo "[research] 研究 API 没在运行。直接执行: ./research" >&2
      exit 1
    fi
    url="$(research_url "$port")"
  fi
  open_browser "$url"
  echo "[research] $url"
}

remember_url() {
  local port="$1"
  mkdir -p "$STATE_DIR"
  echo "http://${HOST}:${port}/research" >"$URL_FILE"
  echo "http://${HOST}:${port}/" >>"$URL_FILE"
}

open_when_ready() {
  local port="$1"
  if ! is_research_page "$port"; then
    echo "[research] 端口 $port 没有返回 /research 页面。" >&2
    return 1
  fi
  remember_url "$port"
  open_browser "$(research_url "$port")"
  echo "[research] 页面: $(research_url "$port")（也支持 http://${HOST}:${port}/ ）"
  echo "[research] 若 8791 打不开，先执行: ./research status  或  ./research verify"
}

cmd_verify() {
  local port ok=0
  if board_api_has_research; then
    for path in "$(board_research_url)" "$(api_base_8765)/"; do
      if curl -sf --max-time 3 "$path" | grep -q 研究实验室; then
        echo "ok $path"
        return 0
      fi
    done
    echo "fail embedded research page"
    return 1
  fi
  for port in $(seq "$FIRST_PORT" $((FIRST_PORT + 10))); do
    if is_research_api "$port" && is_research_page "$port"; then
      if curl -sf --max-time 3 "http://${HOST}:${port}/" | grep -q 研究实验室; then
        echo "ok http://${HOST}:${port}/"
      else
        echo "ok http://${HOST}:${port}/research"
      fi
      return 0
    fi
  done
  echo "fail no research listener on ${FIRST_PORT}-$((FIRST_PORT + 10))"
  cmd_doctor
  return 1
}

cmd_doctor() {
  echo "ROOT=$ROOT"
  ensure_files || true
  local py
  py="$(resolve_python)"
  echo "python=${py:-<未找到>}"
  if [[ -n "$py" ]]; then
    "$py" -c 'import fastapi, uvicorn, pandas; print("deps=ok fastapi", fastapi.__version__)' 2>&1 || echo "deps=missing（用主目录 .venv: RESEARCH_PYTHON=.../.venv/bin/python）"
  fi
  local port
  for port in $(seq "$FIRST_PORT" $((FIRST_PORT + 5))); do
    if is_research_api "$port"; then
      echo "api_${port}=research_status_ok page=$(is_research_page "$port" && echo ok || echo fail)"
    elif python3 - "$port" <<'PY'
import socket, sys
s=socket.socket(); s.settimeout(0.2)
try: sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1])))==0 else 1)
finally: s.close()
PY
    then
      echo "port_${port}=busy_not_research"
    else
      echo "port_${port}=free"
    fi
  done
  [[ -f "$LOG_FILE" ]] && { echo "--- log tail ---"; tail -n 30 "$LOG_FILE"; }
  cmd_status || true
}

cmd_up() {
  ensure_files
  resolve_paths
  local py
  py="$(resolve_python)"
  if [[ -z "$py" ]]; then
    echo "[research] 找不到可用 Python。设置 RESEARCH_PYTHON=主工作目录/.venv/bin/python" >&2
    cmd_doctor >&2
    exit 1
  fi
  if board_api_has_research; then
    local url
    url="$(board_research_url)"
    open_browser "$url"
    echo "[research] 已在 8765 提供研究页，无需再开 8791: $url"
    return 0
  fi
  local port
  if port="$(read_stored_port)"; then
    if running_pid >/dev/null && is_research_api "$port" && is_research_page "$port"; then
      open_when_ready "$port"
      echo "[research] 已在运行 pid=$(cat "$PID_FILE")"
      return 0
    fi
    if is_research_api "$port" && is_research_page "$port"; then
      open_when_ready "$port"
      echo "[research] 端口 $port 上已有研究 API"
      return 0
    fi
  fi
  for port in $(seq "$FIRST_PORT" $((FIRST_PORT + 10))); do
    if is_research_api "$port" && is_research_page "$port" && ! running_pid >/dev/null; then
      echo "$port" >"$PORT_FILE"
      open_when_ready "$port"
      echo "[research] 复用已在监听的研究 API"
      return 0
    fi
  done
  write_precheck_if_missing "$py"
  port="$(pick_port "$py")"
  port="$(start_daemon "$py" "$port")"
  open_when_ready "$port"
  echo "[research] 后台已启动 pid=$(cat "$PID_FILE") port=$port"
  echo "[research] 健康: http://${HOST}:${port}/api/health"
  echo "[research] 停止: ./research down  日志: $LOG_FILE"
  echo "[research] 进程在后台常驻；关闭 run_dev 终端不会停 8791。"
}

ACTION="${1:-up}"
case "$ACTION" in
  up|start) cmd_up ;;
  status) cmd_status ;;
  open) cmd_open ;;
  down|stop) cmd_down ;;
  doctor) cmd_doctor ;;
  verify) cmd_verify ;;
  url)
    if [[ -f "$URL_FILE" ]]; then head -1 "$URL_FILE"; elif cmd_status >/dev/null; then cmd_status | awk '{print $NF}'; else exit 1; fi
    ;;
  -h|--help|help) usage ;;
  *) echo "[research] 未知子命令: $ACTION" >&2; usage >&2; exit 1 ;;
esac
