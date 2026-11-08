#!/usr/bin/env bash
# 单独打开研究页。不重启已经占用 8765、5173、8790 的进程。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="$ROOT/backend/api:$ROOT/backend/trend_breakout:${PYTHONPATH:-}"
if [[ -n "${RESEARCH_PYTHON:-}" ]]; then
  PY="$RESEARCH_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PY="$ROOT/venv/bin/python"
else
  echo "没有找到 .venv。设置 RESEARCH_PYTHON=主工作目录/.venv/bin/python。不要复制那个环境，也不要 reset 主工作目录。"
  exit 1
fi
if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  echo "解释器不可用。设置 RESEARCH_PYTHON=主工作目录/.venv/bin/python"
  exit 1
fi
export API_HOST="${API_HOST:-127.0.0.1}"
export API_PORT="${RESEARCH_API_PORT:-8791}"
export RESEARCH_RESULTS_ROOT="${RESEARCH_RESULTS_ROOT:-$ROOT/backend/trend_breakout/research_lab/results}"
if curl -sf --max-time 2 "http://${API_HOST}:${API_PORT}/api/research/status" | grep -q run_button; then
  echo "研究 API 已在 http://${API_HOST}:${API_PORT}/research"
  exit 0
fi
if ! "$PY" - "$API_HOST" "$API_PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
sock = socket.socket()
try:
    sock.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
then
  echo "端口 ${API_PORT} 已被其他进程占用。执行 ./research up 会自动换端口；不要动 8765、5173、8790。"
  exit 1
fi
echo "打开 http://${API_HOST}:${API_PORT}/research"
echo "健康检查 http://${API_HOST}:${API_PORT}/api/health"
cd "$ROOT/backend/api"
exec "$PY" -m uvicorn app:app --host "$API_HOST" --port "$API_PORT"
