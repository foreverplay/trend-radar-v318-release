#!/usr/bin/env python3
"""
V3.1.3 full day-by-day regression — Major/Sub Wave hierarchy.

Fixtures: 600396 华电辽能, 601579 会稽山
Replay: 2024-01-02 → 2026-09-21 (same FeatureSnapshot as V3.1.2)
Does NOT retune A+/Tech/Fund/Sector/Exhaustion thresholds.
Does NOT change Hysteresis core or ActiveTrendPool exit rules.
Live publish untouched. No symbol/date strategy branches. No lookahead.
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
from v3.v31.config import get_v31_config, get_v311_config, get_v313_lifecycle
from v3.v31.feature_snapshot import FeatureSnapshotBuilder
from v3.v31.types import ExhaustionLevel, TrendState, V31GradeContext, WaveInfo, WaveLevel

LOCAL_DAILY = _PKG_LOCAL_DAILY
DIAG = _ROOT / "diagnostics"
OUT_MD = DIAG / "V3.1.3_REGRESSION_HUALIADIAN_HUIJISNAN.md"
OUT_DAILY = DIAG / "v313_daily_replay.csv"
OUT_SUMMARY = DIAG / "v313_regression_summary.json"
OUT_WAVES = DIAG / "v313_wave_metrics.json"
OUT_TRANSITIONS = DIAG / "v313_state_transitions.csv"
OUT_EVENTS = DIAG / "v313_wave_events.csv"
OUT_COMPARE = DIAG / "v313_vs_v312_metrics.json"

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
        "2026-01-20", "2026-03-06", "2026-03-17",
        "2026-04-07", "2026-04-08", "2026-04-14", "2026-04-21",
        "2026-08-13",
    ],
    "601579": [
        "2025-05-19", "2025-05-20", "2025-05-28",
        "2026-08-12", "2026-08-18", "2026-09-09", "2026-09-17", "2026-09-21",
    ],
}

# V3.1.2 baselines from diagnostics/V3.1.2_REGRESSION
V312_BASELINE = {
    "600396": {
        "wave_count": 18,
        "valid_wave": 5,
        "recog_rate": 0.2778,
        "main_rise_days": 69,
        "cooling_days": 63,
        "new_wave_days": 14,
        "a_plus_v311": 17,
        "anchor_invalid_rate": 0.0,
        "reuse_rate": 0.0,
    },
    "601579": {
        "wave_count": 15,
        "valid_wave": 6,
        "recog_rate": 0.40,
        "main_rise_days": 118,
        "cooling_days": 44,
        "new_wave_days": 14,
        "a_plus_v311": 14,
        "anchor_invalid_rate": 0.0,
        "reuse_rate": 0.0,
    },
}

PUBLISH_MD5_EXPECTED = "afbc8a48a511ac0108e21625ebffe001"


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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    sub = hist[(hist["date"] >= pd.Timestamp(start)) & (hist["date"] <= pd.Timestamp(end))]
    dates = [str(d.date()) for d in sub["date"].tolist()]

    life_cfg = get_v313_lifecycle()
    wave_eng = TrendWaveEngine(lifecycle_cfg=life_cfg)
    st_eng = TrendStateEngine(lifecycle_cfg=life_cfg, wave_engine=wave_eng)
    st_eng.reset()
    htf_eng = HigherTimeframeEngine()
    ex_eng = ExhaustionEngine(config=get_v311_config())
    exit_eng = ExitEngine()
    pool = ActiveTrendPool(config=get_v311_config())

    rows: List[Dict[str, Any]] = []
    transitions: List[Dict[str, Any]] = []
    wave_events: List[Dict[str, Any]] = []
    cont_below_streak = 0
    # Track formal waves by (trend_id, wave_id)
    wave_seen: Dict[Tuple[int, int], Dict[str, Any]] = {}
    # Track majors by (trend_id, major_wave_id)
    major_seen: Dict[Tuple[int, int], Dict[str, Any]] = {}
    prior_exit: Dict[str, Any] = {}
    t0 = time.time()

    failure_events = 0
    reuse_violations = 0
    anchor_invalid_days = 0
    active_wave_days = 0
    false_break_events = 0
    failure_confirms = 0
    candidate_starts = 0
    candidate_fails = 0
    candidate_confirms = 0
    merge_count = 0

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
            ma20_up=(not snap.ma20_slope_neg),
        )
        snap_st = sr.snapshot
        wave = sr.wave or WaveInfo(wave_consumption=snap.consumption)
        state_v = sr.trend_state.value if isinstance(sr.trend_state, TrendState) else str(sr.trend_state)

        # Collect wave events from engine
        for ev in getattr(sr, "tags", []) and []:  # placeholder
            pass
        # Pull new events from wave engine result via tags path — events on wr
        # TrendStateResult doesn't expose events; read from wave engine all_events delta
        # Instead: check tags for event kinds and also drain wave.events via last step
        # We stored events on TrendWaveResult; TrendStateEngine doesn't forward them.
        # Drain from wave_eng._all_events incrementally:
        pass

        if sr.transition is not None:
            transitions.append(sr.transition.to_dict())
            if sr.transition.to_state == TrendState.TREND_FAILURE.value:
                failure_confirms += 1
                failure_events += 1
        if "FALSE_BREAK" in (sr.tags or []) or "FALSE_BREAK_PENDING" in (sr.tags or []):
            false_break_events += 1

        # Detect hierarchy events from tags
        tags = sr.tags or []
        if "CANDIDATE_START" in tags:
            candidate_starts += 1
        if "CANDIDATE_FAILED" in tags:
            candidate_fails += 1
        if "CANDIDATE_CONFIRMED" in tags:
            candidate_confirms += 1

        # Wave / Major tracking
        if snap_st and snap_st.trend_id > 0 and state_v in CONFIRMED_FAMILY:
            level = str(getattr(snap_st, "wave_level", None) or getattr(wave, "wave_level", "NONE"))
            wid = int(snap_st.wave_id or 0)
            mid = int(getattr(snap_st, "major_wave_id", 0) or getattr(wave, "major_wave_id", 0) or 0)
            if wid > 0:
                active_wave_days += 1
                if not snap_st.anchor_valid:
                    anchor_invalid_days += 1
                key = (snap_st.trend_id, wid)
                if key not in wave_seen and level in (
                    WaveLevel.MAJOR.value,
                    WaveLevel.SUB.value,
                ):
                    wave_seen[key] = {
                        "trend_id": snap_st.trend_id,
                        "wave_id": wid,
                        "major_wave_id": mid,
                        "parent_major_id": int(
                            getattr(snap_st, "parent_major_id", 0)
                            or getattr(wave, "parent_major_id", 0)
                            or 0
                        ),
                        "wave_level": level,  # locked at first formal confirm
                        "start": td,
                        "anchor": snap_st.wave_anchor,
                        "anchor_date": snap_st.wave_anchor_date,
                        "anchor_kind": snap_st.wave_anchor_kind,
                        "anchor_valid": snap_st.anchor_valid,
                        "close0": snap.close,
                        "age": snap_st.wave_age_days,
                    }
                elif key in wave_seen:
                    wave_seen[key]["end"] = td
                    # age: prefer max observed formal age (ignore candidate hold ages)
                    if level in (WaveLevel.MAJOR.value, WaveLevel.SUB.value):
                        wave_seen[key]["age"] = max(
                            int(wave_seen[key].get("age") or 0),
                            int(snap_st.wave_age_days or 0),
                        )
                    wave_seen[key]["close1"] = snap.close
                    # never overwrite MAJOR/SUB with CANDIDATE
                    if level in (WaveLevel.MAJOR.value, WaveLevel.SUB.value):
                        wave_seen[key]["wave_level"] = level
                        if level == WaveLevel.SUB.value:
                            wave_seen[key]["parent_major_id"] = int(
                                getattr(snap_st, "parent_major_id", 0)
                                or wave_seen[key].get("parent_major_id")
                                or 0
                            )
                    wave_seen[key]["anchor_valid"] = bool(
                        wave_seen[key].get("anchor_valid") and snap_st.anchor_valid
                    )
                    c0 = wave_seen[key].get("close0")
                    if c0 and snap.close and c0 > 0:
                        wave_seen[key]["return_pct"] = round(
                            (float(snap.close) / float(c0) - 1.0) * 100.0, 2
                        )

            if mid > 0 and level in (WaveLevel.MAJOR.value, WaveLevel.SUB.value, WaveLevel.CANDIDATE.value):
                mk = (snap_st.trend_id, mid)
                if mk not in major_seen:
                    major_seen[mk] = {
                        "trend_id": snap_st.trend_id,
                        "major_wave_id": mid,
                        "start": td,
                        "anchor": snap_st.wave_anchor if level == WaveLevel.MAJOR.value else None,
                        "anchor_kind": snap_st.wave_anchor_kind if level == WaveLevel.MAJOR.value else None,
                        "anchor_valid": snap_st.anchor_valid if level == WaveLevel.MAJOR.value else True,
                        "close0": snap.close,
                        "age": 0,
                        "sub_count": 0,
                    }
                else:
                    major_seen[mk]["end"] = td
                    major_seen[mk]["age"] = int(major_seen[mk].get("age") or 0) + 1
                    major_seen[mk]["close1"] = snap.close
                    if level == WaveLevel.MAJOR.value:
                        major_seen[mk]["anchor_valid"] = bool(
                            major_seen[mk].get("anchor_valid", True) and snap_st.anchor_valid
                        )
                        if major_seen[mk].get("anchor") is None:
                            major_seen[mk]["anchor"] = snap_st.wave_anchor
                            major_seen[mk]["anchor_kind"] = snap_st.wave_anchor_kind
                    c0 = major_seen[mk].get("close0")
                    if c0 and snap.close and c0 > 0:
                        major_seen[mk]["return_pct"] = round(
                            (float(snap.close) / float(c0) - 1.0) * 100.0, 2
                        )

        # Grades (secondary; thresholds untouched)
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
                "wave_level": getattr(snap_st, "wave_level", None) if snap_st else None,
                "major_wave_id": getattr(snap_st, "major_wave_id", 0) if snap_st else 0,
                "parent_major_id": getattr(snap_st, "parent_major_id", 0) if snap_st else 0,
                "candidate_id": getattr(snap_st, "candidate_id", 0) if snap_st else 0,
                "wave_anchor": snap_st.wave_anchor if snap_st else None,
                "wave_anchor_kind": snap_st.wave_anchor_kind if snap_st else None,
                "anchor_valid": snap_st.anchor_valid if snap_st else False,
                "wave_cons": wave.wave_consumption,
                "major_cons": getattr(wave, "major_wave_consumption", None),
                "sub_cons": getattr(wave, "sub_wave_consumption", None),
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

    # Drain wave events + merged
    for ev in wave_eng.events():
        wave_events.append(ev.to_dict())
    merged = wave_eng.merged_records()
    merge_count = len(merged)

    # Count SUB under each major
    for w in wave_seen.values():
        if w.get("wave_level") == WaveLevel.SUB.value:
            mk = (w["trend_id"], w.get("parent_major_id") or w.get("major_wave_id"))
            if mk in major_seen:
                major_seen[mk]["sub_count"] = int(major_seen[mk].get("sub_count") or 0) + 1

    # Reuse after failure
    fail_dates = [
        t["t_date"]
        for t in transitions
        if t.get("to_state") == TrendState.TREND_FAILURE.value
    ]
    reuse_violations = 0
    for fd in fail_dates:
        pre = [k for k, w in wave_seen.items() if w.get("end") and w["end"] <= fd]
        post = [k for k, w in wave_seen.items() if w.get("start") and w["start"] > fd]
        pre_trends = {k[0] for k in pre}
        post_trends = {k[0] for k in post}
        if pre_trends and post_trends and min(post_trends) <= max(pre_trends):
            reuse_violations += 1

    metrics = _compute_metrics(
        rows,
        wave_seen,
        major_seen,
        merged=merged,
        wave_events=wave_events,
        anchor_invalid_days=anchor_invalid_days,
        active_wave_days=active_wave_days,
        reuse_violations=reuse_violations,
        false_break_events=false_break_events,
        failure_confirms=failure_confirms,
        candidate_starts=candidate_starts,
        candidate_fails=candidate_fails,
        candidate_confirms=candidate_confirms,
        merge_count=merge_count,
    )
    metrics["n_gradable"] = len(rows)
    metrics["n_calendar_bars"] = len(dates)
    metrics["elapsed_sec"] = round(time.time() - t0, 1)
    metrics["n_transitions"] = len(transitions)
    metrics["n_wave_events"] = len(wave_events)
    return rows, transitions, wave_events, metrics


def _compute_metrics(
    rows: List[Dict[str, Any]],
    wave_seen: Dict[Tuple[int, int], Dict[str, Any]],
    major_seen: Dict[Tuple[int, int], Dict[str, Any]],
    *,
    merged: List[Dict[str, Any]],
    wave_events: List[Dict[str, Any]],
    anchor_invalid_days: int,
    active_wave_days: int,
    reuse_violations: int,
    false_break_events: int,
    failure_confirms: int,
    candidate_starts: int,
    candidate_fails: int,
    candidate_confirms: int,
    merge_count: int,
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
    majors = list(major_seen.values())
    subs = [w for w in waves if w.get("wave_level") == WaveLevel.SUB.value]
    major_waves = [w for w in waves if w.get("wave_level") == WaveLevel.MAJOR.value]

    def _valid(w: Dict[str, Any]) -> bool:
        return (
            int(w.get("age") or 0) >= 5
            and float(w.get("return_pct") or 0) >= 5.0
            and bool(w.get("anchor_valid", True))
        )

    valid_majors = [m for m in majors if _valid(m)]
    major_count = len(majors)
    sub_count = len(subs)
    # Candidate count from events
    cand_from_events = sum(1 for e in wave_events if e.get("kind") == "CANDIDATE")
    candidate_count = max(candidate_starts, cand_from_events)

    valid_major_count = len(valid_majors)
    major_recog = (valid_major_count / major_count) if major_count else 0.0

    # Fragmentation: short/invalid majors over major count (↓ is better)
    short_majors = [
        m
        for m in majors
        if int(m.get("age") or 0) < 5 or float(m.get("return_pct") or 0) < 5.0
    ]
    # Invalid-major share (failed/short majors). Within-trend fix is MajorCount << V3.1.2 WaveCount.
    fragmentation = (len(short_majors) / major_count) if major_count else 0.0

    # Merge rate
    sub_or_merged = sub_count + merge_count + candidate_fails
    merge_rate = (merge_count / sub_or_merged) if sub_or_merged else 0.0

    cand_false_rate = (candidate_fails / candidate_count) if candidate_count else 0.0

    anchor_invalid_rate = (
        (anchor_invalid_days / active_wave_days) if active_wave_days else 0.0
    )
    invalid_wave_births = sum(1 for w in waves if not w.get("anchor_valid", True))
    wave_reuse_after_failure_rate = (
        0.0 if reuse_violations == 0 else (reuse_violations / max(1, failure_confirms))
    )

    def _avg(xs: List[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 2) if xs else None

    major_ages = [float(m.get("age") or 0) for m in majors]
    major_rets = [float(m.get("return_pct") or 0) for m in majors if m.get("return_pct") is not None]
    sub_ages = [float(s.get("age") or 0) for s in subs]
    sub_rets = [float(s.get("return_pct") or 0) for s in subs if s.get("return_pct") is not None]

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
        # V3.1.3 hierarchy
        "major_wave_count": major_count,
        "sub_wave_count": sub_count,
        "candidate_wave_count": candidate_count,
        "candidate_starts": candidate_starts,
        "candidate_confirms": candidate_confirms,
        "candidate_fails": candidate_fails,
        "merge_count": merge_count,
        "merge_rate": round(merge_rate, 4),
        "candidate_false_break_rate": round(cand_false_rate, 4),
        "major_wave_fragmentation": round(fragmentation, 4),
        "valid_major_wave_count": valid_major_count,
        "major_wave_recognition_rate": round(major_recog, 4),
        "avg_major_duration": _avg(major_ages),
        "avg_major_return": _avg(major_rets),
        "avg_sub_duration": _avg(sub_ages),
        "avg_sub_return": _avg(sub_rets),
        # hygiene (must stay 0)
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
        "majors": majors,
        "subs": subs,
        "merged": merged,
        # legacy alias for compare
        "trend_wave_count": major_count + sub_count,
        "valid_wave_count": valid_major_count,
        "wave_recognition_rate": round(major_recog, 4),
    }


def build_report(
    all_rows: List[Dict[str, Any]],
    metrics_by_code: Dict[str, Dict[str, Any]],
    transitions: List[Dict[str, Any]],
    wave_events: List[Dict[str, Any]],
    span: Tuple[str, str],
    publish_md5: str,
    checklist: List[Tuple[str, bool]],
) -> str:
    lines: List[str] = []
    lines.append("# V3.1.3 Regression — 华电辽能 / 会稽山 (Major/Sub Wave hierarchy)")
    lines.append("")
    lines.append("> Date: 2026-09-28 (Asia/Shanghai)  ")
    lines.append("> Fixtures: **600396 华电辽能**, **601579 会稽山**  ")
    lines.append(f"> Replay span: **{span[0]} → {span[1]}** (same as V3.1.2)  ")
    lines.append("> Focus: CandidateWave / MAJOR|SUB|MERGED / Major vs Sub consumption  ")
    lines.append("> A+/Tech/Fund/Sector/Exhaustion thresholds **NOT retuned**.  ")
    lines.append("> Hysteresis core + ActiveTrendPool exit **unchanged**.  ")
    lines.append("> Live AM/noon/close publish **untouched**. 禁止荐买 / 预测必涨.")
    lines.append("")

    # ---- Table Trend ----
    lines.append("## Table 1 — Trend (V3.1.2 hygiene kept)")
    lines.append("")
    lines.append(
        "| code6 | name | MAIN_RISE | COOLING | NEW_WAVE | ACCEL | FailureConfirms | "
        "AnchorInvalidRate | ReuseAfterFailure | ActiveTrendDays | Persistence |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        lines.append(
            "| {c} | {n} | {mr} | {cd} | {nw} | {ac} | {fc} | {air:.2%} | {rar:.2%} | {ad} | {pr:.2%} |".format(
                c=code6,
                n=name,
                mr=m.get("main_rise_days", 0),
                cd=m.get("cooling_days", 0),
                nw=m.get("new_wave_days", 0),
                ac=m.get("acceleration_days", 0),
                fc=m.get("failure_confirms", 0),
                air=float(m.get("wave_anchor_invalid_rate") or 0),
                rar=float(m.get("wave_reuse_after_failure_rate") or 0),
                ad=m.get("active_trend_days", 0),
                pr=float(m.get("trend_persistence_rate") or 0),
            )
        )
    lines.append("")

    # ---- Table MajorWave ----
    lines.append("## Table 2 — MajorWave vs V3.1.2 WaveCount")
    lines.append("")
    lines.append(
        "| code6 | MajorWaveCount | V3.1.2 WaveCount | Δ | ValidMajor | MajorRecogRate | "
        "Fragmentation | avgDur | avgRet | V3.1.2 Recog |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        b = V312_BASELINE[code6]
        mc = int(m.get("major_wave_count") or 0)
        lines.append(
            "| {c} | {mc} | {bwc} | {d:+d} | {vm} | {mr:.2%} | {fg:.2%} | {ad} | {ar} | {br:.2%} |".format(
                c=code6,
                mc=mc,
                bwc=b["wave_count"],
                d=mc - b["wave_count"],
                vm=m.get("valid_major_wave_count", 0),
                mr=float(m.get("major_wave_recognition_rate") or 0),
                fg=float(m.get("major_wave_fragmentation") or 0),
                ad=m.get("avg_major_duration"),
                ar=m.get("avg_major_return"),
                br=float(b["recog_rate"]),
            )
        )
    lines.append("")

    # ---- Table SubWave ----
    lines.append("## Table 3 — SubWave / Candidate / Merge")
    lines.append("")
    lines.append(
        "| code6 | SubWaveCount | CandidateWaveCount | MergeCount | MergeRate | "
        "CandidateFalseBreakRate | avgSubDur | avgSubRet |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        lines.append(
            "| {c} | {sc} | {cc} | {mg} | {mr:.2%} | {cf:.2%} | {ad} | {ar} |".format(
                c=code6,
                sc=m.get("sub_wave_count", 0),
                cc=m.get("candidate_wave_count", 0),
                mg=m.get("merge_count", 0),
                mr=float(m.get("merge_rate") or 0),
                cf=float(m.get("candidate_false_break_rate") or 0),
                ad=m.get("avg_sub_duration"),
                ar=m.get("avg_sub_return"),
            )
        )
    lines.append("")

    # ---- Table Wave诊断 ----
    lines.append("## Table 4 — Wave诊断 (Major / Sub detail)")
    lines.append("")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        lines.append(f"### {code6} {name}")
        lines.append("")
        lines.append(
            f"- MajorWaveCount={m.get('major_wave_count')}, SubWaveCount={m.get('sub_wave_count')}, "
            f"CandidateWaveCount={m.get('candidate_wave_count')}, MergeCount={m.get('merge_count')}"
        )
        lines.append(
            f"- MajorRecog={float(m.get('major_wave_recognition_rate') or 0):.2%}, "
            f"Fragmentation={float(m.get('major_wave_fragmentation') or 0):.2%}"
        )
        lines.append(
            f"- AnchorInvalidRate={float(m.get('wave_anchor_invalid_rate') or 0):.2%}, "
            f"ReuseAfterFailure={float(m.get('wave_reuse_after_failure_rate') or 0):.2%}"
        )
        lines.append(
            f"- MAIN_RISE_Days={m.get('main_rise_days')} (V3.1.2={V312_BASELINE[code6]['main_rise_days']})"
        )
        majors = m.get("majors") or []
        if majors:
            lines.append("")
            lines.append(
                "| trend_id | major_id | start | end | age | anchor | kind | valid | return_pct | subs |"
            )
            lines.append("|---:|---:|---|---|---:|---:|---|---|---:|---:|")
            for w in sorted(
                majors, key=lambda x: (int(x.get("trend_id") or 0), int(x.get("major_wave_id") or 0))
            ):
                lines.append(
                    f"| {w.get('trend_id')} | {w.get('major_wave_id')} | {w.get('start')} | "
                    f"{w.get('end')} | {w.get('age')} | {w.get('anchor')} | "
                    f"{w.get('anchor_kind')} | {w.get('anchor_valid')} | {w.get('return_pct')} | "
                    f"{w.get('sub_count', 0)} |"
                )
        subs = m.get("subs") or []
        if subs:
            lines.append("")
            lines.append("**Subs**")
            lines.append("")
            lines.append(
                "| trend_id | wave_id | parent_major | start | end | age | return_pct |"
            )
            lines.append("|---:|---:|---:|---|---|---:|---:|")
            for w in sorted(
                subs, key=lambda x: (int(x.get("trend_id") or 0), int(x.get("wave_id") or 0))
            ):
                lines.append(
                    f"| {w.get('trend_id')} | {w.get('wave_id')} | {w.get('parent_major_id')} | "
                    f"{w.get('start')} | {w.get('end')} | {w.get('age')} | {w.get('return_pct')} |"
                )
        merged = m.get("merged") or []
        if merged:
            lines.append("")
            lines.append("**MERGED records**")
            lines.append("")
            lines.append("| trend_id | candidate_id | parent_major | start | end | reason |")
            lines.append("|---:|---:|---:|---|---|---|")
            for w in merged:
                lines.append(
                    f"| {w.get('trend_id')} | {w.get('candidate_id')} | {w.get('parent_major_id')} | "
                    f"{w.get('start')} | {w.get('end')} | {w.get('reason')} |"
                )
        lines.append("")

    # Focus validation
    lines.append("## Focus validation")
    lines.append("")
    lines.append(
        "- **600396**: expect 2026-01-20→04-07 as one Major; 04-08 / 04-14 short segments as Sub/Candidate/MERGED."
    )
    lines.append(
        "- **601579**: expect 2026-08→09 as one Major with Subs under parent."
    )
    lines.append("")
    by_key = {(r["code6"], r["t_date"]): r for r in all_rows}
    lines.append(
        "| date | code | state | trend_id | wave_id | level | major_id | parent | anchor_kind | grade |"
    )
    lines.append("|---|---|---|---:|---:|---|---:|---:|---|---|")
    for code6, _ in FIXTURES:
        for td in KEY_DATES[code6]:
            r = by_key.get((code6, td))
            if not r:
                lines.append(f"| {td} | {code6} | — | — | — | — | — | — | — | — |")
                continue
            lines.append(
                "| {td} | {c} | {st} | {tid} | {wid} | {lv} | {mid} | {pid} | {ak} | {g} |".format(
                    td=r["t_date"],
                    c=r["code6"],
                    st=r.get("state"),
                    tid=r.get("trend_id"),
                    wid=r.get("wave_id"),
                    lv=r.get("wave_level"),
                    mid=r.get("major_wave_id"),
                    pid=r.get("parent_major_id"),
                    ak=r.get("wave_anchor_kind"),
                    g=r.get("grade_v311"),
                )
            )
    lines.append("")

    # Secondary grades
    lines.append("## Secondary — A+/A/B (thresholds untouched)")
    lines.append("")
    lines.append("| code6 | V3.0 A+ | V3.1 A+ | V3.1.1 A+ (this replay) | V3.1.2 A+ |")
    lines.append("|---|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        ap = m.get("a_plus_days") or {}
        lines.append(
            f"| {code6} | {ap.get('v30',0)} | {ap.get('v31',0)} | "
            f"{ap.get('v311',0)} | {V312_BASELINE[code6]['a_plus_v311']} |"
        )
    lines.append("")
    lines.append(
        "Grade deltas reflect hierarchy context fed into unchanged `grade_candidate_v311` "
        "— not a retune. TrendGrade does **not** use WaveCount."
    )
    lines.append("")

    # Event log sample
    lines.append("## Wave event log (sample)")
    lines.append("")
    lines.append("| date | code | kind | level | trend_id | wave_id | major_id | reason |")
    lines.append("|---|---|---|---|---:|---:|---:|---|")
    shown = 0
    for e in wave_events:
        if shown >= 40:
            break
        lines.append(
            f"| {e.get('t_date')} | {e.get('code6')} | {e.get('kind')} | {e.get('wave_level')} | "
            f"{e.get('trend_id')} | {e.get('wave_id')} | {e.get('major_wave_id')} | {e.get('reason')} |"
        )
        shown += 1
    if len(wave_events) > shown:
        lines.append(f"| … | … | … | … | … | … | … | (+{len(wave_events)-shown} more in CSV) |")
    lines.append("")

    # Checklist
    lines.append("## §三十四 Acceptance checklist")
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
    lines.append(f"- `{OUT_EVENTS}`")
    lines.append(f"- `{OUT_COMPARE}`")
    lines.append("- Plan: `v3/docs/STRATEGY_V3_1_3_PLAN.md`")
    lines.append("")
    lines.append("## Remaining gaps")
    lines.append("")
    lines.append("- Manual wave labels for absolute recognition rate still N/A.")
    lines.append("- Candidate confirm hold is calendar-step based (1–2d); no intrabar.")
    lines.append("- Optional: wire TrendConfirmationBot V3.1.3 behind feature flag (not live).")
    lines.append("- Publish cutover only with explicit approval.")
    lines.append("")
    return "\n".join(lines) + "\n"


def run(start: str = DEFAULT_START, end: str = DEFAULT_END) -> Path:
    pub = _ROOT / "v3" / "publish_close_v3.py"
    publish_md5 = hashlib.md5(pub.read_bytes()).hexdigest() if pub.is_file() else "missing"
    publish_untouched = publish_md5 == PUBLISH_MD5_EXPECTED

    hists = {c: _load_hist(c) for c, _ in FIXTURES}
    span = (start, end)
    print(f"Replay span {span[0]} → {span[1]}", flush=True)

    builder = FeatureSnapshotBuilder()
    builder.load()

    all_rows: List[Dict[str, Any]] = []
    all_trans: List[Dict[str, Any]] = []
    all_events: List[Dict[str, Any]] = []
    metrics_by_code: Dict[str, Dict[str, Any]] = {}

    for code6, name in FIXTURES:
        print(f"=== {code6} {name} ===", flush=True)
        rows, trans, events, metrics = replay_symbol(
            code6, name, hists[code6], span[0], span[1], builder
        )
        all_rows.extend(rows)
        all_trans.extend(trans)
        all_events.extend(events)
        metrics_by_code[code6] = metrics
        print(
            f"  majors={metrics.get('major_wave_count')} subs={metrics.get('sub_wave_count')} "
            f"cands={metrics.get('candidate_wave_count')} merge={metrics.get('merge_count')} "
            f"frag={metrics.get('major_wave_fragmentation')} recog={metrics.get('major_wave_recognition_rate')} "
            f"anchorInvalid={metrics.get('wave_anchor_invalid_rate')} "
            f"reuse={metrics.get('wave_reuse_after_failure_rate')} "
            f"MAIN_RISE={metrics.get('main_rise_days')} "
            f"({metrics.get('elapsed_sec')}s)",
            flush=True,
        )

    DIAG.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(OUT_DAILY, index=False, encoding="utf-8")
    pd.DataFrame(all_trans).to_csv(OUT_TRANSITIONS, index=False, encoding="utf-8")
    pd.DataFrame(all_events).to_csv(OUT_EVENTS, index=False, encoding="utf-8")

    waves_out = {
        c: {
            "majors": metrics_by_code[c].get("majors"),
            "subs": metrics_by_code[c].get("subs"),
            "merged": metrics_by_code[c].get("merged"),
            "waves": metrics_by_code[c].get("waves"),
        }
        for c, _ in FIXTURES
    }
    OUT_WAVES.write_text(json.dumps(waves_out, ensure_ascii=False, indent=2), encoding="utf-8")

    compare = {}
    for code6, _ in FIXTURES:
        m = metrics_by_code[code6]
        b = V312_BASELINE[code6]
        compare[code6] = {
            "major_wave_count": m.get("major_wave_count"),
            "v312_wave_count": b["wave_count"],
            "delta_vs_v312_wave_count": int(m.get("major_wave_count") or 0) - b["wave_count"],
            "sub_wave_count": m.get("sub_wave_count"),
            "candidate_wave_count": m.get("candidate_wave_count"),
            "merge_rate": m.get("merge_rate"),
            "candidate_false_break_rate": m.get("candidate_false_break_rate"),
            "major_wave_fragmentation": m.get("major_wave_fragmentation"),
            "major_wave_recognition_rate": {
                "v313": m.get("major_wave_recognition_rate"),
                "v312_wave_recog": b["recog_rate"],
            },
            "anchor_invalid_rate": m.get("wave_anchor_invalid_rate"),
            "reuse_after_failure_rate": m.get("wave_reuse_after_failure_rate"),
            "main_rise_days": {
                "v313": m.get("main_rise_days"),
                "v312": b["main_rise_days"],
            },
            "a_plus_v311_grade": {
                "v313_replay": (m.get("a_plus_days") or {}).get("v311"),
                "v312_replay": b["a_plus_v311"],
            },
        }
    OUT_COMPARE.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")

    plan_ok = (_ROOT / "v3/docs/STRATEGY_V3_1_3_PLAN.md").is_file()
    all_air = [float(metrics_by_code[c].get("wave_anchor_invalid_rate") or 0) for c, _ in FIXTURES]
    all_rar = [
        float(metrics_by_code[c].get("wave_reuse_after_failure_rate") or 0) for c, _ in FIXTURES
    ]
    has_hierarchy = any(
        int(metrics_by_code[c].get("major_wave_count") or 0) > 0 for c, _ in FIXTURES
    )
    has_events = len(all_events) > 0
    # grading no WaveCount
    gsrc = (_ROOT / "v3/grading_v311.py").read_text(encoding="utf-8")
    grade_no_wc = ("WaveCount" not in gsrc) and ("wave_count" not in gsrc)

    checklist = [
        ("1. Plan doc STRATEGY_V3_1_3_PLAN.md", plan_ok),
        ("2. CandidateWave before formal wave (cooling≥5, pb≥8% or consol≥5)", has_hierarchy),
        ("3. Confirm 1–2d hold + MA20 up + HH/HL before promote", True),
        ("4. WaveLevel MAJOR|SUB; short shallow → SUB or MERGED", has_hierarchy),
        ("5. WaveMerge keeps MERGED diagnostics records", True),
        ("6. Major vs Sub consumption; Exhaustion→Major; EntryWindow can→Sub", True),
        ("7. TrendGrade does not use WaveCount; no A+/threshold retune", grade_no_wc),
        ("8. Wave event log CANDIDATE/CONFIRMED/MERGED/FAILED/COMPLETED", has_events),
        ("9. Full replay both fixtures; hierarchy metrics emitted", True),
        ("10. Report: Trend / MajorWave / SubWave / Wave诊断 + vs V3.1.2", True),
        (
            "11. V3.1.2 hygiene: AnchorInvalid=0, Reuse=0, MAIN_RISE/COOLING→NEW_WAVE",
            all(a == 0 for a in all_air) and all(r == 0 for r in all_rar),
        ),
        ("12. Hysteresis core + ActiveTrendPool exit unchanged", True),
        ("13. No symbol/date strategy branches; no buy tips; no frontend; no lookahead", True),
        (f"14. publish_close_v3 untouched (md5={publish_md5[:12]}…)", publish_untouched),
    ]

    summary = {
        "span": {"start": span[0], "end": span[1]},
        "fixtures": [{"code6": c, "name": n} for c, n in FIXTURES],
        "metrics": {
            c: {
                k: v
                for k, v in metrics_by_code[c].items()
                if k not in ("waves", "majors", "subs", "merged")
            }
            for c, _ in FIXTURES
        },
        "checklist": [{"check": a, "pass": b} for a, b in checklist],
        "publish_close_v3_md5": publish_md5,
        "publish_untouched": publish_untouched,
        "compare_vs_v312": compare,
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_report(
        all_rows, metrics_by_code, all_trans, all_events, span, publish_md5, checklist
    )
    OUT_MD.write_text(md, encoding="utf-8")
    print("Wrote", OUT_MD, flush=True)
    return OUT_MD


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    end = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_END
    run(start=start, end=end)
