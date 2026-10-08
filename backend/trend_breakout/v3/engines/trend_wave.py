"""
TrendWaveEngine — V3.1.4/V3.1.5 Major gate (+ V3.1.3 Major/Sub + V3.1.2 WaveAnchor).
V3.1.5: gate_drives_live=False demotes peak gate (label-only 30%/20d).

V3.1.4: Candidate→Major requires PeakGainFromAnchor≥30% AND WaveAge≥20 (as-of,
no lookahead) PLUS V3.1.3 confirm rules. Until then stays CANDIDATE.
Fail before gate → FailedCandidate (never Major). Sub only under confirmed Major.

V3.1.3: CandidateWave before formal wave; confirm hold + MA20 up + HH/HL;
short shallow rebreaks → SUB or MERGED under parent Major.
MajorWaveConsumption vs SubWaveConsumption.
Wave event log: CANDIDATE/CONFIRMED/MERGED/FAILED/FAILED_CANDIDATE/COMPLETED/PROMOTED.

Wave create/end only from CONFIRMED/MAIN_RISE/NEW_WAVE (+ COOLING→NEW_WAVE).
Anchor = breakout/confirm/rebreakout based — never arbitrary hist trough.
禁止荐买 / 预测必涨. No symbol-specific branches. No lookahead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from v3.v31.config import V312_LIFECYCLE, V313_WAVE, V314_MAJOR_GATE
from v3.v31.types import WaveEventKind, WaveEventLogEntry, WaveInfo, WaveLevel


@dataclass
class TrendWaveResult:
    ok: bool = False
    wave: WaveInfo = field(default_factory=WaveInfo)
    session_consumption: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    events: List[WaveEventLogEntry] = field(default_factory=list)
    # internal carry for stateful callers
    peak_close: Optional[float] = None
    cooling_days: int = 0
    pivot_low: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["wave"] = self.wave.to_dict()
        d["events"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in self.events]
        return d


@dataclass
class _WaveCarry:
    wave_id: int = 0
    trend_id: int = 0
    anchor: Optional[float] = None
    anchor_date: Optional[str] = None
    anchor_kind: Optional[str] = None
    anchor_valid: bool = False
    age_days: int = 0
    peak_close: Optional[float] = None
    cooling_days: int = 0
    consolidation_bars: int = 0
    pivot_low: Optional[float] = None
    wave_cons: float = 0.0
    trend_cons: float = 0.0
    active: bool = False
    # V3.1.3 hierarchy
    wave_level: str = WaveLevel.NONE.value
    major_wave_id: int = 0
    parent_major_id: int = 0
    candidate_id: int = 0
    major_cons: float = 0.0
    sub_cons: float = 0.0
    candidate_hold_days: int = 0
    candidate_active: bool = False
    candidate_pivot: Optional[float] = None
    candidate_peak: Optional[float] = None
    candidate_anchor: Optional[float] = None
    candidate_anchor_date: Optional[str] = None
    candidate_pullback_depth: float = 0.0
    major_peak: Optional[float] = None
    major_start_close: Optional[float] = None
    max_wave_id: int = 0
    max_candidate_id: int = 0
    max_major_id: int = 0
    # V3.1.4 big-move gate
    peak_high_since_anchor: Optional[float] = None
    peak_close_since_anchor: Optional[float] = None
    structure_confirmed: bool = False
    major_promoted: bool = False
    is_failed_candidate: bool = False
    developing_from_confirm: bool = False  # first-wave Candidate from create_wave
    confirmed_major_exists: bool = False  # True only after gate promotion


class TrendWaveEngine:
    """
    Stateful wave tracker for V3.1.4 (Major gate) + V3.1.3 (Major/Sub) + V3.1.2 anchors.

    Stateless ``evaluate(hist, ...)`` retained for V3.1.1 compat (legacy).
    Regression V3.1.4 should use ``step(...)``.
    """

    def __init__(
        self,
        pullback_pct: float = 8.0,
        min_advance_pct: float = 5.0,
        lifecycle_cfg: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.pullback_pct = float(pullback_pct)
        self.min_advance_pct = float(min_advance_pct)
        self.cfg = dict(lifecycle_cfg or V312_LIFECYCLE)
        self.v313 = dict(self.cfg.get("v313_wave") or V313_WAVE)
        self.v314 = dict(self.cfg.get("v314_major_gate") or V314_MAJOR_GATE)
        self._carry = _WaveCarry()
        self._code6 = ""
        self._completed_waves: List[Dict[str, Any]] = []
        self._merged_records: List[Dict[str, Any]] = []
        self._all_events: List[WaveEventLogEntry] = []

    def reset(self) -> None:
        self._carry = _WaveCarry()
        self._completed_waves = []
        self._merged_records = []
        self._all_events = []

    def seed_from_snapshot(
        self,
        *,
        trend_id: int,
        wave_id: int,
        anchor: Optional[float],
        anchor_date: Optional[str],
        anchor_kind: Optional[str],
        anchor_valid: bool,
        age_days: int,
        peak_close: Optional[float],
        cooling_days: int,
        pivot_low: Optional[float],
        wave_cons: Optional[float],
        trend_cons: Optional[float],
        active: bool,
        wave_level: str = WaveLevel.NONE.value,
        major_wave_id: int = 0,
        parent_major_id: int = 0,
        candidate_id: int = 0,
        major_cons: Optional[float] = None,
        sub_cons: Optional[float] = None,
    ) -> None:
        self._carry = _WaveCarry(
            wave_id=int(wave_id),
            trend_id=int(trend_id),
            anchor=anchor,
            anchor_date=anchor_date,
            anchor_kind=anchor_kind,
            anchor_valid=bool(anchor_valid),
            age_days=int(age_days),
            peak_close=peak_close,
            cooling_days=int(cooling_days),
            pivot_low=pivot_low,
            wave_cons=float(wave_cons or 0.0),
            trend_cons=float(trend_cons or 0.0),
            active=bool(active),
            wave_level=str(wave_level or WaveLevel.NONE.value),
            major_wave_id=int(major_wave_id),
            parent_major_id=int(parent_major_id),
            candidate_id=int(candidate_id),
            major_cons=float(major_cons if major_cons is not None else (wave_cons or 0.0)),
            sub_cons=float(sub_cons or 0.0),
            max_wave_id=int(wave_id),
            max_major_id=int(major_wave_id or wave_id),
            max_candidate_id=int(candidate_id),
            major_peak=peak_close,
        )

    def on_new_trend(self, trend_id: int) -> None:
        """Retire waves on new TrendId — never reuse across FAILURE."""
        self._carry = _WaveCarry(trend_id=int(trend_id), wave_id=0, active=False)

    def on_trend_failure(self, t_date: str = "") -> None:
        c = self._carry
        td = t_date or c.anchor_date or ""
        under_major = bool(c.confirmed_major_exists and c.major_wave_id > 0)
        # V3.1.4: never-promoted Developing Candidate → FailedCandidate (not Major)
        if c.candidate_active and not under_major:
            c.is_failed_candidate = True
            ev = self._event(
                td,
                WaveEventKind.FAILED_CANDIDATE.value,
                "TREND_FAILURE_BEFORE_MAJOR_GATE",
                wave_level=WaveLevel.CANDIDATE.value,
            )
            self._all_events.append(ev)
            self._completed_waves.append(
                self._snapshot_record(end=td, status="FAILED_CANDIDATE")
            )
        elif c.candidate_active and under_major:
            # Pending Sub/Candidate under Major — merge then fail parent Major
            ev = self._event(
                td,
                WaveEventKind.MERGED.value,
                f"TREND_FAILURE pending Candidate→MERGED into major={c.major_wave_id}",
                wave_level=WaveLevel.MERGED.value,
            )
            self._all_events.append(ev)
            self._merged_records.append(
                {
                    "trend_id": c.trend_id,
                    "candidate_id": c.candidate_id,
                    "parent_major_id": c.major_wave_id,
                    "wave_id": c.wave_id,
                    "start": c.candidate_anchor_date,
                    "end": td,
                    "anchor": c.candidate_anchor,
                    "depth": c.candidate_pullback_depth,
                    "reason": "TREND_FAILURE_PENDING_CANDIDATE",
                    "status": "MERGED",
                }
            )
            c.wave_level = WaveLevel.MAJOR.value
            c.active = True
            c.candidate_active = False
            self._emit_completed_or_failed(
                t_date=td,
                kind=WaveEventKind.FAILED.value,
                reason="TREND_FAILURE",
            )
        elif c.active and c.wave_level in (
            WaveLevel.MAJOR.value,
            WaveLevel.SUB.value,
        ):
            self._emit_completed_or_failed(
                t_date=td,
                kind=WaveEventKind.FAILED.value,
                reason="TREND_FAILURE",
            )
        c.active = False
        c.anchor_valid = False
        c.cooling_days = 0
        c.consolidation_bars = 0
        c.candidate_active = False
        c.candidate_hold_days = 0
        c.structure_confirmed = False
        c.developing_from_confirm = False
        c.confirmed_major_exists = False
        c.major_promoted = False
        c.wave_level = WaveLevel.NONE.value

    def step(
        self,
        *,
        t_date: str,
        close: Optional[float],
        low: Optional[float],
        state: str,
        prior_state: Optional[str],
        is_new_wave_event: bool,
        create_wave: bool,
        session_consumption: Optional[float] = None,
        breakout_ref: Optional[float] = None,
        confirm_ref: Optional[float] = None,
        ma20_up: bool = False,
        hh_hl_ok: bool = True,
        code6: str = "",
        high: Optional[float] = None,
    ) -> TrendWaveResult:
        """
        Advance wave state for one day given decided TrendState.

        create_wave: start Developing Candidate (V3.1.4; Major only after big gate).
        is_new_wave_event: COOLING→NEW_WAVE rebreakout → start CandidateWave.
        high: as-of bar high for PeakGainFromAnchor (no lookahead).
        """
        if code6:
            self._code6 = str(code6).zfill(6)
        tags: List[str] = []
        notes: List[str] = []
        events: List[WaveEventLogEntry] = []
        c = self._carry
        wave_cfg = dict(self.cfg.get("wave") or {})
        v313 = self.v313
        v314 = self.v314
        cand_cfg = dict(v313.get("candidate") or {})
        major_cfg = dict(v313.get("major") or {})
        sub_cfg = dict(v313.get("sub") or {})
        cons_cfg = dict(v313.get("consumption") or {})
        # V3.1.5: when gate_drives_live=False, demote peak gate — structure
        # confirm promotes Major (V3.1.3 behavior); 30%/20d lives in label engine only.
        gate_drives_live = bool(v314.get("gate_drives_live", True))
        if gate_drives_live:
            gate_min_gain = float(v314.get("MAJOR_MIN_PEAK_GAIN_PCT", 0.30))
            gate_min_age = int(v314.get("MAJOR_MIN_AGE_DAYS", 20))
        else:
            gate_min_gain = 0.0
            gate_min_age = 0
        reset_cap = float(
            cons_cfg.get("major_reset_cap")
            or wave_cfg.get("wave_cons_reset_cap", 35.0)
        )
        sub_reset_cap = float(cons_cfg.get("sub_reset_cap", 35.0))

        if close is not None:
            if c.peak_close is None or close > c.peak_close:
                c.peak_close = float(close)
            # Major peak tracks across MAJOR/SUB/CANDIDATE under same major_wave_id
            if c.major_wave_id > 0 and (c.major_peak is None or close > c.major_peak):
                c.major_peak = float(close)
            if c.pivot_low is None or (low is not None and low < c.pivot_low):
                if state in ("COOLING", "OBSERVE") or c.cooling_days > 0:
                    if low is not None:
                        c.pivot_low = float(low)
        # V3.1.4 PeakGainFromAnchor tracking (as-of ≤ t only)
        if c.anchor is not None or c.candidate_anchor is not None:
            self._update_peaks_since_anchor(close=close, high=high)

        # End wave on failure handled by caller via on_trend_failure
        if state == "TREND_FAILURE":
            # Caller may have already invoked on_trend_failure(t_date); only emit if still active
            if c.active or c.candidate_active:
                self.on_trend_failure(t_date=t_date)
            notes.append("wave ended on TREND_FAILURE")
            return TrendWaveResult(
                ok=True,
                wave=self._to_wave(is_new=False),
                session_consumption=session_consumption,
                tags=["WAVE_END_FAILURE"],
                notes=notes,
                events=events,
                peak_close=c.peak_close,
                cooling_days=c.cooling_days,
                pivot_low=c.pivot_low,
            )

        # --- Create initial Developing Candidate on confirm (V3.1.4: NOT yet Major) ---
        if create_wave and not c.active and not c.candidate_active:
            evs = self._birth_developing_candidate(
                t_date=t_date,
                close=close,
                low=low,
                high=high,
                breakout_ref=breakout_ref,
                confirm_ref=confirm_ref,
                session_consumption=session_consumption,
                reset_cap=reset_cap,
                kind_pref="BREAKOUT",
                reason="TREND_CONFIRM→CANDIDATE",
            )
            events.extend(evs)
            tags.append("WAVE_CREATE")
            tags.append("CANDIDATE_START")
            notes.append(
                f"candidate_id={c.candidate_id} wave_id={c.wave_id} "
                f"anchor={c.anchor} kind={c.anchor_kind} valid={c.anchor_valid} "
                f"(Major gate: gain≥{gate_min_gain:.0%} age≥{gate_min_age})"
            )

        # --- COOLING rebreakout → CandidateWave (not immediate formal Major) ---
        # V3.1.4: if still Developing Candidate (no confirmed Major), merge/continue — no Sub.
        elif is_new_wave_event and c.candidate_active and not c.confirmed_major_exists:
            # Rebreak while still Candidate: stay Candidate / merge (do not invent Major/Sub)
            tags.append("CANDIDATE_REBREAK_MERGE")
            notes.append(
                "rebreakout while Developing Candidate — stay Candidate (no Sub without Major)"
            )
            c.cooling_days = 0
            c.consolidation_bars = 0
            # soft-refresh hold so structure re-confirm can proceed
            if not c.structure_confirmed:
                c.candidate_hold_days = 0
            ev = self._event(
                t_date,
                WaveEventKind.MERGED.value,
                "REBREAK_WHILE_CANDIDATE→stay Candidate",
                wave_level=WaveLevel.CANDIDATE.value,
            )
            events.append(ev)
            self._all_events.append(ev)
            self._merged_records.append(
                {
                    "trend_id": c.trend_id,
                    "candidate_id": c.candidate_id,
                    "parent_major_id": 0,
                    "wave_id": c.wave_id,
                    "start": c.anchor_date,
                    "end": t_date,
                    "anchor": c.anchor,
                    "depth": c.candidate_pullback_depth,
                    "reason": "REBREAK_WHILE_CANDIDATE",
                    "status": "MERGED",
                }
            )

        elif is_new_wave_event and (c.active or c.confirmed_major_exists or c.major_wave_id > 0) and not c.candidate_active:
            # If current SUB is short-lived, MERGED into parent before new candidate
            sub_cfg_pre = dict(v313.get("sub") or {})
            merge_age = int(sub_cfg_pre.get("merge_if_age_lt", 5))
            if (
                c.wave_level == WaveLevel.SUB.value
                and c.parent_major_id > 0
                and c.age_days < merge_age
            ):
                rec = {
                    "trend_id": c.trend_id,
                    "candidate_id": c.candidate_id,
                    "parent_major_id": c.parent_major_id,
                    "wave_id": c.wave_id,
                    "start": c.anchor_date,
                    "end": t_date,
                    "anchor": c.anchor,
                    "depth": c.candidate_pullback_depth,
                    "reason": f"SHORT_SUB_MERGE age={c.age_days}<{merge_age}",
                    "status": "MERGED",
                }
                self._merged_records.append(rec)
                ev = self._event(
                    t_date,
                    WaveEventKind.MERGED.value,
                    rec["reason"],
                    wave_level=WaveLevel.MERGED.value,
                )
                events.append(ev)
                self._all_events.append(ev)
                tags.append("SUB_MERGED")
                # restore parent major display
                c.wave_level = WaveLevel.MAJOR.value
                c.wave_id = c.major_wave_id  # display major as active wave
                c.parent_major_id = 0
                c.age_days = max(c.age_days, 1)
            # Eligibility already gated by TrendStateEngine._rebreakout_ok
            # (cooling≥5 + pullback≥8%). Also accept consol≥5 path.
            min_cool = int(cand_cfg.get("min_cooling_days", 5))
            min_pb = float(cand_cfg.get("min_pullback_pct", 8.0))
            min_consol = int(cand_cfg.get("min_consolidation_bars", 5))
            peak = c.peak_close or c.major_peak
            pivot = c.pivot_low
            depth = 0.0
            if peak and pivot and peak > 0:
                depth = (peak - pivot) / peak * 100.0
            consol_ok = c.consolidation_bars >= min_consol or c.cooling_days >= min_consol
            eligible = c.cooling_days >= min_cool and (depth >= min_pb or consol_ok)
            if eligible:
                c.max_candidate_id += 1
                c.candidate_id = c.max_candidate_id
                c.candidate_active = True
                c.candidate_hold_days = 0
                # V3.1.4: must re-run V3.1.3 structure confirm for Sub/new-Major
                c.structure_confirmed = False
                c.developing_from_confirm = False
                c.candidate_pullback_depth = depth
                c.candidate_pivot = pivot
                c.candidate_peak = peak
                if low is not None:
                    c.candidate_anchor = float(low)
                elif close is not None:
                    c.candidate_anchor = float(close)
                c.candidate_anchor_date = t_date
                # Display level while candidate pending
                c.wave_level = WaveLevel.CANDIDATE.value
                base = float(session_consumption) if session_consumption is not None else 0.0
                c.sub_cons = float(min(base, sub_reset_cap))
                tags.append("CANDIDATE_START")
                notes.append(
                    f"candidate_id={c.candidate_id} depth={depth:.1f}% "
                    f"cooling={c.cooling_days} consol={c.consolidation_bars}"
                )
                ev = self._event(
                    t_date,
                    WaveEventKind.CANDIDATE.value,
                    f"cooling_rebreakout depth={depth:.1f}% cooling={c.cooling_days}",
                    wave_level=WaveLevel.CANDIDATE.value,
                )
                events.append(ev)
                self._all_events.append(ev)
            else:
                tags.append("CANDIDATE_BLOCKED")
                notes.append(
                    f"rebreakout not eligible for candidate "
                    f"(cool={c.cooling_days}<{min_cool} or depth/consol)"
                )

        # --- Advance active / candidate ---
        elif c.candidate_active:
            c.candidate_hold_days += 1
            # Developing Candidate ages from WaveAnchor; under Major continue parent age
            if c.developing_from_confirm or not c.confirmed_major_exists:
                c.age_days += 1
            elif c.confirmed_major_exists:
                c.age_days += 1  # keep Major age continuous through pending Candidate
            if close is not None and (
                c.candidate_peak is None or close > c.candidate_peak
            ):
                c.candidate_peak = float(close)
            hold_need = int(cand_cfg.get("confirm_hold_days", 2))
            require_ma = bool(cand_cfg.get("require_ma20_up", True))
            require_hh = bool(cand_cfg.get("require_hh_hl", True))
            struct_ok = (not require_ma or ma20_up) and (not require_hh or hh_hl_ok)
            peak_gain = self._peak_gain_from_anchor()

            # Fail candidate if structure breaks during hold (before structure_confirmed)
            if (not c.structure_confirmed) and (not hh_hl_ok) and require_hh:
                if c.confirmed_major_exists:
                    evs = self._fail_or_merge_candidate(
                        t_date=t_date,
                        reason="CANDIDATE_HH_HL_BREAK",
                        session_consumption=session_consumption,
                        sub_reset_cap=sub_reset_cap,
                    )
                    events.extend(evs)
                    tags.append("CANDIDATE_FAILED")
                elif c.developing_from_confirm:
                    # Developing first-wave: HH/HL stress delays confirm; TrendFailure still ends it
                    tags.append("CANDIDATE_HOLD")
                    notes.append(
                        f"developing HH/HL stress hold={c.candidate_hold_days} "
                        f"peak_gain={peak_gain:.2%} age={c.age_days}"
                    )
                else:
                    evs = self._fail_or_merge_candidate(
                        t_date=t_date,
                        reason="CANDIDATE_HH_HL_BREAK",
                        session_consumption=session_consumption,
                        sub_reset_cap=sub_reset_cap,
                    )
                    events.extend(evs)
                    tags.append("CANDIDATE_FAILED")
            elif (
                (not c.structure_confirmed)
                and require_ma
                and not ma20_up
                and c.candidate_hold_days >= hold_need
            ):
                if c.developing_from_confirm and not c.confirmed_major_exists:
                    # First-wave Developing: delay structure_confirm until MA20 up
                    # (do not kill the whole Candidate on a brief MA20 dip)
                    tags.append("CANDIDATE_HOLD")
                    notes.append(
                        f"developing await MA20 up hold={c.candidate_hold_days} "
                        f"peak_gain={peak_gain:.2%} age={c.age_days}"
                    )
                else:
                    evs = self._fail_or_merge_candidate(
                        t_date=t_date,
                        reason="CANDIDATE_MA20_NOT_UP",
                        session_consumption=session_consumption,
                        sub_reset_cap=sub_reset_cap,
                    )
                    events.extend(evs)
                    tags.append("CANDIDATE_FAILED")
            elif (not c.structure_confirmed) and c.candidate_hold_days >= hold_need and struct_ok:
                # V3.1.3 structure confirm achieved — still Candidate until Major gate
                c.structure_confirmed = True
                tags.append("CANDIDATE_STRUCTURE_OK")
                notes.append(
                    f"structure confirmed hold={c.candidate_hold_days} "
                    f"peak_gain={peak_gain:.2%} age={c.age_days} "
                    f"(gate gain≥{gate_min_gain:.0%} age≥{gate_min_age})"
                )
                ev = self._event(
                    t_date,
                    WaveEventKind.CONFIRMED.value,
                    f"STRUCTURE_CONFIRMED peak_gain={peak_gain:.2%} age={c.age_days}",
                    wave_level=WaveLevel.CANDIDATE.value,
                )
                events.append(ev)
                self._all_events.append(ev)
                # If parent Major exists and this is a cooling rebreak → may become SUB now
                if c.confirmed_major_exists:
                    evs = self._promote_candidate(
                        t_date=t_date,
                        close=close,
                        low=low,
                        session_consumption=session_consumption,
                        reset_cap=reset_cap,
                        sub_reset_cap=sub_reset_cap,
                        major_cfg=major_cfg,
                        sub_cfg=sub_cfg,
                        gate_min_gain=gate_min_gain,
                        gate_min_age=gate_min_age,
                    )
                    events.extend(evs)
                    tags.append("CANDIDATE_CONFIRMED")
                    notes.append(
                        f"promoted→{c.wave_level} wave_id={c.wave_id} "
                        f"major={c.major_wave_id} parent={c.parent_major_id}"
                    )
                else:
                    # Try Major gate immediately if already satisfied
                    evs = self._try_major_gate(
                        t_date=t_date,
                        close=close,
                        session_consumption=session_consumption,
                        reset_cap=reset_cap,
                        gate_min_gain=gate_min_gain,
                        gate_min_age=gate_min_age,
                    )
                    events.extend(evs)
                    if c.major_promoted:
                        tags.append("MAJOR_PROMOTED")
                        tags.append("CANDIDATE_CONFIRMED")
            elif c.structure_confirmed and not c.major_promoted and not c.confirmed_major_exists:
                # Developing Candidate awaiting big-move gate
                if not hh_hl_ok and require_hh:
                    # Post-structure HH/HL break: do not auto-fail (trend_state handles FAILURE).
                    tags.append("CANDIDATE_STRUCT_STRESS")
                    notes.append("HH/HL stress while awaiting Major gate")
                evs = self._try_major_gate(
                    t_date=t_date,
                    close=close,
                    session_consumption=session_consumption,
                    reset_cap=reset_cap,
                    gate_min_gain=gate_min_gain,
                    gate_min_age=gate_min_age,
                )
                events.extend(evs)
                if c.major_promoted:
                    tags.append("MAJOR_PROMOTED")
                    tags.append("CANDIDATE_CONFIRMED")
                    notes.append(
                        f"gate→MAJOR wave_id={c.wave_id} major={c.major_wave_id} "
                        f"peak_gain={peak_gain:.2%} age={c.age_days}"
                    )
                else:
                    tags.append("CANDIDATE_AWAIT_GATE")
                    notes.append(
                        f"await Major gate peak_gain={peak_gain:.2%}/{gate_min_gain:.0%} "
                        f"age={c.age_days}/{gate_min_age}"
                    )
            else:
                tags.append("CANDIDATE_HOLD")
                notes.append(
                    f"candidate hold {c.candidate_hold_days}/{hold_need} "
                    f"ma20_up={ma20_up} hh_hl={hh_hl_ok} "
                    f"peak_gain={peak_gain:.2%} age={c.age_days}"
                )
            # consumption while candidate
            if session_consumption is not None:
                c.sub_cons = float(session_consumption)
                c.wave_cons = float(session_consumption)
            # Track cooling while Developing/Candidate so post-promote rebreak gates work
            if state == "COOLING":
                c.cooling_days += 1
                c.consolidation_bars += 1
                if low is not None:
                    if c.pivot_low is None or low < c.pivot_low:
                        c.pivot_low = float(low)

        elif c.active:
            c.age_days += 1
            if session_consumption is not None:
                c.wave_cons = float(session_consumption)
                if c.wave_level == WaveLevel.MAJOR.value:
                    c.major_cons = float(session_consumption)
                    c.sub_cons = 0.0
                elif c.wave_level == WaveLevel.SUB.value:
                    c.sub_cons = float(session_consumption)
                    # Major cons: soft blend toward session (parent continues)
                    prev = float(c.major_cons or 0.0)
                    c.major_cons = round(0.7 * prev + 0.3 * float(session_consumption), 1)
            if state == "COOLING":
                c.cooling_days += 1
                c.consolidation_bars += 1
                if low is not None:
                    if c.pivot_low is None or low < c.pivot_low:
                        c.pivot_low = float(low)
            else:
                if state in ("MAIN_RISE", "ACCELERATION", "CONFIRMED", "NEW_WAVE") and prior_state == "COOLING":
                    # resumed without formal NEW_WAVE / candidate — clear cooling
                    if not c.candidate_active:
                        c.cooling_days = 0
                        c.consolidation_bars = 0
            if c.anchor is not None:
                c.anchor_valid = self._validate_anchor(c.anchor, close)
            else:
                c.anchor_valid = False
                tags.append("ANCHOR_MISSING")
            # Update major peak while in major/sub under parent
            if close is not None and c.major_wave_id > 0:
                if c.major_peak is None or close > c.major_peak:
                    c.major_peak = float(close)

        # Trend consumption accumulates while trend active
        if session_consumption is not None and c.trend_id > 0:
            prev = float(c.trend_cons or 0.0)
            c.trend_cons = round(0.7 * prev + 0.3 * float(session_consumption), 1)
        elif c.trend_id > 0 and c.wave_cons:
            c.trend_cons = max(float(c.trend_cons or 0.0), float(c.wave_cons))

        is_new = bool(
            "WAVE_CREATE" in tags
            or "MAJOR_CREATE" in tags
            or "MAJOR_PROMOTED" in tags
            or "CANDIDATE_CONFIRMED" in tags
            or "CANDIDATE_START" in tags
        )
        return TrendWaveResult(
            ok=True,
            wave=self._to_wave(is_new=is_new),
            session_consumption=session_consumption,
            tags=tags,
            notes=notes,
            events=events,
            peak_close=c.peak_close,
            cooling_days=c.cooling_days,
            pivot_low=c.pivot_low,
        )

    # ------------------------------------------------------------------
    # Hierarchy helpers (+ V3.1.4 Major gate)
    # ------------------------------------------------------------------
    def _update_peaks_since_anchor(
        self, *, close: Optional[float], high: Optional[float]
    ) -> None:
        """As-of peak high / close since WaveAnchor (no lookahead)."""
        c = self._carry
        if high is not None:
            if c.peak_high_since_anchor is None or float(high) > c.peak_high_since_anchor:
                c.peak_high_since_anchor = float(high)
        if close is not None:
            if c.peak_close_since_anchor is None or float(close) > c.peak_close_since_anchor:
                c.peak_close_since_anchor = float(close)

    def _peak_px_from_anchor(self) -> Optional[float]:
        c = self._carry
        vals = [v for v in (c.peak_high_since_anchor, c.peak_close_since_anchor) if v is not None]
        return max(vals) if vals else None

    def _peak_gain_from_anchor(self) -> float:
        """PeakGainFromAnchor as fraction (0.30 = 30%). Uses anchor or candidate_anchor."""
        c = self._carry
        anchor = c.anchor if c.anchor and c.anchor > 0 else None
        if anchor is None and c.candidate_anchor and c.candidate_anchor > 0:
            anchor = float(c.candidate_anchor)
        peak = self._peak_px_from_anchor()
        if anchor is None or peak is None or anchor <= 0:
            return 0.0
        return float(peak) / float(anchor) - 1.0

    def _birth_developing_candidate(
        self,
        *,
        t_date: str,
        close: Optional[float],
        low: Optional[float],
        high: Optional[float],
        breakout_ref: Optional[float],
        confirm_ref: Optional[float],
        session_consumption: Optional[float],
        reset_cap: float,
        kind_pref: str,
        reason: str,
    ) -> List[WaveEventLogEntry]:
        """V3.1.4: first confirm → Developing Candidate (not Major until gate)."""
        c = self._carry
        events: List[WaveEventLogEntry] = []
        c.max_wave_id += 1
        c.max_candidate_id += 1
        c.wave_id = c.max_wave_id
        c.candidate_id = c.max_candidate_id
        c.major_wave_id = 0
        c.parent_major_id = 0
        c.candidate_active = True
        c.candidate_hold_days = 0
        c.structure_confirmed = False
        c.major_promoted = False
        c.confirmed_major_exists = False
        c.is_failed_candidate = False
        c.developing_from_confirm = True
        c.active = False  # formal active only after Major/Sub; candidate_active drives display
        c.age_days = 0
        c.cooling_days = 0
        c.consolidation_bars = 0
        c.wave_level = WaveLevel.CANDIDATE.value

        anchor = breakout_ref if breakout_ref and breakout_ref > 0 else None
        kind = kind_pref
        if anchor is None:
            anchor = confirm_ref if confirm_ref and confirm_ref > 0 else None
            kind = "CONFIRM"
        if anchor is None and close is not None:
            anchor = float(low) if low is not None else float(close)
            kind = "CONFIRM"
        c.anchor = float(anchor) if anchor is not None else None
        c.anchor_date = t_date
        c.anchor_kind = kind
        c.anchor_valid = self._validate_anchor(c.anchor, close)
        c.candidate_anchor = c.anchor
        c.candidate_anchor_date = t_date
        c.candidate_pivot = float(low) if low is not None else c.anchor
        c.peak_close = float(close) if close is not None else None
        c.candidate_peak = c.peak_close
        c.major_peak = None
        c.major_start_close = None
        c.pivot_low = float(low) if low is not None else c.peak_close
        c.peak_high_since_anchor = float(high) if high is not None else None
        c.peak_close_since_anchor = float(close) if close is not None else None
        # if high missing, seed peak_high with close
        if c.peak_high_since_anchor is None and close is not None:
            c.peak_high_since_anchor = float(close)
        base = float(session_consumption) if session_consumption is not None else 0.0
        c.wave_cons = float(min(base, reset_cap))
        c.major_cons = 0.0
        c.sub_cons = c.wave_cons

        ev = self._event(
            t_date,
            WaveEventKind.CANDIDATE.value,
            reason,
            wave_level=WaveLevel.CANDIDATE.value,
        )
        events.append(ev)
        self._all_events.append(ev)
        return events

    def _try_major_gate(
        self,
        *,
        t_date: str,
        close: Optional[float],
        session_consumption: Optional[float],
        reset_cap: float,
        gate_min_gain: float,
        gate_min_age: int,
    ) -> List[WaveEventLogEntry]:
        """Promote Developing Candidate → Major when PeakGain≥gate AND Age≥gate (as-of)."""
        c = self._carry
        events: List[WaveEventLogEntry] = []
        if c.major_promoted or c.confirmed_major_exists:
            return events
        if not c.structure_confirmed:
            return events
        peak_gain = self._peak_gain_from_anchor()
        if peak_gain < gate_min_gain or int(c.age_days) < gate_min_age:
            return events

        # Promote in place — keep WaveAnchor / age continuity
        c.max_major_id += 1
        c.major_wave_id = c.max_major_id
        c.parent_major_id = 0
        c.wave_level = WaveLevel.MAJOR.value
        c.active = True
        c.candidate_active = False
        c.major_promoted = True
        c.confirmed_major_exists = True
        c.developing_from_confirm = False
        c.major_peak = self._peak_px_from_anchor() or c.peak_close
        c.major_start_close = float(close) if close is not None else c.peak_close
        base = float(session_consumption) if session_consumption is not None else float(c.wave_cons or 0.0)
        c.wave_cons = float(min(base, reset_cap))
        c.major_cons = c.wave_cons
        c.sub_cons = 0.0
        c.cooling_days = 0
        c.consolidation_bars = 0
        c.anchor_valid = self._validate_anchor(c.anchor, close)

        ev = self._event(
            t_date,
            WaveEventKind.PROMOTED.value,
            f"CANDIDATE→MAJOR_GATE peak_gain={peak_gain:.2%} age={c.age_days}",
            wave_level=WaveLevel.MAJOR.value,
        )
        events.append(ev)
        self._all_events.append(ev)
        return events

    def _birth_major(
        self,
        *,
        t_date: str,
        close: Optional[float],
        low: Optional[float],
        breakout_ref: Optional[float],
        confirm_ref: Optional[float],
        session_consumption: Optional[float],
        reset_cap: float,
        kind_pref: str,
        reason: str,
    ) -> List[WaveEventLogEntry]:
        c = self._carry
        # Complete prior major if any
        events: List[WaveEventLogEntry] = []
        if c.active and c.wave_level == WaveLevel.MAJOR.value:
            ev = self._event(
                t_date,
                WaveEventKind.COMPLETED.value,
                "prior_major_completed_on_new_major",
                wave_level=WaveLevel.MAJOR.value,
            )
            events.append(ev)
            self._all_events.append(ev)
            self._completed_waves.append(self._snapshot_record(end=t_date, status="COMPLETED"))

        c.max_wave_id += 1
        c.max_major_id += 1
        c.wave_id = c.max_wave_id
        c.major_wave_id = c.max_major_id
        c.parent_major_id = 0
        c.candidate_id = 0
        c.candidate_active = False
        c.candidate_hold_days = 0
        c.active = True
        c.age_days = 0
        c.cooling_days = 0
        c.consolidation_bars = 0
        c.peak_close = float(close) if close is not None else None
        c.major_peak = c.peak_close
        c.major_start_close = c.peak_close
        c.pivot_low = float(low) if low is not None else c.peak_close
        c.wave_level = WaveLevel.MAJOR.value

        anchor = breakout_ref if breakout_ref and breakout_ref > 0 else None
        kind = kind_pref
        if anchor is None:
            anchor = confirm_ref if confirm_ref and confirm_ref > 0 else None
            kind = "CONFIRM"
        if anchor is None and close is not None:
            anchor = float(low) if low is not None else float(close)
            kind = "CONFIRM"
        c.anchor = float(anchor) if anchor is not None else None
        c.anchor_date = t_date
        c.anchor_kind = kind
        c.anchor_valid = self._validate_anchor(c.anchor, close)
        base = float(session_consumption) if session_consumption is not None else 0.0
        c.wave_cons = float(min(base, reset_cap))
        c.major_cons = c.wave_cons
        c.sub_cons = 0.0

        ev = self._event(
            t_date,
            WaveEventKind.CONFIRMED.value,
            reason,
            wave_level=WaveLevel.MAJOR.value,
        )
        events.append(ev)
        self._all_events.append(ev)
        return events

    def _promote_candidate(
        self,
        *,
        t_date: str,
        close: Optional[float],
        low: Optional[float],
        session_consumption: Optional[float],
        reset_cap: float,
        sub_reset_cap: float,
        major_cfg: Dict[str, Any],
        sub_cfg: Dict[str, Any],
        gate_min_gain: float = 0.30,
        gate_min_age: int = 20,
    ) -> List[WaveEventLogEntry]:
        c = self._carry
        events: List[WaveEventLogEntry] = []
        depth = float(c.candidate_pullback_depth or 0.0)
        # Advance from candidate pivot so far
        pivot = c.candidate_pivot or c.candidate_anchor
        adv = 0.0
        if pivot and pivot > 0 and close is not None:
            adv = (float(close) / float(pivot) - 1.0) * 100.0

        min_depth_major = float(major_cfg.get("min_pullback_depth_for_new_major_pct", 12.0))
        min_adv_major = float(major_cfg.get("min_advance_for_new_major_pct", 12.0))
        shallow_adv = float(sub_cfg.get("shallow_advance_pct", 8.0))

        # V3.1.4: Sub only under existing *confirmed* Major (passed big gate)
        parent = int(c.major_wave_id or 0) if c.confirmed_major_exists else 0
        # V3.1.3: under active parent, default SUB/MERGED.
        # New MAJOR only on clear breakthrough above prior major peak
        # with deep pullback + strong reclaim (no lookahead) AND V3.1.4 gate.
        as_sub = False
        if parent > 0:
            peak = c.major_peak
            breakthrough_pct = float(major_cfg.get("breakthrough_above_peak_pct", 15.0))
            above_peak = bool(
                close is not None
                and peak is not None
                and peak > 0
                and float(close) >= float(peak) * (1.0 + breakthrough_pct / 100.0)
            )
            strong_new_major = (
                depth >= min_depth_major
                and adv >= min_adv_major
                and above_peak
            )
            as_sub = not strong_new_major
            # modest advance always SUB even if above peak
            if adv < shallow_adv:
                as_sub = True
        else:
            # No confirmed Major → cannot be Sub; remain Candidate / await gate
            as_sub = False

        if as_sub:
            # Keep parent major; birth SUB wave_id
            c.max_wave_id += 1
            c.wave_id = c.max_wave_id
            c.parent_major_id = parent
            c.wave_level = WaveLevel.SUB.value
            c.active = True
            c.age_days = 0
            c.cooling_days = 0
            c.consolidation_bars = 0
            c.anchor = c.candidate_anchor
            c.anchor_date = c.candidate_anchor_date or t_date
            c.anchor_kind = "REBREAKOUT"
            c.anchor_valid = self._validate_anchor(c.anchor, close)
            c.peak_close = float(close) if close is not None else c.peak_close
            c.pivot_low = c.candidate_pivot or c.anchor
            base = float(session_consumption) if session_consumption is not None else 0.0
            c.sub_cons = float(min(base, sub_reset_cap))
            c.wave_cons = c.sub_cons
            # major_cons continues (soft)
            if session_consumption is not None:
                prev = float(c.major_cons or 0.0)
                c.major_cons = round(0.7 * prev + 0.3 * float(session_consumption), 1)
            c.candidate_active = False
            c.candidate_hold_days = 0
            ev = self._event(
                t_date,
                WaveEventKind.CONFIRMED.value,
                f"CANDIDATE→SUB parent={parent} depth={depth:.1f}% adv={adv:.1f}%",
                wave_level=WaveLevel.SUB.value,
            )
            events.append(ev)
            self._all_events.append(ev)
        else:
            # Want new MAJOR — V3.1.4 gate required (PeakGain + Age as-of)
            # Switch tracking to this candidate's anchor for gate measurement
            c.anchor = c.candidate_anchor
            c.anchor_date = c.candidate_anchor_date or t_date
            c.anchor_kind = "REBREAKOUT"
            c.anchor_valid = self._validate_anchor(c.anchor, close)
            c.peak_close = float(close) if close is not None else c.peak_close
            c.pivot_low = c.candidate_pivot or c.anchor
            # Reset age/peaks for *new* Major candidate from rebreakout anchor
            if parent > 0:
                c.age_days = max(int(c.candidate_hold_days), 1)
                c.peak_high_since_anchor = float(close) if close is not None else None
                c.peak_close_since_anchor = float(close) if close is not None else None
                c.structure_confirmed = True
                c.developing_from_confirm = True  # treat as developing toward new Major
                c.candidate_active = True
                c.wave_level = WaveLevel.CANDIDATE.value
                c.active = False
                peak_gain = self._peak_gain_from_anchor()
                if peak_gain >= gate_min_gain and int(c.age_days) >= gate_min_age:
                    # Complete prior major then promote
                    if c.confirmed_major_exists:
                        ev = self._event(
                            t_date,
                            WaveEventKind.COMPLETED.value,
                            "prior_major_completed_on_deep_rebreak",
                            wave_level=WaveLevel.MAJOR.value,
                        )
                        events.append(ev)
                        self._all_events.append(ev)
                        self._completed_waves.append(
                            self._snapshot_record(end=t_date, status="COMPLETED")
                        )
                    # allocate new wave id for the new major
                    c.max_wave_id += 1
                    c.wave_id = c.max_wave_id
                    evs = self._try_major_gate(
                        t_date=t_date,
                        close=close,
                        session_consumption=session_consumption,
                        reset_cap=reset_cap,
                        gate_min_gain=gate_min_gain,
                        gate_min_age=gate_min_age,
                    )
                    events.extend(evs)
                else:
                    # Stay Candidate awaiting gate; do not invent Major
                    c.max_wave_id += 1
                    c.wave_id = c.max_wave_id
                    tags_note = (
                        f"DEEP_REBREAK→CANDIDATE await gate "
                        f"gain={peak_gain:.2%} age={c.age_days}"
                    )
                    ev = self._event(
                        t_date,
                        WaveEventKind.CONFIRMED.value,
                        tags_note,
                        wave_level=WaveLevel.CANDIDATE.value,
                    )
                    events.append(ev)
                    self._all_events.append(ev)
            else:
                # No parent — developing candidate path (should use _try_major_gate)
                peak_gain = self._peak_gain_from_anchor()
                c.structure_confirmed = True
                if peak_gain >= gate_min_gain and int(c.age_days) >= gate_min_age:
                    evs = self._try_major_gate(
                        t_date=t_date,
                        close=close,
                        session_consumption=session_consumption,
                        reset_cap=reset_cap,
                        gate_min_gain=gate_min_gain,
                        gate_min_age=gate_min_age,
                    )
                    events.extend(evs)
                else:
                    ev = self._event(
                        t_date,
                        WaveEventKind.CONFIRMED.value,
                        f"STRUCTURE_OK await Major gate gain={peak_gain:.2%} age={c.age_days}",
                        wave_level=WaveLevel.CANDIDATE.value,
                    )
                    events.append(ev)
                    self._all_events.append(ev)
        return events

    def _fail_or_merge_candidate(
        self,
        *,
        t_date: str,
        reason: str,
        session_consumption: Optional[float],
        sub_reset_cap: float,
    ) -> List[WaveEventLogEntry]:
        c = self._carry
        events: List[WaveEventLogEntry] = []
        parent = int(c.major_wave_id or 0)
        if parent > 0:
            # MERGED into parent
            rec = {
                "trend_id": c.trend_id,
                "candidate_id": c.candidate_id,
                "parent_major_id": parent,
                "wave_id": c.wave_id,
                "start": c.candidate_anchor_date,
                "end": t_date,
                "anchor": c.candidate_anchor,
                "depth": c.candidate_pullback_depth,
                "reason": reason,
                "status": "MERGED",
            }
            self._merged_records.append(rec)
            ev = self._event(
                t_date,
                WaveEventKind.MERGED.value,
                f"{reason} → MERGED into major={parent}",
                wave_level=WaveLevel.MERGED.value,
            )
            events.append(ev)
            self._all_events.append(ev)
            # Restore parent major display
            c.wave_level = WaveLevel.MAJOR.value
            c.parent_major_id = 0
            c.candidate_active = False
            c.candidate_hold_days = 0
            c.cooling_days = 0
            c.consolidation_bars = 0
            c.active = True
            if session_consumption is not None:
                c.major_cons = float(session_consumption)
                c.wave_cons = float(session_consumption)
        else:
            # No parent Major — FailedCandidate (never counts as Major)
            c.is_failed_candidate = True
            ev = self._event(
                t_date,
                WaveEventKind.FAILED_CANDIDATE.value,
                f"{reason}|FAILED_CANDIDATE",
                wave_level=WaveLevel.CANDIDATE.value,
            )
            events.append(ev)
            self._all_events.append(ev)
            self._completed_waves.append(
                self._snapshot_record(end=t_date, status="FAILED_CANDIDATE")
            )
            c.candidate_active = False
            c.candidate_hold_days = 0
            c.structure_confirmed = False
            c.developing_from_confirm = False
            c.wave_level = WaveLevel.NONE.value
            c.active = False
        return events

    def _emit_completed_or_failed(self, *, t_date: str, kind: str, reason: str) -> None:
        c = self._carry
        ev = self._event(t_date, kind, reason, wave_level=c.wave_level)
        self._all_events.append(ev)
        if kind == WaveEventKind.COMPLETED.value:
            self._completed_waves.append(self._snapshot_record(end=t_date, status="COMPLETED"))
        elif kind == WaveEventKind.FAILED.value:
            self._completed_waves.append(self._snapshot_record(end=t_date, status="FAILED"))

    def _snapshot_record(self, *, end: str, status: str) -> Dict[str, Any]:
        c = self._carry
        return {
            "trend_id": c.trend_id,
            "wave_id": c.wave_id,
            "major_wave_id": c.major_wave_id,
            "parent_major_id": c.parent_major_id,
            "wave_level": c.wave_level,
            "start": c.anchor_date,
            "end": end,
            "age": c.age_days,
            "anchor": c.anchor,
            "anchor_kind": c.anchor_kind,
            "anchor_valid": c.anchor_valid,
            "status": status,
            "peak_gain_from_anchor": round(self._peak_gain_from_anchor(), 4),
            "structure_confirmed": bool(c.structure_confirmed),
            "major_promoted": bool(c.major_promoted),
            "candidate_id": int(c.candidate_id),
        }

    def _event(
        self,
        t_date: str,
        kind: str,
        reason: str,
        *,
        wave_level: str,
    ) -> WaveEventLogEntry:
        c = self._carry
        return WaveEventLogEntry(
            t_date=t_date,
            code6=self._code6,
            kind=kind,
            reason=reason,
            trend_id=int(c.trend_id),
            wave_id=int(c.wave_id),
            major_wave_id=int(c.major_wave_id),
            parent_major_id=int(c.parent_major_id),
            candidate_id=int(c.candidate_id),
            wave_level=wave_level,
        )

    def bind_trend_id(self, trend_id: int) -> None:
        self._carry.trend_id = int(trend_id)

    def peak_close(self) -> Optional[float]:
        return self._carry.peak_close

    def cooling_days(self) -> int:
        return int(self._carry.cooling_days)

    def consolidation_bars(self) -> int:
        return int(self._carry.consolidation_bars)

    def pivot_low(self) -> Optional[float]:
        return self._carry.pivot_low

    def active(self) -> bool:
        return bool(self._carry.active or self._carry.candidate_active)

    def events(self) -> List[WaveEventLogEntry]:
        return list(self._all_events)

    def merged_records(self) -> List[Dict[str, Any]]:
        return list(self._merged_records)

    def pullback_pct_from_peak(self, close: Optional[float]) -> float:
        pk = self._carry.peak_close
        if pk is None or pk <= 0 or close is None:
            return 0.0
        return max(0.0, (pk - float(close)) / pk * 100.0)

    def _validate_anchor(self, anchor: Optional[float], close: Optional[float]) -> bool:
        """Anchor hygiene: reject missing/non-positive. Extreme ratio alone is OK for 大行情
        (V3.1.4); sticky-valid once set on birth — do not invalidate winners that run >5x.
        """
        if anchor is None or anchor <= 0:
            return False
        # Close sanity only rejects pathological near-zero closes vs anchor
        if close is not None and close > 0:
            ratio = float(close) / float(anchor)
            if ratio < 0.2:
                return False
        return True

    def _to_wave(self, is_new: bool) -> WaveInfo:
        c = self._carry
        level = c.wave_level
        if c.candidate_active and not c.major_promoted:
            level = WaveLevel.CANDIDATE.value
        active_like = c.active or c.candidate_active
        # After FailedCandidate / inactive NONE: do not expose stale wave_id
        wid = int(c.wave_id) if active_like or level in (
            WaveLevel.MAJOR.value, WaveLevel.SUB.value, WaveLevel.CANDIDATE.value
        ) else 0
        # V3.1.4: developing candidate age is WaveAge from anchor (age_days)
        if c.candidate_active and (c.developing_from_confirm or not c.confirmed_major_exists):
            age_out = int(c.age_days)
        elif c.candidate_active:
            age_out = int(c.candidate_hold_days)
        else:
            age_out = int(c.age_days)
        peak_gain = self._peak_gain_from_anchor() if (c.anchor or c.candidate_anchor) else None
        return WaveInfo(
            wave_id=int(wid),
            wave_age_days=age_out,
            wave_consumption=round(float(c.wave_cons), 1) if active_like else None,
            is_new_wave=bool(is_new),
            trend_id=int(c.trend_id),
            wave_anchor=c.candidate_anchor if c.candidate_active and not c.major_promoted else c.anchor,
            wave_anchor_date=(
                c.candidate_anchor_date if c.candidate_active and not c.major_promoted else c.anchor_date
            ),
            wave_anchor_kind=(
                ("REBREAKOUT" if c.candidate_active and not c.developing_from_confirm else c.anchor_kind)
                if c.candidate_active and not c.major_promoted
                else c.anchor_kind
            ),
            anchor_valid=(
                self._validate_anchor(
                    c.candidate_anchor if c.candidate_active and not c.major_promoted else c.anchor,
                    c.peak_close,
                )
                if active_like
                else False
            ),
            trend_consumption=round(float(c.trend_cons), 1) if c.trend_id > 0 else None,
            wave_level=level,
            major_wave_id=int(c.major_wave_id),
            parent_major_id=int(c.parent_major_id),
            candidate_id=int(c.candidate_id),
            major_wave_consumption=round(float(c.major_cons), 1) if c.major_wave_id > 0 else None,
            sub_wave_consumption=(
                round(float(c.sub_cons), 1)
                if (c.wave_level == WaveLevel.SUB.value or c.candidate_active)
                else None
            ),
            is_candidate=bool(c.candidate_active and not c.major_promoted),
            peak_gain_from_anchor=round(float(peak_gain), 4) if peak_gain is not None else None,
            peak_px_from_anchor=self._peak_px_from_anchor(),
            structure_confirmed=bool(c.structure_confirmed),
            is_failed_candidate=bool(c.is_failed_candidate),
            major_promoted=bool(c.major_promoted),
            notes=[
                f"wave_id={c.wave_id}",
                f"level={level}",
                f"major={c.major_wave_id}",
                f"age={c.age_days}",
                f"peak_gain={peak_gain:.2%}" if peak_gain is not None else "peak_gain=na",
                f"anchor={c.anchor}",
                f"kind={c.anchor_kind}",
            ],
        )

    # --- Legacy hist-scan evaluate (V3.1.1 compat) ---
    def evaluate(
        self,
        hist,
        *,
        t_date: Optional[str] = None,
        session_consumption: Optional[float] = None,
    ) -> TrendWaveResult:
        """Legacy path — prefer step() in V3.1.2/V3.1.3."""
        import numpy as np
        import pandas as pd

        if hist is None or not isinstance(hist, pd.DataFrame) or len(hist) < 30:
            return TrendWaveResult(
                ok=False,
                session_consumption=session_consumption,
                notes=["hist insufficient for wave"],
            )
        h = hist.copy()
        if "date" in h.columns:
            h["date"] = pd.to_datetime(h["date"])
            h = h.sort_values("date")
            if t_date:
                h = h[h["date"] <= pd.Timestamp(t_date)]
        h = h.reset_index(drop=True)
        if len(h) < 30:
            return TrendWaveResult(ok=False, session_consumption=session_consumption)

        close = h["close"].astype(float).values
        wave_id = 0
        trough = float(close[0])
        peak = float(close[0])
        wave_start_i = 0
        last_new = False
        for i in range(1, len(close)):
            cv = float(close[i])
            if cv < trough:
                trough = cv
                peak = cv
                wave_start_i = i
            if cv > peak:
                peak = cv
            if peak > trough * (1 + self.min_advance_pct / 100.0):
                dd = (peak - cv) / peak * 100.0 if peak > 0 else 0.0
                if dd >= self.pullback_pct and cv <= peak * (1 - self.pullback_pct / 100.0):
                    if i >= 2 and cv > float(close[i - 1]) and cv > trough * 1.01:
                        wave_id += 1
                        trough = cv
                        peak = cv
                        wave_start_i = i
                        last_new = True
                    else:
                        last_new = False
                else:
                    last_new = False
            else:
                last_new = False

        age = max(0, len(close) - 1 - wave_start_i)
        w = close[wave_start_i:]
        if len(w) >= 2:
            tail_n = min(10, len(w))
            tail = w[-tail_n:]
            gain_tail = (float(tail[-1]) / float(np.min(tail)) - 1.0) * 100.0
            t5 = w[-min(5, len(w)) :]
            gain5 = (float(t5[-1]) / float(t5[0]) - 1.0) * 100.0 if len(t5) >= 2 else 0.0
            est = float(np.clip(max(0.0, gain_tail) * 1.5 + max(0.0, gain5) * 2.0, 0.0, 100.0))
        else:
            est = 0.0

        if last_new:
            base = float(session_consumption) if session_consumption is not None else est
            wave_cons = float(min(base, 35.0))
        elif session_consumption is not None:
            wave_cons = float(session_consumption)
        else:
            wave_cons = est

        tags = ["NEW_WAVE"] if last_new else []
        notes = [f"wave_id={wave_id}", f"age={age}", f"wave_cons={wave_cons:.1f}", "legacy_hist_scan"]
        return TrendWaveResult(
            ok=True,
            wave=WaveInfo(
                wave_id=int(wave_id),
                wave_age_days=int(age),
                wave_consumption=round(wave_cons, 1),
                is_new_wave=bool(last_new),
                anchor_valid=False,
                notes=list(notes),
            ),
            session_consumption=session_consumption,
            tags=tags,
            notes=notes,
        )


__all__ = ["TrendWaveEngine", "TrendWaveResult"]
