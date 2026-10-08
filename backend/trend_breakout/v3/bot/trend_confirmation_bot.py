"""
TrendConfirmationBot — Phase 7+8 unified orchestrator.

Pipeline (Plan order):
  Market (optional shared) → Sector → Technical → Fundamental → Risk →
  Continuity → Lifecycle → grade_candidate → ExitEngine → TrendBotResult

Phase 8: ExitEngine fills warning / protection / break_trend prices + ExitSignal.
Does NOT cut over live V2.3 push/scan. No Backtest (Phase 9).
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import argparse
import sys
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import TREND_BOT_CONFIG, get_config  # noqa: E402
from v3.engines import (  # noqa: E402
    ContinuityEngine,
    ContinuityEngineResult,
    ExitEngine,
    ExitEngineResult,
    FundamentalEngine,
    FundamentalEngineResult,
    LifecycleEngine,
    LifecycleEngineResult,
    MarketEngine,
    MarketEngineResult,
    RiskEngine,
    RiskEngineResult,
    SectorEngine,
    SectorEngineResult,
    TechnicalEngine,
    TechnicalEngineResult,
)
from v3.exit_state import ExitStateStore, default_store  # noqa: E402
from v3.grading import GradeResult, grade_candidate  # noqa: E402
from v3.snapshot import build_stock_snapshot_from_hist  # noqa: E402
from v3.types import (  # noqa: E402
    ContinuityFields,
    DataStatus,
    ExitSignal,
    Grade,
    Lifecycle,
    MarketRegime,
    RiskLevel,
    SectorResult,
    StockSnapshot,
    TrendBotResult,
)

LOCAL_DAILY = _PKG_LOCAL_DAILY
# Grade sort order: A+ → A → B → WATCH → FILTER
_GRADE_RANK: Dict[str, int] = {
    Grade.A_PLUS.value: 0,
    "A+": 0,
    Grade.A.value: 1,
    "A": 1,
    Grade.B.value: 2,
    "B": 2,
    Grade.C.value: 3,
    "C": 3,
    Grade.WATCH.value: 4,
    "WATCH": 4,
    Grade.FILTER.value: 5,
    "FILTER": 5,
}


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


def _load_hist(code6: str, t_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    path = LOCAL_DAILY / f"{code6}.parquet"
    if not path.is_file():
        return None
    try:
        hist = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    return _truncate_hist(hist, t_date)


def _fmt_score(x: Optional[float], digits: int = 1) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if digits == 0:
        return str(int(round(v)))
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits > 0 else str(v)


def _grade_value(g: Union[Grade, str, None]) -> str:
    if isinstance(g, Grade):
        return g.value
    return str(g or Grade.FILTER.value)


def _status_value(s: Union[DataStatus, str, None]) -> str:
    if isinstance(s, DataStatus):
        return s.value
    return str(s or DataStatus.VALID.value)


def _escalate_status(*statuses: Union[DataStatus, str, None]) -> DataStatus:
    """ERROR wins; else DATA_INSUFFICIENT if any; else VALID."""
    vals = [_status_value(s) for s in statuses if s is not None]
    if DataStatus.ERROR.value in vals:
        return DataStatus.ERROR
    if DataStatus.DATA_INSUFFICIENT.value in vals:
        return DataStatus.DATA_INSUFFICIENT
    return DataStatus.VALID


def _fund_dict_for_continuity(fund: FundamentalEngineResult) -> Dict[str, Any]:
    """V2-shaped fund dict for ContinuityEngine (metrics / data_insufficient)."""
    d = dict(fund.fundamental or {})
    if not d:
        d = {
            "ok": bool(fund.ok),
            "score": fund.fund_score,
            "level": fund.level,
            "industry": fund.industry,
            "tags": list(fund.tags or []),
            "notes": list(fund.notes or []),
            "metrics": dict(fund.metrics or {}),
            "checklist": dict(fund.checklist or {}),
            "source": fund.source or "",
        }
    if fund.data_status == DataStatus.DATA_INSUFFICIENT or fund.fund_score is None:
        d["data_insufficient"] = True
    else:
        d.setdefault("data_insufficient", False)
    return d


def _sector_dict_for_continuity(sec: SectorEngineResult) -> Dict[str, Any]:
    """Prefer V2 legacy_sector shape (metrics.sector_return* etc.)."""
    d = dict(sec.legacy_sector or {})
    if d:
        if sec.data_status == DataStatus.DATA_INSUFFICIENT:
            d["data_insufficient"] = True
        return d
    # Fallback from V3 fields
    metrics = dict(sec.metrics or {})
    if sec.relative_strength is not None and "relative_strength" not in metrics:
        metrics["relative_strength"] = sec.relative_strength
    return {
        "ok": bool(sec.ok),
        "data_insufficient": sec.data_status != DataStatus.VALID,
        "score": sec.sector_score,
        "industry": sec.industry,
        "tags": list(sec.tags or []),
        "notes": list(sec.notes or []),
        "metrics": metrics,
        "source": "v3_sector_fallback",
    }


def _resolve_name(
    code6: str,
    name: Optional[str],
    spot_row: Optional[Any] = None,
) -> str:
    if name:
        return str(name).strip()
    # spot_row: Series / dict with 名称 or name
    if spot_row is not None:
        try:
            if isinstance(spot_row, dict):
                for k in ("名称", "name", "Name"):
                    if spot_row.get(k):
                        return str(spot_row[k]).strip()
            else:
                for k in ("名称", "name", "Name"):
                    if hasattr(spot_row, "get") and spot_row.get(k):
                        return str(spot_row.get(k)).strip()
                    if hasattr(spot_row, "__contains__") and k in spot_row:
                        v = spot_row[k]
                        if v is not None and str(v).strip():
                            return str(v).strip()
        except Exception:  # noqa: BLE001
            pass
    return ""


def sort_results(results: Sequence[TrendBotResult]) -> List[TrendBotResult]:
    """
    Sort: grade A+→A→B→WATCH→FILTER, then Continuity DESC, Tech DESC, Sector DESC.
    """

    def key(r: TrendBotResult):
        g = _grade_value(r.grade)
        rank = _GRADE_RANK.get(g, 99)
        cont = float(r.continuity_score) if r.continuity_score is not None else -1.0
        tech = float(r.tech_score) if r.tech_score is not None else -1.0
        sec = float(r.sector_score) if r.sector_score is not None else -1.0
        return (rank, -cont, -tech, -sec, r.code)

    return sorted(results, key=key)


def results_to_markdown_table(results: Sequence[TrendBotResult]) -> str:
    """
    Unified markdown table (Plan §26 columns).

    股票 / 阶段 / 等级 / Tech / Fund / Sector / Cont / 消耗 / 风险 /
    保护价 / 破坏价 / ExitSignal

    protection / break show numeric prices from ExitEngine (Phase 8).
    """
    headers = [
        "股票",
        "阶段",
        "等级",
        "Tech",
        "Fund",
        "Sector",
        "Cont",
        "消耗",
        "风险",
        "保护价",
        "破坏价",
        "ExitSignal",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for r in sort_results(results):
        name = (r.name or "").strip()
        stock = f"{name}({r.code})" if name else r.code
        life = (
            r.lifecycle.value
            if isinstance(r.lifecycle, Lifecycle)
            else str(r.lifecycle or "NONE")
        )
        grade = _grade_value(r.grade)
        risk = (
            r.risk_level.value
            if isinstance(r.risk_level, RiskLevel)
            else str(r.risk_level or "WATCH")
        )
        exit_sig = (
            r.exit_signal.value
            if isinstance(r.exit_signal, ExitSignal)
            else str(r.exit_signal or "HOLD")
        )
        # Exit prices stubbed
        protect = "—" if r.protection_price is None else _fmt_score(r.protection_price, 2)
        brk = "—" if r.break_price is None else _fmt_score(r.break_price, 2)
        row = [
            stock,
            life,
            grade,
            _fmt_score(r.tech_score),
            _fmt_score(r.fund_score),
            _fmt_score(r.sector_score),
            _fmt_score(r.continuity_score),
            _fmt_score(r.consumption_score),
            risk,
            protect,
            brk,
            exit_sig,
        ]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class TrendConfirmationBot:
    """
    Phase 7+8 orchestrator — V3 engines + ExitEngine → TrendBotResult.

    ``evaluate_one`` / ``evaluate_many`` only. No live cutover. No Backtest.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        market_engine: Optional[MarketEngine] = None,
        sector_engine: Optional[SectorEngine] = None,
        technical_engine: Optional[TechnicalEngine] = None,
        fundamental_engine: Optional[FundamentalEngine] = None,
        risk_engine: Optional[RiskEngine] = None,
        continuity_engine: Optional[ContinuityEngine] = None,
        lifecycle_engine: Optional[LifecycleEngine] = None,
        exit_engine: Optional[ExitEngine] = None,
        exit_state: Optional[ExitStateStore] = None,
    ) -> None:
        self.config = config if config is not None else get_config()
        self.market = market_engine or MarketEngine(config=self.config)
        self.sector = sector_engine or SectorEngine(config=self.config)
        self.technical = technical_engine or TechnicalEngine(config=self.config)
        self.fundamental = fundamental_engine or FundamentalEngine(config=self.config)
        self.risk = risk_engine or RiskEngine(config=self.config)
        self.continuity = continuity_engine or ContinuityEngine(config=self.config)
        self.lifecycle = lifecycle_engine or LifecycleEngine(config=self.config)
        self.exit = exit_engine or ExitEngine(config=self.config)
        self.exit_state = exit_state  # None = no auto-persist; caller may pass store

    # ------------------------------------------------------------------
    # Market helper
    # ------------------------------------------------------------------

    def evaluate_market(
        self,
        t_date: str,
        spot_row: Optional[Any] = None,
        *,
        write_archive: bool = True,
        allow_stale_meta: bool = True,
        force_compute: bool = False,
    ) -> MarketEngineResult:
        """
        Shared market evaluation.

        ``spot_row`` may be a DataFrame (full spot) or None.
        ``allow_stale_meta`` defaults True so historical as-of dates can reuse
        ``market_meta.json`` offline without forcing a live recompute.
        """
        spot_df = spot_row if isinstance(spot_row, pd.DataFrame) else None
        return self.market.evaluate(
            str(t_date),
            spot=spot_df,
            write_archive=write_archive,
            allow_stale_meta=allow_stale_meta,
            force_compute=force_compute,
        )

    # ------------------------------------------------------------------
    # evaluate_one
    # ------------------------------------------------------------------

    def evaluate_one(
        self,
        code6: str,
        name: Optional[str] = None,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
        spot_row: Optional[Any] = None,
        market_result: Optional[MarketEngineResult] = None,
        pe: Optional[float] = None,
        industry_pe: Optional[float] = None,
        *,
        prior_state: Optional[Dict[str, Any]] = None,
        persist_continuity: bool = False,
        update_exit_state: bool = False,
        write_archive: bool = False,
        allow_stale_meta: bool = True,
    ) -> TrendBotResult:
        """
        Run full V3 pipeline for one code as-of ``t_date``.

        Loads hist from ``{code6}.parquet under ASHARE_KLINE_ROOT/raw/daily/{code6}.parquet``
        when ``hist`` is not passed; truncates to date <= t_date.
        """
        code = _norm_code(code6)
        asof = str(t_date or "")
        notes: List[str] = []
        risks: List[str] = []
        reasons: List[str] = []

        # --- hist ---
        own_hist = hist
        if own_hist is None:
            own_hist = _load_hist(code, asof or None)
            if own_hist is None:
                notes.append("hist_missing")
        else:
            own_hist = _truncate_hist(own_hist, asof or None)

        display_name = _resolve_name(code, name, spot_row)

        # Empty / missing hist → early FILTER
        if own_hist is None or not isinstance(own_hist, pd.DataFrame) or len(own_hist) == 0:
            return TrendBotResult(
                t_date=asof,
                code=code,
                name=display_name,
                grade=Grade.FILTER,
                data_status=DataStatus.DATA_INSUFFICIENT,
                exit_signal=ExitSignal.HOLD,
                warning_price=None,
                protection_price=None,
                break_price=None,
                reasons=["hist_missing_or_empty"],
                notes=notes + ["DATA_INSUFFICIENT: no hist"],
                risks=risks,
            )

        # --- 1. Market (optional shared) ---
        mkt: MarketEngineResult
        if market_result is not None:
            mkt = market_result
        else:
            mkt = self.evaluate_market(
                asof or "1970-01-01",
                spot_row=spot_row if isinstance(spot_row, pd.DataFrame) else None,
                write_archive=write_archive,
                allow_stale_meta=allow_stale_meta,
            )
        regime_v3 = mkt.regime_v3 if isinstance(mkt.regime_v3, MarketRegime) else MarketRegime.NORMAL
        legacy_regime = mkt.legacy_regime or "NORMAL"
        risk_off = bool(mkt.risk_off)
        if not mkt.ok:
            notes.append(f"market:{_status_value(mkt.data_status)}")

        # --- 2. Sector ---
        try:
            sec: SectorEngineResult = self.sector.evaluate(code, hist=own_hist, t_date=asof or None)
        except Exception as exc:  # noqa: BLE001
            sec = SectorEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code,
                t_date=asof,
                error=f"{type(exc).__name__}: {exc}",
                tags=["SECTOR_EVAL_ERR"],
            )
        if sec.data_status != DataStatus.VALID:
            notes.append(f"sector:{_status_value(sec.data_status)}")

        # --- 3. Technical ---
        try:
            tech: TechnicalEngineResult = self.technical.evaluate(own_hist, t_date=asof or None)
        except Exception as exc:  # noqa: BLE001
            tech = TechnicalEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                technical_score=0.0,
                lifecycle=Lifecycle.NONE,
                phase="NONE",
                error=f"{type(exc).__name__}: {exc}",
                t_date=asof,
            )
        if tech.data_status != DataStatus.VALID:
            notes.append(f"tech:{_status_value(tech.data_status)}")
        if tech.risks:
            risks.extend(list(tech.risks))

        # --- 4. Fundamental ---
        try:
            fund: FundamentalEngineResult = self.fundamental.evaluate(
                code, pe=pe, industry_pe=industry_pe
            )
        except Exception as exc:  # noqa: BLE001
            fund = FundamentalEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code,
                fund_score=None,
                level="DATA_INSUFFICIENT",
                error=f"{type(exc).__name__}: {exc}",
                tags=["FUND_EVAL_ERR", "DATA_INSUFFICIENT"],
            )
        # fund insufficient is optional dim — note only
        if fund.data_status != DataStatus.VALID:
            notes.append(f"fund:{_status_value(fund.data_status)}")

        if not display_name and fund.industry:
            # keep name empty; industry goes to snapshot
            pass

        # --- 5. Risk ---
        try:
            risk_res: RiskEngineResult = self.risk.evaluate(
                hist=own_hist,
                platform_low=tech.platform_low,
                structure_risks=list(tech.risks or []),
                t_date=asof or None,
                code=code,
            )
        except Exception as exc:  # noqa: BLE001
            risk_res = RiskEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code,
                t_date=asof,
                risk_level=RiskLevel.WATCH,
                error=f"{type(exc).__name__}: {exc}",
                tags=["RISK_EVAL_ERR"],
            )
        if risk_res.data_status != DataStatus.VALID:
            notes.append(f"risk:{_status_value(risk_res.data_status)}")
        if risk_res.tags:
            risks.extend([t for t in risk_res.tags if t not in risks])

        # --- 6. Continuity (wire fund/sector context) ---
        fund_d = _fund_dict_for_continuity(fund)
        sec_d = _sector_dict_for_continuity(sec)
        try:
            cont: ContinuityEngineResult = self.continuity.evaluate(
                code,
                hist=own_hist,
                t_date=asof or None,
                fund=fund_d,
                sector=sec_d,
                phase=tech.phase,
                persist=persist_continuity,
            )
        except Exception as exc:  # noqa: BLE001
            cont = ContinuityEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code,
                t_date=asof,
                continuity_type="TYPE_D",
                error=f"{type(exc).__name__}: {exc}",
                tags=["CONT_EVAL_ERR"],
            )
        if cont.data_status != DataStatus.VALID:
            notes.append(f"cont:{_status_value(cont.data_status)}")

        # --- 7. Lifecycle ---
        try:
            life: LifecycleEngineResult = self.lifecycle.evaluate(technical_result=tech)
        except Exception as exc:  # noqa: BLE001
            life = LifecycleEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                lifecycle=Lifecycle.map_from_v2_phase(tech.phase),
                phase=tech.phase or "NONE",
                error=f"{type(exc).__name__}: {exc}",
            )

        # --- data_status escalation (critical = tech + continuity) ---
        critical_statuses: List[Union[DataStatus, str]] = [tech.data_status, cont.data_status]
        # Missing tech/cont scores → insufficient
        if tech.data_status == DataStatus.ERROR or cont.data_status == DataStatus.ERROR:
            data_status = DataStatus.ERROR
        elif (
            tech.data_status == DataStatus.DATA_INSUFFICIENT
            or cont.data_status == DataStatus.DATA_INSUFFICIENT
            or cont.continuity_score is None
            or (not tech.ok and tech.technical_score <= 0)
        ):
            data_status = DataStatus.DATA_INSUFFICIENT
        else:
            data_status = DataStatus.VALID
        # Market ERROR alone does not kill candidate scoring
        _ = critical_statuses  # documented above

        lifecycle = (
            life.lifecycle
            if isinstance(life.lifecycle, Lifecycle)
            else Lifecycle.map_from_v2_phase(tech.phase)
        )
        risk_level = (
            risk_res.risk_level
            if isinstance(risk_res.risk_level, RiskLevel)
            else RiskLevel.WATCH
        )
        broken = bool(risk_res.broken)
        overheated = bool(tech.overheated) or lifecycle == Lifecycle.OVERHEATED

        tech_score = float(tech.technical_score) if tech.technical_score is not None else None
        cont_score = cont.continuity_score
        fund_score = fund.fund_score  # None when insufficient (never fake 0)
        sector_score = sec.sector_score
        consumption = cont.consumption_score

        # --- 8. Grade ---
        gr: GradeResult = grade_candidate(
            tech_score=tech_score,
            continuity_score=cont_score,
            fund_score=fund_score,
            sector_score=sector_score,
            risk_level=risk_level,
            lifecycle=lifecycle,
            regime_v3=regime_v3,
            consumption_score=consumption,
            overheated=overheated,
            broken=broken,
            data_status=data_status,
            config=self.config,
        )
        reasons.extend(list(gr.reasons or []))
        if gr.failed_checks:
            notes.append("failed:" + ",".join(gr.failed_checks[:6]))

        # Snapshot (best-effort)
        snapshot: Optional[StockSnapshot] = None
        try:
            snapshot = build_stock_snapshot_from_hist(
                own_hist, code, name=display_name, t_date=asof or None, tech=tech
            )
            snapshot.fund_score = fund_score
            snapshot.fundamental = dict(fund.fundamental or {}) or None
            snapshot.industry = sec.industry or fund.industry
            snapshot.sector = SectorResult(
                name=sec.industry,
                sector_score=sector_score,
                daily_trend=sec.sector_trend,
                daily_trend_score=sec.daily_trend_score,
                monthly_trend=sec.monthly_trend,
                monthly_trend_score=sec.monthly_trend_score,
                breadth_score=sec.breadth_score,
                relative_strength_20=sec.relative_strength,
                sector_continuity=sec.sector_continuity,
            )
            snapshot.continuity = ContinuityFields(
                continuity_score=cont_score,
                continuity_type=cont.continuity_type,
                continuity_type_cn=cont.continuity_type_cn,
                continuity_delta1=cont.delta1,
                continuity_delta3=cont.delta3,
                continuity_delta5=cont.delta5,
                continuity_delta_label=cont.delta_label,
                consumption_score=consumption,
            )
            snapshot.risk_level = risk_level
            snapshot.risk_tags = list(risk_res.tags or [])
            snapshot.broken = broken
            snapshot.lifecycle = lifecycle
            snapshot.data_status = data_status
        except Exception as exc:  # noqa: BLE001
            notes.append(f"snapshot_err:{type(exc).__name__}")

        close = tech.close
        if close is None and len(own_hist) > 0 and "close" in own_hist.columns:
            try:
                close = float(own_hist["close"].iloc[-1])
            except Exception:  # noqa: BLE001
                close = None

        # --- 9. ExitEngine (Phase 8) ---
        prior = dict(prior_state or {})
        if not prior and self.exit_state is not None:
            prior = self.exit_state.get(code)
        try:
            exit_res: ExitEngineResult = self.exit.evaluate(
                own_hist,
                t_date=asof or None,
                code=code,
                prior_state=prior or None,
                technical_result=tech,
                continuity_score=cont_score,
                continuity_delta1=cont.delta1,
                continuity_delta_label=cont.delta_label,
                consumption_score=consumption,
                risk_level=risk_level,
                risk_broken=broken,
            )
        except Exception as exc:  # noqa: BLE001
            exit_res = ExitEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code,
                t_date=asof,
                exit_signal=ExitSignal.HOLD,
                error=f"{type(exc).__name__}: {exc}",
                notes=[f"exit_err:{type(exc).__name__}"],
            )
        if exit_res.notes:
            notes.extend([n for n in exit_res.notes if n not in notes][:8])
        if exit_res.tags:
            risks.extend([t for t in exit_res.tags if t not in risks])

        warning_price = exit_res.warning_price
        protection_price = exit_res.protection_price
        break_price = exit_res.break_trend_price
        exit_signal = (
            exit_res.exit_signal
            if isinstance(exit_res.exit_signal, ExitSignal)
            else ExitSignal.HOLD
        )
        if close is None:
            close = exit_res.close

        result = TrendBotResult(
            t_date=asof,
            code=code,
            name=display_name,
            grade=gr.grade if isinstance(gr.grade, Grade) else Grade(str(gr.grade)),
            data_status=data_status,
            tech_score=tech_score,
            continuity_score=cont_score,
            fund_score=fund_score,
            sector_score=sector_score,
            consumption_score=consumption,
            lifecycle=lifecycle,
            risk_level=risk_level,
            regime_v3=regime_v3,
            legacy_regime=legacy_regime,
            risk_off=risk_off,
            exit_signal=exit_signal,
            close=close,
            warning_price=warning_price,
            protection_price=protection_price,
            break_price=break_price,
            reasons=reasons,
            risks=risks,
            notes=notes,
            snapshot=snapshot,
        )

        if update_exit_state:
            store = self.exit_state if self.exit_state is not None else default_store()
            # merge peak/streak from ExitEngineResult
            store.update_state(code, exit_res)

        return result

    # ------------------------------------------------------------------
    # evaluate_many
    # ------------------------------------------------------------------

    def evaluate_many(
        self,
        codes: Sequence[str],
        t_date: str,
        *,
        names: Optional[Dict[str, str]] = None,
        hist_map: Optional[Dict[str, pd.DataFrame]] = None,
        spot_row: Optional[Any] = None,
        market_result: Optional[MarketEngineResult] = None,
        prior_states: Optional[Dict[str, Dict[str, Any]]] = None,
        persist_continuity: bool = False,
        update_exit_state: bool = False,
        write_archive: bool = True,
        allow_stale_meta: bool = True,
    ) -> List[TrendBotResult]:
        """
        Thin batch helper: shared Market once, then ``evaluate_one`` per code.
        Returns results sorted by Plan grade order.
        """
        names = names or {}
        hist_map = hist_map or {}
        prior_states = prior_states or {}
        mkt = market_result
        if mkt is None:
            mkt = self.evaluate_market(
                t_date,
                spot_row=spot_row if isinstance(spot_row, pd.DataFrame) else None,
                write_archive=write_archive,
                allow_stale_meta=allow_stale_meta,
            )
        out: List[TrendBotResult] = []
        for raw in codes:
            code = _norm_code(raw)
            out.append(
                self.evaluate_one(
                    code,
                    name=names.get(code) or names.get(raw),
                    hist=hist_map.get(code) or hist_map.get(raw),
                    t_date=t_date,
                    spot_row=spot_row,
                    market_result=mkt,
                    prior_state=prior_states.get(code) or prior_states.get(raw),
                    persist_continuity=persist_continuity,
                    update_exit_state=update_exit_state,
                    write_archive=False,  # already wrote (or skipped) once
                    allow_stale_meta=allow_stale_meta,
                )
            )
        return sort_results(out)


