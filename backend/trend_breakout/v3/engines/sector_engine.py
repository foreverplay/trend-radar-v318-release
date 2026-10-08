"""
SectorEngine — Phase 4 wrapper around V2 SectorEngine + SectorTrendEngine.

Reuses ``v2_sector.SectorEngine`` and ``v2_sector_trend.SectorTrendEngine``.
Maps dailyTrend STRONG_UP/UP/NEUTRAL/DOWN → sectorTrend UP/FLAT/DOWN.
Truncates hist to ``t_date`` (no lookahead).

Does NOT cut over live V2.3 push/scan.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
try:
    from paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
except ImportError:  # packaged / flat layout fallback
    try:
        from trend_breakout.paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
    except ImportError:
        from pathlib import Path as __P
        import os as __os
        _PKG_ROOT = __P(__os.environ.get("TREND_BREAKOUT_ROOT", __P(__file__).resolve().parent))
        _k = __P(__os.environ.get("ASHARE_KLINE_ROOT", str(_PKG_ROOT.parent.parent / "data" / "sample" / "kline")))
        _PKG_LOCAL_DAILY = _k / "raw" / "daily"
        _PKG_CACHE = _PKG_ROOT / "cache"
        _PKG_CONT_CACHE = _PKG_CACHE / "continuity_history"
from typing import Any, Dict, List, Optional, Union

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import TREND_BOT_CONFIG, get_config  # noqa: E402
from v3.types import DataStatus  # noqa: E402
from v2_sector import SectorEngine as V2SectorEngine  # noqa: E402
from v2_sector_trend import SectorTrendEngine  # noqa: E402

LOCAL_DAILY = _PKG_LOCAL_DAILY
# ---------------------------------------------------------------------------
# Trend map: V2.3 STRONG_UP/UP/NEUTRAL/DOWN → V3 UP/FLAT/DOWN
# ---------------------------------------------------------------------------

_TREND_MAP = {
    "STRONG_UP": "UP",
    "UP": "UP",
    "NEUTRAL": "FLAT",
    "DOWN": "DOWN",
}


def map_sector_trend(raw: Optional[str]) -> str:
    """Map SectorTrendEngine dailyTrend → UP/FLAT/DOWN."""
    key = (raw or "NEUTRAL").strip().upper()
    return _TREND_MAP.get(key, "FLAT")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SectorEngineResult:
    """
    V3 sector evaluation of one stock as-of T.

    ``sector_score`` prefers legacy SectorEngine score; falls back to
    SectorTrendEngine.legacySectorScore.
    ``sector_trend`` is UP/FLAT/DOWN (mapped from STRONG_UP/UP/NEUTRAL/DOWN).
    ``relative_strength`` is RS20 (stock20 − sector20).
    ``breadth_score`` / ``sector_continuity`` are continuity-ish breadth
    fields from SectorTrendEngine when available.
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    code: Optional[str] = None
    t_date: Optional[str] = None
    industry: Optional[str] = None
    sector_score: Optional[float] = None
    sector_trend: str = "FLAT"  # UP | FLAT | DOWN
    sector_trend_raw: Optional[str] = None  # STRONG_UP/UP/NEUTRAL/DOWN
    sector_trend_cn: Optional[str] = None
    daily_trend_score: Optional[float] = None
    monthly_trend: Optional[str] = None
    monthly_trend_score: Optional[float] = None
    relative_strength: Optional[float] = None
    relative_strength_bucket: Optional[str] = None
    breadth_score: Optional[float] = None
    sector_continuity: Optional[float] = None  # alias / continuity-ish
    peers_n: int = 0
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    # nested raw payloads
    legacy_sector: Dict[str, Any] = field(default_factory=dict)
    sector_trend_payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _truncate_hist(hist: Optional[pd.DataFrame], t_date: Optional[str]) -> Optional[pd.DataFrame]:
    if hist is None or not isinstance(hist, pd.DataFrame) or len(hist) == 0:
        return hist
    h = hist.copy()
    if "date" in h.columns:
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values("date")
        if t_date:
            h = h[h["date"] <= pd.Timestamp(t_date)]
    return h.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SectorEngine:
    """
    V3 SectorEngine — wraps V2 SectorEngine + SectorTrendEngine.

    ``evaluate(code6, hist, t_date, ...)`` truncates hist to t_date and
    returns sectorScore / sectorTrend / relativeStrength / breadth.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        v2_sector: Optional[V2SectorEngine] = None,
        sector_trend: Optional[SectorTrendEngine] = None,
    ) -> None:
        self.config = config if config is not None else get_config()
        self._v2 = v2_sector or V2SectorEngine()
        self._trend = sector_trend or SectorTrendEngine(sector_engine=self._v2)

    def bind_fundamental(self, fund_engine) -> None:
        self._v2.bind_fundamental(fund_engine)
        self._trend.bind_fundamental(fund_engine)

    def load(self) -> None:
        self._v2.load()
        self._trend.load()

    def evaluate(
        self,
        code6: str,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
        **kwargs: Any,
    ) -> SectorEngineResult:
        code = _norm_code(code6)
        concept = kwargs.get("concept")
        try:
            truncated = _truncate_hist(hist, t_date)

            # Legacy sector score / RS / industry
            legacy = self._v2.evaluate(
                code, hist=truncated, t_date=t_date, concept=concept
            )
            legacy_d = legacy.to_dict() if hasattr(legacy, "to_dict") else {}

            # Sector trend (daily/monthly + breadth + RS20)
            trend_res = self._trend.evaluate(
                code,
                hist=truncated,
                t_date=t_date,
                legacy_sector=legacy_d,
            )
            trend_d = trend_res.to_dict() if hasattr(trend_res, "to_dict") else {}

            # Score preference: legacy SectorEngine.score → trend.legacySectorScore
            sector_score = legacy.score
            if sector_score is None:
                sector_score = trend_res.legacySectorScore

            raw_trend = trend_res.dailyTrend or "NEUTRAL"
            sector_trend = map_sector_trend(raw_trend)

            rs = trend_res.relativeStrength20
            if rs is None:
                rs = (legacy.metrics or {}).get("relative_strength")

            breadth = trend_res.breadthScore
            # continuity-ish: prefer breadth; else sector_ma_stack_share * 100
            continuity = breadth
            if continuity is None:
                share = (legacy.metrics or {}).get("sector_ma_stack_share")
                if share is not None:
                    continuity = round(float(share) * 100.0, 1)

            # data_status
            if legacy.data_insufficient and trend_res.data_insufficient:
                status = DataStatus.DATA_INSUFFICIENT
                ok = False
            elif not legacy.ok and not trend_res.ok:
                status = DataStatus.DATA_INSUFFICIENT
                ok = False
            else:
                status = DataStatus.VALID
                ok = True

            industry = legacy.industry or trend_res.name
            tags = list(dict.fromkeys((legacy.tags or []) + (trend_res.tags or [])))
            notes = list(dict.fromkeys((legacy.notes or []) + (trend_res.notes or [])))

            metrics: Dict[str, Any] = {
                "industry": industry,
                "sector_score": sector_score,
                "sector_trend": sector_trend,
                "sector_trend_raw": raw_trend,
                "relative_strength": None if rs is None else round(float(rs), 3),
                "breadth_score": breadth,
                "sector_continuity": continuity,
                "daily_trend_score": trend_res.dailyTrendScore,
                "monthly_trend": trend_res.monthlyTrend,
                "monthly_trend_score": trend_res.monthlyTrendScore,
                "peers_n": int(trend_res.peers_n or (legacy.metrics or {}).get("peers_n") or 0),
                "legacy_metrics": dict(legacy.metrics or {}),
                "trend_metrics": dict(trend_res.metrics or {}),
            }

            return SectorEngineResult(
                ok=ok,
                data_status=status,
                code=code,
                t_date=t_date,
                industry=industry,
                sector_score=None if sector_score is None else float(sector_score),
                sector_trend=sector_trend,
                sector_trend_raw=raw_trend,
                sector_trend_cn=trend_res.dailyTrendCn,
                daily_trend_score=trend_res.dailyTrendScore,
                monthly_trend=trend_res.monthlyTrend,
                monthly_trend_score=trend_res.monthlyTrendScore,
                relative_strength=None if rs is None else round(float(rs), 3),
                relative_strength_bucket=trend_res.relativeStrengthBucket,
                breadth_score=breadth,
                sector_continuity=continuity,
                peers_n=int(trend_res.peers_n or (legacy.metrics or {}).get("peers_n") or 0),
                tags=tags,
                notes=notes,
                metrics=metrics,
                legacy_sector=legacy_d,
                sector_trend_payload=trend_d,
            )

        except Exception as exc:  # noqa: BLE001
            return SectorEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code,
                t_date=t_date,
                sector_trend="FLAT",
                notes=[f"error:{type(exc).__name__}"],
                tags=["ERROR"],
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Smoke (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SectorEngine smoke")
    parser.add_argument("--code", default="002768")
    parser.add_argument(
        "--parquet",
        default=str(_PKG_LOCAL_DAILY / "002768.parquet"),
    )
    parser.add_argument("--t-date", default="2026-09-21")
    args = parser.parse_args()

    hist = pd.read_parquet(args.parquet)
    eng = SectorEngine()
    r = eng.evaluate(args.code, hist=hist, t_date=args.t_date)

    print("=== SectorEngine smoke ===")
    print(f"code           : {r.code}")
    print(f"t_date         : {r.t_date}")
    print(f"ok / status    : {r.ok} / {r.data_status.value}")
    print(f"industry       : {r.industry}")
    print(f"sectorScore    : {r.sector_score}")
    print(f"sectorTrend    : {r.sector_trend} (raw={r.sector_trend_raw})")
    print(f"relativeStrength: {r.relative_strength} ({r.relative_strength_bucket})")
    print(f"breadthScore   : {r.breadth_score}")
    print(f"sectorContinuity: {r.sector_continuity}")
    print(f"dailyTrendScore: {r.daily_trend_score}")
    print(f"monthlyTrend   : {r.monthly_trend} ({r.monthly_trend_score})")
    print(f"peers_n        : {r.peers_n}")
    print(f"tags           : {r.tags}")
    print(f"notes          : {r.notes}")
    if r.error:
        print(f"error          : {r.error}")

    assert r.data_status != DataStatus.ERROR, f"SectorEngine ERROR: {r.error}"
    assert r.sector_trend in ("UP", "FLAT", "DOWN")
    # No lookahead: last hist date after truncate <= t_date
    truncated = _truncate_hist(hist, args.t_date)
    if truncated is not None and "date" in truncated.columns and len(truncated):
        last = pd.Timestamp(truncated["date"].iloc[-1])
        assert last <= pd.Timestamp(args.t_date), "lookahead: hist beyond t_date"
    print("PASS: SectorEngine sectorScore/trend/RS")


if __name__ == "__main__":
    _smoke()
