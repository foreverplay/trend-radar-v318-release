"""
TrendConfirmationBot V3.0 — Phase 14 package
(types / config / grading / regime / engines / Bot orchestrator).

Additive only. Live scan remains V2.3 (`v2_3_scan.py` / `v2_staircase_scan.py`).
Do not import this package from the close-push path until a later cut-over phase.
ExitEngine (Phase 8) + Backtest (Phase 9–11) + unified report (Phase 12–14). Does not feed live.
"""
from __future__ import annotations

from .config import TREND_BOT_CONFIG, get_config
from .types import (
    DataStatus,
    ExitSignal,
    Grade,
    Lifecycle,
    MarketRegime,
    MarketScore,
    RiskLevel,
    SectorResult,
    StockSnapshot,
    TrendBotResult,
)
from .regime_map import (
    from_legacy_regime,
    to_legacy_regime,
    legacy_to_v3,
    v3_to_legacy,
)
from .grading import GradeResult, grade_candidate
from .market_regime_archive import (
    ARCHIVE_CSV,
    ARCHIVE_JSONL,
    append_daily_regime,
    append_from_market_meta,
    append_from_market_meta_file,
)
from .engines import (
    TechnicalEngine,
    TechnicalEngineResult,
    MarketEngine,
    MarketEngineResult,
    SectorEngine,
    SectorEngineResult,
    FundamentalEngine,
    FundamentalEngineResult,
    RiskEngine,
    RiskEngineResult,
    ContinuityEngine,
    ContinuityEngineResult,
    LifecycleEngine,
    LifecycleEngineResult,
    PRIORITY_LIFECYCLES,
    is_priority_lifecycle,
    ExitEngine,
    ExitEngineResult,
)
from .snapshot import build_stock_snapshot_from_hist

# Bot symbols: lazy (avoid runpy double-load warning for ``python -m v3.bot...``)

__all__ = [
    "TREND_BOT_CONFIG",
    "get_config",
    "DataStatus",
    "ExitSignal",
    "Grade",
    "Lifecycle",
    "MarketRegime",
    "MarketScore",
    "RiskLevel",
    "SectorResult",
    "StockSnapshot",
    "TrendBotResult",
    "from_legacy_regime",
    "to_legacy_regime",
    "legacy_to_v3",
    "v3_to_legacy",
    "GradeResult",
    "grade_candidate",
    "ARCHIVE_CSV",
    "ARCHIVE_JSONL",
    "append_daily_regime",
    "append_from_market_meta",
    "append_from_market_meta_file",
    "TechnicalEngine",
    "TechnicalEngineResult",
    "MarketEngine",
    "MarketEngineResult",
    "SectorEngine",
    "SectorEngineResult",
    "FundamentalEngine",
    "FundamentalEngineResult",
    "RiskEngine",
    "RiskEngineResult",
    "ContinuityEngine",
    "ContinuityEngineResult",
    "LifecycleEngine",
    "LifecycleEngineResult",
    "PRIORITY_LIFECYCLES",
    "is_priority_lifecycle",
    "ExitEngine",
    "ExitEngineResult",
    "TrendConfirmationBot",
    "results_to_markdown_table",
    "sort_results",
    "build_stock_snapshot_from_hist",
    "write_unified_report",
    "format_stock_detail",
]

__version__ = "3.0.0-phase14"


def __getattr__(name: str):
    if name in ("TrendConfirmationBot", "results_to_markdown_table", "sort_results"):
        from .bot import TrendConfirmationBot, results_to_markdown_table, sort_results
        mapping = {
            "TrendConfirmationBot": TrendConfirmationBot,
            "results_to_markdown_table": results_to_markdown_table,
            "sort_results": sort_results,
        }
        return mapping[name]
    if name in ("write_unified_report", "format_stock_detail"):
        from .report import format_stock_detail, write_unified_report
        return {
            "write_unified_report": write_unified_report,
            "format_stock_detail": format_stock_detail,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
