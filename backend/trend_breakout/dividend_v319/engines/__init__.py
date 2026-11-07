"""DividendTrendEngine package (V3.1.9). Independent from v3.engines Growth path."""

from .dividendUniverseEngine import DividendUniverseEngine, build_universe, write_universe_latest
from .dividendQualityEngine import DividendQualityEngine
from .dividendSustainabilityEngine import DividendSustainabilityEngine
from .dividendReturnEngine import DividendReturnEngine, compute_returns, compute_multi_horizon
from .dividendValuationEngine import DividendValuationEngine
from .dividendTechnicalEngine import DividendTechnicalEngine
from .dividendRiskEngine import DividendRiskEngine
from .dividendEntryEngine import DividendEntryEngine
from .dividendTrendEngine import (
    opportunity_score,
    assign_grade,
    historical_total_return_score,
)

__all__ = [
    "DividendUniverseEngine",
    "build_universe",
    "write_universe_latest",
    "DividendQualityEngine",
    "DividendSustainabilityEngine",
    "DividendReturnEngine",
    "compute_returns",
    "compute_multi_horizon",
    "DividendValuationEngine",
    "DividendTechnicalEngine",
    "DividendRiskEngine",
    "DividendEntryEngine",
    "opportunity_score",
    "assign_grade",
    "historical_total_return_score",
]
