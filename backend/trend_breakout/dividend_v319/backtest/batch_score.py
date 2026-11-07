"""
Phase12 batch as-of scorer — reuses engines with event/price indexing for speed.

Does NOT modify Growth/v318. Selection-side TR still uses historical_total_return_score
(bfq+cash when present; else price_qfq_only compressed). Never qfq+cash.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIVIDEND_CONFIG, EVENTS_PATH, KLINE_DAILY, OUT_DIR
from dividend_v319.engines.dividendEntryEngine import DividendEntryEngine
from dividend_v319.engines.dividendQualityEngine import DividendQualityEngine
from dividend_v319.engines.dividendRiskEngine import DividendRiskEngine
from dividend_v319.engines.dividendSustainabilityEngine import DividendSustainabilityEngine
from dividend_v319.engines.dividendTechnicalEngine import DividendTechnicalEngine
from dividend_v319.engines.dividendTrendEngine import (
    assign_grade,
    historical_total_return_score,
    opportunity_score,
    qfq_price_returns,
)
from dividend_v319.engines.dividendUniverseEngine import DividendUniverseEngine
from dividend_v319.engines.dividendValuationEngine import DividendValuationEngine

def _z6(c: str) -> str:
    return str(c).strip().zfill(6)


def _profit_cash_flags(unhealthy: List[str], comps: Dict[str, Any]) -> Tuple[bool, bool]:
    flags = set(unhealthy or [])
    profit = any(
        x in flags
        for x in ("profit_collapse", "profit_decline", "ni_deteriorating", "earnings_decline")
    ) or any("profit" in f and ("declin" in f or "fall" in f or "collapse" in f) for f in flags)
    cash = any(
        x in flags
        for x in ("negative_fcf_vs_dividend", "ocf_persistently_negative", "weak_cash")
    ) or any("fcf" in f or "ocf" in f or "cash" in f for f in flags)
    if comps.get("profit_continuity") is not None and comps["profit_continuity"] < 40:
        profit = True
    if comps.get("fcf_over_div") is not None and comps["fcf_over_div"] < 35:
        cash = True
    if comps.get("ocf_over_ni") is not None and comps["ocf_over_ni"] < 35:
        cash = True
    return profit, cash


def _patch_valuation_speed(val: DividendValuationEngine) -> None:
    """Index events by code; avoid full-table scans on every _cash_ttm call."""
    val._ensure()
    by: Dict[str, pd.DataFrame] = {}
    for code, g in val._events.groupby("code"):
        by[_z6(code)] = g
    val._events_by_code = by  # type: ignore[attr-defined]

    ok = set(val.cfg.assign_progress_ok)

    def _cash_ttm(code: str, as_of: str) -> float:
        t = pd.Timestamp(as_of).normalize()
        start = t - pd.DateOffset(years=1)
        g = val._events_by_code.get(_z6(code))  # type: ignore[attr-defined]
        if g is None or g.empty:
            return 0.0
        sub = g[
            (g["assign_progress"].isin(ok))
            & (g["cash_per_share"].fillna(0) > 0)
            & (g["ex_dividend_date"].notna())
            & (g["ex_dividend_date"] > start)
            & (g["ex_dividend_date"] <= t)
            & (g["disclose_date"].isna() | (g["disclose_date"] <= t))
        ]
        return float(sub["cash_per_share"].sum()) if len(sub) else 0.0

    def _avg_yield_ny(code: str, as_of: str, n_years: int, price: float):
        if price <= 0:
            return None
        t = pd.Timestamp(as_of).normalize()
        yields = []
        for i in range(n_years):
            end = t - pd.DateOffset(years=i)
            start = end - pd.DateOffset(years=1)
            g = val._events_by_code.get(_z6(code))  # type: ignore[attr-defined]
            if g is None:
                continue
            sub = g[
                (g["assign_progress"].isin(ok))
                & (g["cash_per_share"].fillna(0) > 0)
                & (g["ex_dividend_date"].notna())
                & (g["ex_dividend_date"] > start)
                & (g["ex_dividend_date"] <= end)
                & (g["disclose_date"].isna() | (g["disclose_date"] <= t))
            ]
            cash = float(sub["cash_per_share"].sum()) if len(sub) else 0.0
            px = val._price_on(code, str(end.date()))
            p = px[0] if px else price
            if p > 0 and cash > 0:
                yields.append(cash / p)
        return float(np.mean(yields)) if yields else None

    val._cash_ttm = _cash_ttm  # type: ignore[method-assign]
    val._avg_yield_ny = _avg_yield_ny  # type: ignore[method-assign]


def _fast_tr_score(code: str, as_of: str, px_cache: Dict[str, Optional[pd.DataFrame]]) -> Dict[str, Any]:
    """
    Selection-time TR: prefer historical_total_return_score for bfq names;
    else reuse in-memory qfq closes (never add cash on qfq).
    """
    from dividend_v319.config.dividendConfig import BFQ_DIR

    code = _z6(code)
    if (BFQ_DIR / f"{code}.parquet").exists():
        return historical_total_return_score(code, as_of)

    # in-memory qfq proxy (same compression as engine)
    df = px_cache.get(code)
    reasons = ["无bfq：price_qfq_only弱代理（batch内存价），禁止qfq+现金；向50压缩"]
    if df is None or df.empty:
        return {
            "historical_total_return_score": 50.0,
            "return_proxy": "price_qfq_only",
            "data_status": "DATA_INSUFFICIENT",
            "reasons": reasons + ["无价格"],
            "price_return_1y": None,
            "total_return_1y": None,
            "total_return_3y": None,
            "total_return_5y": None,
            "dividend_cash_return_1y": None,
        }
    t = pd.Timestamp(as_of).normalize()
    sub = df[df["date"] <= t]
    if len(sub) < 20:
        return {
            "historical_total_return_score": 50.0,
            "return_proxy": "price_qfq_only",
            "data_status": "DATA_INSUFFICIENT",
            "reasons": reasons + ["价格过短"],
            "price_return_1y": None,
            "total_return_1y": None,
            "total_return_3y": None,
            "total_return_5y": None,
            "dividend_cash_return_1y": None,
        }
    p_end = float(sub.iloc[-1]["close"])
    scores, weights = [], []
    pr1 = None
    for n, w in ((5, 0.45), (3, 0.35), (1, 0.20)):
        start = t - pd.DateOffset(years=int(n))
        past = sub[sub["date"] <= start]
        if past.empty:
            continue
        p0 = float(past.iloc[-1]["close"])
        if p0 <= 0:
            continue
        pr = p_end / p0 - 1.0
        if n == 1:
            pr1 = pr
        # map ann
        ann = (1.0 + pr) ** (1.0 / n) - 1.0
        # copy map from trend engine piecewise
        x = float(ann)
        if x >= 0.20:
            raw = 95.0
        elif x >= 0.12:
            raw = 85.0 + (x - 0.12) / 0.08 * 10.0
        elif x >= 0.06:
            raw = 72.0 + (x - 0.06) / 0.06 * 13.0
        elif x >= 0.0:
            raw = 55.0 + x / 0.06 * 17.0
        elif x >= -0.10:
            raw = 40.0 + (x + 0.10) / 0.10 * 15.0
        elif x >= -0.20:
            raw = 25.0 + (x + 0.20) / 0.10 * 15.0
        else:
            raw = max(0.0, min(100.0, 10.0 + (x + 0.40) / 0.20 * 15.0))
        compressed = 50.0 + 0.45 * (raw - 50.0)
        scores.append(compressed)
        weights.append(w)
    if not scores:
        final = 50.0
    else:
        final = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    return {
        "historical_total_return_score": round(float(max(0.0, min(100.0, final))), 2),
        "return_proxy": "price_qfq_only",
        "data_status": "VALID",
        "reasons": reasons,
        "price_return_1y": pr1,
        "total_return_1y": None,
        "total_return_3y": None,
        "total_return_5y": None,
        "dividend_cash_return_1y": None,
    }


def preload_prices(codes: List[str], kline_dir: Path = KLINE_DAILY) -> Dict[str, Optional[pd.DataFrame]]:
    """Load qfq bars once (includes MA cols for technical engine)."""
    out: Dict[str, Optional[pd.DataFrame]] = {}
    cols = ["date", "open", "close", "MA20", "MA60", "MA120", "MA250", "ATR14"]
    for c in codes:
        path = kline_dir / f"{_z6(c)}.parquet"
        if not path.exists():
            out[_z6(c)] = None
            continue
        try:
            df = pd.read_parquet(path, columns=cols)
        except Exception:
            df = pd.read_parquet(path, columns=["date", "open", "close"])
            for m in ("MA20", "MA60", "MA120", "MA250", "ATR14"):
                if m not in df.columns:
                    df[m] = np.nan
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.dropna(subset=["close"]).sort_values("date")
        out[_z6(c)] = df
    return out


def score_asof(
    as_of: str,
    *,
    min_years: int = 3,
    limit: int = 0,
    px_cache: Optional[Dict[str, Optional[pd.DataFrame]]] = None,
    engines: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    t0 = time.time()
    uni_eng = DividendUniverseEngine(DIVIDEND_CONFIG)
    uni = uni_eng.asof(as_of, min_years=min_years)
    members = uni["members"]
    if limit and limit > 0:
        members = members[:limit]
    codes = [_z6(m["symbol"]) for m in members]
    name_by = {_z6(m["symbol"]): m.get("name") or "" for m in members}

    if engines is None:
        qe = DividendQualityEngine(DIVIDEND_CONFIG)
        se = DividendSustainabilityEngine(DIVIDEND_CONFIG)
        te = DividendTechnicalEngine(DIVIDEND_CONFIG)
        ve = DividendValuationEngine(DIVIDEND_CONFIG)
        _patch_valuation_speed(ve)
        risk_eng = DividendRiskEngine()
        entry_eng = DividendEntryEngine()
    else:
        qe, se, te, ve = engines["q"], engines["s"], engines["t"], engines["v"]
        risk_eng, entry_eng = engines["risk"], engines["entry"]

    if px_cache is None:
        px_cache = preload_prices(codes)
    # share price cache with valuation + technical
    ve._px_cache = {
        k: (v[["date", "close"]].copy() if v is not None else None) for k, v in px_cache.items()
    }  # type: ignore
    te._bar_cache = px_cache  # type: ignore[attr-defined]

    def _tech_load(code: str):
        return te._bar_cache.get(_z6(code))  # type: ignore[attr-defined]

    te._load = _tech_load  # type: ignore[method-assign]

    # Warm panels
    if codes:
        qe.score(codes[0], as_of)
        se.score(codes[0], as_of)

    # Pass tech
    tech_tmp: Dict[str, Any] = {}
    ret_by_group: Dict[str, List[float]] = defaultdict(list)
    qs_rows: Dict[str, Dict[str, Any]] = {}

    for i, code in enumerate(codes):
        qr = qe.score(code, as_of)
        sr = se.score(code, as_of)
        q = qr.dividend_quality_score if hasattr(qr, "dividend_quality_score") else qr.get("dividend_quality_score")
        s = sr.dividend_sustainability_score if hasattr(sr, "dividend_sustainability_score") else sr.get("dividend_sustainability_score")
        hint = getattr(sr, "risk_hint", None) or (sr.get("risk_hint") if isinstance(sr, dict) else None) or "SAFE"
        unhealthy = list(getattr(sr, "unhealthy_flags", None) or (sr.get("unhealthy_flags") if isinstance(sr, dict) else []) or [])
        comps = {}
        if hasattr(sr, "to_dict"):
            sd = sr.to_dict()
            comps = sd.get("component_scores") or sd.get("components") or sd.get("sustainability_components") or {}
            hint = sd.get("risk_hint") or hint
            unhealthy = list(sd.get("unhealthy_flags") or unhealthy)
        elif isinstance(sr, dict):
            comps = sr.get("component_scores") or sr.get("sustainability_components") or {}
        qs_rows[code] = {
            "q": q,
            "s": s,
            "hint": hint,
            "unhealthy": unhealthy,
            "comps": comps,
            "q_status": getattr(qr, "data_status", None),
            "s_status": getattr(sr, "data_status", None),
            "cont_years": getattr(qr, "continuous_cash_years", None),
        }
        grp = ve.industry_group(code)
        tr = te.score(code, as_of, industry_group=grp, industry_median_1y=None)
        tech_tmp[code] = tr
        if tr.price_return_1y_qfq is not None:
            ret_by_group[grp].append(tr.price_return_1y_qfq)
        if (i + 1) % 500 == 0:
            print(f"  [{as_of}] scored {i+1}/{len(codes)}", flush=True)

    group_med_ret = {g: float(np.median(vs)) for g, vs in ret_by_group.items() if vs}
    # adjust rel strength
    w_rel = DividendTechnicalEngine.WEIGHTS.get("rel_strength", 0.15)
    for code, tr in tech_tmp.items():
        grp = ve.industry_group(code)
        med = group_med_ret.get(grp)
        if tr.dividend_trend_score is None or tr.price_return_1y_qfq is None or med is None:
            continue
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
        tr.dividend_trend_score = round(
            float(max(0.0, min(100.0, float(tr.dividend_trend_score) + w_rel * (rel - old)))), 2
        )
        tr.components = comps

    # yield medians
    rough_yields: Dict[str, float] = {}
    yield_by_group: Dict[str, List[float]] = defaultdict(list)
    for code in codes:
        px = ve._price_on(code, as_of)
        if not px:
            continue
        cash = ve._cash_ttm(code, as_of)
        if px[0] > 0 and cash > 0:
            y = cash / px[0]
            rough_yields[code] = y
            yield_by_group[ve.industry_group(code)].append(y)
    group_med_yield = {g: float(np.median(vs)) for g, vs in yield_by_group.items() if vs}

    results: List[Dict[str, Any]] = []
    for code in codes:
        row = qs_rows[code]
        q, s = row["q"], row["s"]
        profit_bad, cash_bad = _profit_cash_flags(row["unhealthy"], row["comps"])
        tr = tech_tmp[code]
        grp = ve.industry_group(code)
        val = ve.score(
            code,
            as_of,
            price_return_1y_qfq=tr.price_return_1y_qfq,
            profit_deteriorating=profit_bad,
            cash_deteriorating=cash_bad,
            industry_yield_median=group_med_yield.get(grp),
        )
        ret_pack = _fast_tr_score(code, as_of, px_cache)
        risk = risk_eng.score(
            code,
            as_of,
            sustainability_score=s,
            risk_hint=row["hint"],
            unhealthy_flags=row["unhealthy"],
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
        grade = assign_grade(opp, quality=q, sustainability=s, risk_level=risk.dividend_risk)
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
        results.append(
            {
                "symbol": code,
                "name": name_by.get(code, ""),
                "as_of": as_of,
                "dividend_quality_score": q,
                "dividend_sustainability_score": s,
                "dividend_trend_score": tr.dividend_trend_score,
                "historical_total_return_score": ret_pack.get("historical_total_return_score"),
                "return_proxy": ret_pack.get("return_proxy"),
                "valuation_score": val.valuation_score,
                "dividend_risk_score": risk.dividend_risk_score,
                "dividend_opportunity_score": opp,
                "dividend_grade": grade,
                "dividend_entry_status": entry.dividend_entry_status,
                "dividend_risk": risk.dividend_risk,
                "dividend_yield_trap": bool(val.dividend_yield_trap),
                "dividend_yield_ttm": val.dividend_yield_ttm,
                "trend_break_price": tr.trend_break_price,
                "protection_price": tr.protection_price,
                "close": tr.close,
                "ma250": tr.ma250,
                "price_return_1y_qfq": tr.price_return_1y_qfq,
                "industry_group": grp,
                "continuous_cash_years": row.get("cont_years"),
            }
        )

    # ranks
    by_q = sorted(
        results,
        key=lambda r: (
            -(r.get("dividend_quality_score") or -1),
            -(r.get("dividend_sustainability_score") or -1),
            -(r.get("dividend_opportunity_score") or -1),
        ),
    )
    for i, r in enumerate(by_q, 1):
        r["quality_rank"] = i
    by_e = sorted(
        results,
        key=lambda r: (
            -(r.get("dividend_opportunity_score") or -1),
            -(r.get("dividend_sustainability_score") or -1),
            -(r.get("dividend_trend_score") or -1),
            -(r.get("valuation_score") or -1),
        ),
    )
    for i, r in enumerate(by_e, 1):
        r["entry_opportunity_rank"] = i

    payload = {
        "as_of": as_of,
        "universe_mode": uni.get("universe_mode"),
        "universe_counts": uni.get("counts"),
        "n_scored": len(results),
        "grade_counts": dict(Counter(r["dividend_grade"] for r in results)),
        "risk_counts": dict(Counter(r["dividend_risk"] for r in results)),
        "entry_counts": dict(Counter(r["dividend_entry_status"] for r in results)),
        "return_proxy_counts": dict(Counter(r.get("return_proxy") for r in results)),
        "n_trap": sum(1 for r in results if r.get("dividend_yield_trap")),
        "elapsed_sec": round(time.time() - t0, 2),
        "rows": results,
    }
    return payload


def save_snapshot(payload: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = payload["as_of"].replace("-", "")
    path = out_dir / f"snapshot_{as_of}.json"
    # write compact without indent for size
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    slim = {k: v for k, v in payload.items() if k != "rows"}
    slim["rows_head"] = payload["rows"][:20]
    (out_dir / f"snapshot_{as_of}_meta.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
