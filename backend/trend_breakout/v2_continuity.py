#!/usr/bin/env python3
"""
V2.2 ContinuityEngine — ContinuityScore + TrendConsumption (state scores).

ContinuityScore 0–100 = Structure25 + Drawdown15 + Volume15 + Fund20 + Sector15 + Space10.
Missing fund/sector continuity inputs → partial score (renormalize available weights)
+ DATA_INSUFFICIENT notes — never fake a 0 wipe of the whole score.

ContinuityScore is a **state score**, never a future-return prediction.
No buy advice. No TotalScore sum with Tech/Fund/Sector/Risk.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

CACHE_DIR = _PKG_CONT_CACHE
OUT = _PKG_ROOT
TYPE_CN = {
    "TYPE_A": "健康延续",
    "TYPE_B": "加速延续",
    "TYPE_C": "高位加速",
    "TYPE_D": "趋势衰减",
}

STAR_LABELS = (
    (90, "极强"),
    (80, "强"),
    (70, "良好"),
    (60, "一般"),
    (0, "较弱"),
)

# Component max weights
W_STRUCTURE = 25.0
W_DRAWDOWN = 15.0
W_VOLUME = 15.0
W_FUND = 20.0
W_SECTOR = 15.0
W_SPACE = 10.0


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return None
        if pd.isna(x):
            return None
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, v)))


def _star_of(score: float) -> str:
    for thr, lab in STAR_LABELS:
        if score >= thr:
            return lab
    return "较弱"


def _prep_ohlcv(hist: pd.DataFrame, t_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    if hist is None or len(hist) < 30:
        return None
    h = hist.copy()
    need = {"close", "high", "low"}
    if not need.issubset(set(h.columns)):
        return None
    if "date" in h.columns:
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values("date")
        if t_date:
            h = h[h["date"] <= pd.Timestamp(t_date)]
    if len(h) < 30:
        return None
    for col in ("open", "close", "high", "low", "volume", "amount"):
        if col in h.columns:
            h[col] = pd.to_numeric(h[col], errors="coerce")
    return h.reset_index(drop=True)


def _ma(s: pd.Series, n: int) -> Optional[float]:
    if s is None or len(s) < n:
        return None
    v = float(s.tail(n).mean())
    return v if np.isfinite(v) else None


def _slope_pct(s: pd.Series, n: int = 10) -> Optional[float]:
    if s is None or len(s) < n + 1:
        return None
    a = float(s.iloc[-1])
    b = float(s.iloc[-(n + 1)])
    if b == 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return (a / b - 1.0) * 100.0


def _ret_pct(close: pd.Series, n: int) -> Optional[float]:
    if close is None or len(close) < n + 1:
        return None
    a = float(close.iloc[-1])
    b = float(close.iloc[-(n + 1)])
    if b == 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return (a / b - 1.0) * 100.0


def _atr(h: pd.DataFrame, n: int = 14) -> Optional[float]:
    if h is None or len(h) < n + 1:
        return None
    high = h["high"].astype(float)
    low = h["low"].astype(float)
    close = h["close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    v = float(tr.tail(n).mean())
    return v if np.isfinite(v) else None


def _hh_hl_20(high: pd.Series, low: pd.Series) -> Tuple[bool, bool]:
    """HigherHigh / HigherLow over ~20d: recent 10d extremum vs prior 10d."""
    if high is None or low is None or len(high) < 20:
        return False, False
    h1 = float(high.iloc[-10:].max())
    h0 = float(high.iloc[-20:-10].max())
    l1 = float(low.iloc[-10:].min())
    l0 = float(low.iloc[-20:-10].min())
    return (h1 >= h0 * 0.999), (l1 >= l0 * 0.999)


def _max_dd_20(close: pd.Series) -> Optional[float]:
    """Max drawdown % within last 20 closes (peak-to-trough). Negative number."""
    if close is None or len(close) < 5:
        return None
    c = close.tail(20).astype(float).values
    peak = c[0]
    max_dd = 0.0
    for x in c:
        if x > peak:
            peak = x
        if peak > 0:
            dd = (x / peak - 1.0) * 100.0
            if dd < max_dd:
                max_dd = dd
    return float(max_dd)


def _pullback_recovery(close: pd.Series, ma20: Optional[float]) -> Optional[float]:
    """
    Score-ish 0–1: depth of recent pullback vs recovery to near highs / above MA20.
    """
    if close is None or len(close) < 15:
        return None
    c = close.tail(20).astype(float)
    peak = float(c.max())
    trough = float(c.min())
    last = float(c.iloc[-1])
    if peak <= 0:
        return None
    depth = (trough / peak - 1.0)  # negative
    # healthy pullback ~ -3% to -12%, then recovered near peak
    recovered = last / peak
    score = 0.5
    if -0.12 <= depth <= -0.02:
        score += 0.25
    elif depth < -0.18:
        score -= 0.25
    if recovered >= 0.97:
        score += 0.25
    elif recovered >= 0.92:
        score += 0.1
    elif recovered < 0.85:
        score -= 0.2
    if ma20 is not None and last >= ma20:
        score += 0.1
    elif ma20 is not None and last < ma20 * 0.98:
        score -= 0.2
    return float(max(0.0, min(1.0, score)))


@dataclass
class ContinuityResult:
    ok: bool = False
    continuity_score: Optional[float] = None
    consumption_score: Optional[float] = None
    continuity_type: str = "TYPE_D"
    continuity_type_cn: str = TYPE_CN["TYPE_D"]
    star: str = "较弱"
    components: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    favorable: List[str] = field(default_factory=list)
    limiting: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    data_insufficient: bool = False
    insufficient_parts: List[str] = field(default_factory=list)
    delta1: Optional[float] = None
    delta3: Optional[float] = None
    delta5: Optional[float] = None
    delta_label: Optional[str] = None  # IMPROVING / DETERIORATING / STABLE / None
    sector_continuity: Optional[float] = None  # raw 0–15 component
    overextended: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContinuityEngine:
    """Compute ContinuityScore + TrendConsumption; persist daily history."""

    def __init__(self, cache_dir: Path = CACHE_DIR, fund_engine=None, sector_engine=None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fund = fund_engine
        self._sector = sector_engine

    def bind(self, fund_engine=None, sector_engine=None) -> None:
        if fund_engine is not None:
            self._fund = fund_engine
        if sector_engine is not None:
            self._sector = sector_engine

    # ---- component scorers (return points within max weight) ----

    def _score_structure(self, h: pd.DataFrame) -> Tuple[float, Dict[str, Any], List[str], List[str]]:
        close = h["close"].astype(float)
        high = h["high"].astype(float)
        low = h["low"].astype(float)
        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean() if len(close) >= 60 else None

        pts = 0.0
        meta: Dict[str, Any] = {}
        fav: List[str] = []
        lim: List[str] = []

        v5, v10, v20 = _ma(close, 5), _ma(close, 10), _ma(close, 20)
        v60 = _ma(close, 60)
        stack = None not in (v5, v10, v20) and v5 > v10 > v20
        meta["ma_stack_5_10_20"] = bool(stack)
        if stack:
            pts += 10.0
            fav.append("均线多头排列(MA5>MA10>MA20)")
            if v60 is not None and v20 is not None and v20 >= v60:
                pts += 3.0
                fav.append("MA20≥MA60")
                meta["ma20_ge_ma60"] = True
            else:
                meta["ma20_ge_ma60"] = bool(v60 is not None and v20 is not None and v20 >= v60)
        else:
            pts += 2.0
            lim.append("均线未完全多头排列")
            meta["ma20_ge_ma60"] = bool(v60 is not None and v20 is not None and v20 >= v60)

        s5 = _slope_pct(ma5.dropna(), 5) if len(ma5.dropna()) >= 6 else None
        s20 = _slope_pct(ma20.dropna(), 10) if len(ma20.dropna()) >= 11 else None
        meta["ma5_slope"] = s5
        meta["ma20_slope"] = s20
        if s20 is not None:
            if s20 >= 3:
                pts += 6.0
                fav.append(f"MA20斜率偏强({s20:.1f}%)")
            elif s20 >= 1:
                pts += 4.0
                fav.append(f"MA20斜率向上({s20:.1f}%)")
            elif s20 >= 0:
                pts += 2.0
            else:
                lim.append(f"MA20斜率走弱({s20:.1f}%)")
        if s5 is not None:
            if s5 >= 2:
                pts += 2.0
            elif s5 < -2:
                pts -= 1.0

        hh, hl = _hh_hl_20(high, low)
        meta["higher_high_20"] = hh
        meta["higher_low_20"] = hl
        if hh and hl:
            pts += 4.0
            fav.append("20日HigherHigh+HigherLow")
        elif hh or hl:
            pts += 2.0
            fav.append("20日HH或HL其一成立")
        else:
            lim.append("20日未形成HH/HL")

        pts = float(max(0.0, min(W_STRUCTURE, pts)))
        meta["points"] = round(pts, 2)
        meta["max"] = W_STRUCTURE
        return pts, meta, fav, lim

    def _score_drawdown(self, h: pd.DataFrame) -> Tuple[float, Dict[str, Any], List[str], List[str], bool]:
        close = h["close"].astype(float)
        ma20 = _ma(close, 20)
        last = float(close.iloc[-1])
        pts = 0.0
        meta: Dict[str, Any] = {}
        fav: List[str] = []
        lim: List[str] = []
        overextended = False

        mdd = _max_dd_20(close)
        meta["max_dd_20"] = mdd
        # bands: shallow healthy (-2~-10) best; deep bad; near-zero with huge run = stretched
        if mdd is not None:
            if -10 <= mdd <= -2:
                pts += 6.0
                fav.append(f"20日回撤健康({mdd:.1f}%)")
            elif -15 <= mdd < -10:
                pts += 4.0
            elif -2 < mdd <= 0:
                pts += 3.0  # no pullback — ok but less "digest"
            elif mdd < -20:
                pts += 0.0
                lim.append(f"20日回撤偏深({mdd:.1f}%)")
            else:
                pts += 2.0

        pr = _pullback_recovery(close, ma20)
        meta["pullback_recovery"] = None if pr is None else round(pr, 3)
        if pr is not None:
            pts += pr * 5.0
            if pr >= 0.7:
                fav.append("回调后修复良好")
            elif pr < 0.4:
                lim.append("回调修复偏弱")

        # Close vs MA — overextended if far above MA20
        dist = None
        if ma20 and ma20 > 0:
            dist = (last / ma20 - 1.0) * 100.0
            meta["dist_ma20_pct"] = round(dist, 3)
            if dist > 18:
                overextended = True
                pts += 0.5
                lim.append(f"OVEREXTENDED距MA20={dist:.1f}%")
            elif dist > 12:
                overextended = True
                pts += 2.0
                lim.append(f"偏离MA20偏大({dist:.1f}%)")
            elif 0 <= dist <= 8:
                pts += 4.0
                fav.append(f"收盘贴近MA20上方({dist:.1f}%)")
            elif -3 <= dist < 0:
                pts += 2.5
            else:
                pts += 1.0
                lim.append(f"收盘弱于MA20({dist:.1f}%)")
        meta["overextended"] = overextended

        pts = float(max(0.0, min(W_DRAWDOWN, pts)))
        meta["points"] = round(pts, 2)
        meta["max"] = W_DRAWDOWN
        return pts, meta, fav, lim, overextended

    def _score_volume(self, h: pd.DataFrame, structure_metrics: Optional[Dict] = None) -> Tuple[float, Dict[str, Any], List[str], List[str]]:
        pts = 0.0
        meta: Dict[str, Any] = {}
        fav: List[str] = []
        lim: List[str] = []
        if "volume" not in h.columns or h["volume"].isna().all():
            meta["points"] = 7.5  # neutral mid when no volume
            meta["max"] = W_VOLUME
            meta["missing_volume"] = True
            lim.append("无成交量列，量价中性给分")
            return 7.5, meta, fav, lim

        close = h["close"].astype(float)
        vol = h["volume"].astype(float).fillna(0.0)
        # up-vol vs down-vol (20d)
        up_v = down_v = 0.0
        for i in range(max(1, len(h) - 20), len(h)):
            if float(close.iloc[i]) >= float(close.iloc[i - 1]):
                up_v += float(vol.iloc[i])
            else:
                down_v += float(vol.iloc[i])
        ratio = up_v / down_v if down_v > 0 else (2.0 if up_v > 0 else 1.0)
        meta["up_down_vol_ratio_20"] = round(ratio, 3)
        if ratio >= 1.4:
            pts += 6.0
            fav.append(f"上涨量能占优(UD={ratio:.2f})")
        elif ratio >= 1.1:
            pts += 4.0
            fav.append(f"上涨量能略优(UD={ratio:.2f})")
        elif ratio >= 0.9:
            pts += 2.5
        else:
            lim.append(f"下跌量能偏重(UD={ratio:.2f})")

        # post-break shrink: if recent breakout day had high VR, subsequent days should cool
        avg20 = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean())
        last_vr = float(vol.iloc[-1] / avg20) if avg20 > 0 else 1.0
        meta["vol_ratio"] = round(last_vr, 3)
        # look for a spike in last 10d then shrink
        if len(vol) >= 15 and avg20 > 0:
            window = vol.tail(10) / avg20
            spike_i = int(window.values.argmax())
            spike_vr = float(window.iloc[spike_i])
            after = window.iloc[spike_i + 1 :]
            if spike_vr >= 1.8 and len(after) >= 2:
                shrink = float(after.mean()) < spike_vr * 0.7
                meta["post_break_shrink"] = shrink
                meta["spike_vr"] = round(spike_vr, 2)
                if shrink:
                    pts += 5.0
                    fav.append("突破后量能收敛")
                else:
                    pts += 1.5
                    lim.append("突破后量能未收敛")
            else:
                meta["post_break_shrink"] = None
                pts += 3.0  # mild default
        else:
            pts += 3.0

        # penalize vol-stall: VR>2 and close near day's low
        if len(h) >= 2 and avg20 > 0:
            day_h = float(h["high"].iloc[-1])
            day_l = float(h["low"].iloc[-1])
            day_c = float(close.iloc[-1])
            rng = day_h - day_l
            close_pos = (day_c - day_l) / rng if rng > 0 else 0.5
            meta["close_pos"] = round(close_pos, 3)
            if last_vr >= 2.0 and close_pos <= 0.3:
                pts -= 4.0
                lim.append("放量滞涨(vol-stall)")
                meta["vol_stall"] = True
            else:
                meta["vol_stall"] = False
                if last_vr >= 1.2 and close_pos >= 0.6:
                    pts += 2.0
                    fav.append("量价配合偏多")

        pts = float(max(0.0, min(W_VOLUME, pts)))
        meta["points"] = round(pts, 2)
        meta["max"] = W_VOLUME
        return pts, meta, fav, lim

    def _score_fund(
        self, code6: str, fund_dict: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[float], Dict[str, Any], List[str], List[str], bool]:
        """
        Fund continuity 0–20. Returns (points_or_None if fully missing, meta, fav, lim, insufficient).
        """
        meta: Dict[str, Any] = {"max": W_FUND}
        fav: List[str] = []
        lim: List[str] = []
        metrics: Dict[str, Any] = {}

        # Prefer passed fund_dict from V2.1 enrich; else evaluate
        fd = fund_dict or {}
        if not fd and self._fund is not None:
            try:
                fr = self._fund.evaluate(code6)
                fd = fr.to_dict() if hasattr(fr, "to_dict") else dict(fr)
            except Exception as e:
                meta["error"] = f"{type(e).__name__}"
                lim.append("基本面持续性数据不足")
                return None, meta, fav, lim, True

        if not fd or fd.get("data_insufficient"):
            lim.append("基本面持续性DATA_INSUFFICIENT")
            meta["points"] = None
            return None, meta, fav, lim, True

        m = fd.get("metrics") or {}
        rev_g = _safe_float(m.get("revenue_growth_pct"))
        ded_g = _safe_float(m.get("deducted_profit_growth_pct"))
        prof_g = _safe_float(m.get("profit_growth_pct"))
        cash_r = _safe_float(m.get("cash_profit_ratio"))
        pos4 = m.get("profit_pos_4q")
        known4 = m.get("profit_known_4q")
        metrics.update(
            {
                "revenue_growth_pct": rev_g,
                "deducted_profit_growth_pct": ded_g,
                "profit_growth_pct": prof_g,
                "cash_profit_ratio": cash_r,
                "profit_pos_4q": pos4,
                "profit_known_4q": known4,
            }
        )

        # Need at least one growth signal
        if rev_g is None and ded_g is None and prof_g is None and not known4:
            lim.append("基本面增长字段缺失")
            meta["points"] = None
            meta.update(metrics)
            return None, meta, fav, lim, True

        pts = 0.0
        # multi-quarter / growth quality (up to ~12)
        g = ded_g if ded_g is not None else prof_g
        if rev_g is not None:
            if rev_g >= 20:
                pts += 5.0
                fav.append(f"营收同比高增({rev_g:.1f}%)")
            elif rev_g >= 10:
                pts += 4.0
                fav.append(f"营收同比稳健({rev_g:.1f}%)")
            elif rev_g >= 0:
                pts += 2.5
            else:
                lim.append(f"营收同比下滑({rev_g:.1f}%)")
        if g is not None:
            if g >= 30:
                pts += 5.0
                fav.append(f"扣非/净利高增({g:.1f}%)")
            elif g >= 10:
                pts += 4.0
                fav.append(f"扣非/净利正增({g:.1f}%)")
            elif g >= 0:
                pts += 2.0
            else:
                lim.append(f"扣非/净利下滑({g:.1f}%)")

        # accel proxy: both rev and profit positive
        if rev_g is not None and g is not None:
            if rev_g >= 10 and g >= 15:
                pts += 3.0
                fav.append("营收与扣非同步加速迹象")
                meta["growth_accel"] = True
            elif rev_g > 0 and g > 0:
                pts += 1.5
                meta["growth_accel"] = False
            else:
                meta["growth_accel"] = False
        else:
            meta["growth_accel"] = None

        # cash sync (up to 3)
        if cash_r is not None:
            if cash_r >= 1.0:
                pts += 3.0
                fav.append(f"经营现金流同步(比值{cash_r:.2f})")
            elif cash_r >= 0.5:
                pts += 1.5
            elif cash_r < 0:
                lim.append("经营现金流为负")
            else:
                pts += 0.5
        elif "CASH_RATIO_SKIPPED_FIN" in (fd.get("tags") or []):
            pts += 1.5  # neutral for financials
            meta["cash_skipped_fin"] = True

        # 4Q continuity (up to 2)
        try:
            if known4 and int(known4) >= 3:
                if int(pos4 or 0) >= 4:
                    pts += 2.0
                    fav.append("近4季盈利均为正")
                elif int(pos4 or 0) >= 3:
                    pts += 1.0
        except Exception:
            pass

        pts = float(max(0.0, min(W_FUND, pts)))
        meta["points"] = round(pts, 2)
        meta.update(metrics)
        return pts, meta, fav, lim, False

    def _score_sector(
        self,
        code6: str,
        sector_dict: Optional[Dict[str, Any]] = None,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
    ) -> Tuple[Optional[float], Dict[str, Any], List[str], List[str], bool]:
        meta: Dict[str, Any] = {"max": W_SECTOR}
        fav: List[str] = []
        lim: List[str] = []

        sd = sector_dict or {}
        if not sd and self._sector is not None:
            try:
                sr = self._sector.evaluate(code6, hist=hist, t_date=t_date)
                sd = sr.to_dict() if hasattr(sr, "to_dict") else dict(sr)
            except Exception as e:
                meta["error"] = type(e).__name__
                lim.append("板块持续性数据不足")
                return None, meta, fav, lim, True

        if not sd or sd.get("data_insufficient"):
            lim.append("板块持续性DATA_INSUFFICIENT")
            meta["points"] = None
            return None, meta, fav, lim, True

        m = sd.get("metrics") or {}
        sec_r5 = _safe_float(m.get("sector_return5"))
        sec_r20 = _safe_float(m.get("sector_return20"))
        ma_share = _safe_float(m.get("sector_ma_stack_share"))
        rs = _safe_float(m.get("relative_strength"))
        stock_stack = m.get("stock_ma_stack")

        meta.update(
            {
                "sector_return5": sec_r5,
                "sector_return20": sec_r20,
                "sector_ma_stack_share": ma_share,
                "relative_strength": rs,
                "industry": sd.get("industry") or m.get("industry"),
            }
        )

        # If peers totally missing and only stock-alone mid score, still usable
        pts = 0.0
        if sec_r20 is not None:
            if sec_r20 >= 10:
                pts += 5.0
                fav.append(f"板块20日强势({sec_r20:.1f}%)")
            elif sec_r20 >= 5:
                pts += 4.0
                fav.append(f"板块20日偏强({sec_r20:.1f}%)")
            elif sec_r20 >= 0:
                pts += 2.5
            elif sec_r20 >= -5:
                pts += 1.0
                lim.append(f"板块20日偏弱({sec_r20:.1f}%)")
            else:
                lim.append(f"板块20日走弱({sec_r20:.1f}%)")
        elif sec_r5 is None and rs is None and ma_share is None:
            # truly nothing
            lim.append("板块收益/广度缺失")
            meta["points"] = None
            return None, meta, fav, lim, True
        else:
            pts += 2.0  # soft mid

        if sec_r5 is not None:
            if sec_r5 >= 3:
                pts += 2.0
            elif sec_r5 < -3:
                pts -= 1.0
                lim.append(f"板块5日回落({sec_r5:.1f}%)")

        # breadth / MA stack share
        if ma_share is not None:
            if ma_share >= 0.6:
                pts += 4.0
                fav.append(f"板块多头广度高({ma_share:.0%})")
            elif ma_share >= 0.4:
                pts += 2.5
            elif ma_share <= 0.25:
                lim.append(f"板块多头广度低({ma_share:.0%})")
        else:
            pts += 1.0

        if rs is not None:
            if rs >= 8:
                pts += 4.0
                fav.append(f"相对强度领先(RS={rs:.1f})")
            elif rs >= 3:
                pts += 3.0
                fav.append(f"相对强度占优(RS={rs:.1f})")
            elif rs >= 0:
                pts += 1.5
            else:
                lim.append(f"相对强度落后(RS={rs:.1f})")
                pts += 0.5
        if stock_stack:
            pts += 1.0

        pts = float(max(0.0, min(W_SECTOR, pts)))
        meta["points"] = round(pts, 2)
        return pts, meta, fav, lim, False

    def _score_space(self, h: pd.DataFrame) -> Tuple[float, Dict[str, Any], List[str], List[str]]:
        close = h["close"].astype(float)
        high = h["high"].astype(float)
        last = float(close.iloc[-1])
        pts = 0.0
        meta: Dict[str, Any] = {"max": W_SPACE}
        fav: List[str] = []
        lim: List[str] = []

        def dist_high(n: int) -> Optional[float]:
            if len(high) < n:
                return None
            # prior high excluding today for "room", but include for fresh BO detect
            hh = float(high.iloc[-n:].max())
            if hh <= 0:
                return None
            return (last / hh - 1.0) * 100.0

        d20 = dist_high(20)
        d60 = dist_high(60) if len(high) >= 60 else None
        d120 = dist_high(120) if len(high) >= 120 else None
        meta["dist_high20_pct"] = None if d20 is None else round(d20, 3)
        meta["dist_high60_pct"] = None if d60 is None else round(d60, 3)
        meta["dist_high120_pct"] = None if d120 is None else round(d120, 3)

        # Fresh breakout: close at/above 20d high
        fresh20 = d20 is not None and d20 >= -0.3
        fresh60 = d60 is not None and d60 >= -0.5
        meta["fresh_breakout_20"] = bool(fresh20)
        meta["fresh_breakout_60"] = bool(fresh60)

        if fresh20:
            pts += 4.0
            fav.append("贴近/突破20日高")
            if fresh60:
                pts += 2.0
                fav.append("同步挑战60日高")
        else:
            # room left below highs can also be ok for continuation after base
            if d20 is not None and -8 <= d20 < -0.3:
                pts += 3.0
                fav.append("距20日高有消化空间")
            elif d20 is not None and d20 < -12:
                pts += 1.0
                lim.append("距20日高较远")
            else:
                pts += 2.0

        # 120d: not already extremely extended above long high for long
        if d120 is not None:
            if -5 <= d120 <= 5:
                pts += 3.0
                fav.append("处于120日高附近（趋势空间打开）")
            elif d120 > 15:
                pts += 1.0
                lim.append("远超120日高，空间消耗偏多")
            elif d120 < -20:
                pts += 1.5
            else:
                pts += 2.0
        else:
            pts += 1.0

        pts = float(max(0.0, min(W_SPACE, pts)))
        meta["points"] = round(pts, 2)
        return pts, meta, fav, lim

    def score_consumption(self, h: pd.DataFrame) -> Tuple[float, Dict[str, Any], List[str]]:
        """TrendConsumption 0–100 — higher = more exhausted / stretched."""
        close = h["close"].astype(float)
        meta: Dict[str, Any] = {}
        notes: List[str] = []
        score = 0.0

        # consecutive up days
        up = 0
        for i in range(len(close) - 1, 0, -1):
            if float(close.iloc[i]) > float(close.iloc[i - 1]):
                up += 1
            else:
                break
        meta["consecutive_up_days"] = up
        if up >= 7:
            score += 25
            notes.append(f"连涨{up}日")
        elif up >= 5:
            score += 18
            notes.append(f"连涨{up}日")
        elif up >= 3:
            score += 10
        elif up >= 2:
            score += 5

        r10 = _ret_pct(close, 10)
        r20 = _ret_pct(close, 20)
        meta["ret10_pct"] = None if r10 is None else round(r10, 3)
        meta["ret20_pct"] = None if r20 is None else round(r20, 3)
        if r10 is not None:
            if r10 > 30:
                score += 25
                notes.append(f"ret10>{30}({r10:.1f}%)")
            elif r10 > 20:
                score += 15
            elif r10 > 12:
                score += 8
            elif r10 > 5:
                score += 3
        if r20 is not None:
            if r20 > 50:
                score += 25
                notes.append(f"ret20>{50}({r20:.1f}%)")
            elif r20 > 35:
                score += 15
            elif r20 > 20:
                score += 8
            elif r20 > 10:
                score += 3

        ma20 = _ma(close, 20)
        last = float(close.iloc[-1])
        dev = None
        if ma20 and ma20 > 0:
            dev = (last / ma20 - 1.0) * 100.0
            meta["ma20_deviation_pct"] = round(dev, 3)
            if dev > 25:
                score += 15
                notes.append(f"MA20偏离过大({dev:.1f}%)")
            elif dev > 15:
                score += 10
            elif dev > 10:
                score += 5
            elif dev > 5:
                score += 2

        atr = _atr(h, 14)
        atr_prev = None
        if len(h) >= 28:
            atr_prev = _atr(h.iloc[:-7], 14)
        meta["atr"] = None if atr is None else round(atr, 4)
        atr_pct = None
        if atr is not None and last > 0:
            atr_pct = atr / last * 100.0
            meta["atr_close_pct"] = round(atr_pct, 3)
            # expansion vs prior
            expand = False
            if atr_prev and atr_prev > 0 and atr > atr_prev * 1.25:
                expand = True
            meta["atr_expanding"] = expand
            if expand and atr_pct >= 4:
                score += 10
                notes.append("ATR/Close扩张")
            elif atr_pct >= 5:
                score += 6
            elif atr_pct >= 3:
                score += 3

        score = _clip(score, 0, 100)
        meta["points"] = round(score, 1)
        return score, meta, notes

    def classify_type(
        self,
        continuity: float,
        consumption: float,
        overextended: bool = False,
        phase: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        TYPE_A健康延续 / TYPE_B加速延续 / TYPE_C高位加速 / TYPE_D趋势衰减
        """
        if continuity < 60:
            return "TYPE_D", TYPE_CN["TYPE_D"]
        if consumption >= 70 or (overextended and consumption >= 60):
            return "TYPE_C", TYPE_CN["TYPE_C"]
        # accelerating structure with elevated but not extreme consumption
        accel_phase = phase in ("MAIN_RISE_ACCEL", "MAIN_RISE", "SECOND_BREAKOUT")
        if continuity >= 75 and consumption >= 50:
            return "TYPE_B", TYPE_CN["TYPE_B"]
        if continuity >= 70 and accel_phase and consumption >= 45:
            return "TYPE_B", TYPE_CN["TYPE_B"]
        if continuity >= 75 and consumption < 50:
            return "TYPE_A", TYPE_CN["TYPE_A"]
        # mid continuity
        if continuity >= 60 and consumption < 70:
            return "TYPE_A", TYPE_CN["TYPE_A"]
        return "TYPE_C", TYPE_CN["TYPE_C"]

    # ---- history / delta ----

    def _hist_path(self, code6: str) -> Path:
        return self.cache_dir / f"{code6}.json"

    def load_history(self, code6: str) -> List[Dict[str, Any]]:
        p = self._hist_path(code6)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "history" in data:
                return list(data["history"])
        except Exception:
            return []
        return []

    def persist_history(
        self,
        code6: str,
        t_date: str,
        continuity: float,
        consumption: float,
        ctype: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        hist = self.load_history(code6)
        entry = {
            "date": t_date,
            "continuity": round(float(continuity), 2),
            "consumption": round(float(consumption), 2),
            "type": ctype,
        }
        if extra:
            entry.update(extra)
        # upsert by date
        hist = [e for e in hist if str(e.get("date")) != t_date]
        hist.append(entry)
        hist.sort(key=lambda e: str(e.get("date")))
        # keep last 120
        hist = hist[-120:]
        self._hist_path(code6).write_text(
            json.dumps({"code": code6, "history": hist}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return hist

    def compute_deltas(
        self, hist: List[Dict[str, Any]], current: float
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
        """ContinuityDelta1/3/5 vs prior sessions in persisted history."""
        if not hist:
            return None, None, None, None
        # hist includes current already; find prior
        scores = [(_safe_float(e.get("continuity")), str(e.get("date"))) for e in hist]
        scores = [(s, d) for s, d in scores if s is not None]
        if len(scores) < 2:
            return None, None, None, None

        def delta_n(n: int) -> Optional[float]:
            if len(scores) <= n:
                return None
            prev = scores[-(n + 1)][0]
            if prev is None:
                return None
            return round(current - prev, 2)

        d1, d3, d5 = delta_n(1), delta_n(3), delta_n(5)
        label = None
        # prefer multi-day confirmation
        signals = [x for x in (d1, d3) if x is not None]
        if len(signals) >= 1:
            if all(x > 0.5 for x in signals):
                label = "IMPROVING"
            elif all(x < -0.5 for x in signals):
                label = "DETERIORATING"
            elif d1 is not None and abs(d1) <= 0.5 and (d3 is None or abs(d3) <= 1.0):
                label = "STABLE"
            elif d1 is not None and d1 > 0.5:
                label = "IMPROVING"
            elif d1 is not None and d1 < -0.5:
                label = "DETERIORATING"
            else:
                label = "STABLE"
        return d1, d3, d5, label

    def evaluate(
        self,
        code: str,
        hist: pd.DataFrame,
        t_date: Optional[str] = None,
        fund: Optional[Dict[str, Any]] = None,
        sector: Optional[Dict[str, Any]] = None,
        phase: Optional[str] = None,
        persist: bool = True,
    ) -> ContinuityResult:
        code6 = _norm_code(code)
        res = ContinuityResult()
        h = _prep_ohlcv(hist, t_date)
        if h is None:
            res.notes = ["行情不足，无法计算持续性"]
            res.data_insufficient = True
            res.tags = ["DATA_INSUFFICIENT"]
            return res

        fav_all: List[str] = []
        lim_all: List[str] = []
        notes: List[str] = []
        tags: List[str] = []
        comps: Dict[str, Any] = {}

        s_pts, s_meta, s_f, s_l = self._score_structure(h)
        comps["structure"] = s_meta
        fav_all.extend(s_f)
        lim_all.extend(s_l)

        d_pts, d_meta, d_f, d_l, overext = self._score_drawdown(h)
        comps["drawdown"] = d_meta
        fav_all.extend(d_f)
        lim_all.extend(d_l)
        if overext:
            tags.append("OVEREXTENDED")

        v_pts, v_meta, v_f, v_l = self._score_volume(h)
        comps["volume"] = v_meta
        fav_all.extend(v_f)
        lim_all.extend(v_l)
        if v_meta.get("vol_stall"):
            tags.append("VOL_STALL")

        f_pts, f_meta, f_f, f_l, f_insuf = self._score_fund(code6, fund)
        comps["fund"] = f_meta
        fav_all.extend(f_f)
        lim_all.extend(f_l)

        sec_pts, sec_meta, sec_f, sec_l, sec_insuf = self._score_sector(
            code6, sector, hist=h, t_date=t_date
        )
        comps["sector"] = sec_meta
        fav_all.extend(sec_f)
        lim_all.extend(sec_l)

        sp_pts, sp_meta, sp_f, sp_l = self._score_space(h)
        comps["space"] = sp_meta
        fav_all.extend(sp_f)
        lim_all.extend(sp_l)

        # Assemble with renormalization for missing fund/sector
        parts = [
            ("structure", s_pts, W_STRUCTURE, False),
            ("drawdown", d_pts, W_DRAWDOWN, False),
            ("volume", v_pts, W_VOLUME, False),
            ("fund", f_pts, W_FUND, f_insuf),
            ("sector", sec_pts, W_SECTOR, sec_insuf),
            ("space", sp_pts, W_SPACE, False),
        ]
        available_w = 0.0
        raw_sum = 0.0
        insuf_parts: List[str] = []
        for name, pts, w, insuf in parts:
            if insuf or pts is None:
                insuf_parts.append(name)
                continue
            available_w += w
            raw_sum += float(pts)

        if available_w <= 0:
            res.notes = ["持续性各维均不足"]
            res.data_insufficient = True
            res.tags = ["DATA_INSUFFICIENT"]
            res.limiting = lim_all
            res.components = comps
            return res

        # Partial score: scale available points to 0–100 by available weight
        continuity = _clip(raw_sum / available_w * 100.0, 0, 100)
        if insuf_parts:
            notes.append(
                "DATA_INSUFFICIENT:" + ",".join(insuf_parts) + "（按可用权重归一，非整分清零）"
            )
            tags.append("DATA_INSUFFICIENT")
            res.data_insufficient = True  # partial
            res.insufficient_parts = insuf_parts

        cons, cons_meta, cons_notes = self.score_consumption(h)
        comps["consumption"] = cons_meta
        if cons_notes:
            notes.extend(["消耗:" + n for n in cons_notes])

        ctype, ctype_cn = self.classify_type(continuity, cons, overextended=overext, phase=phase)

        # deltas via history
        hist_rows: List[Dict[str, Any]] = []
        if persist and t_date:
            hist_rows = self.persist_history(
                code6,
                t_date,
                continuity,
                cons,
                ctype,
                extra={"phase": phase},
            )
        else:
            hist_rows = self.load_history(code6)
        d1, d3, d5, dlab = self.compute_deltas(hist_rows, continuity)
        if dlab:
            tags.append(dlab)

        res.ok = True
        res.continuity_score = round(continuity, 1)
        res.consumption_score = round(cons, 1)
        res.continuity_type = ctype
        res.continuity_type_cn = ctype_cn
        res.star = _star_of(continuity)
        res.components = comps
        res.tags = tags
        res.notes = notes
        res.favorable = fav_all[:12]
        res.limiting = lim_all[:12]
        res.sector_continuity = None if sec_pts is None else round(float(sec_pts), 2)
        res.overextended = overext
        res.delta1 = d1
        res.delta3 = d3
        res.delta5 = d5
        res.delta_label = dlab
        res.metrics = {
            "code6": code6,
            "t_date": t_date,
            "available_weight": available_w,
            "raw_sum": round(raw_sum, 2),
            "star": res.star,
            "type": ctype,
            "type_cn": ctype_cn,
        }
        return res


__all__ = [
    "ContinuityEngine",
    "ContinuityResult",
    "TYPE_CN",
    "W_STRUCTURE",
    "W_DRAWDOWN",
    "W_VOLUME",
    "W_FUND",
    "W_SECTOR",
    "W_SPACE",
]
