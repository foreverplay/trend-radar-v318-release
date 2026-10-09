#!/usr/bin/env python3
"""V3.1.7 close publish — 趋势分析雷达 V3.1.7 观察榜.

Pipeline (after `v2_3_scan.py --t-date T` keeps candidates/market_meta fresh):
  1. Reuse publish_close_v3: candidate collection, Plan grade (grade_candidate,
     thresholds untouched), protection / break price, ExitSignal, regime.
  2. For every candidate that can land in any V3.1.7 partition
     (Plan grade A+/A/B, or meets the EntrySignal floor tech>=75 & cont>=70 &
     sector missing-or->=60), replay the V3.1.7 as-of pipeline
     (v3.v31.live_v317 → regression_v317.replay_symbol) over a warm-up window
     ending at T: TrendState / Maturity / RealizedTrend / FollowThrough /
     EntryWindow / Exhaustion. Only bars <= T. No symbol/date branches.
  3. V3.1.7 grade: Plan grade, except Plan A+ must pass the V3.1.7 maturity
     gate (MATURE, or ADVANCED → shown as EXTENDED; RealizedTrend floor;
     TrendReturn>=15%; TrendAge>=15; Cont>=75; Sector>=60|missing;
     FundRisk!=HIGH; no TrendFailure). Failing Plan A+ → A (never promoted).
  4. Partitions: 主榜「趋势成熟·入场窗口」(EntrySignal) / 观察池「趋势构建中」
     (BUILDING & grade>=B) / 「过热/延伸」(ADVANCED | Exhaustion HIGH |
     window EXTENDED, trend intact) / 其余等级榜 (truncated) / FILTER folded.

Usage:
  python -m v3.publish_close_v317 --t-date YYYY-MM-DD

Outputs:
  report_v317.md · candidates_v317.json · push_close_v317_{YYYYMMDD}.md
  history/a_plus_v317_daily.parquet|.csv + history/a_plus_v317_by_date/{T}.csv
  history/a_plus_plan_daily.parquet|.csv (Plan A+ archive, same schema as V3)
  v3/exit_state/store_v317.json (rerun-safe; V3 store.json untouched)

Observation only. NO RecScore board. NO English ResonanceTags. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
if sys.path and sys.path[0] in ("", str(_SCRIPT_DIR)):
    sys.path.pop(0)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
ROOT = _ROOT

import argparse
import json
import multiprocessing as mp
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from v3 import publish_close_v3 as pc3
from v3.bot import sort_results
from v3.engines import ExitEngine
from v3.engines.entry_signal import EntrySignalEmitter
from v3.regime_map import pair_from_market_meta
from v3.types import TrendBotResult
from v3.v31.config import get_v311_config, get_v317_maturity, get_v317_realized
from v3.v31.live_v317 import DEFAULT_WARMUP, evaluate_v317_asof, load_hist_to_t, make_builder

CST = pc3.CST
VERSION = "V3.1.7"
EXIT_STORE_V317 = ROOT / "v3" / "exit_state" / "store_v317.json"
HIST_DIR = ROOT / "history"
A317_PARQUET = HIST_DIR / "a_plus_v317_daily.parquet"
A317_CSV = HIST_DIR / "a_plus_v317_daily.csv"
A317_BY_DATE = HIST_DIR / "a_plus_v317_by_date"
PUSH_MAX_ROWS = 40
GRADE_RANK = {"A+": 0, "A": 1, "B": 2, "WATCH": 3, "FILTER": 4}
ENTRY_STATES = ("MAIN_RISE", "ACCELERATION", "NEW_WAVE")
DEAD_STATES = ("TREND_FAILURE", "NONE", "OBSERVE")

SEC_MAIN = "MAIN"
SEC_BUILDING = "BUILDING"
SEC_EXTENDED = "EXTENDED"
SEC_REST = "REST"
SEC_FILTER = "FILTER"
SEC_CN = {
    SEC_MAIN: "趋势成熟·入场窗口",
    SEC_BUILDING: "趋势构建中（观察池）",
    SEC_EXTENDED: "过热/延伸",
    SEC_REST: "其余等级榜",
    SEC_FILTER: "FILTER",
}

E = pc3._enum_val
_f = pc3._f


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _scope_ok(r: TrendBotResult) -> bool:
    """Candidates that can enter any V3.1.7 partition (rule floors, not names)."""
    g = E(r.grade, "FILTER")
    if g in ("A+", "A", "B"):
        return True
    t, c, s = r.tech_score, r.continuity_score, r.sector_score
    return (
        t is not None and c is not None and float(t) >= 75.0 and float(c) >= 70.0
        and (s is None or float(s) >= 60.0)
    )


def _load_store() -> Dict[str, Dict[str, Any]]:
    if EXIT_STORE_V317.exists():
        try:
            d = json.loads(EXIT_STORE_V317.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    # seed from V3 store (treated as `last`)
    out: Dict[str, Dict[str, Any]] = {}
    for code, st in (pc3.load_exit_store() or {}).items():
        if isinstance(st, dict):
            out[code] = {"last": st, "prev": {}}
    return out


def _prior_for(store: Dict[str, Dict[str, Any]], t_date: str) -> Dict[str, Dict[str, Any]]:
    """Rerun-safe prior: use state strictly before T."""
    pri: Dict[str, Dict[str, Any]] = {}
    for code, rec in store.items():
        last = rec.get("last") or {}
        prev = rec.get("prev") or {}
        lt = str(last.get("t_date") or "")
        if lt and lt < t_date:
            pri[code] = dict(last)
        elif lt == t_date and str(prev.get("t_date") or "") < t_date and prev:
            pri[code] = dict(prev)
    return pri


def _merge_store(store: Dict[str, Dict[str, Any]], new: Dict[str, Dict[str, Any]], t_date: str) -> None:
    for code, st in new.items():
        if str(st.get("t_date") or "") != t_date:
            continue
        rec = store.setdefault(code, {"last": {}, "prev": {}})
        last = rec.get("last") or {}
        lt = str(last.get("t_date") or "")
        if lt and lt < t_date:
            rec["prev"] = last
            rec["last"] = st
        elif not lt or lt == t_date:
            rec["last"] = st
        # lt > t_date (backfill): keep newer state untouched


def _save_store(store: Dict[str, Dict[str, Any]]) -> None:
    EXIT_STORE_V317.parent.mkdir(parents=True, exist_ok=True)
    EXIT_STORE_V317.write_text(json.dumps(store, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _auto_workers() -> int:
    """CPU-2, capped at 5 and by MemAvailable (~0.5 GB per replay worker)."""
    n = max(1, min(5, (os.cpu_count() or 2) - 2))
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail_gb = int(line.split()[1]) / 1024 / 1024
                n = max(1, min(n, int((avail_gb - 0.6) / 0.5)))
                break
    except Exception:
        pass
    return n


def _note(r: TrendBotResult, key: str) -> str:
    for n in r.notes or []:
        if n.startswith(key + "="):
            return n.split("=", 1)[1]
    return ""


# ---------------------------------------------------------------------------
# V3.1.7 replay (process pool)
# ---------------------------------------------------------------------------

_W_BUILDER = None


def _w_init() -> None:
    global _W_BUILDER
    import warnings
    warnings.filterwarnings("ignore")
    _W_BUILDER = make_builder()


def _w_eval(args: Tuple[str, str, str, int]) -> Dict[str, Any]:
    code6, name, t_date, warmup = args
    t0 = time.time()
    try:
        out = evaluate_v317_asof(code6, name, t_date, builder=_W_BUILDER, warmup=warmup)
    except Exception as e:  # noqa: BLE001
        out = {"code6": code6, "ok": False, "error": f"{type(e).__name__}: {e}"}
    out["elapsed_sec"] = round(time.time() - t0, 1)
    return out


def run_v317_layer(
    items: Sequence[Tuple[str, str]], t_date: str, *, warmup: int, workers: int, verbose: bool = True
) -> Dict[str, Dict[str, Any]]:
    res: Dict[str, Dict[str, Any]] = {}
    if not items:
        return res
    tasks = [(c, n, t_date, warmup) for c, n in items]
    workers = max(1, min(int(workers), len(tasks)))
    t0 = time.time()
    if workers == 1:
        _w_init()
        it = map(_w_eval, tasks)
        for i, out in enumerate(it, 1):
            res[out["code6"]] = out
            if verbose:
                print(f"  v317 {i}/{len(tasks)} {out['code6']} ok={out.get('ok')} {out.get('elapsed_sec')}s", flush=True)
        return res
    # macOS 上父进程读过 parquet 后已有 pyarrow 线程，fork 出去的子进程会在 curl 初始化时崩溃。
    ctx = mp.get_context("spawn" if sys.platform == "darwin" else "fork")
    with ctx.Pool(workers, initializer=_w_init) as pool:
        for i, out in enumerate(pool.imap_unordered(_w_eval, tasks), 1):
            res[out["code6"]] = out
            if verbose:
                print(
                    f"  v317 {i}/{len(tasks)} {out['code6']} ok={out.get('ok')} "
                    f"{out.get('maturity_level')}/{out.get('entry_window')} {out.get('elapsed_sec')}s "
                    f"(t+{time.time()-t0:.0f}s)",
                    flush=True,
                )
    return res


# ---------------------------------------------------------------------------
# V3.1.7 grade / EntrySignal / partition
# ---------------------------------------------------------------------------

def _entry_signal_level(r: TrendBotResult, v: Dict[str, Any], broken: bool) -> Tuple[bool, List[str]]:
    em = EntrySignalEmitter(config=get_v311_config(), version="3.1.7")
    ok, tags = em._rule_ok(
        rule="current",
        state=str(v.get("state") or "").upper(),
        ew=str(v.get("entry_window") or "").upper(),
        tech=r.tech_score,
        cont=r.continuity_score,
        sector=r.sector_score,
        fund=r.fund_score,
        fund_risk_level=str(v.get("fund_risk") or ""),
        risk_level=E(r.risk_level, "WATCH").upper(),
        htf_ok=bool(v.get("htf_ok")),
        broken=bool(broken),
        maturity_level=str(v.get("maturity_level") or "").upper() or None,
        structural_completed=bool(v.get("struct_completed") or v.get("struct_matureContinuation")),
        exhaustion=str(v.get("exhaustion") or "").upper(),
        follow_through=str(v.get("follow_through") or "").upper(),
        current_wave_return=_f(v.get("wave_return")),
        realized_trend_score=_f(v.get("realized_score")),
        trend_age_days=int(v.get("trend_age") or 0),
        low_realized_trend=False,
    )
    return bool(ok), list(tags)


def _a_plus_gate(r: TrendBotResult, v: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """V3.1.7 maturity gate layered on Plan A+ (old thresholds unchanged)."""
    mcfg = get_v317_maturity()
    rcfg = get_v317_realized()
    fails: List[str] = []
    if not v.get("ok"):
        return False, ["V3.1.7 数据不足"]
    state = str(v.get("state") or "").upper()
    mat = str(v.get("maturity_level") or "").upper()
    if state not in ENTRY_STATES:
        fails.append(f"趋势状态{state}非主升/加速/新波段")
    if mat not in ("MATURE", "ADVANCED"):
        fails.append(f"成熟度{mat or '—'}未达MATURE")
    rs = _f(v.get("realized_score"))
    if rs is None or rs < float(rcfg.get("realized_score_min_for_a_plus", 70.0)):
        fails.append(f"已实现趋势分{rs if rs is not None else '—'}<{rcfg.get('realized_score_min_for_a_plus', 70.0)}")
    tr = _f(v.get("trend_return"))
    if tr is None or tr < float(rcfg.get("trend_return_min", 0.15)):
        fails.append("趋势涨幅<15%（LOW_REALIZED_TREND 不给 A+）")
    age = int(v.get("trend_age") or 0)
    if age < int(mcfg.get("a_plus_age_min", 15)):
        fails.append(f"趋势天数{age}<{mcfg.get('a_plus_age_min', 15)}")
    c = r.continuity_score
    if c is None or float(c) < float(mcfg.get("a_plus_cont_min", 75.0)):
        fails.append(f"Cont<{mcfg.get('a_plus_cont_min', 75.0)}")
    s = r.sector_score
    if s is not None and float(s) < float(mcfg.get("a_plus_sector_min", 60.0)):
        fails.append(f"Sector<{mcfg.get('a_plus_sector_min', 60.0)}")
    if str(v.get("fund_risk") or "").upper() == "HIGH":
        fails.append("基本面风险HIGH")
    if state == "TREND_FAILURE":
        fails.append("趋势失效")
    return (not fails), fails


def decorate(
    results: Sequence[TrendBotResult], v317: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in results:
        plan = E(r.grade, "FILTER")
        v = v317.get(r.code)
        evaluated = v is not None
        v = v or {}
        ok = bool(v.get("ok"))
        broken = bool(E(r.exit_signal) == "BROKEN") or E(r.risk_level) == "BROKEN"
        g317 = plan
        notes: List[str] = []
        a_fail: List[str] = []
        if plan == "A+":
            passed, a_fail = _a_plus_gate(r, v)
            if not passed:
                g317 = "A"
                notes.append("Plan A+ 未过 V3.1.7 成熟门 → A：" + "；".join(a_fail))
        es_ok, es_tags = (False, [])
        if ok:
            es_ok, es_tags = _entry_signal_level(r, v, broken)
        mat = str(v.get("maturity_level") or "") if ok else ""
        ew = str(v.get("entry_window") or "") if ok else ""
        exh = str(v.get("exhaustion") or "") if ok else ""
        state = str(v.get("state") or "") if ok else ""
        trend_alive = ok and state not in DEAD_STATES and not broken

        if g317 == "FILTER":
            section = SEC_FILTER
        elif trend_alive and GRADE_RANK.get(g317, 9) <= 3 and (
            mat == "ADVANCED" or exh == "HIGH" or ew == "EXTENDED"
        ) and (_scope_ok(r)):
            section = SEC_EXTENDED
        elif es_ok:
            section = SEC_MAIN
        elif ok and mat == "BUILDING" and GRADE_RANK.get(g317, 9) <= 2:
            section = SEC_BUILDING
        else:
            section = SEC_REST
        # A+ only allowed MATURE (main/others) or ADVANCED (extended); never on BUILDING
        if g317 == "A+" and section == SEC_BUILDING:  # defensive, gate already excludes
            g317 = "A"
        rows.append({
            "r": r,
            "code": r.code,
            "name": r.name,
            "plan_grade": plan,
            "grade_v317": g317,
            "section": section,
            "evaluated": evaluated,
            "v317_ok": ok,
            "v317_error": v.get("error"),
            "trend_state": state or None,
            "maturity": mat or None,
            "maturity_score": v.get("maturity_score") if ok else None,
            "entry_window": ew or None,
            "entry_signal": bool(es_ok),
            "entry_signal_tags": es_tags,
            "entry_signal_replay_edge": bool(v.get("entry_signal_replay_edge")) if ok else False,
            "trend_return": _f(v.get("trend_return")) if ok else None,
            "wave_return": _f(v.get("wave_return")) if ok else None,
            "return20": _f(v.get("return20")) if ok else None,
            "return40": _f(v.get("return40")) if ok else None,
            "return60": _f(v.get("return60")) if ok else None,
            "realized_score": _f(v.get("realized_score")) if ok else None,
            "realized_floor_ok": bool(v.get("realized_floor_ok")) if ok else None,
            "follow_through": v.get("follow_through") if ok else None,
            "main_rise_quality": _f(v.get("main_rise_quality")) if ok else None,
            "trend_age": int(v.get("trend_age") or 0) if ok else None,
            "exhaustion": exh or None,
            "fund_risk": v.get("fund_risk") if ok else None,
            "htf_ok": bool(v.get("htf_ok")) if ok else None,
            "warmup_start": v.get("warmup_start"),
            "hist_source": v.get("hist_source"),
            "a_plus_gate_fail": a_fail,
            "v317_notes": notes,
        })
    return rows


def _sort_key(d: Dict[str, Any]):
    r = d["r"]
    return (
        GRADE_RANK.get(d["grade_v317"], 9),
        -(float(r.continuity_score) if r.continuity_score is not None else -1),
        -(float(r.tech_score) if r.tech_score is not None else -1),
        -(float(r.sector_score) if r.sector_score is not None else -1),
        r.code,
    )


def _main_key(d: Dict[str, Any]):
    ew_rank = {"PRIME": 0, "CONFIRMED": 1}.get(str(d.get("entry_window") or ""), 9)
    return (GRADE_RANK.get(d["grade_v317"], 9), ew_rank) + _sort_key(d)[1:]


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------

HEADERS = [
    "股票", "阶段", "等级", "成熟度", "入场窗口", "趋势涨幅%", "波段涨幅%",
    "Tech", "Fund", "Sector", "Cont", "消耗", "风险", "保护价", "破坏价", "ExitSignal",
]


def _fs(x: Any, digits: int = 1) -> str:
    v = _f(x)
    if v is None:
        return "—"
    s = f"{v:.{digits}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _pct(x: Any) -> str:
    v = _f(x)
    return "—" if v is None else f"{v * 100:.1f}"


def table(rows: Sequence[Dict[str, Any]]) -> str:
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "|".join(["---"] * len(HEADERS)) + "|"]
    for d in rows:
        r: TrendBotResult = d["r"]
        stock = f"{(r.name or '').strip()}({r.code})" if r.name else r.code
        lines.append("| " + " | ".join([
            stock,
            E(r.lifecycle, "NONE"),
            d["grade_v317"],
            d.get("maturity") or "—",
            d.get("entry_window") or "—",
            _pct(d.get("trend_return")),
            _pct(d.get("wave_return")),
            _fs(r.tech_score),
            _fs(r.fund_score),
            _fs(r.sector_score),
            _fs(r.continuity_score),
            _fs(r.consumption_score),
            E(r.risk_level, "WATCH"),
            _fs(r.protection_price, 2),
            _fs(r.break_price, 2),
            E(r.exit_signal, "HOLD"),
        ]) + " |")
    return "\n".join(lines)


def split_sections(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in SEC_CN}
    for d in rows:
        out[d["section"]].append(d)
    out[SEC_MAIN].sort(key=_main_key)
    for k in (SEC_BUILDING, SEC_EXTENDED, SEC_REST, SEC_FILTER):
        out[k].sort(key=_sort_key)
    return out


def counts_v317(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    g = {k: 0 for k in GRADE_RANK}
    p = {k: 0 for k in GRADE_RANK}
    s = {k: 0 for k in SEC_CN}
    for d in rows:
        g[d["grade_v317"]] = g.get(d["grade_v317"], 0) + 1
        p[d["plan_grade"]] = p.get(d["plan_grade"], 0) + 1
        s[d["section"]] += 1
    return {"grade_v317": g, "plan_grade": p, "sections": s,
            "evaluated": sum(1 for d in rows if d["evaluated"]),
            "evaluated_ok": sum(1 for d in rows if d["v317_ok"])}


def _header_lines(t_date: str, meta: Dict[str, Any], regime_v3: str, legacy: str, risk_off: bool,
                  cnt: Dict[str, Any]) -> List[str]:
    s = cnt["sections"]
    g = cnt["grade_v317"]
    return [
        f"regime={regime_v3}（legacy {legacy}，risk_off={risk_off}）  MarketScore={meta.get('market_score', '—')}",
        f"主榜（趋势成熟·入场窗口）={s[SEC_MAIN]}  观察池（趋势构建中）={s[SEC_BUILDING]}  "
        f"过热/延伸={s[SEC_EXTENDED]}  其余={s[SEC_REST]}  FILTER={s[SEC_FILTER]}",
        f"V3.1.7 等级：A+={g['A+']} A={g['A']} B={g['B']} WATCH={g['WATCH']} FILTER={g['FILTER']}",
        "",
        "> 观察池，禁止荐买 / 预测必涨。",
    ]


def build_push_md(rows, *, t_date, meta, regime_v3, legacy, risk_off, cnt) -> str:
    secs = split_sections(rows)
    ymd = t_date.replace("-", "")
    lines = [f"# 📡 趋势分析雷达 V3.1.7 观察榜 {t_date}", ""]
    lines += _header_lines(t_date, meta, regime_v3, legacy, risk_off, cnt)
    lines.append("")
    budget = PUSH_MAX_ROWS
    main = secs[SEC_MAIN]
    lines += [f"## 主榜 · 趋势成熟·入场窗口（{len(main)}）", ""]
    if main:
        lines += [table(main), ""]
    else:
        lines += ["今日无满足 V3.1.7 入场窗口（MATURE + PRIME/CONFIRMED）的标的。", ""]
    budget -= len(main)
    for key, title in ((SEC_BUILDING, "观察池 · 趋势构建中"), (SEC_EXTENDED, "过热/延伸（趋势未失效，不进主榜）"),
                       (SEC_REST, "其余等级榜（A/B/WATCH）")):
        items = secs[key]
        if not items:
            continue
        show = items[: max(0, budget)] if key != SEC_REST else items[: max(0, min(budget, 10))]
        lines += [f"## {title}（{len(items)}）", ""]
        if show:
            lines += [table(show), ""]
            budget -= len(show)
        if len(show) < len(items):
            lines += [f"> 另 {len(items) - len(show)} 只已折叠，见 `report_v317.md`。", ""]
    if secs[SEC_FILTER]:
        lines += [f"> FILTER {len(secs[SEC_FILTER])} 只已折叠。", ""]
    lines += [f"全文：`report_v317.md` / `push_close_v317_{ymd}.md`", ""]
    return "\n".join(lines)


def build_report_md(rows, *, t_date, meta, regime_v3, legacy, risk_off, cnt, src, warmup) -> str:
    secs = split_sections(rows)
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    conclusions = meta.get("market_conclusion") or []
    if not isinstance(conclusions, list):
        conclusions = [str(conclusions)]
    lines = [f"# 📡 趋势分析雷达 V3.1.7 观察榜 {t_date}", "",
             f"**日期：** {t_date}（T日收盘）  **生成时间：** {now} CST", ""]
    lines += _header_lines(t_date, meta, regime_v3, legacy, risk_off, cnt)
    lines += ["", "## 今日市场结论", ""]
    lines += [f"- {c}" for c in conclusions] or ["- —"]
    lines += [
        "",
        "## 分区规则（V3.1.7）",
        "",
        "- **主榜 · 趋势成熟·入场窗口**：成熟度 MATURE + 入场窗口 PRIME/CONFIRMED + 趋势状态主升/加速/新波段 + "
        "Tech≥75、Cont≥70、Sector≥60（缺失不扣）；PRIME 另需突破后延续 PASS 且波段涨幅≥8%；基本面风险 HIGH / 趋势失效阻断。",
        "- **观察池 · 趋势构建中**：成熟度 BUILDING 且等级≥B（趋势在走，但已实现涨幅/延续/天数未证明，不是入场窗口）。",
        "- **过热/延伸**：成熟度 ADVANCED 或 消耗(Exhaustion) HIGH 或 入场窗口 EXTENDED，且趋势未失效；不作为新入场。",
        "- **等级**：沿用 Plan 阈值（未改数字）；Plan A+ 还须通过 V3.1.7 成熟门（MATURE/ADVANCED、已实现趋势分≥70、"
        "趋势涨幅≥15%、趋势天数≥15、Cont≥75、Sector≥60、基本面风险非 HIGH），未过降为 A。",
        "- 趋势涨幅% = 收盘/趋势锚 −1；波段涨幅% = 收盘/当前波段锚 −1；均只用 T 日及以前数据。",
        "",
    ]
    for key, title in ((SEC_MAIN, "主榜 · 趋势成熟·入场窗口"), (SEC_BUILDING, "观察池 · 趋势构建中"),
                       (SEC_EXTENDED, "过热/延伸"), (SEC_REST, "其余等级榜（A/B/WATCH）")):
        items = secs[key]
        lines += [f"## {title}（{len(items)}）", ""]
        lines += [table(items) if items else "（无）", ""]
    if secs[SEC_FILTER]:
        lines += [f"> FILTER {len(secs[SEC_FILTER])} 只已折叠（硬过滤 / 未达 WATCH），详见 `candidates_v317.json`。", ""]
    demoted = [d for d in rows if d["plan_grade"] == "A+" and d["grade_v317"] != "A+"]
    if demoted:
        lines += ["## Plan A+ 未过 V3.1.7 成熟门（降为 A）", ""]
        for d in demoted:
            lines.append(f"- {d['name']}（{d['code']}）：" + "；".join(d["a_plus_gate_fail"]))
        lines.append("")
    a_plus = [d for d in rows if d["grade_v317"] == "A+"]
    if a_plus:
        lines += ["## A+ 摘录（规则理由）", ""]
        for d in sorted(a_plus, key=_sort_key)[:15]:
            r = d["r"]
            lines.append(f"### {r.name}（{r.code}）— A+ · {d.get('maturity')} · {d.get('entry_window')}")
            lines.append("")
            for reason in (r.reasons or [])[:6]:
                lines.append(f"- {reason}")
            lines.append(
                f"- 趋势涨幅 {_pct(d.get('trend_return'))}% / 波段涨幅 {_pct(d.get('wave_return'))}% / "
                f"趋势天数 {d.get('trend_age')} / 突破后延续 {d.get('follow_through')} / 已实现趋势分 {_fs(d.get('realized_score'))}"
            )
            lines.append(f"- 保护价 {_fs(r.protection_price, 2)} / 破坏价 {_fs(r.break_price, 2)} / ExitSignal {E(r.exit_signal)}")
            lines.append("- 规则模板观察表述，不构成买入建议。")
            lines.append("")
    lines += [
        "---",
        "",
        f"*V3.1.7 发布层：候选来自 `{Path(src).name}`；V3.1.7 层对 {cnt['evaluated']} 只（等级≥B 或达入场地板）"
        f"做 as-of 回放（预热 {warmup} 个交易日，成功 {cnt['evaluated_ok']}）；其余 WATCH/FILTER 不进任何分区。*",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# payload / archive
# ---------------------------------------------------------------------------

def _row_json(d: Dict[str, Any]) -> Dict[str, Any]:
    r: TrendBotResult = d["r"]
    out = {
        "t_date": r.t_date, "code": r.code, "code6": r.code, "name": r.name,
        "grade": d["grade_v317"], "grade_v317": d["grade_v317"], "plan_grade": d["plan_grade"],
        "section": d["section"], "section_cn": SEC_CN[d["section"]],
        "lifecycle": E(r.lifecycle), "phase_cn": _note(r, "phase_cn"), "industry": _note(r, "industry"),
        "tech_score": r.tech_score, "fund_score": r.fund_score, "sector_score": r.sector_score,
        "continuity_score": r.continuity_score, "consumption_score": r.consumption_score,
        "risk_level": E(r.risk_level), "protection_price": r.protection_price, "break_price": r.break_price,
        "warning_price": r.warning_price, "exit_signal": E(r.exit_signal), "close": r.close,
        "regime_v3": E(r.regime_v3), "legacy_regime": r.legacy_regime, "risk_off": bool(r.risk_off),
        "data_status": E(r.data_status), "reasons": list(r.reasons or []), "risks": list(r.risks or []),
    }
    for k in ("evaluated", "v317_ok", "v317_error", "trend_state", "maturity", "maturity_score", "entry_window",
              "entry_signal", "entry_signal_tags", "entry_signal_replay_edge", "trend_return", "wave_return",
              "return20", "return40", "return60", "realized_score", "realized_floor_ok", "follow_through",
              "main_rise_quality", "trend_age", "exhaustion", "fund_risk", "htf_ok", "warmup_start",
              "hist_source", "a_plus_gate_fail", "v317_notes"):
        out[k] = d.get(k)
    return out


def to_payload(rows, *, t_date, meta, cnt, src, warmup) -> Dict[str, Any]:
    secs = split_sections(rows)
    pair = pair_from_market_meta(meta)
    allr = [_row_json(d) for d in sorted(rows, key=_sort_key)]
    return {
        "t_date": t_date,
        "model": "V3.1.7_trend_radar_publish",
        "title": f"趋势分析雷达 V3.1.7 观察榜 {t_date}",
        "source_candidates": str(src),
        "regime_v3": E(pair.regime_v3), "legacy_regime": pair.legacy_regime, "risk_off": bool(pair.risk_off),
        "market_score": meta.get("market_score"), "market_conclusion": meta.get("market_conclusion"),
        "counts": cnt, "warmup_days": warmup, "n": len(allr),
        "built_at": datetime.now(CST).isoformat(timespec="seconds"),
        "disclaimer": "观察池；禁止荐买 / 预测必涨；主榜=V3.1.7 成熟入场窗口，非 RecScore",
        "MAIN": [_row_json(d) for d in secs[SEC_MAIN]],
        "BUILDING": [_row_json(d) for d in secs[SEC_BUILDING]],
        "EXTENDED": [_row_json(d) for d in secs[SEC_EXTENDED]],
        "REST": [_row_json(d) for d in secs[SEC_REST]],
        "FILTER": [_row_json(d) for d in secs[SEC_FILTER]],
        "results": allr,
        "meta": {k: meta.get(k) for k in ("t_date", "regime_v2", "regime", "market_score", "cap", "breadth",
                                           "turnover", "indices", "market_conclusion")},
    }


A317_COLS = [
    "t_date", "code6", "name", "grade_v317", "plan_grade", "section", "maturity", "entry_window", "entry_signal",
    "trend_state", "trend_return", "wave_return", "trend_age", "realized_score", "follow_through", "exhaustion",
    "tech", "fund", "sector", "cont", "consumption", "risk_level", "lifecycle", "phase_cn", "industry", "close",
    "protection_price", "break_price", "exit_signal", "regime_v3", "legacy_regime", "risk_off", "a_plus_gate_fail",
]


def append_v317_archive(rows: Sequence[Dict[str, Any]], t_date: str) -> int:
    """Archive every Plan A+ or V3.1.7 A+ row (demoted Plan A+ kept with gate reasons)."""
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    A317_BY_DATE.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(rows, key=_sort_key):
        if d["plan_grade"] != "A+" and d["grade_v317"] != "A+":
            continue
        r = d["r"]
        out.append({
            "t_date": t_date, "code6": r.code, "name": r.name, "grade_v317": d["grade_v317"],
            "plan_grade": d["plan_grade"], "section": d["section"], "maturity": d.get("maturity"),
            "entry_window": d.get("entry_window"), "entry_signal": bool(d.get("entry_signal")),
            "trend_state": d.get("trend_state"), "trend_return": d.get("trend_return"),
            "wave_return": d.get("wave_return"), "trend_age": d.get("trend_age"),
            "realized_score": d.get("realized_score"), "follow_through": d.get("follow_through"),
            "exhaustion": d.get("exhaustion"), "tech": r.tech_score, "fund": r.fund_score,
            "sector": r.sector_score, "cont": r.continuity_score, "consumption": r.consumption_score,
            "risk_level": E(r.risk_level), "lifecycle": E(r.lifecycle), "phase_cn": _note(r, "phase_cn"),
            "industry": _note(r, "industry"), "close": r.close, "protection_price": r.protection_price,
            "break_price": r.break_price, "exit_signal": E(r.exit_signal), "regime_v3": E(r.regime_v3),
            "legacy_regime": r.legacy_regime, "risk_off": bool(r.risk_off),
            "a_plus_gate_fail": "；".join(d.get("a_plus_gate_fail") or []),
        })
    day = pd.DataFrame(out, columns=A317_COLS)
    day.to_csv(A317_BY_DATE / f"{t_date}.csv", index=False, encoding="utf-8")
    if A317_PARQUET.exists():
        old = pd.read_parquet(A317_PARQUET)
        old = old[old["t_date"].astype(str) != t_date]
        all_df = pd.concat([old, day], ignore_index=True) if len(day) else old
    else:
        all_df = day
    if len(all_df):
        all_df = all_df.sort_values(["t_date", "code6"])
    all_df.to_parquet(A317_PARQUET, index=False)
    all_df.to_csv(A317_CSV, index=False, encoding="utf-8")
    return len(out)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def run_publish(
    *,
    t_date: str,
    candidates_path: Optional[Path] = None,
    market_meta_path: Optional[Path] = None,
    skip_exit_persist: bool = False,
    warmup: int = DEFAULT_WARMUP,
    workers: Optional[int] = None,
    no_archive: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    t0 = time.time()
    payload, src = pc3.load_candidates(candidates_path)
    t_date = str(t_date or payload.get("t_date") or "").strip()
    if not t_date:
        raise ValueError("t_date required")
    cand_t = str(payload.get("t_date") or "")
    if cand_t and cand_t != t_date:
        raise ValueError(f"candidates t_date={cand_t} != --t-date {t_date}; run v2_3_scan.py --t-date {t_date} first")

    meta = pc3.load_market_meta(market_meta_path, payload, t_date)
    pair = pair_from_market_meta(meta)
    regime_v3, legacy, risk_off = pair.regime_v3, pair.legacy_regime, bool(pair.risk_off)

    cands = pc3.collect_scored(payload)
    codes = [pc3._norm_code(c.get("code6") or c.get("code")) for c in cands]

    # fresh daily bars to T (local parquet, remote fill when stale; cached)
    hist_map: Dict[str, Optional[pd.DataFrame]] = {}
    src_map: Dict[str, str] = {}

    def _fetch(c6: str):
        try:
            return c6, *load_hist_to_t(c6, t_date)
        except Exception as e:  # noqa: BLE001
            return c6, None, f"err:{type(e).__name__}"

    with ThreadPoolExecutor(max_workers=6) as ex:
        for c6, df, s in ex.map(_fetch, codes):
            hist_map[c6] = df
            src_map[c6] = s
    if verbose:
        from collections import Counter
        print(f"hist sources: {dict(Counter(s.split(':')[0] for s in src_map.values()))} ({time.time()-t0:.0f}s)", flush=True)

    def _fresh_load_hist(code6: str, td: str):
        df = hist_map.get(code6)
        if df is None:
            return None
        df = df[df["date"] <= pd.Timestamp(td)].reset_index(drop=True)
        return df if len(df) >= 30 else None

    store = {} if skip_exit_persist else _load_store()
    prior = _prior_for(store, t_date)
    new_states: Dict[str, Dict[str, Any]] = dict(prior)
    exit_eng = ExitEngine()
    orig_load = pc3.load_hist
    pc3.load_hist = _fresh_load_hist  # reuse evaluate_one with as-of-T bars
    try:
        results = [
            pc3.evaluate_one(c, t_date=t_date, regime_v3=regime_v3, legacy_regime=legacy,
                             risk_off=risk_off, exit_eng=exit_eng, exit_store=new_states)
            for c in cands
        ]
    finally:
        pc3.load_hist = orig_load
    results = sort_results(results)
    plan_counts = pc3.grade_counts(results)

    scope = [(r.code, r.name) for r in results if _scope_ok(r)]
    if workers is None:
        workers = _auto_workers()
    if verbose:
        print(f"Plan counts {plan_counts}; V3.1.7 replay scope={len(scope)} warmup={warmup} workers={workers}", flush=True)
    v317 = run_v317_layer(scope, t_date, warmup=warmup, workers=workers, verbose=verbose)

    rows = decorate(results, v317)
    cnt = counts_v317(rows)

    if not skip_exit_persist:
        _merge_store(store, new_states, t_date)
        _save_store(store)

    ymd = t_date.replace("-", "")
    rv3 = E(regime_v3)
    report_path = ROOT / "report_v317.md"
    report_path.write_text(build_report_md(rows, t_date=t_date, meta=meta, regime_v3=rv3, legacy=legacy,
                                           risk_off=risk_off, cnt=cnt, src=src, warmup=warmup), encoding="utf-8")
    push_path = ROOT / f"push_close_v317_{ymd}.md"
    push_path.write_text(build_push_md(rows, t_date=t_date, meta=meta, regime_v3=rv3, legacy=legacy,
                                       risk_off=risk_off, cnt=cnt), encoding="utf-8")
    cand_path = ROOT / "candidates_v317.json"
    cand_path.write_text(json.dumps(to_payload(rows, t_date=t_date, meta=meta, cnt=cnt, src=src, warmup=warmup),
                                    ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    a317 = aplan = 0
    arch: Dict[str, Any] = {}
    if not no_archive:
        a317 = append_v317_archive(rows, t_date)
        aplan = pc3.append_a_plus_archive(results, t_date)  # Plan A+ archive continuity (same schema)
        try:
            arch = pc3.append_from_market_meta(meta)
        except Exception as e:  # noqa: BLE001
            arch = {"wrote": False, "error": str(e)}

    return {
        "t_date": t_date, "plan_counts": plan_counts, "counts": cnt, "n": len(rows),
        "report": str(report_path), "push": str(push_path), "candidates": str(cand_path),
        "a_plus_v317_rows": a317, "a_plus_plan_rows": aplan, "regime_v3": rv3, "legacy_regime": legacy,
        "risk_off": risk_off, "archive": arch, "elapsed_sec": round(time.time() - t0, 1),
        "v317_errors": {c: v.get("error") for c, v in v317.items() if not v.get("ok")},
        "main": [(d["name"], d["code"], d["grade_v317"], d["maturity"], d["entry_window"], d["trend_return"])
                 for d in split_sections(rows)[SEC_MAIN]],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="V3.1.7 close publish (趋势分析雷达 V3.1.7 观察榜)")
    ap.add_argument("--t-date", default=None, help="YYYY-MM-DD (default: from candidates)")
    ap.add_argument("--candidates", default=None, help="path to candidates_v2(_3).json")
    ap.add_argument("--market-meta", default=None, help="path to market_meta.json")
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="V3.1.7 replay warm-up trading days")
    ap.add_argument("--workers", type=int, default=None, help="process workers for V3.1.7 replay")
    ap.add_argument("--skip-exit-persist", action="store_true", help="do not read/write store_v317.json")
    ap.add_argument("--no-archive", action="store_true", help="dry run: skip history archives")
    args = ap.parse_args(argv)

    t_date = args.t_date
    if not t_date:
        payload, _ = pc3.load_candidates(Path(args.candidates) if args.candidates else None)
        t_date = str(payload.get("t_date") or "")
    if not t_date:
        print("ERROR: --t-date required", file=sys.stderr)
        return 2
    info = run_publish(
        t_date=t_date,
        candidates_path=Path(args.candidates) if args.candidates else None,
        market_meta_path=Path(args.market_meta) if args.market_meta else None,
        skip_exit_persist=bool(args.skip_exit_persist),
        warmup=int(args.warmup), workers=args.workers, no_archive=bool(args.no_archive),
    )
    c = info["counts"]
    print(f"publish_close_v317 t_date={info['t_date']} regime={info['regime_v3']} legacy={info['legacy_regime']} "
          f"risk_off={info['risk_off']} elapsed={info['elapsed_sec']}s")
    print(f"plan counts {info['plan_counts']}")
    print(f"v317 grades {c['grade_v317']}  sections {c['sections']}  evaluated={c['evaluated']} ok={c['evaluated_ok']}")
    for m in info["main"]:
        print(f"  MAIN {m[0]}({m[1]}) {m[2]} {m[3]} {m[4]} TR={None if m[5] is None else round(m[5]*100,1)}%")
    if info["v317_errors"]:
        print(f"v317 errors: {info['v317_errors']}")
    print(f"report:     {info['report']}")
    print(f"push:       {info['push']}")
    print(f"candidates: {info['candidates']}")
    print(f"archive:    v317 rows={info['a_plus_v317_rows']} → {A317_PARQUET}; plan A+ rows={info['a_plus_plan_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
