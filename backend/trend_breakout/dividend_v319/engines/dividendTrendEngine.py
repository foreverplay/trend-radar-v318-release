"""
DividendTrendEngine orchestrator helpers — OpportunityScore / Grade /
HistoricalTotalReturnScore (Phase6–11).

Does not modify GrowthTrendEngine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import BFQ_DIR, DIVIDEND_CONFIG, KLINE_DAILY
from dividend_v319.engines.dividendReturnEngine import compute_multi_horizon
from dividend_v319.models.dividendModels import DividendGrade


def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _map_ann_return_to_score(ann: Optional[float]) -> Optional[float]:
    if ann is None or not np.isfinite(ann):
        return None
    # piecewise 待回测校准
    x = float(ann)
    if x >= 0.20:
        return 95.0
    if x >= 0.12:
        return 85.0 + (x - 0.12) / 0.08 * 10.0
    if x >= 0.06:
        return 72.0 + (x - 0.06) / 0.06 * 13.0
    if x >= 0.0:
        return 55.0 + x / 0.06 * 17.0
    if x >= -0.10:
        return 40.0 + (x + 0.10) / 0.10 * 15.0
    if x >= -0.20:
        return 25.0 + (x + 0.20) / 0.10 * 15.0
    return _clip(10.0 + (x + 0.40) / 0.20 * 15.0)


def _ann(total: float, years: float) -> float:
    if years <= 0 or total <= -0.999:
        return float("nan")
    return float((1.0 + total) ** (1.0 / years) - 1.0)


def qfq_price_returns(
    code: str,
    as_of: str,
    *,
    kline_dir: Path = KLINE_DAILY,
    horizons: Tuple[int, ...] = (1, 3, 5),
) -> Dict[str, Any]:
    """Price-only qfq returns (never add cash)."""
    path = kline_dir / f"{_z6(code)}.parquet"
    out: Dict[str, Any] = {
        "code": _z6(code),
        "as_of": as_of,
        "return_proxy": "price_qfq_only",
        "horizons": {},
        "data_status": "DATA_INSUFFICIENT",
    }
    if not path.exists():
        out["notes"] = ["qfq_missing"]
        return out
    df = pd.read_parquet(path, columns=["date", "close"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    t = pd.Timestamp(as_of).normalize()
    sub = df[df["date"] <= t].dropna(subset=["close"])
    if len(sub) < 20:
        out["notes"] = ["qfq_too_short"]
        return out
    p_end = float(sub.iloc[-1]["close"])
    ok = False
    for n in horizons:
        start = t - pd.DateOffset(years=int(n))
        past = sub[sub["date"] <= start]
        if past.empty:
            out["horizons"][f"{n}y"] = None
            continue
        p0 = float(past.iloc[-1]["close"])
        if p0 <= 0:
            out["horizons"][f"{n}y"] = None
            continue
        pr = p_end / p0 - 1.0
        out["horizons"][f"{n}y"] = {
            "price_return": pr,
            "total_return": None,  # intentionally null — no cash on qfq
            "dividend_cash_return": None,
            "note": "price_qfq_only",
        }
        ok = True
    out["data_status"] = "VALID" if ok else "DATA_INSUFFICIENT"
    return out


def historical_total_return_score(
    code: str,
    as_of: str,
    *,
    bfq_dir: Path = BFQ_DIR,
) -> Dict[str, Any]:
    """
    Prefer true bfq+cash TR. If no bfq: price_qfq_only weak proxy, compress toward 50.
    Never qfq+cash.
    """
    code = _z6(code)
    bfq_path = bfq_dir / f"{code}.parquet"
    reasons: List[str] = []
    if bfq_path.exists():
        tr = compute_multi_horizon(code, as_of, bfq_dir=bfq_dir)
        scores = []
        weights = []
        detail = {"source": "bfq_plus_cash", "horizons": tr.get("horizons") or {}}
        for n, w in ((5, 0.45), (3, 0.35), (1, 0.20)):
            h = (tr.get("horizons") or {}).get(f"{n}y") or {}
            tot = h.get("total_return")
            if tot is None:
                continue
            ann = _ann(float(tot), float(n))
            sc = _map_ann_return_to_score(ann)
            if sc is None:
                continue
            scores.append(sc)
            weights.append(w)
            reasons.append(f"{n}y真TR年化约{ann*100:.1f}%→分{sc:.0f}")
        if not scores:
            return {
                "historical_total_return_score": 50.0,
                "return_proxy": "bfq_present_but_empty",
                "data_status": "DATA_INSUFFICIENT",
                "details": detail,
                "reasons": ["bfq存在但窗口回报算不出，总回报维中性50"],
                "price_return_1y": None,
                "total_return_1y": None,
                "total_return_3y": None,
                "total_return_5y": None,
                "dividend_cash_return_1y": None,
            }
        wsum = sum(weights)
        final = sum(s * w for s, w in zip(scores, weights)) / wsum
        h1 = (tr.get("horizons") or {}).get("1y") or {}
        h3 = (tr.get("horizons") or {}).get("3y") or {}
        h5 = (tr.get("horizons") or {}).get("5y") or {}
        return {
            "historical_total_return_score": round(_clip(final), 2),
            "return_proxy": "bfq_plus_cash",
            "data_status": tr.get("data_status", "VALID"),
            "details": detail,
            "reasons": reasons,
            "price_return_1y": h1.get("price_return"),
            "total_return_1y": h1.get("total_return"),
            "total_return_3y": h3.get("total_return"),
            "total_return_5y": h5.get("total_return"),
            "dividend_cash_return_1y": h1.get("dividend_cash_return"),
        }

    # weak proxy
    prox = qfq_price_returns(code, as_of)
    scores = []
    weights = []
    reasons.append("无bfq：使用price_qfq_only弱代理，禁止qfq+现金；分数向50压缩")
    for n, w in ((5, 0.45), (3, 0.35), (1, 0.20)):
        h = (prox.get("horizons") or {}).get(f"{n}y") or {}
        pr = h.get("price_return") if h else None
        if pr is None:
            continue
        ann = _ann(float(pr), float(n))
        raw = _map_ann_return_to_score(ann)
        if raw is None:
            continue
        compressed = 50.0 + 0.45 * (raw - 50.0)
        scores.append(compressed)
        weights.append(w)
        reasons.append(f"{n}y仅价格年化约{ann*100:.1f}%→压缩分{compressed:.0f}")
    if not scores:
        return {
            "historical_total_return_score": 50.0,
            "return_proxy": "price_qfq_only",
            "data_status": "DATA_INSUFFICIENT",
            "details": prox,
            "reasons": reasons + ["价格代理也不足，总回报维中性50"],
            "price_return_1y": None,
            "total_return_1y": None,
            "total_return_3y": None,
            "total_return_5y": None,
            "dividend_cash_return_1y": None,
        }
    wsum = sum(weights)
    final = sum(s * w for s, w in zip(scores, weights)) / wsum
    h1 = (prox.get("horizons") or {}).get("1y") or {}
    h3 = (prox.get("horizons") or {}).get("3y") or {}
    h5 = (prox.get("horizons") or {}).get("5y") or {}
    return {
        "historical_total_return_score": round(_clip(final), 2),
        "return_proxy": "price_qfq_only",
        "data_status": prox.get("data_status", "DATA_INSUFFICIENT"),
        "details": prox,
        "reasons": reasons,
        "price_return_1y": h1.get("price_return") if h1 else None,
        "total_return_1y": None,  # not true TR
        "total_return_3y": None,
        "total_return_5y": None,
        "dividend_cash_return_1y": None,
    }


def opportunity_score(
    *,
    quality: Optional[float],
    sustainability: Optional[float],
    trend: Optional[float],
    total_return: Optional[float],
    valuation: Optional[float],
    risk: Optional[float],
    cfg=None,
) -> Optional[float]:
    cfg = cfg or DIVIDEND_CONFIG
    # missing → neutral 50
    q = 50.0 if quality is None else float(quality)
    s = 50.0 if sustainability is None else float(sustainability)
    t = 50.0 if trend is None else float(trend)
    tr = 50.0 if total_return is None else float(total_return)
    v = 50.0 if valuation is None else float(valuation)
    r = 50.0 if risk is None else float(risk)
    risk_term = 100.0 - r
    score = (
        cfg.quality_weight * q
        + cfg.sustainability_weight * s
        + cfg.trend_weight * t
        + cfg.total_return_weight * tr
        + cfg.valuation_weight * v
        + cfg.risk_weight * risk_term
    )
    return round(_clip(score), 2)


def assign_grade(
    opp: Optional[float],
    *,
    quality: Optional[float],
    sustainability: Optional[float],
    risk_level: str,
    cfg=None,
) -> str:
    cfg = cfg or DIVIDEND_CONFIG
    if opp is None:
        return DividendGrade.FILTER.value
    risk_level = (risk_level or "").upper()
    # FILTER: very weak
    if opp < cfg.grade_c:
        return DividendGrade.FILTER.value
    if risk_level == "HIGH" and opp < cfg.grade_a:
        # high risk cannot be A+/A
        if opp >= cfg.grade_b:
            return DividendGrade.B.value
        if opp >= cfg.grade_c:
            return DividendGrade.C.value
        return DividendGrade.FILTER.value

    if (
        opp >= cfg.grade_a_plus
        and (quality or 0) >= cfg.a_plus_min_quality
        and (sustainability or 0) >= cfg.a_plus_min_sustainability
        and risk_level != "HIGH"
    ):
        return DividendGrade.A_PLUS.value
    if opp >= cfg.grade_a_plus and risk_level != "HIGH":
        # missed Q/S gate → A
        return DividendGrade.A.value
    if opp >= cfg.grade_a and risk_level != "HIGH":
        return DividendGrade.A.value
    if opp >= cfg.grade_b:
        return DividendGrade.B.value
    if opp >= cfg.grade_c:
        return DividendGrade.C.value
    return DividendGrade.FILTER.value


def run(*_a, **_k):
    raise NotImplementedError("Use opportunity_score / assign_grade / historical_total_return_score")
