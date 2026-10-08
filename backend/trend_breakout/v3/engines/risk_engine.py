"""
RiskEngine — Phase 5 wrapper around V2 RiskEngine.

Reuses ``v2_risk.RiskEngine.evaluate`` unchanged.
Maps LOW/MID/HIGH → V3 RiskLevel via ``RiskLevel.map_from_v2``.
Truncates hist to ``t_date`` (no lookahead). Preserves tags + broken flag.

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
from typing import Any, Dict, List, Optional

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import get_config  # noqa: E402
from v3.types import DataStatus, RiskLevel  # noqa: E402
from v2_risk import RiskEngine as V2RiskEngine  # noqa: E402
from v2_risk import RiskResult  # noqa: E402

LOCAL_DAILY = _PKG_LOCAL_DAILY
# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RiskEngineResult:
    """
    V3 risk evaluation of one hist series as-of T.

    ``risk_level`` is the V3 enum (SAFE/WATCH/HIGH/BROKEN/…).
    ``risk_score_v2`` preserves the raw V2 LOW/MID/HIGH string.
    ``broken`` is exposed when V2 sets it; tags kept (HIGH_VOLUME_WEAK_CLOSE etc.).
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    code: Optional[str] = None
    t_date: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.WATCH
    risk_score_v2: str = "MID"  # LOW / MID / HIGH
    broken: bool = False
    break_risk: bool = False
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    checklist: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    # raw V2 payload
    risk: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        d["risk_level"] = (
            self.risk_level.value
            if isinstance(self.risk_level, RiskLevel)
            else self.risk_level
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
    """Return hist with date <= t_date (no lookahead)."""
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


class RiskEngine:
    """
    V3 RiskEngine — wraps ``v2_risk.RiskEngine``.

    ``evaluate(hist, platform_low=..., structure_risks=..., t_date=...)``
    truncates hist to t_date and maps V2 risk_score → RiskLevel.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        v2: Optional[V2RiskEngine] = None,
    ) -> None:
        self.config = config if config is not None else get_config()
        self._v2 = v2 or V2RiskEngine()

    @property
    def v2(self) -> V2RiskEngine:
        return self._v2

    def evaluate(
        self,
        hist: Optional[pd.DataFrame] = None,
        platform_low: Optional[float] = None,
        structure_risks: Optional[List[str]] = None,
        t_date: Optional[str] = None,
        *,
        code: Optional[str] = None,
    ) -> RiskEngineResult:
        code6 = _norm_code(code) if code else None
        try:
            truncated = _truncate_hist(hist, t_date)
            if truncated is None:
                truncated = pd.DataFrame()

            rr: RiskResult = self._v2.evaluate(
                truncated,
                platform_low=platform_low,
                structure_risks=structure_risks,
            )
            risk_d = rr.to_dict() if hasattr(rr, "to_dict") else {}

            tags = list(rr.tags or [])
            notes = list(rr.notes or [])
            broken = bool(rr.broken)
            # structure at risk but not yet broken → BREAK_RISK
            break_risk = (not broken) and ("TREND_FAILURE" in tags)

            risk_score_v2 = (rr.risk_score or "MID").strip().upper()
            risk_level = RiskLevel.map_from_v2(
                risk_score_v2, broken=broken, break_risk=break_risk
            )

            # HIST_SHORT → still VALID but note; short hist is MID by V2 design
            if "HIST_SHORT" in tags:
                status = DataStatus.DATA_INSUFFICIENT
                ok = False
            else:
                status = DataStatus.VALID
                ok = True

            return RiskEngineResult(
                ok=ok,
                data_status=status,
                code=code6,
                t_date=t_date,
                risk_level=risk_level,
                risk_score_v2=risk_score_v2,
                broken=broken,
                break_risk=break_risk,
                tags=tags,
                notes=notes,
                checklist=dict(rr.checklist or {}),
                metrics=dict(rr.metrics or {}),
                risk=risk_d,
            )
        except Exception as exc:  # noqa: BLE001
            return RiskEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code6,
                t_date=t_date,
                risk_level=RiskLevel.WATCH,
                risk_score_v2="MID",
                tags=["RISK_EVAL_ERR"],
                notes=[f"risk_err:{type(exc).__name__}"],
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Smoke (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RiskEngine smoke")
    parser.add_argument("--code", default="002768")
    parser.add_argument(
        "--parquet",
        default=str(_PKG_LOCAL_DAILY / "002768.parquet"),
    )
    parser.add_argument("--t-date", default="2026-09-21")
    args = parser.parse_args()

    hist = pd.read_parquet(args.parquet)
    eng = RiskEngine()
    r = eng.evaluate(hist=hist, t_date=args.t_date, code=args.code)

    print("=== RiskEngine smoke ===")
    print(f"code           : {r.code}")
    print(f"t_date         : {r.t_date}")
    print(f"ok / status    : {r.ok} / {r.data_status.value}")
    print(f"risk_level     : {r.risk_level.value}")
    print(f"risk_score_v2  : {r.risk_score_v2}")
    print(f"broken         : {r.broken}")
    print(f"break_risk     : {r.break_risk}")
    print(f"tags           : {r.tags}")
    print(f"notes          : {r.notes}")
    if r.error:
        print(f"error          : {r.error}")

    assert r.data_status != DataStatus.ERROR, f"RiskEngine ERROR: {r.error}"
    assert isinstance(r.risk_level, RiskLevel)
    assert r.risk_score_v2 in ("LOW", "MID", "HIGH")
    # No lookahead
    truncated = _truncate_hist(hist, args.t_date)
    if truncated is not None and "date" in truncated.columns and len(truncated):
        last = pd.Timestamp(truncated["date"].iloc[-1])
        assert last <= pd.Timestamp(args.t_date), "lookahead: hist beyond t_date"
    # map consistency
    expected = RiskLevel.map_from_v2(
        r.risk_score_v2, broken=r.broken, break_risk=r.break_risk
    )
    assert r.risk_level == expected, f"map mismatch {r.risk_level} vs {expected}"
    print("PASS: RiskEngine risk_level/tags/broken")


if __name__ == "__main__":
    _smoke()
