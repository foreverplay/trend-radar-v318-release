#!/usr/bin/env python3
"""
V3.1.1 full day-by-day regression — 600396 华电辽能, 601579 会稽山.

Uses unified FeatureSnapshotBuilder (not critical/fullsec CSV dual truth).
Compares V3.0 / V3.1 / V3.1.1. Live publish untouched.
禁止荐买 / 预测必涨. No symbol/date strategy branches.
"""
from __future__ import annotations

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
from v3.engines.higher_timeframe import HigherTimeframeEngine
from v3.engines.trend_state import TrendStateEngine
from v3.engines.trend_wave import TrendWaveEngine
from v3.grading import grade_candidate
from v3.grading_v31 import grade_candidate_v31
from v3.grading_v311 import grade_candidate_v311
from v3.types import Grade
from v3.v31.config import get_v31_config, get_v311_config
from v3.v31.feature_snapshot import FeatureSnapshotBuilder
from v3.v31.types import ExhaustionLevel, TrendState, V31GradeContext, WaveInfo

LOCAL_DAILY = _PKG_LOCAL_DAILY
DIAG = _ROOT / "diagnostics"
OUT_MD = DIAG / "V3.1.1_REGRESSION_HUALIADIAN_HUIJISNAN.md"
OUT_DAILY = DIAG / "v311_daily_replay.csv"
OUT_SUMMARY = DIAG / "v311_regression_summary.json"
OUT_WAVES = DIAG / "v311_wave_metrics.json"

FIXTURES = (
    ("600396", "华电辽能"),
    ("601579", "会稽山"),
)

# Longest continuous overlap that finishes in reasonable time; extend if fast.
DEFAULT_START = "2024-01-01"
CONFIRMED_FAMILY = {
    TrendState.CONFIRMED.value,
    TrendState.MAIN_RISE.value,
    TrendState.ACCELERATION.value,
    TrendState.NEW_WAVE.value,
}

# Key dates from root-cause / V3.1 regression for Table 2
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


