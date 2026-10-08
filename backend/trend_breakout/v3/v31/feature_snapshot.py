"""
Unified FeatureSnapshot builder for V3.1.1 regression.

ONE source of truth for tech/sector/fund/cont/weekly/monthly/market.
Do NOT mix kill_reason_critical_fullpatch.csv vs kill_reason_samples_fullsec.csv
as dual truth (those disagreed on sector, e.g. 2024-05-15 88 vs 62).

Engines are loaded once per FeatureSnapshotBuilder instance.
禁止荐买 / 预测必涨. No symbol-specific branches.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v2_structure import analyze_hist  # noqa: E402
from v2_staircase_scan import _code_full  # noqa: E402
from v2_fundamental import FundamentalEngine  # noqa: E402
from v2_sector import SectorEngine  # noqa: E402
from v2_risk import RiskEngine  # noqa: E402
from v2_continuity import ContinuityEngine  # noqa: E402

from v3.types import Lifecycle, RiskLevel  # noqa: E402
from v3.v31.market_regime_asof import regime_asof  # noqa: E402

STRUCT_TAIL = 320
MIN_BARS = 60


@dataclass
class FeatureSnapshot:
    code6: str
    name: str
    t_date: str
    # tech / structure
    tech: Optional[float] = None
    phase: Optional[str] = None
    lifecycle: Optional[str] = None
    overheated: bool = False
    close: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma20_slope_neg: bool = False
    trend_break_price: Optional[float] = None
    higher_low_broken: bool = False
    structure_rebreak: bool = False
    # scores
    cont: Optional[float] = None
    consumption: Optional[float] = None
    fund: Optional[float] = None
    fund_insufficient: bool = False
    sector: Optional[float] = None
    sector_insufficient: bool = False
    risk_level: str = "WATCH"
    broken: bool = False
    # HTF / market
    monthly: Optional[str] = None
    weekly: Optional[str] = None
    regime_v3: str = "NORMAL"
    risk_off: bool = False
    regime_source: str = "default"
    # meta
    ok: bool = False
    notes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FeatureSnapshotBuilder:
    """Reusable builder — load fund/sector engines once."""

    def __init__(self) -> None:
        self.fund_eng = FundamentalEngine()
        self.sec_eng = SectorEngine()
        self.sec_eng.bind_fundamental(self.fund_eng)
        self.risk_eng = RiskEngine()
        self.cont_eng = ContinuityEngine()
        self.cont_eng.bind(fund_engine=self.fund_eng, sector_engine=self.sec_eng)
        self._loaded = False
        self._fund_cache: Dict[str, Dict[str, Any]] = {}

    def load(self) -> None:
        if self._loaded:
            return
        try:
            self.fund_eng.load()
            self.sec_eng.load()
        except Exception as e:
            # still usable with insufficient flags
            pass
        self._loaded = True

    def build(
        self,
        *,
        code6: str,
        name: str,
        hist: pd.DataFrame,
        t_date: str,
    ) -> FeatureSnapshot:
        self.load()
        code6 = str(code6).zfill(6)
        td = str(t_date)[:10]
        snap = FeatureSnapshot(code6=code6, name=name, t_date=td)

        if hist is None or not isinstance(hist, pd.DataFrame) or hist.empty:
            snap.notes.append("empty hist")
            return snap

        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values("date")
        h = h[h["date"] <= pd.Timestamp(td)].reset_index(drop=True)
        if len(h) < MIN_BARS:
            snap.notes.append("hist < MIN_BARS")
            return snap
        if str(h["date"].iloc[-1].date()) != td:
            snap.notes.append("no bar on t_date")
            return snap

        # Market regime as-of
        reg = regime_asof(td)
        snap.regime_v3 = str(reg.get("regime_v3") or "NORMAL")
        snap.risk_off = bool(reg.get("risk_off"))
        snap.regime_source = str(reg.get("source") or "default")

        hist_s = h.tail(STRUCT_TAIL).reset_index(drop=True)
        try:
            d = analyze_hist(hist_s).to_dict()
        except Exception as e:
            snap.notes.append(f"analyze_hist err:{type(e).__name__}")
            return snap

        tech = float(d.get("score") or 0)
        phase = str(d.get("phase") or "NONE")
        life = Lifecycle.map_from_v2_phase(phase)
        oh = bool(d.get("overheated") or life == Lifecycle.OVERHEATED)
        metrics = d.get("metrics") or {}
        snap.tech = tech
        snap.phase = phase
        snap.lifecycle = life.value
        snap.overheated = oh
        snap.metrics = dict(metrics) if isinstance(metrics, dict) else {}
        snap.close = _f(metrics.get("close"))
        snap.ma20 = _f(metrics.get("ma20") or metrics.get("MA20"))
        snap.ma60 = _f(metrics.get("ma60") or metrics.get("MA60"))
        # structure refs
        brk = metrics.get("breakout_high") or metrics.get("prev20high") or metrics.get("platform_high")
        snap.trend_break_price = _f(brk)
        # MA20 slope (confirmed negative if MA20(T) < MA20(T-5))
        close_s = h["close"].astype(float)
        if len(close_s) >= 25:
            ma20 = close_s.rolling(20).mean()
            m_now = float(ma20.iloc[-1])
            m_prev = float(ma20.iloc[-6]) if len(ma20) >= 6 else m_now
            snap.ma20 = snap.ma20 if snap.ma20 is not None else m_now
            snap.ma20_slope_neg = bool(np.isfinite(m_now) and np.isfinite(m_prev) and m_now < m_prev)
        # Higher-low break heuristic: last swing low taken out
        snap.higher_low_broken = _higher_low_broken(h)
        snap.structure_rebreak = bool(
            phase in ("TREND_FAILURE", "NONE") and tech < 55
        ) or bool((d.get("flags") or {}).get("rebreak"))

        # Fund (cache by code — slow-changing)
        if code6 not in self._fund_cache:
            try:
                fr = self.fund_eng.evaluate(code6)
                self._fund_cache[code6] = fr.to_dict()
            except Exception:
                self._fund_cache[code6] = {
                    "ok": False,
                    "data_insufficient": True,
                    "score": None,
                }
        fund_d = self._fund_cache[code6]
        snap.fund_insufficient = bool(fund_d.get("data_insufficient"))
        snap.fund = None if snap.fund_insufficient else _f(fund_d.get("score"))

        # Sector as-of T
        full = _code_full(code6)
        try:
            sr = self.sec_eng.evaluate(code6, hist=h, t_date=td)
            sec_d = sr.to_dict()
        except Exception:
            sec_d = {"ok": False, "data_insufficient": True, "score": None}
        snap.sector_insufficient = bool(sec_d.get("data_insufficient"))
        snap.sector = None if snap.sector_insufficient else _f(sec_d.get("score"))

        # Risk
        try:
            rr = self.risk_eng.evaluate(
                h,
                platform_low=metrics.get("platform_low"),
                structure_risks=d.get("risks") or [],
            )
            risk_d = rr.to_dict()
        except Exception:
            risk_d = {"risk_score": "MID", "broken": False}
        broken = bool(risk_d.get("broken") or False)
        snap.broken = broken
        snap.risk_level = RiskLevel.map_from_v2(
            str(risk_d.get("risk_score") or "MID"), broken=broken
        ).value

        # Continuity
        try:
            cr = self.cont_eng.evaluate(
                code6,
                h,
                t_date=td,
                fund=fund_d,
                sector=sec_d,
                phase=phase,
                persist=True,
            )
            cont_d = cr.to_dict()
        except Exception:
            cont_d = {
                "continuity_score": None,
                "consumption_score": None,
                "data_insufficient": True,
            }
        snap.cont = _f(cont_d.get("continuity_score"))
        snap.consumption = _f(cont_d.get("consumption_score"))

        # Monthly / weekly HTF from close path (no future)
        snap.monthly = _htf_label(h, step=21)
        snap.weekly = _htf_label(h, step=5)

        snap.ok = snap.tech is not None and snap.cont is not None
        return snap


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _htf_label(h: pd.DataFrame, step: int = 21) -> str:
    if len(h) < max(60, step * 3):
        return "NEUTRAL"
    close = h["close"].astype(float)
    c0 = float(close.iloc[-1 - 3 * step])
    last = float(close.iloc[-1])
    ret3 = (last / c0 - 1.0) * 100.0 if c0 > 0 else 0.0
    ma = float(close.rolling(min(60, len(close))).mean().iloc[-1])
    if ret3 >= 15 and last >= ma:
        return "STRONG_UP"
    if ret3 >= 5 and last >= ma * 0.98:
        return "UP"
    if ret3 <= -10 or last < ma * 0.92:
        return "DOWN"
    return "NEUTRAL"


def _higher_low_broken(h: pd.DataFrame) -> bool:
    """True if recent close undercuts prior swing higher-low (simple 20-bar heuristic)."""
    if len(h) < 40:
        return False
    low = h["low"].astype(float).values
    close = h["close"].astype(float).values
    # last two troughs in 40 bars
    window = low[-40:]
    # find local minima
    mins = []
    for i in range(2, len(window) - 2):
        if window[i] <= window[i - 1] and window[i] <= window[i - 2] and window[i] <= window[i + 1] and window[i] <= window[i + 2]:
            mins.append((i, float(window[i])))
    if len(mins) < 2:
        return False
    prev_hl = mins[-2][1]
    last_c = float(close[-1])
    return last_c < prev_hl * 0.995


__all__ = ["FeatureSnapshot", "FeatureSnapshotBuilder"]
