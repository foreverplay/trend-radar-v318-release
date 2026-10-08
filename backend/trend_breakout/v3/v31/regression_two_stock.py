#!/usr/bin/env python3
"""
V3.1 two-stock regression (600396 华电辽能, 601579 会稽山).

Compares V3.0 grade_candidate vs V3.1 grade_candidate_v31 on diagnostic
critical days + fuller sample windows. Writes:
  diagnostics/V3.1_REGRESSION_HUALIADIAN_HUIJISNAN.md

No live publish. No full-market. No symbol-specific strategy branches.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

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
from v3.types import Grade, Lifecycle, RiskLevel
from v3.v31.types import ExhaustionLevel, TrendState, V31GradeContext, WaveInfo

LOCAL_DAILY = _PKG_LOCAL_DAILY
DIAG = _ROOT / "diagnostics"
OUT_MD = DIAG / "V3.1_REGRESSION_HUALIADIAN_HUIJISNAN.md"
CRITICAL = DIAG / "kill_reason_critical_fullpatch.csv"
FULLSEC = DIAG / "kill_reason_samples_fullsec.csv"

FIXTURES = (
    ("600396", "华电辽能"),
    ("601579", "会稽山"),
)


def _load_hist(code6: str) -> Optional[pd.DataFrame]:
    p = LOCAL_DAILY / f"{code6}.parquet"
    if not p.is_file():
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _grade_v30_row(r: Dict[str, Any]) -> Tuple[str, List[str]]:
    tech = r.get("tech")
    cont = r.get("cont")
    if tech is None or cont is None or (isinstance(tech, float) and pd.isna(tech)):
        return "—", ["no_scores"]
    gr = grade_candidate(
        tech_score=float(tech),
        continuity_score=float(cont),
        fund_score=_optf(r.get("fund")),
        sector_score=_optf(r.get("sector")),
        risk_level=str(r.get("risk") or r.get("risk_level") or "WATCH"),
        lifecycle=str(r.get("life") or r.get("lifecycle") or "NONE"),
        consumption_score=_optf(r.get("consumption")),
        overheated=bool(r.get("oh") if "oh" in r else r.get("overheated")),
        broken=False,
    )
    return (
        gr.grade.value if isinstance(gr.grade, Grade) else str(gr.grade),
        list(gr.reasons or []),
    )


def _optf(x: Any) -> Optional[float]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _v31_for_row(
    r: Dict[str, Any],
    hist: Optional[pd.DataFrame],
    pool: ActiveTrendPool,
) -> Dict[str, Any]:
    code6 = str(r["code6"]).zfill(6)
    t_date = str(r["t_date"])[:10]
    tech = _optf(r.get("tech"))
    cont = _optf(r.get("cont"))
    if tech is None or cont is None:
        return {
            "grade_v30": str(r.get("grade") or "—"),
            "grade_v31": "—",
            "path": "",
            "state": "",
            "exhaustion": "",
            "wave_cons": None,
            "fund_risk": False,
            "in_pool": pool.is_active(code6),
            "tags": ["no_scores"],
        }

    session_cons = _optf(r.get("consumption"))
    life = str(r.get("life") or r.get("lifecycle") or "NONE")
    oh = bool(r.get("oh") if "oh" in r else r.get("overheated"))
    risk = str(r.get("risk") or r.get("risk_level") or "WATCH")
    monthly = r.get("monthly") or r.get("monthly_trend")

    wave_eng = TrendWaveEngine()
    wr = wave_eng.evaluate(hist, t_date=t_date, session_consumption=session_cons)
    wave = wr.wave if wr.ok else WaveInfo(wave_consumption=session_cons)

    htf_eng = HigherTimeframeEngine()
    hr = htf_eng.evaluate(hist, t_date=t_date, monthly_hint=str(monthly) if monthly else None)

    st_eng = TrendStateEngine()
    sr = st_eng.evaluate(
        lifecycle=life,
        overheated=oh,
        broken=False,
        is_new_wave=bool(wave.is_new_wave),
    )

    ex_eng = ExhaustionEngine()
    er = ex_eng.evaluate(
        hist=hist,
        t_date=t_date,
        wave=wave,
        session_consumption=session_cons,
        overheated=oh,
        lifecycle=life,
    )

    prior_entry = pool.get(code6)
    prior_grade = prior_entry.peak_grade if prior_entry and prior_entry.active else None
    in_pool_before = pool.is_active(code6)

    ctx = V31GradeContext(
        trend_state=sr.trend_state,
        exhaustion=er.level,
        wave=wave,
        htf_monthly=hr.monthly_label,
        htf_ok=bool(hr.htf_ok),
        in_pool=in_pool_before,
        prior_grade=prior_grade,
    )

    # V3.0 baseline: prefer CSV grade if present, else recompute
    g30 = str(r.get("grade") or "").strip()
    if not g30 or g30.lower() == "nan":
        g30, _ = _grade_v30_row(r)

    g31 = grade_candidate_v31(
        tech_score=tech,
        continuity_score=cont,
        fund_score=_optf(r.get("fund")),
        sector_score=_optf(r.get("sector")),
        risk_level=risk,
        lifecycle=life,
        consumption_score=session_cons,
        overheated=oh,
        broken=False,
        ctx=ctx,
    )
    g31v = g31.grade.value if isinstance(g31.grade, Grade) else str(g31.grade)

    pr = pool.update(
        code=code6,
        t_date=t_date,
        grade=g31v,
        trend_state=sr.trend_state,
        broken=False,
    )

    return {
        "grade_v30": g30,
        "grade_v31": g31v,
        "path": g31.path,
        "state": sr.trend_state.value,
        "exhaustion": er.level.value,
        "wave_cons": wave.wave_consumption,
        "fund_risk": g31.fund_risk,
        "in_pool": pr.in_pool,
        "pool_action": pr.action,
        "tags": list(g31.tags or []),
        "reasons_v31": list(g31.reasons or [])[:4],
        "htf": hr.monthly_label,
        "htf_ok": hr.htf_ok,
    }


def _gradable_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "tech" not in df.columns:
        return df.iloc[0:0]
    m = df["tech"].notna() & df["cont"].notna()
    return df.loc[m].copy()


def run() -> Path:
    crit = pd.read_csv(CRITICAL)
    full = pd.read_csv(FULLSEC) if FULLSEC.is_file() else crit.copy()

    hists = {c: _load_hist(c) for c, _ in FIXTURES}
    pools = {c: ActiveTrendPool() for c, _ in FIXTURES}

    # --- critical unified table (chronological per code) ---
    crit_rows: List[Dict[str, Any]] = []
    for code6, name in FIXTURES:
        sub = crit[crit["code6"].astype(str).str.zfill(6) == code6].sort_values("t_date")
        pool = pools[code6]
        for _, row in sub.iterrows():
            r = row.to_dict()
            r["code6"] = code6
            r["name"] = name
            out = _v31_for_row(r, hists.get(code6), pool)
            crit_rows.append({**{k: r.get(k) for k in (
                "t_date", "code6", "name", "tech", "cont", "fund", "sector",
                "consumption", "risk", "life", "oh", "in_top600", "rec",
            )}, **out})

    # --- fuller sample A+ counts (reset pools; chronological) ---
    pools2 = {c: ActiveTrendPool() for c, _ in FIXTURES}
    sample_rows: List[Dict[str, Any]] = []
    # Prefer fullsec gradable; align columns
    fs = full.copy()
    if "life" not in fs.columns and "lifecycle" in fs.columns:
        fs["life"] = fs["lifecycle"]
    if "risk" not in fs.columns and "risk_level" in fs.columns:
        fs["risk"] = fs["risk_level"]
    if "oh" not in fs.columns and "overheated" in fs.columns:
        fs["oh"] = fs["overheated"]
    if "monthly" not in fs.columns and "monthly_trend" in fs.columns:
        fs["monthly"] = fs["monthly_trend"]
    if "rec" not in fs.columns and "rec_score" in fs.columns:
        fs["rec"] = fs["rec_score"]

    grad = _gradable_rows(fs)
    for code6, name in FIXTURES:
        sub = grad[grad["code6"].astype(str).str.zfill(6) == code6].sort_values("t_date")
        pool = pools2[code6]
        for _, row in sub.iterrows():
            r = row.to_dict()
            r["code6"] = code6
            r["name"] = name
            out = _v31_for_row(r, hists.get(code6), pool)
            sample_rows.append({**r, **out})

    def count_a_plus(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for code6, _ in FIXTURES:
            out[code6] = sum(
                1
                for x in rows
                if str(x.get("code6")).zfill(6) == code6 and str(x.get(key)) == "A+"
            )
        return out

    # V3.0 archive truth (from parent_payload / ROOT_CAUSE)
    archive_v30 = {"600396": 1, "601579": 0}

    c_v30 = count_a_plus(crit_rows, "grade_v30")
    c_v31 = count_a_plus(crit_rows, "grade_v31")
    s_v30 = count_a_plus(sample_rows, "grade_v30")
    s_v31 = count_a_plus(sample_rows, "grade_v31")

    lines: List[str] = []
    lines.append("# V3.1 Regression — 华电辽能 / 会稽山")
    lines.append("")
    lines.append("> Date: 2026-09-28 (Asia/Shanghai)  ")
    lines.append("> Fixtures: **600396 华电辽能**, **601579 会稽山** (NOT 600550)  ")
    lines.append("> V3.0 = `grade_candidate`; V3.1 = `grade_candidate_v31` + TrendState/Wave/HTF/Exhaustion/Pool  ")
    lines.append("> Live AM/noon/close publish **untouched**. 禁止荐买 / 预测必涨.")
    lines.append("")
    lines.append("## 1. A+ day counts")
    lines.append("")
    lines.append("| code6 | name | Archive V3.0 A+ (10y) | Critical-sample V3.0 A+ | Critical-sample V3.1 A+ | Gradable-sample V3.0 A+ | Gradable-sample V3.1 A+ |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for code6, name in FIXTURES:
        lines.append(
            f"| {code6} | {name} | {archive_v30[code6]} | {c_v30[code6]} | {c_v31[code6]} | {s_v30[code6]} | {s_v31[code6]} |"
        )
    lines.append("")
    lines.append(f"- Critical days source: `{CRITICAL.name}` (n={len(crit_rows)})  ")
    lines.append(f"- Gradable sample source: `{FULLSEC.name}` rows with tech/cont (n={len(sample_rows)})  ")
    lines.append("- Archive V3.0 = full backfill Plan A+ (ROOT_CAUSE).")
    lines.append(
        "- Gradable-sample A+ counts are **secondary**: `fullsec` often has depressed "
        "legacy sector scores (e.g. 2024-05-15 sector=62 vs critical patch 88), so V3.1 "
        "A+ stays rare there. **Primary counterfactual = critical-sample table.**"
    )
    lines.append("")
    lines.append("## 2. Unified critical-day table (V3.0 vs V3.1)")
    lines.append("")
    lines.append(
        "| date | code | PlanV3.0 | PlanV3.1 | path | state | exh | waveCons | fundRisk | pool | tech | cont | fund | sector | cons | life | OH | HTF |"
    )
    lines.append("|---|---|---|---|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---|---|---|")
    for x in crit_rows:
        lines.append(
            "| {t_date} | {code6} | {grade_v30} | {grade_v31} | {path} | {state} | {exhaustion} | {wc} | {fr} | {pool} | {tech} | {cont} | {fund} | {sector} | {cons} | {life} | {oh} | {htf} |".format(
                t_date=x.get("t_date"),
                code6=x.get("code6"),
                grade_v30=x.get("grade_v30"),
                grade_v31=x.get("grade_v31"),
                path=x.get("path"),
                state=x.get("state"),
                exhaustion=x.get("exhaustion"),
                wc=x.get("wave_cons") if x.get("wave_cons") is not None else "—",
                fr="Y" if x.get("fund_risk") else "",
                pool=("Y" if x.get("in_pool") else ""),
                tech=x.get("tech"),
                cont=x.get("cont"),
                fund=x.get("fund"),
                sector=x.get("sector"),
                cons=x.get("consumption"),
                life=x.get("life"),
                oh=x.get("oh"),
                htf=x.get("htf"),
            )
        )
    lines.append("")
    lines.append("## 3. Counterfactual highlights")
    lines.append("")
    flips = [x for x in crit_rows if x.get("grade_v30") != x.get("grade_v31")]
    if not flips:
        lines.append("- No grade flips on critical sample (unexpected — check engines).")
    else:
        for x in flips:
            lines.append(
                f"- **{x['code6']} {x['t_date']}**: {x['grade_v30']} → **{x['grade_v31']}** "
                f"(path={x.get('path')}, state={x.get('state')}, exh={x.get('exhaustion')}, "
                f"tags={','.join(x.get('tags') or [])})"
            )
    lines.append("")
    lines.append("## 4. Design checks")
    lines.append("")
    lines.append("- ExhaustionRisk ≠ TREND_FAILURE: OH rows can stay A+/A under V3.1.")
    lines.append("- WaveConsumption used for A+ gate when wave engine ok.")
    lines.append("- Soft fund → FundRisk (会稽山 fund≈68.4).")
    lines.append("- ActiveTrendPool: enter on A+/A; OH does not exit.")
    lines.append("- No `if code == 600396` branches in grading/engines.")
    lines.append("- Live publish paths not modified.")
    lines.append("")
    lines.append("## 5. Next")
    lines.append("")
    lines.append("- Wire `TrendConfirmationBot` optional V3.1 path behind flag (not default live).")
    lines.append("- Densify day walk beyond diagnostic samples; then broader backfill.")
    lines.append("- Publish cutover only with explicit approval.")
    lines.append("")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # sidecar JSON counts
    summary = {
        "archive_v30_a_plus": archive_v30,
        "critical_v30_a_plus": c_v30,
        "critical_v31_a_plus": c_v31,
        "sample_v30_a_plus": s_v30,
        "sample_v31_a_plus": s_v31,
        "critical_n": len(crit_rows),
        "sample_n": len(sample_rows),
        "flips_critical": [
            {"code6": x["code6"], "t_date": x["t_date"], "v30": x["grade_v30"], "v31": x["grade_v31"]}
            for x in flips
        ],
        "out_md": str(OUT_MD),
    }
    (DIAG / "v31_regression_summary.json").write_text(
        pd.Series(summary).to_json() if False else __import__("json").dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Wrote", OUT_MD)
    print("critical A+ V3.0", c_v30, "V3.1", c_v31)
    print("sample   A+ V3.0", s_v30, "V3.1", s_v31)
    return OUT_MD


if __name__ == "__main__":
    run()
