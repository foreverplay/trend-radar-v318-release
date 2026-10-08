"""
HigherTimeframeEngine — V3.1 monthly (HTF) trend label, no lookahead.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from v3.v31.config import V31_CONFIG


@dataclass
class HigherTimeframeResult:
    ok: bool = False
    monthly_label: str = "NEUTRAL"  # STRONG_UP|UP|NEUTRAL|DOWN
    htf_ok: bool = False
    monthly_return_3: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HigherTimeframeEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or V31_CONFIG

    def evaluate(
        self,
        hist: Optional[pd.DataFrame],
        *,
        t_date: Optional[str] = None,
        monthly_hint: Optional[str] = None,
    ) -> HigherTimeframeResult:
        # Prefer explicit monthly hint from upstream enrich (already as-of T)
        if monthly_hint:
            lab = _norm_label(monthly_hint)
            ok_labs = set((self.config.get("htf") or {}).get("ok_labels") or ())
            htf_ok = lab in ok_labs or lab in ("UP", "STRONG_UP")
            return HigherTimeframeResult(
                ok=True,
                monthly_label=lab,
                htf_ok=htf_ok,
                tags=["HINT"],
                notes=[f"monthly_hint={monthly_hint}→{lab}"],
            )

        if hist is None or not isinstance(hist, pd.DataFrame) or len(hist) < 60:
            return HigherTimeframeResult(ok=False, notes=["hist insufficient for HTF"])

        h = hist.copy()
        if "date" in h.columns:
            h["date"] = pd.to_datetime(h["date"])
            h = h.sort_values("date")
            if t_date:
                h = h[h["date"] <= pd.Timestamp(t_date)]
        h = h.reset_index(drop=True)
        if len(h) < 60:
            return HigherTimeframeResult(ok=False, notes=["hist <60 after truncate"])

        close = h["close"].astype(float)
        # Month-end proxy: every ~21 bars
        step = 21
        if len(close) < step * 3:
            ret3 = float(close.iloc[-1] / close.iloc[0] - 1.0) * 100.0
        else:
            c0 = float(close.iloc[-1 - 3 * step])
            ret3 = float(close.iloc[-1] / c0 - 1.0) * 100.0 if c0 > 0 else 0.0

        ma60 = float(close.rolling(60).mean().iloc[-1])
        last = float(close.iloc[-1])
        slope60 = float(ma60 / float(close.rolling(60).mean().iloc[-11]) - 1.0) * 100.0

        if ret3 >= 15 and last >= ma60 and slope60 > 0:
            lab = "STRONG_UP"
        elif ret3 >= 5 and last >= ma60 * 0.98:
            lab = "UP"
        elif ret3 <= -10 or last < ma60 * 0.92:
            lab = "DOWN"
        else:
            lab = "NEUTRAL"

        ok_labs = set((self.config.get("htf") or {}).get("ok_labels") or ())
        htf_ok = lab in ok_labs or lab in ("UP", "STRONG_UP")
        return HigherTimeframeResult(
            ok=True,
            monthly_label=lab,
            htf_ok=htf_ok,
            monthly_return_3=round(ret3, 2),
            tags=[],
            notes=[f"ret3={ret3:.1f}", f"slope60={slope60:.2f}"],
        )


def _norm_label(s: str) -> str:
    x = str(s or "").strip().upper()
    aliases = {
        "MONTHLY_STRONG_UP": "STRONG_UP",
        "MONTHLY_UP": "UP",
        "MONTHLY_NEUTRAL": "NEUTRAL",
        "MONTHLY_DOWN": "DOWN",
        "STRONG": "STRONG_UP",
    }
    return aliases.get(x, x)


__all__ = ["HigherTimeframeEngine", "HigherTimeframeResult"]
