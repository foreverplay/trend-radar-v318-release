"""
V3.0 Phase 10/11 — BacktestEngine + exit grid / Walk-Forward / ExitQualityScore.

Does NOT select stocks for live. Observation / structure-exit research only.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from .costs import (
    COMMISSION_RATE,
    SLIPPAGE,
    STAMP_TAX_RATE,
    apply_buy_costs,
    apply_sell_costs,
    board_limit_frac,
    is_limit_down,
    is_limit_up,
)
from .metrics import aggregate_metrics, trade_metrics
from .strategies import (
    BreakoutSupportExit,
    DynamicProtectionExit,
    DynamicProtectionMaxDD,
    ExitStrategy,
    FixedPct,
    MA20,
    MA20_0p5ATR,
    MA20_1ATR,
    MA20_ATR,
    STRATEGY_REGISTRY,
    StructureSupportATR,
    SwingLow,
    build_plan18_grid,
    get_strategy,
)
from .exit_quality import (
    MAX_DD_ELIMINATE,
    WEIGHTS,
    combine_window_scores,
    exit_quality_score,
)
from .walk_forward import (
    DEFERRED_NOTE,
    WF_WINDOW_LABEL,
    default_wf_spec,
    filter_entries_for_split,
    sample_legacy_entries,
)

__all__ = [
    "COMMISSION_RATE",
    "STAMP_TAX_RATE",
    "SLIPPAGE",
    "apply_buy_costs",
    "apply_sell_costs",
    "board_limit_frac",
    "is_limit_up",
    "is_limit_down",
    "BacktestEngine",
    "BacktestResult",
    "TradeRecord",
    "aggregate_metrics",
    "trade_metrics",
    "ExitStrategy",
    "DynamicProtectionExit",
    "DynamicProtectionMaxDD",
    "MA20",
    "MA20_0p5ATR",
    "MA20_1ATR",
    "MA20_ATR",
    "FixedPct",
    "SwingLow",
    "BreakoutSupportExit",
    "StructureSupportATR",
    "build_plan18_grid",
    "STRATEGY_REGISTRY",
    "get_strategy",
    "exit_quality_score",
    "combine_window_scores",
    "WEIGHTS",
    "MAX_DD_ELIMINATE",
    "default_wf_spec",
    "sample_legacy_entries",
    "filter_entries_for_split",
    "WF_WINDOW_LABEL",
    "DEFERRED_NOTE",
]

__version__ = "3.0.0-phase11"


def __getattr__(name: str):
    if name in ("BacktestEngine", "BacktestResult", "TradeRecord"):
        from .engine import BacktestEngine, BacktestResult, TradeRecord

        return {
            "BacktestEngine": BacktestEngine,
            "BacktestResult": BacktestResult,
            "TradeRecord": TradeRecord,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