# ---------------------------------------------------------------------------
# CLI / smoke
# ---------------------------------------------------------------------------


def _parse_codes(raw: str) -> List[str]:
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [_norm_code(p) for p in parts if p]


def _smoke_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TrendConfirmationBot V3 Phase 8 — evaluate codes → markdown table"
    )
    parser.add_argument(
        "--codes",
        default="002768,301108",
        help="Comma-separated code6 list (default: 002768,301108)",
    )
    parser.add_argument("--t-date", default="2026-09-21", help="As-of date YYYY-MM-DD")
    parser.add_argument(
        "--persist-continuity",
        action="store_true",
        default=False,
        help="Persist continuity history (default off)",
    )
    parser.add_argument(
        "--no-stale-meta",
        action="store_true",
        default=False,
        help="Disallow stale market_meta.json when t_date mismatches",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    codes = _parse_codes(args.codes)
    bot = TrendConfirmationBot()
    results = bot.evaluate_many(
        codes,
        t_date=args.t_date,
        persist_continuity=args.persist_continuity,
        write_archive=False,
        allow_stale_meta=not args.no_stale_meta,
    )
    table = results_to_markdown_table(results)
    print(f"# TrendConfirmationBot V3.0 Phase 8 — as-of {args.t_date}")
    print()
    if results:
        r0 = results[0]
        print(
            f"regime_v3={r0.regime_v3.value if isinstance(r0.regime_v3, MarketRegime) else r0.regime_v3} "
            f"legacy={r0.legacy_regime} risk_off={r0.risk_off}"
        )
        print()
    print(table)
    print()
    # grade distribution
    dist: Dict[str, int] = {}
    for r in results:
        g = _grade_value(r.grade)
        dist[g] = dist.get(g, 0) + 1
    print("grade_distribution:", dict(sorted(dist.items(), key=lambda kv: _GRADE_RANK.get(kv[0], 99))))
    # assert at least one graded row
    assert len(results) >= 1, "expected >=1 result"
    assert all(isinstance(r.grade, Grade) for r in results), "every row must have Grade"
    # Phase 8: exit prices filled when hist ok
    priced = [r for r in results if r.data_status != DataStatus.DATA_INSUFFICIENT]
    if priced:
        assert all(r.protection_price is not None for r in priced), "protection_price expected"
        assert all(r.break_price is not None for r in priced), "break_price expected"
        print("exit sample:", {
            r.code: {
                "warn": r.warning_price,
                "prot": r.protection_price,
                "brk": r.break_price,
                "sig": r.exit_signal.value if isinstance(r.exit_signal, ExitSignal) else r.exit_signal,
            }
            for r in priced
        })
    print("PASS: TrendConfirmationBot smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_cli())


__all__ = [
    "TrendConfirmationBot",
    "results_to_markdown_table",
    "sort_results",
]
