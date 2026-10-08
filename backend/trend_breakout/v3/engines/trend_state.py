"""
TrendStateEngine — V3.1.4 trend-state machine (V3.1.2 lifecycle + Major/Sub + Major gate).

Stateful day-step with:
  - TrendFailure aligned to ExitEngine structure + 1-day false break
  - TrendId never reused across FAILURE
  - MAIN_RISE from CONFIRMED; COOLING → NEW_WAVE; ACCELERATION ≠ OH default
  - Exhaustion HIGH never alone forces COOLING/FAILURE
  - Emits TrendStateSnapshot + transition reasons

Does not invent buy signals. No symbol-specific branches.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union

from v3.types import Lifecycle
from v3.v31.config import V312_LIFECYCLE, get_v313_lifecycle, get_v314_lifecycle
from v3.v31.types import (
    ExhaustionLevel,
    StateTransitionLogEntry,
    TrendState,
    TrendStateSnapshot,
    WaveInfo,
)
from v3.engines.trend_wave import TrendWaveEngine


@dataclass
class TrendStateResult:
    ok: bool = True
    trend_state: TrendState = TrendState.NONE
    lifecycle: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    snapshot: Optional[TrendStateSnapshot] = None
    transition: Optional[StateTransitionLogEntry] = None
    wave: Optional[WaveInfo] = None
    events: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["trend_state"] = (
            self.trend_state.value
            if isinstance(self.trend_state, TrendState)
            else self.trend_state
        )
        if self.snapshot is not None:
            d["snapshot"] = self.snapshot.to_dict()
        if self.transition is not None:
            d["transition"] = self.transition.to_dict()
        if self.wave is not None:
            d["wave"] = self.wave.to_dict()
        return d


# Lifecycle → coarse hint (OH does NOT map to ACCELERATION in V3.1.2)
_LIFE_HINT = {
    Lifecycle.TREND_FAILURE.value: TrendState.TREND_FAILURE,
    Lifecycle.OVERHEATED.value: None,  # exhaustion only
    Lifecycle.ACCELERATION.value: TrendState.ACCELERATION,
    Lifecycle.MAIN_RISE.value: TrendState.MAIN_RISE,
    Lifecycle.SECOND_BREAKOUT.value: TrendState.CONFIRMED,
    Lifecycle.FIRST_BREAKOUT.value: TrendState.CONFIRMED,
    Lifecycle.PLATFORM_HOLD.value: TrendState.OBSERVE,
    Lifecycle.HEALTHY_PULLBACK.value: TrendState.COOLING,
    Lifecycle.PLATFORM.value: TrendState.OBSERVE,
    Lifecycle.ACCUMULATION.value: TrendState.OBSERVE,
    Lifecycle.OBSERVE.value: TrendState.OBSERVE,
    Lifecycle.NONE.value: TrendState.NONE,
}

_ACTIVE = {
    TrendState.CONFIRMED.value,
    TrendState.MAIN_RISE.value,
    TrendState.ACCELERATION.value,
    TrendState.COOLING.value,
    TrendState.NEW_WAVE.value,
}


class TrendStateEngine:
    """
    V3.1.2 stateful engine.

    Use ``step(...)`` for day-by-day replay.
    ``evaluate(...)`` kept for V3.1.1 stateless mapping (compat).
    """

    def __init__(
        self,
        lifecycle_cfg: Optional[Dict[str, Any]] = None,
        wave_engine: Optional[TrendWaveEngine] = None,
    ) -> None:
        self.cfg = dict(lifecycle_cfg or get_v314_lifecycle())
        self.wave = wave_engine or TrendWaveEngine(lifecycle_cfg=self.cfg)
        self._trend_id = 0
        self._state = TrendState.NONE.value
        self._days_in_state = 0
        self._days_in_trend = 0
        self._false_break_pending = False
        self._false_break_days = 0
        self._code6 = ""
        self._peak_in_trend: Optional[float] = None
        self._advance_from_anchor_pct = 0.0

    def reset(self) -> None:
        self._trend_id = 0
        self._state = TrendState.NONE.value
        self._days_in_state = 0
        self._days_in_trend = 0
        self._false_break_pending = False
        self._false_break_days = 0
        self._peak_in_trend = None
        self._advance_from_anchor_pct = 0.0
        self.wave.reset()

    # ------------------------------------------------------------------
    # Stateless compat (V3.1 / V3.1.1)
    # ------------------------------------------------------------------
    def evaluate(
        self,
        *,
        lifecycle: Union[str, Lifecycle, None] = None,
        overheated: bool = False,
        broken: bool = False,
        is_new_wave: bool = False,
        phase: Optional[str] = None,
    ) -> TrendStateResult:
        tags: List[str] = []
        notes: List[str] = []

        if broken:
            return TrendStateResult(
                ok=True,
                trend_state=TrendState.TREND_FAILURE,
                lifecycle=_life_str(lifecycle),
                tags=["BROKEN"],
                notes=["broken → TREND_FAILURE"],
            )

        life = _life_str(lifecycle)
        if life == "NONE" and phase:
            life = Lifecycle.map_from_v2_phase(phase).value

        if life == Lifecycle.TREND_FAILURE.value:
            return TrendStateResult(
                ok=True,
                trend_state=TrendState.TREND_FAILURE,
                lifecycle=life,
                tags=["LIFE_FAIL"],
                notes=["lifecycle TREND_FAILURE"],
            )

        if is_new_wave and life not in (
            Lifecycle.TREND_FAILURE.value,
            Lifecycle.NONE.value,
        ):
            tags.append("NEW_WAVE")
            notes.append("wave engine marked NEW_WAVE")
            return TrendStateResult(
                ok=True,
                trend_state=TrendState.NEW_WAVE,
                lifecycle=life,
                tags=tags,
                notes=notes,
            )

        hint = _LIFE_HINT.get(life, TrendState.OBSERVE)
        # V3.1.2: OVERHEATED does not become ACCELERATION
        if life == Lifecycle.OVERHEATED.value:
            state = TrendState.ACCELERATION if False else TrendState.MAIN_RISE
            tags.append("OH_NO_ACCEL_DEFAULT")
            notes.append("overheated → keep MAIN_RISE-ish; exhaustion separate (compat path)")
            # In compat path keep prior-like: map to MAIN_RISE not ACCELERATION
            return TrendStateResult(
                ok=True,
                trend_state=TrendState.MAIN_RISE,
                lifecycle=life,
                tags=tags,
                notes=notes,
            )

        state = hint if hint is not None else TrendState.OBSERVE
        if overheated and state not in (TrendState.TREND_FAILURE, TrendState.NONE):
            tags.append("OVERHEAT_FLAG")
            notes.append("overheated flag → exhaustion separate; no ACCEL default")

        return TrendStateResult(
            ok=True,
            trend_state=state,
            lifecycle=life,
            tags=tags,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Stateful day step (V3.1.2)
    # ------------------------------------------------------------------
    def step(
        self,
        *,
        code6: str,
        t_date: str,
        close: Optional[float],
        low: Optional[float] = None,
        high: Optional[float] = None,
        lifecycle: Union[str, Lifecycle, None] = None,
        overheated: bool = False,
        broken: bool = False,
        ma20_slope_neg: bool = False,
        higher_low_broken: bool = False,
        trend_break_price: Optional[float] = None,
        close_below_ma20: bool = False,
        session_consumption: Optional[float] = None,
        exhaustion: Union[str, ExhaustionLevel, None] = None,
        ext_ma20_pct: Optional[float] = None,
        limit_ups_5d: int = 0,
        cont: Optional[float] = None,
        tech: Optional[float] = None,
        breakout_ref: Optional[float] = None,
        false_break_intraday: bool = False,
        ma20_up: Optional[bool] = None,
    ) -> TrendStateResult:
        self._code6 = str(code6).zfill(6)
        life = _life_str(lifecycle)
        exh = ExhaustionLevel.coerce(exhaustion)
        fail_cfg = dict(self.cfg.get("failure") or {})
        conf_cfg = dict(self.cfg.get("confirm") or {})
        mr_cfg = dict(self.cfg.get("main_rise") or {})
        acc_cfg = dict(self.cfg.get("acceleration") or {})
        cool_cfg = dict(self.cfg.get("cooling") or {})
        nw_cfg = dict(self.cfg.get("new_wave") or {})

        tags: List[str] = []
        notes: List[str] = []
        prior = self._state
        false_break_allow = int(fail_cfg.get("false_break_allow_days", 1))

        # --- Structure failure raw signal (ExitEngine-aligned + HL) ---
        close_below_break = (
            close is not None
            and trend_break_price is not None
            and float(close) < float(trend_break_price)
        )
        raw_failure = bool(
            (not fail_cfg.get("require_close_below_break", True) or close_below_break)
            and (not fail_cfg.get("require_ma20_slope_neg", True) or ma20_slope_neg)
            and (not fail_cfg.get("require_higher_low_break", True) or higher_low_broken)
        )
        # Also honor explicit broken from risk engine as hard failure candidate
        if broken and prior in _ACTIVE:
            raw_failure = True
            tags.append("RISK_BROKEN")

        # Intraday false break: low pierced but close held
        if false_break_intraday and not close_below_break:
            tags.append("FALSE_BREAK")
            notes.append("false_break: low pierced break but close held")

        failure_confirmed = False
        if raw_failure and prior in _ACTIVE:
            # 1-day false-break allowance: first raw day → pending; second confirms
            if false_break_allow <= 0:
                failure_confirmed = True
            elif self._false_break_pending:
                self._false_break_days += 1
                if self._false_break_days >= false_break_allow:
                    failure_confirmed = True
                else:
                    tags.append("FALSE_BREAK_PENDING")
                    notes.append(
                        f"structure pierce day {self._false_break_days}/{false_break_allow} — not yet FAILURE"
                    )
            else:
                self._false_break_pending = True
                self._false_break_days = 0
                tags.append("FALSE_BREAK_PENDING")
                notes.append("1-day false-break allowance started")
        else:
            # reclaim / not raw → clear pending
            if self._false_break_pending and not raw_failure:
                notes.append("false-break reclaimed — clear pending")
            self._false_break_pending = False
            self._false_break_days = 0

        # Exhaustion HIGH must NOT force FAILURE
        if exh == ExhaustionLevel.HIGH:
            tags.append("EXH_HIGH_NO_FORCE_FAIL")
            notes.append("Exhaustion HIGH annotates risk only (≠ TREND_FAILURE)")

        new_state = prior
        create_wave = False
        new_wave_event = False
        reason = "HOLD"

        if failure_confirmed:
            new_state = TrendState.TREND_FAILURE.value
            reason = "STRUCTURE_FAIL: Close<Break + MA20Slope<0 + HigherLow"
            tags.append("TREND_FAILURE")
        elif prior == TrendState.TREND_FAILURE.value:
            # After failure → OBSERVE until re-confirm
            if self._can_confirm(life, tech, cont, close, close_below_ma20, conf_cfg, ma20_slope_neg):
                new_state = TrendState.CONFIRMED.value
                reason = "RECONFIRM_AFTER_FAILURE"
                create_wave = True
                self._alloc_trend()
                tags.append("NEW_TREND")
            else:
                new_state = TrendState.OBSERVE.value
                reason = "POST_FAIL_OBSERVE"
        elif prior in (TrendState.NONE.value, TrendState.OBSERVE.value):
            if self._can_confirm(life, tech, cont, close, close_below_ma20, conf_cfg, ma20_slope_neg):
                new_state = TrendState.CONFIRMED.value
                reason = "CONFIRM"
                create_wave = True
                self._alloc_trend()
                tags.append("NEW_TREND")
            else:
                new_state = TrendState.OBSERVE.value if prior != TrendState.NONE.value else (
                    TrendState.OBSERVE.value if life not in ("NONE", "") else TrendState.NONE.value
                )
                if life and life not in ("NONE",):
                    new_state = TrendState.OBSERVE.value
                reason = "OBSERVE"
        elif prior == TrendState.COOLING.value:
            # COOLING → NEW_WAVE on rebreakout after min cooling / pullback
            if self._rebreakout_ok(close, low, nw_cfg):
                new_state = TrendState.NEW_WAVE.value
                reason = "COOLING_REBREAKOUT→NEW_WAVE"
                new_wave_event = True
                tags.append("NEW_WAVE")
            elif failure_confirmed:
                new_state = TrendState.TREND_FAILURE.value
                reason = "FAIL_FROM_COOLING"
            elif self._still_cooling(close, cool_cfg):
                new_state = TrendState.COOLING.value
                reason = "STILL_COOLING"
            else:
                # resume prior rise without formal new wave
                new_state = TrendState.MAIN_RISE.value
                reason = "COOLING_RESUME→MAIN_RISE"
        elif prior == TrendState.NEW_WAVE.value:
            # After NEW_WAVE, promote to MAIN_RISE quickly if structure holds
            if self._accel_ok(ext_ma20_pct, limit_ups_5d, life, overheated, acc_cfg):
                new_state = TrendState.ACCELERATION.value
                reason = "NEW_WAVE→ACCELERATION"
            elif self._days_in_state >= 2:
                new_state = TrendState.MAIN_RISE.value
                reason = "NEW_WAVE→MAIN_RISE"
            else:
                new_state = TrendState.NEW_WAVE.value
                reason = "NEW_WAVE_HOLD"
            # cooling entry
            if self._enter_cooling(close, prior, cool_cfg, exh):
                new_state = TrendState.COOLING.value
                reason = "PULLBACK→COOLING"
        elif prior == TrendState.CONFIRMED.value:
            if self._enter_cooling(close, prior, cool_cfg, exh):
                new_state = TrendState.COOLING.value
                reason = "CONFIRMED_PULLBACK→COOLING"
            elif self._main_rise_ok(life, cont, mr_cfg):
                new_state = TrendState.MAIN_RISE.value
                reason = "CONFIRMED→MAIN_RISE"
            elif self._accel_ok(ext_ma20_pct, limit_ups_5d, life, overheated, acc_cfg):
                # Only real accel — not default from OH
                new_state = TrendState.ACCELERATION.value
                reason = "CONFIRMED→ACCELERATION"
            else:
                new_state = TrendState.CONFIRMED.value
                reason = "CONFIRMED_HOLD"
        elif prior == TrendState.MAIN_RISE.value:
            if self._enter_cooling(close, prior, cool_cfg, exh):
                new_state = TrendState.COOLING.value
                reason = "MAIN_RISE_PULLBACK→COOLING"
            elif self._accel_ok(ext_ma20_pct, limit_ups_5d, life, overheated, acc_cfg):
                new_state = TrendState.ACCELERATION.value
                reason = "MAIN_RISE→ACCELERATION"
            else:
                new_state = TrendState.MAIN_RISE.value
                reason = "MAIN_RISE_HOLD"
        elif prior == TrendState.ACCELERATION.value:
            if self._enter_cooling(close, prior, cool_cfg, exh):
                new_state = TrendState.COOLING.value
                reason = "ACCEL_PULLBACK→COOLING"
            elif not self._accel_ok(ext_ma20_pct, limit_ups_5d, life, overheated, acc_cfg):
                # de-accelerate to MAIN_RISE (not failure)
                new_state = TrendState.MAIN_RISE.value
                reason = "ACCEL→MAIN_RISE"
            else:
                new_state = TrendState.ACCELERATION.value
                reason = "ACCEL_HOLD"
        else:
            new_state = TrendState.OBSERVE.value
            reason = "FALLBACK_OBSERVE"

        # OH flag: never alone → ACCELERATION
        if overheated or life == Lifecycle.OVERHEATED.value:
            tags.append("OVERHEAT_FLAG")
            notes.append("OH → Exhaustion/EntryWindow only; ACCELERATION ≠ default")

        # Wave engine step
        if new_state == TrendState.TREND_FAILURE.value and prior != TrendState.TREND_FAILURE.value:
            self.wave.on_trend_failure(t_date=t_date)

        # bind trend id before wave create
        self.wave.bind_trend_id(self._trend_id)

        # V3.1.3 candidate confirm signals (no lookahead)
        _ma20_up = (not ma20_slope_neg) if ma20_up is None else bool(ma20_up)
        _hh_hl_ok = not bool(higher_low_broken)

        wr = self.wave.step(
            t_date=t_date,
            close=close,
            low=low,
            high=high,
            state=new_state,
            prior_state=prior,
            is_new_wave_event=new_wave_event,
            create_wave=create_wave,
            session_consumption=session_consumption,
            breakout_ref=breakout_ref or trend_break_price,
            confirm_ref=low if low is not None else close,
            ma20_up=_ma20_up,
            hh_hl_ok=_hh_hl_ok,
            code6=self._code6,
        )
        tags.extend(wr.tags or [])
        notes.extend(wr.notes or [])

        # Update counters / peak
        if close is not None:
            if self._peak_in_trend is None or close > self._peak_in_trend:
                if new_state in _ACTIVE:
                    self._peak_in_trend = float(close)
        winfo = wr.wave
        if winfo.wave_anchor and close and winfo.wave_anchor > 0:
            self._advance_from_anchor_pct = (
                float(close) / float(winfo.wave_anchor) - 1.0
            ) * 100.0
        else:
            self._advance_from_anchor_pct = 0.0

        if new_state == prior:
            self._days_in_state += 1
        else:
            self._days_in_state = 1
        if new_state in _ACTIVE:
            if prior in _ACTIVE and not (
                prior != TrendState.TREND_FAILURE.value
                and new_state != TrendState.TREND_FAILURE.value
                and "NEW_TREND" in tags
            ):
                self._days_in_trend += 1
            elif "NEW_TREND" in tags:
                self._days_in_trend = 1
            else:
                self._days_in_trend = max(1, self._days_in_trend + 1)
        elif new_state == TrendState.TREND_FAILURE.value:
            self._days_in_trend = 0
            self._peak_in_trend = None

        transition = None
        if new_state != prior:
            transition = StateTransitionLogEntry(
                t_date=t_date,
                code6=self._code6,
                from_state=prior,
                to_state=new_state,
                trend_id=self._trend_id,
                wave_id=int(winfo.wave_id or 0),
                reason=reason,
                tags=list(tags),
            )

        self._state = new_state
        ts = TrendState(new_state) if new_state in TrendState._value2member_map_ else TrendState.NONE

        snap = TrendStateSnapshot(
            t_date=t_date,
            code6=self._code6,
            trend_id=self._trend_id,
            wave_id=int(winfo.wave_id or 0),
            state=new_state,
            prior_state=prior,
            transition=f"{prior}→{new_state}" if new_state != prior else None,
            wave_anchor=winfo.wave_anchor,
            wave_anchor_date=winfo.wave_anchor_date,
            wave_anchor_kind=winfo.wave_anchor_kind,
            wave_age_days=int(winfo.wave_age_days or 0),
            wave_consumption=winfo.wave_consumption,
            trend_consumption=winfo.trend_consumption,
            trend_break_price=trend_break_price,
            false_break_pending=bool(self._false_break_pending),
            exhaustion=exh.value,
            anchor_valid=bool(winfo.anchor_valid),
            days_in_state=self._days_in_state,
            days_in_trend=self._days_in_trend,
            tags=list(tags),
            notes=list(notes),
            wave_level=str(getattr(winfo, "wave_level", "NONE") or "NONE"),
            major_wave_id=int(getattr(winfo, "major_wave_id", 0) or 0),
            parent_major_id=int(getattr(winfo, "parent_major_id", 0) or 0),
            candidate_id=int(getattr(winfo, "candidate_id", 0) or 0),
            major_wave_consumption=getattr(winfo, "major_wave_consumption", None),
            sub_wave_consumption=getattr(winfo, "sub_wave_consumption", None),
            peak_gain_from_anchor=getattr(winfo, "peak_gain_from_anchor", None),
            structure_confirmed=bool(getattr(winfo, "structure_confirmed", False)),
        )

        return TrendStateResult(
            ok=True,
            trend_state=ts,
            lifecycle=life,
            tags=tags,
            notes=notes,
            snapshot=snap,
            transition=transition,
            wave=winfo,
            events=list(getattr(wr, "events", []) or []),
        )

    # --- helpers ---
    def _alloc_trend(self) -> None:
        self._trend_id += 1
        self._days_in_trend = 0
        self._peak_in_trend = None
        self._false_break_pending = False
        self._false_break_days = 0
        self.wave.on_new_trend(self._trend_id)

    def _can_confirm(
        self,
        life: str,
        tech: Optional[float],
        cont: Optional[float],
        close: Optional[float],
        close_below_ma20: bool,
        conf_cfg: Dict[str, Any],
        ma20_slope_neg: bool,
    ) -> bool:
        life_ok = life in set(conf_cfg.get("lifecycle_confirm") or ())
        # HEALTHY_PULLBACK alone is not confirm
        tech_min = float(conf_cfg.get("tech_min", 65))
        cont_min = float(conf_cfg.get("cont_min", 55))
        tech_ok = tech is None or float(tech) >= tech_min
        cont_ok = cont is None or float(cont) >= cont_min
        ma_ok = True
        if conf_cfg.get("require_close_above_ma20", True):
            ma_ok = not close_below_ma20 and not ma20_slope_neg
        return bool(life_ok and tech_ok and cont_ok and ma_ok)

    def _main_rise_ok(
        self, life: str, cont: Optional[float], mr_cfg: Dict[str, Any]
    ) -> bool:
        if life in set(mr_cfg.get("lifecycle_force") or ()):
            return True
        min_days = int(mr_cfg.get("min_days_confirmed", 3))
        min_adv = float(mr_cfg.get("min_advance_from_anchor_pct", 8.0))
        cont_min = float(mr_cfg.get("cont_min", 65.0))
        cont_ok = cont is None or float(cont) >= cont_min
        return (
            self._days_in_state >= min_days
            and self._advance_from_anchor_pct >= min_adv
            and cont_ok
        )

    def _accel_ok(
        self,
        ext_ma20_pct: Optional[float],
        limit_ups_5d: int,
        life: str,
        overheated: bool,
        acc_cfg: Dict[str, Any],
    ) -> bool:
        # OH alone NEVER qualifies
        if overheated and not (
            (ext_ma20_pct is not None and ext_ma20_pct >= float(acc_cfg.get("ext_ma20_pct_min", 18)))
            or limit_ups_5d >= int(acc_cfg.get("limit_ups_5d_min", 2))
        ):
            return False
        ext_ok = ext_ma20_pct is not None and float(ext_ma20_pct) >= float(
            acc_cfg.get("ext_ma20_pct_min", 18)
        )
        lu_ok = int(limit_ups_5d) >= int(acc_cfg.get("limit_ups_5d_min", 2))
        life_ok = life in set(acc_cfg.get("lifecycle_ok") or ()) and (ext_ok or lu_ok)
        return bool(ext_ok or lu_ok or life_ok)

    def _enter_cooling(
        self,
        close: Optional[float],
        prior: str,
        cool_cfg: Dict[str, Any],
        exh: ExhaustionLevel,
    ) -> bool:
        allow = set(cool_cfg.get("allow_from_states") or ())
        if prior not in allow:
            return False
        # Exhaustion HIGH alone must NOT force COOLING
        pb = self.wave.pullback_pct_from_peak(close)
        min_pb = float(cool_cfg.get("pullback_from_peak_pct", 5.0))
        if pb >= min_pb:
            return True
        return False

    def _still_cooling(self, close: Optional[float], cool_cfg: Dict[str, Any]) -> bool:
        pb = self.wave.pullback_pct_from_peak(close)
        min_pb = float(cool_cfg.get("pullback_from_peak_pct", 5.0))
        return pb >= min_pb * 0.6 or self.wave.cooling_days() < int(cool_cfg.get("min_days", 2))

    def _rebreakout_ok(
        self, close: Optional[float], low: Optional[float], nw_cfg: Dict[str, Any]
    ) -> bool:
        min_cool = int(nw_cfg.get("min_cooling_days", 5))
        min_pb = float(nw_cfg.get("min_pullback_pct", 8.0))
        min_consol = int(nw_cfg.get("min_consolidation_bars", 5))
        if self.wave.cooling_days() < min_cool:
            return False
        # Peak pullback must have reached min_pb OR consolidation≥N (V3.1.3)
        peak = self.wave.peak_close()
        pivot = self.wave.pivot_low()
        if close is None or peak is None or peak <= 0 or pivot is None:
            return False
        max_pb = (peak - pivot) / peak * 100.0
        consol = 0
        if hasattr(self.wave, "consolidation_bars"):
            consol = int(self.wave.consolidation_bars())
        if max_pb < min_pb and consol < min_consol:
            return False
        # Reclaim: close back above pivot with small margin
        if close > pivot * 1.015:
            return True
        return False


def _life_str(lifecycle: Union[str, Lifecycle, None]) -> str:
    if isinstance(lifecycle, Lifecycle):
        return lifecycle.value
    if lifecycle is None:
        return Lifecycle.NONE.value
    s = str(lifecycle).strip().upper()
    if s == "MAIN_RISE_ACCEL":
        return Lifecycle.ACCELERATION.value
    return s or Lifecycle.NONE.value


__all__ = ["TrendStateEngine", "TrendStateResult"]
