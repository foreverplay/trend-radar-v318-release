"""
DividendTrendEngine central config (V3.1.9 Phase 1).

Weights / thresholds below are Plan §27 initial defaults.
标记：待回测校准 — do NOT treat as production-calibrated.

Paths resolve from this package + optional .env overrides (portable; no /workspace).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


DIV_ROOT = Path(__file__).resolve().parents[1]  # .../dividend_v319
PKG_ROOT = DIV_ROOT.parents[2]  # .../trend-radar-v318-release


def _load_dotenv(pkg_root: Path) -> Dict[str, str]:
    """Minimal .env parse (last wins). Does not override existing os.environ."""
    path = pkg_root / ".env"
    found: Dict[str, str] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        name, value = raw.split("=", 1)
        found[name.strip()] = value.strip().strip('"').strip("'")
    return found


def _env_path(key: str, default: Path, dotenv: Dict[str, str]) -> Path:
    raw = os.environ.get(key) or dotenv.get(key) or ""
    if not raw:
        return default
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (PKG_ROOT / p).resolve()
    return p


_DOTENV = _load_dotenv(PKG_ROOT)

TREND_ROOT = _env_path(
    "TREND_BREAKOUT_ROOT",
    PKG_ROOT / "backend" / "trend_breakout",
    _DOTENV,
)
# Keep DIV_ROOT as package dir even if TREND_BREAKOUT_ROOT is overridden
# (engines live under dividend_v319/).

_FIN_ROOT = _env_path(
    "ASHARE_FINANCIALS_ROOT",
    PKG_ROOT / "data" / "ashare-financials",
    _DOTENV,
)
# Prefer DIVIDEND_KLINE_ROOT over ASHARE_KLINE_ROOT. Value may be either
# the daily parquet dir (.../raw/daily) or a kline root (.../dividend_kline).
_kline_raw = (
    os.environ.get("DIVIDEND_KLINE_ROOT")
    or _DOTENV.get("DIVIDEND_KLINE_ROOT")
    or ""
)
_used_ashare_fallback = False
if not _kline_raw:
    _kline_raw = os.environ.get("ASHARE_KLINE_ROOT") or _DOTENV.get("ASHARE_KLINE_ROOT") or ""
    _used_ashare_fallback = bool(_kline_raw)

if _kline_raw:
    _kline = Path(_kline_raw).expanduser()
    if not _kline.is_absolute():
        _kline = (PKG_ROOT / _kline).resolve()
else:
    _kline = (PKG_ROOT / "data" / "cache" / "dividend_kline" / "raw" / "daily").resolve()

if _kline.name != "daily":
    daily_cand = _kline / "raw" / "daily"
    # Growth ASHARE_KLINE_ROOT often points at a kline root; dividend live uses its own tree.
    if daily_cand.is_dir() or not any(_kline.glob("*.parquet")):
        _kline = daily_cand.resolve()

FHPS_DIR = _FIN_ROOT / "raw" / "fhps"
FHPS_ALL = FHPS_DIR / "fhps_2021_2025_all.parquet"
FIN_CLEAN = _FIN_ROOT / "clean"
FIN_META = _FIN_ROOT / "meta"
UNIVERSE_NON_ST = FIN_META / "universe_listed_non_st.csv"
KLINE_DAILY = _kline
KLINE_UNIVERSE = _kline.parent.parent / "meta" / "universe.csv" if _kline.name == "daily" else _FIN_ROOT / "meta" / "universe.csv"
HFQ_CACHE_ANALYSIS = _FIN_ROOT / "analysis" / "_hfq_daily_cache"
CONT5_CSV = _FIN_ROOT / "analysis" / "连续现金分红5年_后复权五年年化收益排行.csv"
CACHE_DIR = DIV_ROOT / "cache"
OUT_DIR = DIV_ROOT / "out"
DATA_DIR = DIV_ROOT / "data"
EVENTS_PATH = DATA_DIR / "dividend_events.parquet"
BFQ_DIR = DATA_DIR / "bfq_daily"


@dataclass
class DividendConfig:
    # --- universe ---
    quality_history_years: int = 5  # 待回测校准
    min_dividend_years: int = 3  # 待回测校准；Phase1 自主池默认用连续5年(2021-2025)
    autonomous_require_years: Tuple[str, ...] = ("2021", "2022", "2023", "2024", "2025")
    min_listing_years_for_trend: float = 3.0  # 待回测校准
    exclude_name_tokens: Tuple[str, ...] = ("ST", "退", "整理")
    # index pool (current snapshot only in Phase1)
    dividend_index_codes: Tuple[str, ...] = (
        "000922",  # 中证红利
        "000015",  # 上证红利
    )
    # assign_progress values that count as paid cash
    assign_progress_ok: Tuple[str, ...] = ("实施分配",)

    # --- trend MAs (红利更长周期) ---
    ma_short: int = 20
    ma_medium: int = 60
    ma_long: int = 120
    ma_very_long: int = 250

    # --- opportunity score weights (Plan §10) 待回测校准 ---
    quality_weight: float = 0.20
    sustainability_weight: float = 0.25
    trend_weight: float = 0.25
    total_return_weight: float = 0.15
    valuation_weight: float = 0.10
    risk_weight: float = 0.05  # applied to (100 - DividendRiskScore)

    # --- QualityEngine sub-weights (Plan §3) 待回测校准 ---
    # continuity25 / stability_growth20 / payout20 / cash_ability25 / record_quality10
    quality_component_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "continuity": 0.25,
            "stability_growth": 0.20,
            "payout": 0.20,
            "cash_ability": 0.25,
            "record_quality": 0.10,
        }
    )
    # --- SustainabilityEngine sub-weights (Plan §4) 待回测校准 ---
    sustainability_component_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "ocf_over_ni": 0.20,
            "fcf_over_div": 0.20,
            "payout": 0.15,
            "profit_continuity": 0.20,
            "revenue_stability": 0.10,
            "leverage": 0.15,
        }
    )
    # special-dividend heuristic (DPS vs other-year median) 待回测校准
    special_div_heuristic_multiple: float = 2.5

    # --- grade thresholds 待回测校准 ---
    grade_a_plus: float = 85.0
    grade_a: float = 75.0
    grade_b: float = 65.0
    grade_c: float = 50.0
    a_plus_min_quality: float = 75.0
    a_plus_min_sustainability: float = 75.0

    # --- TotalReturn policy (Phase1 decision) ---
    # See PHASE1_DATA_AUDIT.md. Do not double-count cash on qfq/hfq.
    total_return_method: str = "unadj_plus_cash_events"  # target
    total_return_fallback: str = "hfq_proxy_no_extra_cash"

    # --- costs (reuse growth backtest spirit; 待回测校准) ---
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage: float = 0.001

    def score_weights(self) -> Dict[str, float]:
        return {
            "quality": self.quality_weight,
            "sustainability": self.sustainability_weight,
            "trend": self.trend_weight,
            "total_return": self.total_return_weight,
            "valuation": self.valuation_weight,
            "risk": self.risk_weight,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # tuples → lists for JSON
        for k, v in list(d.items()):
            if isinstance(v, tuple):
                d[k] = list(v)
        d["_note"] = "待回测校准 — Plan §27 initial defaults"
        return d


DIVIDEND_CONFIG = DividendConfig()
