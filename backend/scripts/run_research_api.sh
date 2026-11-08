#!/usr/bin/env bash
# 单独的研究只读 API。不重启已经占用 8765 的榜单进程。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/venv/bin/python"
fi
export API_HOST="${API_HOST:-127.0.0.1}"
export API_PORT="${RESEARCH_API_PORT:-8791}"
export RESEARCH_RESULTS_ROOT="${RESEARCH_RESULTS_ROOT:-$ROOT/backend/trend_breakout/research_lab/results}"
cd "$ROOT/backend/api"
exec "$PY" -m uvicorn app:app --host "$API_HOST" --port "$API_PORT"
