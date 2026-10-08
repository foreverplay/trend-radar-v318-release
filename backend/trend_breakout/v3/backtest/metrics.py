"""
Plan-style trade / aggregate metrics for Phase 9 BacktestEngine.

Computable now (Plan §19 proxies):
  PeakGain, ExitGain, TrendCaptureRatio, GivebackRatio, hold_days, max_dd_trade
  Aggregate: n, mean/median return, max DD (worst trade), avg hold,
             capture / giveback means, early_exit / false_break proxies

Deferred to Phase 10: full Walk-Forward / OOS matrix, strategy-grid ranking.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np


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


def trend_capture_ratio(exit_gain: Optional[float], peak_gain: Optional[float]) -> Optional[float]:
    """ExitGain / PeakGain when PeakGain > 0; else None."""
    eg, pg = _f(exit_gain), _f(peak_gain)
    if eg is None or pg is None or pg <= 1e-12:
        return None
    return eg / pg


def giveback_ratio(exit_gain: Optional[float], peak_gain: Optional[float]) -> Optional[float]:
    """(PeakGain - ExitGain) / PeakGain when PeakGain > 0."""
    eg, pg = _f(exit_gain), _f(peak_gain)
    if eg is None or pg is None or pg <= 1e-12:
        return None
    return (pg - eg) / pg


def trade_metrics(
    *,
    entry_price: float,
    exit_price: float,
    peak_close: float,
    trough_close: Optional[float] = None,
    hold_days: int = 0,
    buy_fee_frac: float = 0.0,
    sell_fee_frac: float = 0.0,
    false_break_seen: bool = False,
    exit_reason: str = "",
    early_exit_hold_lt: int = 3,
) -> Dict[str, Any]:
    """
    Per-trade metrics.

    Prices should be fill prices (slip already in); fees as fraction of notional
    (commission±stamp) so ExitGain is net of round-trip costs.
    """
    ep = float(entry_price)
    xp = float(exit_price)
    if ep <= 0:
        return {"ok": False, "error": "bad_entry"}

    # Net: pay fee on buy (effective entry up), fee on sell (effective exit down)
    eff_entry = ep * (1.0 + float(buy_fee_frac))
    eff_exit = xp * (1.0 - float(sell_fee_frac))
    exit_gain = (eff_exit - eff_entry) / eff_entry
    peak_gain = (float(peak_close) - ep) / ep if peak_close else None
    # trough vs entry (unrealized low)
    max_dd_trade = None
    if peak_close and trough_close is not None and float(peak_close) > 0:
        max_dd_trade = (float(peak_close) - float(trough_close)) / float(peak_close)
    elif trough_close is not None and ep > 0:
        max_dd_trade = max(0.0, (ep - float(trough_close)) / ep)

    tcr = trend_capture_ratio(exit_gain, peak_gain)
    gbr = giveback_ratio(exit_gain, peak_gain)
    early = bool(hold_days < int(early_exit_hold_lt) and exit_gain is not None and exit_gain < 0)

    return {
        "ok": True,
        "exit_gain": exit_gain,
        "peak_gain": peak_gain,
        "trend_capture_ratio": tcr,
        "giveback_ratio": gbr,
        "hold_days": int(hold_days),
        "max_dd_trade": max_dd_trade,
        "false_break_seen": bool(false_break_seen),
        "early_exit_proxy": early,
        "exit_reason": exit_reason,
        "gross_exit_gain": (xp - ep) / ep,
    }


def _nanmean(xs: Sequence[Optional[float]]) -> Optional[float]:
    arr = [float(x) for x in xs if x is not None and np.isfinite(float(x))]
    if not arr:
        return None
    return float(np.mean(arr))


def _nanmedian(xs: Sequence[Optional[float]]) -> Optional[float]:
    arr = [float(x) for x in xs if x is not None and np.isfinite(float(x))]
    if not arr:
        return None
    return float(np.median(arr))


def aggregate_metrics(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate Plan §19-style summary over closed trades."""
    rows = [t for t in trades if t and t.get("ok", True) and t.get("status") != "skipped"]
    closed = [t for t in rows if t.get("exit_gain") is not None or t.get("status") == "closed"]
    # prefer explicit exit_gain
    eg = [_f(t.get("exit_gain")) for t in closed]
    eg = [x for x in eg if x is not None]
    pg = [_f(t.get("peak_gain")) for t in closed]
    tcr = [_f(t.get("trend_capture_ratio")) for t in closed]
    gbr = [_f(t.get("giveback_ratio")) for t in closed]
    holds = [_f(t.get("hold_days")) for t in closed]
    dds = [_f(t.get("max_dd_trade")) for t in closed]
    n = len(closed)
    n_fb = sum(1 for t in closed if t.get("false_break_seen"))
    n_early = sum(1 for t in closed if t.get("early_exit_proxy"))
    n_win = sum(1 for x in eg if x is not None and x > 0)

    max_dd = None
    if dds:
        valid = [x for x in dds if x is not None]
        if valid:
            max_dd = float(max(valid))

    return {
        "n_closed": n,
        "n_input": len(rows),
        "n_win": n_win,
        "win_rate": (n_win / n) if n else None,
        "mean_return": _nanmean(eg),
        "median_return": _nanmedian(eg),
        "max_dd": max_dd,  # worst per-trade peak→trough DD
        "avg_hold_days": _nanmean(holds),
        "mean_peak_gain": _nanmean(pg),
        "mean_capture": _nanmean(tcr),
        "mean_giveback": _nanmean(gbr),
        "false_break_rate": (n_fb / n) if n else None,
        "early_exit_rate": (n_early / n) if n else None,
        "n_false_break": n_fb,
        "n_early_exit": n_early,
    }


