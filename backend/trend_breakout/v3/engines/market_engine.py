"""
MarketEngine — Phase 4 wrapper around V2 market_meta / compute_market_meta.

Reuses ``v2_staircase_scan.compute_market_meta`` (lazy import) or an existing
``market_meta.json``. Maps → Plan regimes via ``regime_map``, derives
trend UP/FLAT/DOWN, and on success writes the daily regime archive
(idempotent by t_date).

Does NOT cut over live V2.3 push/scan.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import TREND_BOT_CONFIG, get_config  # noqa: E402
from v3.market_regime_archive import (  # noqa: E402
    ARCHIVE_CSV,
    ARCHIVE_JSONL,
    append_from_market_meta,
)
from v3.regime_map import (  # noqa: E402
    market_score_from_meta,
    pair_from_market_meta,
)
from v3.types import DataStatus, MarketRegime, MarketScore  # noqa: E402

DEFAULT_META_PATH = _ROOT / "market_meta.json"

# Lazy-loaded compute_market_meta (avoids heavy import at package load).
_compute_market_meta_fn = None


def _get_compute_market_meta():
    """Import compute_market_meta from v2_staircase_scan only when needed."""
    global _compute_market_meta_fn
    if _compute_market_meta_fn is not None:
        return _compute_market_meta_fn
    # Import carefully: module has no import-time side effects beyond deps.
    from v2_staircase_scan import compute_market_meta as _fn  # noqa: WPS433

    _compute_market_meta_fn = _fn
    return _compute_market_meta_fn


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MarketEngineResult:
    """
    V3 market evaluation as-of T.

    Dual regime labels: ``regime_v3`` (BULL/NORMAL/BEAR/CHOP) +
    ``legacy_regime`` (STRONG/NORMAL/WEAK/RISK_OFF) + ``risk_off``.
    ``trend`` is a coarse UP/FLAT/DOWN derived from indices / score.
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    t_date: Optional[str] = None
    regime_v3: MarketRegime = MarketRegime.NORMAL
    legacy_regime: str = "NORMAL"
    risk_off: bool = False
    market_score: float = 0.0
    trend: str = "FLAT"  # UP | FLAT | DOWN
    cap: int = 10
    notes: List[str] = field(default_factory=list)
    breadth: Dict[str, Any] = field(default_factory=dict)
    turnover: Dict[str, Any] = field(default_factory=dict)
    indices: Dict[str, Any] = field(default_factory=dict)
    # archive write result (if attempted)
    archive: Optional[Dict[str, Any]] = None
    # raw market_meta dict
    market_meta: Dict[str, Any] = field(default_factory=dict)
    source: str = ""  # compute_market_meta | market_meta.json | kwargs.meta
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        d["regime_v3"] = (
            self.regime_v3.value
            if isinstance(self.regime_v3, MarketRegime)
            else self.regime_v3
        )
        return d

    def to_market_score(self) -> MarketScore:
        return MarketScore(
            t_date=str(self.t_date or ""),
            market_score=float(self.market_score),
            regime_v3=self.regime_v3,
            legacy_regime=self.legacy_regime,
            risk_off=self.risk_off,
            cap=int(self.cap),
            notes=list(self.notes),
            breadth=dict(self.breadth),
            turnover=dict(self.turnover),
            indices=dict(self.indices),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_trend(meta: dict) -> str:
    """
    Coarse market trend UP/FLAT/DOWN from market_meta shape.

    Heuristic (no new network calls):
      - RISK_OFF / BEAR-ish → DOWN
      - avg index pct + above_ma20 share + market_score
    """
    legacy = str(meta.get("regime_v2") or meta.get("legacy_regime") or "").upper()
    if legacy == "RISK_OFF":
        return "DOWN"
    ms = float(meta.get("market_score") or 0.0)
    indices = meta.get("indices") or {}
    pcts: List[float] = []
    n_above = 0
    n_idx = 0
    for info in indices.values():
        if not isinstance(info, dict):
            continue
        n_idx += 1
        p = info.get("pct")
        if p is not None:
            try:
                pcts.append(float(p))
            except (TypeError, ValueError):
                pass
        if info.get("above_ma20"):
            n_above += 1
    avg_pct = sum(pcts) / len(pcts) if pcts else 0.0
    above_share = (n_above / n_idx) if n_idx else 0.5

    # Strong up
    if ms >= 65 and avg_pct > 0.3 and above_share >= 0.5:
        return "UP"
    if ms >= 70 and avg_pct >= 0:
        return "UP"
    # Down
    if ms <= 40 or avg_pct <= -0.5 or (legacy == "WEAK" and avg_pct < 0):
        return "DOWN"
    if ms < 50 and avg_pct < 0 and above_share < 0.5:
        return "DOWN"
    return "FLAT"


def _load_meta_file(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            meta = json.load(f)
        if isinstance(meta, dict) and meta.get("t_date"):
            return meta
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _result_from_meta(
    meta: dict,
    *,
    source: str,
    write_archive: bool = True,
) -> MarketEngineResult:
    pair = pair_from_market_meta(meta)
    ms_obj = market_score_from_meta(meta)
    trend = _derive_trend(meta)
    notes = list(ms_obj.notes)
    archive_info: Optional[Dict[str, Any]] = None
    if write_archive:
        try:
            archive_info = append_from_market_meta(meta)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"archive_error:{type(exc).__name__}")
            archive_info = {"wrote": False, "error": f"{type(exc).__name__}: {exc}"}

    gaps = meta.get("data_gaps") or []
    data_status = DataStatus.VALID
    # Soft: gaps don't fail the engine if we still have a score/regime
    if not meta.get("regime_v2") and not meta.get("legacy_regime") and not meta.get("regime"):
        data_status = DataStatus.DATA_INSUFFICIENT

    return MarketEngineResult(
        ok=True,
        data_status=data_status,
        t_date=str(meta.get("t_date") or ms_obj.t_date),
        regime_v3=pair.regime_v3,
        legacy_regime=pair.legacy_regime,
        risk_off=pair.risk_off,
        market_score=float(ms_obj.market_score),
        trend=trend,
        cap=int(ms_obj.cap),
        notes=notes,
        breadth=dict(ms_obj.breadth),
        turnover=dict(ms_obj.turnover),
        indices=dict(ms_obj.indices),
        archive=archive_info,
        market_meta=dict(meta),
        source=source,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MarketEngine:
    """
    Evaluate market regime as-of ``t_date``.

    Priority:
      1. ``meta=`` kwarg (pre-built market_meta dict)
      2. ``spot=`` provided → call ``compute_market_meta(t_date, spot)``
      3. ``force_compute=True`` → call ``compute_market_meta`` (spot may be None)
      4. ``meta_path`` / default ``market_meta.json`` if t_date matches (or
         ``allow_stale_meta=True``)
      5. else attempt ``compute_market_meta(t_date, spot=None)``
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config if config is not None else get_config()

    def evaluate(
        self,
        t_date: str,
        spot: Optional[pd.DataFrame] = None,
        **kwargs: Any,
    ) -> MarketEngineResult:
        write_archive = bool(kwargs.get("write_archive", True))
        force_compute = bool(kwargs.get("force_compute", False))
        allow_stale_meta = bool(kwargs.get("allow_stale_meta", False))
        meta_path = kwargs.get("meta_path")
        prebuilt = kwargs.get("meta")

        try:
            # 1) explicit meta dict
            if isinstance(prebuilt, dict) and prebuilt.get("t_date"):
                return _result_from_meta(
                    prebuilt, source="kwargs.meta", write_archive=write_archive
                )

            # 2) spot provided → always compute
            if spot is not None or force_compute:
                fn = _get_compute_market_meta()
                meta = fn(str(t_date), spot)
                return _result_from_meta(
                    meta, source="compute_market_meta", write_archive=write_archive
                )

            # 3) try local market_meta.json (smoke / offline)
            path = Path(meta_path) if meta_path else DEFAULT_META_PATH
            file_meta = _load_meta_file(path)
            if file_meta is not None:
                file_td = str(file_meta.get("t_date") or "")
                if file_td == str(t_date) or allow_stale_meta:
                    src = f"market_meta.json:{path.name}"
                    return _result_from_meta(
                        file_meta, source=src, write_archive=write_archive
                    )

            # 4) fallback: live compute without spot
            fn = _get_compute_market_meta()
            meta = fn(str(t_date), None)
            return _result_from_meta(
                meta, source="compute_market_meta", write_archive=write_archive
            )

        except Exception as exc:  # noqa: BLE001
            return MarketEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                t_date=str(t_date),
                regime_v3=MarketRegime.NORMAL,
                legacy_regime="NORMAL",
                risk_off=False,
                market_score=0.0,
                trend="FLAT",
                cap=0,
                notes=[f"error:{type(exc).__name__}"],
                error=f"{type(exc).__name__}: {exc}",
                source="error",
            )


# ---------------------------------------------------------------------------
# Smoke (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="MarketEngine smoke")
    parser.add_argument("--t-date", default="2026-09-23")
    parser.add_argument(
        "--meta-path",
        default=str(DEFAULT_META_PATH),
        help="existing market_meta.json for offline smoke",
    )
    parser.add_argument(
        "--force-compute",
        action="store_true",
        help="call compute_market_meta even if meta file exists",
    )
    args = parser.parse_args()

    eng = MarketEngine()
    r1 = eng.evaluate(
        args.t_date,
        meta_path=args.meta_path,
        force_compute=args.force_compute,
        allow_stale_meta=True,
    )
    # Second call → archive idempotent (wrote=False if unchanged)
    r2 = eng.evaluate(
        args.t_date,
        meta=r1.market_meta if r1.ok else None,
        meta_path=args.meta_path,
        allow_stale_meta=True,
        force_compute=False,
    )

    print("=== MarketEngine smoke ===")
    print(f"t_date         : {r1.t_date}")
    print(f"ok / status    : {r1.ok} / {r1.data_status.value}")
    print(f"source         : {r1.source}")
    print(f"regime_v3      : {r1.regime_v3.value}")
    print(f"legacy_regime  : {r1.legacy_regime}")
    print(f"risk_off       : {r1.risk_off}")
    print(f"market_score   : {r1.market_score}")
    print(f"trend          : {r1.trend}")
    print(f"cap            : {r1.cap}")
    print(f"archive1       : {r1.archive}")
    print(f"archive2       : {r2.archive}")
    print(f"ARCHIVE_JSONL  : {ARCHIVE_JSONL}")
    print(f"ARCHIVE_CSV    : {ARCHIVE_CSV}")
    if r1.archive:
        print(f"archive n_rows : {r1.archive.get('n_rows')}")
    if r1.error:
        print(f"error          : {r1.error}")

    assert r1.ok, f"MarketEngine failed: {r1.error}"
    assert r1.regime_v3 in MarketRegime, "invalid regime_v3"
    assert r1.legacy_regime in ("STRONG", "NORMAL", "WEAK", "RISK_OFF")
    assert r1.trend in ("UP", "FLAT", "DOWN")
    # idempotent: second write should not change row count content
    if r2.archive is not None:
        assert r2.archive.get("wrote") is False or r2.archive.get("n_rows") == r1.archive.get(
            "n_rows"
        ), "archive not idempotent"
    print("PASS: MarketEngine + archive idempotent")


if __name__ == "__main__":
    _smoke()
