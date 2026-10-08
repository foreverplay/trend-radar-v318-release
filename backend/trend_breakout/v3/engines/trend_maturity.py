"""
V3.1.6 / V3.1.7 TrendMaturityEngine — structural maturity (detection ≠ entry).

V3.1.7 adds RealizedTrend gates, FollowThrough, MainRiseQuality, new score weights.

Score 0–100 with weights:
  结构成熟度 30, 突破确认度 25, 趋势持续度 20, 量价确认 15, 高周期确认 10.

Levels: EARLY | BUILDING | MATURE | ADVANCED.
NOT calendar age alone; NOT TechnicalScore alone.
Tracks StructuralConfirmation: breakout → healthy pullback → HL → rebreakout → hold.
Also MatureContinuation: established trend + contraction + volume rebreak (generic).

No symbol/date strategy branches. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from v3.v31.config import get_v316_maturity, get_v317_maturity
from v3.v31.types import (
    BreakoutFollowThrough,
    FollowThroughLevel,
    MainRiseQuality,
    RealizedTrendResult,
    SignalMaturity,
    StructuralConfirmation,
    TrendMaturityLevel,
    TrendState,
)


_BREAKOUT_PHASES = frozenset(
    {
        "FIRST_BREAKOUT",
        "SECOND_BREAKOUT",
        "MAIN_RISE",
        "MAIN_RISE_ACCEL",
        "ACCELERATION",
    }
)
_PULLBACK_PHASES = frozenset(
    {
        "HEALTHY_PULLBACK",
        "PLATFORM_HOLD",
        "PLATFORM",
    }
)
_CONFIRMED_FAMILY = frozenset(
    {
        TrendState.CONFIRMED.value,
        TrendState.MAIN_RISE.value,
        TrendState.ACCELERATION.value,
        TrendState.NEW_WAVE.value,
        TrendState.COOLING.value,
    }
)
_MATURE_STATES = frozenset(
    {
        TrendState.MAIN_RISE.value,
        TrendState.ACCELERATION.value,
        TrendState.NEW_WAVE.value,
    }
)


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass
class _TrendTrack:
    """Per-trend_id structural confirmation tracker (as-of only)."""

    trend_id: int = 0
    first_breakout: bool = False
    first_breakout_date: Optional[str] = None
    first_breakout_px: Optional[float] = None
    healthy_pullback: bool = False
    pullback_date: Optional[str] = None
    pullback_low: Optional[float] = None
    higher_low: bool = False
    prior_swing_low: Optional[float] = None
    re_breakout: bool = False
    re_breakout_date: Optional[str] = None
    re_breakout_px: Optional[float] = None
    hold_days: int = 0
    breakout_hold: bool = False
    # MatureContinuation path
    contraction_seen: bool = False
    contraction_date: Optional[str] = None
    mature_continuation: bool = False
    days_in_confirmed: int = 0
    peak_close: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return bool(
            self.first_breakout
            and self.healthy_pullback
            and self.higher_low
            and self.re_breakout
        )


class TrendMaturityEngine:
    """
    Stateful day-step maturity scorer.

    Call ``reset()`` per symbol; ``step(...)`` each as-of day.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.cfg = dict(config or get_v316_maturity())
        self.version = str(self.cfg.get("version") or "3.1.6")
        self._tracks: Dict[int, _TrendTrack] = {}
        self._active_tid: int = 0
        self._breakout_events: Dict[int, List[Dict[str, Any]]] = {}  # tid -> breakout marks
        self._mature_since: Dict[int, str] = {}  # tid -> first MATURE date

    def reset(self) -> None:
        self._tracks = {}
        self._active_tid = 0
        self._breakout_events = {}
        self._mature_since = {}

    def step(
        self,
        *,
        t_date: str,
        trend_id: int,
        trend_state: Union[str, TrendState, None],
        lifecycle: Optional[str] = None,
        close: Optional[float] = None,
        low: Optional[float] = None,
        high: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        ma20_slope_neg: bool = False,
        higher_low_broken: bool = False,
        htf_ok: bool = False,
        peak_gain_from_anchor: Optional[float] = None,
        wave_age_days: int = 0,
        session_consumption: Optional[float] = None,
        ext_ma20_pct: Optional[float] = None,
        broken: bool = False,
        # V3.1.7
        realized: Optional[RealizedTrendResult] = None,
        trend_age_days: Optional[int] = None,
        vol_ratio: Optional[float] = None,
        just_breakout_today: bool = False,
    ) -> SignalMaturity:
        state = (
            trend_state.value
            if isinstance(trend_state, TrendState)
            else str(trend_state or TrendState.NONE.value).upper()
        )
        life = str(lifecycle or "").upper()
        m = dict(metrics or {})
        reasons: List[str] = []

        if broken or state == TrendState.TREND_FAILURE.value:
            self._active_tid = 0
            return SignalMaturity(
                score=0.0,
                level=TrendMaturityLevel.EARLY.value,
                reasons=["trend_failure/broken → EARLY"],
                structural=StructuralConfirmation(notes=["reset on failure"]),
            )

        tid = int(trend_id or 0)
        if tid <= 0 or state not in _CONFIRMED_FAMILY:
            return SignalMaturity(
                score=0.0,
                level=TrendMaturityLevel.EARLY.value,
                reasons=["no confirmed trend"],
                structural=StructuralConfirmation(),
            )

        if tid != self._active_tid:
            # new trend id → fresh tracker
            self._active_tid = tid
            if tid not in self._tracks:
                self._tracks[tid] = _TrendTrack(trend_id=tid)

        tr = self._tracks[tid]
        tr.days_in_confirmed += 1
        c = _f(close)
        lo = _f(low)
        if c is not None:
            tr.peak_close = max(float(tr.peak_close or c), c)

        # ---- StructuralConfirmation state machine ----
        self._update_structure(
            tr,
            t_date=t_date,
            state=state,
            life=life,
            close=c,
            low=lo,
            metrics=m,
            ma20_slope_neg=ma20_slope_neg,
            higher_low_broken=higher_low_broken,
            reasons=reasons,
        )

        # ---- Component scores ----
        s_struct = self._score_structure(tr, m, ma20_slope_neg, higher_low_broken)
        s_break = self._score_breakout(tr, m, life)
        s_cont = self._score_continuation(tr, state, peak_gain_from_anchor, wave_age_days)
        s_vp = self._score_volume_price(tr, m, life)
        s_htf = 100.0 if htf_ok else 25.0

        w_s = float(self.cfg.get("weight_structure", 30))
        w_b = float(self.cfg.get("weight_breakout", 25))
        w_c = float(self.cfg.get("weight_continuation", 20))
        w_v = float(self.cfg.get("weight_volume_price", 15))
        w_h = float(self.cfg.get("weight_htf", 10))
        total_w = w_s + w_b + w_c + w_v + w_h
        score = _clamp(
            (s_struct * w_s + s_break * w_b + s_cont * w_c + s_vp * w_v + s_htf * w_h)
            / total_w
        )

        # MAIN_RISE_CONFIRMED boost → ensure MATURE eligibility when checklist met
        main_rise_confirmed = self._main_rise_confirmed(
            state=state, m=m, ma20_slope_neg=ma20_slope_neg, higher_low_broken=higher_low_broken, tr=tr
        )
        if main_rise_confirmed and (tr.completed or tr.mature_continuation):
            score = max(score, float(self.cfg.get("level_mature_min", 65)))
            reasons.append("MAIN_RISE_CONFIRMED + structure/continuation → MATURE floor")

        level = self._level_from_score(score)

        # Clamp: CONFIRMED detection cannot be MATURE/ADVANCED
        max_for_confirmed = str(self.cfg.get("confirmed_max_level") or "BUILDING")
        if state == TrendState.CONFIRMED.value:
            order = [
                TrendMaturityLevel.EARLY.value,
                TrendMaturityLevel.BUILDING.value,
                TrendMaturityLevel.MATURE.value,
                TrendMaturityLevel.ADVANCED.value,
            ]
            if order.index(level) > order.index(max_for_confirmed):
                level = max_for_confirmed
                # also cap score just below mature threshold for honesty
                mature_min = float(self.cfg.get("level_mature_min", 65))
                score = min(score, mature_min - 0.1)
                reasons.append(
                    f"CONFIRMED clamp → max {max_for_confirmed} (detection ≠ entry)"
                )

        # COOLING inherits prior maturity but cannot be ADVANCED fresh entry
        if state == TrendState.COOLING.value and level == TrendMaturityLevel.ADVANCED.value:
            level = TrendMaturityLevel.MATURE.value

        reasons.append(
            f"score={score:.1f} struct={s_struct:.0f} break={s_break:.0f} "
            f"cont={s_cont:.0f} vp={s_vp:.0f} htf={s_htf:.0f}"
        )
        if tr.completed:
            reasons.append("StructuralConfirmation.completed")
        if tr.mature_continuation:
            reasons.append("MatureContinuation")

        structural = StructuralConfirmation(
            firstBreakout=tr.first_breakout,
            healthyPullback=tr.healthy_pullback,
            higherLow=tr.higher_low,
            reBreakout=tr.re_breakout,
            breakoutHold=tr.breakout_hold,
            completed=tr.completed,
            matureContinuation=tr.mature_continuation,
            firstBreakoutDate=tr.first_breakout_date,
            pullbackDate=tr.pullback_date,
            reBreakoutDate=tr.re_breakout_date,
            holdDays=tr.hold_days,
            notes=list(tr.notes[-8:]),
        )

        sm = SignalMaturity(
            score=round(score, 2),
            level=level,
            structure=round(s_struct, 2),
            breakout=round(s_break, 2),
            continuation=round(s_cont, 2),
            volumePrice=round(s_vp, 2),
            htf=round(s_htf, 2),
            reasons=reasons,
            structural=structural,
        )
        if str(self.version).startswith("3.1.7"):
            # tr may be undefined if early-returned; only overlay when we have a track
            tr_ref = self._tracks.get(tid) if tid > 0 else None
            sm = self._apply_v317(
                sm,
                tr=tr_ref,
                t_date=t_date,
                state=state,
                life=life,
                close=_f(close),
                metrics=m,
                ma20_slope_neg=ma20_slope_neg,
                higher_low_broken=higher_low_broken,
                realized=realized,
                trend_age_days=int(
                    trend_age_days if trend_age_days is not None else (wave_age_days or 0)
                ),
                vol_ratio=_f(vol_ratio) if vol_ratio is not None else _f(m.get("vol_ratio")),
                just_breakout_today=bool(just_breakout_today),
                peak_gain_from_anchor=peak_gain_from_anchor,
            )
        return sm

    # ------------------------------------------------------------------
    def _update_structure(
        self,
        tr: _TrendTrack,
        *,
        t_date: str,
        state: str,
        life: str,
        close: Optional[float],
        low: Optional[float],
        metrics: Dict[str, Any],
        ma20_slope_neg: bool,
        higher_low_broken: bool,
        reasons: List[str],
    ) -> None:
        vol_r = _f(metrics.get("vol_ratio"))
        pullback_ok = bool(metrics.get("pullback_ok"))
        ma20_up = not ma20_slope_neg and (_f(metrics.get("ma20_slope")) or 0) >= 0
        bo_hi = _f(metrics.get("breakout_high"))
        pct_today = _f(metrics.get("pct_today"))
        hold_need = int(self.cfg.get("breakout_hold_days", 2))
        strong_vol = float(self.cfg.get("strong_breakout_vol_ratio", 2.0))
        strong_pct = float(self.cfg.get("strong_breakout_pct", 5.0))
        pb_vol_max = float(self.cfg.get("healthy_pullback_vol_max", 1.15))

        is_breakout_day = life in _BREAKOUT_PHASES or (
            state in (TrendState.CONFIRMED.value, TrendState.NEW_WAVE.value)
            and life in _BREAKOUT_PHASES
        )
        # Also treat explicit FIRST/SECOND breakout
        if life in ("FIRST_BREAKOUT", "SECOND_BREAKOUT"):
            is_breakout_day = True

        # 1) First breakout
        if not tr.first_breakout and (
            is_breakout_day
            or state == TrendState.CONFIRMED.value
            or life in ("FIRST_BREAKOUT", "SECOND_BREAKOUT")
        ):
            # Prefer actual breakout phase for first mark
            if life in ("FIRST_BREAKOUT", "SECOND_BREAKOUT", "MAIN_RISE", "MAIN_RISE_ACCEL", "ACCELERATION") or (
                state == TrendState.CONFIRMED.value and close is not None
            ):
                tr.first_breakout = True
                tr.first_breakout_date = t_date
                tr.first_breakout_px = bo_hi or close
                tr.prior_swing_low = low
                tr.notes.append(f"firstBreakout@{t_date}:{life or state}")
                tr.hold_days = 1 if close is not None and (bo_hi is None or close >= bo_hi * 0.995) else 0

        # After first breakout: track hold on first leg (does not complete alone)
        if tr.first_breakout and not tr.re_breakout and not tr.healthy_pullback:
            if close is not None and tr.first_breakout_px is not None and close >= tr.first_breakout_px * 0.995:
                if life not in _PULLBACK_PHASES:
                    tr.hold_days += 0  # first-leg hold tracked lightly
            # Detect healthy pullback
            if life in _PULLBACK_PHASES or (pullback_ok and not is_breakout_day):
                vol_ok = vol_r is None or vol_r <= pb_vol_max
                struct_ok = not higher_low_broken and (ma20_up or not ma20_slope_neg)
                if struct_ok and (vol_ok or pullback_ok):
                    tr.healthy_pullback = True
                    tr.pullback_date = t_date
                    tr.pullback_low = low if low is not None else close
                    tr.notes.append(f"healthyPullback@{t_date}")
                    # higher low vs prior swing
                    if tr.prior_swing_low is not None and tr.pullback_low is not None:
                        if float(tr.pullback_low) >= float(tr.prior_swing_low) * 0.995:
                            tr.higher_low = True
                            tr.notes.append(f"higherLow@{t_date}")
                    elif not higher_low_broken and pullback_ok:
                        tr.higher_low = True
                        tr.notes.append(f"higherLow_heuristic@{t_date}")

        # Continue tracking pullback / HL if already in pullback
        if tr.first_breakout and tr.healthy_pullback and not tr.re_breakout:
            if life in _PULLBACK_PHASES:
                if low is not None:
                    tr.pullback_low = (
                        min(float(tr.pullback_low), float(low))
                        if tr.pullback_low is not None
                        else float(low)
                    )
                if not tr.higher_low and not higher_low_broken:
                    if tr.prior_swing_low is not None and tr.pullback_low is not None:
                        if float(tr.pullback_low) >= float(tr.prior_swing_low) * 0.995:
                            tr.higher_low = True
                            tr.notes.append(f"higherLow@{t_date}")
                    elif pullback_ok:
                        tr.higher_low = True
                        tr.notes.append(f"higherLow_heuristic@{t_date}")

            # Re-breakout after pullback
            if is_breakout_day and life in (
                "FIRST_BREAKOUT",
                "SECOND_BREAKOUT",
                "MAIN_RISE",
                "MAIN_RISE_ACCEL",
                "ACCELERATION",
            ):
                tr.re_breakout = True
                tr.re_breakout_date = t_date
                tr.re_breakout_px = bo_hi or close
                tr.hold_days = 1
                tr.notes.append(f"reBreakout@{t_date}:{life}")
                # strong day may relax hold
                if (
                    vol_r is not None
                    and vol_r >= strong_vol
                    and pct_today is not None
                    and pct_today >= strong_pct
                ):
                    tr.breakout_hold = True
                    tr.hold_days = max(tr.hold_days, hold_need)
                    tr.notes.append(f"strongBreakoutHold@{t_date}")

        # Hold after re-breakout
        if tr.re_breakout and not tr.breakout_hold:
            ref = tr.re_breakout_px or tr.first_breakout_px
            if close is not None and ref is not None and close >= float(ref) * 0.995:
                tr.hold_days += 1
                if tr.hold_days >= hold_need:
                    tr.breakout_hold = True
                    tr.notes.append(f"breakoutHold@{t_date}:days={tr.hold_days}")
            else:
                # lost hold — reset hold counter but keep rebreak flag
                if close is not None and ref is not None and close < float(ref) * 0.98:
                    tr.hold_days = 0

        # MatureContinuation: established mature state + contraction + volume rebreak
        if state in _MATURE_STATES:
            if life in _PULLBACK_PHASES or (
                pullback_ok and vol_r is not None and vol_r <= pb_vol_max
            ):
                tr.contraction_seen = True
                tr.contraction_date = t_date
            if tr.contraction_seen and is_breakout_day and life in _BREAKOUT_PHASES:
                if vol_r is not None and vol_r >= 1.2:
                    tr.mature_continuation = True
                    tr.notes.append(f"MatureContinuation@{t_date}")
            # Also: MAIN_RISE/ACCEL with completed partial structure + MA alignment
            if (
                not tr.mature_continuation
                and tr.first_breakout
                and (tr.healthy_pullback or tr.contraction_seen)
                and bool(metrics.get("ma5_gt_ma10_gt_ma20"))
                and not ma20_slope_neg
                and state in (TrendState.MAIN_RISE.value, TrendState.ACCELERATION.value)
                and (tr.re_breakout or is_breakout_day)
            ):
                tr.mature_continuation = True
                if not tr.higher_low and not higher_low_broken:
                    tr.higher_low = True
                if not tr.re_breakout and is_breakout_day:
                    tr.re_breakout = True
                    tr.re_breakout_date = t_date
                    tr.re_breakout_px = bo_hi or close
                tr.notes.append(f"MatureContinuation_main@{t_date}")

        # If we entered MAIN_RISE with full checklist mid-flight, mark HL from structure
        if (
            state in _MATURE_STATES
            and tr.first_breakout
            and tr.healthy_pullback
            and not tr.higher_low
            and not higher_low_broken
        ):
            tr.higher_low = True
            tr.notes.append(f"higherLow_on_main@{t_date}")

    def _main_rise_confirmed(
        self,
        *,
        state: str,
        m: Dict[str, Any],
        ma20_slope_neg: bool,
        higher_low_broken: bool,
        tr: _TrendTrack,
    ) -> bool:
        if state not in (
            TrendState.MAIN_RISE.value,
            TrendState.ACCELERATION.value,
            TrendState.NEW_WAVE.value,
        ):
            return False
        ma_stack = bool(m.get("ma5_gt_ma10_gt_ma20"))
        ma20_up = not ma20_slope_neg
        hh_hl = not higher_low_broken and (tr.higher_low or tr.healthy_pullback)
        valid_bo = tr.first_breakout or tr.re_breakout or tr.mature_continuation
        consecutive = tr.breakout_hold or tr.hold_days >= 1 or tr.mature_continuation
        return bool(ma_stack and ma20_up and hh_hl and valid_bo and consecutive)

    def _score_structure(
        self,
        tr: _TrendTrack,
        m: Dict[str, Any],
        ma20_slope_neg: bool,
        higher_low_broken: bool,
    ) -> float:
        pts = 0.0
        if bool(m.get("ma5_gt_ma10_gt_ma20")):
            pts += 35.0
        ma20_slope = _f(m.get("ma20_slope"))
        if not ma20_slope_neg and (ma20_slope is None or ma20_slope > 0):
            pts += 25.0
        if not higher_low_broken:
            pts += 15.0
        if tr.higher_low:
            pts += 15.0
        if tr.healthy_pullback:
            pts += 10.0
        return _clamp(pts)

    def _score_breakout(self, tr: _TrendTrack, m: Dict[str, Any], life: str) -> float:
        pts = 0.0
        if tr.first_breakout:
            pts += 25.0
        # Distinguish first vs rebreak — rebreak valued higher
        if tr.re_breakout:
            pts += 40.0
        elif life == "SECOND_BREAKOUT":
            pts += 20.0  # phase says second but tracker may lag
        elif life == "FIRST_BREAKOUT":
            pts += 10.0
        if tr.breakout_hold:
            pts += 25.0
        elif tr.hold_days >= 1:
            pts += 10.0
        if tr.completed:
            pts += 10.0
        return _clamp(pts)

    def _score_continuation(
        self,
        tr: _TrendTrack,
        state: str,
        peak_gain: Optional[float],
        wave_age: int,
    ) -> float:
        pts = 0.0
        # Time is auxiliary only — soft contribution capped
        days = tr.days_in_confirmed
        pts += min(25.0, days * 1.5)  # ~17 days → 25, not auto-mature
        if state == TrendState.MAIN_RISE.value:
            pts += 30.0
        elif state == TrendState.ACCELERATION.value:
            pts += 35.0
        elif state == TrendState.NEW_WAVE.value:
            pts += 30.0
        elif state == TrendState.CONFIRMED.value:
            pts += 10.0
        pg = float(peak_gain) if peak_gain is not None else 0.0
        if pg >= 0.08:
            pts += 15.0
        elif pg >= 0.04:
            pts += 8.0
        if tr.mature_continuation:
            pts += 15.0
        return _clamp(pts)

    def _score_volume_price(self, tr: _TrendTrack, m: Dict[str, Any], life: str) -> float:
        pts = 40.0  # base
        vol_r = _f(m.get("vol_ratio"))
        if life in _BREAKOUT_PHASES and vol_r is not None:
            if vol_r >= 1.5:
                pts += 35.0
            elif vol_r >= 1.2:
                pts += 20.0
            else:
                pts += 5.0
        if life in _PULLBACK_PHASES and vol_r is not None:
            if vol_r <= 1.0:
                pts += 25.0  # healthy contraction
            elif vol_r <= 1.15:
                pts += 15.0
        if bool(m.get("second_ok")):
            pts += 10.0
        if tr.healthy_pullback and tr.re_breakout:
            pts += 15.0
        return _clamp(pts)

    def _level_from_score(self, score: float) -> str:
        if score >= float(self.cfg.get("level_advanced_min", 85)):
            return TrendMaturityLevel.ADVANCED.value
        if score >= float(self.cfg.get("level_mature_min", 65)):
            return TrendMaturityLevel.MATURE.value
        if score >= float(self.cfg.get("level_building_min", 40)):
            return TrendMaturityLevel.BUILDING.value
        return TrendMaturityLevel.EARLY.value


    # ------------------------------------------------------------------
    # V3.1.7 overlay
    # ------------------------------------------------------------------
    def _apply_v317(
        self,
        sm: SignalMaturity,
        *,
        tr: Optional[_TrendTrack],
        t_date: str,
        state: str,
        life: str,
        close: Optional[float],
        metrics: Dict[str, Any],
        ma20_slope_neg: bool,
        higher_low_broken: bool,
        realized: Optional[RealizedTrendResult],
        trend_age_days: int,
        vol_ratio: Optional[float],
        just_breakout_today: bool,
        peak_gain_from_anchor: Optional[float],
    ) -> SignalMaturity:
        cfg = self.cfg
        reasons = list(sm.reasons)
        # Track breakout event dates for follow-through
        tid = int(tr.trend_id) if tr else 0
        if tr and (tr.re_breakout_date or tr.first_breakout_date):
            evs = self._breakout_events.setdefault(tid, [])
            for d, kind in (
                (tr.first_breakout_date, "first"),
                (tr.re_breakout_date, "re"),
            ):
                if d and not any(e.get("date") == d for e in evs):
                    evs.append(
                        {
                            "date": d,
                            "kind": kind,
                            "px": tr.re_breakout_px if kind == "re" else tr.first_breakout_px,
                        }
                    )

        ft = self._follow_through(
            tid=tid,
            t_date=t_date,
            close=close,
            metrics=metrics,
            vol_ratio=vol_ratio,
            tr=tr,
        )
        mrq = self._main_rise_quality(
            state=state,
            tr=tr,
            metrics=metrics,
            ma20_slope_neg=ma20_slope_neg,
            higher_low_broken=higher_low_broken,
            realized=realized,
            vol_ratio=vol_ratio,
        )
        tstruct = self._trend_structure_score(
            tr=tr,
            metrics=metrics,
            ma20_slope_neg=ma20_slope_neg,
            higher_low_broken=higher_low_broken,
            life=life,
        )

        trend_ret = float(realized.trendReturn) if realized else float(peak_gain_from_anchor or 0.0)
        wave_ret = float(realized.currentWaveReturn) if realized else float(peak_gain_from_anchor or 0.0)
        age = int(trend_age_days or (realized.realizedDays if realized else 0) or (tr.days_in_confirmed if tr else 0))

        # V317 component scores
        age_s = _clamp(min(100.0, age * (100.0 / 25.0)))  # soft; 25d → 100
        ret_s = _clamp(0 if trend_ret <= 0 else min(100.0, trend_ret / 0.30 * 100.0))
        wr_s = _clamp(0 if wave_ret <= 0 else min(100.0, wave_ret / 0.20 * 100.0))
        hh_s = 100.0 if (tr and tr.re_breakout) or bool(metrics.get("ma5_gt_ma10_gt_ma20")) else (
            60.0 if tr and tr.first_breakout else 20.0
        )
        if higher_low_broken:
            hh_s = min(hh_s, 30.0)
        hl_s = 100.0 if (tr and tr.higher_low and not higher_low_broken) else (
            40.0 if not higher_low_broken else 0.0
        )
        stab_s = float(ft.score)

        w_age = float(cfg.get("weight_age", 20))
        w_ret = float(cfg.get("weight_cum_return", 25))
        w_wr = float(cfg.get("weight_wave_return", 20))
        w_hh = float(cfg.get("weight_hh", 15))
        w_hl = float(cfg.get("weight_hl", 10))
        w_st = float(cfg.get("weight_stability", 10))
        score = _clamp(
            (age_s * w_age + ret_s * w_ret + wr_s * w_wr + hh_s * w_hh + hl_s * w_hl + stab_s * w_st)
            / 100.0
        )

        level = self._level_from_score(score)

        # --- Hard MATURE gates ---
        floor_ok = bool(realized.matureFloorOk) if realized else False
        min_age = int(cfg.get("min_age_for_mature", 15))
        mrq_min = float(cfg.get("main_rise_quality_min", 70))
        struct_ok = bool(sm.structural and (sm.structural.completed or sm.structural.matureContinuation))
        hl_ok = bool(tr and tr.higher_low and not higher_low_broken)
        ma_ok = bool(metrics.get("ma5_gt_ma10_gt_ma20")) and not ma20_slope_neg
        pullback_q = bool(tr and tr.healthy_pullback) or struct_ok
        stability_ok = ft.level in (
            FollowThroughLevel.PASS.value,
            FollowThroughLevel.WEAK.value,
        ) and ft.daysSinceBreakout >= 3

        # 10d+30% alone cannot MATURE
        fast_spike = age <= 10 and trend_ret >= 0.30
        structural_extras_ok = hl_ok and ma_ok and pullback_q and (
            stability_ok or (tr and tr.breakout_hold)
        )

        can_mature = True
        block_reasons: List[str] = []
        if age < min_age:
            can_mature = False
            block_reasons.append(f"age={age}<{min_age}")
        if not floor_ok:
            can_mature = False
            block_reasons.append("realized_floor_fail")
        if mrq.score < mrq_min:
            can_mature = False
            block_reasons.append(f"mainRiseQuality={mrq.score:.0f}<{mrq_min:.0f}")
        if ft.level == FollowThroughLevel.FAIL.value:
            can_mature = False
            block_reasons.append("followThrough=FAIL")
        if just_breakout_today or (ft.daysSinceBreakout < 3 and not stability_ok):
            # same-day / <3d structure → max BUILDING
            can_mature = False
            block_reasons.append("await_follow_through")
        if fast_spike and not structural_extras_ok:
            can_mature = False
            block_reasons.append("fast_spike_without_structure")
        if state == TrendState.CONFIRMED.value:
            can_mature = False
            block_reasons.append("CONFIRMED_clamp")

        order = [
            TrendMaturityLevel.EARLY.value,
            TrendMaturityLevel.BUILDING.value,
            TrendMaturityLevel.MATURE.value,
            TrendMaturityLevel.ADVANCED.value,
        ]
        if not can_mature:
            max_lvl = str(cfg.get("same_day_max_level") or "BUILDING")
            if order.index(level) > order.index(max_lvl):
                level = max_lvl
                mature_min = float(cfg.get("level_mature_min", 65))
                score = min(score, mature_min - 0.1)
            reasons.append("V317_BLOCK:" + ",".join(block_reasons))
            if tid:
                self._mature_since.pop(tid, None)
        else:
            # ensure at least MATURE floor when gates pass
            mature_min = float(cfg.get("level_mature_min", 65))
            score = max(score, mature_min)
            level = TrendMaturityLevel.MATURE.value
            reasons.append("V317_MATURE_gates_pass")
            # First days after gates pass stay MATURE (so PRIME/CONFIRMED can fire).
            # ADVANCED only after being mature for a while (age since first mature >= 5)
            # or when score very high AND already tracked as mature previously.
            first_m = self._mature_since.get(tid) if tid else None
            if tid and first_m is None:
                self._mature_since[tid] = t_date
                first_m = t_date
                level = TrendMaturityLevel.MATURE.value
                reasons.append("V317_first_mature_day")
            else:
                matured_days = 0
                if first_m and t_date:
                    try:
                        from datetime import datetime
                        matured_days = (datetime.strptime(t_date[:10], "%Y-%m-%d")
                                        - datetime.strptime(first_m[:10], "%Y-%m-%d")).days
                    except Exception:
                        matured_days = 0
                if score >= float(cfg.get("level_advanced_min", 85)) and matured_days >= 5:
                    level = TrendMaturityLevel.ADVANCED.value
                    reasons.append(f"V317_ADVANCED after {matured_days}d mature")
                else:
                    level = TrendMaturityLevel.MATURE.value

        if state == TrendState.CONFIRMED.value:
            max_for_confirmed = str(cfg.get("confirmed_max_level") or "BUILDING")
            if order.index(level) > order.index(max_for_confirmed):
                level = max_for_confirmed
                score = min(score, float(cfg.get("level_mature_min", 65)) - 0.1)

        reasons.append(
            f"V317 score={score:.1f} age={age_s:.0f} ret={ret_s:.0f} wr={wr_s:.0f} "
            f"hh={hh_s:.0f} hl={hl_s:.0f} stab={stab_s:.0f} ft={ft.level} mrq={mrq.score:.0f}"
        )

        sm.score = round(score, 2)
        sm.level = level
        sm.reasons = reasons
        sm.ageScore = round(age_s, 2)
        sm.returnScore = round(ret_s, 2)
        sm.waveReturnScore = round(wr_s, 2)
        sm.hhScore = round(hh_s, 2)
        sm.hlScore = round(hl_s, 2)
        sm.stabilityScore = round(stab_s, 2)
        sm.mainRiseQuality = round(mrq.score, 2)
        sm.followThrough = ft.level
        sm.followThroughScore = round(ft.score, 2)
        sm.trendStructureScore = round(tstruct, 2)
        sm.realizedTrendScore = round(float(realized.score), 2) if realized else 0.0
        sm.trendAge = age
        sm.trendReturn = round(trend_ret, 6)
        sm.currentWaveReturn = round(wave_ret, 6)
        # map structure/breakout fields for compat
        sm.structure = round(tstruct, 2)
        sm.breakout = round(stab_s, 2)
        sm.continuation = round(wr_s, 2)
        return sm

    def _follow_through(
        self,
        *,
        tid: int,
        t_date: str,
        close: Optional[float],
        metrics: Dict[str, Any],
        vol_ratio: Optional[float],
        tr: Optional[_TrendTrack],
    ) -> BreakoutFollowThrough:
        cfg = self.cfg
        evs = list(self._breakout_events.get(tid) or [])
        if tr and tr.re_breakout_date:
            ref_date = tr.re_breakout_date
            ref_px = tr.re_breakout_px
        elif tr and tr.first_breakout_date:
            ref_date = tr.first_breakout_date
            ref_px = tr.first_breakout_px
        elif evs:
            ref_date = evs[-1]["date"]
            ref_px = evs[-1].get("px")
        else:
            return BreakoutFollowThrough(
                level=FollowThroughLevel.UNKNOWN.value,
                score=20.0,
                reasons=["no_breakout_ref"],
            )

        # days since breakout — approximate via hold_days / tracker notes; caller may pass hist
        # Use hold_days and whether completed as proxy when hist not in engine
        days = 0
        if ref_date and t_date:
            try:
                # calendar proxy; regression passes trend_age; here use hold + completed
                from datetime import datetime

                d0 = datetime.strptime(ref_date[:10], "%Y-%m-%d")
                d1 = datetime.strptime(t_date[:10], "%Y-%m-%d")
                days = max(0, (d1 - d0).days)  # calendar; trading-day refine in regression via age
            except Exception:
                days = int(tr.hold_days) if tr else 0

        # Prefer structural hold signals
        held = bool(tr and tr.breakout_hold) or (
            close is not None and ref_px is not None and close >= float(ref_px) * 0.995
        )
        price_c = 0.0
        if close is not None and ref_px is not None and float(ref_px) > 0:
            price_c = close / float(ref_px) - 1.0
        vol_ok = vol_ratio is None or vol_ratio >= 0.8
        close_ok = held

        score = 30.0
        if days >= 3 and held:
            score += 25.0
        if days >= 5 and held and price_c >= 0.0:
            score += 20.0
        if days >= 10 and held and price_c >= 0.02:
            score += 15.0
        if close_ok:
            score += 5.0
        if vol_ok and days >= 3:
            score += 5.0
        if tr and tr.completed:
            score += 10.0
        if not held and days >= 3:
            score = min(score, 35.0)

        score = _clamp(score)
        pass_min = float(cfg.get("follow_through_pass_min", 70))
        weak_min = float(cfg.get("follow_through_weak_min", 45))
        if days < 3:
            level = FollowThroughLevel.WEAK.value if held else FollowThroughLevel.FAIL.value
            if days == 0:
                level = FollowThroughLevel.UNKNOWN.value
                score = min(score, 40.0)
        elif score >= pass_min and held:
            level = FollowThroughLevel.PASS.value
        elif score >= weak_min and held:
            level = FollowThroughLevel.WEAK.value
        else:
            level = FollowThroughLevel.FAIL.value

        return BreakoutFollowThrough(
            level=level,
            score=round(score, 2),
            daysSinceBreakout=int(days),
            heldAboveBreak=bool(held),
            priceContinuation=round(price_c, 6),
            volumeOk=bool(vol_ok),
            closePositionOk=bool(close_ok),
            reasons=[f"ref={ref_date}", f"days≈{days}", f"held={held}"],
        )

    def _main_rise_quality(
        self,
        *,
        state: str,
        tr: Optional[_TrendTrack],
        metrics: Dict[str, Any],
        ma20_slope_neg: bool,
        higher_low_broken: bool,
        realized: Optional[RealizedTrendResult],
        vol_ratio: Optional[float],
    ) -> MainRiseQuality:
        pts = 0.0
        reasons: List[str] = []
        in_main = state in (
            TrendState.MAIN_RISE.value,
            TrendState.ACCELERATION.value,
            TrendState.NEW_WAVE.value,
        )
        if in_main:
            pts += 25.0
            reasons.append("main_family")
        hh = bool(tr and (tr.re_breakout or tr.first_breakout)) and not higher_low_broken
        hl = bool(tr and tr.higher_low) and not higher_low_broken
        new_high = bool(realized and realized.newHighProgress >= 85.0)
        pullback_intact = bool(tr and tr.healthy_pullback and not higher_low_broken) or (
            not higher_low_broken and bool(metrics.get("pullback_ok"))
        )
        vol_ctrl = vol_ratio is None or vol_ratio <= 3.5
        if hh:
            pts += 20.0
        if hl:
            pts += 20.0
        if new_high:
            pts += 15.0
        if pullback_intact:
            pts += 10.0
        if vol_ctrl:
            pts += 10.0
        if bool(metrics.get("ma5_gt_ma10_gt_ma20")) and not ma20_slope_neg:
            pts += 10.0
        if higher_low_broken or ma20_slope_neg:
            pts = min(pts, 40.0)
        return MainRiseQuality(
            score=round(_clamp(pts), 2),
            higherHigh=hh,
            higherLow=hl,
            newHigh=new_high,
            pullbackIntact=pullback_intact,
            volumeControlled=vol_ctrl,
            reasons=reasons,
        )

    def _trend_structure_score(
        self,
        *,
        tr: Optional[_TrendTrack],
        metrics: Dict[str, Any],
        ma20_slope_neg: bool,
        higher_low_broken: bool,
        life: str,
    ) -> float:
        pts = 0.0
        if bool(metrics.get("ma5_gt_ma10_gt_ma20")):
            pts += 25.0
        if not ma20_slope_neg:
            pts += 15.0
        if not higher_low_broken:
            pts += 10.0
        if tr and tr.higher_low:
            pts += 15.0
        if tr and tr.healthy_pullback:
            pts += 10.0
        if life in ("SECOND_BREAKOUT",) or (tr and tr.re_breakout):
            pts += 15.0
        elif life in ("FIRST_BREAKOUT",) or (tr and tr.first_breakout):
            pts += 8.0
        if tr and tr.breakout_hold:
            pts += 10.0
        return _clamp(pts)



__all__ = ["TrendMaturityEngine", "SignalMaturity"]
