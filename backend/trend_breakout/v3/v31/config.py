"""V3.1 / V3.1.1 thresholds — additive; does not mutate V3.0 TREND_BOT_CONFIG live path."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

# Formal CLASSIC vs STRENGTH matrices (V3.1.1 §五)
A_PLUS_PATH_CONFIG: Dict[str, Any] = {
    "CLASSIC": {
        "tech_min": 85.0,
        "cont_min": 80.0,
        "sector_min": 70.0,
        "require_htf": False,
        "require_strength_state": False,
        "allowed_risk": ("SAFE", "WATCH"),
        "allowed_risk_relaxed": ("SAFE", "WATCH", "HIGH"),
    },
    "STRENGTH": {
        "tech_min": 80.0,
        "cont_min": 75.0,
        "sector_min": 70.0,
        "require_htf": True,
        "require_strength_state": True,
        "states": ("CONFIRMED", "MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
        "allowed_risk": ("SAFE", "WATCH"),
        "allowed_risk_relaxed": ("SAFE", "WATCH", "HIGH"),
    },
}

# Frozen V3.1.0 behavior for grade_candidate_v31 counterfactual
V310_CONFIG: Dict[str, Any] = {
    "version": "3.1.0",
    "phase": "scaffold",
    "fund_soft": {
        "hard_min_for_a_plus": 65.0,
        "classic_min": 70.0,
    },
    "strength_path": {
        "tech_min": 80.0,
        "cont_min": 75.0,
        "sector_min": 70.0,
        "states": ("CONFIRMED", "MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
    },
    "classic_a_plus": {
        "tech_min": 85.0,
        "cont_min": 80.0,
        "fund_min": 70.0,
        "sector_min": 70.0,
        "consumption_max": 70.0,
        "allowed_risk": ("SAFE", "WATCH"),
        "allowed_risk_relaxed": ("SAFE", "WATCH", "HIGH"),
    },
    "a": {
        "tech_min": 75.0,
        "cont_min": 70.0,
        "fund_min": 60.0,
        "sector_min": 60.0,
        "consumption_max": 80.0,
        "allowed_risk": ("SAFE", "WATCH", "WARNING", "HIGH"),
    },
    "b": {
        "tech_min": 65.0,
        "cont_min": 60.0,
        "fund_min": 50.0,
        "sector_min": 50.0,
        "consumption_max": 90.0,
    },
    "watch": {"tech_min": 55.0, "cont_min": 50.0},
    "filter": {
        "tech_min": 55.0,
        "cont_min": 50.0,
        "block_risk": ("BROKEN",),
        "block_states": ("TREND_FAILURE",),
    },
    "overheat_caps_grade": False,
    "use_wave_consumption": True,
    "wave_consumption_max_a_plus": 70.0,
    "relax_high_risk_in_states": ("MAIN_RISE", "ACCELERATION", "NEW_WAVE", "CONFIRMED"),
    "hysteresis": {
        "enabled": True,
        "persist_grades": ("A+", "A"),
        "cont_slack": 5.0,
        "tech_slack": 5.0,
        "clear_on_states": ("TREND_FAILURE",),
    },
    "pool": {
        "entry_grades": ("A+", "A"),
        "exit_states": ("TREND_FAILURE",),
        "discovery_universe_cap": 600,
    },
    "exhaustion": {
        "watch_consumption": 70.0,
        "high_consumption": 85.0,
        "high_limit_up_5d": 3,
        "watch_ext_ma20_pct": 15.0,
        "high_ext_ma20_pct": 25.0,
    },
    "htf": {
        "ok_labels": ("UP", "STRONG_UP", "MONTHLY_UP", "MONTHLY_STRONG_UP"),
    },
}

V31_CONFIG: Dict[str, Any] = {
    "version": "3.1.1",
    "phase": "correction",
    "a_plus_paths": deepcopy(A_PLUS_PATH_CONFIG),
    "fund_risk": {
        "watch_min": 65.0,
        "none_min": 70.0,
    },
    "fund_soft": {
        "hard_min_for_a_plus": 65.0,
        "classic_min": 70.0,
    },
    "strength_path": {
        "tech_min": 80.0,
        "cont_min": 75.0,
        "sector_min": 70.0,
        "states": ("CONFIRMED", "MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
    },
    "classic_a_plus": {
        "tech_min": 85.0,
        "cont_min": 80.0,
        "fund_min": 70.0,
        "sector_min": 70.0,
        "consumption_max": 70.0,
        "allowed_risk": ("SAFE", "WATCH"),
        "allowed_risk_relaxed": ("SAFE", "WATCH", "HIGH"),
    },
    "a": {
        "tech_min": 75.0,
        "cont_min": 70.0,
        "fund_min": 60.0,
        "sector_min": 60.0,
        "consumption_max": 80.0,
        "allowed_risk": ("SAFE", "WATCH", "WARNING", "HIGH"),
    },
    "b": {
        "tech_min": 65.0,
        "cont_min": 60.0,
        "fund_min": 50.0,
        "sector_min": 50.0,
        "consumption_max": 90.0,
    },
    "watch": {"tech_min": 55.0, "cont_min": 50.0},
    "filter": {
        "tech_min": 55.0,
        "cont_min": 50.0,
        "block_risk": ("BROKEN",),
        "block_states": ("TREND_FAILURE",),
    },
    "overheat_caps_grade": False,
    "use_wave_consumption": False,
    "consumption_blocks_a_plus": False,
    "wave_consumption_max_a_plus": 70.0,
    "relax_high_risk_in_states": ("MAIN_RISE", "ACCELERATION", "NEW_WAVE", "CONFIRMED"),
    "hysteresis": {
        "enabled": True,
        "persist_grades": ("A+", "A"),
        "cont_slack": 2.0,
        "tech_slack": 2.0,
        "clear_on_states": ("TREND_FAILURE",),
        "invalidate_on": (
            "TREND_FAILURE",
            "CLOSE_BELOW_BREAK",
            "MA20_SLOPE_NEG",
            "HIGHER_LOW_BREAK",
            "CONT_BELOW_70_2D",
            "STRUCTURE_REBREAK",
        ),
    },
    "pool": {
        "entry_grades": ("A+", "A"),
        "exit_states": ("TREND_FAILURE",),
        "discovery_universe_cap": 600,
    },
    "exhaustion": {
        "elevated_consumption": 70.0,
        "high_consumption": 85.0,
        "watch_consumption": 70.0,
        "high_limit_up_5d": 3,
        "watch_ext_ma20_pct": 15.0,
        "high_ext_ma20_pct": 25.0,
        "elevated_ext_ma20_pct": 15.0,
        "efficiency_elevated": 0.35,
        "efficiency_high": 0.20,
    },
    "entry_window": {
        "prime_cons_max": 40.0,
        "prime_ext_max": 8.0,
        "confirmed_cons_max": 60.0,
        "confirmed_ext_max": 15.0,
        "follow_cons_max": 75.0,
        "follow_ext_max": 22.0,
    },
    "htf": {
        "ok_labels": ("UP", "STRONG_UP", "MONTHLY_UP", "MONTHLY_STRONG_UP"),
    },
}


# V3.1.2 lifecycle / wave params only — does NOT retune A+/Tech/Fund/Sector/Exhaustion thresholds
V312_LIFECYCLE: Dict[str, Any] = {
    "version": "3.1.2",
    "failure": {
        # Align with ExitEngine structure; HigherLow required (user §二)
        "require_close_below_break": True,
        "require_ma20_slope_neg": True,
        "require_higher_low_break": True,
        "false_break_allow_days": 1,  # 1-day false break allowance
    },
    "confirm": {
        # Enter CONFIRMED from OBSERVE/NONE
        "lifecycle_confirm": (
            "FIRST_BREAKOUT",
            "SECOND_BREAKOUT",
            "MAIN_RISE",
            "ACCELERATION",
        ),
        "tech_min": 65.0,
        "cont_min": 55.0,
        "require_close_above_ma20": True,
    },
    "main_rise": {
        "min_days_confirmed": 3,
        "min_advance_from_anchor_pct": 8.0,
        "cont_min": 65.0,
        "lifecycle_force": ("MAIN_RISE",),
    },
    "acceleration": {
        # Real accel only — NOT OH alias
        "ext_ma20_pct_min": 18.0,
        "limit_ups_5d_min": 2,
        "lifecycle_ok": ("ACCELERATION",),
        # overheated alone does NOT force ACCELERATION
    },
    "cooling": {
        "pullback_from_peak_pct": 6.0,
        "min_days": 2,
        # Exhaustion HIGH may accompany COOLING but never forces it alone
        "allow_from_states": ("MAIN_RISE", "ACCELERATION", "CONFIRMED", "NEW_WAVE"),
    },
    "new_wave": {
        "min_pullback_pct": 8.0,
        "min_cooling_days": 5,
        "min_consolidation_bars": 3,
        "rebreakout_above_anchor_pct": 0.0,  # close > cooling pivot / prior platform
        "create_from_states": ("CONFIRMED", "MAIN_RISE", "NEW_WAVE", "COOLING"),
    },
    "wave": {
        "min_age_valid": 5,
        "min_advance_valid_pct": 5.0,
        "wave_cons_reset_cap": 35.0,
    },
    "strength_states": (
        "CONFIRMED",
        "MAIN_RISE",
        "ACCELERATION",
        "NEW_WAVE",
    ),
    # COOLING is in-trend but not a strength A+ state
}

V311_CONFIG = V31_CONFIG


def get_v31_config() -> Dict[str, Any]:
    """V3.1.0 frozen config for grade_candidate_v31 counterfactual."""
    return deepcopy(V310_CONFIG)


def get_v311_config() -> Dict[str, Any]:
    return deepcopy(V31_CONFIG)


def get_a_plus_path_config() -> Dict[str, Any]:
    return deepcopy(A_PLUS_PATH_CONFIG)


def get_v312_lifecycle() -> Dict[str, Any]:
    return deepcopy(V312_LIFECYCLE)

# V3.1.3 Major/Sub Wave hierarchy — does NOT retune A+/Tech/Fund/Sector/Exhaustion
# Does NOT change Hysteresis or ActiveTrendPool exit rules.
V313_WAVE: Dict[str, Any] = {
    "version": "3.1.3",
    # Inherit V3.1.2 lifecycle gates; hierarchy params only below
    "candidate": {
        "min_cooling_days": 5,
        "min_pullback_pct": 8.0,
        "min_consolidation_bars": 5,
        "confirm_hold_days": 2,  # 1–2 days hold before promote
        "require_ma20_up": True,
        "require_hh_hl": True,
    },
    "major": {
        "min_age_valid": 5,
        "min_advance_valid_pct": 5.0,
        # Promote to new MAJOR (vs SUB) only on deep+strong+peak breakthrough
        "min_age_for_new_major": 8,
        "min_advance_for_new_major_pct": 12.0,
        "min_pullback_depth_for_new_major_pct": 12.0,
        "breakthrough_above_peak_pct": 15.0,
    },
    "sub": {
        "min_age": 2,
        # Below this age on failure → MERGED into parent
        "merge_if_age_lt": 5,
        "shallow_advance_pct": 8.0,
    },
    "merge": {
        "keep_merged_records": True,
    },
    "consumption": {
        "major_reset_cap": 35.0,
        "sub_reset_cap": 35.0,
    },
    "wave_cons_reset_cap": 35.0,
}


def get_v313_wave() -> Dict[str, Any]:
    return deepcopy(V313_WAVE)


def get_v313_lifecycle() -> Dict[str, Any]:
    """V3.1.2 lifecycle + V3.1.3 wave hierarchy overlay."""
    cfg = deepcopy(V312_LIFECYCLE)
    cfg["version"] = "3.1.3"
    cfg["v313_wave"] = deepcopy(V313_WAVE)
    # Keep new_wave gates aligned with candidate eligibility
    nw = dict(cfg.get("new_wave") or {})
    cand = V313_WAVE["candidate"]
    nw["min_cooling_days"] = int(cand["min_cooling_days"])
    nw["min_pullback_pct"] = float(cand["min_pullback_pct"])
    nw["min_consolidation_bars"] = int(cand["min_consolidation_bars"])
    cfg["new_wave"] = nw
    return cfg


# V3.1.4 Major big-move gate — does NOT retune A+/Tech/Fund/Sector/Exhaustion
# Does NOT change Hysteresis or ActiveTrendPool exit rules.
# Candidate→Major requires PeakGainFromAnchor≥30% AND WaveAge≥20 (as-of, no lookahead)
# PLUS existing V3.1.3 confirm rules.
V314_MAJOR_GATE: Dict[str, Any] = {
    "version": "3.1.4",
    "MAJOR_MIN_PEAK_GAIN_PCT": 0.30,  # 30% peak gain from WaveAnchor
    "MAJOR_MIN_AGE_DAYS": 20,         # trading days since WaveAnchor
    # inherit V3.1.3 hierarchy; gate is additive
    "require_v313_confirm": True,
}


def get_v314_major_gate() -> Dict[str, Any]:
    return deepcopy(V314_MAJOR_GATE)


def get_v314_lifecycle() -> Dict[str, Any]:
    """V3.1.3 lifecycle + V3.1.4 Major big-move gate overlay."""
    cfg = get_v313_lifecycle()
    cfg["version"] = "3.1.4"
    cfg["v314_major_gate"] = deepcopy(V314_MAJOR_GATE)
    # Keep v313_wave; gate reads v314_major_gate
    return cfg


# V3.1.5 — EntrySignal / MajorMoveLabel decoupling
# PeakGate 30%/20d is LABEL-ONLY (gate_drives_live=False).
# Do NOT retune A+/Tech/Fund/Sector/Continuity/Exhaustion thresholds.
V315_MAJOR_GATE_LABEL_ONLY: Dict[str, Any] = {
    "version": "3.1.5",
    "gate_drives_live": False,  # demote V3.1.4 peak gate for structure/A+/EntrySignal/pool
    # Constants retained for documentation / label engine parity with V3.1.4 gate
    "MAJOR_MIN_PEAK_GAIN_PCT": 0.30,
    "MAJOR_MIN_AGE_DAYS": 20,
    "require_v313_confirm": True,
}

V315_LABEL: Dict[str, Any] = {
    "version": "3.1.5",
    "MAJOR_MIN_PEAK_GAIN_PCT": 0.30,
    "MAJOR_MIN_AGE_DAYS": 20,  # recall: historical major label age
    "SPIKE_GIVEBACK_MIN": 0.50,  # PeakToEndGiveback ≥ this → SPIKE_MAJOR
    "FORWARD_WINDOWS": (20, 40, 60, 120),
    "HIT_THRESHOLDS": {
        20: 0.15,   # +15%
        40: 0.30,
        60: 0.30,
        120: 0.30,
    },
    "SMALL_MOVE_MAX": 0.10,
    "MEDIUM_MOVE_MAX": 0.30,  # MAJOR_MOVE when PeakGain ≥ this
    "entry_price_mode": "signal_close",
    # EntrySignal current-rule floors = existing A-band (NOT raised)
    "entry_signal": {
        "entry_windows": ("PRIME", "CONFIRMED"),
        "states": ("CONFIRMED", "MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
        "tech_min": 75.0,   # existing A floor
        "cont_min": 70.0,   # existing A floor
        "sector_min": 60.0, # existing A floor; None sector = ok
        "require_htf": False,
    },
}


def get_v315_major_gate_label_only() -> Dict[str, Any]:
    return deepcopy(V315_MAJOR_GATE_LABEL_ONLY)


def get_v315_label() -> Dict[str, Any]:
    return deepcopy(V315_LABEL)


def get_v315_lifecycle() -> Dict[str, Any]:
    """V3.1.3 structure + V3.1.5 demoted peak gate (label-only)."""
    cfg = get_v313_lifecycle()
    cfg["version"] = "3.1.5"
    cfg["v314_major_gate"] = deepcopy(V315_MAJOR_GATE_LABEL_ONLY)
    cfg["v315_label"] = deepcopy(V315_LABEL)
    return cfg


__all__ = [
    "A_PLUS_PATH_CONFIG",
    "V310_CONFIG",
    "V31_CONFIG",
    "V311_CONFIG",
    "get_v31_config",
    "get_v311_config",
    "get_a_plus_path_config",
    "V312_LIFECYCLE",
    "get_v312_lifecycle",
    "V313_WAVE",
    "get_v313_wave",
    "get_v313_lifecycle",
    "V314_MAJOR_GATE",
    "get_v314_major_gate",
    "get_v314_lifecycle",
    "V315_MAJOR_GATE_LABEL_ONLY",
    "V315_LABEL",
    "get_v315_major_gate_label_only",
    "get_v315_label",
    "get_v315_lifecycle",
    "V316_MATURITY",
    "V316_LABEL",
    "get_v316_maturity",
    "get_v316_label",
    "get_v316_lifecycle",
    "V317_REALIZED",
    "V317_MATURITY",
    "V317_LABEL",
    "get_v317_realized",
    "get_v317_maturity",
    "get_v317_label",
    "get_v317_lifecycle",
]



# V3.1.6 — TrendMaturity + mature EntrySignal (detection ≠ entry)
# Does NOT retune A+/Tech/Fund/Sector/Continuity/Exhaustion thresholds.
# PeakGate remains label-only (inherit V3.1.5).
V316_MATURITY: Dict[str, Any] = {
    "version": "3.1.6",
    # Score weights (sum=100)
    "weight_structure": 30.0,
    "weight_breakout": 25.0,
    "weight_continuation": 20.0,
    "weight_volume_price": 15.0,
    "weight_htf": 10.0,
    # Level thresholds
    "level_building_min": 40.0,
    "level_mature_min": 65.0,
    "level_advanced_min": 85.0,
    # Structural confirmation
    "breakout_hold_days": 2,
    "strong_breakout_vol_ratio": 2.0,  # generic relax hold if vol_ratio ≥ this
    "strong_breakout_pct": 5.0,       # and day pct ≥ this
    "healthy_pullback_vol_max": 1.15,  # contraction relative to avg
    "pullback_max_depth_pct": 12.0,
    # CONFIRMED state clamp: never ADVANCED / MATURE via detection alone
    "confirmed_max_level": "BUILDING",
    # EntrySignal mature states (CONFIRMED excluded)
    "entry_states": ("MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
    "entry_maturity_levels": ("MATURE", "ADVANCED"),
    "entry_windows": ("PRIME", "CONFIRMED"),
    # existing A floors — NOT raised
    "tech_min": 75.0,
    "cont_min": 70.0,
    "sector_min": 60.0,
    # Lead-day eval buckets (label-only)
    "lead_optimal_max": 5,
    "lead_acceptable_max": 10,
    "lead_early_max": 15,
}

V316_LABEL: Dict[str, Any] = {
    "version": "3.1.6",
    # inherit V3.1.5 label constants
    "MAJOR_MIN_PEAK_GAIN_PCT": 0.30,
    "MAJOR_MIN_AGE_DAYS": 20,
    "SPIKE_GIVEBACK_MIN": 0.50,
    "FORWARD_WINDOWS": (20, 40, 60, 120),
    "HIT_THRESHOLDS": {
        20: 0.15,
        40: 0.30,
        60: 0.30,
        120: 0.30,
    },
    "SMALL_MOVE_MAX": 0.10,
    "MEDIUM_MOVE_MAX": 0.30,
    "entry_price_mode": "signal_close",
    "entry_signal": {
        "entry_windows": ("PRIME", "CONFIRMED"),
        "states": ("MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
        "maturity_levels": ("MATURE", "ADVANCED"),
        "tech_min": 75.0,
        "cont_min": 70.0,
        "sector_min": 60.0,
        "require_htf": False,
        "block_fund_risk_high": True,
        "block_exhaustion_prime": True,  # Exhaustion HIGH → not PRIME
    },
}


def get_v316_maturity() -> Dict[str, Any]:
    return deepcopy(V316_MATURITY)


def get_v316_label() -> Dict[str, Any]:
    return deepcopy(V316_LABEL)


def get_v316_lifecycle() -> Dict[str, Any]:
    """V3.1.5 structure + V3.1.6 maturity overlay."""
    cfg = get_v315_lifecycle()
    cfg["version"] = "3.1.6"
    cfg["v316_maturity"] = deepcopy(V316_MATURITY)
    cfg["v316_label"] = deepcopy(V316_LABEL)
    cfg["v315_label"] = deepcopy(V315_LABEL)
    return cfg


# V3.1.7 — RealizedTrend + stricter MATURE / PRIME / A+ maturity gates
# Does NOT retune A+/Tech/Fund/Sector/Continuity/Exhaustion base thresholds.
# PeakGate remains label-only. Builds on V3.1.6 maturity.
V317_REALIZED: Dict[str, Any] = {
    "version": "3.1.7",
    "weight_trend_return": 30.0,
    "weight_return20": 20.0,
    "weight_return40": 15.0,
    "weight_return60": 10.0,
    "weight_new_high": 10.0,
    "weight_price_expansion": 15.0,
    "trend_return_min": 0.15,
    "wave_return_min": 0.08,
    "return20_min": 0.08,
    "return40_min": 0.12,
    "return60_min": 0.15,
    "realized_score_min_for_a_plus": 70.0,
}

V317_MATURITY: Dict[str, Any] = {
    "version": "3.1.7",
    # Score weights (sum=100): age20 / cumReturn25 / waveReturn20 / HH15 / HL10 / stability10
    "weight_age": 20.0,
    "weight_cum_return": 25.0,
    "weight_wave_return": 20.0,
    "weight_hh": 15.0,
    "weight_hl": 10.0,
    "weight_stability": 10.0,
    "level_building_min": 40.0,
    "level_mature_min": 65.0,
    "level_advanced_min": 85.0,
    "min_age_for_mature": 15,
    "main_rise_quality_min": 70.0,
    "confirmed_max_level": "BUILDING",
    # same-day structure → max BUILDING (need follow-through)
    "same_day_max_level": "BUILDING",
    # Follow-through windows
    "follow_through_days": (3, 5, 10),
    "follow_through_pass_min": 70.0,
    "follow_through_weak_min": 45.0,
    # Entry
    "entry_states": ("MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
    "entry_maturity_levels": ("MATURE",),  # PRIME requires MATURE; ADVANCED→EXTENDED
    "entry_windows": ("PRIME", "CONFIRMED"),
    "prime_require_follow_through_pass": True,
    "prime_wave_return_min": 0.08,
    "tech_min": 75.0,
    "cont_min": 70.0,
    "sector_min": 60.0,
    "a_plus_cont_min": 75.0,
    "a_plus_sector_min": 60.0,
    "a_plus_realized_min": 70.0,
    "a_plus_age_min": 15,
    "lead_optimal_max": 5,
    "lead_acceptable_max": 10,
    "lead_early_max": 15,
    # inherit structural hold knobs from V316
    "breakout_hold_days": 2,
    "strong_breakout_vol_ratio": 2.0,
    "strong_breakout_pct": 5.0,
    "healthy_pullback_vol_max": 1.15,
    "pullback_max_depth_pct": 12.0,
}

V317_LABEL: Dict[str, Any] = {
    "version": "3.1.7",
    "MAJOR_MIN_PEAK_GAIN_PCT": 0.30,
    "MAJOR_MIN_AGE_DAYS": 20,
    "SPIKE_GIVEBACK_MIN": 0.50,
    "FORWARD_WINDOWS": (20, 40, 60, 120),
    "HIT_THRESHOLDS": {
        20: 0.15,
        40: 0.30,
        60: 0.30,
        120: 0.30,
    },
    "SMALL_MOVE_MAX": 0.10,
    "MEDIUM_MOVE_MAX": 0.30,
    "entry_price_mode": "signal_close",
    "entry_signal": {
        "entry_windows": ("PRIME", "CONFIRMED"),
        "states": ("MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
        "maturity_levels": ("MATURE",),
        "tech_min": 75.0,
        "cont_min": 70.0,
        "sector_min": 60.0,
        "require_htf": False,
        "block_fund_risk_high": True,
        "block_exhaustion_prime": True,
        "require_follow_through_pass_for_prime": True,
        "prime_wave_return_min": 0.08,
    },
}


def get_v317_realized() -> Dict[str, Any]:
    return deepcopy(V317_REALIZED)


def get_v317_maturity() -> Dict[str, Any]:
    return deepcopy(V317_MATURITY)


def get_v317_label() -> Dict[str, Any]:
    return deepcopy(V317_LABEL)


def get_v317_lifecycle() -> Dict[str, Any]:
    """V3.1.6 structure + V3.1.7 realized/maturity overlay."""
    cfg = get_v316_lifecycle()
    cfg["version"] = "3.1.7"
    cfg["v317_realized"] = deepcopy(V317_REALIZED)
    cfg["v317_maturity"] = deepcopy(V317_MATURITY)
    cfg["v317_label"] = deepcopy(V317_LABEL)
    return cfg


# V3.1.8 — TrendGrade ⊥ EntryStatus; restore A+/A/B/C; RAPID_MAIN_RISE
# Maturity / Realized / FollowThrough thresholds inherit V3.1.7 (not raised).
# TrendGrade must NOT be forced down by Maturity / Realized / EntryWindow.
V318_GRADE: Dict[str, Any] = {
    "version": "3.1.8",
    "a_plus": {
        "structure_min": 85.0,
        "cont_min": 80.0,
        "sector_min": 70.0,
        "require_fund_not_high": True,
        "block_trend_failure": True,
    },
    "a": {
        "structure_min": 80.0,
        "cont_min": 70.0,  # align Plan/V311 A band (甘李 Cont~73 保持 A)
        "cont_min_strong_structure": 65.0,  # structure>=90 soft floor
        "sector_min": 60.0,
        "require_fund_not_high": True,
        "block_trend_failure": True,
    },
    "b": {
        "structure_min": 65.0,
        "tech_min": 65.0,
        "cont_min": 60.0,
        "sector_min": 0.0,
    },
    "c": {
        "structure_min": 50.0,
        "tech_min": 55.0,
        "cont_min": 50.0,
    },
    "watch": {
        "structure_min": 40.0,
        "tech_min": 50.0,
        "cont_min": 45.0,
    },
    # When TrendStructureScore missing, use Tech as proxy (annotated)
    "structure_proxy_from_tech": True,
    # RAPID path may award A+/A without full structure maturity
    "rapid_a_plus": {
        "structure_min": 70.0,
        "tech_min": 80.0,
        "cont_min": 75.0,
        "sector_min": 60.0,
    },
    "rapid_a": {
        "structure_min": 60.0,
        "tech_min": 75.0,
        "cont_min": 70.0,
        "sector_min": 55.0,
    },
}

V318_RAPID: Dict[str, Any] = {
    "version": "3.1.8",
    "return20_min": 0.15,
    "trend_return_min": 0.12,
    "trend_return_age_max": 25,
    "cont_min": 75.0,
    "require_ma_stack": True,
    "require_hl_intact": True,
    "vol_ratio_max": 4.0,
    "alive_states": ("CONFIRMED", "MAIN_RISE", "ACCELERATION", "NEW_WAVE"),
    # Entry overlay: BUILDING/EARLY + RAPID → FOLLOW (not PRIME)
    "entry_status_on_building": "FOLLOW",
    "block_if_exhaustion_high": True,
    "block_if_extended_cons": True,
}

V318_BOARDS: Dict[str, Any] = {
    "mature_entry_grades": ("A+", "A"),
    "mature_entry_windows": ("PRIME", "CONFIRMED"),
    "trend_full_grades": ("A+", "A", "B", "C"),
    "watch_pool_grades": ("B", "C"),
    "watch_pool_maturity": ("EARLY", "BUILDING"),
    "extended_grades": ("A+", "A"),
    "extended_window": "EXTENDED",
}

V318_LABEL: Dict[str, Any] = {
    "version": "3.1.8",
    # inherit V317 label windows / hit thresholds
    **{k: v for k, v in V317_LABEL.items() if k != "version"},
}


def get_v318_grade() -> Dict[str, Any]:
    return deepcopy(V318_GRADE)


def get_v318_rapid() -> Dict[str, Any]:
    return deepcopy(V318_RAPID)


def get_v318_boards() -> Dict[str, Any]:
    return deepcopy(V318_BOARDS)


def get_v318_label() -> Dict[str, Any]:
    return deepcopy(V318_LABEL)


def get_v318_lifecycle() -> Dict[str, Any]:
    """V3.1.7 engines + V3.1.8 grade/rapid overlay."""
    cfg = get_v317_lifecycle()
    cfg["version"] = "3.1.8"
    cfg["v318_grade"] = deepcopy(V318_GRADE)
    cfg["v318_rapid"] = deepcopy(V318_RAPID)
    cfg["v318_boards"] = deepcopy(V318_BOARDS)
    cfg["v318_label"] = deepcopy(V318_LABEL)
    return cfg
