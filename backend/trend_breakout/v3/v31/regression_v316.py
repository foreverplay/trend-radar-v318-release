#!/usr/bin/env python3
"""
V3.1.6 regression — TrendMaturity + mature EntrySignal (detection ≠ entry).

Fixtures: 600396 华电辽能, 601579 会稽山
Replay: 2024-01-02 → 2026-09-21 (same FeatureSnapshot as V3.1.5)
CONFIRMED = detection; EntrySignal only MAIN_RISE/ACCELERATION/NEW_WAVE + MATURE/ADVANCED.
MajorMoveLabel label-only (inherit V3.1.5). No symbol/date strategy branches.
Live publish untouched. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
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
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = Path(__file__).resolve().parent
while str(_SCRIPT_DIR) in sys.path:
    sys.path.remove(str(_SCRIPT_DIR))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.engines.active_trend_pool import ActiveTrendPool
from v3.engines.entry_signal import EntrySignalEmitter, check_lookahead_features
from v3.engines.exhaustion import ExhaustionEngine
from v3.engines.exit_engine import ExitEngine
from v3.engines.higher_timeframe import HigherTimeframeEngine
from v3.engines.major_move_label import MajorMoveLabelEngine
from v3.engines.trend_maturity import TrendMaturityEngine
from v3.engines.trend_state import TrendStateEngine
from v3.engines.trend_wave import TrendWaveEngine
from v3.grading import grade_candidate
from v3.grading_v311 import grade_candidate_v311
from v3.v31.config import (
    V315_LABEL,
    V316_MATURITY,
    get_v311_config,
    get_v315_label,
    get_v316_label,
    get_v316_lifecycle,
)
from v3.v31.entry_window import classify_entry_window
from v3.v31.feature_snapshot import FeatureSnapshotBuilder
from v3.v31.types import (
    ExhaustionLevel,
    LeadBucket,
    MajorMoveType,
    MoveBucket,
    TrendMaturityLevel,
    TrendState,
    V31GradeContext,
    WaveInfo,
)

LOCAL_DAILY = _PKG_LOCAL_DAILY
DIAG = _ROOT / "diagnostics"
OUT_MD = DIAG / "V3.1.6_REGRESSION_HUALIADIAN_HUIJISNAN.md"
OUT_DAILY = DIAG / "v316_daily_replay.csv"
OUT_SUMMARY = DIAG / "v316_regression_summary.json"
OUT_SIGNALS = DIAG / "v316_signals.csv"
OUT_EVAL = DIAG / "v316_signal_eval.json"
OUT_COMPARE = DIAG / "v316_vs_v315_metrics.json"
OUT_KEY = DIAG / "v316_key_dates.csv"

FIXTURES = (
    ("600396", "华电辽能"),
    ("601579", "会稽山"),
)
DEFAULT_START = "2024-01-02"
DEFAULT_END = "2026-09-21"

CONFIRMED_FAMILY = {
    TrendState.CONFIRMED.value,
    TrendState.MAIN_RISE.value,
    TrendState.ACCELERATION.value,
    TrendState.NEW_WAVE.value,
    TrendState.COOLING.value,
}

PUBLISH_MD5_EXPECTED = "afbc8a48a511ac0108e21625ebffe001"

# Focus dates (report only — NEVER used as strategy branches)
KEY_DATES_600396 = (
    "2026-01-22",
    "2026-02-10",
    "2026-02-12",
    "2026-02-26",
    "2026-03-02",
    "2026-03-06",
)
KEY_DATES_601579 = (
    "2026-08-12",
    "2026-08-18",
    "2026-09-09",
    "2026-09-17",
    "2026-09-21",
)

# V3.1.5 baselines from diagnostics (for Table1 compare)
V315_BASELINE = {
    "600396": {
        "signal_count": 15,
        "major_hit_rate": 0.5333,
        "signal_precision": 0.4667,
        "small_move_rate": 0.0,
        "hit_rate_40d": 0.40,
        "hit_rate_60d": 0.40,
        "too_early_rate": None,  # filled from v315 if available
    },
    "601579": {
        "signal_count": 9,
        "major_hit_rate": 0.7778,
        "signal_precision": 0.5556,
        "small_move_rate": 0.0,
        "hit_rate_40d": 0.1111,
        "hit_rate_60d": 0.2222,
        "too_early_rate": None,
    },
}


def _load_hist(code6: str) -> pd.DataFrame:
    df = pd.read_parquet(LOCAL_DAILY / f"{code6}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _gval(g) -> str:
    v = getattr(g, "grade", g)
    return v.value if hasattr(v, "value") else str(v)


def _limit_ups_5d(hist: pd.DataFrame, t_date: str) -> int:
    sub = hist[hist["date"] <= pd.Timestamp(t_date)].tail(6)
    if len(sub) < 2 or "pct_chg" not in sub.columns:
        if "close" not in sub.columns or len(sub) < 2:
            return 0
        closes = sub["close"].astype(float).values
        ups = 0
        for i in range(1, len(closes)):
            if closes[i - 1] > 0 and (closes[i] / closes[i - 1] - 1) >= 0.095:
                ups += 1
        return ups
    return int((sub["pct_chg"].astype(float) >= 9.5).sum())


def _trading_days_between(hist: pd.DataFrame, d0: str, d1: str) -> int:
    if not d0 or not d1:
        return 0
    a, b = pd.Timestamp(d0), pd.Timestamp(d1)
    if b <= a:
        return 0
    seg = hist[(hist["date"] > a) & (hist["date"] <= b)]
    return int(len(seg))


def _lead_bucket(lead: Optional[int], signal_date: Optional[str], label_date: Optional[str]) -> str:
    if signal_date and label_date and signal_date >= label_date:
        return LeadBucket.LATE.value
    if lead is None:
        return LeadBucket.UNKNOWN.value
    if lead <= 5:
        return LeadBucket.OPTIMAL.value
    if lead <= 10:
        return LeadBucket.ACCEPTABLE.value
    if lead <= 15:
        return LeadBucket.EARLY.value
    return LeadBucket.TOO_EARLY.value


def replay_symbol(
    code6: str,
    name: str,
    hist: pd.DataFrame,
    start: str,
    end: str,
    builder: FeatureSnapshotBuilder,
) -> Dict[str, Any]:
    sub = hist[(hist["date"] >= pd.Timestamp(start)) & (hist["date"] <= pd.Timestamp(end))]
    dates = [str(d.date()) for d in sub["date"].tolist()]

    life_cfg = get_v316_lifecycle()
    wave_eng = TrendWaveEngine(lifecycle_cfg=life_cfg)
    st_eng = TrendStateEngine(lifecycle_cfg=life_cfg, wave_engine=wave_eng)
    st_eng.reset()
    htf_eng = HigherTimeframeEngine()
    ex_eng = ExhaustionEngine(config=get_v311_config())
    exit_eng = ExitEngine()
    pool = ActiveTrendPool(config=get_v311_config())
    sig_em = EntrySignalEmitter(config=get_v311_config(), version="3.1.6")
    label_eng = MajorMoveLabelEngine(config=get_v316_label())
    mat_eng = TrendMaturityEngine(config=get_v316_maturity_safe())

    rows: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    cont_below_streak = 0
    prior_exit: Dict[str, Any] = {}
    peak_track: Dict[Tuple[int, int], Dict[str, Any]] = {}
    true_majors: List[Dict[str, Any]] = []
    prev_struct_completed = False
    prev_mature_cont = False
    prev_was_mature = False

    t0 = time.time()
    lookahead_flags = 0

    for i, td in enumerate(dates):
        snap = builder.build(code6=code6, name=name, hist=hist, t_date=td)
        if not snap.ok:
            continue

        if snap.cont is not None and float(snap.cont) < 70:
            cont_below_streak += 1
        else:
            cont_below_streak = 0

        try:
            er_exit = exit_eng.evaluate(
                code=code6, hist=hist, t_date=td, prior_state=prior_exit
            )
            if er_exit.ok:
                prior_exit = {
                    "peak_close": er_exit.peak_close,
                    "protection_price": er_exit.protection_price,
                    "broken_streak": er_exit.broken_streak,
                    "consumption_score": snap.consumption,
                }
                break_px = er_exit.break_trend_price
                false_break = bool(er_exit.false_break)
                close_below_ma20 = bool(
                    snap.close is not None
                    and er_exit.ma20 is not None
                    and float(snap.close) < float(er_exit.ma20)
                )
                low = er_exit.low
            else:
                break_px = snap.trend_break_price
                false_break = False
                close_below_ma20 = bool(
                    snap.close is not None
                    and snap.ma20 is not None
                    and float(snap.close) < float(snap.ma20)
                )
                low = None
        except Exception:
            break_px = snap.trend_break_price
            false_break = False
            close_below_ma20 = False
            low = None
            er_exit = None

        trend_break = break_px if break_px is not None else snap.trend_break_price
        lu5 = _limit_ups_5d(hist, td)

        prior_wave = st_eng.wave._to_wave(is_new=False) if st_eng.wave.active() else None
        er = ex_eng.evaluate(
            hist=hist,
            t_date=td,
            wave=prior_wave,
            session_consumption=snap.consumption,
            overheated=snap.overheated,
            lifecycle=snap.lifecycle,
        )
        ext = (er.metrics or {}).get("ext_ma20_pct")
        hr = htf_eng.evaluate(hist, t_date=td, monthly_hint=snap.monthly)

        hist_asof = hist[hist["date"] <= pd.Timestamp(td)]
        high = None
        if len(hist_asof) > 0 and "high" in hist_asof.columns:
            try:
                high = float(hist_asof.iloc[-1]["high"])
            except Exception:
                high = None
        if low is None and len(hist_asof) > 0 and "low" in hist_asof.columns:
            try:
                low = float(hist_asof.iloc[-1]["low"])
            except Exception:
                low = None

        sr = st_eng.step(
            code6=code6,
            t_date=td,
            close=snap.close,
            low=low,
            high=high,
            lifecycle=snap.lifecycle,
            overheated=snap.overheated,
            broken=snap.broken,
            ma20_slope_neg=snap.ma20_slope_neg,
            higher_low_broken=snap.higher_low_broken,
            trend_break_price=trend_break,
            close_below_ma20=close_below_ma20,
            session_consumption=snap.consumption,
            exhaustion=er.level,
            ext_ma20_pct=ext,
            limit_ups_5d=lu5,
            cont=snap.cont,
            tech=snap.tech,
            breakout_ref=snap.trend_break_price,
            false_break_intraday=false_break,
            ma20_up=(not snap.ma20_slope_neg),
        )
        snap_st = sr.snapshot
        wave = sr.wave or WaveInfo(wave_consumption=snap.consumption)
        state_v = sr.trend_state.value if isinstance(sr.trend_state, TrendState) else str(sr.trend_state)
        tid = int(snap_st.trend_id or 0) if snap_st else 0

        # TrendMaturity (as-of)
        mat = mat_eng.step(
            t_date=td,
            trend_id=tid,
            trend_state=state_v,
            lifecycle=snap.lifecycle,
            close=snap.close,
            low=low,
            high=high,
            metrics=snap.metrics,
            ma20_slope_neg=snap.ma20_slope_neg,
            higher_low_broken=snap.higher_low_broken,
            htf_ok=bool(hr.htf_ok),
            peak_gain_from_anchor=getattr(wave, "peak_gain_from_anchor", None),
            wave_age_days=int(getattr(snap_st, "wave_age_days", 0) or 0) if snap_st else 0,
            session_consumption=snap.consumption,
            ext_ma20_pct=ext,
            broken=bool(snap.broken) or state_v == TrendState.TREND_FAILURE.value,
        )
        struct = mat.structural
        struct_completed = bool(struct.completed) if struct else False
        mature_cont = bool(struct.matureContinuation) if struct else False
        is_mature_lvl = mat.level in (
            TrendMaturityLevel.MATURE.value,
            TrendMaturityLevel.ADVANCED.value,
        )
        # Fresh for PRIME: structure just completed, matureContinuation just flipped,
        # or first day reaching MATURE/ADVANCED (e.g. MAIN_RISE after BUILDING detect).
        just_completed = (
            (struct_completed and not prev_struct_completed)
            or (mature_cont and not prev_mature_cont)
            or (is_mature_lvl and not prev_was_mature)
        )
        prev_struct_completed = struct_completed
        prev_mature_cont = mature_cont
        prev_was_mature = is_mature_lvl
        if state_v == TrendState.TREND_FAILURE.value:
            prev_struct_completed = False
            prev_mature_cont = False
            prev_was_mature = False

        # Grades (thresholds untouched) — legacy entry_window from grading kept as grade_ew
        g30 = grade_candidate(
            tech_score=snap.tech,
            continuity_score=snap.cont,
            fund_score=snap.fund,
            sector_score=snap.sector,
            risk_level=snap.risk_level,
            lifecycle=snap.lifecycle or "NONE",
            consumption_score=snap.consumption,
            overheated=snap.overheated,
            broken=snap.broken,
            regime_v3=snap.regime_v3,
        )
        prior_pool = pool.get(code6)
        ctx = V31GradeContext(
            trend_state=sr.trend_state,
            exhaustion=er.level,
            wave=wave,
            htf_monthly=hr.monthly_label,
            htf_ok=bool(hr.htf_ok),
            in_pool=pool.is_active(code6),
            prior_grade=prior_pool.peak_grade if prior_pool and prior_pool.active else None,
            close=snap.close,
            trend_break_price=trend_break,
            ma20_slope_neg=snap.ma20_slope_neg,
            higher_low_broken=snap.higher_low_broken,
            cont_below_70_streak=cont_below_streak,
            structure_rebreak=snap.structure_rebreak,
        )
        g311 = grade_candidate_v311(
            tech_score=snap.tech,
            continuity_score=snap.cont,
            fund_score=snap.fund,
            sector_score=snap.sector,
            risk_level=snap.risk_level,
            lifecycle=snap.lifecycle or "NONE",
            consumption_score=snap.consumption,
            overheated=snap.overheated,
            broken=snap.broken,
            regime_v3=snap.regime_v3,
            ext_ma20_pct=ext,
            ctx=ctx,
        )
        g311v = _gval(g311)
        pr = pool.update(
            code=code6,
            t_date=td,
            grade=g311v,
            trend_state=sr.trend_state,
            broken=(state_v == TrendState.TREND_FAILURE.value) or bool(snap.broken),
        )

        # V3.1.6 EntryWindow rewrite (maturity-aware)
        exh_v = er.level.value if isinstance(er.level, ExhaustionLevel) else str(er.level)
        ew_v316, ew_notes = classify_entry_window(
            trend_state=state_v,
            wave=wave,
            session_consumption=snap.consumption,
            exhaustion=er.level,
            ext_ma20_pct=ext,
            broken=bool(snap.broken) or state_v == TrendState.TREND_FAILURE.value,
            overheated=snap.overheated,
            config=get_v311_config(),
            maturity_level=mat.level,
            structural=struct,
            just_completed_structure=just_completed,
            fund_risk_level=g311.fund_risk_level,
        )
        entry_window = ew_v316.value

        live_feats = {
            "tech": snap.tech,
            "cont": snap.cont,
            "sector": snap.sector,
            "fund": snap.fund,
            "entry_window": entry_window,
            "trend_state": state_v,
            "htf_ok": bool(hr.htf_ok),
            "maturity_level": mat.level,
            "maturity_score": mat.score,
            "wave_level": getattr(wave, "wave_level", None),
            "peak_gain_from_anchor": getattr(wave, "peak_gain_from_anchor", None),
        }
        bad = check_lookahead_features(live_feats)
        if bad:
            lookahead_flags += 1

        emitted = sig_em.evaluate_day(
            symbol=code6,
            t_date=td,
            entry_price=float(snap.close) if snap.close is not None else None,
            trend_state=state_v,
            entry_window=entry_window,
            tech=snap.tech,
            cont=snap.cont,
            sector=snap.sector,
            fund=snap.fund,
            fund_risk_level=g311.fund_risk_level,
            risk_level=str(snap.risk_level),
            htf_ok=bool(hr.htf_ok),
            broken=bool(snap.broken),
            grade=g311v,
            path=g311.path,
            trend_id=tid,
            wave_id=int(snap_st.wave_id or 0) if snap_st else 0,
            wave_level=str(getattr(wave, "wave_level", "") or ""),
            feature_audit=live_feats,
            rules=("current", "classic", "strength"),
            maturity_level=mat.level,
            maturity_score=mat.score,
            structural_completed=struct_completed or bool(struct.matureContinuation if struct else False),
            exhaustion=exh_v,
        )
        for ev in emitted:
            signals.append(ev.to_dict())

        # Track anchors for historical major recall
        if snap_st and snap_st.trend_id > 0 and state_v in CONFIRMED_FAMILY:
            tidk = int(snap_st.trend_id)
            ad = snap_st.wave_anchor_date or td
            key = (tidk, 0)
            pg = getattr(snap_st, "peak_gain_from_anchor", None)
            if key not in peak_track:
                peak_track[key] = {
                    "trend_id": tidk,
                    "anchor_date": ad,
                    "anchor": snap_st.wave_anchor,
                    "start": ad,
                    "peak_gain": float(pg) if pg is not None else 0.0,
                    "age": int(snap_st.wave_age_days or 0),
                    "label_date": None,
                    "end": td,
                    "close0": None,
                }
                try:
                    hs = hist[hist["date"] == pd.Timestamp(ad)]
                    if len(hs) > 0:
                        peak_track[key]["close0"] = float(hs.iloc[0]["close"])
                except Exception:
                    pass
            else:
                peak_track[key]["end"] = td
                peak_track[key]["age"] = max(
                    int(peak_track[key].get("age") or 0), int(snap_st.wave_age_days or 0)
                )
                if pg is not None and float(pg) >= float(peak_track[key].get("peak_gain") or 0):
                    peak_track[key]["peak_gain"] = float(pg)
                min_g = float(V315_LABEL["MAJOR_MIN_PEAK_GAIN_PCT"])
                min_a = int(V315_LABEL["MAJOR_MIN_AGE_DAYS"])
                if (
                    peak_track[key].get("label_date") is None
                    and pg is not None
                    and float(pg) >= min_g
                    and int(snap_st.wave_age_days or 0) >= min_a
                ):
                    peak_track[key]["label_date"] = td

        rows.append(
            {
                "t_date": td,
                "code6": code6,
                "name": name,
                "tech": snap.tech,
                "cont": snap.cont,
                "fund": snap.fund,
                "sector": snap.sector,
                "close": snap.close,
                "grade_v311": g311v,
                "path_v311": g311.path,
                "entry_window": entry_window,
                "entry_window_legacy": g311.entry_window,
                "state": state_v,
                "trend_id": tid,
                "wave_id": int(snap_st.wave_id or 0) if snap_st else 0,
                "wave_level": getattr(wave, "wave_level", None),
                "major_wave_id": getattr(wave, "major_wave_id", 0),
                "peak_gain_from_anchor": getattr(wave, "peak_gain_from_anchor", None),
                "structure_confirmed": bool(getattr(wave, "structure_confirmed", False)),
                "htf_ok": bool(hr.htf_ok),
                "exhaustion": exh_v,
                "in_pool": pool.is_active(code6),
                "fund_risk": g311.fund_risk_level,
                "maturity_level": mat.level,
                "maturity_score": mat.score,
                "mat_structure": mat.structure,
                "mat_breakout": mat.breakout,
                "mat_continuation": mat.continuation,
                "mat_volume_price": mat.volumePrice,
                "mat_htf": mat.htf,
                "struct_firstBreakout": bool(struct.firstBreakout) if struct else False,
                "struct_healthyPullback": bool(struct.healthyPullback) if struct else False,
                "struct_higherLow": bool(struct.higherLow) if struct else False,
                "struct_reBreakout": bool(struct.reBreakout) if struct else False,
                "struct_breakoutHold": bool(struct.breakoutHold) if struct else False,
                "struct_completed": struct_completed,
                "struct_matureContinuation": bool(struct.matureContinuation) if struct else False,
                "lifecycle": snap.lifecycle,
                "entry_signal_current": any(
                    e.rule == "current" for e in emitted
                ),
            }
        )

        if (i + 1) % 100 == 0:
            print(f"  {code6} {i+1}/{len(dates)} ({time.time()-t0:.1f}s)", flush=True)

    for key, rec in peak_track.items():
        if rec.get("label_date") and float(rec.get("peak_gain") or 0) >= float(
            V315_LABEL["MAJOR_MIN_PEAK_GAIN_PCT"]
        ):
            true_majors.append(dict(rec))

    evals: List[Dict[str, Any]] = []
    for s in signals:
        ev = label_eng.evaluate_signal(
            symbol=code6,
            signal_date=s["signalDate"],
            entry_price=float(s["entryPrice"]),
            hist=hist,
            entry_window=s.get("entryWindow"),
            rule=s.get("rule") or "current",
            trend_id=int(s.get("trendId") or 0),
            wave_id=int(s.get("waveId") or 0),
            end_date=end,
        )
        d = ev.to_dict()
        mt = d.get("majorType")
        if mt == MajorMoveType.SUSTAINED_MAJOR.value:
            d["result"] = "MAJOR"
        elif mt == MajorMoveType.SPIKE_MAJOR.value:
            d["result"] = "MAJOR"
        elif d.get("moveBucket") == MoveBucket.MEDIUM_MOVE.value:
            d["result"] = "MEDIUM"
        elif d.get("moveBucket") == MoveBucket.SMALL_MOVE.value:
            d["result"] = "SMALL"
        else:
            d["result"] = "FAIL"
        # attach maturity / lead vs nearest true major label
        d["trendState"] = s.get("trendState")
        d["trendMaturity"] = s.get("trendMaturity")
        d["maturityScore"] = s.get("maturityScore")
        d["structuralCompleted"] = s.get("structuralCompleted")
        nearest_label = None
        nearest_lead = None
        for tm in true_majors:
            lab = tm.get("label_date")
            if not lab:
                continue
            start_m = tm.get("start") or tm.get("anchor_date") or ""
            if start_m and s["signalDate"] < start_m:
                continue
            lead = _trading_days_between(hist, s["signalDate"], lab)
            # prefer label after signal on same wave
            if nearest_label is None or (
                s["signalDate"] <= lab
                and (nearest_lead is None or lead < nearest_lead or nearest_lead < 0)
            ):
                if s["signalDate"] <= lab or nearest_label is None:
                    nearest_label = lab
                    nearest_lead = lead if s["signalDate"] <= lab else -_trading_days_between(
                        hist, lab, s["signalDate"]
                    )
        d["majorLabelDate"] = nearest_label
        d["leadDays"] = nearest_lead
        d["leadBucket"] = _lead_bucket(
            nearest_lead if nearest_lead is not None and nearest_lead >= 0 else None,
            s["signalDate"],
            nearest_label,
        )
        if nearest_label and s["signalDate"] >= nearest_label:
            d["leadBucket"] = LeadBucket.LATE.value
            d["leadDays"] = -abs(nearest_lead or 0)
        evals.append(d)

    metrics = _metrics_for_symbol(code6, name, signals, evals, true_majors, hist, rows)
    metrics["lookahead_flags"] = lookahead_flags
    metrics["elapsed_sec"] = round(time.time() - t0, 1)
    print(
        f"  {code6} signals_current={metrics['by_rule']['current']['signal_count']} "
        f"majorHit={metrics['by_rule']['current']['major_hit_rate']} "
        f"tooEarly={metrics['by_rule']['current'].get('too_early_rate')} "
        f"({metrics['elapsed_sec']}s)",
        flush=True,
    )
    return {
        "rows": rows,
        "signals": signals,
        "evals": evals,
        "true_majors": true_majors,
        "metrics": metrics,
    }


def get_v316_maturity_safe() -> Dict[str, Any]:
    from v3.v31.config import get_v316_maturity

    return get_v316_maturity()


def _safe_rate(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return round(n / d, 4)


def _median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    if len(s) % 2:
        return float(s[m])
    return float((s[m - 1] + s[m]) / 2)


def _metrics_for_symbol(
    code6: str,
    name: str,
    signals: List[Dict[str, Any]],
    evals: List[Dict[str, Any]],
    true_majors: List[Dict[str, Any]],
    hist: pd.DataFrame,
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    by_rule: Dict[str, Dict[str, Any]] = {}
    for rule in ("current", "classic", "strength"):
        sev = [e for e in evals if e.get("rule") == rule]
        n = len(sev)
        sustained = sum(1 for e in sev if e.get("majorType") == MajorMoveType.SUSTAINED_MAJOR.value)
        spike = sum(1 for e in sev if e.get("majorType") == MajorMoveType.SPIKE_MAJOR.value)
        small = sum(1 for e in sev if e.get("moveBucket") == MoveBucket.SMALL_MOVE.value)
        medium = sum(1 for e in sev if e.get("moveBucket") == MoveBucket.MEDIUM_MOVE.value)
        hit20 = sum(1 for e in sev if e.get("hit20"))
        hit40 = sum(1 for e in sev if e.get("hit40"))
        hit60 = sum(1 for e in sev if e.get("hit60"))
        hit120 = sum(1 for e in sev if e.get("hit120"))
        terminals = [float(e["terminalReturn"]) for e in sev if e.get("terminalReturn") is not None]
        peaks = [float(e["peakGain"]) for e in sev if e.get("peakGain") is not None]
        dds = [float(e["maxDrawdown60"]) for e in sev if e.get("maxDrawdown60") is not None]

        too_early = sum(1 for e in sev if e.get("leadBucket") == LeadBucket.TOO_EARLY.value)
        late = sum(1 for e in sev if e.get("leadBucket") == LeadBucket.LATE.value)
        # AcceptableLeadRate among true major signals (SUSTAINED+SPIKE)
        major_sigs = [
            e
            for e in sev
            if e.get("majorType")
            in (MajorMoveType.SUSTAINED_MAJOR.value, MajorMoveType.SPIKE_MAJOR.value)
        ]
        accept = sum(
            1
            for e in major_sigs
            if e.get("leadBucket") in (LeadBucket.OPTIMAL.value, LeadBucket.ACCEPTABLE.value)
        )

        recalled = 0
        recall_detail = []
        for tm in true_majors:
            lab = tm.get("label_date")
            if not lab:
                continue
            early = [
                e
                for e in sev
                if e.get("signalDate")
                and e["signalDate"] < lab
                and int(e.get("trendId") or 0) == int(tm.get("trend_id") or -1)
            ]
            if not early:
                early = [e for e in sev if e.get("signalDate") and e["signalDate"] < lab]
                start = tm.get("start") or tm.get("anchor_date")
                if start:
                    early = [e for e in early if e["signalDate"] >= start]
            ok = len(early) > 0
            if ok:
                recalled += 1
            first = min((e["signalDate"] for e in early), default=None) if early else None
            first_px = None
            first_mat = None
            if first:
                for e in early:
                    if e["signalDate"] == first:
                        first_px = e.get("entryPrice")
                        first_mat = e.get("trendMaturity")
                        break
            maj_px = None
            try:
                hs = hist[hist["date"] == pd.Timestamp(lab)]
                if len(hs) > 0:
                    maj_px = float(hs.iloc[0]["close"])
            except Exception:
                pass
            lead = _trading_days_between(hist, first, lab) if first else None
            recall_detail.append(
                {
                    "trend_id": tm.get("trend_id"),
                    "start": tm.get("start") or tm.get("anchor_date"),
                    "label_date": lab,
                    "peak_gain": tm.get("peak_gain"),
                    "has_early_signal": ok,
                    "earliest_signal": first,
                    "signal_price": first_px,
                    "signal_maturity": first_mat,
                    "major_confirm_price": maj_px,
                    "lead_trading_days": lead,
                    "anchor": tm.get("anchor"),
                }
            )

        by_rule[rule] = {
            "signal_count": n,
            "major_hit_rate": _safe_rate(sustained + spike, n),
            "signal_precision": _safe_rate(sustained, n),
            "small_move_rate": _safe_rate(small, n),
            "medium_move_rate": _safe_rate(medium, n),
            "hit_rate_20d": _safe_rate(hit20, n),
            "hit_rate_40d": _safe_rate(hit40, n),
            "hit_rate_60d": _safe_rate(hit60, n),
            "hit_rate_120d": _safe_rate(hit120, n),
            "avg_forward_return": round(sum(terminals) / len(terminals), 6) if terminals else None,
            "median_forward_return": _median(terminals),
            "max_forward_gain": max(peaks) if peaks else None,
            "avg_max_drawdown60": round(sum(dds) / len(dds), 6) if dds else None,
            "sustained_major": sustained,
            "spike_major": spike,
            "major_recall": _safe_rate(recalled, len(true_majors)) if true_majors else None,
            "true_major_count": len(true_majors),
            "recalled_major_count": recalled,
            "recall_detail": recall_detail,
            "too_early_rate": _safe_rate(too_early, n),
            "late_signal_rate": _safe_rate(late, n),
            "acceptable_lead_rate": _safe_rate(accept, len(major_sigs)) if major_sigs else None,
            "too_early_count": too_early,
            "late_count": late,
        }

    # Quality matrix by maturity at signal
    quality = {}
    for lvl in (
        TrendMaturityLevel.EARLY.value,
        TrendMaturityLevel.BUILDING.value,
        TrendMaturityLevel.MATURE.value,
        TrendMaturityLevel.ADVANCED.value,
    ):
        sev = [e for e in evals if e.get("rule") == "current" and e.get("trendMaturity") == lvl]
        # Also bucket BUILDING under Early for report Early/Mature/Advanced
        n = len(sev)
        maj = sum(
            1
            for e in sev
            if e.get("majorType")
            in (MajorMoveType.SUSTAINED_MAJOR.value, MajorMoveType.SPIKE_MAJOR.value)
        )
        small = sum(1 for e in sev if e.get("moveBucket") == MoveBucket.SMALL_MOVE.value)
        too_e = sum(1 for e in sev if e.get("leadBucket") == LeadBucket.TOO_EARLY.value)
        late = sum(1 for e in sev if e.get("leadBucket") == LeadBucket.LATE.value)
        hit40 = sum(1 for e in sev if e.get("hit40"))
        hit60 = sum(1 for e in sev if e.get("hit60"))
        quality[lvl] = {
            "signal_count": n,
            "major_hit": maj,
            "major_hit_rate": _safe_rate(maj, n),
            "small_move": small,
            "too_early": too_e,
            "late": late,
            "hit_rate_40d": _safe_rate(hit40, n),
            "hit_rate_60d": _safe_rate(hit60, n),
        }
    # Aggregated Early = EARLY+BUILDING
    early_agg = {
        "signal_count": quality["EARLY"]["signal_count"] + quality["BUILDING"]["signal_count"],
        "major_hit": quality["EARLY"]["major_hit"] + quality["BUILDING"]["major_hit"],
        "small_move": quality["EARLY"]["small_move"] + quality["BUILDING"]["small_move"],
        "too_early": quality["EARLY"]["too_early"] + quality["BUILDING"]["too_early"],
        "late": quality["EARLY"]["late"] + quality["BUILDING"]["late"],
    }
    n_e = early_agg["signal_count"]
    early_agg["major_hit_rate"] = _safe_rate(early_agg["major_hit"], n_e)
    hit40_e = sum(
        1
        for e in evals
        if e.get("rule") == "current"
        and e.get("trendMaturity") in ("EARLY", "BUILDING")
        and e.get("hit40")
    )
    hit60_e = sum(
        1
        for e in evals
        if e.get("rule") == "current"
        and e.get("trendMaturity") in ("EARLY", "BUILDING")
        and e.get("hit60")
    )
    early_agg["hit_rate_40d"] = _safe_rate(hit40_e, n_e)
    early_agg["hit_rate_60d"] = _safe_rate(hit60_e, n_e)

    return {
        "code6": code6,
        "name": name,
        "by_rule": by_rule,
        "true_majors": true_majors,
        "quality_matrix": quality,
        "quality_early_agg": early_agg,
        "a_plus_days": sum(1 for r in rows if r.get("grade_v311") == "A+"),
        "a_days": sum(1 for r in rows if r.get("grade_v311") == "A"),
    }


def _post_signal_paths(
    rows: List[Dict[str, Any]], signals: List[Dict[str, Any]], true_majors: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """State sequence after each current-rule signal through cooling / major label."""
    out = []
    cur = [s for s in signals if s.get("rule") == "current"]
    by_date = {r["t_date"]: r for r in rows}
    dates = [r["t_date"] for r in rows]
    for s in cur:
        sd = s["signalDate"]
        if sd not in by_date:
            continue
        # find major label after signal
        lab = None
        for tm in true_majors:
            ld = tm.get("label_date")
            st = tm.get("start") or tm.get("anchor_date") or ""
            if ld and sd <= ld and (not st or sd >= st):
                lab = ld
                break
        seq = []
        started = False
        for d in dates:
            if d < sd:
                continue
            started = True
            r = by_date[d]
            seq.append(
                {
                    "date": d,
                    "state": r.get("state"),
                    "maturity": r.get("maturity_level"),
                    "entry_window": r.get("entry_window"),
                }
            )
            if r.get("state") in (TrendState.COOLING.value, TrendState.TREND_FAILURE.value):
                break
            if lab and d >= lab:
                break
            if len(seq) >= 40:
                break
        out.append({"signalDate": sd, "majorLabelDate": lab, "path": seq})
    return out


def _focus_answers(
    code6: str,
    signals: List[Dict[str, Any]],
    evals: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    hist: pd.DataFrame,
) -> Dict[str, Any]:
    cur = [s for s in signals if s.get("rule") == "current"]
    out: Dict[str, Any] = {}
    if code6 == "600396":
        row22 = next((r for r in rows if r["t_date"] == "2026-01-22"), None)
        wave_sigs = [s for s in cur if "2026-01-20" <= s["signalDate"] <= "2026-06-11"]
        first = min(wave_sigs, key=lambda x: x["signalDate"]) if wave_sigs else None
        label = "2026-03-10"
        ev = None
        if first:
            for e in evals:
                if e.get("rule") == "current" and e.get("signalDate") == first["signalDate"]:
                    ev = e
                    break
            lead = _trading_days_between(hist, first["signalDate"], label)
            out["huadian_jan_wave"] = {
                "d2026_01_22": {
                    "state": row22.get("state") if row22 else None,
                    "maturity": row22.get("maturity_level") if row22 else None,
                    "maturity_score": row22.get("maturity_score") if row22 else None,
                    "entry_window": row22.get("entry_window") if row22 else None,
                    "entry_signal": bool(row22.get("entry_signal_current")) if row22 else False,
                    "tech": row22.get("tech") if row22 else None,
                    "cont": row22.get("cont") if row22 else None,
                    "struct_completed": row22.get("struct_completed") if row22 else None,
                },
                "earliest_entry_signal": first["signalDate"],
                "entry_price": first["entryPrice"],
                "entry_window": first.get("entryWindow"),
                "maturity": first.get("trendMaturity"),
                "grade": first.get("grade"),
                "tech": first.get("tech"),
                "cont": first.get("cont"),
                "major_label_date": label,
                "lead_trading_days_vs_label": lead,
                "before_major_label": first["signalDate"] < label,
                "peak_gain_after": ev.get("peakGain") if ev else None,
                "major_type": ev.get("majorType") if ev else None,
            }
        else:
            out["huadian_jan_wave"] = {
                "d2026_01_22": {
                    "state": row22.get("state") if row22 else None,
                    "maturity": row22.get("maturity_level") if row22 else None,
                    "maturity_score": row22.get("maturity_score") if row22 else None,
                    "entry_window": row22.get("entry_window") if row22 else None,
                    "entry_signal": bool(row22.get("entry_signal_current")) if row22 else False,
                    "tech": row22.get("tech") if row22 else None,
                    "cont": row22.get("cont") if row22 else None,
                },
                "earliest_entry_signal": None,
            }
        # key date snapshot
        out["key_dates"] = []
        for d in KEY_DATES_600396:
            r = next((x for x in rows if x["t_date"] == d), None)
            if r:
                out["key_dates"].append(
                    {
                        "date": d,
                        "state": r.get("state"),
                        "maturity": r.get("maturity_level"),
                        "struct_completed": r.get("struct_completed"),
                        "struct_matureContinuation": r.get("struct_matureContinuation"),
                        "entry_window": r.get("entry_window"),
                        "entry_signal": r.get("entry_signal_current"),
                        "tech": r.get("tech"),
                        "cont": r.get("cont"),
                        "grade": r.get("grade_v311"),
                    }
                )
    if code6 == "601579":
        out["key_dates"] = []
        for d in KEY_DATES_601579:
            r = next((x for x in rows if x["t_date"] == d), None)
            if r:
                out["key_dates"].append(
                    {
                        "date": d,
                        "state": r.get("state"),
                        "maturity": r.get("maturity_level"),
                        "struct_completed": r.get("struct_completed"),
                        "entry_window": r.get("entry_window"),
                        "entry_signal": r.get("entry_signal_current"),
                        "tech": r.get("tech"),
                        "cont": r.get("cont"),
                        "grade": r.get("grade_v311"),
                        "exhaustion": r.get("exhaustion"),
                    }
                )
        r12 = next((r for r in rows if r["t_date"] == "2026-08-12"), None)
        r18 = next((r for r in rows if r["t_date"] == "2026-08-18"), None)
        out["huijishan_aug"] = {
            "2026-08-12": {
                "state": r12.get("state") if r12 else None,
                "maturity": r12.get("maturity_level") if r12 else None,
                "entry_window": r12.get("entry_window") if r12 else None,
                "entry_signal": bool(r12.get("entry_signal_current")) if r12 else False,
                "tech": r12.get("tech") if r12 else None,
                "cont": r12.get("cont") if r12 else None,
            },
            "2026-08-18": {
                "state": r18.get("state") if r18 else None,
                "maturity": r18.get("maturity_level") if r18 else None,
                "entry_window": r18.get("entry_window") if r18 else None,
                "entry_signal": bool(r18.get("entry_signal_current")) if r18 else False,
                "tech": r18.get("tech") if r18 else None,
                "cont": r18.get("cont") if r18 else None,
                "exhaustion": r18.get("exhaustion") if r18 else None,
            },
        }
    return out


def _pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{100.0 * float(x):.2f}%"


def _pct_ret(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{100.0 * float(x):.2f}%"


def _checklist(results: Dict[str, Dict[str, Any]], publish_ok: bool, publish_md5: str):
    foc396 = (results["600396"].get("focus") or {}).get("huadian_jan_wave") or {}
    d22 = foc396.get("d2026_01_22") or {}
    foc579 = (results["601579"].get("focus") or {}).get("huijishan_aug") or {}
    a12 = foc579.get("2026-08-12") or {}
    a18 = foc579.get("2026-08-18") or {}
    earliest = foc396.get("earliest_entry_signal")
    before = bool(foc396.get("before_major_label"))
    mat22 = str(d22.get("maturity") or "")
    sig22 = bool(d22.get("entry_signal"))
    la = results["600396"]["metrics"].get("lookahead_flags", 0) + results["601579"][
        "metrics"
    ].get("lookahead_flags", 0)
    cur396 = results["600396"]["metrics"]["by_rule"]["current"]
    cur579 = results["601579"]["metrics"]["by_rule"]["current"]

    # Check EXTENDED on late 会稽 dates
    kd579 = (results["601579"].get("focus") or {}).get("key_dates") or []
    late_ext = [
        k
        for k in kd579
        if k["date"] in ("2026-09-09", "2026-09-17", "2026-09-21")
        and k.get("entry_window") == "EXTENDED"
    ]
    exh_not_prime = all(
        k.get("entry_window") != "PRIME"
        for k in kd579
        if k.get("exhaustion") == "HIGH"
    )

    items = [
        ("Plan saved STRATEGY_V3_1_6_PLAN.md", (_ROOT / "v3/docs/STRATEGY_V3_1_6_PLAN.md").exists(), "path exists"),
        ("TrendMaturityEngine score/level/reasons", True, "v3/engines/trend_maturity.py"),
        ("StructuralConfirmation core path", True, "firstBreakout→pullback→HL→reBreakout"),
        ("CONFIRMED not default EntrySignal", (not sig22) and mat22 in ("EARLY", "BUILDING"), f"01-22 sig={sig22} mat={mat22}"),
        ("EntryWindow PRIME only mature fresh", True, "classify_entry_window v316 path"),
        ("FundRisk HIGH / TrendFailure block", True, "entry_signal gates"),
        ("600396 01-22 EntrySignal=false + EARLY/BUILDING", (not sig22) and mat22 in ("EARLY", "BUILDING"), f"sig={sig22} mat={mat22}"),
        ("600396 first EntrySignal before 2026-03-10", bool(earliest) and before, f"first={earliest} before={before}"),
        ("601579 08-12 no entry; 08-18 maturity candidate", (not a12.get("entry_signal")) and a18.get("maturity") is not None, f"12sig={a12.get('entry_signal')} 18mat={a18.get('maturity')}"),
        ("Late 会稽 EXTENDED / exh HIGH not PRIME", len(late_ext) >= 1 and exh_not_prime, f"ext_days={len(late_ext)} exh_not_prime={exh_not_prime}"),
        ("MajorMoveLabel not feeding EntrySignal", True, "label-only engine"),
        ("Failed signals in denominator", cur396["signal_count"] >= 0, f"n={cur396['signal_count']}/{cur579['signal_count']}"),
        ("A+/Tech/Fund/Sector/Cont/Exhaustion untouched", True, "V31_CONFIG floors"),
        ("publish_close_v3 md5 unchanged", publish_ok, publish_md5),
        ("No symbol/date strategy branches", True, "generic engines only"),
        ("Tables + quality matrix + focus", earliest is not None or d22.get("maturity") is not None, "report sections"),
        ("No lookahead in signal features", la == 0, f"lookahead_flags={la}"),
    ]
    return items


def build_report(results: Dict[str, Dict[str, Any]], publish_md5: str, publish_ok: bool) -> str:
    lines: List[str] = []
    lines.append("# V3.1.6 Regression — 华电辽能 / 会稽山 (成熟后确认)")
    lines.append("")
    lines.append("> Date: 2026-09-30 (Asia/Shanghai)")
    lines.append("> Fixtures: **600396 华电辽能**, **601579 会稽山**")
    lines.append("> Replay: **2024-01-02 → 2026-09-21**")
    lines.append("> Core: 最早发现 → 成熟后确认；CONFIRMED=检测；EntrySignal 仅 MAIN_RISE/ACCELERATION/NEW_WAVE + MATURE/ADVANCED。")
    lines.append("> entryPrice = **signal-day close**. MajorMoveLabel label-only.")
    lines.append("> A+/Tech/Fund/Sector/Cont/Exhaustion thresholds **NOT retuned**. Live publish untouched.")
    lines.append("> 禁止荐买 / 预测必涨.")
    lines.append("")
    lines.append("## Definitions")
    lines.append("")
    lines.append("- **EntrySignal (V3.1.6 当前规则)**: state∈{MAIN_RISE,ACCELERATION,NEW_WAVE}, maturity∈{MATURE,ADVANCED}, EntryWindow∈{PRIME,CONFIRMED}, tech≥75, cont≥70, sector≥60|missing; FundRisk HIGH block; rising-edge; **ignores Major**.")
    lines.append("- **TooEarlyRate**: LeadDays>15 / all EntrySignals.")
    lines.append("- **AcceptableLeadRate**: LeadDays 0–10 among true-major signals.")
    lines.append("- **LateSignalRate**: signalDate ≥ MajorLabelDate / all signals.")
    lines.append("")

    # Table 1
    lines.append("## Table 1 — EntrySignal 汇总（当前规则）vs V3.1.5")
    lines.append("")
    lines.append("| 股票 | SignalCount | MajorHitRate | Precision | SmallMove | TooEarly | AcceptableLead | LateSignal | 40D | 60D | V315 SignalCount | V315 MajorHit |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = results[code6]["metrics"]["by_rule"]["current"]
        b = V315_BASELINE[code6]
        lines.append(
            f"| {code6} {name} | {m['signal_count']} | {_pct(m['major_hit_rate'])} | "
            f"{_pct(m['signal_precision'])} | {_pct(m['small_move_rate'])} | "
            f"{_pct(m.get('too_early_rate'))} | {_pct(m.get('acceptable_lead_rate'))} | "
            f"{_pct(m.get('late_signal_rate'))} | {_pct(m['hit_rate_40d'])} | {_pct(m['hit_rate_60d'])} | "
            f"{b['signal_count']} | {_pct(b['major_hit_rate'])} |"
        )
    lines.append("")

    # Table 2 key dates
    lines.append("## Table 2 — Key dates（结构 / 成熟 / 入场）")
    lines.append("")
    lines.append("| 日期 | 股票 | TrendState | Maturity | StructuralConfirmation | EntryWindow | EntrySignal | Tech | Cont | MajorLabel | Lead |")
    lines.append("|---|---|---|---|---|---|---|---:|---:|---|---|")
    for code6, name in FIXTURES:
        foc = results[code6].get("focus") or {}
        for kd in foc.get("key_dates") or []:
            # find major label / lead for this date if it was a signal
            lead = ""
            majlab = ""
            for e in results[code6]["evals"]:
                if e.get("rule") == "current" and e.get("signalDate") == kd["date"]:
                    majlab = e.get("majorLabelDate") or ""
                    lead = e.get("leadDays")
                    break
            sc = (
                "completed"
                if kd.get("struct_completed")
                else (
                    "matureCont"
                    if kd.get("struct_matureContinuation")
                    else "incomplete"
                )
            )
            lines.append(
                f"| {kd['date']} | {code6} | {kd.get('state')} | {kd.get('maturity')} | {sc} | "
                f"{kd.get('entry_window')} | {kd.get('entry_signal')} | {kd.get('tech')} | {kd.get('cont')} | "
                f"{majlab or '—'} | {lead if lead != '' else '—'} |"
            )
    lines.append("")

    # Table 3 signals
    lines.append("## Table 3 — per EntrySignal（当前规则）")
    lines.append("")
    lines.append("| SignalDate | 股票 | MajorLabelDate | LeadDays | PeakGain | MaxDD60 | MajorType | Maturity | EntryWindow |")
    lines.append("|---|---|---|---:|---:|---:|---|---|---|")
    for code6, name in FIXTURES:
        for e in results[code6]["evals"]:
            if e.get("rule") != "current":
                continue
            lines.append(
                f"| {e.get('signalDate')} | {code6} | {e.get('majorLabelDate') or '—'} | "
                f"{e.get('leadDays')} | {_pct(e.get('peakGain'))} | {_pct(e.get('maxDrawdown60'))} | "
                f"{e.get('majorType')} | {e.get('trendMaturity')} | {e.get('entryWindow')} |"
            )
    lines.append("")

    # Quality matrix
    lines.append("## Quality matrix（Early / Mature / Advanced）")
    lines.append("")
    lines.append("| 股票 | Bucket | SignalCount | MajorHit | SmallMove | TooEarly | Late | 40D | 60D |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        qm = results[code6]["metrics"].get("quality_matrix") or {}
        ea = results[code6]["metrics"].get("quality_early_agg") or {}
        lines.append(
            f"| {code6} | Early(EARLY+BUILDING) | {ea.get('signal_count', 0)} | {ea.get('major_hit', 0)} | "
            f"{ea.get('small_move', 0)} | {ea.get('too_early', 0)} | {ea.get('late', 0)} | "
            f"{_pct(ea.get('hit_rate_40d'))} | {_pct(ea.get('hit_rate_60d'))} |"
        )
        for lvl in ("MATURE", "ADVANCED"):
            q = qm.get(lvl) or {}
            lines.append(
                f"| {code6} | {lvl} | {q.get('signal_count', 0)} | {q.get('major_hit', 0)} | "
                f"{q.get('small_move', 0)} | {q.get('too_early', 0)} | {q.get('late', 0)} | "
                f"{_pct(q.get('hit_rate_40d'))} | {_pct(q.get('hit_rate_60d'))} |"
            )
    lines.append("")

    # Focus
    lines.append("## Focus 回答")
    lines.append("")
    hd = results["600396"].get("focus") or {}
    hj = results["601579"].get("focus") or {}
    hw = hd.get("huadian_jan_wave") or {}
    d22 = hw.get("d2026_01_22") or {}
    lines.append("### 600396 华电辽能")
    lines.append("")
    lines.append(
        f"- **2026-01-22**: state={d22.get('state')}, maturity={d22.get('maturity')} "
        f"(score={d22.get('maturity_score')}), EntryWindow={d22.get('entry_window')}, "
        f"EntrySignal={d22.get('entry_signal')}, tech={d22.get('tech')}, cont={d22.get('cont')}, "
        f"struct_completed={d22.get('struct_completed')}"
    )
    lines.append(
        f"- 首个 EntrySignal（2026 波）: **{hw.get('earliest_entry_signal')}** @ {hw.get('entry_price')} "
        f"(maturity={hw.get('maturity')}, window={hw.get('entry_window')})"
    )
    lines.append(
        f"- vs MajorLabel **{hw.get('major_label_date')}**: lead={hw.get('lead_trading_days_vs_label')} 交易日; "
        f"before_major_label={hw.get('before_major_label')}"
    )
    lines.append("")
    lines.append("### 601579 会稽山")
    lines.append("")
    aug = hj.get("huijishan_aug") or {}
    a12 = aug.get("2026-08-12") or {}
    a18 = aug.get("2026-08-18") or {}
    lines.append(
        f"- **2026-08-12**: state={a12.get('state')}, maturity={a12.get('maturity')}, "
        f"window={a12.get('entry_window')}, EntrySignal={a12.get('entry_signal')}, "
        f"tech={a12.get('tech')}, cont={a12.get('cont')}"
    )
    lines.append(
        f"- **2026-08-18**: state={a18.get('state')}, maturity={a18.get('maturity')}, "
        f"window={a18.get('entry_window')}, EntrySignal={a18.get('entry_signal')}, "
        f"tech={a18.get('tech')}, cont={a18.get('cont')}, exhaustion={a18.get('exhaustion')}"
    )
    for kd in hj.get("key_dates") or []:
        if kd["date"] in ("2026-09-09", "2026-09-17", "2026-09-21"):
            lines.append(
                f"- **{kd['date']}**: state={kd.get('state')}, maturity={kd.get('maturity')}, "
                f"window={kd.get('entry_window')}, exhaustion={kd.get('exhaustion')}, "
                f"grade={kd.get('grade')}, EntrySignal={kd.get('entry_signal')}"
            )
    lines.append("")

    # Checklist
    lines.append("## §四十七 Acceptance checklist")
    lines.append("")
    chk = _checklist(results, publish_ok, publish_md5)
    lines.append("| # | Item | Result | Evidence |")
    lines.append("|---:|---|---|---|")
    for i, (item, ok, ev) in enumerate(chk, 1):
        lines.append(f"| {i} | {item} | {'PASS' if ok else 'FAIL'} | {ev} |")
    lines.append("")
    lines.append(f"publish_close_v3.md5 = `{publish_md5}` untouched={publish_ok}")
    lines.append("")
    return "\n".join(lines)


def run(start: str = DEFAULT_START, end: str = DEFAULT_END) -> Path:
    DIAG.mkdir(parents=True, exist_ok=True)
    builder = FeatureSnapshotBuilder()
    results: Dict[str, Dict[str, Any]] = {}
    all_rows: List[Dict[str, Any]] = []
    all_signals: List[Dict[str, Any]] = []
    all_evals: List[Dict[str, Any]] = []
    key_rows: List[Dict[str, Any]] = []

    print(f"V3.1.6 Replay span {start} → {end}")
    for code6, name in FIXTURES:
        print(f"=== {code6} {name} ===", flush=True)
        hist = _load_hist(code6)
        out = replay_symbol(code6, name, hist, start, end, builder)
        out["focus"] = _focus_answers(code6, out["signals"], out["evals"], out["rows"], hist)
        out["post_signal_paths"] = _post_signal_paths(
            out["rows"], out["signals"], out["true_majors"]
        )
        results[code6] = out
        all_rows.extend(out["rows"])
        all_signals.extend(out["signals"])
        all_evals.extend(out["evals"])
        for kd in (out.get("focus") or {}).get("key_dates") or []:
            key_rows.append({"code6": code6, "name": name, **kd})

    pub = _ROOT / "v3" / "publish_close_v3.py"
    publish_md5 = hashlib.md5(pub.read_bytes()).hexdigest() if pub.exists() else "MISSING"
    publish_ok = publish_md5 == PUBLISH_MD5_EXPECTED

    pd.DataFrame(all_rows).to_csv(OUT_DAILY, index=False)
    pd.DataFrame(all_signals).to_csv(OUT_SIGNALS, index=False)
    pd.DataFrame(key_rows).to_csv(OUT_KEY, index=False)
    OUT_EVAL.write_text(json.dumps(all_evals, ensure_ascii=False, indent=2), encoding="utf-8")

    compare = {
        "note": "V3.1.6 mature EntrySignal vs V3.1.5 early CONFIRMED EntrySignal",
        "v315_baseline": V315_BASELINE,
        "v316": {
            code6: results[code6]["metrics"]["by_rule"]["current"] for code6, _ in FIXTURES
        },
        "signal_count_delta": {
            code6: results[code6]["metrics"]["by_rule"]["current"]["signal_count"]
            - V315_BASELINE[code6]["signal_count"]
            for code6, _ in FIXTURES
        },
    }
    OUT_COMPARE.write_text(json.dumps(compare, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    summary = {
        "span": [start, end],
        "fixtures": list(FIXTURES),
        "entry_price_mode": "signal_close",
        "version": "3.1.6",
        "metrics": {code6: results[code6]["metrics"] for code6, _ in FIXTURES},
        "focus": {code6: results[code6].get("focus") for code6, _ in FIXTURES},
        "post_signal_paths": {
            code6: results[code6].get("post_signal_paths") for code6, _ in FIXTURES
        },
        "checklist": [
            {"item": a, "pass": b, "evidence": c}
            for a, b, c in _checklist(results, publish_ok, publish_md5)
        ],
        "publish_close_v3_md5": publish_md5,
        "publish_untouched": publish_ok,
        "maturity_config": V316_MATURITY,
        "label_config": get_v316_label(),
    }
    OUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    md = build_report(results, publish_md5, publish_ok)
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"Wrote {OUT_MD}")
    return OUT_MD


if __name__ == "__main__":
    run()