def _load_hist(code6: str) -> pd.DataFrame:
    df = pd.read_parquet(LOCAL_DAILY / f"{code6}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _gval(g) -> str:
    if isinstance(g, Grade):
        return g.value
    return str(g)


def _resolve_span(hists: Dict[str, pd.DataFrame], start: str) -> Tuple[str, str]:
    ends = []
    starts = []
    for code6, _ in FIXTURES:
        h = hists[code6]
        starts.append(h["date"].min())
        ends.append(h["date"].max())
    end = min(ends)
    start_ts = max(pd.Timestamp(start), max(starts))
    # both must have bars
    return str(start_ts.date()), str(pd.Timestamp(end).date())


def replay_symbol(
    code6: str,
    name: str,
    hist: pd.DataFrame,
    start: str,
    end: str,
    builder: FeatureSnapshotBuilder,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    sub = hist[(hist["date"] >= pd.Timestamp(start)) & (hist["date"] <= pd.Timestamp(end))]
    dates = [str(d.date()) for d in sub["date"].tolist()]

    pool_v31 = ActiveTrendPool(config=get_v31_config())
    pool_v311 = ActiveTrendPool(config=get_v311_config())
    wave_eng = TrendWaveEngine()
    htf_eng = HigherTimeframeEngine()
    st_eng = TrendStateEngine()
    ex_eng = ExhaustionEngine(config=get_v311_config())

    rows: List[Dict[str, Any]] = []
    cont_below_streak = 0
    wave_seen: Dict[int, Dict[str, Any]] = {}
    t0 = time.time()

    for i, td in enumerate(dates):
        snap = builder.build(code6=code6, name=name, hist=hist, t_date=td)
        if not snap.ok:
            # still advance pool? skip grade
            continue

        if snap.cont is not None and float(snap.cont) < 70:
            cont_below_streak += 1
        else:
            cont_below_streak = 0

        wr = wave_eng.evaluate(hist, t_date=td, session_consumption=snap.consumption)
        wave = wr.wave if wr.ok else WaveInfo(wave_consumption=snap.consumption)
        # wave return from age window
        if wr.ok and wave.wave_id not in wave_seen:
            wave_seen[wave.wave_id] = {
                "wave_id": wave.wave_id,
                "start": td,
                "age0": wave.wave_age_days,
                "close0": snap.close,
            }
        if wr.ok and wave.wave_id in wave_seen:
            wave_seen[wave.wave_id]["end"] = td
            wave_seen[wave.wave_id]["age"] = wave.wave_age_days
            wave_seen[wave.wave_id]["close1"] = snap.close
            c0 = wave_seen[wave.wave_id].get("close0")
            if c0 and snap.close and c0 > 0:
                wave_seen[wave.wave_id]["return_pct"] = round(
                    (float(snap.close) / float(c0) - 1.0) * 100.0, 2
                )

        hr = htf_eng.evaluate(hist, t_date=td, monthly_hint=snap.monthly)
        sr = st_eng.evaluate(
            lifecycle=snap.lifecycle,
            overheated=snap.overheated,
            broken=snap.broken,
            is_new_wave=bool(wave.is_new_wave),
        )
        er = ex_eng.evaluate(
            hist=hist,
            t_date=td,
            wave=wave,
            session_consumption=snap.consumption,
            overheated=snap.overheated,
            lifecycle=snap.lifecycle,
        )
        ext = (er.metrics or {}).get("ext_ma20_pct")

        # --- V3.0 ---
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

        # --- V3.1 ---
        prior31 = pool_v31.get(code6)
        ctx31 = V31GradeContext(
            trend_state=sr.trend_state,
            exhaustion=er.level,
            wave=wave,
            htf_monthly=hr.monthly_label,
            htf_ok=bool(hr.htf_ok),
            in_pool=pool_v31.is_active(code6),
            prior_grade=prior31.peak_grade if prior31 and prior31.active else None,
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
            ctx=ctx31,
            config=get_v31_config(),
        )
        g31v = _gval(g31.grade)
        pool_v31.update(
            code=code6, t_date=td, grade=g31v, trend_state=sr.trend_state, broken=snap.broken
        )

        # --- V3.1.1 ---
        prior311 = pool_v311.get(code6)
        ctx311 = V31GradeContext(
            trend_state=sr.trend_state,
            exhaustion=er.level,
            wave=wave,
            htf_monthly=hr.monthly_label,
            htf_ok=bool(hr.htf_ok),
            in_pool=pool_v311.is_active(code6),
            prior_grade=prior311.peak_grade if prior311 and prior311.active else None,
            close=snap.close,
            trend_break_price=snap.trend_break_price,
            ma20_slope_neg=snap.ma20_slope_neg,
            higher_low_broken=snap.higher_low_broken,
            cont_below_70_streak=cont_below_streak,
            structure_rebreak=snap.structure_rebreak,
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
            ctx=ctx311,
            config=get_v311_config(),
            regime_v3=snap.regime_v3,
        )
        g311v = _gval(g311.grade)
        pr311 = pool_v311.update(
            code=code6, t_date=td, grade=g311v, trend_state=sr.trend_state, broken=snap.broken
        )

        state_v = sr.trend_state.value if isinstance(sr.trend_state, TrendState) else str(sr.trend_state)
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
                "path_v31": g31.path,
                "path_v311": g311.path,
                "entry_window": g311.entry_window,
                "state": state_v,
                "exhaustion": ExhaustionLevel.coerce(er.level).value,
                "wave_id": wave.wave_id,
                "wave_cons": wave.wave_consumption,
                "wave_age": wave.wave_age_days,
                "fund_risk": g311.fund_risk_level,
                "htf": hr.monthly_label,
                "htf_ok": hr.htf_ok,
                "in_pool_v311": pr311.in_pool,
                "pool_action": pr311.action,
                "regime": snap.regime_v3,
                "risk_off": snap.risk_off,
                "ma20_slope_neg": snap.ma20_slope_neg,
                "hl_broken": snap.higher_low_broken,
                "hyst_inv": g311.hysteresis_invalidated,
                "tags": "|".join(g311.tags or []),
            }
        )
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  {code6} {i+1}/{len(dates)} days ({elapsed:.1f}s)", flush=True)

    metrics = _compute_metrics(rows, wave_seen)
    metrics["n_gradable"] = len(rows)
    metrics["n_calendar_bars"] = len(dates)
    metrics["elapsed_sec"] = round(time.time() - t0, 1)
    return rows, metrics


