"""
TREND_BOT_CONFIG — V3.0 Plan §30 thresholds + Plan §11–12 grade matrix.

Phase 14: unified report + detail + smoke (Backtest does NOT feed live).
Primary A+/A/B definition = this threshold matrix (NOT RecommendationScore≥90).
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Plan §30 — structure / candidate / exit / bear raised thresholds
# Defaults aligned with V2.0 staircase numbers (STRATEGY_V2.md) so Phase 3–4
# engines can swap in without silent drift.
# ---------------------------------------------------------------------------
TREND_BOT_CONFIG: Dict[str, Any] = {
    "version": "3.0.0",
    "phase": 14,
    # --- platform (平台整理) ---
    "platform": {
        "min_days": 5,
        "max_days": 20,
        "max_range_pct": 15.0,       # 区间 ≤15%
        "prefer_range_pct": 12.0,    # 优 ≤12%
        "min_close_position": 0.65,  # ClosePosition≥0.65
        "prefer_close_position": 0.80,
        "second_half_low_ge_first_half": True,
    },
    # --- first breakout (首次突破) ---
    "breakout": {
        "close_gt_platform_high_mult": 1.01,  # Close > 平台高×1.01
        "min_vol_ratio": 1.3,                 # Vol/VolMA20≥1.3
        "prefer_vol_ratio": 1.8,
        "min_breakout_strength_pct": 1.0,     # 优先 ≥1%
        "strong_breakout_strength_pct": 2.0,
    },
    # --- healthy pullback (健康分歧) ---
    "pullback": {
        "min_days": 2,
        "max_days": 8,
        "max_pullback_pct": 12.0,     # 回撤≤12%
        "prefer_pullback_pct": 8.0,   # 优≤8%
        "require_close_ge_ma20": True,
        "require_vol_shrink": True,
        "max_consecutive_big_down_days": 2,  # 无连续 3 大阴 → allow ≤2
        "must_hold_platform_low": True,
    },
    # --- second breakout (二次突破) ---
    "secondBreakout": {
        "require_close_gt_breakout_high": True,
        "require_close_gt_platform_high": True,
        "min_vol_ratio": 1.2,
        "min_close_position": 0.80,
        "main_rise_day_pct": 5.0,  # 日涨≥5% → 主升启动候选标记
    },
    # --- candidate grade matrix (Plan §11–12); independent scores, no TotalScore ---
    "candidate": {
        "A+": {
            "tech_min": 85.0,
            "cont_min": 80.0,
            "fund_min": 70.0,       # skipped if fund missing
            "sector_min": 70.0,     # skipped if sector missing
            "consumption_max": 70.0,
            "allowed_risk": ("SAFE", "WATCH"),
            "allowed_lifecycle": (
                "SECOND_BREAKOUT",
                "MAIN_RISE",
                "ACCELERATION",
            ),
        },
        "A": {
            "tech_min": 75.0,
            "cont_min": 70.0,
            "fund_min": 60.0,
            "sector_min": 60.0,
            "consumption_max": 80.0,
            "allowed_risk": ("SAFE", "WATCH", "WARNING"),
            "allowed_lifecycle": (
                "PLATFORM_HOLD",
                "HEALTHY_PULLBACK",
                "SECOND_BREAKOUT",
                "MAIN_RISE",
                "ACCELERATION",
            ),
        },
        "B": {
            "tech_min": 65.0,
            "cont_min": 60.0,
            "fund_min": 50.0,
            "sector_min": 50.0,
            "consumption_max": 90.0,
            "allowed_risk": ("SAFE", "WATCH", "WARNING", "HIGH"),
            "allowed_lifecycle": (
                "PLATFORM",
                "FIRST_BREAKOUT",
                "PLATFORM_HOLD",
                "HEALTHY_PULLBACK",
                "SECOND_BREAKOUT",
                "MAIN_RISE",
                "ACCELERATION",
                "OBSERVE",
            ),
        },
        "WATCH": {
            "tech_min": 55.0,
            "cont_min": 50.0,
            "fund_min": 0.0,
            "sector_min": 0.0,
            "consumption_max": 95.0,
            "allowed_risk": ("SAFE", "WATCH", "WARNING", "HIGH", "BREAK_RISK"),
            "allowed_lifecycle": None,  # any except hard-fail handled upstream
        },
        # hard filter floors (below → FILTER)
        "filter": {
            "tech_min": 55.0,
            "cont_min": 50.0,
            "block_risk": ("BROKEN",),
            "block_lifecycle": ("TREND_FAILURE",),
            "block_overheated_from_core": True,  # OH → max WATCH, never A+/A/B core
        },
    },
    # --- exit engine (Phase 8; Plan §§13–16) ---
    "exit": {
        # Phase 11: measured winner from exit_grid_2026proxy
        "default_strategy": "Fixed5%",
        # TrendBreakPrice = StructureSupport - ATR14 * atrMultiplier
        "atrMultiplier": 0.5,
        "atr_mult_stop": 2.0,          # legacy entry-stop distance (not BreakTrend)
        "atr_mult_trail": 2.5,         # trailing after +10% (reserved)
        # PeakProtection = PeakClose * (1 - maxDrawdown)
        "maxDrawdown": 0.15,
        "max_dd_pct": 15.0,            # synonym / legacy (%); used if maxDrawdown absent
        # 0 = same-day close confirm (Phase 8 default); 1/2 = multi-day streak hook
        "confirm_days": 0,
        "protect_atr_mult": 1.5,      # reserved PROTECT ATR band
        "reduce_risk_ma_break": True,  # Close<MA20 + slope≤0 → REDUCE_RISK
        "require_ma20_slope_neg": True,  # SELL requires MA20Slope < 0
        "warning_mode": "structure_support",  # WarningPrice = StructureSupport
        "swing_lookback": 10,         # RecentSwingLow window
        "near_protection_pct": 0.02,  # within 2% of protection → PROTECT
        "watch_cont_drop": 3.0,       # |delta1| threshold for WATCH
        "watch_consumption_rise": 5.0,
        "watch_consumption_level": 70.0,
    },
    # --- backtest (Phase 11; DEFAULT from exit grid — NOT live selection) ---
    "backtest": {
        "max_hold_days": 60,
        "force_close_at_end": True,
        "commission": 0.0003,   # 万三
        "stamp": 0.001,        # 千一 sell
        "slippage": 0.001,     # 0.1%
        "buy_delay_limit_up": True,
        "sell_delay_limit_down": True,
        "default_strategy": "Fixed5%",
        # Phase 10/11: grid + WF done; default_strategy from measured winner (see results/exit_grid_2026proxy)
        "feeds_live_selection": False,
    },
    # --- BEAR / RISK_OFF raised bars (added to candidate mins) ---
    "bear": {
        "tech_add": 5.0,
        "cont_add": 5.0,
        "fund_add": 5.0,
        "sector_add": 5.0,
        "consumption_tighten": 5.0,  # consumption_max -= 5
        "disallow_grades": (),      # keep empty; RISK_OFF push gated elsewhere
    },
    # CHOP slightly raised (weaker than BEAR)
    "chop": {
        "tech_add": 3.0,
        "cont_add": 3.0,
        "fund_add": 0.0,
        "sector_add": 3.0,
        "consumption_tighten": 0.0,
    },
    # liquidity / base filters (shared)
    "liquidity": {
        "min_avg20_amount_yi": 0.3,  # 3000万 = 0.3亿
        "min_listed_trading_days": 60,
    },
    # scoring weights reserved for later engines (not a TotalScore)
    "score_dims": (
        "tech_score",
        "continuity_score",
        "fund_score",
        "sector_score",
        "risk_level",
        "lifecycle",
    ),
}


def get_config() -> Dict[str, Any]:
    """Return a deep copy so callers cannot mutate the module default."""
    return deepcopy(TREND_BOT_CONFIG)


def candidate_thresholds_for_regime(regime_v3: str, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Return candidate grade dict with BEAR/CHOP raises applied.
    Does not mutate TREND_BOT_CONFIG.
    """
    base = deepcopy((cfg or TREND_BOT_CONFIG)["candidate"])
    regime = (regime_v3 or "NORMAL").upper()
    bump = None
    if regime == "BEAR":
        bump = (cfg or TREND_BOT_CONFIG)["bear"]
    elif regime == "CHOP":
        bump = (cfg or TREND_BOT_CONFIG)["chop"]
    if not bump:
        return base
    for g in ("A+", "A", "B", "WATCH"):
        block = base[g]
        block["tech_min"] = float(block["tech_min"]) + float(bump.get("tech_add", 0))
        block["cont_min"] = float(block["cont_min"]) + float(bump.get("cont_add", 0))
        block["fund_min"] = float(block["fund_min"]) + float(bump.get("fund_add", 0))
        block["sector_min"] = float(block["sector_min"]) + float(bump.get("sector_add", 0))
        block["consumption_max"] = float(block["consumption_max"]) - float(
            bump.get("consumption_tighten", 0)
        )
    return base


__all__ = ["TREND_BOT_CONFIG", "get_config", "candidate_thresholds_for_regime"]
