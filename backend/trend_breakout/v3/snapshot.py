"""
Thin StockSnapshot builders from OHLCV hist (Phase 3).

Fund / sector / continuity / risk fields left empty — later engines fill them.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from v3.engines.technical_engine import TechnicalEngine, TechnicalEngineResult, _truncate_hist
from v3.types import DataStatus, Lifecycle, StockSnapshot


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def build_stock_snapshot_from_hist(
    hist: pd.DataFrame,
    symbol: str,
    name: str = "",
    t_date: Optional[str] = None,
    *,
    engine: Optional[TechnicalEngine] = None,
    tech: Optional[TechnicalEngineResult] = None,
) -> StockSnapshot:
    """
    Fill StockSnapshot OHLCV / MAs from last bar of hist (as-of t_date).

    Skips fund fields. If ``tech`` is omitted, runs TechnicalEngine.evaluate.
    """
    truncated = _truncate_hist(hist, t_date)
    if tech is None:
        eng = engine or TechnicalEngine()
        tech = eng.evaluate(truncated if truncated is not None else hist, t_date=None)

    asof = t_date or tech.t_date or ""
    close = open_ = high = low = volume = amount = None
    ma20 = ma60 = None
    vol_ratio = dist = avg20 = None

    if truncated is not None and len(truncated) > 0:
        row = truncated.iloc[-1]
        close = _f(row.get("close"))
        open_ = _f(row.get("open"))
        high = _f(row.get("high"))
        low = _f(row.get("low"))
        volume = _f(row.get("volume"))
        amount = _f(row.get("amount"))
        if "MA20" in truncated.columns:
            ma20 = _f(row.get("MA20"))
        if "MA60" in truncated.columns:
            ma60 = _f(row.get("MA60"))
        if not asof and "date" in truncated.columns:
            asof = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")

    # Prefer engine metrics when present
    close = tech.close if tech.close is not None else close
    ma20 = tech.ma20 if tech.ma20 is not None else ma20
    ma60 = tech.ma60 if tech.ma60 is not None else ma60
    vol_ratio = tech.vol_ratio
    dist = tech.dist_ma20_pct
    avg20 = tech.avg20_amt_yi

    return StockSnapshot(
        code=symbol,
        name=name or "",
        t_date=asof or "",
        close=close,
        open=open_,
        high=high,
        low=low,
        volume=volume,
        amount=amount,
        lifecycle=tech.lifecycle if isinstance(tech.lifecycle, Lifecycle) else Lifecycle.NONE,
        phase_v2=tech.phase,
        overheated=bool(tech.overheated),
        kjs_tag=tech.kjs_tag,
        platform_high=tech.platform_high,
        platform_low=tech.platform_low,
        prev20high=tech.prev20high,
        ma20=ma20,
        ma60=ma60,
        vol_ratio=vol_ratio,
        dist_ma20_pct=dist,
        tech_score=float(tech.technical_score),
        data_status=tech.data_status if isinstance(tech.data_status, DataStatus) else DataStatus.VALID,
        avg20_amt_yi=avg20,
        extras={
            "atr": tech.atr,
            "breakout_high": tech.breakout_high,
            "score_breakdown": dict(tech.score_breakdown or {}),
            "tags": list(tech.tags or []),
        },
    )


__all__ = ["build_stock_snapshot_from_hist"]
