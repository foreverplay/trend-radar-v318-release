"""
ExhaustionEngine — ExhaustionRisk (≠ TREND_FAILURE).

V3.1.1 levels: NORMAL | ELEVATED | HIGH.
Annotates overheat / extension / MajorWaveConsumption (V3.1.3) / price-efficiency.
Does NOT force grade≤WATCH or TREND_FAILURE by itself.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from v3.v31.config import V31_CONFIG
from v3.v31.types import ExhaustionLevel, WaveInfo


@dataclass
class ExhaustionResult:
    ok: bool = True
    level: ExhaustionLevel = ExhaustionLevel.NORMAL
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["level"] = (
            ExhaustionLevel.coerce(self.level).value
            if self.level is not None
            else ExhaustionLevel.NORMAL.value
        )
        return d


class ExhaustionEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or V31_CONFIG
        self.cfg = dict(self.config.get("exhaustion") or {})

    def evaluate(
        self,
        *,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
        wave: Optional[WaveInfo] = None,
        session_consumption: Optional[float] = None,
        overheated: bool = False,
        lifecycle: Optional[str] = None,
    ) -> ExhaustionResult:
        tags: List[str] = []
        notes: List[str] = []
        metrics: Dict[str, Any] = {}
        level = ExhaustionLevel.NORMAL

        cons = None
        # V3.1.3: Exhaustion refs MajorWaveConsumption by default
        if wave is not None and getattr(wave, "major_wave_consumption", None) is not None:
            cons = float(wave.major_wave_consumption)
            metrics["major_wave_consumption"] = cons
            metrics["wave_consumption"] = cons
        elif wave is not None and wave.wave_consumption is not None:
            cons = float(wave.wave_consumption)
            metrics["wave_consumption"] = cons
        elif session_consumption is not None:
            cons = float(session_consumption)
            metrics["session_consumption"] = cons

        ext = None
        limit_ups_5 = 0
        price_eff = None
        if hist is not None and isinstance(hist, pd.DataFrame) and len(hist) >= 20:
            h = hist.copy()
            if "date" in h.columns:
                h["date"] = pd.to_datetime(h["date"])
                h = h.sort_values("date")
                if t_date:
                    h = h[h["date"] <= pd.Timestamp(t_date)]
            h = h.reset_index(drop=True)
            if len(h) >= 20:
                c = h["close"].astype(float)
                ma20 = float(c.rolling(20).mean().iloc[-1])
                last = float(c.iloc[-1])
                if ma20 > 0:
                    ext = (last / ma20 - 1.0) * 100.0
                    metrics["ext_ma20_pct"] = round(ext, 2)
                rets = c.pct_change() * 100.0
                limit_ups_5 = int((rets.tail(5) >= 9.5).sum())
                metrics["limit_ups_5d"] = limit_ups_5

                # Price-efficiency (5d): net gain / gross range; low = wasteful churn
                tail = h.tail(5)
                if len(tail) >= 3:
                    net = float(tail["close"].iloc[-1]) / float(tail["close"].iloc[0]) - 1.0
                    hi = float(tail["high"].astype(float).max())
                    lo = float(tail["low"].astype(float).min())
                    span = (hi / lo - 1.0) if lo > 0 else 0.0
                    if span > 1e-6:
                        price_eff = max(0.0, net) / span
                    else:
                        price_eff = 1.0 if net >= 0 else 0.0
                    # upper-wick waste on last bar
                    row = tail.iloc[-1]
                    o, hi_b, lo_b, cl = (
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                    )
                    rng = hi_b - lo_b
                    if rng > 0:
                        upper = (hi_b - max(o, cl)) / rng
                        metrics["upper_wick_frac"] = round(upper, 3)
                        if upper >= 0.55 and (cl / o - 1.0) * 100 < 2.0:
                            tags.append("WICK_WASTE")
                    metrics["price_efficiency_5d"] = round(float(price_eff), 3)

        elev_c = float(self.cfg.get("elevated_consumption", self.cfg.get("watch_consumption", 70)))
        high_c = float(self.cfg.get("high_consumption", 85))
        high_lu = int(self.cfg.get("high_limit_up_5d", 3))
        elev_ext = float(
            self.cfg.get("elevated_ext_ma20_pct", self.cfg.get("watch_ext_ma20_pct", 15))
        )
        high_ext = float(self.cfg.get("high_ext_ma20_pct", 25))
        eff_elev = float(self.cfg.get("efficiency_elevated", 0.35))
        eff_high = float(self.cfg.get("efficiency_high", 0.20))

        score = 0
        if cons is not None:
            if cons >= high_c:
                score += 2
                tags.append("CONS_HIGH")
            elif cons >= elev_c:
                score += 1
                tags.append("CONS_ELEVATED")
        if ext is not None:
            if ext >= high_ext:
                score += 2
                tags.append("EXT_HIGH")
            elif ext >= elev_ext:
                score += 1
                tags.append("EXT_ELEVATED")
        if limit_ups_5 >= high_lu:
            score += 2
            tags.append("LIMIT_UP_CLUSTER")
        elif limit_ups_5 >= 2:
            score += 1
            tags.append("LIMIT_UP_2")

        if price_eff is not None and ext is not None and ext >= elev_ext:
            if price_eff <= eff_high:
                score += 2
                tags.append("EFF_POOR")
            elif price_eff <= eff_elev:
                score += 1
                tags.append("EFF_WEAK")

        if overheated or (lifecycle or "").upper() == "OVERHEATED":
            score += 1
            tags.append("OH_FLAG")
            notes.append("overheated flag feeds ExhaustionRisk only (≠ TREND_FAILURE)")

        if score >= 3:
            level = ExhaustionLevel.HIGH
        elif score >= 1:
            level = ExhaustionLevel.ELEVATED

        notes.append(f"exhaustion_score={score}→{level.value}")
        return ExhaustionResult(
            ok=True,
            level=level,
            tags=tags,
            notes=notes,
            metrics=metrics,
        )


__all__ = ["ExhaustionEngine", "ExhaustionResult"]
