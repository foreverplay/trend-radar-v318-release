"""Central path configuration for Trend Radar V3.1.8 package.

Environment variables:
  TREND_BREAKOUT_ROOT  — project root (default: this package directory)
  ASHARE_KLINE_ROOT    — K-line data root (default: ../../data/sample/kline
                         relative to this file, i.e. package data/sample/kline)
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG = Path(__file__).resolve().parent

def _default_kline_root() -> Path:
    # backend/trend_breakout -> backend -> release -> data/sample/kline
    cand = _PKG.parent.parent / "data" / "sample" / "kline"
    if cand.is_dir():
        return cand
    # fallback: sibling ashare-kline (legacy box layout)
    legacy = Path("/workspace/ashare-kline")
    if legacy.is_dir():
        return legacy
    return cand


ROOT = Path(os.environ.get("TREND_BREAKOUT_ROOT", str(_PKG))).expanduser().resolve()
KLINE_ROOT = Path(os.environ.get("ASHARE_KLINE_ROOT", str(_default_kline_root()))).expanduser().resolve()
LOCAL_DAILY = KLINE_ROOT / "raw" / "daily"
CACHE_DIR = ROOT / "cache"
CONTINUITY_CACHE = CACHE_DIR / "continuity_history"
HISTORY_DIR = ROOT / "history"
LOGS_DIR = ROOT / "logs"

# Ensure common dirs exist when imported in a fresh checkout
for _d in (CACHE_DIR, CONTINUITY_CACHE, HISTORY_DIR, LOGS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
