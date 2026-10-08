"""
ContinuityEngine — Phase 6 wrapper around V2 ContinuityEngine.

Reuses ``v2_continuity.ContinuityEngine.evaluate`` unchanged.
Truncates hist to ``t_date`` (no lookahead). Surfaces continuity_score,
consumption_score, continuity_type, deltas (1/3/5), data_status, tags/notes.

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
from v3.types import DataStatus  # noqa: E402
from v2_continuity import ContinuityEngine as V2ContinuityEngine  # noqa: E402
from v2_continuity import ContinuityResult  # noqa: E402

LOCAL_DAILY = _PKG_LOCAL_DAILY
# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ContinuityEngineResult:
    """
    V3 continuity evaluation of one hist series as-of T.

    Scores/types/deltas mirror V2 ContinuityResult. ``data_status`` maps
    V2 ``data_insufficient`` → DATA_INSUFFICIENT (partial fund/sector still
    may have ok=True with a renormalized score).
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    code: Optional[str] = None
    t_date: Optional[str] = None
    continuity_score: Optional[float] = None
    consumption_score: Optional[float] = None
    continuity_type: str = "TYPE_D"
    continuity_type_cn: str = ""
    star: str = ""
    delta1: Optional[float] = None
    delta3: Optional[float] = None
    delta5: Optional[float] = None
    delta_label: Optional[str] = None
    sector_continuity: Optional[float] = None
    overextended: bool = False
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    favorable: List[str] = field(default_factory=list)
    limiting: List[str] = field(default_factory=list)
    insufficient_parts: List[str] = field(default_factory=list)
    components: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    # raw V2 payload
    continuity: Dict[str, Any] = field(default_factory=dict)
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


class ContinuityEngine:
    """
    V3 ContinuityEngine — wraps ``v2_continuity.ContinuityEngine``.

    Signature mirrors V2::

        evaluate(code, hist, t_date=None, fund=None, sector=None,
                 phase=None, persist=True)
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        v2: Optional[V2ContinuityEngine] = None,
        fund_engine=None,
        sector_engine=None,
    ) -> None:
        self.config = config if config is not None else get_config()
        self._v2 = v2 or V2ContinuityEngine(
            fund_engine=fund_engine, sector_engine=sector_engine
        )

    @property
    def v2(self) -> V2ContinuityEngine:
        return self._v2

    def bind(self, fund_engine=None, sector_engine=None) -> None:
        self._v2.bind(fund_engine=fund_engine, sector_engine=sector_engine)

    def evaluate(
        self,
        code: str,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
        fund: Optional[Dict[str, Any]] = None,
        sector: Optional[Dict[str, Any]] = None,
        phase: Optional[str] = None,
        persist: bool = True,
    ) -> ContinuityEngineResult:
        code6 = _norm_code(code)
        try:
            truncated = _truncate_hist(hist, t_date)
            if truncated is None:
                truncated = pd.DataFrame()

            cr: ContinuityResult = self._v2.evaluate(
                code6,
                truncated,
                t_date=t_date,
                fund=fund,
                sector=sector,
                phase=phase,
                persist=persist,
            )
            cont_d = cr.to_dict() if hasattr(cr, "to_dict") else {}

            if cr.data_insufficient and not cr.ok:
                status = DataStatus.DATA_INSUFFICIENT
            elif cr.data_insufficient:
                # partial (fund/sector missing) but score still produced
                status = DataStatus.DATA_INSUFFICIENT
            elif cr.ok:
                status = DataStatus.VALID
            else:
                status = DataStatus.DATA_INSUFFICIENT

            return ContinuityEngineResult(
                ok=bool(cr.ok),
                data_status=status,
                code=code6,
                t_date=t_date,
                continuity_score=cr.continuity_score,
                consumption_score=cr.consumption_score,
                continuity_type=cr.continuity_type or "TYPE_D",
                continuity_type_cn=cr.continuity_type_cn or "",
                star=cr.star or "",
                delta1=cr.delta1,
                delta3=cr.delta3,
                delta5=cr.delta5,
                delta_label=cr.delta_label,
                sector_continuity=cr.sector_continuity,
                overextended=bool(cr.overextended),
                tags=list(cr.tags or []),
                notes=list(cr.notes or []),
                favorable=list(cr.favorable or []),
                limiting=list(cr.limiting or []),
                insufficient_parts=list(cr.insufficient_parts or []),
                components=dict(cr.components or {}),
                metrics=dict(cr.metrics or {}),
                continuity=cont_d,
            )
        except Exception as exc:  # noqa: BLE001
            return ContinuityEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code6,
                t_date=t_date,
                continuity_type="TYPE_D",
                tags=["CONT_EVAL_ERR"],
                notes=[f"cont_err:{type(exc).__name__}"],
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Smoke (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ContinuityEngine smoke")
    parser.add_argument("--code", default="002768")
    parser.add_argument(
        "--parquet",
        default=str(_PKG_LOCAL_DAILY / "002768.parquet"),
    )
    parser.add_argument("--t-date", default="2026-09-21")
    parser.add_argument(
        "--persist",
        action="store_true",
        default=False,
        help="persist daily continuity history (default off for smoke)",
    )
    args = parser.parse_args()

    hist = pd.read_parquet(args.parquet)
    # phase from TechnicalEngine (no invented detection here)
    from v3.engines.technical_engine import TechnicalEngine

    tech = TechnicalEngine().evaluate(hist, t_date=args.t_date)
    eng = ContinuityEngine()
    r = eng.evaluate(
        args.code,
        hist=hist,
        t_date=args.t_date,
        phase=tech.phase,
        persist=args.persist,
    )

    print("=== ContinuityEngine smoke ===")
    print(f"code              : {r.code}")
    print(f"t_date            : {r.t_date}")
    print(f"ok / status       : {r.ok} / {r.data_status.value}")
    print(f"continuity_score  : {r.continuity_score}")
    print(f"consumption_score : {r.consumption_score}")
    print(f"continuity_type   : {r.continuity_type} ({r.continuity_type_cn})")
    print(f"delta1/3/5        : {r.delta1} / {r.delta3} / {r.delta5}")
    print(f"delta_label       : {r.delta_label}")
    print(f"phase (from tech) : {tech.phase}")
    print(f"tags              : {r.tags}")
    print(f"notes             : {r.notes[:6]}")
    if r.error:
        print(f"error             : {r.error}")

    assert r.data_status != DataStatus.ERROR, f"ContinuityEngine ERROR: {r.error}"
    # No lookahead
    truncated = _truncate_hist(hist, args.t_date)
    if truncated is not None and "date" in truncated.columns and len(truncated):
        last = pd.Timestamp(truncated["date"].iloc[-1])
        assert last <= pd.Timestamp(args.t_date), "lookahead: hist beyond t_date"
    print("PASS: ContinuityEngine continuity/consumption/deltas")


if __name__ == "__main__":
    _smoke()
