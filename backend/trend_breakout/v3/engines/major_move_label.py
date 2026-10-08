"""
V3.1.5 MajorMoveLabelEngine — backtest labels ONLY.

May use future bars inside ForwardWindow. Must NEVER write labels back into
live EntrySignal / TrendGrade / EntryWindow / pool features.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from v3.v31.config import get_v315_label
from v3.v31.types import MajorMoveType, MoveBucket, SignalEvaluation


def _peak_to_end_giveback(entry: float, peak: float, end: float) -> float:
    denom = peak - entry
    if denom <= 1e-12:
        return 0.0
    return float((peak - end) / denom)


class MajorMoveLabelEngine:
    """Evaluate forward outcomes for EntrySignals (label-only)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = config or get_v315_label()
        self.windows: Tuple[int, ...] = tuple(self.cfg.get("FORWARD_WINDOWS") or (20, 40, 60, 120))
        self.hits: Dict[int, float] = {
            int(k): float(v) for k, v in (self.cfg.get("HIT_THRESHOLDS") or {}).items()
        }
        self.major_min = float(self.cfg.get("MAJOR_MIN_PEAK_GAIN_PCT", 0.30))
        self.spike_gb = float(self.cfg.get("SPIKE_GIVEBACK_MIN", 0.50))
        self.small_max = float(self.cfg.get("SMALL_MOVE_MAX", 0.10))
        self.medium_max = float(self.cfg.get("MEDIUM_MOVE_MAX", 0.30))

    def evaluate_signal(
        self,
        *,
        symbol: str,
        signal_date: str,
        entry_price: float,
        hist: pd.DataFrame,
        entry_window: Optional[str] = None,
        rule: str = "current",
        trend_id: int = 0,
        wave_id: int = 0,
        end_date: Optional[str] = None,
    ) -> SignalEvaluation:
        """
        Forward evaluation from signal_date using bars AFTER signal (and including
        signal day high only for same-day extension — peak from next bars preferred).

        Peak / max returns use highs from signal day forward (inclusive of signal day
        high relative to entry close is allowed for intraday extension; primary path
        uses trading days after signal for forward windows).
        """
        notes: List[str] = []
        if entry_price is None or entry_price <= 0:
            return SignalEvaluation(
                symbol=str(symbol).zfill(6),
                signalDate=signal_date,
                entryPrice=float(entry_price or 0.0),
                majorType=MajorMoveType.NON_MAJOR.value,
                moveBucket=MoveBucket.FAIL.value,
                rule=rule,
                notes=["invalid_entry_price"],
            )

        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values("date").reset_index(drop=True)
        sig_ts = pd.Timestamp(signal_date)
        # forward bars: strictly after signal date (entry already at signal close)
        fwd = h[h["date"] > sig_ts].reset_index(drop=True)
        if end_date:
            fwd = fwd[fwd["date"] <= pd.Timestamp(end_date)].reset_index(drop=True)

        max_w = max(self.windows)
        if len(fwd) == 0:
            return SignalEvaluation(
                symbol=str(symbol).zfill(6),
                signalDate=signal_date,
                entryPrice=float(entry_price),
                majorType=MajorMoveType.ONGOING.value,
                moveBucket=MoveBucket.FAIL.value,
                entryWindow=entry_window,
                rule=rule,
                trendId=trend_id,
                waveId=wave_id,
                notes=["no_forward_bars"],
            )

        # Use up to max window for peak/terminal; terminal = last close within available / max_w
        use = fwd.iloc[: min(len(fwd), max_w)]
        highs = use["high"].astype(float) if "high" in use.columns else use["close"].astype(float)
        closes = use["close"].astype(float)
        lows = use["low"].astype(float) if "low" in use.columns else closes

        peak_px = float(highs.max())
        peak_i = int(highs.values.argmax())
        peak_date = str(use.iloc[peak_i]["date"].date())
        end_px = float(closes.iloc[-1])
        peak_gain = peak_px / float(entry_price) - 1.0
        terminal = end_px / float(entry_price) - 1.0
        giveback = _peak_to_end_giveback(float(entry_price), peak_px, end_px)

        def _max_ret(n: int) -> Optional[float]:
            if len(fwd) < 1:
                return None
            sl = fwd.iloc[: min(len(fwd), n)]
            hx = sl["high"].astype(float) if "high" in sl.columns else sl["close"].astype(float)
            return float(hx.max() / float(entry_price) - 1.0)

        def _max_dd(n: int) -> Optional[float]:
            """Max drawdown from entry using closes in first n forward days (negative or 0)."""
            if len(fwd) < 1:
                return None
            sl = fwd.iloc[: min(len(fwd), n)]
            lx = sl["low"].astype(float) if "low" in sl.columns else sl["close"].astype(float)
            min_low = float(lx.min())
            return float(min_low / float(entry_price) - 1.0)

        f20 = _max_ret(20)
        f40 = _max_ret(40)
        f60 = _max_ret(60)
        f120 = _max_ret(120)
        dd20 = _max_dd(20)
        dd40 = _max_dd(40)
        dd60 = _max_dd(60)

        hit20 = bool(f20 is not None and f20 >= float(self.hits.get(20, 0.15)))
        hit40 = bool(f40 is not None and f40 >= float(self.hits.get(40, 0.30)))
        hit60 = bool(f60 is not None and f60 >= float(self.hits.get(60, 0.30)))
        hit120 = bool(f120 is not None and f120 >= float(self.hits.get(120, 0.30)))
        any_hit = hit20 or hit40 or hit60 or hit120

        # Incomplete forward window for full 120d classification
        ongoing = len(fwd) < 20 and peak_gain < self.major_min

        if peak_gain < self.small_max:
            bucket = MoveBucket.SMALL_MOVE.value
        elif peak_gain < self.medium_max:
            bucket = MoveBucket.MEDIUM_MOVE.value
        else:
            bucket = MoveBucket.MAJOR_MOVE.value

        if ongoing:
            mtype = MajorMoveType.ONGOING.value
            major_hit = False
            notes.append("ONGOING_incomplete_forward")
        elif peak_gain >= self.major_min:
            major_hit = True
            if terminal > 0 and any_hit and giveback < self.spike_gb:
                mtype = MajorMoveType.SUSTAINED_MAJOR.value
            else:
                mtype = MajorMoveType.SPIKE_MAJOR.value
                if giveback >= self.spike_gb:
                    notes.append(f"SPIKE_giveback={giveback:.2f}")
                if terminal <= 0:
                    notes.append(f"SPIKE_terminal={terminal:.4f}")
                if not any_hit:
                    notes.append("SPIKE_no_forward_hit")
        else:
            mtype = MajorMoveType.NON_MAJOR.value
            major_hit = False

        # Result tag for Table 3
        if mtype == MajorMoveType.SUSTAINED_MAJOR.value:
            result = "MAJOR"
        elif mtype == MajorMoveType.SPIKE_MAJOR.value:
            result = "MAJOR"  # peak touched; type distinguishes spike
            notes.append("result_MAJOR_but_SPIKE")
        elif bucket == MoveBucket.MEDIUM_MOVE.value:
            result = "MEDIUM"
        elif bucket == MoveBucket.SMALL_MOVE.value:
            result = "SMALL"
        else:
            result = "FAIL"
        notes.append(f"result={result}")

        return SignalEvaluation(
            symbol=str(symbol).zfill(6),
            signalDate=signal_date,
            entryPrice=float(entry_price),
            forward20dMaxReturn=None if f20 is None else round(f20, 6),
            forward40dMaxReturn=None if f40 is None else round(f40, 6),
            forward60dMaxReturn=None if f60 is None else round(f60, 6),
            forward120dMaxReturn=None if f120 is None else round(f120, 6),
            maxDrawdown20=None if dd20 is None else round(dd20, 6),
            maxDrawdown40=None if dd40 is None else round(dd40, 6),
            maxDrawdown60=None if dd60 is None else round(dd60, 6),
            majorHit=bool(major_hit),
            majorType=mtype,
            peakGain=round(peak_gain, 6),
            terminalReturn=round(terminal, 6),
            peakDate=peak_date,
            peakToEndGiveback=round(giveback, 6),
            moveBucket=bucket,
            entryWindow=entry_window,
            rule=rule,
            trendId=int(trend_id or 0),
            waveId=int(wave_id or 0),
            hit20=hit20,
            hit40=hit40,
            hit60=hit60,
            hit120=hit120,
            lookaheadError=False,
            notes=notes,
        )

    def label_historical_major(
        self,
        *,
        anchor_date: str,
        anchor_price: float,
        hist: pd.DataFrame,
        promote_or_label_date: str,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Identify a historical true Major for recall (PeakGain≥30% Age≥20 as-of label date).
        Label-only helper — not an EntrySignal.
        """
        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values("date")
        a = pd.Timestamp(anchor_date)
        lab = pd.Timestamp(promote_or_label_date)
        seg = h[(h["date"] >= a) & (h["date"] <= lab)]
        if len(seg) == 0 or anchor_price <= 0:
            return {"is_major": False}
        peak = float(seg["high"].max()) if "high" in seg.columns else float(seg["close"].max())
        peak_gain = peak / float(anchor_price) - 1.0
        age = int(len(seg))
        is_major = peak_gain >= self.major_min and age >= int(self.cfg.get("MAJOR_MIN_AGE_DAYS", 20))
        return {
            "is_major": bool(is_major),
            "peak_gain": peak_gain,
            "age": age,
            "label_date": promote_or_label_date,
            "anchor_date": anchor_date,
            "anchor_price": anchor_price,
            "end_date": end_date,
        }


__all__ = ["MajorMoveLabelEngine"]
