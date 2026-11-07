"""
DividendBacktestEngine — Phase12.

Portfolio NAV uses **qfq price path** with explicit label price_only
(cash dividends NOT reinvested → understates true dividend strategy TR).
Never adds cash on top of qfq.

Costs: commission 万三 both sides + stamp 千一 sell + slippage 0.1%/side
(aligned with v3/backtest/costs.py / ashare-tech-backtest).

Benchmark: 中证红利 000922 price series (price_only; no TR index available locally).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIVIDEND_CONFIG, KLINE_DAILY

COMMISSION = float(DIVIDEND_CONFIG.commission_rate)  # 0.0003
STAMP = float(DIVIDEND_CONFIG.stamp_tax_rate)  # 0.001
SLIP = float(DIVIDEND_CONFIG.slippage)  # 0.001


def _z6(c: str) -> str:
    return str(c).strip().zfill(6)


def load_index_price(code: str = "000922") -> pd.DataFrame:
    path = KLINE_DAILY / f"{_z6(code)}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"index bars missing: {path}")
    df = pd.read_parquet(path, columns=["date", "open", "close"])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def trading_days_between(index_df: pd.DataFrame, start: str, end: str) -> List[pd.Timestamp]:
    s = pd.Timestamp(start).normalize()
    e = pd.Timestamp(end).normalize()
    sub = index_df[(index_df["date"] >= s) & (index_df["date"] <= e)]
    return list(sub["date"])


def next_open_date(index_df: pd.DataFrame, as_of: str) -> Optional[pd.Timestamp]:
    t = pd.Timestamp(as_of).normalize()
    fut = index_df[index_df["date"] > t]
    if fut.empty:
        # same-day fallback if as_of is a trading day
        same = index_df[index_df["date"] == t]
        return None if same.empty else t
    return pd.Timestamp(fut.iloc[0]["date"])


@dataclass
class StrategySpec:
    name: str
    kind: str  # quality_core | entry_tilt | high_yield | index_bh
    k: Optional[int] = None  # max names; None = all qualifiers
    notes: str = ""


def select_portfolio(rows: List[Dict[str, Any]], spec: StrategySpec) -> List[str]:
    """Return ordered symbol list for equal-weight portfolio at a snapshot."""
    if spec.kind == "quality_core":
        pool = [
            r
            for r in rows
            if r.get("dividend_grade") in ("A+", "A")
            and r.get("dividend_risk") in ("SAFE", "WATCH")
            and not r.get("dividend_yield_trap")
        ]
        # drop TREND_BROKEN as exit-style filter even for new buys
        pool = [r for r in pool if r.get("dividend_entry_status") != "TREND_BROKEN"]
        pool.sort(
            key=lambda r: (
                -(r.get("dividend_opportunity_score") or -1),
                -(r.get("dividend_sustainability_score") or -1),
                r.get("symbol"),
            )
        )
    elif spec.kind == "entry_tilt":
        pool = [
            r
            for r in rows
            if r.get("dividend_grade") in ("A+", "A")
            and r.get("dividend_risk") in ("SAFE", "WATCH")
            and not r.get("dividend_yield_trap")
            and r.get("dividend_entry_status")
            in ("ACCUMULATE_WATCH", "PULLBACK_WATCH", "TREND_CONFIRM")
        ]
        pool.sort(
            key=lambda r: (
                -(r.get("dividend_opportunity_score") or -1),
                -(r.get("dividend_sustainability_score") or -1),
                -(r.get("dividend_trend_score") or -1),
                r.get("symbol"),
            )
        )
    elif spec.kind == "high_yield":
        pool = [r for r in rows if (r.get("dividend_yield_ttm") or 0) > 0]
        pool.sort(
            key=lambda r: (
                -(r.get("dividend_yield_ttm") or 0),
                r.get("symbol"),
            )
        )
    else:
        return []

    if spec.k is not None:
        pool = pool[: int(spec.k)]
    return [_z6(r["symbol"]) for r in pool]


def _px_on(df: pd.DataFrame, dt: pd.Timestamp, field: str = "close") -> Optional[float]:
    sub = df[df["date"] <= dt]
    if sub.empty:
        return None
    # prefer exact date match for execution day
    exact = df[df["date"] == dt]
    if not exact.empty:
        v = exact.iloc[0][field]
    else:
        v = sub.iloc[-1][field]
    if pd.isna(v):
        return None
    return float(v)


def simulate_equal_weight(
    *,
    rebalance_targets: Dict[pd.Timestamp, List[str]],  # signal_date -> symbols
    exec_dates: Dict[pd.Timestamp, pd.Timestamp],  # signal_date -> exec open date
    px: Dict[str, pd.DataFrame],
    calendar: List[pd.Timestamp],
    initial_nav: float = 1_000_000.0,
) -> Dict[str, Any]:
    """
    Quarterly/semiannual equal-weight rebalance.
    Signal at as_of close → trade at next session open (slip applied).
    Daily mark-to-market on qfq close. price_only (no cash dividends).
    """
    if not calendar:
        return {"error": "empty_calendar"}

    cash = float(initial_nav)
    # holdings: code -> shares
    holdings: Dict[str, float] = {}
    equity = []
    trades = []
    turnover_notional = 0.0
    peak = initial_nav
    max_dd = 0.0

    signal_by_exec = {exec_dates[s]: s for s in exec_dates}
    pending_signal: Optional[pd.Timestamp] = None

    for dt in calendar:
        # execute pending rebalance at today's open
        if dt in signal_by_exec:
            sig = signal_by_exec[dt]
            target = list(rebalance_targets.get(sig) or [])
            # liquidate all at open
            for code, sh in list(holdings.items()):
                df = px.get(code)
                if df is None or sh <= 0:
                    continue
                op = _px_on(df, dt, "open")
                if op is None or op <= 0:
                    op = _px_on(df, dt, "close")
                if op is None or op <= 0:
                    continue
                fill = op * (1.0 - SLIP)
                notional = sh * fill
                fee = notional * (COMMISSION + STAMP)
                cash += notional - fee
                turnover_notional += notional
                trades.append(
                    {
                        "date": str(dt.date()),
                        "signal_asof": str(sig.date()),
                        "side": "SELL",
                        "symbol": code,
                        "shares": sh,
                        "fill": fill,
                        "fee": fee,
                    }
                )
            holdings = {}
            # deploy equal weight to targets
            tradable = []
            for code in target:
                df = px.get(code)
                if df is None:
                    continue
                op = _px_on(df, dt, "open")
                if op is None or op <= 0:
                    op = _px_on(df, dt, "close")
                if op is None or op <= 0:
                    continue
                tradable.append((code, op))
            if tradable and cash > 0:
                per = cash / len(tradable)
                for code, op in tradable:
                    fill = op * (1.0 + SLIP)
                    fee_rate = COMMISSION
                    # shares such that shares*fill*(1+fee) ~= per
                    sh = per / (fill * (1.0 + fee_rate))
                    notional = sh * fill
                    fee = notional * fee_rate
                    if notional + fee > cash:
                        sh = cash / (fill * (1.0 + fee_rate))
                        notional = sh * fill
                        fee = notional * fee_rate
                    cash -= notional + fee
                    holdings[code] = holdings.get(code, 0.0) + sh
                    turnover_notional += notional
                    trades.append(
                        {
                            "date": str(dt.date()),
                            "signal_asof": str(sig.date()),
                            "side": "BUY",
                            "symbol": code,
                            "shares": sh,
                            "fill": fill,
                            "fee": fee,
                        }
                    )

        # mark to market
        nav = cash
        for code, sh in holdings.items():
            df = px.get(code)
            if df is None or sh <= 0:
                continue
            cl = _px_on(df, dt, "close")
            if cl is None:
                continue
            nav += sh * cl
        peak = max(peak, nav)
        dd = nav / peak - 1.0
        max_dd = min(max_dd, dd)
        equity.append({"date": str(dt.date()), "nav": nav, "n_hold": len(holdings), "cash": cash})

    return {
        "equity": equity,
        "trades": trades,
        "max_dd": max_dd,
        "turnover_notional": turnover_notional,
        "final_nav": equity[-1]["nav"] if equity else initial_nav,
        "initial_nav": initial_nav,
        "return_label": "price_only_qfq",
        "cost_model": {
            "commission": COMMISSION,
            "stamp_sell": STAMP,
            "slippage": SLIP,
        },
    }


def simulate_index_bh(
    index_df: pd.DataFrame,
    start: str,
    end: str,
    initial_nav: float = 1_000_000.0,
) -> Dict[str, Any]:
    """Buy index at first open after start, hold to end. price_only."""
    cal = trading_days_between(index_df, start, end)
    if len(cal) < 2:
        return {"error": "short_calendar", "equity": []}
    # buy on first day open
    d0 = cal[0]
    row0 = index_df[index_df["date"] == d0].iloc[0]
    buy_px = float(row0["open"]) * (1.0 + SLIP)
    fee = initial_nav * COMMISSION  # approx: spend almost all
    shares = (initial_nav - fee) / buy_px
    cash_spent = shares * buy_px
    fee = cash_spent * COMMISSION
    # recompute shares with fee
    shares = initial_nav / (buy_px * (1.0 + COMMISSION))
    equity = []
    peak = initial_nav
    max_dd = 0.0
    for dt in cal:
        cl = float(index_df[index_df["date"] == dt].iloc[0]["close"])
        nav = shares * cl
        peak = max(peak, nav)
        max_dd = min(max_dd, nav / peak - 1.0)
        equity.append({"date": str(dt.date()), "nav": nav, "n_hold": 1, "cash": 0.0})
    # sell at end for realized (optional) — leave marked
    return {
        "equity": equity,
        "trades": [
            {
                "date": str(d0.date()),
                "side": "BUY",
                "symbol": "000922",
                "shares": shares,
                "fill": buy_px,
                "fee": shares * buy_px * COMMISSION,
            }
        ],
        "max_dd": max_dd,
        "turnover_notional": shares * buy_px,
        "final_nav": equity[-1]["nav"],
        "initial_nav": initial_nav,
        "return_label": "price_only_index",
        "cost_model": {"commission": COMMISSION, "stamp_sell": STAMP, "slippage": SLIP},
    }


def metrics_from_equity(equity: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not equity or len(equity) < 2:
        return {"n_days": len(equity)}
    navs = np.array([e["nav"] for e in equity], dtype=float)
    rets = navs[1:] / navs[:-1] - 1.0
    total = float(navs[-1] / navs[0] - 1.0)
    n_years = max((pd.Timestamp(equity[-1]["date"]) - pd.Timestamp(equity[0]["date"])).days / 365.25, 1e-9)
    ann = float((1.0 + total) ** (1.0 / n_years) - 1.0) if total > -0.999 else float("nan")
    vol = float(rets.std(ddof=1) * math.sqrt(252)) if len(rets) > 2 else float("nan")
    peak = np.maximum.accumulate(navs)
    dd = navs / peak - 1.0
    max_dd = float(dd.min())
    calmar = float(ann / abs(max_dd)) if max_dd < 0 else float("nan")
    return {
        "start": equity[0]["date"],
        "end": equity[-1]["date"],
        "n_days": len(equity),
        "n_years": round(n_years, 3),
        "total_return": total,
        "ann_return": ann,
        "vol_ann": vol,
        "max_dd": max_dd,
        "calmar": calmar,
        "return_label": "price_only",
    }


def excess_vs(strat: Dict[str, Any], bench: Dict[str, Any]) -> Dict[str, Any]:
    if not strat.get("ann_return") or not bench.get("ann_return"):
        return {}
    return {
        "ann_excess": float(strat["ann_return"] - bench["ann_return"]),
        "total_excess": float(strat["total_return"] - bench["total_return"]),
        "dd_diff": float(strat["max_dd"] - bench["max_dd"]),
    }
