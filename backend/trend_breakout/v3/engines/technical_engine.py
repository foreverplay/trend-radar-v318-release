"""
TechnicalEngine — Phase 3 wrapper around V2 staircase structure logic.

Reuses ``v2_structure.analyze_hist`` unchanged. Maps StructureResult →
TechnicalEngineResult. Does not rewrite platform/breakout/pullback math.
Does not mutate TREND_BOT_CONFIG into analyze_hist internals this phase
(config is read for documentation / pass-through only).

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

import numpy as np
import pandas as pd

# Ensure . is on path for ``v3`` + ``v2_structure``.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import TREND_BOT_CONFIG, get_config  # noqa: E402
from v3.types import DataStatus, Lifecycle  # noqa: E402
from v2_structure import StructureResult, analyze_hist  # noqa: E402


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class TechnicalEngineResult:
    """
    V3 technical evaluation of one hist series as-of T.

    ``technical_score`` == StructureResult.score (regression-stable).
    ``phase`` / ``phase_cn`` are raw V2 strings; ``lifecycle`` is the V3 enum.
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    technical_score: float = 0.0
    lifecycle: Lifecycle = Lifecycle.NONE
    phase: str = "NONE"
    phase_cn: str = ""
    # structure extras
    overheated: bool = False
    drop: bool = False
    drop_reason: str = ""
    grade_v2: Optional[str] = None  # S/A/B/C/OH from analyze_hist (not V3 Grade)
    pushable_core: bool = False
    kjs_tag: Optional[str] = None
    kjs_pattern: bool = False
    # breakdown / narrative
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    # key metrics for later engines
    close: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    atr: Optional[float] = None
    platform_high: Optional[float] = None
    platform_low: Optional[float] = None
    breakout_high: Optional[float] = None
    prev20high: Optional[float] = None
    vol_ratio: Optional[float] = None
    dist_ma20_pct: Optional[float] = None
    avg20_amt_yi: Optional[float] = None
    # full payloads
    metrics: Dict[str, Any] = field(default_factory=dict)
    flags: Dict[str, Any] = field(default_factory=dict)
    # config thresholds pass-through (documentation only this phase)
    config_thresholds: Dict[str, Any] = field(default_factory=dict)
    # raw V2 StructureResult as dict (debug / regression)
    structure: Dict[str, Any] = field(default_factory=dict)
    t_date: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        d["lifecycle"] = (
            self.lifecycle.value if isinstance(self.lifecycle, Lifecycle) else self.lifecycle
        )
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_hist(hist: pd.DataFrame, t_date: Optional[str]) -> pd.DataFrame:
    """Return hist with date <= t_date (no lookahead). Empty if nothing remains."""
    if hist is None or len(hist) == 0:
        return hist if hist is not None else pd.DataFrame()
    df = hist.copy()
    if "date" not in df.columns:
        return df
    df["date"] = pd.to_datetime(df["date"])
    if t_date:
        cutoff = pd.Timestamp(t_date)
        df = df.loc[df["date"] <= cutoff].copy()
    return df.sort_values("date").reset_index(drop=True)


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def _atr_from_hist(hist: pd.DataFrame) -> Optional[float]:
    """Prefer ATR14 column if present; else None (do not invent ATR this phase)."""
    if hist is None or len(hist) == 0:
        return None
    for col in ("ATR14", "atr14", "atr"):
        if col in hist.columns:
            return _safe_float(hist[col].iloc[-1])
    return None


def _prev20high(hist: pd.DataFrame) -> Optional[float]:
    if hist is None or len(hist) < 2 or "high" not in hist.columns:
        return None
    # prior 20 highs excluding T (common breakout reference)
    window = hist["high"].iloc[-21:-1] if len(hist) >= 21 else hist["high"].iloc[:-1]
    if len(window) == 0:
        return None
    return _safe_float(window.max())


def _data_status_from_structure(sr: StructureResult) -> DataStatus:
    reason = (sr.drop_reason or "").lower()
    if reason in ("hist_too_short", "hist_empty", "no_bars"):
        return DataStatus.DATA_INSUFFICIENT
    return DataStatus.VALID


