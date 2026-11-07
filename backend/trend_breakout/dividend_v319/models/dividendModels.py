"""DividendTrendResult and related types (Plan §20). Phase1 — no fake scores."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DividendGrade(str, Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    FILTER = "FILTER"


class DividendEntryStatus(str, Enum):
    ACCUMULATE_WATCH = "ACCUMULATE_WATCH"
    PULLBACK_WATCH = "PULLBACK_WATCH"
    TREND_CONFIRM = "TREND_CONFIRM"
    HOLD_WATCH = "HOLD_WATCH"
    EXTENDED = "EXTENDED"
    AVOID = "AVOID"
    TREND_BROKEN = "TREND_BROKEN"


class DividendRiskLevel(str, Enum):
    SAFE = "SAFE"
    WATCH = "WATCH"
    WARNING = "WARNING"
    HIGH = "HIGH"


class DataStatus(str, Enum):
    VALID = "VALID"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    ERROR = "ERROR"


class UniverseSource(str, Enum):
    INDEX = "INDEX"
    AUTONOMOUS = "AUTONOMOUS"
    BOTH = "BOTH"


@dataclass
class UniverseMember:
    symbol: str
    name: str = ""
    sources: List[str] = field(default_factory=list)
    index_codes: List[str] = field(default_factory=list)
    cash_dividend_years: List[str] = field(default_factory=list)
    continuous_cash_years: int = 0
    excluded: bool = False
    exclude_reason: Optional[str] = None
    data_status: str = DataStatus.VALID.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DividendTrendResult:
    """Plan §20 structure. Scoring fields optional until later phases."""

    symbol: str
    name: str = ""
    date: str = ""
    strategy: str = "DIVIDEND_TREND"

    dividend_yield_ttm: Optional[float] = None

    dividend_quality_score: Optional[float] = None
    dividend_sustainability_score: Optional[float] = None
    dividend_trend_score: Optional[float] = None
    historical_total_return_score: Optional[float] = None
    valuation_score: Optional[float] = None
    dividend_risk_score: Optional[float] = None
    dividend_opportunity_score: Optional[float] = None

    dividend_grade: Optional[str] = None
    dividend_entry_status: Optional[str] = None
    dividend_risk: Optional[str] = None

    price_return_1y: Optional[float] = None
    total_return_1y: Optional[float] = None
    total_return_3y: Optional[float] = None
    total_return_5y: Optional[float] = None
    dividend_cash_return_1y: Optional[float] = None

    protection_price: Optional[float] = None
    trend_break_price: Optional[float] = None

    reasons: List[str] = field(default_factory=list)
    data_status: str = DataStatus.DATA_INSUFFICIENT.value

    quality_rank: Optional[int] = None
    entry_opportunity_rank: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
