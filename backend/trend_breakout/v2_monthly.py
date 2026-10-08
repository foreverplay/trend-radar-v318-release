#!/usr/bin/env python3
"""
V2.3 StockMonthlyTrendEngine — calendar-month OHLCV aggregation + monthly trend score.

No buy advice. Monthly DOWN only tags / lowers RecommendationScore elsewhere (not hard-filter).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
try:
    from paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
except ImportError:  # packaged / flat layout fallback
    try:
        from trend_breakout.paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
    except ImportError:
        from pathlib import Path as __P
        import os as __os
        _PKG_ROOT = __P(__os.environ.get("TREND_BREAKOUT_ROOT", __P(__file__).resolve().parent))
        _k = __P(__os.environ.get("ASHARE_KLINE_ROOT", str(_PKG_ROOT.parent.parent / "data" / "sample" / "kline")))
        _PKG_LOCAL_DAILY = _k / "raw" / "daily"
        _PKG_CACHE = _PKG_ROOT / "cache"
        _PKG_CONT_CACHE = _PKG_CACHE / "continuity_history"
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

LOCAL_DAILY = _PKG_LOCAL_DAILY
def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _slope_last(series: pd.Series, n: int = 3) -> Optional[float]:
    """Simple slope of last n points (last - first) / (n-1)."""
    if series is None or len(series) < n:
        return None
    y = series.tail(n).astype(float).values
    if not np.all(np.isfinite(y)):
        return None
    return float((y[-1] - y[0]) / max(n - 1, 1))


def aggregate_daily_to_monthly(df: pd.DataFrame, t_date: Optional[str] = None) -> pd.DataFrame:
    """
    Aggregate daily OHLCV to calendar-month bars.
    Close = last trading day close; OHLC: open first, high max, low min;
    volume/amount = sum.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    h = df.copy()
    if "date" not in h.columns:
        return pd.DataFrame()
    h["date"] = pd.to_datetime(h["date"])
    h = h.sort_values("date")
    if t_date:
        h = h[h["date"] <= pd.Timestamp(t_date)]
    if len(h) == 0:
        return pd.DataFrame()
    h["ym"] = h["date"].dt.to_period("M")
    agg: Dict[str, Any] = {
        "date": ("date", "last"),
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
    }
    if "volume" in h.columns:
        agg["volume"] = ("volume", "sum")
    if "amount" in h.columns:
        agg["amount"] = ("amount", "sum")
    m = h.groupby("ym", as_index=False).agg(**agg)
    m = m.sort_values("date").reset_index(drop=True)
    return m


@dataclass
class StockMonthlyTrendResult:
    ok: bool = False
    data_insufficient: bool = True
    trend: str = "MONTHLY_NEUTRAL"  # STRONG_UP / UP / NEUTRAL / DOWN
    trend_cn: str = "中性"
    score: Optional[float] = None  # StockMonthlyTrendScore 0–100
    metrics: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    n_months: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_TREND_CN = {
    "MONTHLY_STRONG_UP": "强势向上",
    "MONTHLY_UP": "向上",
    "MONTHLY_NEUTRAL": "中性",
    "MONTHLY_DOWN": "向下",
}


