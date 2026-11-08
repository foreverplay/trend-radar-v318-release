#!/usr/bin/env bash
# 单独打开研究页。不重启已经占用 8765、5173、8790 的进程。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
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
if [[ ! -x "$PY" ]]; then
  echo "解释器不存在或不可执行: RESEARCH_PYTHON"
  exit 1
fi
export API_HOST="${API_HOST:-127.0.0.1}"
export API_PORT="${RESEARCH_API_PORT:-8791}"
export RESEARCH_RESULTS_ROOT="${RESEARCH_RESULTS_ROOT:-$ROOT/backend/trend_breakout/research_lab/results}"
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
  echo "端口 ${API_PORT} 已有进程。不要动 8765、5173、8790。换一个空端口，例如 RESEARCH_API_PORT=8792。"
  exit 1
fi
echo "打开 http://${API_HOST}:${API_PORT}/research"
echo "健康检查 http://${API_HOST}:${API_PORT}/api/health"
cd "$ROOT/backend/api"
exec "$PY" -m uvicorn app:app --host "$API_HOST" --port "$API_PORT"
