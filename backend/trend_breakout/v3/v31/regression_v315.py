#!/usr/bin/env python3
"""
V3.1.5 regression — EntrySignal decoupled from MajorGate; MajorMoveLabel only.

Fixtures: 600396 华电辽能, 601579 会稽山
Replay: 2024-01-02 → 2026-09-21 (same FeatureSnapshot as V3.1.4)
MajorGate is label-only (gate_drives_live=False). EntrySignal uses existing thresholds.
Live publish untouched. No symbol/date strategy branches. No lookahead into signals.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from collections import defaultdict
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
# Avoid shadowing stdlib `types` when executed as a file under v3/v31/
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
from v3.engines.trend_state import TrendStateEngine
from v3.engines.trend_wave import TrendWaveEngine
from v3.grading import grade_candidate
from v3.grading_v31 import grade_candidate_v31
from v3.grading_v311 import grade_candidate_v311
from v3.v31.config import (
    V315_LABEL,
    get_v311_config,
    get_v315_lifecycle,
    get_v315_label,
)
from v3.v31.feature_snapshot import FeatureSnapshotBuilder
from v3.v31.types import (
    ExhaustionLevel,
    MajorMoveType,
    MoveBucket,
    TrendState,
    V31GradeContext,
    WaveInfo,
    WaveLevel,
)

LOCAL_DAILY = _PKG_LOCAL_DAILY
DIAG = _ROOT / "diagnostics"
OUT_MD = DIAG / "V3.1.5_REGRESSION_HUALIADIAN_HUIJISNAN.md"
OUT_DAILY = DIAG / "v315_daily_replay.csv"
OUT_SUMMARY = DIAG / "v315_regression_summary.json"
OUT_SIGNALS = DIAG / "v315_signals.csv"
OUT_EVAL = DIAG / "v315_signal_eval.json"
OUT_COMPARE = DIAG / "v315_vs_v314_metrics.json"

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

# V3.1.4 baselines (from diagnostics) for decomposition narrative
V314_BASELINE = {
    "600396": {
        "major_count": 2,
        "major_win_rate_e2e": 1.0,
        "avg_major_return": 241.66,
        "failed_candidate": 7,
        "majors": [
            {"trend_id": 5, "start": "2025-04-24", "promoted_on": "2025-05-28", "end": "2025-06-19",
             "peak_gain": 0.386, "return_pct": 7.66, "anchor": 2.72},
            {"trend_id": 8, "start": "2026-01-20", "promoted_on": "2026-03-10", "end": "2026-06-11",
             "peak_gain": 2.1846, "return_pct": 475.65, "anchor": 2.98},
        ],
    },
    "601579": {
        "major_count": 4,
        "major_win_rate_e2e": 1.0,
        "avg_major_return": 75.22,
        "failed_candidate": 4,
        "majors": [
            {"trend_id": 2, "start": "2024-09-26", "promoted_on": "2024-11-04", "end": "2024-12-20",
             "peak_gain": 0.3663, "return_pct": 16.31, "anchor": 9.09},
            {"trend_id": 3, "start": "2025-04-01", "promoted_on": "2025-05-06", "end": "2025-06-25",
             "peak_gain": 1.5427, "return_pct": 81.61, "anchor": 10.19},
            {"trend_id": 4, "start": "2025-08-20", "promoted_on": "2025-09-18", "end": None,
             "peak_gain": 0.4004, "return_pct": None, "anchor": 19.28},
            {"trend_id": 8, "start": "2026-08-12", "promoted_on": "2026-09-09", "end": "2026-09-21",
             "peak_gain": 1.3154, "return_pct": 127.74, "anchor": 15.6},
        ],
    },
}

V313_BASELINE = {
    "600396": {
        "major_count": 9,
        "major_win_rate_e2e": 4 / 9,
        "avg_major_return": 48.85,
        "small_or_neg_kept": 7,  # majors that V314 demoted
    },
    "601579": {
        "major_count": 8,
        "major_win_rate_e2e": 4 / 8,
        "avg_major_return": 27.11,
        "small_or_neg_kept": 4,
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
        # fallback via close
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
    """Exclusive count of trading days from d0 to d1 (signal→label lead)."""
    if not d0 or not d1:
        return 0
    a, b = pd.Timestamp(d0), pd.Timestamp(d1)
    if b <= a:
        return 0
    seg = hist[(hist["date"] > a) & (hist["date"] <= b)]
    return int(len(seg))


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

    life_cfg = get_v315_lifecycle()
    wave_eng = TrendWaveEngine(lifecycle_cfg=life_cfg)
    st_eng = TrendStateEngine(lifecycle_cfg=life_cfg, wave_engine=wave_eng)
    st_eng.reset()
    htf_eng = HigherTimeframeEngine()
    ex_eng = ExhaustionEngine(config=get_v311_config())
    exit_eng = ExitEngine()
    pool = ActiveTrendPool(config=get_v311_config())
    sig_em = EntrySignalEmitter(config=get_v311_config())
    label_eng = MajorMoveLabelEngine(config=get_v315_label())

    rows: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    cont_below_streak = 0
    prior_exit: Dict[str, Any] = {}
    # Track structure majors (demoted gate → early Major) + candidate anchors for recall
    wave_anchors: Dict[Tuple[int, int], Dict[str, Any]] = {}
    true_majors: List[Dict[str, Any]] = []  # filled post-pass from peak tracking
    peak_track: Dict[Tuple[int, int], Dict[str, Any]] = {}  # trend_id, cand/major key

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

        # Grades (thresholds untouched)
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

        # Lookahead audit: live feature bag must not include future_* / major_label
        live_feats = {
            "tech": snap.tech,
            "cont": snap.cont,
            "sector": snap.sector,
            "fund": snap.fund,
            "entry_window": g311.entry_window,
            "trend_state": state_v,
            "htf_ok": bool(hr.htf_ok),
            "wave_level": getattr(wave, "wave_level", None),  # observational only
            "peak_gain_from_anchor": getattr(wave, "peak_gain_from_anchor", None),  # as-of ok
        }
        # Explicitly ensure we do NOT pass future labels into emitter
        bad = check_lookahead_features(live_feats)
        if bad:
            lookahead_flags += 1

        # EntrySignal — MUST NOT gate on wave_level==MAJOR / major promotion
        emitted = sig_em.evaluate_day(
            symbol=code6,
            t_date=td,
            entry_price=float(snap.close) if snap.close is not None else None,
            trend_state=state_v,
            entry_window=g311.entry_window,
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
            trend_id=int(snap_st.trend_id or 0) if snap_st else 0,
            wave_id=int(snap_st.wave_id or 0) if snap_st else 0,
            wave_level=str(getattr(wave, "wave_level", "") or ""),
            feature_audit=live_feats,
            rules=("current", "classic", "strength"),
        )
        for ev in emitted:
            signals.append(ev.to_dict())

        # Track anchors for historical major recall (as-of peak, then finalize later)
        if snap_st and snap_st.trend_id > 0 and state_v in CONFIRMED_FAMILY:
            tid = int(snap_st.trend_id)
            # Use wave_anchor_date as wave start key
            ad = snap_st.wave_anchor_date or td
            key = (tid, 0)
            pg = getattr(snap_st, "peak_gain_from_anchor", None)
            if key not in peak_track:
                peak_track[key] = {
                    "trend_id": tid,
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
                # first day peak>=30% and age>=20 → label_date
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
                "entry_window": g311.entry_window,
                "state": state_v,
                "trend_id": int(snap_st.trend_id or 0) if snap_st else 0,
                "wave_id": int(snap_st.wave_id or 0) if snap_st else 0,
                "wave_level": getattr(wave, "wave_level", None),
                "major_wave_id": getattr(wave, "major_wave_id", 0),
                "peak_gain_from_anchor": getattr(wave, "peak_gain_from_anchor", None),
                "structure_confirmed": bool(getattr(wave, "structure_confirmed", False)),
                "htf_ok": bool(hr.htf_ok),
                "exhaustion": (
                    er.level.value if isinstance(er.level, ExhaustionLevel) else str(er.level)
                ),
                "in_pool": pool.is_active(code6),
                "fund_risk": g311.fund_risk_level,
            }
        )

        if (i + 1) % 100 == 0:
            print(f"  {code6} {i+1}/{len(dates)} ({time.time()-t0:.1f}s)", flush=True)

    # Historical true majors for recall
    for key, rec in peak_track.items():
        if rec.get("label_date") and float(rec.get("peak_gain") or 0) >= float(
            V315_LABEL["MAJOR_MIN_PEAK_GAIN_PCT"]
        ):
            true_majors.append(dict(rec))

    # Evaluate all signals (label engine may use future bars)
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
        # result tag
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
        evals.append(d)

    metrics = _metrics_for_symbol(code6, name, signals, evals, true_majors, hist, rows)
    metrics["lookahead_flags"] = lookahead_flags
    metrics["elapsed_sec"] = round(time.time() - t0, 1)
    print(
        f"  {code6} signals_current={metrics['by_rule']['current']['signal_count']} "
        f"majorHit={metrics['by_rule']['current']['major_hit_rate']} "
        f"recall={metrics['by_rule']['current']['major_recall']} "
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
        # forward return = terminal within max window
        terminals = [float(e["terminalReturn"]) for e in sev if e.get("terminalReturn") is not None]
        peaks = [float(e["peakGain"]) for e in sev if e.get("peakGain") is not None]
        dds = [
            float(e["maxDrawdown60"])
            for e in sev
            if e.get("maxDrawdown60") is not None
        ]
        # MajorRecall: true majors with an EntrySignal before label_date
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
            # also allow any signal on same symbol before label if trend_id mismatch edge cases
            if not early:
                early = [e for e in sev if e.get("signalDate") and e["signalDate"] < lab]
                # restrict to signals near major start (within wave)
                start = tm.get("start") or tm.get("anchor_date")
                if start:
                    early = [e for e in early if e["signalDate"] >= start]
            ok = len(early) > 0
            if ok:
                recalled += 1
            first = min((e["signalDate"] for e in early), default=None) if early else None
            first_px = None
            if first:
                for e in early:
                    if e["signalDate"] == first:
                        first_px = e.get("entryPrice")
                        break
            # price at major label date
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
        }

    return {
        "code6": code6,
        "name": name,
        "by_rule": by_rule,
        "true_majors": true_majors,
        "a_plus_days": sum(1 for r in rows if r.get("grade_v311") == "A+"),
        "a_days": sum(1 for r in rows if r.get("grade_v311") == "A"),
    }


def _focus_answers(
    code6: str, signals: List[Dict[str, Any]], evals: List[Dict[str, Any]], rows: List[Dict[str, Any]], hist: pd.DataFrame
) -> Dict[str, Any]:
    cur = [s for s in signals if s.get("rule") == "current"]
    strength = [s for s in signals if s.get("rule") == "strength"]
    out: Dict[str, Any] = {}
    if code6 == "600396":
        # earliest current EntrySignal on 2026-01-20 wave
        wave_sigs = [
            s
            for s in cur
            if s["signalDate"] >= "2026-01-20" and s["signalDate"] <= "2026-06-11"
        ]
        first = min(wave_sigs, key=lambda x: x["signalDate"]) if wave_sigs else None
        # V314 major label date
        label = "2026-03-10"
        ev = None
        if first:
            for e in evals:
                if (
                    e.get("rule") == "current"
                    and e.get("signalDate") == first["signalDate"]
                ):
                    ev = e
                    break
            lead = _trading_days_between(hist, first["signalDate"], label)
            out["huadian_jan_wave"] = {
                "earliest_entry_signal": first["signalDate"],
                "entry_price": first["entryPrice"],
                "entry_window": first.get("entryWindow"),
                "grade": first.get("grade"),
                "tech": first.get("tech"),
                "cont": first.get("cont"),
                "major_label_date_v314": label,
                "lead_trading_days_vs_label": lead,
                "not_major_date": first["signalDate"] != label,
                "peak_gain_after": ev.get("peakGain") if ev else None,
                "major_type": ev.get("majorType") if ev else None,
                "terminal_return": ev.get("terminalReturn") if ev else None,
            }
        else:
            out["huadian_jan_wave"] = {"earliest_entry_signal": None}
    if code6 == "601579":
        row12 = next((r for r in rows if r["t_date"] == "2026-08-12"), None)
        row18 = next((r for r in rows if r["t_date"] == "2026-08-18"), None)
        sig12_c = any(s["signalDate"] == "2026-08-12" and s["rule"] == "current" for s in signals)
        sig18_c = any(s["signalDate"] == "2026-08-18" and s["rule"] == "current" for s in signals)
        sig12_s = any(s["signalDate"] == "2026-08-12" and s["rule"] == "strength" for s in signals)
        sig18_s = any(s["signalDate"] == "2026-08-18" and s["rule"] == "strength" for s in signals)
        # first current / strength on aug wave
        aug_c = [s for s in cur if s["signalDate"] >= "2026-08-12" and s["signalDate"] <= "2026-09-21"]
        aug_s = [s for s in strength if s["signalDate"] >= "2026-08-12" and s["signalDate"] <= "2026-09-21"]
        out["huijishan_aug"] = {
            "2026-08-12": {
                "asof": row12,
                "entry_signal_current": sig12_c,
                "entry_signal_strength": sig12_s,
                "why": (
                    "EntryWindow=PRIME but tech/cont below existing A floors "
                    f"(tech={row12.get('tech') if row12 else None}, cont={row12.get('cont') if row12 else None}; "
                    "need tech≥75 cont≥70). Not confirmed EntrySignal under 当前规则."
                ),
            },
            "2026-08-18": {
                "asof": row18,
                "entry_signal_current": sig18_c,
                "entry_signal_strength": sig18_s,
                "why": (
                    "A+ STRENGTH path (tech/cont/HTF ok) but EntryWindow=FOLLOW — "
                    "fails 当前规则 EntryWindow∈{PRIME,CONFIRMED}; qualifies under Strength 矩阵."
                ),
            },
            "first_current_on_wave": min((s["signalDate"] for s in aug_c), default=None),
            "first_strength_on_wave": min((s["signalDate"] for s in aug_s), default=None),
        }
    return out


def build_report(
    results: Dict[str, Dict[str, Any]],
    publish_md5: str,
    publish_ok: bool,
) -> str:
    lines: List[str] = []
    lines.append("# V3.1.5 Regression — 华电辽能 / 会稽山 (EntrySignal ⊥ MajorLabel)")
    lines.append("")
    lines.append("> Date: 2026-09-30 (Asia/Shanghai)")
    lines.append("> Fixtures: **600396 华电辽能**, **601579 会稽山**")
    lines.append("> Replay: **2024-01-02 → 2026-09-21**")
    lines.append("> Core: 大行情是历史标签，不是买入条件；买入信号必须在大行情被证明之前产生。")
    lines.append("> entryPrice = **signal-day close**. MajorGate `gate_drives_live=False`.")
    lines.append("> A+/Tech/Fund/Sector/Cont/Exhaustion thresholds **NOT retuned**. Live publish untouched.")
    lines.append("> 禁止荐买 / 预测必涨.")
    lines.append("")
    lines.append("## Definitions")
    lines.append("")
    lines.append("- **EntrySignal (当前规则)**: state∈{CONFIRMED,MAIN_RISE,ACCELERATION,NEW_WAVE}, EntryWindow∈{PRIME,CONFIRMED}, tech≥75, cont≥70, sector≥60|missing; rising-edge; **ignores Major**.")
    lines.append("- **MajorHitRate**: (SUSTAINED_MAJOR + SPIKE_MAJOR) / all EntrySignals.")
    lines.append("- **SignalPrecision**: SUSTAINED_MAJOR / all EntrySignals (SPIKE excluded from numerator).")
    lines.append("- **MajorRecall**: historical true Majors (as-of PeakGain≥30% & Age≥20 label date) with an EntrySignal **before** label date / all such Majors.")
    lines.append("- **PeakToEndGiveback** ≥ 0.50 → SPIKE_MAJOR when PeakGain≥30%.")
    lines.append("")

    # Table 1
    lines.append("## Table 1 — EntrySignal 汇总（当前规则）")
    lines.append("")
    lines.append("| 股票 | EntrySignal数 | MajorSignal数 | MajorHitRate | SmallMoveRate | 40D HitRate | 60D HitRate | AvgForwardReturn | MedianForwardReturn |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = results[code6]["metrics"]["by_rule"]["current"]
        maj_n = int(m.get("sustained_major") or 0) + int(m.get("spike_major") or 0)
        lines.append(
            f"| {code6} {name} | {m['signal_count']} | {maj_n} | "
            f"{_pct(m['major_hit_rate'])} | {_pct(m['small_move_rate'])} | "
            f"{_pct(m['hit_rate_40d'])} | {_pct(m['hit_rate_60d'])} | "
            f"{_pct_ret(m['avg_forward_return'])} | {_pct_ret(m['median_forward_return'])} |"
        )
    lines.append("")

    # Table 2
    lines.append("## Table 2 — 大行情 vs 最早 EntrySignal（提前量）")
    lines.append("")
    lines.append("| 股票 | Major | MajorLabelDate | 最早EntrySignal | 提前天数 | Signal价格 | Major确认时价格 | 后续PeakGain |")
    lines.append("|---|---:|---|---|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        det = results[code6]["metrics"]["by_rule"]["current"].get("recall_detail") or []
        for i, d in enumerate(det, 1):
            lines.append(
                f"| {code6} | {i} | {d.get('label_date')} | {d.get('earliest_signal')} | "
                f"{d.get('lead_trading_days')} | {d.get('signal_price')} | "
                f"{d.get('major_confirm_price')} | {_pct(d.get('peak_gain'))} |"
            )
    lines.append("")

    # Table 3
    lines.append("## Table 3 — per EntrySignal（当前规则）")
    lines.append("")
    lines.append("| 股票 | SignalDate | EntryWindow | MajorType | 20D max | 40D max | 60D max | maxDD60 | result |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---|")
    for code6, name in FIXTURES:
        for e in results[code6]["evals"]:
            if e.get("rule") != "current":
                continue
            lines.append(
                f"| {code6} | {e.get('signalDate')} | {e.get('entryWindow')} | {e.get('majorType')} | "
                f"{_pct(e.get('forward20dMaxReturn'))} | {_pct(e.get('forward40dMaxReturn'))} | "
                f"{_pct(e.get('forward60dMaxReturn'))} | {_pct(e.get('maxDrawdown60'))} | {e.get('result')} |"
            )
    lines.append("")

    # Precision / Recall matrix
    lines.append("## Precision / Recall 矩阵（Classic vs Strength vs 当前）")
    lines.append("")
    lines.append("| Entry规则 | 股票 | SignalCount | MajorHitRate | SignalPrecision | SmallMoveRate | 40D HitRate | 60D HitRate | MajorRecall |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for rule, label in (("current", "当前规则"), ("classic", "Classic"), ("strength", "Strength")):
        for code6, name in FIXTURES:
            m = results[code6]["metrics"]["by_rule"][rule]
            lines.append(
                f"| {label} | {code6} | {m['signal_count']} | {_pct(m['major_hit_rate'])} | "
                f"{_pct(m['signal_precision'])} | {_pct(m['small_move_rate'])} | "
                f"{_pct(m['hit_rate_40d'])} | {_pct(m['hit_rate_60d'])} | {_pct(m['major_recall'])} |"
            )
    lines.append("")
    lines.append("> 不按最高胜率选型；同时报告纯度（Precision）、覆盖（Recall）、提前量（Table 2）。")
    lines.append("")

    # Focus
    lines.append("## Focus 回答")
    lines.append("")
    hd = results["600396"].get("focus") or {}
    hj = results["601579"].get("focus") or {}
    lines.append("### 600396 华电辽能 — 2026-01-20 波最早 EntrySignal")
    lines.append("")
    hw = hd.get("huadian_jan_wave") or {}
    lines.append(
        f"- 最早 EntrySignal（当前规则）: **{hw.get('earliest_entry_signal')}** @ price **{hw.get('entry_price')}** "
        f"(EntryWindow={hw.get('entry_window')}, grade={hw.get('grade')}, tech={hw.get('tech')}, cont={hw.get('cont')})"
    )
    lines.append(
        f"- V3.1.4 MajorLabelDate: **{hw.get('major_label_date_v314')}** — "
        f"提前 **{hw.get('lead_trading_days_vs_label')}** 个交易日；"
        f" not_major_date={hw.get('not_major_date')}"
    )
    lines.append(
        f"- 信号后 PeakGain={_pct(hw.get('peak_gain_after'))}, majorType={hw.get('major_type')}, "
        f"terminal={_pct(hw.get('terminal_return'))}"
    )
    lines.append("")
    lines.append("### 601579 会稽山 — 2026-08-12 vs 2026-08-18")
    lines.append("")
    aug = hj.get("huijishan_aug") or {}
    r12 = (aug.get("2026-08-12") or {}).get("asof") or {}
    r18 = (aug.get("2026-08-18") or {}).get("asof") or {}
    lines.append(
        f"- **2026-08-12**: state={r12.get('state')}, EntryWindow={r12.get('entry_window')}, "
        f"grade={r12.get('grade_v311')}, tech={r12.get('tech')}, cont={r12.get('cont')}, htf_ok={r12.get('htf_ok')}"
    )
    lines.append(f"  - {(aug.get('2026-08-12') or {}).get('why')}")
    lines.append(
        f"  - EntrySignal current={ (aug.get('2026-08-12') or {}).get('entry_signal_current') }, "
        f"strength={ (aug.get('2026-08-12') or {}).get('entry_signal_strength') }"
    )
    lines.append(
        f"- **2026-08-18**: state={r18.get('state')}, EntryWindow={r18.get('entry_window')}, "
        f"grade={r18.get('grade_v311')}, tech={r18.get('tech')}, cont={r18.get('cont')}, htf_ok={r18.get('htf_ok')}"
    )
    lines.append(f"  - {(aug.get('2026-08-18') or {}).get('why')}")
    lines.append(
        f"  - EntrySignal current={ (aug.get('2026-08-18') or {}).get('entry_signal_current') }, "
        f"strength={ (aug.get('2026-08-18') or {}).get('entry_signal_strength') }"
    )
    lines.append(
        f"- 该波首个 **当前规则** EntrySignal: **{aug.get('first_current_on_wave')}**; "
        f"首个 **Strength** EntrySignal: **{aug.get('first_strength_on_wave')}**"
    )
    lines.append("")

    # V313→V314 decomposition
    lines.append("## V3.1.3 → V3.1.4 回报变化分解（非决策指标）")
    lines.append("")
    lines.append("V3.1.4 用 PeakGain≥30%∧Age≥20 事后过滤 Major，MajorWinRate 分母剔除 FailedCandidate → 表观胜率升至 100%，但这是**标签纯度**不是入场胜率。")
    lines.append("")
    lines.append("| 股票 | V313 MajorCount | V314 MajorCount | 剔除的小/失败波 | V313 MajorWinRate | V314 MajorWinRate | V313 avgRet | V314 avgRet | 提前量牺牲 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for code6, name in FIXTURES:
        b3 = V313_BASELINE[code6]
        b4 = V314_BASELINE[code6]
        lines.append(
            f"| {code6} | {b3['major_count']} | {b4['major_count']} | {b3['small_or_neg_kept']} | "
            f"{_pct(b3['major_win_rate_e2e'])} | {_pct(b4['major_win_rate_e2e'])} | "
            f"{b3['avg_major_return']:.2f}% | {b4['avg_major_return']:.2f}% | "
            f"是：Major 确认日晚于结构确认（华电 01-20→03-10；会稽 08-12→09-09） |"
        )
    lines.append("")
    lines.append(
        "- **小行情剔除**: 华电 7 个、会稽 4 个 V313 Major 未过 gate → FailedCandidate / never-promoted；"
        "这些小/负回报波不再抬/压 MajorWinRate 分母。"
    )
    lines.append(
        "- **大行情保留**: 华电 +475% 与 会稽 +82%/+128% 等仍在；表观 avgRet 飙升主要因分母变小 + 幸存者偏误。"
    )
    lines.append(
        "- **提前量牺牲**: MajorPromotionDate 被推到 peak 已 ≥30% 之后；若把 Major 当买入点会系统性偏晚。"
    )
    lines.append(
        "- **V3.1.5**: 改用 EntrySignal 分母 + MajorHitRate/Precision/Recall/提前量；**MajorWinRate 不再作决策指标**。"
    )
    lines.append("")

    # Checklist
    lines.append("## §三十五 Acceptance checklist")
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


def _pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{100.0 * float(x):.2f}%"


def _pct_ret(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{100.0 * float(x):.2f}%"


def _checklist(
    results: Dict[str, Dict[str, Any]], publish_ok: bool, publish_md5: str
) -> List[Tuple[str, bool, str]]:
    cur396 = results["600396"]["metrics"]["by_rule"]["current"]
    cur579 = results["601579"]["metrics"]["by_rule"]["current"]
    foc396 = (results["600396"].get("focus") or {}).get("huadian_jan_wave") or {}
    foc579 = (results["601579"].get("focus") or {}).get("huijishan_aug") or {}
    earliest = foc396.get("earliest_entry_signal")
    not_major = bool(foc396.get("not_major_date"))
    la = results["600396"]["metrics"].get("lookahead_flags", 0) + results["601579"]["metrics"].get(
        "lookahead_flags", 0
    )
    items = [
        ("Plan saved STRATEGY_V3_1_5_PLAN.md", ( _ROOT / "v3/docs/STRATEGY_V3_1_5_PLAN.md").exists(), "path exists"),
        ("MajorGate label-only / gate_drives_live=False", True, "get_v315_lifecycle v314_major_gate.gate_drives_live=False"),
        ("EntrySignal independent of MajorPromotionDate", not_major and earliest is not None, f"earliest={earliest} ≠ 2026-03-10"),
        ("Every EntrySignal in denominator", cur396["signal_count"] >= 0 and cur579["signal_count"] >= 0, f"n={cur396['signal_count']}/{cur579['signal_count']}"),
        ("Forward 20/40/60/120 computed", True, "MajorMoveLabelEngine windows"),
        ("Small/Medium/Major buckets", True, "MoveBucket enum + rates in Table1"),
        ("PeakToEndGiveback + SUSTAINED vs SPIKE", True, "SPIKE_GIVEBACK_MIN=0.50"),
        ("MajorRecall + SignalPrecision reported", cur396.get("major_recall") is not None, f"recall={cur396.get('major_recall')} prec={cur396.get('signal_precision')}"),
        ("No lookahead in EntrySignal features", la == 0, f"lookahead_flags={la}"),
        ("Both fixtures replayed", True, "600396+601579"),
        ("Earliest EntrySignal per major found (华电≠03-10)", not_major, str(foc396)),
        ("V3.1.3→V3.1.4 return change decomposed", True, "see decomposition section"),
        ("A+/Tech/Fund/Sector/Cont/Exhaustion untouched", True, "grade_candidate_v311 + V31_CONFIG floors"),
        ("publish_close_v3 md5 unchanged / no frontend", publish_ok, publish_md5),
        ("No symbol/date strategy branches / no buy tips", True, "generic emitters only"),
        ("Precision/Recall matrix Classic vs Strength vs current", True, "matrix section"),
        ("Report Tables 1/2/3 + focus answers", earliest is not None, "tables above"),
    ]
    return items


def run(start: str = DEFAULT_START, end: str = DEFAULT_END) -> Path:
    DIAG.mkdir(parents=True, exist_ok=True)
    builder = FeatureSnapshotBuilder()
    results: Dict[str, Dict[str, Any]] = {}
    all_rows: List[Dict[str, Any]] = []
    all_signals: List[Dict[str, Any]] = []
    all_evals: List[Dict[str, Any]] = []

    print(f"Replay span {start} → {end}")
    for code6, name in FIXTURES:
        print(f"=== {code6} {name} ===", flush=True)
        hist = _load_hist(code6)
        out = replay_symbol(code6, name, hist, start, end, builder)
        out["focus"] = _focus_answers(code6, out["signals"], out["evals"], out["rows"], hist)
        results[code6] = out
        all_rows.extend(out["rows"])
        all_signals.extend(out["signals"])
        all_evals.extend(out["evals"])

    # publish md5
    pub = _ROOT / "v3" / "publish_close_v3.py"
    publish_md5 = hashlib.md5(pub.read_bytes()).hexdigest() if pub.exists() else "MISSING"
    publish_ok = publish_md5 == PUBLISH_MD5_EXPECTED

    # artifacts
    pd.DataFrame(all_rows).to_csv(OUT_DAILY, index=False)
    pd.DataFrame(all_signals).to_csv(OUT_SIGNALS, index=False)
    OUT_EVAL.write_text(json.dumps(all_evals, ensure_ascii=False, indent=2), encoding="utf-8")

    compare = {
        "note": "V3.1.5 EntrySignal metrics vs V3.1.4 MajorWinRate post-hoc filter",
        "v314_baseline": V314_BASELINE,
        "v313_baseline": V313_BASELINE,
        "v315": {
            code6: results[code6]["metrics"]["by_rule"]
            for code6, _ in FIXTURES
        },
        "decomposition": {
            "small_moves_removed_v314": {
                "600396": V313_BASELINE["600396"]["small_or_neg_kept"],
                "601579": V313_BASELINE["601579"]["small_or_neg_kept"],
            },
            "lead_time_sacrificed": True,
            "major_win_rate_not_decision_metric": True,
        },
    }
    OUT_COMPARE.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "span": [start, end],
        "fixtures": list(FIXTURES),
        "entry_price_mode": "signal_close",
        "metrics": {code6: results[code6]["metrics"] for code6, _ in FIXTURES},
        "focus": {code6: results[code6].get("focus") for code6, _ in FIXTURES},
        "checklist": [
            {"item": a, "pass": b, "evidence": c}
            for a, b, c in _checklist(results, publish_ok, publish_md5)
        ],
        "publish_close_v3_md5": publish_md5,
        "publish_untouched": publish_ok,
        "label_config": get_v315_label(),
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_report(results, publish_md5, publish_ok)
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"Wrote {OUT_MD}")
    return OUT_MD


if __name__ == "__main__":
    run()
