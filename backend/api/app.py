"""Minimal FastAPI for Trend Radar V3.1.8 boards."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from boards_loader import (
    find_sample_root,
    list_board_dates,
    list_dividend_dates,
    load_by_date,
    load_dividend_by_date,
    load_dividend_latest,
    load_latest,
)
import quote_service
import research_reader

app = FastAPI(
    title="趋势分析雷达 V3.1.8 + 红利模块 V3.1.9 API",
    description="观察池 API；Growth 四榜 + 红利三区独立；禁止荐买 / 预测必涨。A+ = 图形质量，不等于今天必买。",
    version="3.1.9",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE = find_sample_root()


@app.middleware("http")
async def no_store_boards(request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    if request.url.path.startswith("/api/boards") or request.url.path.startswith("/api/research") or request.url.path == "/api/meta" or request.url.path == "/research":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": "V3.1.8",
        "modules": {"growth": "V3.1.8", "dividend": "V3.1.9"},
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
        "modules": {"growth": "V3.1.8", "dividend": "V3.1.9"},
        "dates": list_board_dates(SAMPLE),
        "dividend_dates": list_dividend_dates(SAMPLE),
        "market_meta": market,
        "boards_rules": {
            "mature_entry": "A+/A + PRIME/CONFIRMED",
            "trend_all": "A+/A/B/C",
            "watch_pool": "B/C + EARLY/BUILDING",
            "extended": "A+/A + EXTENDED",
        },
        "dividend_boards_rules": {
            "quality": "优质红利（趋势观察评级 A+/A，且可持续分红）",
            "opportunity": "当前机会（趋势观察评级至少 A，且红利等级 A+/A）",
            "risk_watch": "风险观察（趋势观察评级 A+/A 中的陷阱/高风险/趋势破坏）",
        },
        "disclaimer": "观察池；A+≠今天必买；禁止荐买 / 预测必涨。红利三区独立于 Growth 四榜。",
    }


@app.get("/api/boards/latest")
def boards_latest() -> Dict[str, Any]:
    try:
        return load_latest(SAMPLE)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/boards/dividend/latest")
def dividend_boards_latest() -> Dict[str, Any]:
    try:
        return load_dividend_latest(SAMPLE)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/boards/dividend/{date}")
def dividend_boards_date(date: str) -> Dict[str, Any]:
    try:
        return load_dividend_by_date(date, SAMPLE)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/boards/{date}")
def boards_date(date: str) -> Dict[str, Any]:
    try:
        return load_by_date(date, SAMPLE)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _quote_call(fn, code: str, **kwargs):
    try:
        return fn(code, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"行情源暂不可用: {type(e).__name__}") from e


@app.get("/api/quotes/{code}/trends")
def quote_trends(code: str, date: Optional[str] = None) -> Dict[str, Any]:
    return _quote_call(quote_service.get_trends, code, trade_date=date)


@app.get("/api/quotes/{code}/klines")
def quote_klines(code: str, period: str = "day", as_of: Optional[str] = None) -> Dict[str, Any]:
    if period not in ("day", "month"):
        raise HTTPException(status_code=400, detail="period 仅支持 day 或 month")
    return _quote_call(quote_service.get_klines, code, period=period, as_of=as_of)


@app.get("/api/quotes/{code}/flow")
def quote_flow(code: str, date: Optional[str] = None) -> Dict[str, Any]:
    return _quote_call(quote_service.get_flow, code, trade_date=date)


@app.get("/api/quotes/{code}/rise-reason")
def quote_rise_reason(code: str, name: str = "", date: Optional[str] = None) -> Dict[str, Any]:
    return _quote_call(quote_service.get_rise_reason, code, name=name, trade_date=date)


@app.get("/research", response_class=HTMLResponse)
def research_page() -> str:
    return Path(__file__).with_name("research_page.html").read_text(encoding="utf-8")


def _research_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except research_reader.ResearchReadError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@app.get("/api/research/status")
def research_status() -> Dict[str, Any]:
    return _research_call(research_reader.status_payload)


@app.get("/api/research/runs")
def research_runs() -> Dict[str, Any]:
    return _research_call(research_reader.list_runs)


@app.get("/api/research/runs/{run_id}")
def research_run(run_id: str) -> Dict[str, Any]:
    return _research_call(research_reader.run_detail, run_id)


@app.get("/api/research/runs/{run_id}/grades")
def research_grades(run_id: str, date: Optional[str] = None, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    return _research_call(research_reader.grades, run_id, date, page, page_size)


@app.get("/api/research/runs/{run_id}/orders")
def research_orders(run_id: str, date: Optional[str] = None, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    return _research_call(research_reader.table_section, run_id, "orders", date, page, page_size)


@app.get("/api/research/runs/{run_id}/trades")
def research_trades(run_id: str, date: Optional[str] = None, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    return _research_call(research_reader.table_section, run_id, "trades", date, page, page_size)


@app.get("/api/research/runs/{run_id}/positions")
def research_positions(run_id: str, date: Optional[str] = None, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    return _research_call(research_reader.table_section, run_id, "daily_positions", date, page, page_size)


@app.get("/api/research/runs/{run_id}/equity")
def research_equity(run_id: str, page: int = 1, page_size: int = 200) -> Dict[str, Any]:
    return _research_call(research_reader.table_section, run_id, "equity", None, page, page_size)


@app.get("/api/research/runs/{run_id}/metrics")
def research_metrics(run_id: str) -> Dict[str, Any]:
    return _research_call(research_reader.metrics, run_id)


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
