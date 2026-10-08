#!/usr/bin/env python3
"""V3 close publish cutover — Plan-grade user board from V2.3 candidates.

Keep v2_3_scan.py for hist/enrich freshness, then run this script.

Usage:
  python ./v3/publish_close_v3.py --t-date YYYY-MM-DD

Outputs:
  report_v3.md
  candidates_v3.json
  push_close_v3_{YYYYMMDD}.md
  v3/report/out/trend_bot_results_{T}.json|.md
  history/a_plus_plan_daily.parquet|.csv + a_plus_plan_by_date/{T}.csv
  market regime archive append
  v3/exit_state/store.json

User table = Plan grades with Tech/Fund/Sector/Cont/消耗/风险/保护价/破坏价/ExitSignal.
NO RecScore board. NO English ResonanceTags. NO 荐买.
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

# Prevent `python v3/publish_close_v3.py` from shadowing stdlib `types`
_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
_sys0 = str(_SCRIPT_DIR)
if sys.path and sys.path[0] in ("", _sys0):
    sys.path.pop(0)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
ROOT = _ROOT

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from v3.bot import results_to_markdown_table, sort_results
from v3.config import TREND_BOT_CONFIG
from v3.engines import ExitEngine
from v3.grading import grade_candidate
from v3.market_regime_archive import append_from_market_meta
from v3.regime_map import pair_from_market_meta
from v3.report import write_unified_report
from v3.types import (
    DataStatus,
    ExitSignal,
    Grade,
    Lifecycle,
    MarketRegime,
    RiskLevel,
    TrendBotResult,
)

CST = timezone(timedelta(hours=8))
LOCAL_DAILY = _PKG_LOCAL_DAILY
EXIT_STORE_PATH = ROOT / "v3" / "exit_state" / "store.json"
REPORT_OUT_DIR = ROOT / "v3" / "report" / "out"
HIST_DIR = ROOT / "history"
A_PLUS_PARQUET = HIST_DIR / "a_plus_plan_daily.parquet"
A_PLUS_CSV = HIST_DIR / "a_plus_plan_daily.csv"
A_PLUS_BY_DATE = HIST_DIR / "a_plus_plan_by_date"

LIST_KEYS: Sequence[str] = (
    "recommendation_list",
    "all_hard_pass",
    "s_list",
    "a_list",
    "b_list",
    "oh_list",
    "core_list",
    "observation_list",
    "high_risk_list",
    "filter_list",
    "pool_new_trend",
    "pool_second_confirm",
    "pool_main_continue",
    "pool_trend_decay",
    "kjs_list",
)

A_PLUS_COLS = [
    "t_date", "code6", "name", "tech", "fund", "sector", "cont", "consumption",
    "risk_level", "lifecycle", "grade", "phase_v2", "phase_cn", "industry",
    "close", "protection_price", "break_price", "exit_signal",
    "regime_v3", "legacy_regime", "risk_off",
]


def _enum_val(x: Any, default: str = "") -> str:
    if x is None:
        return default
    return str(x.value) if hasattr(x, "value") else str(x)


def _norm_code(code: Any) -> str:
    c = str(code or "").strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def load_candidates(path: Optional[Path] = None) -> Tuple[Dict[str, Any], Path]:
    if path is not None:
        p = Path(path)
    else:
        p = ROOT / "candidates_v2_3.json"
        if not p.exists():
            p = ROOT / "candidates_v2.json"
    if not p.exists():
        raise FileNotFoundError(f"candidates not found: {p}")
    return json.loads(p.read_text(encoding="utf-8")), p


def load_market_meta(
    path: Optional[Path], payload: Dict[str, Any], t_date: str
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    p = Path(path) if path else ROOT / "market_meta.json"
    if p.exists():
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    embedded = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if embedded and str(embedded.get("t_date") or t_date) == t_date:
        merged = dict(meta)
        merged.update({k: v for k, v in embedded.items() if v is not None})
        meta = merged
    if not meta.get("t_date"):
        meta["t_date"] = t_date
    if not meta.get("regime_v2"):
        meta["regime_v2"] = payload.get("regime_v2") or "NORMAL"
    if meta.get("market_score") is None and payload.get("market_score") is not None:
        meta["market_score"] = payload.get("market_score")
    if not meta.get("market_conclusion") and payload.get("market_conclusion"):
        meta["market_conclusion"] = payload.get("market_conclusion")
    if meta.get("cap") is None and payload.get("cap") is not None:
        meta["cap"] = payload.get("cap")
    return meta


def collect_scored(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    by: Dict[str, Dict[str, Any]] = {}

    def richness(c: Dict[str, Any]) -> int:
        keys = (
            "tech_score", "continuity_score", "fund_score", "sector_score",
            "consumption_score", "risk_level", "recommendation",
            "fundamental", "sector", "continuity", "risk",
        )
        return sum(1 for k in keys if c.get(k) is not None)

    for key in LIST_KEYS:
        for c in payload.get(key) or []:
            if not isinstance(c, dict):
                continue
            c6 = _norm_code(c.get("code6") or c.get("code"))
            if not c6 or c6 == "000000":
                continue
            tech = c.get("tech_score", c.get("score"))
            cont = c.get("continuity_score")
            if tech is None or cont is None:
                continue
            prev = by.get(c6)
            if prev is None or richness(c) >= richness(prev):
                by[c6] = c
    return list(by.values())


def _fund_score(c: Dict[str, Any]) -> Optional[float]:
    fund = c.get("fundamental") or {}
    if fund.get("data_insufficient"):
        return None
    return _f(c.get("fund_score") if c.get("fund_score") is not None else fund.get("score"))


def _sector_score(c: Dict[str, Any]) -> Optional[float]:
    sec = c.get("sector") or {}
    if sec.get("data_insufficient"):
        return None
    return _f(c.get("sector_score") if c.get("sector_score") is not None else sec.get("score"))


def _risk(c: Dict[str, Any]) -> Tuple[RiskLevel, bool]:
    risk = c.get("risk") or {}
    broken = bool(risk.get("broken") or c.get("broken"))
    raw = c.get("risk_level") or risk.get("risk_score") or "MID"
    return RiskLevel.map_from_v2(str(raw), broken=broken), broken


def _life(c: Dict[str, Any]) -> Lifecycle:
    return Lifecycle.map_from_v2_phase(c.get("phase") or c.get("phase_v2"))


def _data_status(c: Dict[str, Any]) -> DataStatus:
    fund = c.get("fundamental") or {}
    sec = c.get("sector") or {}
    if fund.get("data_insufficient") and sec.get("data_insufficient"):
        return DataStatus.DATA_INSUFFICIENT
    return DataStatus.VALID


def load_hist(code6: str, t_date: str) -> Optional[pd.DataFrame]:
    path = LOCAL_DAILY / f"{code6}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] <= pd.Timestamp(t_date)].sort_values("date").reset_index(drop=True)
    return df if len(df) >= 30 else None


def load_exit_store() -> Dict[str, Dict[str, Any]]:
    if not EXIT_STORE_PATH.exists():
        return {}
    try:
        data = json.loads(EXIT_STORE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_exit_store(store: Dict[str, Dict[str, Any]]) -> None:
    EXIT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXIT_STORE_PATH.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def evaluate_one(
    c: Dict[str, Any],
    *,
    t_date: str,
    regime_v3: MarketRegime,
    legacy_regime: str,
    risk_off: bool,
    exit_eng: ExitEngine,
    exit_store: Dict[str, Dict[str, Any]],
) -> TrendBotResult:
    code6 = _norm_code(c.get("code6") or c.get("code"))
    name = str(c.get("name") or "")
    tech = _f(c.get("tech_score", c.get("score")))
    cont = _f(c.get("continuity_score"))
    fund = _fund_score(c)
    sector = _sector_score(c)
    consumption = _f(c.get("consumption_score"))
    if consumption is None:
        consumption = _f((c.get("continuity") or {}).get("consumption_score"))
    risk_level, broken = _risk(c)
    life = _life(c)
    overheated = bool(c.get("overheated") or life == Lifecycle.OVERHEATED)
    data_status = _data_status(c)

    gr = grade_candidate(
        tech_score=tech,
        continuity_score=cont,
        fund_score=fund,
        sector_score=sector,
        risk_level=risk_level,
        lifecycle=life,
        regime_v3=regime_v3,
        consumption_score=consumption,
        overheated=overheated,
        broken=broken,
        data_status=data_status,
    )

    m = c.get("metrics") or {}
    close_hint = _f(c.get("close") if c.get("close") is not None else m.get("close"))
    ma20_hint = _f(c.get("ma20") if c.get("ma20") is not None else m.get("ma20"))
    slope_hint = _f(m.get("ma20_slope"))
    plat_hint = _f(
        m.get("platform_low")
        or ((c.get("risk") or {}).get("metrics") or {}).get("platform_low_used")
    )

    hist = load_hist(code6, t_date)
    prior = dict(exit_store.get(code6) or {})
    exit_res = exit_eng.evaluate(
        hist,
        t_date=t_date,
        code=code6,
        prior_state=prior,
        close=close_hint,
        ma20=ma20_hint,
        ma20_slope=slope_hint,
        platform_low=plat_hint,
        continuity_score=cont,
        continuity_delta1=_f(c.get("continuity_delta1")),
        continuity_delta_label=c.get("continuity_delta_label"),
        consumption_score=consumption,
        risk_level=risk_level,
        risk_broken=broken,
    )

    if exit_res.ok:
        exit_store[code6] = {
            "protection_price": exit_res.protection_price,
            "peak_close": exit_res.peak_close,
            "broken_streak": int(exit_res.broken_streak or 0),
            "warning_price": exit_res.warning_price,
            "break_trend_price": exit_res.break_trend_price,
            "structure_support": exit_res.structure_support,
            "exit_signal": _enum_val(exit_res.exit_signal, ExitSignal.HOLD.value),
            "consumption_score": consumption,
            "t_date": t_date,
        }

    industry = c.get("industry") or (c.get("sector") or {}).get("industry") or ""
    risks = list(c.get("risks") or [])
    if exit_res.tags:
        risks.extend([f"exit:{t}" for t in list(exit_res.tags)[:4]])

    regime_enum = regime_v3 if isinstance(regime_v3, MarketRegime) else MarketRegime(str(regime_v3))

    return TrendBotResult(
        t_date=t_date,
        code=code6,
        name=name,
        grade=gr.grade,
        data_status=data_status,
        tech_score=tech,
        continuity_score=cont,
        fund_score=fund,
        sector_score=sector,
        consumption_score=consumption,
        lifecycle=life,
        risk_level=risk_level,
        regime_v3=regime_enum,
        legacy_regime=legacy_regime,
        risk_off=bool(risk_off),
        exit_signal=exit_res.exit_signal if exit_res.ok else ExitSignal.HOLD,
        close=exit_res.close if exit_res.close is not None else close_hint,
        warning_price=exit_res.warning_price,
        protection_price=exit_res.protection_price,
        break_price=exit_res.break_trend_price,
        reasons=list(gr.reasons),
        risks=risks,
        notes=[
            f"phase_v2={c.get('phase') or ''}",
            f"phase_cn={c.get('phase_cn') or ''}",
            f"industry={industry}",
            *(list(exit_res.notes[:2]) if exit_res.notes else []),
        ],
    )


def grade_counts(results: Sequence[TrendBotResult]) -> Dict[str, int]:
    out = {"A+": 0, "A": 0, "B": 0, "WATCH": 0, "FILTER": 0}
    for r in results:
        g = _enum_val(r.grade, "FILTER")
        out[g] = out.get(g, 0) + 1
    return out


def build_user_md(
    results: Sequence[TrendBotResult],
    *,
    t_date: str,
    meta: Dict[str, Any],
    legacy: str,
    regime_v3: str,
    risk_off: bool,
    counts: Dict[str, int],
) -> str:
    sorted_rows = sort_results(list(results))
    board = [r for r in sorted_rows if _enum_val(r.grade) in ("A+", "A", "B", "WATCH")]
    filter_n = counts.get("FILTER", 0)
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    conclusions = meta.get("market_conclusion") or []
    if not isinstance(conclusions, list):
        conclusions = [str(conclusions)]
    default_exit = (TREND_BOT_CONFIG.get("exit") or {}).get("default_strategy", "")

    lines: List[str] = [
        "# 📡 趋势确认突破雷达 V3.0 — Plan 等级观察榜",
        "",
        f"**日期：** {t_date}（T日收盘）",
        f"**生成时间（CST）：** {now} CST",
        f"**regime_v3：** {regime_v3}  **legacy：** {legacy}  **risk_off：** {risk_off}",
        f"**MarketScore：** {meta.get('market_score', '—')}",
        f"**DEFAULT_EXIT：** `{default_exit}`",
        f"**等级计数：** A+={counts.get('A+', 0)}  A={counts.get('A', 0)}  "
        f"B={counts.get('B', 0)}  WATCH={counts.get('WATCH', 0)}  FILTER={filter_n}",
        "",
        "## 今日市场结论",
        "",
    ]
    for c in conclusions:
        lines.append(f"- {c}")
    lines += [
        "",
        "> **观察池 / 结构确认输出。禁止荐买 / 预测必涨。**  "
        "等级以 Plan 阈值矩阵为准（**不是** RecommendationScore）。  "
        "V2.3 RecScore 仅内部保留，不再作为用户主榜。",
        "",
        "## V3 Plan 等级观察榜",
        "",
        "排序：A+→A→B→WATCH→FILTER；同级 Cont↓ Tech↓ Sector↓。",
        "",
        results_to_markdown_table(board),
        "",
    ]
    if filter_n:
        lines.append(
            f"> FILTER {filter_n} 只已折叠（硬过滤 / 未达 WATCH）；详见 `candidates_v3.json`。"
        )
        lines.append("")

    a_plus = [r for r in board if _enum_val(r.grade) == "A+"]
    if a_plus:
        lines += ["## A+ 摘录（规则理由）", ""]
        for r in a_plus[:15]:
            lines.append(f"### {r.name or ''}（{r.code}）— A+")
            lines.append("")
            for reason in (r.reasons or [])[:8]:
                lines.append(f"- {reason}")
            pp = "—" if r.protection_price is None else f"{r.protection_price:.2f}"
            bp = "—" if r.break_price is None else f"{r.break_price:.2f}"
            lines.append(
                f"- 保护价 {pp} / 破坏价 {bp} / ExitSignal `{_enum_val(r.exit_signal)}`"
            )
            lines.append("- **说明：** 规则模板观察表述，**不构成买入建议**。")
            lines.append("")

    lines += [
        "---",
        "",
        "*V3 publish-layer cutover. Internal RecScore may still exist in scan JSON; "
        "user push table is Plan-grade only.*",
        "",
    ]
    return "\n".join(lines)


def build_push_md(
    results: Sequence[TrendBotResult],
    *,
    t_date: str,
    meta: Dict[str, Any],
    legacy: str,
    regime_v3: str,
    risk_off: bool,
    counts: Dict[str, int],
) -> str:
    board = [
        r for r in sort_results(list(results))
        if _enum_val(r.grade) in ("A+", "A", "B", "WATCH")
    ]
    ymd = t_date.replace("-", "")
    return "\n".join([
        f"# 📡 V3 Plan 观察榜 {t_date}",
        "",
        f"regime={regime_v3} (legacy {legacy}, risk_off={risk_off})  "
        f"MarketScore={meta.get('market_score', '—')}  "
        f"A+={counts.get('A+', 0)} A={counts.get('A', 0)} "
        f"B={counts.get('B', 0)} WATCH={counts.get('WATCH', 0)}",
        "",
        "> 观察池；**禁止荐买 / 预测必涨**。非 RecScore 主榜。",
        "",
        results_to_markdown_table(board[:40]),
        "",
        f"全文：`report_v3.md` / `push_close_v3_{ymd}.md`",
        "",
    ])


def to_candidates_payload(
    results: Sequence[TrendBotResult],
    *,
    t_date: str,
    meta: Dict[str, Any],
    counts: Dict[str, int],
    source_path: str,
) -> Dict[str, Any]:
    sorted_rows = sort_results(list(results))
    by_grade: Dict[str, List[Dict[str, Any]]] = {
        "A+": [], "A": [], "B": [], "WATCH": [], "FILTER": [],
    }
    rows: List[Dict[str, Any]] = []
    for r in sorted_rows:
        g = _enum_val(r.grade, "FILTER")
        row = {
            "t_date": r.t_date,
            "code": r.code,
            "code6": r.code,
            "name": r.name,
            "grade": g,
            "lifecycle": _enum_val(r.lifecycle),
            "tech_score": r.tech_score,
            "fund_score": r.fund_score,
            "sector_score": r.sector_score,
            "continuity_score": r.continuity_score,
            "consumption_score": r.consumption_score,
            "risk_level": _enum_val(r.risk_level),
            "protection_price": r.protection_price,
            "break_price": r.break_price,
            "warning_price": r.warning_price,
            "exit_signal": _enum_val(r.exit_signal),
            "close": r.close,
            "regime_v3": _enum_val(r.regime_v3),
            "legacy_regime": r.legacy_regime,
            "risk_off": bool(r.risk_off),
            "data_status": _enum_val(r.data_status),
            "reasons": list(r.reasons or []),
            "risks": list(r.risks or []),
            "notes": list(r.notes or []),
        }
        rows.append(row)
        by_grade.setdefault(g, []).append(row)

    pair = pair_from_market_meta(meta)
    return {
        "t_date": t_date,
        "model": "V3.0_plan_grade_publish",
        "source_candidates": source_path,
        "regime_v3": rows[0]["regime_v3"] if rows else _enum_val(pair.regime_v3),
        "legacy_regime": rows[0]["legacy_regime"] if rows else meta.get("regime_v2"),
        "risk_off": rows[0]["risk_off"] if rows else bool(pair.risk_off),
        "market_score": meta.get("market_score"),
        "market_conclusion": meta.get("market_conclusion"),
        "grade_counts": counts,
        "n": len(rows),
        "built_at": datetime.now(CST).isoformat(timespec="seconds"),
        "disclaimer": "观察池/结构确认；禁止荐买/预测必涨；用户主榜=Plan等级，非RecScore",
        "A_PLUS": by_grade.get("A+", []),
        "A": by_grade.get("A", []),
        "B": by_grade.get("B", []),
        "WATCH": by_grade.get("WATCH", []),
        "FILTER": by_grade.get("FILTER", []),
        "results": rows,
        "meta": {
            "t_date": t_date,
            "regime_v2": meta.get("regime_v2"),
            "regime": meta.get("regime"),
            "market_score": meta.get("market_score"),
            "cap": meta.get("cap"),
            "breadth": meta.get("breadth"),
            "turnover": meta.get("turnover"),
            "indices": meta.get("indices"),
            "market_conclusion": meta.get("market_conclusion"),
        },
    }


def append_a_plus_archive(results: Sequence[TrendBotResult], t_date: str) -> int:
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    A_PLUS_BY_DATE.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for r in sort_results(list(results)):
        if _enum_val(r.grade) != "A+":
            continue
        phase_v2 = phase_cn = industry = ""
        for note in r.notes or []:
            if note.startswith("phase_v2="):
                phase_v2 = note.split("=", 1)[1]
            elif note.startswith("phase_cn="):
                phase_cn = note.split("=", 1)[1]
            elif note.startswith("industry="):
                industry = note.split("=", 1)[1]
        rows.append({
            "t_date": t_date,
            "code6": r.code,
            "name": r.name,
            "tech": r.tech_score,
            "fund": r.fund_score,
            "sector": r.sector_score,
            "cont": r.continuity_score,
            "consumption": r.consumption_score,
            "risk_level": _enum_val(r.risk_level),
            "lifecycle": _enum_val(r.lifecycle),
            "grade": "A+",
            "phase_v2": phase_v2,
            "phase_cn": phase_cn,
            "industry": industry,
            "close": r.close,
            "protection_price": r.protection_price,
            "break_price": r.break_price,
            "exit_signal": _enum_val(r.exit_signal),
            "regime_v3": _enum_val(r.regime_v3),
            "legacy_regime": r.legacy_regime,
            "risk_off": bool(r.risk_off),
        })

    day = pd.DataFrame(rows, columns=A_PLUS_COLS)
    day.to_csv(A_PLUS_BY_DATE / f"{t_date}.csv", index=False, encoding="utf-8")

    if A_PLUS_PARQUET.exists():
        old = pd.read_parquet(A_PLUS_PARQUET)
        old = old[old["t_date"].astype(str) != t_date]
        all_df = pd.concat([old, day], ignore_index=True)
    else:
        all_df = day
    if len(all_df):
        all_df = all_df.sort_values(
            ["t_date", "tech", "code6"], ascending=[True, False, True]
        )
    all_df.to_parquet(A_PLUS_PARQUET, index=False)
    all_df.to_csv(A_PLUS_CSV, index=False, encoding="utf-8")
    return len(rows)


def run_publish(
    *,
    t_date: str,
    candidates_path: Optional[Path] = None,
    market_meta_path: Optional[Path] = None,
    skip_exit_persist: bool = False,
) -> Dict[str, Any]:
    payload, src = load_candidates(candidates_path)
    t_date = str(t_date or payload.get("t_date") or "").strip()
    if not t_date:
        raise ValueError("t_date required")

    meta = load_market_meta(market_meta_path, payload, t_date)
    pair = pair_from_market_meta(meta)
    regime_v3 = pair.regime_v3
    legacy = pair.legacy_regime
    risk_off = bool(pair.risk_off)

    cands = collect_scored(payload)
    exit_eng = ExitEngine()
    exit_store: Dict[str, Dict[str, Any]] = {} if skip_exit_persist else load_exit_store()

    results = [
        evaluate_one(
            c,
            t_date=t_date,
            regime_v3=regime_v3,
            legacy_regime=legacy,
            risk_off=risk_off,
            exit_eng=exit_eng,
            exit_store=exit_store,
        )
        for c in cands
    ]
    results = sort_results(results)
    counts = grade_counts(results)

    if not skip_exit_persist:
        save_exit_store(exit_store)

    report_path = ROOT / "report_v3.md"
    report_path.write_text(
        build_user_md(
            results, t_date=t_date, meta=meta, legacy=legacy,
            regime_v3=_enum_val(regime_v3), risk_off=risk_off, counts=counts,
        ),
        encoding="utf-8",
    )

    ymd = t_date.replace("-", "")
    push_path = ROOT / f"push_close_v3_{ymd}.md"
    push_path.write_text(
        build_push_md(
            results, t_date=t_date, meta=meta, legacy=legacy,
            regime_v3=_enum_val(regime_v3), risk_off=risk_off, counts=counts,
        ),
        encoding="utf-8",
    )

    cand_path = ROOT / "candidates_v3.json"
    cand_path.write_text(
        json.dumps(
            to_candidates_payload(
                results, t_date=t_date, meta=meta, counts=counts, source_path=str(src),
            ),
            ensure_ascii=False, indent=2, default=str,
        ),
        encoding="utf-8",
    )

    unified = write_unified_report(
        results,
        REPORT_OUT_DIR,
        t_date,
        include_details=True,
        meta={
            "source": "publish_close_v3",
            "source_candidates": str(src),
            "grade_counts": counts,
            "legacy_regime": legacy,
            "risk_off": risk_off,
        },
    )

    a_plus_n = append_a_plus_archive(results, t_date)

    try:
        arch = append_from_market_meta(meta)
    except Exception as e:  # noqa: BLE001
        arch = {"wrote": False, "error": str(e)}

    return {
        "t_date": t_date,
        "counts": counts,
        "n": len(results),
        "a_plus_archived": a_plus_n,
        "report_v3": str(report_path),
        "push": str(push_path),
        "candidates_v3": str(cand_path),
        "unified_json": str(unified.get("json")),
        "unified_md": str(unified.get("md")),
        "regime_v3": _enum_val(regime_v3),
        "legacy_regime": legacy,
        "risk_off": risk_off,
        "archive": arch,
        "exit_store": str(EXIT_STORE_PATH),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="V3 close publish from V2.3 candidates")
    ap.add_argument("--t-date", default=None, help="YYYY-MM-DD (default: from candidates)")
    ap.add_argument("--candidates", default=None, help="path to candidates_v2(_3).json")
    ap.add_argument("--market-meta", default=None, help="path to market_meta.json")
    ap.add_argument(
        "--skip-exit-persist", action="store_true",
        help="do not read/write v3/exit_state/store.json",
    )
    args = ap.parse_args(argv)

    t_date = args.t_date
    if not t_date:
        payload, _ = load_candidates(Path(args.candidates) if args.candidates else None)
        t_date = str(payload.get("t_date") or "")
    if not t_date:
        print("ERROR: --t-date required", file=sys.stderr)
        return 2

    info = run_publish(
        t_date=t_date,
        candidates_path=Path(args.candidates) if args.candidates else None,
        market_meta_path=Path(args.market_meta) if args.market_meta else None,
        skip_exit_persist=bool(args.skip_exit_persist),
    )
    c = info["counts"]
    print(
        f"publish_close_v3 t_date={info['t_date']} "
        f"regime={info['regime_v3']} legacy={info['legacy_regime']} risk_off={info['risk_off']}"
    )
    print(
        f"counts A+={c.get('A+', 0)} A={c.get('A', 0)} B={c.get('B', 0)} "
        f"WATCH={c.get('WATCH', 0)} FILTER={c.get('FILTER', 0)} n={info['n']}"
    )
    print(f"report_v3:     {info['report_v3']}")
    print(f"push:          {info['push']}")
    print(f"candidates_v3: {info['candidates_v3']}")
    print(f"unified:       {info['unified_json']}")
    print(f"a_plus_rows:   {info['a_plus_archived']} → {A_PLUS_PARQUET}")
    print(f"exit_store:    {info['exit_store']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