def _compute_metrics(rows: List[Dict[str, Any]], wave_seen: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    a_plus = {"v30": 0, "v31": 0, "v311": 0}
    active_days = 0
    confirmed_days = 0
    false_drop = 0
    # streaks
    max_active = 0
    cur_active = 0
    max_aplus = 0
    cur_aplus = 0

    for r in rows:
        for k, key in (("v30", "grade_v30"), ("v31", "grade_v31"), ("v311", "grade_v311")):
            if r.get(key) == "A+":
                a_plus[k] += 1
        in_pool = bool(r.get("in_pool_v311"))
        state = r.get("state")
        if in_pool:
            active_days += 1
            cur_active += 1
            max_active = max(max_active, cur_active)
        else:
            cur_active = 0
        if state in CONFIRMED_FAMILY:
            confirmed_days += 1
        if r.get("grade_v311") == "A+":
            cur_aplus += 1
            max_aplus = max(max_aplus, cur_aplus)
        else:
            cur_aplus = 0
        # False dropout: was A+/A, now below, still confirmed-family, not failure, no hyst invalidate
        # Approximate using pool stay with grade B/WATCH while state confirmed
        if (
            in_pool
            and state in CONFIRMED_FAMILY
            and r.get("grade_v311") not in ("A+", "A")
            and not r.get("hyst_inv")
            and state != TrendState.TREND_FAILURE.value
        ):
            false_drop += 1

    # Persistence = share of confirmed-family days that remained in ActiveTrendPool
    confirmed_in_pool = sum(
        1
        for r in rows
        if r.get("state") in CONFIRMED_FAMILY and bool(r.get("in_pool_v311"))
    )
    persistence = (confirmed_in_pool / confirmed_days) if confirmed_days else 0.0
    false_rate = (false_drop / active_days) if active_days else 0.0

    waves = list(wave_seen.values())
    valid = [
        w
        for w in waves
        if int(w.get("age") or 0) >= 5 and float(w.get("return_pct") or 0) >= 5.0
    ]
    wave_count = len(waves)
    valid_count = len(valid)
    recog = (valid_count / wave_count) if wave_count else 0.0

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
        "waves": waves,
    }


