"""
V3.0 shared types — Plan §3 StockSnapshot, §31 TrendBotResult, regimes, grades.

Phase 2 stubs only. Engines later fill fields; Optional fund / price placeholders OK.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MarketRegime(str, Enum):
    """Plan V3 market regimes (BULL/NORMAL/BEAR/CHOP)."""

    BULL = "BULL"
    NORMAL = "NORMAL"
    BEAR = "BEAR"
    CHOP = "CHOP"


class LegacyRegime(str, Enum):
    """V2.x MarketRegime labels — keep RISK_OFF."""

    STRONG = "STRONG"
    NORMAL = "NORMAL"
    WEAK = "WEAK"
    RISK_OFF = "RISK_OFF"


class RiskLevel(str, Enum):
    """V3 risk ladder (extends V2 LOW/MID/HIGH)."""

    SAFE = "SAFE"
    WATCH = "WATCH"
    WARNING = "WARNING"
    BREAK_RISK = "BREAK_RISK"
    BROKEN = "BROKEN"
    HIGH = "HIGH"

    @classmethod
    def map_from_v2(cls, v2: Union[str, None], *, broken: bool = False, break_risk: bool = False) -> "RiskLevel":
        """
        Map V2 RiskEngine LOW/MID/HIGH (+ broken flags) → V3 RiskLevel.

        LOW  → SAFE
        MID  → WATCH
        HIGH → HIGH
        broken=True → BROKEN (wins)
        break_risk=True (structure at risk, not yet broken) → BREAK_RISK
        """
        if broken:
            return cls.BROKEN
        if break_risk:
            return cls.BREAK_RISK
        s = (v2 or "MID").strip().upper()
        if s == "LOW":
            return cls.SAFE
        if s == "MID":
            return cls.WATCH
        if s == "HIGH":
            return cls.HIGH
        # already a V3 label?
        try:
            return cls(s)
        except ValueError:
            return cls.WATCH


class Lifecycle(str, Enum):
    """
    Plan lifecycle stages.

    V2 MAIN_RISE_ACCEL → ACCELERATION (documented split).
    V2 MAIN_RISE (if ever emitted alone) → MAIN_RISE.
    """

    ACCUMULATION = "ACCUMULATION"
    PLATFORM = "PLATFORM"
    FIRST_BREAKOUT = "FIRST_BREAKOUT"
    HEALTHY_PULLBACK = "HEALTHY_PULLBACK"
    PLATFORM_HOLD = "PLATFORM_HOLD"
    SECOND_BREAKOUT = "SECOND_BREAKOUT"
    MAIN_RISE = "MAIN_RISE"
    ACCELERATION = "ACCELERATION"
    OVERHEATED = "OVERHEATED"
    TREND_FAILURE = "TREND_FAILURE"
    OBSERVE = "OBSERVE"
    NONE = "NONE"

    @classmethod
    def map_from_v2_phase(cls, phase: Union[str, None]) -> "Lifecycle":
        """Map existing V2 structure phase strings → V3 Lifecycle."""
        p = (phase or "NONE").strip().upper()
        # documented: MAIN_RISE_ACCEL → ACCELERATION
        if p == "MAIN_RISE_ACCEL":
            return cls.ACCELERATION
        if p == "MAIN_RISE":
            return cls.MAIN_RISE
        try:
            return cls(p)
        except ValueError:
            aliases = {
                "OH": cls.OVERHEATED,
                "FAIL": cls.TREND_FAILURE,
                "BROKEN": cls.TREND_FAILURE,
            }
            return aliases.get(p, cls.NONE)


class ExitSignal(str, Enum):
    HOLD = "HOLD"
    WATCH = "WATCH"
    PROTECT = "PROTECT"
    REDUCE_RISK = "REDUCE_RISK"
    SELL = "SELL"


class Grade(str, Enum):
    """V3 observation grade — threshold matrix, not RecScore bands.

    V3.1.8 restores C (雏形) between B and WATCH for TrendGrade full ladder.
    """

    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    WATCH = "WATCH"
    FILTER = "FILTER"


class DataStatus(str, Enum):
    VALID = "VALID"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Market / sector stubs
# ---------------------------------------------------------------------------


@dataclass
class MarketScore:
    """Market score + dual regime labels (Plan regime + V2 legacy)."""

    t_date: str
    market_score: float
    regime_v3: MarketRegime = MarketRegime.NORMAL
    legacy_regime: str = "NORMAL"  # STRONG|NORMAL|WEAK|RISK_OFF
    risk_off: bool = False
    cap: int = 10
    notes: List[str] = field(default_factory=list)
    breadth: Dict[str, Any] = field(default_factory=dict)
    turnover: Dict[str, Any] = field(default_factory=dict)
    indices: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["regime_v3"] = self.regime_v3.value if isinstance(self.regime_v3, MarketRegime) else self.regime_v3
        return d


@dataclass
class ContinuityFields:
    """Continuity stub matching V2.2/V2.3 continuity outputs."""

    continuity_score: Optional[float] = None
    continuity_type: Optional[str] = None
    continuity_type_cn: Optional[str] = None
    continuity_delta1: Optional[float] = None
    continuity_delta3: Optional[float] = None
    continuity_delta5: Optional[float] = None
    continuity_delta_label: Optional[str] = None
    consumption_score: Optional[float] = None


@dataclass
class SectorResult:
    """Sector / sector-trend stub (Plan outputs + V2.3 SectorTrend fields)."""

    name: Optional[str] = None
    sector_score: Optional[float] = None
    daily_trend: Optional[str] = None
    daily_trend_score: Optional[float] = None
    monthly_trend: Optional[str] = None
    monthly_trend_score: Optional[float] = None
    breadth_score: Optional[float] = None
    relative_strength_20: Optional[float] = None
    sector_continuity: Optional[float] = None


# ---------------------------------------------------------------------------
# Plan §3 — StockSnapshot
# ---------------------------------------------------------------------------


@dataclass
class StockSnapshot:
    """
    Per-stock snapshot (Plan §3).

    Core identity + structure + independent scores.
    Fund fields optional (DATA_INSUFFICIENT when absent — do not fake 0).
    """

    code: str
    name: str = ""
    t_date: str = ""
    close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    industry: Optional[str] = None
    # structure / lifecycle
    lifecycle: Lifecycle = Lifecycle.NONE
    phase_v2: Optional[str] = None  # original V2 phase string
    overheated: bool = False
    kjs_tag: Optional[str] = None
    platform_high: Optional[float] = None
    platform_low: Optional[float] = None
    prev20high: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    vol_ratio: Optional[float] = None
    dist_ma20_pct: Optional[float] = None
    rs20_pct: Optional[float] = None
    rs60_pct: Optional[float] = None
    # independent scores (no TotalScore)
    tech_score: Optional[float] = None
    continuity: ContinuityFields = field(default_factory=ContinuityFields)
    # optional fund
    fund_score: Optional[float] = None
    fundamental: Optional[Dict[str, Any]] = None
    # sector
    sector: SectorResult = field(default_factory=SectorResult)
    # risk
    risk_level: RiskLevel = RiskLevel.WATCH
    risk_tags: List[str] = field(default_factory=list)
    broken: bool = False
    # meta
    data_status: DataStatus = DataStatus.VALID
    avg20_amt_yi: Optional[float] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["lifecycle"] = self.lifecycle.value if isinstance(self.lifecycle, Lifecycle) else self.lifecycle
        d["risk_level"] = self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level
        d["data_status"] = self.data_status.value if isinstance(self.data_status, DataStatus) else self.data_status
        return d


# ---------------------------------------------------------------------------
# Plan §31 — TrendBotResult
# ---------------------------------------------------------------------------


@dataclass
class TrendBotResult:
    """
    Unified per-name V3 result (Plan §31).

    warning / protection / break prices filled by ExitEngine (Phase 8).
    """

    t_date: str
    code: str
    name: str = ""
    grade: Grade = Grade.FILTER
    data_status: DataStatus = DataStatus.VALID
    # scores (independent)
    tech_score: Optional[float] = None
    continuity_score: Optional[float] = None
    fund_score: Optional[float] = None
    sector_score: Optional[float] = None
    consumption_score: Optional[float] = None
    # state
    lifecycle: Lifecycle = Lifecycle.NONE
    risk_level: RiskLevel = RiskLevel.WATCH
    regime_v3: MarketRegime = MarketRegime.NORMAL
    legacy_regime: str = "NORMAL"
    risk_off: bool = False
    exit_signal: ExitSignal = ExitSignal.HOLD
    # exit prices (ExitEngine Phase 8)
    close: Optional[float] = None
    warning_price: Optional[float] = None
    protection_price: Optional[float] = None
    break_price: Optional[float] = None
    # narrative (rules only; no buy language)
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    snapshot: Optional[StockSnapshot] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["grade"] = self.grade.value if isinstance(self.grade, Grade) else self.grade
        d["data_status"] = self.data_status.value if isinstance(self.data_status, DataStatus) else self.data_status
        d["lifecycle"] = self.lifecycle.value if isinstance(self.lifecycle, Lifecycle) else self.lifecycle
        d["risk_level"] = self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level
        d["regime_v3"] = self.regime_v3.value if isinstance(self.regime_v3, MarketRegime) else self.regime_v3
        d["exit_signal"] = self.exit_signal.value if isinstance(self.exit_signal, ExitSignal) else self.exit_signal
        if self.snapshot is not None:
            d["snapshot"] = self.snapshot.to_dict()
        return d


__all__ = [
    "MarketRegime",
    "LegacyRegime",
    "RiskLevel",
    "Lifecycle",
    "ExitSignal",
    "Grade",
    "DataStatus",
    "MarketScore",
    "ContinuityFields",
    "SectorResult",
    "StockSnapshot",
    "TrendBotResult",
]
