#!/usr/bin/env python3
"""
V2.1 RiskEngine — OHLCV-only trading risk tags.

Signals:
  HIGH_VOLUME_WEAK_CLOSE (VR≥2 & closePos≤0.3; escalate if ≥2 times)
  FREQUENT_UPPER_SHADOW (≥3 in 10d); + HIGH_LEVEL_DISTRIBUTION_RISK if ret20>30%
  HIGH_LEVEL_VOLUME / HIGH_LEVEL_WEAKNESS
  TREND_FAILURE / BROKEN (close<MA20 & <platform low)

RiskScore: LOW / MID / HIGH
No buy advice.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def _f(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except Exception:
        return default


@dataclass
class RiskResult:
    risk_score: str = "MID"  # LOW / MID / HIGH
    tags: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    checklist: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    broken: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RiskEngine:
    def evaluate(
        self,
        hist: pd.DataFrame,
        platform_low: Optional[float] = None,
        structure_risks: Optional[List[str]] = None,
    ) -> RiskResult:
        res = RiskResult()
        tags: List[str] = []
        notes: List[str] = []

        if hist is None or len(hist) < 25:
            res.risk_score = "MID"
            res.tags = ["HIST_SHORT"]
            res.checklist = {"交易风险": "⚠"}
            res.notes = ["K线不足，风险中性"]
            return res

        df = hist.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

        o = df["open"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        v = df["volume"].astype(float) if "volume" in df.columns else pd.Series(np.nan, index=df.index)

        ma20 = c.rolling(20).mean()
        vol_ma20 = v.rolling(20).mean()

        # close position in day range
        rng = (h - l).replace(0, np.nan)
        close_pos = (c - l) / rng

        vr = v / vol_ma20.replace(0, np.nan)

        # HIGH_VOLUME_WEAK_CLOSE in last 10 sessions
        look = 10
        hvwc_idx = []
        for i in range(max(20, len(df) - look), len(df)):
            if not np.isfinite(vr.iloc[i]) or not np.isfinite(close_pos.iloc[i]):
                continue
            if float(vr.iloc[i]) >= 2.0 and float(close_pos.iloc[i]) <= 0.3:
                hvwc_idx.append(i)
        hvwc_n = len(hvwc_idx)
        if hvwc_n >= 1:
            tags.append("HIGH_VOLUME_WEAK_CLOSE")
            notes.append(f"近{look}日放量弱收×{hvwc_n}")
        if hvwc_n >= 2:
            tags.append("HIGH_VOLUME_WEAK_CLOSE_ESCALATED")

        # upper shadow: high - max(open,close) >= 0.4 * range
        upper_n = 0
        for i in range(len(df) - look, len(df)):
            if i < 0:
                continue
            hi, lo, op, cl = float(h.iloc[i]), float(l.iloc[i]), float(o.iloc[i]), float(c.iloc[i])
            day_rng = hi - lo
            if day_rng <= 0:
                continue
            upper = hi - max(op, cl)
            if upper / day_rng >= 0.40:
                upper_n += 1
        if upper_n >= 3:
            tags.append("FREQUENT_UPPER_SHADOW")
            notes.append(f"近10日上影≥40%区间共{upper_n}次")

        ret20 = None
        if len(c) >= 21 and float(c.iloc[-21]) != 0:
            ret20 = (float(c.iloc[-1]) / float(c.iloc[-21]) - 1.0) * 100.0
        ret5 = None
        if len(c) >= 6 and float(c.iloc[-6]) != 0:
            ret5 = (float(c.iloc[-1]) / float(c.iloc[-6]) - 1.0) * 100.0

        if "FREQUENT_UPPER_SHADOW" in tags and ret20 is not None and ret20 > 30:
            tags.append("HIGH_LEVEL_DISTRIBUTION_RISK")

        # HIGH_LEVEL_VOLUME / WEAKNESS
        last_vr = float(vr.iloc[-1]) if np.isfinite(vr.iloc[-1]) else float("nan")
        last_cp = float(close_pos.iloc[-1]) if np.isfinite(close_pos.iloc[-1]) else float("nan")
        dist_ma20 = None
        if np.isfinite(ma20.iloc[-1]) and float(ma20.iloc[-1]) != 0:
            dist_ma20 = (float(c.iloc[-1]) / float(ma20.iloc[-1]) - 1.0) * 100.0

        if ret20 is not None and ret20 > 25 and np.isfinite(last_vr) and last_vr >= 2.0:
            tags.append("HIGH_LEVEL_VOLUME")
        if ret20 is not None and ret20 > 20 and np.isfinite(last_cp) and last_cp <= 0.35:
            tags.append("HIGH_LEVEL_WEAKNESS")
        if dist_ma20 is not None and dist_ma20 > 25 and np.isfinite(last_vr) and last_vr >= 1.8 and last_cp <= 0.4:
            if "HIGH_LEVEL_WEAKNESS" not in tags:
                tags.append("HIGH_LEVEL_WEAKNESS")

        # TREND_FAILURE / BROKEN
        last_c = float(c.iloc[-1])
        last_ma20 = float(ma20.iloc[-1]) if np.isfinite(ma20.iloc[-1]) else float("nan")
        broken = False
        if np.isfinite(last_ma20) and last_c < last_ma20:
            pl = platform_low
            if pl is None:
                # proxy: 20d low of closes
                pl = float(c.tail(20).min())
            if last_c < float(pl):
                broken = True
                tags.append("BROKEN")
                tags.append("TREND_FAILURE")
                notes.append("收盘跌破MA20且低于平台/近低")
            elif np.isfinite(last_vr) and last_vr > 1.5:
                tags.append("TREND_FAILURE")
                notes.append("收盘<MA20且放量")

        # merge structure risks hints
        for t in structure_risks or []:
            if t in ("TREND_FAILURE", "OVERHEATED") and t not in tags:
                if t == "TREND_FAILURE":
                    tags.append("TREND_FAILURE")

        # Risk score
        high_hits = {
            "HIGH_VOLUME_WEAK_CLOSE_ESCALATED",
            "HIGH_LEVEL_DISTRIBUTION_RISK",
            "BROKEN",
            "HIGH_LEVEL_WEAKNESS",
        }
        mid_hits = {
            "HIGH_VOLUME_WEAK_CLOSE",
            "FREQUENT_UPPER_SHADOW",
            "HIGH_LEVEL_VOLUME",
            "TREND_FAILURE",
        }
        hi_n = sum(1 for t in tags if t in high_hits)
        mid_n = sum(1 for t in tags if t in mid_hits)

        if broken or hi_n >= 1 or mid_n >= 3:
            risk_score = "HIGH"
        elif mid_n >= 1 or hi_n >= 1:
            risk_score = "MID"
        else:
            risk_score = "LOW"

        # escalate OH-like: ret5 very hot + weak close
        if ret5 is not None and ret5 > 35 and ("HIGH_VOLUME_WEAK_CLOSE" in tags or "FREQUENT_UPPER_SHADOW" in tags):
            risk_score = "HIGH"
            if "HIGH_LEVEL_DISTRIBUTION_RISK" not in tags:
                tags.append("HIGH_LEVEL_DISTRIBUTION_RISK")

        checklist = {"交易风险": "✓" if risk_score == "LOW" else "⚠"}

        res.risk_score = risk_score
        res.tags = tags
        res.broken = broken
        res.notes = notes
        res.checklist = checklist
        res.metrics = {
            "hvwc_count_10d": hvwc_n,
            "upper_shadow_10d": upper_n,
            "return5_pct": None if ret5 is None else round(ret5, 3),
            "return20_pct": None if ret20 is None else round(ret20, 3),
            "last_vr": None if not np.isfinite(last_vr) else round(last_vr, 3),
            "last_close_pos": None if not np.isfinite(last_cp) else round(last_cp, 3),
            "dist_ma20_pct": None if dist_ma20 is None else round(dist_ma20, 3),
            "platform_low_used": platform_low,
        }
        return res


__all__ = ["RiskEngine", "RiskResult"]
