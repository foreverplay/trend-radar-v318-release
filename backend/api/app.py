"""Minimal FastAPI for Trend Radar V3.1.8 boards."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from boards_loader import find_sample_root, list_board_dates, load_by_date, load_latest

app = FastAPI(
    title="趋势分析雷达 V3.1.8 API",
    description="观察池 API；禁止荐买 / 预测必涨。A+ = 图形质量，不等于今天必买。",
    version="3.1.8",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE = find_sample_root()


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": "V3.1.8",
        "sample_root": str(SAMPLE),
        "disclaimer": "观察池；禁止荐买 / 预测必涨",
    }


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    meta_path = SAMPLE / "market_meta.json"
    market = {}
    if meta_path.is_file():
        market = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "version": "V3.1.8",
        "dates": list_board_dates(SAMPLE),
        "market_meta": market,
        "boards_rules": {
            "mature_entry": "A+/A + PRIME/CONFIRMED",
            "trend_all": "A+/A/B/C",
            "watch_pool": "B/C + EARLY/BUILDING",
            "extended": "A+/A + EXTENDED",
        },
        "disclaimer": "观察池；A+≠今天必买；禁止荐买 / 预测必涨。",
    }


@app.get("/api/boards/latest")
def boards_latest() -> Dict[str, Any]:
    try:
        return load_latest(SAMPLE)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/boards/{date}")
def boards_date(date: str) -> Dict[str, Any]:
    try:
        return load_by_date(date, SAMPLE)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/run/publish")
def run_publish(as_of: Optional[str] = None) -> Dict[str, Any]:
    """Optional heavy endpoint — disabled by default.

    Set ALLOW_PUBLISH=1 to enable. Requires full kline DB + candidates.
    """
    if os.environ.get("ALLOW_PUBLISH") != "1":
        raise HTTPException(
            status_code=403,
            detail="Disabled. Set ALLOW_PUBLISH=1 and ensure ASHARE_KLINE_ROOT + candidates are configured. Prefer CLI: python -m v3.publish_close_v318",
        )
    # Document-only stub: real publish is heavy and should run via CLI script.
    return {
        "ok": False,
        "message": "Use backend/scripts/run_close_scan.sh or: cd backend/trend_breakout && python -m v3.publish_close_v318",
        "as_of": as_of,
    }


def main() -> None:
    import uvicorn

    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8765"))
    uvicorn.run("app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
