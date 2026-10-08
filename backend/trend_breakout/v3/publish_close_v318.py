#!/usr/bin/env python3
"""V3.1.8 close publish — 趋势分析雷达 V3.1.8（TrendGrade ⊥ EntryStatus）.

Reuses V3.1.7 as-of replay (state/maturity/realized/follow-through).
Replaces maturity-gated Grade demotion with independent TrendGrade + RAPID_MAIN_RISE.
Boards: 成熟入场榜 / 趋势全榜 / 观察池 / 过热延伸.

Does NOT change live V3.1.7 routines. Observation only. 禁止荐买 / 预测必涨.
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
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from v3 import publish_close_v3 as pc3
from v3 import publish_close_v317 as v317p
from v3.bot import sort_results
from v3.engines import ExitEngine
from v3.engines.entry_signal import EntrySignalEmitter
from v3.engines.rapid_main_rise import detect_rapid_main_rise
from v3.grading_v318 import grade_trend_v318
from v3.regime_map import pair_from_market_meta
from v3.types import TrendBotResult
from v3.v31.config import get_v311_config, get_v318_boards
from v3.v31.entry_window import apply_v318_entry_overlay
from v3.v31.live_v317 import DEFAULT_WARMUP, load_hist_to_t
from v3.v31.types import EntryWindow, ExhaustionLevel

CST = pc3.CST
VERSION = "V3.1.8"
EXIT_STORE_V318 = ROOT / "v3" / "exit_state" / "store_v318.json"
HIST_DIR = ROOT / "history"
A318_PARQUET = HIST_DIR / "a_plus_v318_daily.parquet"
A318_CSV = HIST_DIR / "a_plus_v318_daily.csv"
A318_BY_DATE = HIST_DIR / "a_plus_v318_by_date"
PUSH_MAX_ROWS = 50

GRADE_RANK = {"A+": 0, "A": 1, "B": 2, "C": 3, "WATCH": 4, "FILTER": 5}
EW_RANK = {"PRIME": 0, "CONFIRMED": 1, "FOLLOW": 2, "EXTENDED": 3, "NO_ENTRY": 4}
EXH_PENALTY = {"HIGH": 2, "ELEVATED": 1, "NORMAL": 0}

SEC_MAIN = "MAIN"          # 成熟入场榜
SEC_TREND = "TREND"        # 趋势全榜（互斥展示时：非 MAIN/EXTENDED 的 A–C）
SEC_WATCH = "WATCH_POOL"   # 观察池 B/C + EARLY/BUILDING
SEC_EXTENDED = "EXTENDED"  # 过热延伸 A+/A + EXTENDED
SEC_REST = "REST"
SEC_FILTER = "FILTER"
SEC_CN = {
    SEC_MAIN: "成熟入场榜",
    SEC_TREND: "趋势全榜",
    SEC_WATCH: "观察池",
    SEC_EXTENDED: "过热延伸",
    SEC_REST: "其余",
    SEC_FILTER: "FILTER",
}

E = pc3._enum_val
_f = pc3._f
_note = getattr(v317p, "_note", lambda r, k: None)
_scope_ok = v317p._scope_ok
_auto_workers = v317p._auto_workers
run_v317_layer = v317p.run_v317_layer


def _load_store() -> Dict[str, Any]:
    if EXIT_STORE_V318.exists():
        try:
            return json.loads(EXIT_STORE_V318.read_text(encoding="utf-8"))
        except Exception:
            return {}
    # seed from v317 store if present (rerun-safe, separate file)
    if v317p.EXIT_STORE_V317.exists():
        try:
            return json.loads(v317p.EXIT_STORE_V317.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _prior_for(store, t_date):
    return v317p._prior_for(store, t_date)


def _merge_store(store, new_states, t_date):
    return v317p._merge_store(store, new_states, t_date)


def _save_store(store):
    EXIT_STORE_V318.parent.mkdir(parents=True, exist_ok=True)
    EXIT_STORE_V318.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _entry_signal_level(r: TrendBotResult, v: Dict[str, Any], broken: bool, ew: str) -> Tuple[bool, List[str]]:
    """Mature entry = V317 current rule but EntryWindow from V318 overlay."""
    em = EntrySignalEmitter(config=get_v311_config(), version="3.1.7")
    ok, tags = em._rule_ok(
        rule="current",
        state=str(v.get("state") or "").upper(),
        ew=str(ew or "").upper(),
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


def decorate(
    results: Sequence[TrendBotResult], v317: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    boards = get_v318_boards()
    mature_g = set(boards.get("mature_entry_grades") or ("A+", "A"))
    mature_ew = set(boards.get("mature_entry_windows") or ("PRIME", "CONFIRMED"))
    trend_g = set(boards.get("trend_full_grades") or ("A+", "A", "B", "C"))
    watch_g = set(boards.get("watch_pool_grades") or ("B", "C"))
    watch_m = set(boards.get("watch_pool_maturity") or ("EARLY", "BUILDING"))
    ext_g = set(boards.get("extended_grades") or ("A+", "A"))

    rows: List[Dict[str, Any]] = []
    for r in results:
        plan = E(r.grade, "FILTER")
        v = v317.get(r.code) or {}
        evaluated = r.code in v317
        ok = bool(v.get("ok"))
        broken = bool(E(r.exit_signal) == "BROKEN") or E(r.risk_level) == "BROKEN"
        state = str(v.get("state") or "") if ok else ""
        mat = str(v.get("maturity_level") or "") if ok else ""
        ew317 = str(v.get("entry_window") or "") if ok else ""
        exh = str(v.get("exhaustion") or "") if ok else ""
        trend_alive = ok and state not in v317p.DEAD_STATES and not broken

        rapid = detect_rapid_main_rise(
            trend_state=state,
            return20=_f(v.get("return20")),
            trend_return=_f(v.get("trend_return")),
            trend_age_days=int(v.get("trend_age") or 0) if ok else None,
            continuity_score=r.continuity_score,
            ma5_gt_ma10_gt_ma20=v.get("ma5_gt_ma10_gt_ma20"),
            higher_low_intact=(
                bool(v.get("struct_higherLow"))
                if v.get("struct_higherLow") is not None
                else (None if v.get("higher_low_broken") is None else (not bool(v.get("higher_low_broken"))))
            ),
            higher_high=bool(v.get("struct_reBreakout") or v.get("struct_firstBreakout")),
            vol_ratio=_f(v.get("vol_ratio")),
            exhaustion=exh,
            broken=broken or state == "TREND_FAILURE",
        )

        gres = grade_trend_v318(
            trend_structure_score=_f(v.get("trend_structure_score")) if ok else None,
            continuity_score=r.continuity_score,
            sector_score=r.sector_score,
            tech_score=r.tech_score,
            fund_risk_level=str(v.get("fund_risk") or "") if ok else None,
            trend_state=state or None,
            broken=broken,
            rapid_main_rise=bool(rapid.active),
            htf_ok=bool(v.get("htf_ok")) if ok else None,
            legacy_grade=plan,
        )
        g318 = gres.grade.value

        try:
            ew_enum = EntryWindow(ew317) if ew317 else EntryWindow.NO_ENTRY
        except Exception:
            ew_enum = EntryWindow.NO_ENTRY
        ew318_enum, ew_notes = apply_v318_entry_overlay(
            entry_window=ew_enum,
            maturity_level=mat or "EARLY",
            rapid_main_rise=bool(rapid.active),
            exhaustion=ExhaustionLevel.coerce(exh or "NORMAL"),
            notes=[],
        )
        ew = ew318_enum.value if ok else ""

        es_ok, es_tags = (False, [])
        if ok:
            es_ok, es_tags = _entry_signal_level(r, v, broken, ew)
        # Mature entry board also requires grade A+/A (independent of EntrySignal alone)
        mature_ok = bool(es_ok and g318 in mature_g and ew in mature_ew)

        # Mutually exclusive section for push layout
        if mature_ok:
            section = SEC_MAIN
        elif g318 in ext_g and ew == "EXTENDED" and trend_alive:
            section = SEC_EXTENDED
        elif g318 in watch_g and mat in watch_m and trend_alive:
            section = SEC_WATCH
        elif g318 in trend_g and not broken and state != "TREND_FAILURE":
            section = SEC_TREND
        elif g318 == "FILTER":
            section = SEC_FILTER
        else:
            section = SEC_REST

        # Trend full board: Grade A–C; as-of optional (Plan B/C without replay still visible)
        in_trend_full = g318 in trend_g and not broken and state != "TREND_FAILURE"
        in_watch_pool = g318 in watch_g and mat in watch_m and trend_alive
        in_extended = g318 in ext_g and ew == "EXTENDED" and trend_alive

        rows.append({
            "r": r,
            "code": r.code,
            "name": r.name,
            "plan_grade": plan,
            "legacy_grade": plan,
            "grade_v317": None,  # filled optionally later
            "grade_v318": g318,
            "grade_path": gres.path,
            "grade_tags": list(gres.tags),
            "grade_reasons": list(gres.reasons),
            "structure_score": gres.structure_score,
            "structure_source": gres.structure_source,
            "rapid_main_rise": bool(rapid.active),
            "rapid_path": rapid.path,
            "section": section,
            "in_trend_full": in_trend_full,
            "in_watch_pool": in_watch_pool,
            "in_extended": in_extended,
            "in_mature_entry": mature_ok,
            "evaluated": evaluated,
            "v318_ok": ok,
            "v318_error": v.get("error"),
            "trend_state": state or None,
            "maturity": mat or None,
            "maturity_score": v.get("maturity_score") if ok else None,
            "entry_window": ew or None,
            "entry_window_v317": ew317 or None,
            "entry_window_notes": ew_notes,
            "entry_signal": bool(mature_ok),
            "entry_signal_raw": bool(es_ok),
            "entry_signal_tags": es_tags,
            "trend_return": _f(v.get("trend_return")) if ok else None,
            "wave_return": _f(v.get("wave_return")) if ok else None,
            "return20": _f(v.get("return20")) if ok else None,
            "return40": _f(v.get("return40")) if ok else None,
            "return60": _f(v.get("return60")) if ok else None,
            "realized_score": _f(v.get("realized_score")) if ok else None,
            "realized_floor_ok": bool(v.get("realized_floor_ok")) if ok else None,
            "follow_through": v.get("follow_through") if ok else None,
            "main_rise_quality": _f(v.get("main_rise_quality")) if ok else None,
            "trend_structure_score": _f(v.get("trend_structure_score")) if ok else None,
            "trend_age": int(v.get("trend_age") or 0) if ok else None,
            "exhaustion": exh or None,
            "fund_risk": v.get("fund_risk") if ok else None,
            "htf_ok": bool(v.get("htf_ok")) if ok else None,
            "warmup_start": v.get("warmup_start"),
            "hist_source": v.get("hist_source"),
        })
    return rows


def _sort_trend(d: Dict[str, Any]):
    r = d["r"]
    exh_p = EXH_PENALTY.get(str(d.get("exhaustion") or "NORMAL").upper(), 0)
    return (
        GRADE_RANK.get(d["grade_v318"], 9),
        -(float(d["structure_score"]) if d.get("structure_score") is not None else -1),
        -(float(r.continuity_score) if r.continuity_score is not None else -1),
        -(float(d["maturity_score"]) if d.get("maturity_score") is not None else -1),
        -(float(d["realized_score"]) if d.get("realized_score") is not None else -1),
        -(float(r.sector_score) if r.sector_score is not None else -1),
        exh_p,
        r.code,
    )


def _main_key(d: Dict[str, Any]):
    ew_rank = EW_RANK.get(str(d.get("entry_window") or ""), 9)
    return (GRADE_RANK.get(d["grade_v318"], 9), ew_rank) + _sort_trend(d)[1:]


def split_sections(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in SEC_CN}
    for d in rows:
        out[d["section"]].append(d)
    out[SEC_MAIN].sort(key=_main_key)
    for k in (SEC_TREND, SEC_WATCH, SEC_EXTENDED, SEC_REST, SEC_FILTER):
        out[k].sort(key=_sort_trend)
    return out


def trend_full_list(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted([d for d in rows if d.get("in_trend_full")], key=_sort_trend)


def counts_v318(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    g = {k: 0 for k in GRADE_RANK}
    p = {k: 0 for k in GRADE_RANK}
    s = {k: 0 for k in SEC_CN}
    for d in rows:
        g[d["grade_v318"]] = g.get(d["grade_v318"], 0) + 1
        p[d["plan_grade"]] = p.get(d["plan_grade"], 0) + 1
        s[d["section"]] += 1
    return {
        "grade_v318": g,
        "plan_grade": p,
        "sections": s,
        "trend_full": sum(1 for d in rows if d.get("in_trend_full")),
        "watch_pool": sum(1 for d in rows if d.get("in_watch_pool")),
        "extended_board": sum(1 for d in rows if d.get("in_extended")),
        "mature_entry": sum(1 for d in rows if d.get("in_mature_entry")),
        "rapid_count": sum(1 for d in rows if d.get("rapid_main_rise")),
        "evaluated": sum(1 for d in rows if d["evaluated"]),
        "evaluated_ok": sum(1 for d in rows if d["v318_ok"]),
    }


HEADERS = [
    "股票", "阶段", "TrendGrade", "成熟度", "EntryStatus", "路径", "趋势涨幅%", "波段涨幅%",
    "Struct", "Tech", "Cont", "Sector", "消耗", "风险", "保护价", "破坏价", "ExitSignal",
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
        path = "RAPID" if d.get("rapid_main_rise") else (d.get("grade_path") or "—")
        lines.append("| " + " | ".join([
            stock,
            E(r.lifecycle, "NONE"),
            d["grade_v318"],
            d.get("maturity") or "—",
            d.get("entry_window") or "—",
            path,
            _pct(d.get("trend_return")),
            _pct(d.get("wave_return")),
            _fs(d.get("structure_score") if d.get("structure_score") is not None else d.get("trend_structure_score")),
            _fs(r.tech_score),
            _fs(r.continuity_score),
            _fs(r.sector_score),
            _fs(r.consumption_score),
            E(r.risk_level, "WATCH"),
            _fs(r.protection_price, 2),
            _fs(r.break_price, 2),
            E(r.exit_signal, "HOLD"),
        ]) + " |")
    return "\n".join(lines)


def _header_lines(t_date, meta, regime_v3, legacy, risk_off, cnt) -> List[str]:
    s = cnt["sections"]
    g = cnt["grade_v318"]
    return [
        f"regime={regime_v3}（legacy {legacy}，risk_off={risk_off}）  MarketScore={meta.get('market_score', '—')}",
        f"成熟入场榜={cnt['mature_entry']}  趋势全榜={cnt['trend_full']}  观察池={cnt['watch_pool']}  "
        f"过热延伸={cnt['extended_board']}  RAPID={cnt['rapid_count']}",
        f"分区展示：MAIN={s[SEC_MAIN]} TREND={s[SEC_TREND]} WATCH={s[SEC_WATCH]} EXT={s[SEC_EXTENDED]} "
        f"REST={s[SEC_REST]} FILTER={s[SEC_FILTER]}",
        f"V3.1.8 TrendGrade：A+={g.get('A+',0)} A={g.get('A',0)} B={g.get('B',0)} C={g.get('C',0)} "
        f"WATCH={g.get('WATCH',0)} FILTER={g.get('FILTER',0)}",
        "",
        "> 观察池；A+≠今天必买；PRIME/CONFIRMED 才是成熟入场窗口。禁止荐买 / 预测必涨。",
    ]


def build_push_md(rows, *, t_date, meta, regime_v3, legacy, risk_off, cnt) -> str:
    secs = split_sections(rows)
    ymd = t_date.replace("-", "")
    lines = [f"# 📡 趋势分析雷达 V3.1.8 观察榜 {t_date}", ""]
    lines += _header_lines(t_date, meta, regime_v3, legacy, risk_off, cnt)
    lines.append("")
    budget = PUSH_MAX_ROWS

    main = secs[SEC_MAIN]
    lines += [f"## 成熟入场榜（{len(main)}）", ""]
    if main:
        lines += [table(main), ""]
    else:
        lines += ["今日无满足 成熟入场榜（TrendGrade∈{A+,A} 且 EntryStatus∈{PRIME,CONFIRMED}）的标的。", ""]
    budget -= len(main)

    # 趋势全榜（完整 A–C，含 MAIN/EXT/WATCH 重叠票，截断展示）
    full = trend_full_list(rows)
    show_full = full[: max(0, min(budget, 25))]
    lines += [f"## 趋势全榜（{len(full)}，展示 {len(show_full)}）", ""]
    if show_full:
        lines += [table(show_full), ""]
        budget -= len(show_full)
        if len(show_full) < len(full):
            lines += [f"> 另 {len(full) - len(show_full)} 只见 `report_v318.md`。", ""]
    else:
        lines += ["（无）", ""]

    for key, title in (
        (SEC_WATCH, "观察池（B/C + EARLY/BUILDING）"),
        (SEC_EXTENDED, "过热延伸（A+/A + EXTENDED）"),
    ):
        # Prefer membership flags for these boards (may overlap trend full)
        if key == SEC_WATCH:
            items = sorted([d for d in rows if d.get("in_watch_pool")], key=_sort_trend)
        else:
            items = sorted([d for d in rows if d.get("in_extended")], key=_sort_trend)
        if not items:
            continue
        show = items[: max(0, budget)]
        lines += [f"## {title}（{len(items)}）", ""]
        if show:
            lines += [table(show), ""]
            budget -= len(show)
        if len(show) < len(items):
            lines += [f"> 另 {len(items) - len(show)} 只已折叠。", ""]

    filt_n = sum(1 for d in rows if d["section"] == SEC_FILTER)
    if filt_n:
        lines += [f"> FILTER {filt_n} 只已折叠。", ""]
    lines += [f"全文：`report_v318.md` / `push_close_v318_{ymd}.md`", ""]
    return "\n".join(lines)


def build_report_md(rows, *, t_date, meta, regime_v3, legacy, risk_off, cnt, src, warmup) -> str:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    conclusions = meta.get("market_conclusion") or []
    if not isinstance(conclusions, list):
        conclusions = [str(conclusions)]
    lines = [
        f"# 📡 趋势分析雷达 V3.1.8 观察榜 {t_date}", "",
        f"**日期：** {t_date}（T日收盘）  **生成时间：** {now} CST", "",
    ]
    lines += _header_lines(t_date, meta, regime_v3, legacy, risk_off, cnt)
    lines += ["", "## 今日市场结论", ""]
    lines += [f"- {c}" for c in conclusions] or ["- —"]
    lines += [
        "",
        "## 分区规则（V3.1.8）",
        "",
        "- **成熟入场榜**：TrendGrade∈{A+,A} AND EntryStatus∈{PRIME,CONFIRMED}（保持严格）。",
        "- **趋势全榜**：TrendGrade∈{A+,A,B,C}；排序 Grade→Structure→Cont→MaturityScore→Realized→Sector→Exhaustion惩罚。",
        "- **观察池**：Grade∈{B,C} + Maturity∈{EARLY,BUILDING}。",
        "- **过热延伸**：Grade∈{A+,A} + EntryStatus=EXTENDED（趋势未失效）。",
        "- **TrendGrade** 独立于 Maturity/Entry/Realized；**RAPID_MAIN_RISE** 可在 BUILDING 给 A+/A + FOLLOW。",
        "- A+ ≠ 今天必买；PRIME 才是成熟窗口。",
        "",
    ]
    main = [d for d in rows if d.get("in_mature_entry")]
    full = trend_full_list(rows)
    watch = sorted([d for d in rows if d.get("in_watch_pool")], key=_sort_trend)
    ext = sorted([d for d in rows if d.get("in_extended")], key=_sort_trend)
    for title, items in (
        ("成熟入场榜", main),
        ("趋势全榜", full),
        ("观察池", watch),
        ("过热延伸", ext),
    ):
        lines += [f"## {title}（{len(items)}）", ""]
        lines += [table(items) if items else "（无）", ""]
    rapid_rows = [d for d in rows if d.get("rapid_main_rise")]
    if rapid_rows:
        lines += ["## RAPID_MAIN_RISE 摘录", ""]
        for d in sorted(rapid_rows, key=_sort_trend)[:20]:
            lines.append(
                f"- {d['name']}({d['code']}) Grade={d['grade_v318']} Mat={d.get('maturity')} "
                f"EW={d.get('entry_window')} TR={_pct(d.get('trend_return'))}%"
            )
        lines.append("")
    lines += [
        "---", "",
        f"*V3.1.8：候选 `{Path(src).name}`；as-of 回放 {cnt['evaluated']} 只（成功 {cnt['evaluated_ok']}），"
        f"预热 {warmup} 日。现网 V3.1.7 例程未改。*",
        "",
    ]
    return "\n".join(lines)


def _row_json(d: Dict[str, Any]) -> Dict[str, Any]:
    r: TrendBotResult = d["r"]
    out = {
        "t_date": r.t_date, "code": r.code, "code6": r.code, "name": r.name,
        "grade": d["grade_v318"], "grade_v318": d["grade_v318"], "plan_grade": d["plan_grade"],
        "legacy_grade": d.get("legacy_grade"), "grade_path": d.get("grade_path"),
        "section": d["section"], "section_cn": SEC_CN.get(d["section"], d["section"]),
        "in_mature_entry": d.get("in_mature_entry"), "in_trend_full": d.get("in_trend_full"),
        "in_watch_pool": d.get("in_watch_pool"), "in_extended": d.get("in_extended"),
        "rapid_main_rise": d.get("rapid_main_rise"),
        "lifecycle": E(r.lifecycle), "phase_cn": _note(r, "phase_cn"), "industry": _note(r, "industry"),
        "tech_score": r.tech_score, "fund_score": r.fund_score, "sector_score": r.sector_score,
        "continuity_score": r.continuity_score, "consumption_score": r.consumption_score,
        "risk_level": E(r.risk_level), "protection_price": r.protection_price, "break_price": r.break_price,
        "warning_price": r.warning_price, "exit_signal": E(r.exit_signal), "close": r.close,
        "regime_v3": E(r.regime_v3), "legacy_regime": r.legacy_regime, "risk_off": bool(r.risk_off),
        "data_status": E(r.data_status), "reasons": list(r.reasons or []), "risks": list(r.risks or []),
    }
    for k in (
        "evaluated", "v318_ok", "v318_error", "trend_state", "maturity", "maturity_score",
        "entry_window", "entry_window_v317", "entry_signal", "entry_signal_raw", "entry_signal_tags",
        "trend_return", "wave_return", "return20", "return40", "return60", "realized_score",
        "realized_floor_ok", "follow_through", "main_rise_quality", "trend_structure_score",
        "structure_score", "structure_source", "trend_age", "exhaustion", "fund_risk", "htf_ok",
        "warmup_start", "hist_source", "grade_tags", "grade_reasons",
    ):
        out[k] = d.get(k)
    return out


def to_payload(rows, *, t_date, meta, cnt, src, warmup) -> Dict[str, Any]:
    pair = pair_from_market_meta(meta)
    allr = [_row_json(d) for d in sorted(rows, key=_sort_trend)]
    return {
        "t_date": t_date,
        "model": "V3.1.8_trend_radar_publish",
        "title": f"趋势分析雷达 V3.1.8 观察榜 {t_date}",
        "source_candidates": str(src),
        "regime_v3": E(pair.regime_v3), "legacy_regime": pair.legacy_regime, "risk_off": bool(pair.risk_off),
        "market_score": meta.get("market_score"), "market_conclusion": meta.get("market_conclusion"),
        "counts": cnt, "warmup_days": warmup, "n": len(allr),
        "built_at": datetime.now(CST).isoformat(timespec="seconds"),
        "disclaimer": "观察池；禁止荐买 / 预测必涨；A+≠必买；成熟入场榜=A+/A+PRIME/CONFIRMED",
        "MAIN": [_row_json(d) for d in rows if d.get("in_mature_entry")],
        "TREND_FULL": [_row_json(d) for d in trend_full_list(rows)],
        "WATCH_POOL": [_row_json(d) for d in rows if d.get("in_watch_pool")],
        "EXTENDED": [_row_json(d) for d in rows if d.get("in_extended")],
        "results": allr,
        "meta": {k: meta.get(k) for k in ("t_date", "regime_v2", "regime", "market_score", "cap", "breadth",
                                           "turnover", "indices", "market_conclusion")},
    }


def append_v318_archive(rows: Sequence[Dict[str, Any]], t_date: str) -> int:
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    A318_BY_DATE.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(rows, key=_sort_trend):
        if d["grade_v318"] not in ("A+", "A") and not d.get("rapid_main_rise"):
            continue
        r = d["r"]
        out.append({
            "t_date": t_date, "code6": r.code, "name": r.name, "grade_v318": d["grade_v318"],
            "plan_grade": d["plan_grade"], "section": d["section"], "maturity": d.get("maturity"),
            "entry_window": d.get("entry_window"), "entry_signal": bool(d.get("entry_signal")),
            "rapid_main_rise": bool(d.get("rapid_main_rise")),
            "trend_state": d.get("trend_state"), "trend_return": d.get("trend_return"),
            "wave_return": d.get("wave_return"), "trend_age": d.get("trend_age"),
            "realized_score": d.get("realized_score"), "structure_score": d.get("structure_score"),
            "follow_through": d.get("follow_through"), "exhaustion": d.get("exhaustion"),
            "tech": r.tech_score, "fund": r.fund_score, "sector": r.sector_score,
            "cont": r.continuity_score, "close": r.close,
            "protection_price": r.protection_price, "break_price": r.break_price,
            "exit_signal": E(r.exit_signal),
        })
    day = pd.DataFrame(out)
    day.to_csv(A318_BY_DATE / f"{t_date}.csv", index=False, encoding="utf-8")
    if A318_PARQUET.exists():
        old = pd.read_parquet(A318_PARQUET)
        old = old[old["t_date"].astype(str) != t_date]
        all_df = pd.concat([old, day], ignore_index=True) if len(day) else old
    else:
        all_df = day
    if len(all_df):
        all_df = all_df.sort_values(["t_date", "code6"])
        all_df.to_parquet(A318_PARQUET, index=False)
        all_df.to_csv(A318_CSV, index=False, encoding="utf-8")
    return len(out)


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
        raise ValueError(f"candidates t_date={cand_t} != --t-date {t_date}")

    meta = pc3.load_market_meta(market_meta_path, payload, t_date)
    pair = pair_from_market_meta(meta)
    regime_v3, legacy, risk_off = pair.regime_v3, pair.legacy_regime, bool(pair.risk_off)

    cands = pc3.collect_scored(payload)
    codes = [pc3._norm_code(c.get("code6") or c.get("code")) for c in cands]

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
    pc3.load_hist = _fresh_load_hist
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
        print(f"Plan counts {plan_counts}; V3.1.8 replay scope={len(scope)} warmup={warmup} workers={workers}", flush=True)
    v317 = run_v317_layer(scope, t_date, warmup=warmup, workers=workers, verbose=verbose)

    rows = decorate(results, v317)
    cnt = counts_v318(rows)

    if not skip_exit_persist:
        _merge_store(store, new_states, t_date)
        _save_store(store)

    ymd = t_date.replace("-", "")
    rv3 = E(regime_v3)
    report_path = ROOT / "report_v318.md"
    report_path.write_text(build_report_md(rows, t_date=t_date, meta=meta, regime_v3=rv3, legacy=legacy,
                                           risk_off=risk_off, cnt=cnt, src=src, warmup=warmup), encoding="utf-8")
    push_path = ROOT / f"push_close_v318_{ymd}.md"
    push_path.write_text(build_push_md(rows, t_date=t_date, meta=meta, regime_v3=rv3, legacy=legacy,
                                       risk_off=risk_off, cnt=cnt), encoding="utf-8")
    cand_path = ROOT / "candidates_v318.json"
    cand_path.write_text(json.dumps(to_payload(rows, t_date=t_date, meta=meta, cnt=cnt, src=src, warmup=warmup),
                                    ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    a318 = 0
    if not no_archive:
        a318 = append_v318_archive(rows, t_date)

    return {
        "t_date": t_date, "plan_counts": plan_counts, "counts": cnt, "n": len(rows),
        "report": str(report_path), "push": str(push_path), "candidates": str(cand_path),
        "a_plus_v318_rows": a318, "regime_v3": rv3, "legacy_regime": legacy,
        "risk_off": risk_off, "elapsed_sec": round(time.time() - t0, 1),
        "v318_errors": {c: v.get("error") for c, v in v317.items() if not v.get("ok")},
        "main": [(d["name"], d["code"], d["grade_v318"], d["maturity"], d["entry_window"], d["trend_return"])
                 for d in rows if d.get("in_mature_entry")],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="V3.1.8 close publish")
    ap.add_argument("--t-date", default=None)
    ap.add_argument("--candidates", default=None)
    ap.add_argument("--market-meta", default=None)
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--skip-exit-persist", action="store_true")
    ap.add_argument("--no-archive", action="store_true")
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
    print(f"publish_close_v318 t_date={info['t_date']} regime={info['regime_v3']} "
          f"elapsed={info['elapsed_sec']}s")
    print(f"plan counts {info['plan_counts']}")
    print(f"v318 grades {c['grade_v318']}  mature={c['mature_entry']} trend_full={c['trend_full']} "
          f"watch={c['watch_pool']} ext={c['extended_board']} rapid={c['rapid_count']}")
    for m in info["main"]:
        print(f"  MAIN {m[0]}({m[1]}) {m[2]} {m[3]} {m[4]} TR={None if m[5] is None else round(m[5]*100,1)}%")
    print(f"report: {info['report']}")
    print(f"push:   {info['push']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
