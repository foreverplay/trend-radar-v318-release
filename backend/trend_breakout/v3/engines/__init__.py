"""
V3.0 engines package — Phases 3–8 engines.

Bot orchestration: ``v3.bot.TrendConfirmationBot`` (Phase 7+8).
Phase 8: ExitEngine. BacktestEngine exists under v3/backtest.
V3.1/V3.1.2 additive: TrendState/Wave/HTF/Exhaustion/ActiveTrendPool (V3.1.2 = TrendId/WaveAnchor/COOLING lifecycle; not live publish).
Live scan remains V2.3 publish path; do not wire V3.1 into close-push yet.
"""
from __future__ import annotations

from .technical_engine import TechnicalEngine, TechnicalEngineResult
from .market_engine import MarketEngine, MarketEngineResult
from .sector_engine import SectorEngine, SectorEngineResult, map_sector_trend
from .fundamental_engine import FundamentalEngine, FundamentalEngineResult
from .risk_engine import RiskEngine, RiskEngineResult
from .continuity_engine import ContinuityEngine, ContinuityEngineResult
from .lifecycle_engine import (
    LifecycleEngine,
    LifecycleEngineResult,
    PRIORITY_LIFECYCLES,
    is_priority_lifecycle,
)
from .exit_engine import ExitEngine, ExitEngineResult
from .trend_state import TrendStateEngine, TrendStateResult
from .trend_wave import TrendWaveEngine, TrendWaveResult
from .higher_timeframe import HigherTimeframeEngine, HigherTimeframeResult
from .exhaustion import ExhaustionEngine, ExhaustionResult
from .active_trend_pool import ActiveTrendPool, ActiveTrendPoolResult
from .entry_signal import EntrySignalEmitter
from .major_move_label import MajorMoveLabelEngine
from .trend_maturity import TrendMaturityEngine
from .realized_trend import RealizedTrendEngine

__all__ = [
    "TechnicalEngine",
    "TechnicalEngineResult",
    "MarketEngine",
    "MarketEngineResult",
    "SectorEngine",
    "SectorEngineResult",
    "map_sector_trend",
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
    "TrendStateEngine",
    "TrendStateResult",
    "TrendWaveEngine",
    "TrendWaveResult",
    "HigherTimeframeEngine",
    "HigherTimeframeResult",
    "ExhaustionEngine",
    "ExhaustionResult",
    "ActiveTrendPool",
    "ActiveTrendPoolResult",
    "EntrySignalEmitter",
    "MajorMoveLabelEngine",
    "TrendMaturityEngine",
    "RealizedTrendEngine",
]