def summary_markdown(
    agg: Dict[str, Any],
    *,
    strategy: str = "",
    entry_source: str = "",
    extra_lines: Optional[List[str]] = None,
) -> str:
    """Render aggregate as a short markdown block for CLI smoke."""

    def pct(x: Optional[float]) -> str:
        if x is None:
            return "n/a"
        return f"{100.0 * float(x):.2f}%"

    def num(x: Optional[float], nd: int = 2) -> str:
        if x is None:
            return "n/a"
        return f"{float(x):.{nd}f}"

    lines = [
        "## Backtest summary (Phase 9 — ExitEngine validation)",
        "",
        "> Observation / structure-exit research only. **Does not feed live selection.** 禁止荐买.",
        "",
        f"- **strategy**: `{strategy}`",
        f"- **entry_source**: `{entry_source}`",
        f"- **n_closed**: {agg.get('n_closed', 0)} (input rows={agg.get('n_input', 0)})",
        f"- **win_rate**: {pct(agg.get('win_rate'))} ({agg.get('n_win', 0)} wins)",
        f"- **mean_return (ExitGain)**: {pct(agg.get('mean_return'))}",
        f"- **median_return**: {pct(agg.get('median_return'))}",
        f"- **mean_peak_gain**: {pct(agg.get('mean_peak_gain'))}",
        f"- **mean_capture (Exit/Peak)**: {num(agg.get('mean_capture'), 3)}",
        f"- **mean_giveback**: {num(agg.get('mean_giveback'), 3)}",
        f"- **max_dd (worst trade)**: {pct(agg.get('max_dd'))}",
        f"- **avg_hold_days**: {num(agg.get('avg_hold_days'), 1)}",
        f"- **false_break_rate**: {pct(agg.get('false_break_rate'))}",
        f"- **early_exit_rate**: {pct(agg.get('early_exit_rate'))}",
    ]
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines)
    lines.append("")
    lines.append(
        "_Phase 10 deferred: full strategy grid (Fixed%/MA20/ATR/Swing/DynamicProtection) "
        "+ Walk-Forward / OOS matrix._"
    )
    return "\n".join(lines)


__all__ = [
    "trend_capture_ratio",
    "giveback_ratio",
    "trade_metrics",
    "aggregate_metrics",
    "summary_markdown",
]
