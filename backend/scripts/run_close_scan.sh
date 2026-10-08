#!/usr/bin/env bash
# Full close pipeline: v2_3_scan then publish_close_v318
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TB="$ROOT/backend/trend_breakout"
export TREND_BREAKOUT_ROOT="${TREND_BREAKOUT_ROOT:-$TB}"
export ASHARE_KLINE_ROOT="${ASHARE_KLINE_ROOT:-$ROOT/data/sample/kline}"
cd "$TB"
echo "[1/2] v2_3_scan (needs full kline under ASHARE_KLINE_ROOT=$ASHARE_KLINE_ROOT)"
python v2_3_scan.py "$@"
echo "[2/2] publish_close_v318"
python -m v3.publish_close_v318
echo "Done. See push_close_v318_*.md under $TB"
