"""
EntryWindow classifier — separate from TrendGrade.

PRIME | CONFIRMED | FOLLOW | EXTENDED | NO_ENTRY

V3.1.6 / V3.1.7 rewrite when maturity/structural provided:
  PRIME: only MATURE + (MAIN_RISE|NEW_WAVE) + just completed structural confirm
  CONFIRMED-early cannot be PRIME
  CONFIRMED window: mature + structure complete but not fresh rebreak slot
  FOLLOW: mature, still valid, mid-rise
  EXTENDED: still valid but far from structure OR Exhaustion HIGH
  Exhaustion HIGH → cannot be PRIME (does not kill trend)

V3.1.1–V3.1.5 path preserved when maturity_level is None (live grading compat).
Never folded into TrendGrade. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from v3.v31.config import get_v311_config
from v3.v31.types import (
    EntryWindow,
    ExhaustionLevel,
    StructuralConfirmation,
    TrendMaturityLevel,
    TrendState,
    WaveInfo,
)


_MATURE_LEVELS = frozenset(
    {
        TrendMaturityLevel.MATURE.value,
        TrendMaturityLevel.ADVANCED.value,
    }
)


def classify_entry_window(
    *,
    trend_state: Union[str, TrendState, None],
    wave: Optional[WaveInfo] = None,
    session_consumption: Optional[float] = None,
    exhaustion: Union[str, ExhaustionLevel, None] = None,
    ext_ma20_pct: Optional[float] = None,
    broken: bool = False,
    overheated: bool = False,
    config: Optional[Dict[str, Any]] = None,
    # V3.1.6 optional maturity inputs — when present, use rewrite
    maturity_level: Optional[str] = None,
    structural: Optional[StructuralConfirmation] = None,
    just_completed_structure: bool = False,
    fund_risk_level: Optional[str] = None,
    # V3.1.7
    follow_through: Optional[str] = None,
    current_wave_return: Optional[float] = None,
    version: Optional[str] = None,
    # V3.1.8
    rapid_main_rise: bool = False,
) -> tuple[EntryWindow, List[str]]:
    """Return (EntryWindow, notes). Observation only — not advice."""
    cfg = (config or get_v311_config()).get("entry_window") or {}
    notes: List[str] = []

    state = (
        trend_state.value
        if isinstance(trend_state, TrendState)
        else str(trend_state or TrendState.NONE.value).upper()
    )
    if broken or state == TrendState.TREND_FAILURE.value:
        notes.append("structure/trend failure → NO_ENTRY")
        return EntryWindow.NO_ENTRY, notes
    if state in (TrendState.NONE.value, TrendState.OBSERVE.value):
        notes.append("no confirmed trend → NO_ENTRY")
        return EntryWindow.NO_ENTRY, notes

    exh = ExhaustionLevel.coerce(exhaustion)

    # ---- V3.1.7 / V3.1.8 maturity-aware path ----
    ver = str(version or "")
    if maturity_level is not None and (
        ver.startswith("3.1.7") or ver.startswith("3.1.8") or follow_through is not None
    ):
        ew, notes = _classify_v317(
            state=state,
            maturity_level=str(maturity_level).upper(),
            structural=structural,
            just_completed_structure=just_completed_structure,
            exhaustion=exh,
            session_consumption=session_consumption,
            ext_ma20_pct=ext_ma20_pct,
            overheated=overheated,
            wave=wave,
            cfg=cfg,
            notes=notes,
            follow_through=str(follow_through or "UNKNOWN").upper(),
            current_wave_return=current_wave_return,
        )
        if ver.startswith("3.1.8"):
            ew, notes = apply_v318_entry_overlay(
                entry_window=ew,
                maturity_level=str(maturity_level).upper(),
                rapid_main_rise=bool(rapid_main_rise),
                exhaustion=exh,
                notes=notes,
            )
        return ew, notes

    # ---- V3.1.6 maturity-aware path ----
    if maturity_level is not None:
        return _classify_v316(
            state=state,
            maturity_level=str(maturity_level).upper(),
            structural=structural,
            just_completed_structure=just_completed_structure,
            exhaustion=exh,
            session_consumption=session_consumption,
            ext_ma20_pct=ext_ma20_pct,
            overheated=overheated,
            wave=wave,
            cfg=cfg,
            notes=notes,
        )

    # ---- Legacy V3.1.1–V3.1.5 path (live grading) ----
    return _classify_legacy(
        state=state,
        wave=wave,
        session_consumption=session_consumption,
        exhaustion=exh,
        ext_ma20_pct=ext_ma20_pct,
        overheated=overheated,
        cfg=cfg,
        notes=notes,
    )


def _classify_v317(
    *,
    state: str,
    maturity_level: str,
    structural: Optional[StructuralConfirmation],
    just_completed_structure: bool,
    exhaustion: ExhaustionLevel,
    session_consumption: Optional[float],
    ext_ma20_pct: Optional[float],
    overheated: bool,
    wave: Optional[WaveInfo],
    cfg: Dict[str, Any],
    notes: List[str],
    follow_through: str,
    current_wave_return: Optional[float],
) -> tuple[EntryWindow, List[str]]:
    """V3.1.7: PRIME needs MATURE + WaveReturn>=8% + FollowThrough=PASS.
    ADVANCED or Exhaustion HIGH → EXTENDED (trend not killed).
    """
    cons = _consumption(wave, session_consumption, notes)
    ext = float(ext_ma20_pct) if ext_ma20_pct is not None else None
    follow_c = float(cfg.get("follow_cons_max", 75))
    follow_e = float(cfg.get("follow_ext_max", 22))
    conf_c = float(cfg.get("confirmed_cons_max", 60))
    conf_e = float(cfg.get("confirmed_ext_max", 15))
    prime_c = float(cfg.get("prime_cons_max", 40))
    prime_e = float(cfg.get("prime_ext_max", 8))

    completed = bool(structural.completed) if structural else False
    mature_cont = bool(structural.matureContinuation) if structural else False
    structure_ok = completed or mature_cont
    wr = float(current_wave_return) if current_wave_return is not None else None
    ft = str(follow_through or "UNKNOWN").upper()

    # EXTENDED: Exhaustion HIGH / ADVANCED / OH / far
    if exhaustion == ExhaustionLevel.HIGH or overheated:
        notes.append("V317 HIGH exhaustion or OH → EXTENDED (not PRIME; trend not killed)")
        return EntryWindow.EXTENDED, notes
    if maturity_level == TrendMaturityLevel.ADVANCED.value:
        # ADVANCED → EXTENDED for new entries (may still be mid FOLLOW if early advanced+fresh — keep EXTENDED per plan)
        if cons is not None and cons >= float(cfg.get("confirmed_cons_max", 60)):
            notes.append("ADVANCED + elevated cons → EXTENDED")
            return EntryWindow.EXTENDED, notes
        # early ADVANCED with fresh structure may be CONFIRMED (not PRIME)
        if structure_ok and just_completed_structure and ft == "PASS" and (
            wr is None or wr >= 0.08
        ):
            if (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
                notes.append("ADVANCED early fresh → CONFIRMED (PRIME reserved for MATURE)")
                return EntryWindow.CONFIRMED, notes
        notes.append("ADVANCED → EXTENDED")
        return EntryWindow.EXTENDED, notes

    if cons is not None and cons >= follow_c:
        notes.append(f"cons={cons:.1f}≥{follow_c} → EXTENDED")
        return EntryWindow.EXTENDED, notes
    if ext is not None and ext >= follow_e:
        notes.append(f"ext={ext:.1f}≥{follow_e} → EXTENDED")
        return EntryWindow.EXTENDED, notes

    is_mature = maturity_level == TrendMaturityLevel.MATURE.value

    if not is_mature:
        if state == TrendState.CONFIRMED.value:
            notes.append("CONFIRMED + immature → NO_ENTRY")
            return EntryWindow.NO_ENTRY, notes
        notes.append(f"{state}+{maturity_level} → NO_ENTRY (await MATURE)")
        return EntryWindow.NO_ENTRY, notes

    fresh = bool(just_completed_structure)

    # PRIME: MATURE + (MAIN_RISE|NEW_WAVE) + WaveReturn>=8% + FollowThrough=PASS + fresh
    if (
        is_mature
        and state in (TrendState.MAIN_RISE.value, TrendState.NEW_WAVE.value)
        and structure_ok
        and fresh
        and ft == "PASS"
        and (wr is None or wr >= 0.08)
        and exhaustion != ExhaustionLevel.HIGH
    ):
        if (cons is None or cons < prime_c) and (ext is None or ext < prime_e):
            notes.append("V317 MATURE+PASS+WR→PRIME")
            return EntryWindow.PRIME, notes
        if (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
            notes.append("V317 fresh past prime cons → CONFIRMED")
            return EntryWindow.CONFIRMED, notes

    # ACCELERATION MATURE
    if is_mature and state == TrendState.ACCELERATION.value and structure_ok:
        if ft == "PASS" and (wr is None or wr >= 0.08):
            if (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
                notes.append("V317 ACCEL MATURE → CONFIRMED")
                return EntryWindow.CONFIRMED, notes
        if (cons is None or cons < follow_c) and (ext is None or ext < follow_e):
            notes.append("V317 ACCEL MATURE mid → FOLLOW")
            return EntryWindow.FOLLOW, notes
        notes.append("V317 ACCEL far → EXTENDED")
        return EntryWindow.EXTENDED, notes

    if is_mature and structure_ok:
        if ft in ("PASS", "WEAK") and (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
            notes.append("V317 MATURE structure → CONFIRMED")
            return EntryWindow.CONFIRMED, notes
        if (cons is None or cons < follow_c) and (ext is None or ext < follow_e):
            notes.append("V317 MATURE mid → FOLLOW")
            return EntryWindow.FOLLOW, notes
        notes.append("V317 MATURE far → EXTENDED")
        return EntryWindow.EXTENDED, notes

    if is_mature:
        notes.append("V317 MATURE without structure → FOLLOW/NO")
        if (cons is None or cons < follow_c):
            return EntryWindow.FOLLOW, notes
        return EntryWindow.EXTENDED, notes

    notes.append("V317 fallback NO_ENTRY")
    return EntryWindow.NO_ENTRY, notes


def _classify_v316(
    *,
    state: str,
    maturity_level: str,
    structural: Optional[StructuralConfirmation],
    just_completed_structure: bool,
    exhaustion: ExhaustionLevel,
    session_consumption: Optional[float],
    ext_ma20_pct: Optional[float],
    overheated: bool,
    wave: Optional[WaveInfo],
    cfg: Dict[str, Any],
    notes: List[str],
) -> tuple[EntryWindow, List[str]]:
    cons = _consumption(wave, session_consumption, notes)
    ext = float(ext_ma20_pct) if ext_ma20_pct is not None else None
    follow_c = float(cfg.get("follow_cons_max", 75))
    follow_e = float(cfg.get("follow_ext_max", 22))
    conf_c = float(cfg.get("confirmed_cons_max", 60))
    conf_e = float(cfg.get("confirmed_ext_max", 15))

    completed = bool(structural.completed) if structural else False
    mature_cont = bool(structural.matureContinuation) if structural else False
    structure_ok = completed or mature_cont
    is_mature = maturity_level in _MATURE_LEVELS

    # EXTENDED: Exhaustion HIGH / OH / far from structure — grade may stay A+
    if exhaustion == ExhaustionLevel.HIGH or overheated:
        notes.append("HIGH exhaustion or OH → EXTENDED (not PRIME; trend not killed)")
        return EntryWindow.EXTENDED, notes
    if cons is not None and cons >= follow_c:
        notes.append(f"cons={cons:.1f}≥{follow_c} → EXTENDED")
        return EntryWindow.EXTENDED, notes
    if ext is not None and ext >= follow_e:
        notes.append(f"ext={ext:.1f}≥{follow_e} → EXTENDED")
        return EntryWindow.EXTENDED, notes

    # Detection-only CONFIRMED / COOLING without maturity → NO_ENTRY for window
    # (still tracked by pool; not a buy window)
    if not is_mature:
        if state == TrendState.CONFIRMED.value:
            notes.append("CONFIRMED + immature → NO_ENTRY (detection, not entry)")
            return EntryWindow.NO_ENTRY, notes
        if state == TrendState.COOLING.value:
            notes.append("COOLING + immature → NO_ENTRY")
            return EntryWindow.NO_ENTRY, notes
        # MAIN_RISE/ACCEL/NEW_WAVE but still BUILDING
        notes.append(f"{state}+{maturity_level} → NO_ENTRY (await MATURE)")
        return EntryWindow.NO_ENTRY, notes

    # Mature path
    # "Fresh" = just completed structural confirm THIS day (or matureContinuation just flipped).
    # Do NOT use holdDays<=N — holdDays freezes at hold_need and would keep fresh forever.
    fresh = bool(just_completed_structure)

    prime_c = float(cfg.get("prime_cons_max", 40))
    prime_e = float(cfg.get("prime_ext_max", 8))

    # PRIME: MATURE/ADVANCED + (MAIN_RISE|NEW_WAVE) + just completed structural confirm
    # CONFIRMED-early cannot be PRIME (handled by is_mature gate above).
    # Exhaustion HIGH already returned EXTENDED above.
    if (
        maturity_level in _MATURE_LEVELS
        and state in (TrendState.MAIN_RISE.value, TrendState.NEW_WAVE.value)
        and structure_ok
        and fresh
        and exhaustion != ExhaustionLevel.HIGH
    ):
        if (cons is None or cons < prime_c) and (ext is None or ext < prime_e):
            notes.append("mature + MAIN_RISE/NEW_WAVE + fresh structure → PRIME")
            return EntryWindow.PRIME, notes
        # fresh but cons/ext past prime → still CONFIRMED if under conf
        if (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
            notes.append("fresh structure but past prime cons/ext → CONFIRMED")
            return EntryWindow.CONFIRMED, notes

    # ACCELERATION mature early → CONFIRMED (not PRIME); mid → FOLLOW
    if (
        maturity_level in _MATURE_LEVELS
        and state == TrendState.ACCELERATION.value
        and structure_ok
        and exhaustion != ExhaustionLevel.HIGH
    ):
        if fresh and (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
            notes.append("ACCELERATION mature early/fresh → CONFIRMED")
            return EntryWindow.CONFIRMED, notes
        if (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
            notes.append("ACCELERATION mature → CONFIRMED")
            return EntryWindow.CONFIRMED, notes
        if (cons is None or cons < follow_c) and (ext is None or ext < follow_e):
            notes.append("ACCELERATION mature mid → FOLLOW")
            return EntryWindow.FOLLOW, notes
        notes.append("ACCELERATION mature far → EXTENDED")
        return EntryWindow.EXTENDED, notes

    # CONFIRMED window: mature + structure complete (fresh or not — PRIME already tried)
    if is_mature and structure_ok:
        if (cons is None or cons < conf_c) and (ext is None or ext < conf_e):
            notes.append("mature + structure complete → CONFIRMED")
            return EntryWindow.CONFIRMED, notes

    # FOLLOW: mature, still valid, mid-rise
    if is_mature:
        if (cons is None or cons < follow_c) and (ext is None or ext < follow_e):
            notes.append("mature mid-rise → FOLLOW")
            return EntryWindow.FOLLOW, notes
        notes.append("mature but far → EXTENDED")
        return EntryWindow.EXTENDED, notes

    notes.append("fallback NO_ENTRY")
    return EntryWindow.NO_ENTRY, notes


def _classify_legacy(
    *,
    state: str,
    wave: Optional[WaveInfo],
    session_consumption: Optional[float],
    exhaustion: ExhaustionLevel,
    ext_ma20_pct: Optional[float],
    overheated: bool,
    cfg: Dict[str, Any],
    notes: List[str],
) -> tuple[EntryWindow, List[str]]:
    cons = _consumption(wave, session_consumption, notes)
    ext = float(ext_ma20_pct) if ext_ma20_pct is not None else None

    prime_c = float(cfg.get("prime_cons_max", 40))
    prime_e = float(cfg.get("prime_ext_max", 8))
    conf_c = float(cfg.get("confirmed_cons_max", 60))
    conf_e = float(cfg.get("confirmed_ext_max", 15))
    follow_c = float(cfg.get("follow_cons_max", 75))
    follow_e = float(cfg.get("follow_ext_max", 22))

    if exhaustion == ExhaustionLevel.HIGH or overheated:
        notes.append("HIGH exhaustion or OH → EXTENDED")
        return EntryWindow.EXTENDED, notes
    if cons is not None and cons >= follow_c:
        notes.append(f"cons={cons:.1f}≥{follow_c} → EXTENDED")
        return EntryWindow.EXTENDED, notes
    if ext is not None and ext >= follow_e:
        notes.append(f"ext={ext:.1f}≥{follow_e} → EXTENDED")
        return EntryWindow.EXTENDED, notes

    def _under(cmax: float, emax: float) -> bool:
        ok_c = cons is None or cons < cmax
        ok_e = ext is None or ext < emax
        return ok_c and ok_e

    if wave is not None and wave.is_new_wave and _under(prime_c, prime_e):
        notes.append("NEW_WAVE + low cons/ext → PRIME")
        return EntryWindow.PRIME, notes
    if state in (TrendState.CONFIRMED.value, TrendState.NEW_WAVE.value) and _under(
        prime_c, prime_e
    ):
        notes.append("early confirmed window → PRIME")
        return EntryWindow.PRIME, notes
    if _under(conf_c, conf_e):
        notes.append("mid confirmed → CONFIRMED")
        return EntryWindow.CONFIRMED, notes
    if _under(follow_c, follow_e):
        notes.append("follow zone → FOLLOW")
        return EntryWindow.FOLLOW, notes

    notes.append("beyond follow thresholds → EXTENDED")
    return EntryWindow.EXTENDED, notes


def _consumption(
    wave: Optional[WaveInfo],
    session_consumption: Optional[float],
    notes: List[str],
) -> Optional[float]:
    cons = None
    level = str(getattr(wave, "wave_level", "") or "") if wave is not None else ""
    if wave is not None and level == "SUB" and getattr(wave, "sub_wave_consumption", None) is not None:
        cons = float(wave.sub_wave_consumption)
        notes.append("EntryWindow cons from SubWaveConsumption")
    elif wave is not None and getattr(wave, "major_wave_consumption", None) is not None and level == "MAJOR":
        cons = float(wave.major_wave_consumption)
    elif wave is not None and wave.wave_consumption is not None:
        cons = float(wave.wave_consumption)
    elif session_consumption is not None:
        cons = float(session_consumption)
    return cons


__all__ = ["classify_entry_window"]

def apply_v318_entry_overlay(
    *,
    entry_window: EntryWindow,
    maturity_level: str,
    rapid_main_rise: bool,
    exhaustion: ExhaustionLevel,
    notes: Optional[List[str]] = None,
) -> tuple[EntryWindow, List[str]]:
    """V3.1.8: RAPID_MAIN_RISE + EARLY/BUILDING → FOLLOW (never PRIME).

    Does not upgrade EXTENDED / PRIME / CONFIRMED. Does not kill trend.
    """
    out_notes: List[str] = list(notes or [])
    mat = str(maturity_level or "").upper()
    if not rapid_main_rise:
        return entry_window, out_notes
    if exhaustion == ExhaustionLevel.HIGH:
        out_notes.append("V318 RAPID blocked by Exhaustion HIGH")
        return entry_window, out_notes
    if entry_window in (EntryWindow.PRIME, EntryWindow.CONFIRMED, EntryWindow.EXTENDED, EntryWindow.FOLLOW):
        out_notes.append(f"V318 RAPID keep {entry_window.value}")
        return entry_window, out_notes
    if mat in (TrendMaturityLevel.EARLY.value, TrendMaturityLevel.BUILDING.value):
        out_notes.append("V318 RAPID_MAIN_RISE + BUILDING/EARLY → FOLLOW")
        return EntryWindow.FOLLOW, out_notes
    out_notes.append("V318 RAPID no overlay")
    return entry_window, out_notes

