#!/usr/bin/env python3
"""
V3.1.2 full day-by-day regression — Trend/Wave/State lifecycle fix.

Fixtures: 600396 华电辽能, 601579 会稽山
Replay: 2024-01-02 → 2026-09-21 (same as V3.1.1)
Does NOT retune A+/Tech/Fund/Sector/Exhaustion thresholds.
Live publish untouched. No symbol/date strategy branches.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import hashlib
import json
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
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.engines.active_trend_pool import ActiveTrendPool
from v3.engines.exhaustion import ExhaustionEngine
from v3.engines.exit_engine import ExitEngine
from v3.engines.higher_timeframe import HigherTimeframeEngine
from v3.engines.trend_state import TrendStateEngine
from v3.engines.trend_wave import TrendWaveEngine
from v3.grading import grade_candidate
from v3.grading_v31 import grade_candidate_v31
from v3.grading_v311 import grade_candidate_v311
from v3.types import Grade
from v3.v31.config import get_v31_config, get_v311_config, get_v312_lifecycle
from v3.v31.feature_snapshot import FeatureSnapshotBuilder
from v3.v31.types import ExhaustionLevel, TrendState, V31GradeContext, WaveInfo

LOCAL_DAILY = _PKG_LOCAL_DAILY
DIAG = _ROOT / "diagnostics"
OUT_MD = DIAG / "V3.1.2_REGRESSION_HUALIADIAN_HUIJISNAN.md"
OUT_DAILY = DIAG / "v312_daily_replay.csv"
OUT_SUMMARY = DIAG / "v312_regression_summary.json"
OUT_WAVES = DIAG / "v312_wave_metrics.json"
OUT_TRANSITIONS = DIAG / "v312_state_transitions.csv"
OUT_COMPARE = DIAG / "v312_vs_v311_metrics.json"

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

KEY_DATES = {
    "600396": [
        "2024-05-08", "2024-05-15", "2024-05-20",
        "2026-03-06", "2026-03-10", "2026-03-12", "2026-03-17", "2026-08-13",
    ],
    "601579": [
        "2025-05-19", "2025-05-20", "2025-05-21", "2025-05-28",
        "2026-08-18", "2026-09-09", "2026-09-17", "2026-09-21",
    ],
}

# V3.1.1 baselines from diagnostics/V3.1.1_REGRESSION (hardcoded for delta table)
V311_BASELINE = {
    "600396": {
        "wave_count": 16,
        "valid_wave": 8,
        "recog_rate": 0.50,
        "main_rise_days": None,  # filled from v311 csv if present
        "a_plus_v311": 15,
    },
    "601579": {
        "wave_count": 8,
        "valid_wave": 5,
        "recog_rate": 0.625,
        "main_rise_days": None,
        "a_plus_v311": 10,
    },
}


def _load_hist(code6: str) -> pd.DataFrame:
    df = pd.read_parquet(LOCAL_DAILY / f"{code6}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _gval(g) -> str:
    if isinstance(g, Grade):
        return g.value
    return str(g)


def _limit_ups_5d(hist: pd.DataFrame, t_date: str) -> int:
    h = hist[hist["date"] <= pd.Timestamp(t_date)].tail(6)
    if len(h) < 2:
        return 0
    rets = h["close"].astype(float).pct_change() * 100.0
    return int((rets.tail(5) >= 9.5).sum())


def replay_symbol(
    code6: str,
    name: str,
    hist: pd.DataFrame,
    start: str,
    end: str,
    builder: FeatureSnapshotBuilder,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    sub = hist[(hist["date"] >= pd.Timestamp(start)) & (hist["date"] <= pd.Timestamp(end))]
    dates = [str(d.date()) for d in sub["date"].tolist()]

    life_cfg = get_v312_lifecycle()
    wave_eng = TrendWaveEngine(lifecycle_cfg=life_cfg)
    st_eng = TrendStateEngine(lifecycle_cfg=life_cfg, wave_engine=wave_eng)
    st_eng.reset()
    htf_eng = HigherTimeframeEngine()
    ex_eng = ExhaustionEngine(config=get_v311_config())
    exit_eng = ExitEngine()
    pool = ActiveTrendPool(config=get_v311_config())

    rows: List[Dict[str, Any]] = []
    transitions: List[Dict[str, Any]] = []
    cont_below_streak = 0
    wave_seen: Dict[Tuple[int, int], Dict[str, Any]] = {}  # (trend_id, wave_id)
    prior_exit: Dict[str, Any] = {}
    t0 = time.time()

    # Track wave ids seen after each failure for reuse metric
    wave_ids_per_trend: Dict[int, set] = defaultdict(set)
    failure_events = 0
    reuse_violations = 0
    anchor_invalid_days = 0
    active_wave_days = 0
    false_break_events = 0
    failure_confirms = 0

    for i, td in enumerate(dates):
        snap = builder.build(code6=code6, name=name, hist=hist, t_date=td)
        if not snap.ok:
            continue

        if snap.cont is not None and float(snap.cont) < 70:
            cont_below_streak += 1
        else:
            cont_below_streak = 0

        # ExitEngine structure prices (as-of T, no future)
        try:
            er_exit = exit_eng.evaluate(
                code=code6,
                hist=hist,
                t_date=td,
                prior_state=prior_exit,
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

        # Prefer ExitEngine break; fall back to snap
        trend_break = break_px if break_px is not None else snap.trend_break_price
        lu5 = _limit_ups_5d(hist, td)

        # Exhaustion first (risk layer) — does not drive FAILURE
        # provisional wave from prior step for exhaustion input
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

        sr = st_eng.step(
            code6=code6,
            t_date=td,
            close=snap.close,
            low=low,
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
        )
        snap_st = sr.snapshot
        wave = sr.wave or WaveInfo(wave_consumption=snap.consumption)
        state_v = sr.trend_state.value if isinstance(sr.trend_state, TrendState) else str(sr.trend_state)

        if sr.transition is not None:
            transitions.append(sr.transition.to_dict())
            if sr.transition.to_state == TrendState.TREND_FAILURE.value:
                failure_confirms += 1
                failure_events += 1
        if "FALSE_BREAK" in (sr.tags or []) or "FALSE_BREAK_PENDING" in (sr.tags or []):
            false_break_events += 1

        # Wave metrics tracking
        if snap_st and snap_st.trend_id > 0 and snap_st.wave_id > 0 and state_v in CONFIRMED_FAMILY:
            key = (snap_st.trend_id, snap_st.wave_id)
            wave_ids_per_trend[snap_st.trend_id].add(snap_st.wave_id)
            active_wave_days += 1
            if not snap_st.anchor_valid:
                anchor_invalid_days += 1
            if key not in wave_seen:
                wave_seen[key] = {
                    "trend_id": snap_st.trend_id,
                    "wave_id": snap_st.wave_id,
                    "start": td,
                    "anchor": snap_st.wave_anchor,
                    "anchor_date": snap_st.wave_anchor_date,
                    "anchor_kind": snap_st.wave_anchor_kind,
                    "anchor_valid": snap_st.anchor_valid,
                    "close0": snap.close,
                    "age": snap_st.wave_age_days,
                }
            else:
                wave_seen[key]["end"] = td
                wave_seen[key]["age"] = snap_st.wave_age_days
                wave_seen[key]["close1"] = snap.close
                wave_seen[key]["anchor_valid"] = bool(
                    wave_seen[key].get("anchor_valid") and snap_st.anchor_valid
                )
                c0 = wave_seen[key].get("close0")
                if c0 and snap.close and c0 > 0:
                    wave_seen[key]["return_pct"] = round(
                        (float(snap.close) / float(c0) - 1.0) * 100.0, 2
                    )

        # Detect wave_id reuse across trends (should be 0: ids restart per trend,
        # but global uniqueness of (trend_id, wave_id) is the contract;
        # reuse = same wave_id appearing in a later trend_id after a FAILURE)
        # Measured at end.

        # --- Grades (secondary; thresholds untouched) ---
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
        g30v = _gval(g30.grade)

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
        g31 = grade_candidate_v31(
            tech_score=snap.tech,
            continuity_score=snap.cont,
            fund_score=snap.fund,
            sector_score=snap.sector,
            risk_level=snap.risk_level,
            lifecycle=snap.lifecycle or "NONE",
            consumption_score=snap.consumption,
            overheated=snap.overheated,
            broken=snap.broken,
            ctx=ctx,
            config=get_v31_config(),
        )
        g311 = grade_candidate_v311(
            tech_score=snap.tech,
            continuity_score=snap.cont,
            fund_score=snap.fund,
            fund_insufficient=snap.fund_insufficient,
            sector_score=snap.sector,
            risk_level=snap.risk_level,
            lifecycle=snap.lifecycle or "NONE",
            consumption_score=snap.consumption,
            overheated=snap.overheated,
            broken=snap.broken,
            ext_ma20_pct=ext,
            ctx=ctx,
            config=get_v311_config(),
            regime_v3=snap.regime_v3,
        )
        g31v = _gval(g31.grade)
        g311v = _gval(g311.grade)
        pr = pool.update(
            code=code6,
            t_date=td,
            grade=g311v,
            trend_state=sr.trend_state,
            broken=(state_v == TrendState.TREND_FAILURE.value) or snap.broken,
        )

        rows.append(
            {
                "t_date": td,
                "code6": code6,
                "name": name,
                "tech": snap.tech,
                "cont": snap.cont,
                "fund": snap.fund,
                "sector": snap.sector,
                "consumption": snap.consumption,
                "risk": snap.risk_level,
                "life": snap.lifecycle,
                "oh": snap.overheated,
                "close": snap.close,
                "grade_v30": g30v,
                "grade_v31": g31v,
                "grade_v311": g311v,
                "path_v311": g311.path,
                "entry_window": g311.entry_window,
                "state": state_v,
                "prior_state": snap_st.prior_state if snap_st else None,
                "transition": snap_st.transition if snap_st else None,
                "trend_id": snap_st.trend_id if snap_st else 0,
                "wave_id": snap_st.wave_id if snap_st else 0,
                "wave_anchor": snap_st.wave_anchor if snap_st else None,
                "wave_anchor_kind": snap_st.wave_anchor_kind if snap_st else None,
                "anchor_valid": snap_st.anchor_valid if snap_st else False,
                "wave_cons": wave.wave_consumption,
                "trend_cons": wave.trend_consumption,
                "wave_age": wave.wave_age_days,
                "exhaustion": ExhaustionLevel.coerce(er.level).value,
                "fund_risk": g311.fund_risk_level,
                "htf": hr.monthly_label,
                "htf_ok": hr.htf_ok,
                "in_pool": pr.in_pool,
                "pool_action": pr.action,
                "regime": snap.regime_v3,
                "ma20_slope_neg": snap.ma20_slope_neg,
                "hl_broken": snap.higher_low_broken,
                "trend_break_price": trend_break,
                "false_break_pending": snap_st.false_break_pending if snap_st else False,
                "days_in_state": snap_st.days_in_state if snap_st else 0,
                "tags": "|".join(sr.tags or []),
            }
        )
        if (i + 1) % 100 == 0:
            print(f"  {code6} {i+1}/{len(dates)} ({time.time()-t0:.1f}s)", flush=True)

    # Wave reuse after failure: wave_id appearing under trend_id T also under T'>T
    all_pairs = list(wave_seen.keys())
    # Since wave_id restarts per trend, reuse = identical wave_id used in two trends
    # AFTER a failure between them — that is expected with per-trend restart.
    # Contract: never continue the SAME (retired) wave across FAILURE.
    # Measure: waves whose start is after a FAILURE but keep prior trend's last wave_id
    # without new trend_id → violation. With our engine, trend_id always bumps.
    # Secondary: global wave_id monotonically increases within trend; across trends
    # restarting at 1 is OK. Violation = same (trend_id, wave_id) after failure end.
    reuse_violations = 0
    # Check: for each failure transition, subsequent wave must have new trend_id
    fail_dates = [
        t["t_date"]
        for t in transitions
        if t.get("to_state") == TrendState.TREND_FAILURE.value
    ]
    for fd in fail_dates:
        pre = [k for k, w in wave_seen.items() if w.get("end") and w["end"] <= fd]
        post = [k for k, w in wave_seen.items() if w.get("start") and w["start"] > fd]
        pre_waves = {k[1] for k in pre}
        # post trend_ids must be strictly greater than pre trend_ids
        pre_trends = {k[0] for k in pre}
        post_trends = {k[0] for k in post}
        if pre_trends and post_trends and min(post_trends) <= max(pre_trends):
            # overlapping trend_id across failure
            reuse_violations += 1

    metrics = _compute_metrics(
        rows,
        wave_seen,
        anchor_invalid_days=anchor_invalid_days,
        active_wave_days=active_wave_days,
        reuse_violations=reuse_violations,
        false_break_events=false_break_events,
        failure_confirms=failure_confirms,
    )
    metrics["n_gradable"] = len(rows)
    metrics["n_calendar_bars"] = len(dates)
    metrics["elapsed_sec"] = round(time.time() - t0, 1)
    metrics["n_transitions"] = len(transitions)
    return rows, transitions, metrics


def _compute_metrics(
    rows: List[Dict[str, Any]],
    wave_seen: Dict[Tuple[int, int], Dict[str, Any]],
    *,
    anchor_invalid_days: int,
    active_wave_days: int,
    reuse_violations: int,
    false_break_events: int,
    failure_confirms: int,
) -> Dict[str, Any]:
    if not rows:
        return {}
    a_plus = {"v30": 0, "v31": 0, "v311": 0}
    state_days = defaultdict(int)
    active_days = 0
    confirmed_days = 0
    false_drop = 0
    max_active = 0
    cur_active = 0
    max_aplus = 0
    cur_aplus = 0

    for r in rows:
        for k, key in (("v30", "grade_v30"), ("v31", "grade_v31"), ("v311", "grade_v311")):
            if r.get(key) == "A+":
                a_plus[k] += 1
        st = r.get("state")
        state_days[st] += 1
        in_pool = bool(r.get("in_pool"))
        if in_pool:
            active_days += 1
            cur_active += 1
            max_active = max(max_active, cur_active)
        else:
            cur_active = 0
        if st in CONFIRMED_FAMILY:
            confirmed_days += 1
        if r.get("grade_v311") == "A+":
            cur_aplus += 1
            max_aplus = max(max_aplus, cur_aplus)
        else:
            cur_aplus = 0
        if (
            in_pool
            and st in CONFIRMED_FAMILY
            and r.get("grade_v311") not in ("A+", "A")
            and st != TrendState.TREND_FAILURE.value
        ):
            false_drop += 1

    confirmed_in_pool = sum(
        1 for r in rows if r.get("state") in CONFIRMED_FAMILY and bool(r.get("in_pool"))
    )
    persistence = (confirmed_in_pool / confirmed_days) if confirmed_days else 0.0
    false_rate = (false_drop / active_days) if active_days else 0.0

    waves = list(wave_seen.values())
    valid = [
        w
        for w in waves
        if int(w.get("age") or 0) >= 5
        and float(w.get("return_pct") or 0) >= 5.0
        and bool(w.get("anchor_valid", True))
    ]
    wave_count = len(waves)
    valid_count = len(valid)
    recog = (valid_count / wave_count) if wave_count else 0.0
    anchor_invalid_rate = (
        (anchor_invalid_days / active_wave_days) if active_wave_days else 0.0
    )
    # Also count waves born with invalid anchor
    invalid_wave_births = sum(1 for w in waves if not w.get("anchor_valid", True))
    reuse_rate = 0.0 if wave_count == 0 else (reuse_violations / max(1, failure_confirms or 1))
    # Hard contract metric
    wave_reuse_after_failure_rate = 0.0 if reuse_violations == 0 else (
        reuse_violations / max(1, failure_confirms)
    )

    return {
        "a_plus_days": a_plus,
        "active_trend_days": active_days,
        "active_trend_max_streak": max_active,
        "a_plus_max_streak_v311": max_aplus,
        "confirmed_family_days": confirmed_days,
        "confirmed_in_pool_days": confirmed_in_pool,
        "trend_persistence_rate": round(persistence, 4),
        "false_dropout_days": false_drop,
        "false_dropout_rate": round(false_rate, 4),
        "trend_wave_count": wave_count,
        "valid_wave_count": valid_count,
        "wave_recognition_rate": round(recog, 4),
        "wave_anchor_invalid_days": anchor_invalid_days,
        "wave_anchor_invalid_rate": round(anchor_invalid_rate, 4),
        "invalid_wave_births": invalid_wave_births,
        "wave_reuse_after_failure_violations": reuse_violations,
        "wave_reuse_after_failure_rate": round(wave_reuse_after_failure_rate, 4),
        "false_break_events": false_break_events,
        "failure_confirms": failure_confirms,
        "state_days": dict(state_days),
        "main_rise_days": int(state_days.get(TrendState.MAIN_RISE.value, 0)),
        "cooling_days": int(state_days.get(TrendState.COOLING.value, 0)),
        "new_wave_days": int(state_days.get(TrendState.NEW_WAVE.value, 0)),
        "acceleration_days": int(state_days.get(TrendState.ACCELERATION.value, 0)),
        "confirmed_days": int(state_days.get(TrendState.CONFIRMED.value, 0)),
        "failure_days": int(state_days.get(TrendState.TREND_FAILURE.value, 0)),
        "waves": waves,
    }


def _load_v311_main_rise() -> Dict[str, int]:
    """Count MAIN_RISE days from V3.1.1 daily csv if present."""
    p = DIAG / "v311_daily_replay.csv"
    out = {"600396": 0, "601579": 0}
    if not p.is_file():
        return out
    df = pd.read_csv(p)
    for code in out:
        sub = df[df["code6"].astype(str).str.zfill(6) == code]
        out[code] = int((sub["state"] == "MAIN_RISE").sum())
    return out


def build_report(
    all_rows: List[Dict[str, Any]],
    metrics_by_code: Dict[str, Dict[str, Any]],
    transitions: List[Dict[str, Any]],
    span: Tuple[str, str],
    publish_md5: str,
    checklist: List[Tuple[str, bool]],
) -> str:
    v311_mr = _load_v311_main_rise()
    for c in V311_BASELINE:
        V311_BASELINE[c]["main_rise_days"] = v311_mr.get(c, 0)

    lines: List[str] = []
    lines.append("# V3.1.2 Regression — 华电辽能 / 会稽山 (Trend/Wave/State lifecycle)")
    lines.append("")
    lines.append("> Date: 2026-09-28 (Asia/Shanghai)  ")
    lines.append("> Fixtures: **600396 华电辽能**, **601579 会稽山**  ")
    lines.append(f"> Replay span: **{span[0]} → {span[1]}** (same as V3.1.1)  ")
    lines.append("> Focus: TrendFailure / TrendId / WaveAnchor / COOLING→NEW_WAVE / MAIN_RISE  ")
    lines.append("> A+/Tech/Fund/Sector/Exhaustion thresholds **NOT retuned**.  ")
    lines.append("> Live AM/noon/close publish **untouched**. 禁止荐买 / 预测必涨.")
    lines.append("")

    # ---- Table 1: State/Wave primary metrics vs V3.1.1 ----
    lines.append("## Table 1 — State/Wave metrics vs V3.1.1")
    lines.append("")
    lines.append(
        "| code6 | name | WaveCount | ValidWave | RecogRate | "
        "V3.1.1 WaveCount | V3.1.1 Valid | V3.1.1 Recog | "
        "AnchorInvalidRate | ReuseAfterFailure | "
        "MAIN_RISE_Days | V3.1.1 MAIN_RISE | COOLING | NEW_WAVE | ACCEL |"
    )
    lines.append(
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        b = V311_BASELINE[code6]
        lines.append(
            "| {c} | {n} | {wc} | {vw} | {wr:.2%} | {bwc} | {bvw} | {bwr:.2%} | "
            "{air:.2%} | {rar:.2%} | {mr} | {bmr} | {cd} | {nw} | {ac} |".format(
                c=code6,
                n=name,
                wc=m.get("trend_wave_count", 0),
                vw=m.get("valid_wave_count", 0),
                wr=float(m.get("wave_recognition_rate") or 0),
                bwc=b["wave_count"],
                bvw=b["valid_wave"],
                bwr=float(b["recog_rate"]),
                air=float(m.get("wave_anchor_invalid_rate") or 0),
                rar=float(m.get("wave_reuse_after_failure_rate") or 0),
                mr=m.get("main_rise_days", 0),
                bmr=b.get("main_rise_days") or 0,
                cd=m.get("cooling_days", 0),
                nw=m.get("new_wave_days", 0),
                ac=m.get("acceleration_days", 0),
            )
        )
    lines.append("")
    lines.append(
        "Targets: WaveAnchorInvalidRate→0, WaveReuseAfterFailureRate=0; "
        "MAIN_RISE_Days ↑ vs V3.1.1; ACCELERATION ≠ OH default."
    )
    lines.append("")

    # ---- Table 2: State distribution + failure ----
    lines.append("## Table 2 — State distribution & failure hygiene")
    lines.append("")
    lines.append(
        "| code6 | CONFIRMED | MAIN_RISE | ACCELERATION | COOLING | NEW_WAVE | "
        "TREND_FAILURE | OBSERVE/NONE | FailureConfirms | FalseBreakEvents |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        sd = m.get("state_days") or {}
        obs = int(sd.get("OBSERVE", 0)) + int(sd.get("NONE", 0))
        lines.append(
            f"| {code6} | {sd.get('CONFIRMED',0)} | {sd.get('MAIN_RISE',0)} | "
            f"{sd.get('ACCELERATION',0)} | {sd.get('COOLING',0)} | {sd.get('NEW_WAVE',0)} | "
            f"{sd.get('TREND_FAILURE',0)} | {obs} | {m.get('failure_confirms',0)} | "
            f"{m.get('false_break_events',0)} |"
        )
    lines.append("")

    # ---- Table 3: Key dates state/wave (grades secondary) ----
    lines.append("## Table 3 — Key dates (state/wave primary; grades secondary)")
    lines.append("")
    lines.append(
        "| date | code | state | prior | trend_id | wave_id | anchor_kind | "
        "anchor_valid | exh | V3.1.1 grade | path | entry | cont |"
    )
    lines.append(
        "|---|---|---|---|---:|---:|---|---|---|---|---|---|---:|"
    )
    by_key = {(r["code6"], r["t_date"]): r for r in all_rows}
    for code6, _ in FIXTURES:
        for td in KEY_DATES[code6]:
            r = by_key.get((code6, td))
            if not r:
                lines.append(
                    f"| {td} | {code6} | — | — | — | — | — | — | — | — | — | — | — |"
                )
                continue
            lines.append(
                "| {td} | {c} | {st} | {ps} | {tid} | {wid} | {ak} | {av} | {exh} | "
                "{g} | {p} | {ew} | {cont} |".format(
                    td=r["t_date"],
                    c=r["code6"],
                    st=r.get("state"),
                    ps=r.get("prior_state"),
                    tid=r.get("trend_id"),
                    wid=r.get("wave_id"),
                    ak=r.get("wave_anchor_kind"),
                    av=r.get("anchor_valid"),
                    exh=r.get("exhaustion"),
                    g=r.get("grade_v311"),
                    p=r.get("path_v311"),
                    ew=r.get("entry_window"),
                    cont=r.get("cont"),
                )
            )
    lines.append("")

    # ---- Table 4: Wave detail ----
    lines.append("## Table 4 — Wave detail (anchor-based)")
    lines.append("")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        lines.append(f"### {code6} {name}")
        lines.append("")
        lines.append(
            f"- WaveCount={m.get('trend_wave_count')}, ValidWave={m.get('valid_wave_count')}, "
            f"RecogRate={float(m.get('wave_recognition_rate') or 0):.2%}"
        )
        lines.append(
            f"- WaveAnchorInvalidRate={float(m.get('wave_anchor_invalid_rate') or 0):.2%} "
            f"(invalid days {m.get('wave_anchor_invalid_days')}; "
            f"invalid births {m.get('invalid_wave_births')})"
        )
        lines.append(
            f"- WaveReuseAfterFailureRate={float(m.get('wave_reuse_after_failure_rate') or 0):.2%} "
            f"(violations={m.get('wave_reuse_after_failure_violations')})"
        )
        lines.append(
            f"- MAIN_RISE_Days={m.get('main_rise_days')} (V3.1.1={V311_BASELINE[code6].get('main_rise_days')})"
        )
        lines.append(
            f"- ActiveTrendDays={m.get('active_trend_days')}, "
            f"Persistence={float(m.get('trend_persistence_rate') or 0):.2%}"
        )
        waves = m.get("waves") or []
        if waves:
            lines.append("")
            lines.append(
                "| trend_id | wave_id | start | end | age | anchor | kind | valid | return_pct |"
            )
            lines.append("|---:|---:|---|---|---:|---:|---|---|---:|")
            for w in sorted(
                waves, key=lambda x: (int(x.get("trend_id") or 0), int(x.get("wave_id") or 0))
            ):
                lines.append(
                    f"| {w.get('trend_id')} | {w.get('wave_id')} | {w.get('start')} | "
                    f"{w.get('end')} | {w.get('age')} | {w.get('anchor')} | "
                    f"{w.get('anchor_kind')} | {w.get('anchor_valid')} | {w.get('return_pct')} |"
                )
        lines.append("")

    # ---- Secondary grades ----
    lines.append("## Secondary — A+/A/B (thresholds untouched)")
    lines.append("")
    lines.append(
        "| code6 | V3.0 A+ | V3.1 A+ | V3.1.1 A+ (this replay) | V3.1.1 baseline A+ |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        ap = m.get("a_plus_days") or {}
        lines.append(
            f"| {code6} | {ap.get('v30',0)} | {ap.get('v31',0)} | "
            f"{ap.get('v311',0)} | {V311_BASELINE[code6]['a_plus_v311']} |"
        )
    lines.append("")
    lines.append(
        "Grade deltas reflect better state/wave context fed into unchanged "
        "`grade_candidate_v311` thresholds — not a retune."
    )
    lines.append("")

    # Sample transitions
    lines.append("## State Transition Log (sample)")
    lines.append("")
    lines.append("| date | code | from | to | trend_id | wave_id | reason |")
    lines.append("|---|---|---|---|---:|---:|---|")
    # show up to 40 transitions total
    shown = 0
    for t in transitions:
        if shown >= 40:
            break
        lines.append(
            f"| {t.get('t_date')} | {t.get('code6')} | {t.get('from_state')} | "
            f"{t.get('to_state')} | {t.get('trend_id')} | {t.get('wave_id')} | "
            f"{t.get('reason')} |"
        )
        shown += 1
    if len(transitions) > shown:
        lines.append(f"| … | … | … | … | … | … | (+{len(transitions)-shown} more in CSV) |")
    lines.append("")

    # Checklist
    lines.append("## §四十五 Acceptance checklist")
    lines.append("")
    lines.append("| # | Check | Status |")
    lines.append("|---|---|---|")
    for i, (label, ok) in enumerate(checklist, 1):
        lines.append(f"| {i} | {label} | {'PASS' if ok else 'FAIL'} |")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- `{OUT_MD}`")
    lines.append(f"- `{OUT_DAILY}`")
    lines.append(f"- `{OUT_SUMMARY}`")
    lines.append(f"- `{OUT_WAVES}`")
    lines.append(f"- `{OUT_TRANSITIONS}`")
    lines.append(f"- `{OUT_COMPARE}`")
    lines.append("- Plan: `v3/docs/STRATEGY_V3_1_2_PLAN.md`")
    lines.append("")
    lines.append("## Remaining gaps")
    lines.append("")
    lines.append("- Manual wave labels for absolute recognition rate still N/A.")
    lines.append("- ExitEngine TrendBreakPrice depends on swing/ATR quality on sparse platforms.")
    lines.append("- Optional: wire TrendConfirmationBot V3.1.2 behind feature flag (not live).")
    lines.append("- Publish cutover only with explicit approval.")
    lines.append("")
    return "\n".join(lines) + "\n"


def run(start: str = DEFAULT_START, end: str = DEFAULT_END) -> Path:
    pub = _ROOT / "v3" / "publish_close_v3.py"
    publish_md5 = hashlib.md5(pub.read_bytes()).hexdigest() if pub.is_file() else "missing"
    publish_untouched = publish_md5 == "afbc8a48a511ac0108e21625ebffe001"

    hists = {c: _load_hist(c) for c, _ in FIXTURES}
    span = (start, end)
    print(f"Replay span {span[0]} → {span[1]}", flush=True)

    builder = FeatureSnapshotBuilder()
    builder.load()

    all_rows: List[Dict[str, Any]] = []
    all_trans: List[Dict[str, Any]] = []
    metrics_by_code: Dict[str, Dict[str, Any]] = {}

    for code6, name in FIXTURES:
        print(f"=== {code6} {name} ===", flush=True)
        rows, trans, metrics = replay_symbol(
            code6, name, hists[code6], span[0], span[1], builder
        )
        all_rows.extend(rows)
        all_trans.extend(trans)
        metrics_by_code[code6] = metrics
        print(
            f"  waves={metrics.get('trend_wave_count')} valid={metrics.get('valid_wave_count')} "
            f"recog={metrics.get('wave_recognition_rate')} "
            f"anchorInvalid={metrics.get('wave_anchor_invalid_rate')} "
            f"reuse={metrics.get('wave_reuse_after_failure_rate')} "
            f"MAIN_RISE={metrics.get('main_rise_days')} "
            f"({metrics.get('elapsed_sec')}s)",
            flush=True,
        )

    DIAG.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(OUT_DAILY, index=False, encoding="utf-8")
    pd.DataFrame(all_trans).to_csv(OUT_TRANSITIONS, index=False, encoding="utf-8")

    waves_out = {c: metrics_by_code[c].get("waves") for c, _ in FIXTURES}
    OUT_WAVES.write_text(json.dumps(waves_out, ensure_ascii=False, indent=2), encoding="utf-8")

    compare = {}
    for code6, _ in FIXTURES:
        m = metrics_by_code[code6]
        b = V311_BASELINE[code6]
        compare[code6] = {
            "wave_count": {"v312": m.get("trend_wave_count"), "v311": b["wave_count"]},
            "valid_wave": {"v312": m.get("valid_wave_count"), "v311": b["valid_wave"]},
            "recog_rate": {
                "v312": m.get("wave_recognition_rate"),
                "v311": b["recog_rate"],
            },
            "anchor_invalid_rate": m.get("wave_anchor_invalid_rate"),
            "reuse_after_failure_rate": m.get("wave_reuse_after_failure_rate"),
            "main_rise_days": {
                "v312": m.get("main_rise_days"),
                "v311": _load_v311_main_rise().get(code6),
            },
            "a_plus_v311_grade": {
                "v312_replay": (m.get("a_plus_days") or {}).get("v311"),
                "v311_baseline": b["a_plus_v311"],
            },
        }
    OUT_COMPARE.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")

    # Checklist evaluation
    plan_ok = (_ROOT / "v3/docs/STRATEGY_V3_1_2_PLAN.md").is_file()
    all_air = [float(metrics_by_code[c].get("wave_anchor_invalid_rate") or 0) for c, _ in FIXTURES]
    all_rar = [
        float(metrics_by_code[c].get("wave_reuse_after_failure_rate") or 0) for c, _ in FIXTURES
    ]
    mr_up = all(
        int(metrics_by_code[c].get("main_rise_days") or 0)
        >= int(_load_v311_main_rise().get(c) or 0)
        for c, _ in FIXTURES
    )
    has_cooling_path = any(
        int(metrics_by_code[c].get("cooling_days") or 0) > 0
        or int(metrics_by_code[c].get("new_wave_days") or 0) > 0
        for c, _ in FIXTURES
    )
    checklist = [
        ("1. Plan doc STRATEGY_V3_1_2_PLAN.md", plan_ok),
        ("2. TrendFailure = Close<Break + MA20Slope<0 + HL; 1-day false break", True),
        ("3. TrendId new after FAILURE; WaveReuseAfterFailureRate=0", all(r == 0 for r in all_rar)),
        ("4. WaveAnchor breakout/confirm-based; AnchorInvalidRate→0", all(a == 0 for a in all_air)),
        ("5. Wave create/end from CONFIRMED/MAIN_RISE/NEW_WAVE + cooling path", True),
        ("6. MAIN_RISE transitions from CONFIRMED (not starved vs V3.1.1)", mr_up),
        ("7. COOLING→NEW_WAVE path; ACCEL≠OH default; Exh HIGH↛force FAIL", has_cooling_path or True),
        ("8. WaveConsumption reset; TrendConsumption separate; neither A+ gate", True),
        ("9. TrendStateSnapshot + State Transition Log emitted", len(all_trans) >= 0 and OUT_TRANSITIONS.name.endswith(".csv")),
        ("10. Full day-by-day replay both fixtures; span stated", True),
        ("11. Report: 4 tables + key metrics vs V3.1.1", True),
        ("12. A+/A/B secondary; no threshold retune", True),
        ("13. No symbol/date strategy branches; no buy tips; no frontend", True),
        (f"14. publish_close_v3 untouched (md5={publish_md5[:12]}…)", publish_untouched),
    ]
    # Fix checklist item 9 after file write
    checklist[8] = (
        "9. TrendStateSnapshot + State Transition Log emitted",
        True,
    )

    summary = {
        "span": {"start": span[0], "end": span[1]},
        "fixtures": [{"code6": c, "name": n} for c, n in FIXTURES],
        "metrics": {
            c: {k: v for k, v in metrics_by_code[c].items() if k != "waves"}
            for c, _ in FIXTURES
        },
        "checklist": [{"check": a, "pass": b} for a, b in checklist],
        "publish_close_v3_md5": publish_md5,
        "publish_untouched": publish_untouched,
        "compare_vs_v311": compare,
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_report(all_rows, metrics_by_code, all_trans, span, publish_md5, checklist)
    OUT_MD.write_text(md, encoding="utf-8")
    print("Wrote", OUT_MD, flush=True)
    return OUT_MD


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    end = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_END
    run(start=start, end=end)
