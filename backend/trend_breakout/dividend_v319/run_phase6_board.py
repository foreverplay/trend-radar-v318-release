#!/usr/bin/env python3
"""
Phase6–11: Valuation + Technical + Risk + Opportunity/Grade/Entry → 红利趋势榜.

Usage:
  cd <TREND_BREAKOUT_ROOT>
  python -m dividend_v319.run_phase6_board
  python -m dividend_v319.run_phase6_board --as-of 2025-12-31
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIV_ROOT, DIVIDEND_CONFIG, OUT_DIR
from dividend_v319.engines.dividendEntryEngine import DividendEntryEngine
from dividend_v319.engines.dividendRiskEngine import DividendRiskEngine
from dividend_v319.engines.dividendTechnicalEngine import DividendTechnicalEngine
from dividend_v319.engines.dividendTrendEngine import (
    assign_grade,
    historical_total_return_score,
    opportunity_score,
)
from dividend_v319.engines.dividendValuationEngine import DividendValuationEngine

CST = ZoneInfo("Asia/Shanghai")


def _z6(c: str) -> str:
    return str(c).strip().zfill(6)


def summarize(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"n": 0}
    a = np.array(vals, dtype=float)
    return {
        "n": int(len(a)),
        "mean": float(a.mean()),
        "std": float(a.std()),
        "p10": float(np.percentile(a, 10)),
        "p25": float(np.percentile(a, 25)),
        "p50": float(np.percentile(a, 50)),
        "p75": float(np.percentile(a, 75)),
        "p90": float(np.percentile(a, 90)),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def load_phase3(as_of: str) -> List[Dict[str, Any]]:
    path = OUT_DIR / "phase3" / f"scores_{as_of.replace('-', '')}.json"
    if not path.exists():
        raise FileNotFoundError(f"Phase3 scores missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["rows"]


def profit_cash_flags(row: Dict[str, Any]) -> tuple:
    flags = set(row.get("unhealthy_flags") or [])
    profit = any(
        x in flags
        for x in (
            "profit_collapse",
            "profit_decline",
            "ni_deteriorating",
            "earnings_decline",
        )
    ) or any("profit" in f and ("declin" in f or "fall" in f or "collapse" in f) for f in flags)
    cash = any(
        x in flags
        for x in (
            "negative_fcf_vs_dividend",
            "ocf_persistently_negative",
            "weak_cash",
        )
    ) or any("fcf" in f or "ocf" in f or "cash" in f for f in flags)
    # also use sustainability components if present
    comps = row.get("sustainability_components") or {}
    if comps.get("profit_continuity") is not None and comps["profit_continuity"] < 40:
        profit = True
    if comps.get("fcf_over_div") is not None and comps["fcf_over_div"] < 35:
        cash = True
    if comps.get("ocf_over_ni") is not None and comps["ocf_over_ni"] < 35:
        cash = True
    return profit, cash


# 趋势观察优质评级：A+ / A。B、C、未入观察池都不算。
TREND_QUALITY_GRADES = ("A+", "A")


def load_trend_observation_grades(as_of: str) -> Dict[str, str]:
    """当日趋势观察 TrendGrade。没有同日完整发布时返回空表，调用方不额外清空三区。"""
    day = (as_of or "")[:10]
    if len(day) != 10:
        return {}
    trend_root = DIV_ROOT.parent
    candidates = trend_root / "candidates_v318.json"
    if candidates.is_file():
        try:
            payload = json.loads(candidates.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and str(payload.get("t_date") or "")[:10] == day:
            grades: Dict[str, str] = {}
            for row in payload.get("results") or []:
                code = _z6(row.get("code6") or row.get("code") or "")
                grade = str(row.get("grade_v318") or row.get("grade") or "").strip()
                if code.strip("0") and grade:
                    grades[code] = grade
            if grades:
                return grades

    archive = trend_root / "history" / "a_plus_v318_by_date" / f"{day}.csv"
    if archive.is_file():
        grades = {}
        for line in archive.read_text(encoding="utf-8").splitlines()[1:]:
            cols = [c.strip() for c in line.split(",")]
            if len(cols) < 4:
                continue
            code = _z6(cols[1])
            grade = cols[3]
            if code.strip("0") and grade:
                grades[code] = grade
        if grades:
            return grades

    report = trend_root / "report_v318.md"
    if not report.is_file():
        return {}
    text = report.read_text(encoding="utf-8")
    if day not in text[:500]:
        return {}
    rank = {"A+": 5, "A": 4, "B": 3, "C": 2, "WATCH": 1, "FILTER": 0}
    grades = {}
    for line in text.splitlines():
        if not line.startswith("|") or "股票" in line or set(line) <= set("|-: "):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3:
            continue
        code = ""
        head = cols[0]
        if "(" in head and head.endswith(")"):
            code = _z6(head[head.rfind("(") + 1 : -1])
        grade = cols[2]
        if not code.strip("0") or grade not in rank:
            continue
        prev = grades.get(code)
        if prev is None or rank[grade] > rank[prev]:
            grades[code] = grade
    return grades


def board_bucket(
    row: Dict[str, Any],
    trend_grade: Optional[str] = None,
    *,
    require_trend_quality: bool = False,
) -> List[str]:
    """A name may appear in multiple boards.

    有当日趋势观察评级时，三区都要求优质评级（A+ / A）。
    当前机会的红利等级也至少为 A（不含 B）。
    """
    tags = []
    if require_trend_quality and trend_grade not in TREND_QUALITY_GRADES:
        return tags

    grade = row.get("dividend_grade")
    risk = row.get("dividend_risk")
    trap = row.get("dividend_yield_trap")
    entry = row.get("dividend_entry_status")
    q = row.get("dividend_quality_score") or 0
    s = row.get("dividend_sustainability_score") or 0

    if trap or risk == "HIGH" or (
        risk == "WARNING" and (q < 70 or s < 55)
    ):
        tags.append("风险观察")

    if grade in ("A+", "A") and s >= 70 and risk in ("SAFE", "WATCH"):
        tags.append("优质红利")

    y = row.get("dividend_yield_ttm")
    y_ok = y is None or y >= 0.01  # TTM股息率过低不进当前机会（仍可留在优质红利）
    if (
        entry
        in (
            "TREND_CONFIRM",
            "ACCUMULATE_WATCH",
            "PULLBACK_WATCH",
        )
        and grade in TREND_QUALITY_GRADES
        and risk in ("SAFE", "WATCH")
        and not trap
        and y_ok
    ):
        tags.append("当前机会")

    return tags


def to_markdown(payload: Dict[str, Any]) -> str:
    as_of = payload["as_of"]
    gc = payload["grade_counts"]
    lines = [
        f"# 红利趋势榜 V3.1.9 Phase6–11（as_of={as_of}）",
        "",
        f"- generated_at: {payload['generated_at']}（Asia/Shanghai）",
        f"- universe_scored: {payload['n_scored']}",
        f"- 非荐买；操作提示不含「强烈买入/必涨/稳赚」",
        f"- TotalReturn：有bfq用真TR；无bfq用 price_qfq_only 弱代理（向50压缩），**禁止 qfq+现金**",
        f"- 技术趋势：qfq MA 结构（与现金TR口径分离）",
        "",
        "## 等级计数",
        "",
        "| 等级 | 数量 |",
        "|---|---:|",
    ]
    for g in ("A+", "A", "B", "C", "FILTER"):
        lines.append(f"| {g} | {gc.get(g, 0)} |")
    lines += [
        "",
        f"- Risk: {json.dumps(payload.get('risk_counts', {}), ensure_ascii=False)}",
        f"- Entry: {json.dumps(payload.get('entry_counts', {}), ensure_ascii=False)}",
        f"- return_proxy: {json.dumps(payload.get('return_proxy_counts', {}), ensure_ascii=False)}",
        f"- DIVIDEND_TRAP flags: {payload.get('n_trap', 0)}",
        "",
        "## 当前机会 Top15（按 EntryOpportunityRank）",
        "",
        "| Rank | 代码 | 名称 | Grade | Opp | Q | S | Trend | Val | Risk | Entry | 股息率TTM | 行业组 |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|---|---:|---|",
    ]
    for r in payload.get("top_entry15", []):
        y = r.get("dividend_yield_ttm")
        y_s = "" if y is None else f"{y*100:.2f}%"
        lines.append(
            f"| {r.get('entry_opportunity_rank')} | {r['symbol']} | {r.get('name','')} | "
            f"{r.get('dividend_grade')} | {r.get('dividend_opportunity_score')} | "
            f"{r.get('dividend_quality_score')} | {r.get('dividend_sustainability_score')} | "
            f"{r.get('dividend_trend_score')} | {r.get('valuation_score')} | "
            f"{r.get('dividend_risk')} | {r.get('dividend_entry_status')} | {y_s} | "
            f"{r.get('industry_group','')} |"
        )

    lines += [
        "",
        "## 优质红利 Top10（按 QualityRank）",
        "",
        "| Rank | 代码 | 名称 | Grade | Q | S | Opp | Risk |",
        "|---:|---|---|---|---:|---:|---:|---|",
    ]
    for r in payload.get("top_quality10", []):
        lines.append(
            f"| {r.get('quality_rank')} | {r['symbol']} | {r.get('name','')} | "
            f"{r.get('dividend_grade')} | {r.get('dividend_quality_score')} | "
            f"{r.get('dividend_sustainability_score')} | {r.get('dividend_opportunity_score')} | "
            f"{r.get('dividend_risk')} |"
        )

    lines += [
        "",
        "## 风险观察示例",
        "",
        "| 代码 | 名称 | Risk | Trap | Q | S | Opp | 主要原因 |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for r in payload.get("risk_examples", []):
        reason = "；".join((r.get("reasons") or [])[:2]).replace("|", "/")
        lines.append(
            f"| {r['symbol']} | {r.get('name','')} | {r.get('dividend_risk')} | "
            f"{r.get('dividend_yield_trap')} | {r.get('dividend_quality_score')} | "
            f"{r.get('dividend_sustainability_score')} | {r.get('dividend_opportunity_score')} | "
            f"{reason[:100]} |"
        )

    lines += ["", "## 已知偏差", ""]
    for g in payload.get("known_gaps", []):
        lines.append(f"- {g}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2025-12-31")
    ap.add_argument("--limit", type=int, default=0, help="debug limit; 0=all")
    args = ap.parse_args()
    as_of = args.as_of

    t0 = time.time()
    phase3_rows = load_phase3(as_of)
    if args.limit and args.limit > 0:
        phase3_rows = phase3_rows[: args.limit]

    val_eng = DividendValuationEngine(DIVIDEND_CONFIG)
    tech_eng = DividendTechnicalEngine(DIVIDEND_CONFIG)
    risk_eng = DividendRiskEngine()
    entry_eng = DividendEntryEngine()

    # Pass A: technical first (need industry 1y medians)
    print(f"tech pass {len(phase3_rows)} …", flush=True)
    tech_tmp: Dict[str, Any] = {}
    ret_by_group: Dict[str, List[float]] = defaultdict(list)
    for i, row in enumerate(phase3_rows):
        code = _z6(row["symbol"])
        grp = val_eng.industry_group(code)
        tr = tech_eng.score(code, as_of, industry_group=grp, industry_median_1y=None)
        tech_tmp[code] = tr
        if tr.price_return_1y_qfq is not None:
            ret_by_group[grp].append(tr.price_return_1y_qfq)
        if (i + 1) % 500 == 0:
            print(f"  tech {i+1}/{len(phase3_rows)}", flush=True)

    group_med_ret = {
        g: float(np.median(vs)) for g, vs in ret_by_group.items() if vs
    }

    # refresh rel_strength with medians (lightweight recompute component)
    print("tech rel-strength adjust …", flush=True)
    for code, tr in tech_tmp.items():
        grp = val_eng.industry_group(code)
        med = group_med_ret.get(grp)
        if tr.dividend_trend_score is None or tr.price_return_1y_qfq is None or med is None:
            continue
        # rebuild rel component like engine
        diff = tr.price_return_1y_qfq - med
        if diff > 0.10:
            rel = 85.0
        elif diff > 0.0:
            rel = 70.0
        elif diff > -0.10:
            rel = 50.0
        elif diff > -0.20:
            rel = 35.0
        else:
            rel = 20.0
        comps = dict(tr.components or {})
        old = comps.get("rel_strength", 50.0) or 50.0
        comps["rel_strength"] = rel
        # adjust final score by weight delta
        w = DividendTechnicalEngine.WEIGHTS["rel_strength"]
        if tr.dividend_trend_score is not None:
            tr.dividend_trend_score = round(
                float(tr.dividend_trend_score) + w * (rel - old), 2
            )
            tr.dividend_trend_score = float(max(0.0, min(100.0, tr.dividend_trend_score)))
        tr.components = comps

    # Pass B: rough yields for industry median
    print("yield pass …", flush=True)
    rough_yields: Dict[str, float] = {}
    yield_by_group: Dict[str, List[float]] = defaultdict(list)
    for i, row in enumerate(phase3_rows):
        code = _z6(row["symbol"])
        # use valuation helpers
        px = val_eng._price_on(code, as_of)
        if not px:
            continue
        cash = val_eng._cash_ttm(code, as_of)
        if px[0] > 0 and cash > 0:
            y = cash / px[0]
            rough_yields[code] = y
            yield_by_group[val_eng.industry_group(code)].append(y)
        if (i + 1) % 500 == 0:
            print(f"  yield {i+1}/{len(phase3_rows)}", flush=True)
    group_med_yield = {
        g: float(np.median(vs)) for g, vs in yield_by_group.items() if vs
    }

    # Pass C: full scores
    print("compose pass …", flush=True)
    results: List[Dict[str, Any]] = []
    for i, row in enumerate(phase3_rows):
        code = _z6(row["symbol"])
        name = row.get("name") or ""
        q = row.get("dividend_quality_score")
        s = row.get("dividend_sustainability_score")
        hint = row.get("risk_hint") or "SAFE"
        unhealthy = list(row.get("unhealthy_flags") or [])
        profit_bad, cash_bad = profit_cash_flags(row)

        tr = tech_tmp[code]
        grp = val_eng.industry_group(code)
        val = val_eng.score(
            code,
            as_of,
            price_return_1y_qfq=tr.price_return_1y_qfq,
            profit_deteriorating=profit_bad,
            cash_deteriorating=cash_bad,
            industry_yield_median=group_med_yield.get(grp),
        )

        ret_pack = historical_total_return_score(code, as_of)

        risk = risk_eng.score(
            code,
            as_of,
            sustainability_score=s,
            risk_hint=hint,
            unhealthy_flags=unhealthy,
            dividend_yield_trap=bool(val.dividend_yield_trap),
            trap_flags=list(val.trap_flags or []),
            trend_score=tr.dividend_trend_score,
            price_return_1y_qfq=tr.price_return_1y_qfq,
            quality_score=q,
        )

        opp = opportunity_score(
            quality=q,
            sustainability=s,
            trend=tr.dividend_trend_score,
            total_return=ret_pack.get("historical_total_return_score"),
            valuation=val.valuation_score,
            risk=risk.dividend_risk_score,
        )
        grade = assign_grade(
            opp,
            quality=q,
            sustainability=s,
            risk_level=risk.dividend_risk,
        )

        entry = entry_eng.score(
            code,
            as_of,
            grade=grade,
            opportunity_score=opp,
            quality_score=q,
            sustainability_score=s,
            trend_score=tr.dividend_trend_score,
            valuation_score=val.valuation_score,
            risk_level=risk.dividend_risk,
            risk_score=risk.dividend_risk_score,
            dividend_yield_trap=bool(val.dividend_yield_trap),
            pe_percentile=val.pe_percentile,
            pb_percentile=val.pb_percentile,
            close=tr.close,
            ma60=tr.ma60,
            ma250=tr.ma250,
            price_return_1y_qfq=tr.price_return_1y_qfq,
        )

        reasons: List[str] = []
        for block in (
            row.get("reasons") or [],
            tr.reasons or [],
            val.reasons or [],
            risk.reasons or [],
            ret_pack.get("reasons") or [],
            entry.why_watch or [],
            entry.why_not_now or [],
        ):
            for r in block:
                if r and r not in reasons:
                    reasons.append(r)
        reasons = reasons[:24]
        reasons.append(f"操作提示：{entry.action_hint}")

        data_flags = []
        for ds, lab in (
            (row.get("quality_data_status"), "Q"),
            (row.get("sustainability_data_status"), "S"),
            (tr.data_status, "Trend"),
            (val.data_status, "Val"),
            (ret_pack.get("data_status"), "TR"),
        ):
            if ds and ds != "VALID":
                data_flags.append(f"{lab}:{ds}")

        out = {
            "strategy": "DIVIDEND_TREND",
            "symbol": code,
            "name": name,
            "date": as_of,
            "dividend_yield_ttm": val.dividend_yield_ttm,
            "dividend_yield_3y_avg": val.dividend_yield_3y_avg,
            "dividend_yield_5y_avg": val.dividend_yield_5y_avg,
            "pe_ttm": val.pe_ttm,
            "pb": val.pb,
            "pe_percentile": val.pe_percentile,
            "pb_percentile": val.pb_percentile,
            "industry_name": val.industry_name,
            "industry_group": val.industry_group,
            "dividend_quality_score": q,
            "dividend_sustainability_score": s,
            "dividend_trend_score": tr.dividend_trend_score,
            "historical_total_return_score": ret_pack.get(
                "historical_total_return_score"
            ),
            "return_proxy": ret_pack.get("return_proxy"),
            "valuation_score": val.valuation_score,
            "dividend_risk_score": risk.dividend_risk_score,
            "dividend_opportunity_score": opp,
            "dividend_grade": grade,
            "dividend_entry_status": entry.dividend_entry_status,
            "dividend_risk": risk.dividend_risk,
            "dividend_yield_trap": bool(val.dividend_yield_trap),
            "trap_flags": val.trap_flags,
            "price_return_1y": ret_pack.get("price_return_1y"),
            "price_return_1y_qfq": tr.price_return_1y_qfq,
            "total_return_1y": ret_pack.get("total_return_1y"),
            "total_return_3y": ret_pack.get("total_return_3y"),
            "total_return_5y": ret_pack.get("total_return_5y"),
            "dividend_cash_return_1y": ret_pack.get("dividend_cash_return_1y"),
            "protection_price": tr.protection_price,
            "trend_break_price": tr.trend_break_price,
            "close": tr.close,
            "ma60": tr.ma60,
            "ma250": tr.ma250,
            "action_hint": entry.action_hint,
            "why_watch": entry.why_watch,
            "why_not_now": entry.why_not_now,
            "wait_for": entry.wait_for,
            "reasons": reasons,
            "data_status": "DATA_INSUFFICIENT" if data_flags else "VALID",
            "data_flags": data_flags,
            "continuous_cash_years": row.get("continuous_cash_years"),
            "risk_hint_sustainability": hint,
            "unhealthy_flags": unhealthy,
            "board_tags": [],  # filled after ranks
        }
        results.append(out)
        if (i + 1) % 400 == 0:
            print(f"  compose {i+1}/{len(phase3_rows)}", flush=True)

    # Ranks
    by_quality = sorted(
        results,
        key=lambda r: (
            -(r.get("dividend_quality_score") or -1),
            -(r.get("dividend_sustainability_score") or -1),
            -(r.get("dividend_opportunity_score") or -1),
        ),
    )
    for i, r in enumerate(by_quality, 1):
        r["quality_rank"] = i

    by_entry = sorted(
        results,
        key=lambda r: (
            -(r.get("dividend_opportunity_score") or -1),
            -(r.get("dividend_sustainability_score") or -1),
            -(r.get("dividend_trend_score") or -1),
            -(r.get("valuation_score") or -1),
        ),
    )
    for i, r in enumerate(by_entry, 1):
        r["entry_opportunity_rank"] = i

    trend_grades = load_trend_observation_grades(as_of)
    require_trend_quality = bool(trend_grades)
    for r in results:
        trend_grade = trend_grades.get(_z6(r["symbol"])) if require_trend_quality else None
        if require_trend_quality:
            r["trend_observation_grade"] = trend_grade
        r["board_tags"] = board_bucket(
            r,
            trend_grade,
            require_trend_quality=require_trend_quality,
        )

    # boards
    quality_board = [
        r
        for r in by_quality
        if "优质红利" in r["board_tags"]
    ][:100]
    # 当前机会: among tagged, sort by entry rank
    entry_board = sorted(
        [r for r in results if "当前机会" in r["board_tags"]],
        key=lambda r: r["entry_opportunity_rank"],
    )[:100]
    risk_board = sorted(
        [r for r in results if "风险观察" in r["board_tags"]],
        key=lambda r: (-(r.get("dividend_risk_score") or 0), r.get("symbol")),
    )[:100]

    # ensure >=3 trap/HIGH in risk examples
    risk_examples = []
    for r in risk_board:
        if r.get("dividend_yield_trap") or r.get("dividend_risk") == "HIGH":
            risk_examples.append(r)
        if len(risk_examples) >= 8:
            break
    if len(risk_examples) < 3:
        for r in sorted(
            results,
            key=lambda x: -(x.get("dividend_risk_score") or 0),
        ):
            if r in risk_examples:
                continue
            if r.get("dividend_yield_trap") or r.get("dividend_risk") in (
                "HIGH",
                "WARNING",
            ):
                risk_examples.append(r)
            if len(risk_examples) >= 5:
                break

    grade_counts = Counter(r.get("dividend_grade") for r in results)
    risk_counts = Counter(r.get("dividend_risk") for r in results)
    entry_counts = Counter(r.get("dividend_entry_status") for r in results)
    proxy_counts = Counter(r.get("return_proxy") for r in results)
    n_trap = sum(1 for r in results if r.get("dividend_yield_trap"))

    # A+ gate check
    a_plus = [r for r in results if r.get("dividend_grade") == "A+"]
    a_plus_violations = [
        r["symbol"]
        for r in a_plus
        if (r.get("dividend_quality_score") or 0) < 75
        or (r.get("dividend_sustainability_score") or 0) < 75
        or r.get("dividend_risk") == "HIGH"
    ]

    known_gaps = [
        "三区均对齐当日趋势观察优质评级（TrendGrade∈{A+,A}）；当前机会的红利等级也须为 A+/A。无同日趋势观察发布时不套用该闸门",
        "全市场 bfq 仅~33只；多数 historical_total_return_score 为 price_qfq_only 弱代理（已向50压缩）",
        "PE/PB 用最近年报 EPS/BPS × qfq 价，非券商一致预期 TTM；历史百分位用年末截面近似",
        "行业分组为粗分（银行/煤炭/公用/交运/其他），组内可比性有限",
        "红利指数历史成分仍缺失（universe_mode=proxy_no_hist_index_cons）",
        "特别分红强标签弱，Quality 侧仍靠 DPS 启发式",
        "权重与等级阈值待回测校准（Plan §27 初始值）",
        "技术相对强度用同组1年收益中位，非正式行业指数",
        "Phase3→Phase6：在原 Q/S 上叠加 Trend/Val/Risk/Opp，分布会整体重排",
    ]

    slim = lambda rows: [
        {
            k: r.get(k)
            for k in (
                "symbol",
                "name",
                "dividend_grade",
                "dividend_opportunity_score",
                "dividend_quality_score",
                "dividend_sustainability_score",
                "dividend_trend_score",
                "historical_total_return_score",
                "return_proxy",
                "valuation_score",
                "dividend_risk_score",
                "dividend_risk",
                "dividend_entry_status",
                "dividend_yield_ttm",
                "dividend_yield_trap",
                "industry_group",
                "quality_rank",
                "entry_opportunity_rank",
                "action_hint",
                "reasons",
                "board_tags",
                "trend_observation_grade",
            )
        }
        for r in rows
    ]

    board_payload = {
        "strategy": "DIVIDEND_TREND",
        "version": "V3.1.9-phase6",
        "phase": "6-11",
        "as_of": as_of,
        "disclaimer": "观察池；红利三区须当日趋势观察评级 A+/A，当前机会红利等级也须 A+/A；禁止荐买 / 预测必涨。",
        "generated_at": datetime.now(CST).isoformat(),
        "timezone": "Asia/Shanghai",
        "n_scored": len(results),
        "grade_counts": dict(grade_counts),
        "risk_counts": dict(risk_counts),
        "entry_counts": dict(entry_counts),
        "return_proxy_counts": dict(proxy_counts),
        "n_trap": n_trap,
        "a_plus_count": len(a_plus),
        "a_plus_gate_violations": a_plus_violations,
        "score_summary": {
            "opportunity": summarize(
                [r["dividend_opportunity_score"] for r in results if r.get("dividend_opportunity_score") is not None]
            ),
            "trend": summarize(
                [r["dividend_trend_score"] for r in results if r.get("dividend_trend_score") is not None]
            ),
            "valuation": summarize(
                [r["valuation_score"] for r in results if r.get("valuation_score") is not None]
            ),
            "risk": summarize(
                [r["dividend_risk_score"] for r in results if r.get("dividend_risk_score") is not None]
            ),
            "total_return_score": summarize(
                [
                    r["historical_total_return_score"]
                    for r in results
                    if r.get("historical_total_return_score") is not None
                ]
            ),
        },
        "config_weights": DIVIDEND_CONFIG.score_weights(),
        "boards": {
            "优质红利": slim(quality_board),
            "当前机会": slim(entry_board),
            "风险观察": slim(risk_board),
        },
        "top_entry15": slim(entry_board[:15]),
        "top_quality10": slim(quality_board[:10]),
        "risk_examples": slim(risk_examples[:8]),
        "known_gaps": known_gaps,
        "publish_close_v318_untouched": True,
        "elapsed_sec": round(time.time() - t0, 1),
    }

    out_dir = OUT_DIR / "boards"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = as_of.replace("-", "")
    full_path = out_dir / f"dividend_board_{tag}.json"
    # full rows are large — store boards + all results compact
    full_doc = {
        **board_payload,
        "rows": results,
    }
    with full_path.open("w", encoding="utf-8") as f:
        json.dump(full_doc, f, ensure_ascii=False)

    md_path = out_dir / f"dividend_board_{tag}.md"
    md_path.write_text(to_markdown(board_payload), encoding="utf-8")

    # summary without full rows
    summary_path = out_dir / f"dividend_board_{tag}_summary.json"
    summary_path.write_text(
        json.dumps(board_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (OUT_DIR / "dividend_board_latest.md").write_text(
        to_markdown(board_payload), encoding="utf-8"
    )
    (OUT_DIR / "dividend_board_latest_summary.json").write_text(
        json.dumps(board_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "as_of": as_of,
                "n_scored": len(results),
                "grade_counts": dict(grade_counts),
                "risk_counts": dict(risk_counts),
                "n_trap": n_trap,
                "a_plus": len(a_plus),
                "a_plus_violations": a_plus_violations[:10],
                "top_entry5": [
                    {
                        "symbol": r["symbol"],
                        "name": r["name"],
                        "grade": r["dividend_grade"],
                        "opp": r["dividend_opportunity_score"],
                        "entry": r["dividend_entry_status"],
                    }
                    for r in entry_board[:5]
                ],
                "risk_examples": [
                    {
                        "symbol": r["symbol"],
                        "risk": r["dividend_risk"],
                        "trap": r["dividend_yield_trap"],
                    }
                    for r in risk_examples[:5]
                ],
                "elapsed_sec": board_payload["elapsed_sec"],
                "paths": {
                    "full": str(full_path),
                    "md": str(md_path),
                    "summary": str(summary_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
