#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export TREND_BREAKOUT_ROOT="${TREND_BREAKOUT_ROOT:-$ROOT/backend/trend_breakout}"
export ASHARE_KLINE_ROOT="${ASHARE_KLINE_ROOT:-$ROOT/data/sample/kline}"
cd "$ROOT/backend/api"
exec python -m uvicorn app:app --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8765}"
