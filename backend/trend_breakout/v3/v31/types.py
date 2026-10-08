"""V3.1 … V3.1.7 observation types — TrendState, Exhaustion, Wave, Pool, EntryWindow, EntrySignal, MajorMoveLabel, TrendMaturity."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TrendState(str, Enum):
    """Trend-state machine (observation only). V3.1.2 adds COOLING."""

    NONE = "NONE"
    OBSERVE = "OBSERVE"
    CONFIRMED = "CONFIRMED"
    MAIN_RISE = "MAIN_RISE"
    ACCELERATION = "ACCELERATION"
    COOLING = "COOLING"
    NEW_WAVE = "NEW_WAVE"
    TREND_FAILURE = "TREND_FAILURE"


class ExhaustionLevel(str, Enum):
    """
    ExhaustionRisk — NOT TREND_FAILURE.

    V3.1.1 canonical values: NORMAL | ELEVATED | HIGH.
    Use ExhaustionLevel.coerce() for V3.1 NONE/WATCH aliases.
    """

    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"

    @classmethod
    def coerce(cls, x: Any) -> "ExhaustionLevel":
        if isinstance(x, cls):
            return x
        v = str(x or "NORMAL").strip().upper()
        alias = {
            "NONE": "NORMAL",
            "WATCH": "ELEVATED",
            "NORMAL": "NORMAL",
            "ELEVATED": "ELEVATED",
            "HIGH": "HIGH",
        }
        v = alias.get(v, "NORMAL")
        return cls(v)


# Back-compat attribute aliases used by older V3.1 call sites
ExhaustionLevel.NONE = ExhaustionLevel.NORMAL  # type: ignore[attr-defined]
ExhaustionLevel.WATCH = ExhaustionLevel.ELEVATED  # type: ignore[attr-defined]


class EntryWindow(str, Enum):
    """Timing / position window — never folded into TrendGrade."""

    PRIME = "PRIME"
    CONFIRMED = "CONFIRMED"
    FOLLOW = "FOLLOW"
    EXTENDED = "EXTENDED"
    NO_ENTRY = "NO_ENTRY"


class WaveLevel(str, Enum):
    """V3.1.3 wave hierarchy level."""

    NONE = "NONE"
    CANDIDATE = "CANDIDATE"
    MAJOR = "MAJOR"
    SUB = "SUB"
    MERGED = "MERGED"


class WaveEventKind(str, Enum):
    """V3.1.3 / V3.1.4 wave event log kinds."""

    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    MERGED = "MERGED"
    FAILED = "FAILED"
    FAILED_CANDIDATE = "FAILED_CANDIDATE"  # V3.1.4: trend failed before Major gate
    COMPLETED = "COMPLETED"
    PROMOTED = "PROMOTED"  # V3.1.4: Candidate→Major via big-move gate


class FundRiskLevel(str, Enum):
    """FundRisk ladder — only HIGH blocks A+."""

    NONE = "NONE"
    WATCH = "WATCH"
    HIGH = "HIGH"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"



class MajorMoveType(str, Enum):
    """V3.1.5 backtest label — NEVER feeds live EntrySignal / TrendGrade."""

    SUSTAINED_MAJOR = "SUSTAINED_MAJOR"
    SPIKE_MAJOR = "SPIKE_MAJOR"
    NON_MAJOR = "NON_MAJOR"
    ONGOING = "ONGOING"


class MoveBucket(str, Enum):
    """PeakGain bucket after signal (label-only)."""

    SMALL_MOVE = "SMALL_MOVE"
    MEDIUM_MOVE = "MEDIUM_MOVE"
    MAJOR_MOVE = "MAJOR_MOVE"
    FAIL = "FAIL"  # no usable forward / negative terminal with tiny peak


@dataclass
class SignalEvaluation:
    """V3.1.5 per-EntrySignal forward evaluation (label-only; may use future bars)."""

    symbol: str
    signalDate: str
    entryPrice: float
    forward20dMaxReturn: Optional[float] = None
    forward40dMaxReturn: Optional[float] = None
    forward60dMaxReturn: Optional[float] = None
    forward120dMaxReturn: Optional[float] = None
    maxDrawdown20: Optional[float] = None
    maxDrawdown40: Optional[float] = None
    maxDrawdown60: Optional[float] = None
    majorHit: bool = False
    majorType: str = MajorMoveType.NON_MAJOR.value
    peakGain: Optional[float] = None
    terminalReturn: Optional[float] = None
    peakDate: Optional[str] = None
    peakToEndGiveback: Optional[float] = None
    moveBucket: str = MoveBucket.SMALL_MOVE.value
    entryWindow: Optional[str] = None
    rule: str = "current"
    trendId: int = 0
    waveId: int = 0
    hit20: bool = False
    hit40: bool = False
    hit60: bool = False
    hit120: bool = False
    lookaheadError: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EntrySignalEvent:
    """Realtime EntrySignal (as-of ≤ t only; no future fields)."""

    symbol: str
    signalDate: str
    entryPrice: float
    entryWindow: str
    rule: str  # current|classic|strength
    trendState: str = ""
    tech: Optional[float] = None
    cont: Optional[float] = None
    sector: Optional[float] = None
    htfOk: bool = False
    grade: Optional[str] = None
    path: Optional[str] = None
    trendId: int = 0
    waveId: int = 0
    waveLevel: str = ""  # observational only; MUST NOT gate emission
    tags: List[str] = field(default_factory=list)
    lookaheadError: bool = False
    # V3.1.6 maturity (as-of only)
    trendMaturity: Optional[str] = None
    maturityScore: Optional[float] = None
    structuralCompleted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WaveInfo:
    wave_id: int = 0
    wave_age_days: int = 0
    wave_consumption: Optional[float] = None
    is_new_wave: bool = False
    notes: List[str] = field(default_factory=list)
    wave_return_pct: Optional[float] = None
    # V3.1.2
    trend_id: int = 0
    wave_anchor: Optional[float] = None
    wave_anchor_date: Optional[str] = None
    wave_anchor_kind: Optional[str] = None  # BREAKOUT|CONFIRM|REBREAKOUT
    anchor_valid: bool = True
    trend_consumption: Optional[float] = None
    # V3.1.3 hierarchy
    wave_level: str = "NONE"  # WaveLevel value
    major_wave_id: int = 0
    parent_major_id: int = 0
    candidate_id: int = 0
    major_wave_consumption: Optional[float] = None
    sub_wave_consumption: Optional[float] = None
    is_candidate: bool = False
    # V3.1.4 big-move gate (as-of)
    peak_gain_from_anchor: Optional[float] = None  # fraction, e.g. 0.30 = 30%
    peak_px_from_anchor: Optional[float] = None
    structure_confirmed: bool = False
    is_failed_candidate: bool = False
    major_promoted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WaveEventLogEntry:
    """V3.1.3 wave event log row."""

    t_date: str
    code6: str = ""
    kind: str = ""  # CANDIDATE|CONFIRMED|MERGED|FAILED|COMPLETED
    reason: str = ""
    trend_id: int = 0
    wave_id: int = 0
    major_wave_id: int = 0
    parent_major_id: int = 0
    candidate_id: int = 0
    wave_level: str = "NONE"
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PoolEntry:
    code: str
    entered_date: str
    peak_grade: str  # "A+" | "A"
    last_grade: str
    last_state: str = TrendState.CONFIRMED.value
    active: bool = True
    exit_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrendStateSnapshot:
    """Unified daily trend/wave/state snapshot (V3.1.2 §九)."""

    t_date: str = ""
    code6: str = ""
    trend_id: int = 0
    wave_id: int = 0
    state: str = TrendState.NONE.value
    prior_state: Optional[str] = None
    transition: Optional[str] = None
    wave_anchor: Optional[float] = None
    wave_anchor_date: Optional[str] = None
    wave_anchor_kind: Optional[str] = None
    wave_age_days: int = 0
    wave_consumption: Optional[float] = None
    trend_consumption: Optional[float] = None
    trend_break_price: Optional[float] = None
    false_break_pending: bool = False
    exhaustion: str = ExhaustionLevel.NORMAL.value
    anchor_valid: bool = True
    days_in_state: int = 0
    days_in_trend: int = 0
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # V3.1.3
    wave_level: str = "NONE"
    major_wave_id: int = 0
    parent_major_id: int = 0
    candidate_id: int = 0
    major_wave_consumption: Optional[float] = None
    sub_wave_consumption: Optional[float] = None
    # V3.1.4
    peak_gain_from_anchor: Optional[float] = None
    structure_confirmed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StateTransitionLogEntry:
    t_date: str
    code6: str
    from_state: str
    to_state: str
    trend_id: int
    wave_id: int
    reason: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class V31GradeContext:
    """Context for grade_candidate_v31 / v311 (pool / HTF / wave / structure)."""

    trend_state: TrendState = TrendState.NONE
    exhaustion: ExhaustionLevel = ExhaustionLevel.NORMAL
    wave: Optional[WaveInfo] = None
    htf_monthly: Optional[str] = None
    htf_ok: bool = False
    in_pool: bool = False
    prior_grade: Optional[str] = None
    fund_risk: bool = False  # legacy bool; prefer fund_risk_level
    fund_risk_level: Optional[str] = None
    entry_window: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    # hysteresis invalidate inputs (optional)
    close: Optional[float] = None
    trend_break_price: Optional[float] = None
    ma20_slope_neg: bool = False
    higher_low_broken: bool = False
    cont_below_70_streak: int = 0
    structure_rebreak: bool = False
    risk_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["trend_state"] = (
            self.trend_state.value
            if isinstance(self.trend_state, TrendState)
            else self.trend_state
        )
        exh = ExhaustionLevel.coerce(self.exhaustion)
        d["exhaustion"] = exh.value
        if self.wave is not None:
            d["wave"] = self.wave.to_dict()
        return d



class TrendMaturityLevel(str, Enum):
    """V3.1.6 structural maturity — NOT TechnicalScore alone; NOT calendar age alone."""

    EARLY = "EARLY"
    BUILDING = "BUILDING"
    MATURE = "MATURE"
    ADVANCED = "ADVANCED"


class LeadBucket(str, Enum):
    """V3.1.6 evaluation-only lead-day bucket (LeadDays = MajorLabelDate − EntrySignalDate)."""

    OPTIMAL = "OPTIMAL"          # 0–5
    ACCEPTABLE = "ACCEPTABLE"    # 6–10
    EARLY = "EARLY"              # 11–15
    TOO_EARLY = "TOO_EARLY"      # >15
    LATE = "LATE"                # signal ≥ label
    UNKNOWN = "UNKNOWN"


@dataclass
class StructuralConfirmation:
    """Breakout → healthy pullback → higher low → rebreakout → hold."""

    firstBreakout: bool = False
    healthyPullback: bool = False
    higherLow: bool = False
    reBreakout: bool = False
    breakoutHold: bool = False
    completed: bool = False
    matureContinuation: bool = False
    firstBreakoutDate: Optional[str] = None
    pullbackDate: Optional[str] = None
    reBreakoutDate: Optional[str] = None
    holdDays: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SignalMaturity:
    """Per-day maturity explanation (sub-scores 0–100 each component scale)."""

    score: float = 0.0
    level: str = TrendMaturityLevel.EARLY.value
    structure: float = 0.0
    breakout: float = 0.0
    continuation: float = 0.0
    volumePrice: float = 0.0
    htf: float = 0.0
    reasons: List[str] = field(default_factory=list)
    structural: Optional[StructuralConfirmation] = None
    # V3.1.7 optional components
    ageScore: float = 0.0
    returnScore: float = 0.0
    waveReturnScore: float = 0.0
    hhScore: float = 0.0
    hlScore: float = 0.0
    stabilityScore: float = 0.0
    mainRiseQuality: float = 0.0
    followThrough: str = "UNKNOWN"
    followThroughScore: float = 0.0
    trendStructureScore: float = 0.0
    realizedTrendScore: float = 0.0
    trendAge: int = 0
    trendReturn: float = 0.0
    currentWaveReturn: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.structural is not None:
            d["structural"] = self.structural.to_dict()
        return d



class FollowThroughLevel(str, Enum):
    """V3.1.7 breakout follow-through (as-of only)."""

    PASS = "PASS"
    WEAK = "WEAK"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass
class RealizedTrendResult:
    """V3.1.7 RealizedTrendEngine output — T and earlier only."""

    score: float = 0.0
    trendReturn: float = 0.0
    currentWaveReturn: float = 0.0
    return20: Optional[float] = None
    return40: Optional[float] = None
    return60: Optional[float] = None
    priceExpansion: float = 0.0
    newHighProgress: float = 0.0
    realizedDays: int = 0
    trendAnchorPrice: Optional[float] = None
    waveAnchorPrice: Optional[float] = None
    matureFloorOk: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BreakoutFollowThrough:
    """Post-breakout 3/5/10d continuation (as-of only)."""

    level: str = "UNKNOWN"
    score: float = 0.0
    daysSinceBreakout: int = 0
    heldAboveBreak: bool = False
    priceContinuation: float = 0.0
    volumeOk: bool = False
    closePositionOk: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MainRiseQuality:
    """Main-rise quality 0–100 (as-of only)."""

    score: float = 0.0
    higherHigh: bool = False
    higherLow: bool = False
    newHigh: bool = False
    pullbackIntact: bool = False
    volumeControlled: bool = False
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



__all__ = [
    "TrendState",
    "ExhaustionLevel",
    "EntryWindow",
    "FundRiskLevel",
    "WaveLevel",
    "WaveEventKind",
    "WaveInfo",
    "WaveEventLogEntry",
    "PoolEntry",
    "TrendStateSnapshot",
    "StateTransitionLogEntry",
    "V31GradeContext",
    "MajorMoveType",
    "MoveBucket",
    "SignalEvaluation",
    "EntrySignalEvent",
    "TrendMaturityLevel",
    "LeadBucket",
    "StructuralConfirmation",
    "SignalMaturity",
    "FollowThroughLevel",
    "RealizedTrendResult",
    "BreakoutFollowThrough",
    "MainRiseQuality",
]