class StockMonthlyTrendEngine:
    """Evaluate stock monthly trend from daily hist or local parquet."""

    MIN_MONTHS = 8  # need enough bars for MA5 + slopes; MA20 soft

    def evaluate(
        self,
        code: str,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
    ) -> StockMonthlyTrendResult:
        code6 = _norm_code(code)
        res = StockMonthlyTrendResult()
        df = hist
        if df is None or len(df) < 40:
            p = LOCAL_DAILY / f"{code6}.parquet"
            if p.exists():
                try:
                    cols = ["date", "open", "high", "low", "close", "volume", "amount"]
                    raw = pd.read_parquet(p)
                    use = [c for c in cols if c in raw.columns]
                    df = raw[use]
                except Exception as e:
                    res.notes.append(f"parquet_err:{type(e).__name__}")
                    res.tags = ["DATA_INSUFFICIENT"]
                    return res
        if df is None or len(df) < 40:
            res.notes.append("日线不足，无法聚合月线")
            res.tags = ["DATA_INSUFFICIENT"]
            return res

        monthly = aggregate_daily_to_monthly(df, t_date=t_date)
        n = len(monthly)
        res.n_months = n
        if n < self.MIN_MONTHS:
            res.notes.append(f"月线仅{n}根，不足{self.MIN_MONTHS}")
            res.tags = ["DATA_INSUFFICIENT"]
            res.data_insufficient = True
            # still try soft classify if >=5
            if n < 5:
                return res

        close = monthly["close"].astype(float)
        ma3 = close.rolling(3, min_periods=3).mean()
        ma5 = close.rolling(5, min_periods=5).mean()
        ma10 = close.rolling(10, min_periods=8).mean()
        ma20 = close.rolling(20, min_periods=12).mean()

        c = float(close.iloc[-1])
        v_ma3 = float(ma3.iloc[-1]) if pd.notna(ma3.iloc[-1]) else None
        v_ma5 = float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else None
        v_ma10 = float(ma10.iloc[-1]) if n >= 8 and pd.notna(ma10.iloc[-1]) else None
        v_ma20 = float(ma20.iloc[-1]) if n >= 12 and pd.notna(ma20.iloc[-1]) else None

        s_ma5 = _slope_last(ma5.dropna(), 3)
        s_ma10 = _slope_last(ma10.dropna(), 3) if n >= 10 else None
        s_ma3 = _slope_last(ma3.dropna(), 3)

        # --- trend classify ---
        trend = "MONTHLY_NEUTRAL"
        if v_ma5 is not None and s_ma5 is not None:
            strong = (
                v_ma3 is not None
                and v_ma10 is not None
                and v_ma3 > v_ma5 > v_ma10
                and s_ma5 > 0
                and (s_ma10 is not None and s_ma10 > 0)
                and c > v_ma5
            )
            up = v_ma3 is not None and v_ma3 > v_ma5 and c > v_ma5 and s_ma5 > 0
            down = c < v_ma5 and s_ma5 < 0
            if strong:
                trend = "MONTHLY_STRONG_UP"
            elif up:
                trend = "MONTHLY_UP"
            elif down:
                trend = "MONTHLY_DOWN"
            else:
                trend = "MONTHLY_NEUTRAL"

        res.trend = trend
        res.trend_cn = _TREND_CN.get(trend, trend)

        # --- StockMonthlyTrendScore 0–100 ---
        # 均线结构30 + 斜率20 + 价格位置20 + 20期趋势15 + 成交量结构15
        parts: Dict[str, float] = {}
        notes: List[str] = list(res.notes)
        tags: List[str] = []

        # 1) MA structure 30
        struct = 10.0
        if v_ma3 is not None and v_ma5 is not None:
            if v_ma10 is not None and v_ma3 > v_ma5 > v_ma10:
                struct = 30.0
            elif v_ma3 > v_ma5:
                struct = 22.0
            elif v_ma3 > v_ma5 * 0.98:
                struct = 15.0
            elif c < (v_ma5 or c):
                struct = 5.0
        parts["ma_structure"] = struct

        # 2) slopes 20
        slope_s = 8.0
        if s_ma5 is not None:
            # normalize roughly by price
            rel = s_ma5 / max(abs(c), 1e-6)
            if s_ma5 > 0 and (s_ma10 is None or s_ma10 > 0):
                slope_s = 20.0 if rel > 0.005 else 16.0
            elif s_ma5 > 0:
                slope_s = 14.0
            elif s_ma5 < 0 and (s_ma10 is not None and s_ma10 < 0):
                slope_s = 2.0
            elif s_ma5 < 0:
                slope_s = 5.0
            else:
                slope_s = 10.0
        parts["slope"] = slope_s

        # 3) price position 20
        pos = 10.0
        if v_ma5 is not None:
            if c > v_ma5:
                if v_ma10 is not None and c > v_ma10:
                    pos = 20.0 if (v_ma20 is None or c > v_ma20) else 16.0
                else:
                    pos = 14.0
            else:
                pos = 4.0 if c < v_ma5 * 0.95 else 8.0
        parts["price_pos"] = pos

        # 4) 20-period trend (monthly ret over ~20 months or available) 15
        t20 = 7.0
        look = min(20, max(n - 1, 1))
        if n >= 6:
            base = float(close.iloc[-(look + 1)]) if n > look else float(close.iloc[0])
            if base > 0:
                ret = (c / base - 1.0) * 100.0
                if ret >= 40:
                    t20 = 15.0
                elif ret >= 20:
                    t20 = 12.0
                elif ret >= 5:
                    t20 = 9.0
                elif ret >= 0:
                    t20 = 7.0
                elif ret >= -15:
                    t20 = 4.0
                else:
                    t20 = 1.0
                parts["ret_lookback_pct"] = round(ret, 2)
        parts["trend20"] = t20

        # 5) volume structure 15
        vol_s = 7.0
        if "volume" in monthly.columns and n >= 6:
            vol = monthly["volume"].astype(float)
            recent = float(vol.tail(3).mean())
            prior = float(vol.tail(6).head(3).mean()) if n >= 6 else recent
            if prior > 0:
                vr = recent / prior
                # rising price + rising/stable vol better
                if trend in ("MONTHLY_STRONG_UP", "MONTHLY_UP") and vr >= 1.0:
                    vol_s = 15.0 if vr >= 1.2 else 12.0
                elif trend in ("MONTHLY_STRONG_UP", "MONTHLY_UP") and vr < 0.7:
                    vol_s = 6.0
                    tags.append("MONTHLY_VOL_DIVERGE")
                elif trend == "MONTHLY_DOWN" and vr >= 1.2:
                    vol_s = 3.0  # distribution
                else:
                    vol_s = 8.0
                parts["vol_ratio_3_6"] = round(vr, 3)
        else:
            notes.append("无月线成交量，量能维中性")
        parts["volume"] = vol_s

        score = float(
            round(
                parts["ma_structure"]
                + parts["slope"]
                + parts["price_pos"]
                + parts["trend20"]
                + parts["volume"],
                1,
            )
        )
        score = float(min(100.0, max(0.0, score)))

        if trend == "MONTHLY_STRONG_UP":
            tags.append("MONTHLY_STRONG_UP")
        elif trend == "MONTHLY_UP":
            tags.append("MONTHLY_UP")
        elif trend == "MONTHLY_DOWN":
            tags.append("MONTHLY_DOWN")
        else:
            tags.append("MONTHLY_NEUTRAL")

        res.ok = n >= self.MIN_MONTHS
        res.data_insufficient = n < self.MIN_MONTHS
        if res.data_insufficient:
            tags.append("DATA_INSUFFICIENT")
        res.score = score
        res.metrics = {
            "close": round(c, 4),
            "ma3": None if v_ma3 is None else round(v_ma3, 4),
            "ma5": None if v_ma5 is None else round(v_ma5, 4),
            "ma10": None if v_ma10 is None else round(v_ma10, 4),
            "ma20": None if v_ma20 is None else round(v_ma20, 4),
            "ma5_slope": None if s_ma5 is None else round(s_ma5, 6),
            "ma10_slope": None if s_ma10 is None else round(s_ma10, 6),
            "ma3_slope": None if s_ma3 is None else round(s_ma3, 6),
            "parts": {k: round(float(v), 2) if isinstance(v, (int, float)) else v for k, v in parts.items()},
            "n_months": n,
        }
        res.tags = tags
        res.notes = notes
        return res


__all__ = [
    "StockMonthlyTrendEngine",
    "StockMonthlyTrendResult",
    "aggregate_daily_to_monthly",
]
