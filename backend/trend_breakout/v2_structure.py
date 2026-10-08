#!/usr/bin/env python3
"""
V2.0 阶梯式主升突破模型 — structure detection + scoring helpers.

Seek: 底部蓄势→首次突破→健康分歧→平台不破→二次突破→主升加速.
Focus ④→⑤→⑥ (PLATFORM_HOLD → SECOND_BREAKOUT → MAIN_RISE_ACCEL).
No invented prices. No buy advice.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---- phase enum ----
PHASE_CN = {
    "ACCUMULATION": "底部蓄势",
    "PLATFORM": "平台整理",
    "FIRST_BREAKOUT": "首次突破",
    "HEALTHY_PULLBACK": "健康分歧",
    "PLATFORM_HOLD": "平台不破",
    "SECOND_BREAKOUT": "二次突破",
    "MAIN_RISE_ACCEL": "主升加速",
    "OVERHEATED": "过热",
    "TREND_FAILURE": "趋势破坏",
    "NONE": "未成型",
    "OBSERVE": "观察",
}

DROP_REASONS = {
    "SINGLE_DAY_CRASH": "单日跌幅>15%无平台",
    "CHASE_NO_PULLBACK": "无健康分歧追涨",
    "VOL_STALL": "放量滞涨(VR>2收近低)",
    "DUMP": "放量下跌(VR>1.5)",
    "TREND_FAILURE": "跌破MA20+放量",
    "OVERHEATED": "5日涨幅>35过热",
    "HIGH_RISK_LIMIT": "近段≥3涨停高风险",
}


def ma_series(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def slope_pct(series: pd.Series, lookback: int = 10) -> float:
    """(val/val_Nago - 1) * 100"""
    if len(series) < lookback + 1:
        return float("nan")
    a = float(series.iloc[-1])
    b = float(series.iloc[-1 - lookback])
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return (a / b - 1.0) * 100.0


def safe_div(a: float, b: float, default: float = float("nan")) -> float:
    if b is None or not np.isfinite(b) or b == 0:
        return default
    if a is None or not np.isfinite(a):
        return default
    return a / b


@dataclass
class StructureResult:
    ok: bool = False
    drop: bool = False
    drop_reason: str = ""
    phase: str = "NONE"
    phase_cn: str = PHASE_CN["NONE"]
    kjs_pattern: bool = False  # True if T-day KJS (KJS_NOW)
    kjs_hist: bool = False  # True if any KJS in lookback (excl. or incl. T)
    kjs_ever_60d: bool = False
    kjs_last_date: Optional[str] = None
    kjs_tag: Optional[str] = None  # KJS_NOW | KJS_HIST | None
    overheated: bool = False
    pushable_core: bool = False  # S/A core buy-candidate eligibility (False for OH)
    score: float = 0.0
    grade: Optional[str] = None
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    risks: List[str] = field(default_factory=list)
    flags: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def _platform_window(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    end_idx: int,
    min_w: int = 5,
    max_w: int = 20,
) -> Optional[Dict[str, Any]]:
    """
    Find best platform window ending at end_idx (inclusive), length 5–20.
    Prefer range ≤12%, require ≤15%; back-half low ≥ front-half low.
    PlatformHigh/Low from closes (robust: also consider highs/lows lightly).
    """
    best = None
    best_key = None
    for w in range(min_w, max_w + 1):
        start = end_idx - w + 1
        if start < 0:
            continue
        c = close.iloc[start : end_idx + 1]
        h = high.iloc[start : end_idx + 1]
        l = low.iloc[start : end_idx + 1]
        # primary: closes
        plat_high = float(c.max())
        plat_low = float(c.min())
        # robust blend: slightly widen with highs/lows but stay close-anchored
        plat_high = max(plat_high, float(h.max()) * 0.995)  # soft
        plat_high = float(c.max())  # stick to closes as primary per spec
        plat_low = float(c.min())
        if plat_low <= 0 or plat_high <= plat_low:
            continue
        rng = (plat_high / plat_low - 1.0) * 100.0
        if rng > 15.0:
            continue
        mid = start + w // 2
        front_low = float(c.iloc[: w // 2].min()) if w >= 4 else float(c.min())
        back_low = float(c.iloc[w // 2 :].min()) if w >= 4 else float(c.min())
        if back_low < front_low * 0.995:  # allow tiny noise; prefer back >= front
            # soft fail: allow if very close
            if back_low < front_low * 0.98:
                continue
        last_c = float(c.iloc[-1])
        close_pos = (last_c - plat_low) / (plat_high - plat_low) if plat_high > plat_low else 0.0
        # prefer tighter range, higher close position, longer stable platform
        prefer = (1 if rng <= 12 else 0) + (1 if close_pos >= 0.80 else 0) + (1 if back_low >= front_low else 0)
        key = (prefer, -rng, close_pos, w)
        if best_key is None or key > best_key:
            best_key = key
            best = {
                "window": w,
                "start_idx": start,
                "end_idx": end_idx,
                "platform_high": plat_high,
                "platform_low": plat_low,
                "platform_range_pct": rng,
                "close_position": close_pos,
                "front_low": front_low,
                "back_low": back_low,
                "prefer_flags": prefer,
            }
    return best


def detect_ma_structure(close: pd.Series) -> Dict[str, Any]:
    ma5 = ma_series(close, 5)
    ma10 = ma_series(close, 10)
    ma20 = ma_series(close, 20)
    ma60 = ma_series(close, 60)
    c = float(close.iloc[-1])
    m5, m10, m20, m60 = float(ma5.iloc[-1]), float(ma10.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1])
    s5 = slope_pct(ma5, 5)
    s10 = slope_pct(ma10, 5)
    s20 = slope_pct(ma20, 10)  # MA20Slope = (MA20/MA20_10ago-1)*100
    return {
        "ma5": m5,
        "ma10": m10,
        "ma20": m20,
        "ma60": m60,
        "ma5_gt_ma10_gt_ma20": bool(m5 > m10 > m20),
        "ma20_ge_ma60": bool(m20 >= m60 * 0.995),  # slight below OK
        "ma20_slope": s20,
        "ma5_slope": s5,
        "ma10_slope": s10,
        "close_ge_ma20": bool(c >= m20),
        "slopes_positive": bool(
            (np.isfinite(s5) and s5 > 0)
            and (np.isfinite(s10) and s10 > 0)
            and (np.isfinite(s20) and s20 > 0)
        ),
    }


def _vol_ma20(vol: pd.Series, i: int) -> float:
    if i < 20:
        return float("nan")
    return float(vol.iloc[i - 20 : i].mean())  # exclude day i itself (prior 20)


def _find_first_breakout(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    vol: pd.Series,
    amt: pd.Series,
    lookback: int = 40,
) -> Optional[Dict[str, Any]]:
    """
    Scan recent bars for a first breakout day: Close > PlatformHigh*1.01 and Vol/VolMA20 >= 1.3.
    Platform is computed on window ending the day before the breakout.
    """
    n = len(close)
    t = n - 1
    # search first-BO candidates in [t-30, t-2] so room for pullback + second
    search_lo = max(25, t - lookback)
    search_hi = t - 2  # need at least 2 days after for pullback window min
    best = None
    for i in range(search_hi, search_lo - 1, -1):
        plat = _platform_window(close, high, low, i - 1, 5, 20)
        if plat is None:
            continue
        ph = plat["platform_high"]
        pl = plat["platform_low"]
        ci = float(close.iloc[i])
        if ci <= ph * 1.01:
            continue
        vma = _vol_ma20(vol, i)
        vi = float(vol.iloc[i])
        vr = safe_div(vi, vma)
        if not np.isfinite(vr) or vr < 1.3:
            # try amount
            ama = float(amt.iloc[i - 20 : i].mean()) if i >= 20 else float("nan")
            ar = safe_div(float(amt.iloc[i]), ama)
            if np.isfinite(ar) and ar >= 1.3:
                vr = ar
            else:
                continue
        amt_up = True
        if i >= 1 and np.isfinite(float(amt.iloc[i - 1])):
            amt_up = float(amt.iloc[i]) >= float(amt.iloc[i - 1]) * 0.95
        cand = {
            "idx": i,
            "date": str(close.index[i]) if hasattr(close.index, "dtype") else i,
            "breakout_high": float(high.iloc[i]),
            "breakout_close": ci,
            "platform": plat,
            "vol_ratio": vr,
            "amt_up": amt_up,
            "prefer_vol": vr >= 1.8,
        }
        # prefer more recent, stronger vol
        if best is None or (i > best["idx"]) or (i == best["idx"] and vr > best["vol_ratio"]):
            # take most recent valid first BO
            if best is None or i >= best["idx"]:
                best = cand
                # most recent wins — since we iterate descending, first hit is most recent
                break
    return best


def _analyze_pullback(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    vol: pd.Series,
    first: Dict[str, Any],
    t_idx: int,
) -> Optional[Dict[str, Any]]:
    """Pullback window 2–8 days after first BO, ending at day before second BO or at T."""
    i0 = first["idx"]
    ph = first["platform"]["platform_high"]
    pl = first["platform"]["platform_low"]
    bh = first["breakout_high"]
    # pullback days: i0+1 .. min(i0+8, t_idx)
    end = min(i0 + 8, t_idx)
    start = i0 + 1
    if end - start + 1 < 2:
        return None
    seg_c = close.iloc[start : end + 1]
    seg_l = low.iloc[start : end + 1]
    seg_v = vol.iloc[start : end + 1]
    # may close < breakout high but not break platform low
    min_low = float(seg_l.min())
    if min_low < pl:
        return {"ok": False, "reason": "broke_platform_low", "days": end - start + 1}
    pb_pct = (bh - float(seg_c.min())) / bh * 100.0 if bh > 0 else 0.0
    if pb_pct > 12.0:
        return {"ok": False, "reason": "pullback_too_deep", "pullback_pct": pb_pct, "days": end - start + 1}
    # no 3 consecutive big down days (<-3%)
    rets = close.pct_change().iloc[start : end + 1]
    big_down = (rets < -0.03).astype(int).tolist()
    streak = 0
    max_streak = 0
    for x in big_down:
        streak = streak + 1 if x else 0
        max_streak = max(max_streak, streak)
    if max_streak >= 3:
        return {"ok": False, "reason": "3_big_down", "days": end - start + 1}
    # Close >= MA20 on last pullback day (or T if still in pullback)
    ma20 = float(close.iloc[max(0, end - 19) : end + 1].mean()) if end >= 19 else float("nan")
    c_end = float(close.iloc[end])
    close_ge_ma20 = bool(np.isfinite(ma20) and c_end >= ma20)
    # vol shrink: adj avg vol during pullback < breakout-day vol
    bo_vol = float(vol.iloc[i0])
    avg_pb_vol = float(seg_v.mean())
    vol_shrink = avg_pb_vol < bo_vol
    days = end - start + 1
    healthy = (
        2 <= days <= 8
        and pb_pct <= 12.0
        and min_low >= pl
        and max_streak < 3
        and close_ge_ma20
        and vol_shrink
    )
    prefer = pb_pct <= 8.0
    return {
        "ok": healthy,
        "days": days,
        "pullback_pct": pb_pct,
        "min_low": min_low,
        "platform_low": pl,
        "close_ge_ma20": close_ge_ma20,
        "vol_shrink": vol_shrink,
        "prefer_shallow": prefer,
        "end_idx": end,
        "reason": "" if healthy else "not_healthy",
    }


def _count_limit_ups(close: pd.Series, lookback: int = 10, thr: float = 0.095) -> int:
    rets = close.pct_change().iloc[-lookback:]
    return int((rets >= thr).sum())


def score_structure(
    ma: Dict[str, Any],
    plat: Optional[Dict[str, Any]],
    first: Optional[Dict[str, Any]],
    pullback: Optional[Dict[str, Any]],
    second: Optional[Dict[str, Any]],
    accel: Optional[Dict[str, Any]],
    vol_ratio_t: float,
) -> Tuple[float, Dict[str, float]]:
    """
    Score /100:
      均线20 + 平台20 + 首次突破15 + 分歧20 + 二次突破15 + 主升加速10
      + volume bonuses folded into components.
    """
    sb: Dict[str, float] = {
        "ma": 0.0,
        "platform": 0.0,
        "first_bo": 0.0,
        "pullback": 0.0,
        "second_bo": 0.0,
        "accel": 0.0,
    }
    # MA 20
    pts = 0.0
    if ma.get("ma5_gt_ma10_gt_ma20"):
        pts += 8
    elif ma.get("ma20") and ma.get("ma5") and ma["ma5"] > ma["ma20"]:
        pts += 4
    if ma.get("ma20_ge_ma60"):
        pts += 4
    else:
        pts += 2  # slight below OK
    s20 = ma.get("ma20_slope")
    if np.isfinite(s20) and s20 > 1.0:
        pts += 5
    elif np.isfinite(s20) and s20 > 0:
        pts += 3
    if ma.get("slopes_positive"):
        pts += 3
    sb["ma"] = min(20.0, pts)

    # Platform 20
    if plat:
        pts = 8.0
        rng = plat.get("platform_range_pct", 99)
        if rng <= 8:
            pts += 6
        elif rng <= 12:
            pts += 5
        elif rng <= 15:
            pts += 3
        cp = plat.get("close_position", 0)
        if cp >= 0.80:
            pts += 4
        elif cp >= 0.65:
            pts += 2
        if plat.get("back_low", 0) >= plat.get("front_low", 1):
            pts += 2
        sb["platform"] = min(20.0, pts)

    # First breakout 15
    if first:
        pts = 8.0
        vr = first.get("vol_ratio", 0)
        if vr >= 1.8:
            pts += 5
        elif vr >= 1.3:
            pts += 3
        if first.get("amt_up"):
            pts += 2
        sb["first_bo"] = min(15.0, pts)

    # Pullback / 分歧 20
    if pullback and pullback.get("ok"):
        pts = 10.0
        if pullback.get("prefer_shallow"):
            pts += 4
        else:
            pts += 2
        if pullback.get("vol_shrink"):
            pts += 4
        if pullback.get("close_ge_ma20"):
            pts += 2
        sb["pullback"] = min(20.0, pts)
    elif pullback and not pullback.get("ok") and pullback.get("reason") != "broke_platform_low":
        sb["pullback"] = 4.0  # partial

    # Second breakout 15
    if second and second.get("ok"):
        pts = 8.0
        if second.get("vol_ratio", 0) >= 1.2:
            pts += 3
        if second.get("close_position", 0) >= 0.8:
            pts += 2
        if second.get("day_pct", 0) >= 5:
            pts += 2
        sb["second_bo"] = min(15.0, pts)

    # Accel 10
    if accel:
        pts = 0.0
        if accel.get("return5_gt_half10"):
            pts += 4
        if accel.get("ma5_slope_gt_ma10"):
            pts += 3
        r5 = accel.get("return5_pct", 0)
        if 10 <= r5 <= 25:
            pts += 3
        elif r5 > 25:
            pts += 1  # lower score when hot
        sb["accel"] = min(10.0, pts)

    # volume bonus sprinkle into first/second if VR strong at T
    if np.isfinite(vol_ratio_t):
        if vol_ratio_t >= 2.0:
            sb["second_bo"] = min(15.0, sb["second_bo"] + 1)
        elif vol_ratio_t >= 1.5:
            sb["first_bo"] = min(15.0, sb["first_bo"] + 0.5)

    total = float(sum(sb.values()))
    return min(100.0, total), sb



def _kjs_at_end(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    vol: pd.Series,
    amt: pd.Series,
    end_idx: int,
) -> bool:
    """
    Whether first + healthy pullback 2–8d + second + MA5 slope up all hold
    when the series is truncated at end_idx (inclusive).
    """
    if end_idx < 40:
        return False
    c = close.iloc[: end_idx + 1]
    h = high.iloc[: end_idx + 1]
    l = low.iloc[: end_idx + 1]
    v = vol.iloc[: end_idx + 1]
    a = amt.iloc[: end_idx + 1]
    first = _find_first_breakout(c, h, l, v, a)
    if not first:
        return False
    t_local = len(c) - 1
    pullback = _analyze_pullback(c, h, l, v, first, t_local)
    if not pullback or not pullback.get("ok"):
        return False
    if not (2 <= pullback.get("days", 0) <= 8):
        return False
    ph = first["platform"]["platform_high"]
    pl = first["platform"]["platform_low"]
    bh = first["breakout_high"]
    cT = float(c.iloc[-1])
    days_since = t_local - first["idx"]
    if days_since < 2 or not (cT > bh and cT > ph):
        return False
    vma = _vol_ma20(v, t_local)
    vr = safe_div(float(v.iloc[-1]), vma)
    if not np.isfinite(vr):
        ama = float(a.iloc[max(0, t_local - 20) : t_local].mean()) if t_local >= 1 else float("nan")
        vr = safe_div(float(a.iloc[-1]), ama)
    cp = (cT - pl) / (ph - pl) if ph > pl else 0.0
    if not (np.isfinite(vr) and vr >= 1.2 and cp >= 0.8):
        return False
    ma = detect_ma_structure(c)
    if not (np.isfinite(ma.get("ma5_slope")) and ma["ma5_slope"] > 0):
        return False
    if not (
        np.isfinite(ma.get("ma5_slope"))
        and np.isfinite(ma.get("ma10_slope"))
        and ma["ma5_slope"] > ma["ma10_slope"]
    ):
        return False
    return True


def scan_kjs_history(
    hist: pd.DataFrame,
    lookback: int = 60,
    dates: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """
    Scan recent lookback trading days (including T) for any day where KJS held.
    Returns kjs_ever_60d, kjs_last_date (YYYY-MM-DD or None), and list of hit dates.
    """
    out: Dict[str, Any] = {
        "kjs_ever_60d": False,
        "kjs_last_date": None,
        "kjs_hit_dates": [],
    }
    if hist is None or len(hist) < 70:
        return out
    df = hist.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    amt = df["amount"].astype(float)
    n = len(df)
    t = n - 1
    start = max(40, t - lookback + 1)
    hits: List[str] = []
    last_date = None
    # iterate newest→oldest; first hit is kjs_last_date (enough for ever_60d)
    for end_idx in range(t, start - 1, -1):
        try:
            if _kjs_at_end(close, high, low, vol, amt, end_idx):
                if "date" in df.columns:
                    d = pd.Timestamp(df["date"].iloc[end_idx]).strftime("%Y-%m-%d")
                else:
                    d = str(end_idx)
                hits.append(d)
                last_date = d
                break  # most recent is sufficient
        except Exception:
            continue
    out["kjs_hit_dates"] = hits
    out["kjs_ever_60d"] = bool(hits)
    out["kjs_last_date"] = last_date
    return out


def analyze_hist(hist: pd.DataFrame) -> StructureResult:
    """
    Run full staircase structure detection on a hist dataframe ending at T.
    Required cols: date, open, high, low, close, volume, amount
    """
    res = StructureResult()
    if hist is None or len(hist) < 70:
        res.drop = True
        res.drop_reason = "hist_too_short"
        return res

    df = hist.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    amt = df["amount"].astype(float)
    t = len(df) - 1
    cT = float(close.iloc[-1])
    pct_t = float(close.iloc[-1] / close.iloc[-2] - 1) * 100 if t >= 1 else 0.0

    # liquidity
    avg20_amt = float(amt.iloc[-20:].mean())
    if avg20_amt < 3e7:
        res.drop = True
        res.drop_reason = "liq_below_3000wan"
        return res

    ma = detect_ma_structure(close)
    if not (np.isfinite(ma["ma20_slope"]) and ma["ma20_slope"] > 1.0):
        # not hard drop; score will suffer — but preferred filter
        pass

    # hard drop rules
    # single day >15% down without platform
    day_rets = close.pct_change()
    if float(day_rets.iloc[-1]) <= -0.15:
        res.drop = True
        res.drop_reason = "SINGLE_DAY_CRASH"
        res.phase = "TREND_FAILURE"
        res.phase_cn = PHASE_CN["TREND_FAILURE"]
        return res

    vma_t = _vol_ma20(vol, t)
    vr_t = safe_div(float(vol.iloc[-1]), vma_t)
    if not np.isfinite(vr_t):
        ama = float(amt.iloc[-21:-1].mean()) if t >= 20 else float("nan")
        vr_t = safe_div(float(amt.iloc[-1]), ama)

    # Close < MA20 + VR > 1.5 → TREND_FAILURE
    if cT < ma["ma20"] and np.isfinite(vr_t) and vr_t > 1.5:
        res.drop = True
        res.drop_reason = "TREND_FAILURE"
        res.phase = "TREND_FAILURE"
        res.phase_cn = PHASE_CN["TREND_FAILURE"]
        res.risks.append("TREND_FAILURE")
        return res

    # dump VR>1.5 and down day
    if pct_t < -2 and np.isfinite(vr_t) and vr_t > 1.5:
        res.risks.append("DUMP")
        # not auto-drop unless severe
        if pct_t < -5:
            res.drop = True
            res.drop_reason = "DUMP"
            res.phase = "TREND_FAILURE"
            res.phase_cn = PHASE_CN["TREND_FAILURE"]
            return res

    # vol stall: VR>2 close near low
    hi_t, lo_t = float(high.iloc[-1]), float(low.iloc[-1])
    if hi_t > lo_t and np.isfinite(vr_t) and vr_t > 2.0:
        near_low = (cT - lo_t) / (hi_t - lo_t) <= 0.25
        if near_low and pct_t < 1:
            res.risks.append("VOL_STALL")

    # find first breakout
    first = _find_first_breakout(close, high, low, vol, amt)
    pullback = None
    second = None
    plat_now = _platform_window(close, high, low, t - 1 if t > 0 else t, 5, 20)

    if first:
        pullback = _analyze_pullback(close, high, low, vol, first, t)
        # second breakout at T: Close > BreakoutHigh and > platform high; VR>=1.2; ClosePos>=0.8
        ph = first["platform"]["platform_high"]
        pl = first["platform"]["platform_low"]
        bh = first["breakout_high"]
        days_since = t - first["idx"]
        if days_since >= 2 and cT > bh and cT > ph:
            # close position vs original platform
            cp = (cT - pl) / (ph - pl) if ph > pl else 0.0
            # also allow expanded: vs recent range
            vr_ok = np.isfinite(vr_t) and vr_t >= 1.2
            second = {
                "ok": bool(vr_ok and cp >= 0.8),
                "vol_ratio": vr_t,
                "close_position": cp,
                "day_pct": pct_t,
                "days_since_first": days_since,
                "main_rise_candidate": pct_t >= 5.0,
            }
            if not vr_ok:
                second["ok"] = False
                second["reason"] = "vol_weak"
            elif cp < 0.8:
                second["ok"] = False
                second["reason"] = "close_pos_low"
        plat_now = first["platform"]

    # accel
    ret5 = (float(close.iloc[-1] / close.iloc[-6] - 1) * 100) if t >= 5 else float("nan")
    ret10 = (float(close.iloc[-1] / close.iloc[-11] - 1) * 100) if t >= 10 else float("nan")
    accel = {
        "return5_pct": ret5,
        "return10_pct": ret10,
        "return5_gt_half10": bool(np.isfinite(ret5) and np.isfinite(ret10) and ret5 > ret10 / 2.0),
        "ma5_slope_gt_ma10": bool(
            np.isfinite(ma["ma5_slope"])
            and np.isfinite(ma["ma10_slope"])
            and ma["ma5_slope"] > ma["ma10_slope"]
        ),
    }
    n_lu = _count_limit_ups(close, 10)
    overheated = False
    if np.isfinite(ret5) and ret5 > 35:
        res.risks.append("OVERHEATED")
        overheated = True
    if n_lu >= 3:
        res.risks.append("HIGH_RISK_LIMIT")
        overheated = True
    if overheated:
        res.overheated = True
        res.phase = "OVERHEATED"
        res.phase_cn = PHASE_CN["OVERHEATED"]

    # chase without healthy pullback: first BO recently and immediately extending hard
    if first and (t - first["idx"]) <= 1 and pct_t >= 5 and (not pullback or not pullback.get("ok")):
        if cT > first["breakout_high"] * 1.08:
            res.risks.append("CHASE_NO_PULLBACK")

    # phase determination (focus ④→⑤→⑥)
    phase = "NONE"
    if res.phase == "OVERHEATED":
        phase = "OVERHEATED"
    elif second and second.get("ok") and second.get("main_rise_candidate") and accel.get("return5_gt_half10"):
        phase = "MAIN_RISE_ACCEL"
    elif second and second.get("ok"):
        phase = "SECOND_BREAKOUT"
    elif first and pullback and pullback.get("ok") and cT >= first["platform"]["platform_low"]:
        # platform hold after healthy pullback, not yet second
        if cT <= first["breakout_high"]:
            phase = "HEALTHY_PULLBACK"
        else:
            phase = "PLATFORM_HOLD"
    elif first and (t - first["idx"]) <= 1:
        phase = "FIRST_BREAKOUT"
    elif first and pullback and not pullback.get("ok"):
        phase = "OBSERVE"
    elif plat_now and plat_now.get("close_position", 0) >= 0.65 and ma.get("ma20_slope", 0) > 1:
        phase = "PLATFORM"
    elif ma.get("ma20_ge_ma60") and ma.get("close_ge_ma20"):
        phase = "ACCUMULATION"
    else:
        phase = "NONE"

    # KJS_PATTERN: first + healthy 2–8d + no break platform + second + 5d slope up
    kjs = False
    if (
        first
        and pullback
        and pullback.get("ok")
        and 2 <= pullback.get("days", 0) <= 8
        and second
        and second.get("ok")
        and np.isfinite(ma.get("ma5_slope"))
        and ma["ma5_slope"] > 0
        and accel.get("ma5_slope_gt_ma10")
    ):
        kjs = True

    score, sb = score_structure(ma, plat_now, first, pullback, second, accel, vr_t)

    # grades
    full_structure = bool(
        first and pullback and pullback.get("ok") and second and second.get("ok")
    )
    grade = None
    if score >= 85 and full_structure:
        grade = "S"
    elif score >= 75:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 55:
        grade = "C"
    else:
        grade = "DROP"

    # prefer MA filters: soft penalty already in score; hard-ish for push
    if np.isfinite(ret5) and ret5 > 25:
        score = max(0, score - 3)
        if "Return5>25" not in res.risks:
            res.risks.append("Return5>25")

    # re-map grade after score adjustments
    if not res.drop:
        if score >= 85 and full_structure:
            grade = "S"
        elif score >= 75:
            grade = "A"
        elif score >= 65:
            grade = "B"
        elif score >= 55:
            grade = "C"
        else:
            grade = "DROP"
        # MA20 clearly below MA60: demote S/A one notch
        if not ma.get("ma20_ge_ma60") and ma["ma20"] < ma["ma60"] * 0.98:
            if grade == "S":
                grade = "A"
            elif grade == "A":
                grade = "B"

    # OVERHEATED / HIGH_RISK: keep for report with grade OH — NOT silent DROP,
    # NOT S/A core pushable. Preserve numeric score.
    if res.overheated or phase == "OVERHEATED":
        phase = "OVERHEATED"
        res.overheated = True
        # displayable OH grade; not S/A core
        grade = "OH"
        res.drop = False
        res.drop_reason = ""
        if "OVERHEATED" not in res.risks and (np.isfinite(ret5) and ret5 > 35):
            res.risks.append("OVERHEATED")

    # KJS history (last 60 trading days)
    kjs_hist_info = scan_kjs_history(df, lookback=60)
    kjs_ever = bool(kjs_hist_info.get("kjs_ever_60d")) or bool(kjs)
    kjs_last = kjs_hist_info.get("kjs_last_date")
    if kjs and "date" in df.columns:
        kjs_last = pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")
        kjs_tag = "KJS_NOW"
        kjs_hist_flag = False
    elif kjs:
        kjs_tag = "KJS_NOW"
        kjs_hist_flag = False
    elif kjs_hist_info.get("kjs_ever_60d"):
        kjs_tag = "KJS_HIST"
        kjs_hist_flag = True
    else:
        kjs_tag = None
        kjs_hist_flag = False

    pushable_core = bool(not res.overheated and grade in ("S", "A"))

    res.ok = not res.drop and grade not in (None, "DROP")
    res.phase = phase
    res.phase_cn = PHASE_CN.get(phase, phase)
    # kjs_pattern True if T-day OR history (report shows KJS; tag distinguishes)
    res.kjs_pattern = bool(kjs or kjs_hist_info.get("kjs_ever_60d"))
    res.kjs_hist = kjs_hist_flag
    res.kjs_ever_60d = kjs_ever
    res.kjs_last_date = kjs_last
    res.kjs_tag = kjs_tag
    res.overheated = bool(res.overheated)
    res.pushable_core = pushable_core
    res.score = round(score, 2)
    res.grade = grade if grade != "DROP" else None
    res.score_breakdown = {k: round(v, 2) for k, v in sb.items()}
    res.metrics = {
        "close": round(cT, 3),
        "pct_today": round(pct_t, 3),
        "vol_ratio": round(float(vr_t), 3) if np.isfinite(vr_t) else None,
        "avg20_amt_yi": round(avg20_amt / 1e8, 3),
        "ma5": round(ma["ma5"], 3),
        "ma10": round(ma["ma10"], 3),
        "ma20": round(ma["ma20"], 3),
        "ma60": round(ma["ma60"], 3),
        "ma20_slope": round(ma["ma20_slope"], 3) if np.isfinite(ma["ma20_slope"]) else None,
        "ma5_slope": round(ma["ma5_slope"], 3) if np.isfinite(ma["ma5_slope"]) else None,
        "ma10_slope": round(ma["ma10_slope"], 3) if np.isfinite(ma["ma10_slope"]) else None,
        "ma5_gt_ma10_gt_ma20": ma["ma5_gt_ma10_gt_ma20"],
        "ma20_ge_ma60": ma["ma20_ge_ma60"],
        "return5_pct": round(ret5, 3) if np.isfinite(ret5) else None,
        "return10_pct": round(ret10, 3) if np.isfinite(ret10) else None,
        "limit_ups_10d": n_lu,
        "platform_high": round(plat_now["platform_high"], 3) if plat_now else None,
        "platform_low": round(plat_now["platform_low"], 3) if plat_now else None,
        "platform_range_pct": round(plat_now["platform_range_pct"], 3) if plat_now else None,
        "platform_window": plat_now["window"] if plat_now else None,
        "close_position": round(plat_now["close_position"], 3) if plat_now else None,
        "first_bo_idx": first["idx"] if first else None,
        "first_bo_vol_ratio": round(first["vol_ratio"], 3) if first else None,
        "breakout_high": round(first["breakout_high"], 3) if first else None,
        "pullback_days": pullback.get("days") if pullback else None,
        "pullback_pct": round(pullback["pullback_pct"], 3) if pullback and "pullback_pct" in pullback else None,
        "pullback_ok": bool(pullback.get("ok")) if pullback else False,
        "second_ok": bool(second.get("ok")) if second else False,
        "second_day_pct": round(second["day_pct"], 3) if second else None,
        "dist_ma20_pct": round((cT / ma["ma20"] - 1) * 100, 3) if ma["ma20"] else None,
    }
    res.flags = {
        "full_structure": full_structure,
        "main_rise_candidate": bool(second and second.get("main_rise_candidate")),
        "healthy_pullback": bool(pullback and pullback.get("ok")),
        "approaching_second": bool(
            first
            and pullback
            and pullback.get("ok")
            and cT >= first["breakout_high"] * 0.98
            and cT <= first["breakout_high"] * 1.01
        ),
    }
    if grade == "DROP" and not res.drop and not res.overheated:
        res.drop = True
        res.drop_reason = res.drop_reason or "score_below_55"
    if res.overheated:
        res.drop = False
        res.grade = "OH"
        res.phase = "OVERHEATED"
        res.phase_cn = PHASE_CN["OVERHEATED"]
        res.pushable_core = False
        res.ok = True  # keep visible in report boards
    return res


def map_regime_v2(market_score: float, up_share: float, avg_idx_pct: float, vs_ma5: float) -> str:
    """STRONG/NORMAL/WEAK/RISK_OFF from indices+breadth+turnover proxies."""
    if market_score < 30 or (up_share < 0.30 and avg_idx_pct < 0) or (avg_idx_pct < -1.5 and vs_ma5 < 0.85):
        return "RISK_OFF"
    if market_score < 45 or up_share < 0.40:
        return "WEAK"
    if market_score >= 75 and up_share >= 0.55:
        return "STRONG"
    return "NORMAL"


def apply_regime_gates(
    grade: Optional[str],
    score: float,
    regime: str,
    full_structure: bool,
    overheated: bool = False,
) -> Tuple[Optional[str], bool, str]:
    """
    Returns (effective_grade, pushable, note).
    NORMAL: S bar +5 (score threshold for S becomes 80 if full structure)
    WEAK: only ≥85
    RISK_OFF: observation only, no ordinary push
    STRONG: standard grades
    OH / overheated: never pushable as core S/A.
    """
    if overheated or grade == "OH":
        return "OH", False, "OVERHEATED: observe only, not core"
    if regime == "RISK_OFF":
        return grade, False, "RISK_OFF: observation only"
    if regime == "WEAK":
        if score >= 85 and full_structure:
            return "S" if score >= 85 else grade, True, "WEAK: only ≥85"
        return grade, False, "WEAK: below 85, no push"
    if regime == "NORMAL":
        # S bar +5 → S if score>=80 and full structure (easier S bar)
        if full_structure and score >= 80:
            return "S", True, "NORMAL: S bar +5"
        if grade in ("S", "A"):
            return grade, True, "NORMAL"
        return grade, False, "NORMAL: non S/A observe"
    # STRONG
    if grade in ("S", "A"):
        return grade, True, "STRONG"
    return grade, False, "STRONG: B/C observe"


__all__ = [
    "PHASE_CN",
    "StructureResult",
    "analyze_hist",
    "detect_ma_structure",
    "map_regime_v2",
    "apply_regime_gates",
    "score_structure",
    "scan_kjs_history",
]
