"""
V3.1.7 RealizedTrendEngine — as-of ≤ t only.

Weights: TrendReturn 30 / return20 20 / return40 15 / return60 10 /
         newHighProgress 10 / priceExpansion 15.

TrendReturn = close/TrendAnchorPrice - 1.
CurrentWaveReturn required to avoid long-horizon drift with weak current wave.
Mature floor: TrendReturn>=15% AND CurrentWaveReturn>=8% AND
  (20D>=8% OR 40D>=12% OR 60D>=15%).

No symbol/date branches. No future PeakGain/MajorLabel. 禁止荐买/预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from v3.v31.config import get_v317_realized
from v3.v31.types import RealizedTrendResult


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _ret_score(ret: Optional[float], soft: float, full: float) -> float:
    """Map return fraction to 0–100 with soft/full thresholds."""
    if ret is None:
        return 0.0
    r = float(ret)
    if r <= 0:
        return 0.0
    if r >= full:
        return 100.0
    if r >= soft:
        return 55.0 + 45.0 * (r - soft) / max(full - soft, 1e-9)
    return 55.0 * (r / max(soft, 1e-9))


class RealizedTrendEngine:
    """Stateless per-day realized trend scorer (as-of hist slice)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = dict(config or get_v317_realized())

    def evaluate(
        self,
        *,
        hist: pd.DataFrame,
        t_date: str,
        close: Optional[float],
        trend_anchor_price: Optional[float],
        wave_anchor_price: Optional[float],
        trend_anchor_date: Optional[str] = None,
        ma20: Optional[float] = None,
        high: Optional[float] = None,
    ) -> RealizedTrendResult:
        reasons: List[str] = []
        c = _f(close)
        if c is None or c <= 0:
            return RealizedTrendResult(reasons=["no_close"])

        asof = hist[hist["date"] <= pd.Timestamp(t_date)].copy()
        if len(asof) < 2:
            return RealizedTrendResult(reasons=["insufficient_bars"])

        tap = _f(trend_anchor_price) or _f(wave_anchor_price)
        wap = _f(wave_anchor_price) or tap
        trend_ret = (c / tap - 1.0) if tap and tap > 0 else 0.0
        wave_ret = (c / wap - 1.0) if wap and wap > 0 else 0.0

        r20 = self._n_day_return(asof, 20)
        r40 = self._n_day_return(asof, 40)
        r60 = self._n_day_return(asof, 60)

        realized_days = 0
        if trend_anchor_date:
            try:
                ad = pd.Timestamp(trend_anchor_date)
                realized_days = int(len(asof[asof["date"] > ad]))
            except Exception:
                realized_days = 0
        if realized_days <= 0 and tap and tap > 0:
            # fallback: bars since closest anchor touch
            realized_days = min(len(asof), 60)

        nh = self._new_high_progress(asof, c, high=_f(high))
        pe = self._price_expansion(c, ma20=_f(ma20), asof=asof)

        # component scores 0–100
        s_tr = _ret_score(trend_ret, soft=0.10, full=0.30)
        s_20 = _ret_score(r20, soft=0.05, full=0.15)
        s_40 = _ret_score(r40, soft=0.08, full=0.20)
        s_60 = _ret_score(r60, soft=0.10, full=0.25)
        s_nh = _clamp(nh)
        s_pe = _clamp(pe)

        w = self.cfg
        score = _clamp(
            (
                s_tr * float(w.get("weight_trend_return", 30))
                + s_20 * float(w.get("weight_return20", 20))
                + s_40 * float(w.get("weight_return40", 15))
                + s_60 * float(w.get("weight_return60", 10))
                + s_nh * float(w.get("weight_new_high", 10))
                + s_pe * float(w.get("weight_price_expansion", 15))
            )
            / 100.0
        )

        tr_min = float(w.get("trend_return_min", 0.15))
        wr_min = float(w.get("wave_return_min", 0.08))
        r20_min = float(w.get("return20_min", 0.08))
        r40_min = float(w.get("return40_min", 0.12))
        r60_min = float(w.get("return60_min", 0.15))

        horizon_ok = (
            (r20 is not None and r20 >= r20_min)
            or (r40 is not None and r40 >= r40_min)
            or (r60 is not None and r60 >= r60_min)
        )
        mature_floor = bool(trend_ret >= tr_min and wave_ret >= wr_min and horizon_ok)

        reasons.append(
            f"TR={trend_ret:.2%} WR={wave_ret:.2%} "
            f"r20={None if r20 is None else f'{r20:.2%}'} "
            f"r40={None if r40 is None else f'{r40:.2%}'} "
            f"r60={None if r60 is None else f'{r60:.2%}'} "
            f"floor={'OK' if mature_floor else 'NO'}"
        )

        return RealizedTrendResult(
            score=round(score, 2),
            trendReturn=round(trend_ret, 6),
            currentWaveReturn=round(wave_ret, 6),
            return20=None if r20 is None else round(r20, 6),
            return40=None if r40 is None else round(r40, 6),
            return60=None if r60 is None else round(r60, 6),
            priceExpansion=round(s_pe, 2),
            newHighProgress=round(s_nh, 2),
            realizedDays=int(realized_days),
            trendAnchorPrice=tap,
            waveAnchorPrice=wap,
            matureFloorOk=mature_floor,
            reasons=reasons,
        )

    def _n_day_return(self, asof: pd.DataFrame, n: int) -> Optional[float]:
        if len(asof) <= n:
            return None
        c0 = _f(asof.iloc[-1]["close"])
        cN = _f(asof.iloc[-(n + 1)]["close"])
        if c0 is None or cN is None or cN <= 0:
            return None
        return c0 / cN - 1.0

    def _new_high_progress(
        self, asof: pd.DataFrame, close: float, high: Optional[float] = None
    ) -> float:
        window = asof.tail(60)
        if len(window) < 5 or "high" not in window.columns:
            return 40.0
        hh = float(window["high"].astype(float).max())
        if hh <= 0:
            return 40.0
        px = float(high) if high is not None else close
        # at/above high → 100; within 3% → high; linear below
        if px >= hh * 0.999:
            return 100.0
        gap = (hh - px) / hh
        if gap <= 0.03:
            return 85.0 + 15.0 * (1.0 - gap / 0.03)
        if gap <= 0.10:
            return 50.0 + 35.0 * (1.0 - (gap - 0.03) / 0.07)
        return max(0.0, 50.0 * (1.0 - min(gap, 0.25) / 0.25))

    def _price_expansion(
        self, close: float, *, ma20: Optional[float], asof: pd.DataFrame
    ) -> float:
        pts = 40.0
        if ma20 is not None and ma20 > 0:
            ext = close / ma20 - 1.0
            if 0.02 <= ext <= 0.18:
                pts += 40.0  # healthy expansion
            elif 0 < ext < 0.02:
                pts += 20.0
            elif 0.18 < ext <= 0.28:
                pts += 25.0  # stretched but not chaos
            elif ext > 0.28:
                pts += 5.0  # over-extended quality penalty
            else:
                pts += 0.0
        # body quality vs prior close
        if len(asof) >= 2:
            prev = _f(asof.iloc[-2]["close"])
            if prev and prev > 0:
                day_ret = close / prev - 1.0
                if 0.01 <= day_ret <= 0.07:
                    pts += 20.0
                elif day_ret > 0:
                    pts += 8.0
        return _clamp(pts)


__all__ = ["RealizedTrendEngine", "RealizedTrendResult"]