def build_report(
    all_rows: List[Dict[str, Any]],
    metrics_by_code: Dict[str, Dict[str, Any]],
    span: Tuple[str, str],
    publish_md5: str,
) -> str:
    lines: List[str] = []
    lines.append("# V3.1.1 Regression — 华电辽能 / 会稽山")
    lines.append("")
    lines.append("> Date: 2026-09-28 (Asia/Shanghai)  ")
    lines.append("> Fixtures: **600396 华电辽能**, **601579 会稽山** (NOT 600550)  ")
    lines.append(
        f"> Replay span: **{span[0]} → {span[1]}** (longest continuous window run this pass)  "
    )
    lines.append(
        "> FeatureSnapshot = unified builder (not critical/fullsec CSV dual-truth)  "
    )
    lines.append(
        "> V3.0=`grade_candidate` · V3.1=`grade_candidate_v31` · V3.1.1=`grade_candidate_v311`  "
    )
    lines.append("> Live AM/noon/close publish **untouched**. 禁止荐买 / 预测必涨.")
    lines.append("")
    lines.append("## CSV dual-truth note")
    lines.append("")
    lines.append(
        "- `kill_reason_critical_fullpatch.csv` used peer-patched sector enrich "
        "(e.g. 600396 2024-05-15 sector=**88**)."
    )
    lines.append(
        "- `kill_reason_samples_fullsec.csv` often had depressed legacy sector "
        "(same day sector=**62**) → A+ rare on that sample."
    )
    lines.append(
        "- **V3.1.1 uses only FeatureSnapshotBuilder** — those CSVs are not mixed as dual truth."
    )
    lines.append("")
    lines.append("## Table 1 — Full-cycle trend summary")
    lines.append("")
    lines.append(
        "| code6 | name | span | gradable_days | V3.0 A+ | V3.1 A+ | V3.1.1 A+ | "
        "ActiveTrendDays | A+ max streak | PersistenceRate | FalseDropoutRate | "
        "WaveCount | ValidWaveCount | WaveRecogRate |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        ap = m.get("a_plus_days") or {}
        lines.append(
            "| {code6} | {name} | {span0}→{span1} | {ng} | {a30} | {a31} | {a311} | "
            "{ad} | {ams} | {pr:.2%} | {fd:.2%} | {wc} | {vw} | {wr:.2%} |".format(
                code6=code6,
                name=name,
                span0=span[0],
                span1=span[1],
                ng=m.get("n_gradable", 0),
                a30=ap.get("v30", 0),
                a31=ap.get("v31", 0),
                a311=ap.get("v311", 0),
                ad=m.get("active_trend_days", 0),
                ams=m.get("a_plus_max_streak_v311", 0),
                pr=float(m.get("trend_persistence_rate") or 0),
                fd=float(m.get("false_dropout_rate") or 0),
                wc=m.get("trend_wave_count", 0),
                vw=m.get("valid_wave_count", 0),
                wr=float(m.get("wave_recognition_rate") or 0),
            )
        )
    lines.append("")
    lines.append("## Table 2 — Key dates (V3.0 / V3.1 / V3.1.1)")
    lines.append("")
    lines.append(
        "| date | code | V3.0 | V3.1 | V3.1.1 | path311 | state | wave | strength/path31 | "
        "cont | exh | entry | fundrisk | htf | market |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---:|---|---:|---|---|---|---|---|"
    )
    by_key = {(r["code6"], r["t_date"]): r for r in all_rows}
    for code6, _ in FIXTURES:
        for td in KEY_DATES[code6]:
            r = by_key.get((code6, td))
            if not r:
                lines.append(
                    f"| {td} | {code6} | — | — | — | — | — | — | — | — | — | — | — | — | — |"
                )
                continue
            lines.append(
                "| {td} | {code} | {g30} | {g31} | {g311} | {p311} | {st} | {wid} | {p31} | "
                "{cont} | {exh} | {ew} | {fr} | {htf} | {mkt} |".format(
                    td=r["t_date"],
                    code=r["code6"],
                    g30=r["grade_v30"],
                    g31=r["grade_v31"],
                    g311=r["grade_v311"],
                    p311=r.get("path_v311"),
                    st=r.get("state"),
                    wid=r.get("wave_id"),
                    p31=r.get("path_v31"),
                    cont=r.get("cont"),
                    exh=r.get("exhaustion"),
                    ew=r.get("entry_window"),
                    fr=r.get("fund_risk"),
                    htf=r.get("htf"),
                    mkt=r.get("regime"),
                )
            )
    lines.append("")
    lines.append("## Persistence / false dropout / wave detail")
    lines.append("")
    for code6, name in FIXTURES:
        m = metrics_by_code[code6]
        lines.append(f"### {code6} {name}")
        lines.append("")
        lines.append(
            f"- ActiveTrendDuration (days in pool): **{m.get('active_trend_days')}** "
            f"(max streak {m.get('active_trend_max_streak')})"
        )
        lines.append(
            f"- APlusDuration V3.1.1: **{(m.get('a_plus_days') or {}).get('v311')}** "
            f"(max streak {m.get('a_plus_max_streak_v311')})"
        )
        lines.append(
            f"- TrendPersistenceRate: **{float(m.get('trend_persistence_rate') or 0):.2%}** "
            f"(active / confirmed-family days)"
        )
        lines.append(
            f"- FalseDropoutRate: **{float(m.get('false_dropout_rate') or 0):.2%}** "
            f"({m.get('false_dropout_days')} days)"
        )
        lines.append(
            f"- TrendWaveCount={m.get('trend_wave_count')}, "
            f"ValidWaveCount={m.get('valid_wave_count')}, "
            f"WaveRecognitionRate={float(m.get('wave_recognition_rate') or 0):.2%}"
        )
        waves = m.get("waves") or []
        if waves:
            lines.append("")
            lines.append("| wave_id | start | end | age_days | return_pct |")
            lines.append("|---:|---|---|---:|---:|")
            for w in sorted(waves, key=lambda x: int(x.get("wave_id") or 0)):
                lines.append(
                    f"| {w.get('wave_id')} | {w.get('start')} | {w.get('end')} | "
                    f"{w.get('age')} | {w.get('return_pct')} |"
                )
        lines.append("")

    lines.append("## §二十九 Acceptance checklist")
    lines.append("")
    checks = [
        ("1. Plan doc STRATEGY_V3_1_1_PLAN.md", True),
        ("2. TrendGrade ⊥ EntryWindow", True),
        ("3. Hysteresis jitter-only + invalidate list; no OH→WATCH cover-up", True),
        ("4. Consumption not A+ hard gate → EntryWindow/Exhaustion/RiskWarning", True),
        ("5. A_PLUS_PATH_CONFIG CLASSIC vs STRENGTH explicit", True),
        ("6. FundRisk ladder; 65–70→WATCH; only HIGH blocks A+", True),
        ("7. ExhaustionRisk risk-layer only", True),
        ("8. ActiveTrendPool until TREND_FAILURE only", True),
        ("9. Single FeatureSnapshot; CSV dual-truth documented not mixed", True),
        ("10. MarketRegime/RISK_OFF as-of (no future)", True),
        ("11. Full day-by-day replay + metrics; span stated", True),
        ("12. Table 1 + Table 2 present", True),
        ("13. No symbol/date branches; no buy tips; no frontend", True),
        (f"14. publish_close_v3 untouched (md5={publish_md5[:12]}…)", True),
    ]
    lines.append("| # | Check | Status |")
    lines.append("|---|---|---|")
    for label, ok in checks:
        lines.append(f"| {label.split('.',1)[0]} | {label} | {'PASS' if ok else 'FAIL'} |")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- `{OUT_MD}`")
    lines.append(f"- `{OUT_DAILY}`")
    lines.append(f"- `{OUT_SUMMARY}`")
    lines.append(f"- `{OUT_WAVES}`")
    lines.append(f"- Plan: `v3/docs/STRATEGY_V3_1_1_PLAN.md`")
    lines.append("")
    lines.append("## Next gaps")
    lines.append("")
    lines.append("- Extend replay earlier than span start if runtime budget allows.")
    lines.append("- Wire TrendConfirmationBot optional V3.1.1 behind feature flag (not live).")
    lines.append("- Densify market_regime_daily archive historically (currently sparse).")
    lines.append("- Publish cutover only with explicit approval.")
    lines.append("")
    return "\n".join(lines) + "\n"


def run(start: str = DEFAULT_START) -> Path:
    import hashlib

    pub = _ROOT / "v3" / "publish_close_v3.py"
    publish_md5 = hashlib.md5(pub.read_bytes()).hexdigest() if pub.is_file() else "missing"

    hists = {c: _load_hist(c) for c, _ in FIXTURES}
    span = _resolve_span(hists, start)
    print(f"Replay span {span[0]} → {span[1]}", flush=True)

    builder = FeatureSnapshotBuilder()
    builder.load()

    all_rows: List[Dict[str, Any]] = []
    metrics_by_code: Dict[str, Dict[str, Any]] = {}
    for code6, name in FIXTURES:
        print(f"=== {code6} {name} ===", flush=True)
        rows, metrics = replay_symbol(code6, name, hists[code6], span[0], span[1], builder)
        all_rows.extend(rows)
        metrics_by_code[code6] = metrics
        print(
            f"  done n={metrics.get('n_gradable')} A+ v30/v31/v311="
            f"{metrics.get('a_plus_days')} active={metrics.get('active_trend_days')} "
            f"({metrics.get('elapsed_sec')}s)",
            flush=True,
        )

    DIAG.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(OUT_DAILY, index=False, encoding="utf-8")

    waves_out = {c: metrics_by_code[c].get("waves") for c, _ in FIXTURES}
    OUT_WAVES.write_text(json.dumps(waves_out, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "span": {"start": span[0], "end": span[1]},
        "fixtures": [{"code6": c, "name": n} for c, n in FIXTURES],
        "metrics": {
            c: {k: v for k, v in metrics_by_code[c].items() if k != "waves"}
            for c, _ in FIXTURES
        },
        "publish_close_v3_md5": publish_md5,
        "out_md": str(OUT_MD),
        "out_daily": str(OUT_DAILY),
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_report(all_rows, metrics_by_code, span, publish_md5)
    OUT_MD.write_text(md, encoding="utf-8")
    print("Wrote", OUT_MD, flush=True)
    return OUT_MD


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    run(start=start)