def _map_structure(
    sr: StructureResult,
    *,
    hist: pd.DataFrame,
    t_date: Optional[str],
    cfg: Dict[str, Any],
) -> TechnicalEngineResult:
    metrics = dict(sr.metrics or {})
    risks = list(sr.risks or [])
    notes: List[str] = []
    tags: List[str] = []

    if sr.drop and sr.drop_reason:
        notes.append(f"drop:{sr.drop_reason}")
        tags.append(sr.drop_reason)
    for r in risks:
        if r not in tags:
            tags.append(r)
        notes.append(f"risk:{r}")
    if sr.overheated:
        tags.append("OVERHEATED")
    if sr.kjs_tag:
        tags.append(sr.kjs_tag)

    atr = _atr_from_hist(hist)
    if atr is not None:
        metrics.setdefault("atr", atr)
        metrics.setdefault("atr14", atr)

    p20 = _prev20high(hist)
    if p20 is not None:
        metrics.setdefault("prev20high", p20)

    # thresholds pass-through (platform / breakout / pullback / secondBreakout)
    thresholds = {
        "platform": dict(cfg.get("platform") or {}),
        "breakout": dict(cfg.get("breakout") or {}),
        "pullback": dict(cfg.get("pullback") or {}),
        "secondBreakout": dict(cfg.get("secondBreakout") or {}),
        "liquidity": dict(cfg.get("liquidity") or {}),
    }

    asof = t_date
    if not asof and len(hist) and "date" in hist.columns:
        asof = pd.Timestamp(hist["date"].iloc[-1]).strftime("%Y-%m-%d")

    return TechnicalEngineResult(
        ok=bool(sr.ok),
        data_status=_data_status_from_structure(sr),
        technical_score=float(sr.score or 0.0),
        lifecycle=Lifecycle.map_from_v2_phase(sr.phase),
        phase=sr.phase or "NONE",
        phase_cn=sr.phase_cn or "",
        overheated=bool(sr.overheated),
        drop=bool(sr.drop),
        drop_reason=sr.drop_reason or "",
        grade_v2=sr.grade,
        pushable_core=bool(sr.pushable_core),
        kjs_tag=sr.kjs_tag,
        kjs_pattern=bool(sr.kjs_pattern),
        score_breakdown=dict(sr.score_breakdown or {}),
        notes=notes,
        tags=tags,
        risks=risks,
        close=_safe_float(metrics.get("close")),
        ma20=_safe_float(metrics.get("ma20")),
        ma60=_safe_float(metrics.get("ma60")),
        atr=atr,
        platform_high=_safe_float(metrics.get("platform_high")),
        platform_low=_safe_float(metrics.get("platform_low")),
        breakout_high=_safe_float(metrics.get("breakout_high")),
        prev20high=p20,
        vol_ratio=_safe_float(metrics.get("vol_ratio")),
        dist_ma20_pct=_safe_float(metrics.get("dist_ma20_pct")),
        avg20_amt_yi=_safe_float(metrics.get("avg20_amt_yi")),
        metrics=metrics,
        flags=dict(sr.flags or {}),
        config_thresholds=thresholds,
        structure=sr.to_dict(),
        t_date=asof,
        error=None,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TechnicalEngine:
    """
    Thin adapter: truncate hist → ``analyze_hist`` → TechnicalEngineResult.

    Config is accepted for API stability / documentation pass-through; this
    phase does **not** feed thresholds into analyze_hist internals.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config if config is not None else get_config()

    def evaluate(
        self,
        hist: pd.DataFrame,
        *,
        t_date: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> TechnicalEngineResult:
        cfg = config if config is not None else self.config
        if cfg is None:
            cfg = TREND_BOT_CONFIG

        try:
            if hist is None or not isinstance(hist, pd.DataFrame) or len(hist) == 0:
                return TechnicalEngineResult(
                    ok=False,
                    data_status=DataStatus.DATA_INSUFFICIENT,
                    technical_score=0.0,
                    lifecycle=Lifecycle.NONE,
                    phase="NONE",
                    phase_cn="",
                    drop=True,
                    drop_reason="hist_empty",
                    notes=["drop:hist_empty"],
                    tags=["hist_empty"],
                    t_date=t_date,
                    config_thresholds={
                        "platform": dict(cfg.get("platform") or {}),
                        "breakout": dict(cfg.get("breakout") or {}),
                        "pullback": dict(cfg.get("pullback") or {}),
                        "secondBreakout": dict(cfg.get("secondBreakout") or {}),
                    },
                )

            truncated = _truncate_hist(hist, t_date)
            if truncated is None or len(truncated) == 0:
                return TechnicalEngineResult(
                    ok=False,
                    data_status=DataStatus.DATA_INSUFFICIENT,
                    technical_score=0.0,
                    lifecycle=Lifecycle.NONE,
                    phase="NONE",
                    phase_cn="",
                    drop=True,
                    drop_reason="hist_empty_after_truncate",
                    notes=["drop:hist_empty_after_truncate"],
                    tags=["hist_empty_after_truncate"],
                    t_date=t_date,
                )

            if len(truncated) < 70:
                # Still call analyze_hist for identical drop_reason behavior
                sr = analyze_hist(truncated)
                out = _map_structure(sr, hist=truncated, t_date=t_date, cfg=cfg)
                if out.data_status == DataStatus.VALID and sr.drop_reason == "hist_too_short":
                    out.data_status = DataStatus.DATA_INSUFFICIENT
                return out

            sr = analyze_hist(truncated)
            return _map_structure(sr, hist=truncated, t_date=t_date, cfg=cfg)

        except Exception as exc:  # noqa: BLE001 — surface as ERROR status
            return TechnicalEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                technical_score=0.0,
                lifecycle=Lifecycle.NONE,
                phase="NONE",
                phase_cn="",
                drop=True,
                drop_reason="ERROR",
                notes=[f"error:{type(exc).__name__}"],
                tags=["ERROR"],
                t_date=t_date,
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Smoke / regression (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="TechnicalEngine smoke + regression")
    parser.add_argument(
        "--parquet",
        default=str(_PKG_LOCAL_DAILY / "002768.parquet"),
    )
    parser.add_argument("--t-date", default="2026-09-21")
    args = parser.parse_args()

    hist = pd.read_parquet(args.parquet)
    # Same truncate for both paths
    truncated = _truncate_hist(hist, args.t_date)

    direct = analyze_hist(truncated)
    engine = TechnicalEngine()
    result = engine.evaluate(hist, t_date=args.t_date)

    same_score = float(direct.score) == float(result.technical_score)
    same_phase = (direct.phase or "NONE") == (result.phase or "NONE")

    print("=== TechnicalEngine smoke ===")
    print(f"symbol parquet : {args.parquet}")
    print(f"t_date         : {args.t_date}")
    print(f"bars           : {len(truncated)}")
    print(f"technical_score: {result.technical_score}")
    print(f"lifecycle      : {result.lifecycle.value}")
    print(f"phase          : {result.phase} ({result.phase_cn})")
    print(f"data_status    : {result.data_status.value}")
    print(f"close / ma20   : {result.close} / {result.ma20}")
    print(f"atr            : {result.atr}")
    print(f"platform H/L   : {result.platform_high} / {result.platform_low}")
    print(f"breakout_high  : {result.breakout_high}")
    print(f"score_breakdown: {result.score_breakdown}")
    print(f"tags           : {result.tags}")
    print("--- regression ---")
    print(f"analyze_hist score={direct.score} phase={direct.phase}")
    print(f"engine.evaluate  score={result.technical_score} phase={result.phase}")
    print(f"same_score={same_score} same_phase={same_phase}")

    assert same_score, f"score mismatch: {direct.score} vs {result.technical_score}"
    assert same_phase, f"phase mismatch: {direct.phase} vs {result.phase}"
    print("PASS: technical_score and raw phase match analyze_hist")


if __name__ == "__main__":
    _smoke()
