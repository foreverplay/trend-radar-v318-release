"""
ExitQualityScore — Plan §24 proxy for Phase 10/11 strategy ranking.

Weights follow Plan §19 metric family (capture / giveback / return / DD /
stability). MaxDD rule (Plan §24):

  * Soft penalize when **worst-trade** peak→trough MaxDD > 20%
  * Hard eliminate when worst-trade MaxDD > 35% (or n_closed=0)

Independent-trade *equity-curve* MaxDD is recorded via ``equity_curve_max_dd``
but is **not** the hard gate on the 2026YTD_proxy (correlated A+ lists make
sequential equity DD pathologically large for every config). Full Plan
portfolio MaxDD WF is deferred with 2019–2025 A+ archives.

禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Sequence

import numpy as np

WEIGHTS: Dict[str, float] = {
    "trend_capture": 0.30,
    "giveback": 0.20,
    "return": 0.25,
    "drawdown": 0.15,
    "stability": 0.10,
}

MAX_DD_SOFT = 0.20  # Plan §24 soft threshold
MAX_DD_ELIMINATE = 0.35  # hard gate on worst-trade MaxDD (proxy adaptation)
MAX_DD_PLAN = 0.20  # documented Plan ideal (equity/portfolio)


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def equity_curve_max_dd(returns: Sequence[float]) -> Optional[float]:
    """Max DD of equal-weight sequential equity (diagnostic only on this proxy)."""
    arr = [float(r) for r in returns if r is not None and np.isfinite(float(r))]
    if not arr:
        return None
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in arr:
        eq *= 1.0 + float(r)
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return float(max_dd)


def _capture_component(mean_capture: Optional[float]) -> float:
    v = _f(mean_capture)
    if v is None:
        return 0.0
    return _clip01(v)


def _giveback_component(mean_giveback: Optional[float]) -> float:
    v = _f(mean_giveback)
    if v is None:
        return 0.5
    return 1.0 - _clip01(v)


def _return_component(mean_return: Optional[float]) -> float:
    v = _f(mean_return)
    if v is None:
        return 0.0
    if v <= -0.10:
        return 0.0
    if v <= 0.0:
        return 0.5 * (v + 0.10) / 0.10
    if v >= 0.20:
        return 1.0
    return 0.5 + 0.5 * (v / 0.20)


def _drawdown_component(worst_trade_dd: Optional[float]) -> float:
    """0% → 1.0; 20% → 0.0 (linear vs soft threshold)."""
    v = _f(worst_trade_dd)
    if v is None:
        return 0.5
    return 1.0 - _clip01(v / MAX_DD_SOFT)


def _stability_component(
    win_rate: Optional[float], early_exit_rate: Optional[float]
) -> float:
    wr = _f(win_rate)
    er = _f(early_exit_rate)
    wr_s = _clip01(wr if wr is not None else 0.0)
    er_s = 1.0 - _clip01(er if er is not None else 0.0)
    return 0.6 * wr_s + 0.4 * er_s


def exit_quality_score(
    agg: Dict[str, Any],
    *,
    trade_returns: Optional[Iterable[float]] = None,
) -> Dict[str, Any]:
    """
    Compute ExitQualityScore from aggregate_metrics.

    Hard eliminate: n_closed=0 or worst-trade max_dd > 35%.
    Soft penalize: score scaled down when worst-trade max_dd > 20%.
    """
    n_closed = int(agg.get("n_closed") or 0)
    worst_dd = _f(agg.get("max_dd"))
    eq_dd = None
    if trade_returns is not None:
        eq_dd = equity_curve_max_dd(list(trade_returns))
    if eq_dd is None:
        eq_dd = _f(agg.get("equity_max_dd"))

    eliminated = False
    reason = None
    if n_closed <= 0:
        eliminated = True
        reason = "no_closed_trades"
    elif worst_dd is not None and worst_dd > MAX_DD_ELIMINATE:
        eliminated = True
        reason = f"worst_trade_max_dd>{MAX_DD_ELIMINATE:.0%} ({worst_dd:.2%})"

    comps = {
        "trend_capture": _capture_component(agg.get("mean_capture")),
        "giveback": _giveback_component(agg.get("mean_giveback")),
        "return": _return_component(agg.get("mean_return")),
        "drawdown": _drawdown_component(worst_dd),
        "stability": _stability_component(agg.get("win_rate"), agg.get("early_exit_rate")),
    }
    raw = sum(WEIGHTS[k] * comps[k] for k in WEIGHTS)
    soft_pen = False
    if (
        not eliminated
        and worst_dd is not None
        and worst_dd > MAX_DD_SOFT
        and MAX_DD_ELIMINATE > MAX_DD_SOFT
    ):
        soft_pen = True
        raw *= max(0.0, 1.0 - (worst_dd - MAX_DD_SOFT) / (MAX_DD_ELIMINATE - MAX_DD_SOFT))

    score = None if eliminated else round(100.0 * raw, 4)
    return {
        "score": score,
        "eliminated": eliminated,
        "eliminate_reason": reason,
        "soft_penalized_20": soft_pen,
        "components": comps,
        "weights": dict(WEIGHTS),
        "worst_trade_max_dd": worst_dd,
        "equity_max_dd": eq_dd,
        "n_closed": n_closed,
        "raw_0_1": None if eliminated else raw,
    }


def combine_window_scores(
    train: Optional[Dict[str, Any]],
    val: Optional[Dict[str, Any]],
    *,
    train_weight: float = 0.4,
    val_weight: float = 0.6,
) -> Dict[str, Any]:
    """
    Selection = 0.4 Train + 0.6 Val.
    Hard eliminate only when Val fails. Train failure → soft ×0.7.
    """
    tw, vw = float(train_weight), float(val_weight)
    s = tw + vw
    tw, vw = tw / s, vw / s
    t_score = _f((train or {}).get("score"))
    v_score = _f((val or {}).get("score"))
    t_elim = bool((train or {}).get("eliminated", True))
    v_elim = bool((val or {}).get("eliminated", True))
    if v_elim or v_score is None:
        return {
            "selection_score": None,
            "eliminated": True,
            "eliminate_reason": (
                f"val:{(val or {}).get('eliminate_reason')}"
                if v_elim
                else "val_score_missing"
            ),
            "train_score": t_score,
            "val_score": v_score,
            "train_soft_penalized": t_elim,
        }
    t_eff = 0.0 if t_elim or t_score is None else float(t_score)
    sel = tw * t_eff + vw * float(v_score)
    if t_elim or t_score is None:
        sel *= 0.7
    return {
        "selection_score": round(sel, 4),
        "eliminated": False,
        "eliminate_reason": None,
        "train_score": t_score,
        "val_score": v_score,
        "train_soft_penalized": bool(t_elim or t_score is None),
    }


__all__ = [
    "WEIGHTS",
    "MAX_DD_SOFT",
    "MAX_DD_ELIMINATE",
    "MAX_DD_PLAN",
    "equity_curve_max_dd",
    "exit_quality_score",
    "combine_window_scores",
]
