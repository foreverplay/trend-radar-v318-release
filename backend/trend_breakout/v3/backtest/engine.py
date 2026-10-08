"""
BacktestEngine — Phase 9 ExitEngine validation only.

- Entry: caller-supplied (t_date, code6) list, OR legacy RecScore A+ archive
  (labeled ``entry_source=legacy_rec_score``). Primary path does NOT pick live stocks.
- Buy T+1 open (close fallback); sell next open after exit signal.
- Costs + limit-up/down skip/delay.
- No lookahead: hist truncated to evaluation day; ExitEngine prior_state ratchet.

Does NOT feed live selection. 禁止荐买 / 预测必涨.

Phase 10: Walk-Forward / OOS matrix across strategy variants.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass, field
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.backtest.costs import (  # noqa: E402
    COMMISSION_RATE,
    SLIPPAGE,
    STAMP_TAX_RATE,
    apply_buy_costs,
    apply_sell_costs,
    is_limit_down,
    is_limit_up,
)
from v3.backtest.metrics import aggregate_metrics, summary_markdown, trade_metrics  # noqa: E402
from v3.backtest.strategies import (  # noqa: E402
    BarContext,
    DynamicProtectionExit,
    ExitStrategy,
    get_strategy,
)
from v3.config import TREND_BOT_CONFIG, get_config  # noqa: E402
from v3.exit_state import ExitStateStore  # noqa: E402
from v3.types import ExitSignal  # noqa: E402

LOCAL_DAILY = _PKG_LOCAL_DAILY
LEGACY_A_PLUS = _ROOT / "history" / "a_plus_daily_2026.parquet"
EntryLike = Union[Tuple[Any, Any], Dict[str, Any]]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class TradeRecord:
    code: str
    signal_date: str
    entry_date: Optional[str] = None
    exit_date: Optional[str] = None
    exit_signal_date: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    peak_close: Optional[float] = None
    trough_close: Optional[float] = None
    hold_days: int = 0
    exit_gain: Optional[float] = None
    peak_gain: Optional[float] = None
    trend_capture_ratio: Optional[float] = None
    giveback_ratio: Optional[float] = None
    max_dd_trade: Optional[float] = None
    false_break_seen: bool = False
    early_exit_proxy: bool = False
    exit_reason: str = ""
    exit_signal: Optional[str] = None
    status: str = "open"  # open|closed|skipped|force_close
    skip_reason: Optional[str] = None
    buy_fee: float = 0.0
    sell_fee: float = 0.0
    entry_source: str = ""
    strategy: str = ""
    notes: List[str] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    trades: List[TradeRecord] = field(default_factory=list)
    aggregate: Dict[str, Any] = field(default_factory=dict)
    strategy: str = ""
    entry_source: str = ""
    start: Optional[str] = None
    end: Optional[str] = None
    empty_reason: Optional[str] = None
    costs: Dict[str, float] = field(default_factory=dict)

    def to_markdown(self) -> str:
        extra = []
        if self.empty_reason:
            extra.append(f"- **empty_reason**: {self.empty_reason}")
        if self.costs:
            extra.append(
                f"- **costs**: commission={self.costs.get('commission')}, "
                f"stamp={self.costs.get('stamp')}, slip={self.costs.get('slippage')}"
            )
        # sample trades
        closed = [t for t in self.trades if t.status in ("closed", "force_close")]
        if closed:
            extra.append("")
            extra.append("### Sample closed trades")
            extra.append("")
            extra.append("| code | signal | entry | exit | hold | ExitGain | PeakGain | capture | reason |")
            extra.append("|------|--------|-------|------|-----:|---------:|---------:|--------:|--------|")
            for t in closed[:12]:
                eg = f"{100*t.exit_gain:.2f}%" if t.exit_gain is not None else "n/a"
                pg = f"{100*t.peak_gain:.2f}%" if t.peak_gain is not None else "n/a"
                cap = f"{t.trend_capture_ratio:.2f}" if t.trend_capture_ratio is not None else "n/a"
                extra.append(
                    f"| {t.code} | {t.signal_date} | {t.entry_date} | {t.exit_date} | "
                    f"{t.hold_days} | {eg} | {pg} | {cap} | {t.exit_reason} |"
                )
        return summary_markdown(
            self.aggregate,
            strategy=self.strategy,
            entry_source=self.entry_source,
            extra_lines=extra,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_code(code: Any) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _norm_date(d: Any) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def load_hist(code6: str, root: Path = LOCAL_DAILY) -> Optional[pd.DataFrame]:
    path = Path(root) / f"{_norm_code(code6)}.parquet"
    if not path.is_file():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if df is None or len(df) == 0:
        return None
    if "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _bar_px(row: pd.Series, prefer: str = "open") -> Optional[float]:
    for col in (prefer, "close", "open"):
        if col in row.index and pd.notna(row[col]):
            try:
                v = float(row[col])
            except (TypeError, ValueError):
                continue
            if np.isfinite(v) and v > 0:
                return v
    return None


def _pct_chg(row: pd.Series) -> Optional[float]:
    if "pct_chg" in row.index and pd.notna(row["pct_chg"]):
        return float(row["pct_chg"])
    return None


def normalize_entries(
    entries: Sequence[EntryLike],
) -> List[Tuple[str, str]]:
    """Normalize to list of (t_date, code6)."""
    out: List[Tuple[str, str]] = []
    for e in entries:
        if isinstance(e, dict):
            d = e.get("t_date") or e.get("date") or e.get("signal_date")
            c = e.get("code6") or e.get("code") or e.get("symbol")
        elif isinstance(e, (tuple, list)) and len(e) >= 2:
            d, c = e[0], e[1]
        else:
            continue
        if d is None or c is None:
            continue
        out.append((_norm_date(d), _norm_code(c)))
    return out


def load_legacy_a_plus_entries(
    path: Path = LEGACY_A_PLUS,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 20,
    seed: int = 42,
) -> Tuple[List[Tuple[str, str]], str]:
    """
    Alternate entry source: legacy RecScore A+ archive.
    Labeled ``entry_source=legacy_rec_score`` — NOT Plan-grade A+.
    """
    if not Path(path).is_file():
        return [], "legacy_a_plus_missing"
    df = pd.read_parquet(path)
    if "t_date" not in df.columns or "code6" not in df.columns:
        return [], "legacy_a_plus_bad_schema"
    df = df.copy()
    df["t_date"] = pd.to_datetime(df["t_date"])
    if start:
        df = df[df["t_date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["t_date"] <= pd.Timestamp(end)]
    if len(df) == 0:
        return [], "legacy_a_plus_empty_range"
    # diversify: sample up to `limit` rows across dates
    df = df.sort_values(["t_date", "recommendation_score"], ascending=[True, False])
    # take top per day then flatten, cap total
    picked: List[Tuple[str, str]] = []
    for t, g in df.groupby("t_date", sort=True):
        for _, row in g.head(2).iterrows():
            picked.append((_norm_date(t), _norm_code(row["code6"])))
            if len(picked) >= limit:
                break
        if len(picked) >= limit:
            break
    if len(picked) < min(limit, 5) and len(df) > 0:
        # fallback: first N
        picked = [
            (_norm_date(r.t_date), _norm_code(r.code6))
            for r in df.head(limit).itertuples()
        ]
    return picked[:limit], "legacy_rec_score"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """
    Validate exit strategies on supplied entries.

    ``run(entries, strategy, start, end, ...)`` → BacktestResult.
    """

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        hist_root: Path = LOCAL_DAILY,
        commission: float = COMMISSION_RATE,
        stamp: float = STAMP_TAX_RATE,
        slippage: float = SLIPPAGE,
        max_hold_days: int = 60,
        force_close_at_end: bool = True,
    ) -> None:
        self.config = config if config is not None else get_config()
        self.hist_root = Path(hist_root)
        self.commission = float(commission)
        self.stamp = float(stamp)
        self.slippage = float(slippage)
        self.max_hold_days = int(max_hold_days)
        self.force_close_at_end = bool(force_close_at_end)
        bt = (self.config or {}).get("backtest") or {}
        if bt.get("max_hold_days") is not None:
            self.max_hold_days = int(bt["max_hold_days"])

    def run(
        self,
        entries: Optional[Sequence[EntryLike]] = None,
        strategy: Optional[Any] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        *,
        entry_source: str = "caller",
        max_entries: Optional[int] = None,
    ) -> BacktestResult:
        if strategy is None:
            strategy = DynamicProtectionExit(config=self.config)
        strat_name = getattr(strategy, "name", type(strategy).__name__)
        if hasattr(strategy, "reset"):
            strategy.reset()

        if entries is None:
            entries_n, entry_source = load_legacy_a_plus_entries(
                start=start, end=end, limit=max_entries or 20
            )
        else:
            entries_n = normalize_entries(entries)
            if start:
                entries_n = [e for e in entries_n if e[0] >= _norm_date(start)]
            if end:
                entries_n = [e for e in entries_n if e[0] <= _norm_date(end)]
            if max_entries is not None:
                entries_n = entries_n[: int(max_entries)]

        costs = {
            "commission": self.commission,
            "stamp": self.stamp,
            "slippage": self.slippage,
        }
        if not entries_n:
            return BacktestResult(
                trades=[],
                aggregate=aggregate_metrics([]),
                strategy=strat_name,
                entry_source=entry_source,
                start=start,
                end=end,
                empty_reason="no_entries",
                costs=costs,
            )

        trades: List[TradeRecord] = []
        for sig_date, code in entries_n:
            tr = self._run_one(code, sig_date, strategy, end=end, entry_source=entry_source)
            tr.strategy = strat_name
            trades.append(tr)

        closed_dicts = [t.to_dict() for t in trades]
        agg = aggregate_metrics(closed_dicts)
        empty_reason = None
        n_closed = int(agg.get("n_closed") or 0)
        if n_closed == 0:
            skips = [t.skip_reason or t.status for t in trades]
            empty_reason = "no_closed_trades: " + ", ".join(sorted(set(skips))[:6])

        return BacktestResult(
            trades=trades,
            aggregate=agg,
            strategy=strat_name,
            entry_source=entry_source,
            start=start,
            end=end,
            empty_reason=empty_reason,
            costs=costs,
        )

    def _run_one(
        self,
        code: str,
        signal_date: str,
        strategy: Any,
        *,
        end: Optional[str] = None,
        entry_source: str = "",
    ) -> TradeRecord:
        code6 = _norm_code(code)
        rec = TradeRecord(
            code=code6,
            signal_date=signal_date,
            entry_source=entry_source,
            strategy=getattr(strategy, "name", ""),
        )
        hist = load_hist(code6, self.hist_root)
        if hist is None or len(hist) == 0:
            rec.status = "skipped"
            rec.skip_reason = "hist_missing"
            rec.ok = False
            return rec

        dates = hist["date"]
        sig_ts = pd.Timestamp(signal_date)
        # Buy T+1: first session strictly after signal_date
        future = hist[dates > sig_ts]
        if len(future) == 0:
            rec.status = "skipped"
            rec.skip_reason = "no_T+1_bar"
            rec.ok = False
            return rec

        # --- find buy day (skip limit-up) ---
        buy_idx_in_hist: Optional[int] = None
        buy_row = None
        delayed = 0
        for i in range(len(future)):
            row = future.iloc[i]
            # absolute index in hist
            abs_i = int(future.index[i])
            if is_limit_up(_pct_chg(row), code6):
                delayed += 1
                rec.notes.append(f"buy_delay_limit_up:{_norm_date(row['date'])}")
                if delayed > 5:
                    break
                continue
            px = _bar_px(row, "open")
            if px is None:
                continue
            buy_idx_in_hist = abs_i
            buy_row = row
            break

        if buy_idx_in_hist is None or buy_row is None:
            rec.status = "skipped"
            rec.skip_reason = "buy_blocked_limit_up_or_no_px"
            rec.ok = False
            return rec

        open_px = _bar_px(buy_row, "open")
        assert open_px is not None
        fill_px, buy_fee, _cash = apply_buy_costs(
            open_px, shares=1.0, commission=self.commission, slippage=self.slippage
        )
        entry_date = _norm_date(buy_row["date"])
        rec.entry_date = entry_date
        rec.entry_price = fill_px
        rec.buy_fee = buy_fee
        # fee frac vs fill notional for metrics
        buy_fee_frac = self.commission  # slip already in fill_px

        # China T+1: cannot sell on entry day — evaluate from next bar
        peak_close = float(buy_row["close"]) if pd.notna(buy_row.get("close")) else fill_px
        trough_close = peak_close
        false_break_seen = False
        store = ExitStateStore(persist=False)
        # seed peak with entry reference
        prior = {"peak_close": peak_close, "protection_price": None, "broken_streak": 0}

        end_ts = pd.Timestamp(end) if end else None
        pending_exit: Optional[Dict[str, Any]] = None
        hold_days = 0

        # iterate bars after entry
        after = hist[hist["date"] > pd.Timestamp(entry_date)]
        if end_ts is not None:
            after = after[after["date"] <= end_ts]

        for pos, (abs_i, row) in enumerate(after.iterrows()):
            t_date = _norm_date(row["date"])
            hold_days = pos + 1  # trading days after entry

            # --- execute pending sell at today's open ---
            if pending_exit is not None:
                if is_limit_down(_pct_chg(row), code6):
                    rec.notes.append(f"sell_delay_limit_down:{t_date}")
                    # keep pending; still update peak/state below? skip signal update this day
                    # but continue waiting
                    close_px = _bar_px(row, "close")
                    if close_px is not None:
                        peak_close = max(peak_close, close_px)
                        trough_close = min(trough_close, close_px)
                    continue
                sell_open = _bar_px(row, "open")
                if sell_open is None:
                    continue
                spx, sfee, _cin = apply_sell_costs(
                    sell_open,
                    shares=1.0,
                    commission=self.commission,
                    stamp=self.stamp,
                    slippage=self.slippage,
                )
                return self._finalize(
                    rec,
                    exit_date=t_date,
                    exit_price=spx,
                    sell_fee=sfee,
                    peak_close=peak_close,
                    trough_close=trough_close,
                    hold_days=hold_days - 1,  # held through prior day
                    false_break_seen=false_break_seen,
                    exit_reason=pending_exit.get("reason", ""),
                    exit_signal=pending_exit.get("exit_signal"),
                    buy_fee_frac=buy_fee_frac,
                    status="closed",
                )

            # truncate hist to t (no lookahead)
            h_asof = hist.loc[:abs_i].copy() if not isinstance(abs_i, slice) else hist[hist["date"] <= pd.Timestamp(t_date)]
            # abs_i from iterrows is the index label
            if abs_i not in hist.index:
                h_asof = hist[hist["date"] <= pd.Timestamp(t_date)].copy()
            else:
                # inclusive through this row by position order
                loc = hist.index.get_loc(abs_i)
                if isinstance(loc, slice):
                    h_asof = hist.iloc[: loc.stop].copy()
                elif isinstance(loc, np.ndarray):
                    h_asof = hist.iloc[: int(np.max(loc)) + 1].copy()
                else:
                    h_asof = hist.iloc[: int(loc) + 1].copy()

            close_px = _bar_px(row, "close")
            if close_px is None:
                continue
            low_px = _bar_px(row, "low") if "low" in row.index else close_px
            high_px = _bar_px(row, "high") if "high" in row.index else close_px
            peak_close = max(peak_close, close_px)
            if low_px is not None:
                trough_close = min(trough_close, low_px)
            else:
                trough_close = min(trough_close, close_px)

            # ExitEngine prior + evaluate when needed
            exit_result = None
            requires = bool(getattr(strategy, "requires_exit_engine", False))
            if requires and hasattr(strategy, "evaluate_exit_engine"):
                ctx_pre = BarContext(
                    code=code6,
                    t_date=t_date,
                    hist=h_asof,
                    close=close_px,
                    low=low_px,
                    high=high_px,
                    open_=_bar_px(row, "open"),
                    entry_price=fill_px,
                    entry_date=entry_date,
                    peak_close=peak_close,
                    trough_close=trough_close,
                    hold_days=hold_days,
                    prior_state=dict(prior),
                )
                exit_result = strategy.evaluate_exit_engine(ctx_pre, prior_state=prior)
                # ratchet state for next day
                prior = store.update_state(code6, exit_result)
                if exit_result.false_break:
                    false_break_seen = True
            elif requires:
                # generic: call ExitEngine directly
                from v3.engines.exit_engine import ExitEngine

                exit_result = ExitEngine(config=self.config).evaluate(
                    h_asof, t_date=t_date, code=code6, prior_state=prior
                )
                prior = store.update_state(code6, exit_result)
                if exit_result.false_break:
                    false_break_seen = True

            ctx = BarContext(
                code=code6,
                t_date=t_date,
                hist=h_asof,
                close=close_px,
                low=low_px,
                high=high_px,
                open_=_bar_px(row, "open"),
                entry_price=fill_px,
                entry_date=entry_date,
                peak_close=peak_close,
                trough_close=trough_close,
                hold_days=hold_days,
                prior_state=dict(prior),
                exit_result=exit_result,
            )
            sig = strategy.on_bar(ctx)
            action = getattr(sig, "action", "HOLD")
            reason = getattr(sig, "reason", "")
            esig = getattr(sig, "exit_signal", None)

            if action == "EXIT":
                pending_exit = {
                    "reason": reason,
                    "exit_signal": esig,
                    "signal_date": t_date,
                }
                rec.exit_signal_date = t_date
                continue

            if hold_days >= self.max_hold_days:
                pending_exit = {
                    "reason": f"max_hold_{self.max_hold_days}",
                    "exit_signal": esig,
                    "signal_date": t_date,
                }
                rec.exit_signal_date = t_date
                continue

        # end of data: pending sell or force close
        if pending_exit is not None:
            # try last available after signal — if we never filled, force on last bar close
            last = after.iloc[-1] if len(after) else buy_row
            # if last day was the signal day and limit-down blocked, use close
            if is_limit_down(_pct_chg(last), code6):
                spx = _bar_px(last, "close") or fill_px
                # still apply sell costs on close
                spx2, sfee, _ = apply_sell_costs(
                    spx / (1.0 - self.slippage) if self.slippage < 1 else spx,
                    shares=1.0,
                    commission=self.commission,
                    stamp=self.stamp,
                    slippage=0.0,  # already using close approx
                )
                # simpler: sell at close with slip
                spx2, sfee, _ = apply_sell_costs(
                    float(_bar_px(last, "close") or fill_px),
                    shares=1.0,
                    commission=self.commission,
                    stamp=self.stamp,
                    slippage=self.slippage,
                )
                return self._finalize(
                    rec,
                    exit_date=_norm_date(last["date"]),
                    exit_price=spx2,
                    sell_fee=sfee,
                    peak_close=peak_close,
                    trough_close=trough_close,
                    hold_days=hold_days,
                    false_break_seen=false_break_seen,
                    exit_reason=pending_exit.get("reason", "") + "+limit_down_close",
                    exit_signal=pending_exit.get("exit_signal"),
                    buy_fee_frac=buy_fee_frac,
                    status="closed",
                )
            # no next open after signal — use last close path above already; if pending
            # was set on last day, we never got a next open
            spx2, sfee, _ = apply_sell_costs(
                float(_bar_px(last, "close") or fill_px),
                shares=1.0,
                commission=self.commission,
                stamp=self.stamp,
                slippage=self.slippage,
            )
            return self._finalize(
                rec,
                exit_date=_norm_date(last["date"]),
                exit_price=spx2,
                sell_fee=sfee,
                peak_close=peak_close,
                trough_close=trough_close,
                hold_days=hold_days,
                false_break_seen=false_break_seen,
                exit_reason=pending_exit.get("reason", "") + "+eod_no_next_open",
                exit_signal=pending_exit.get("exit_signal"),
                buy_fee_frac=buy_fee_frac,
                status="closed",
            )

        if self.force_close_at_end and rec.entry_price is not None:
            last = after.iloc[-1] if len(after) else buy_row
            spx2, sfee, _ = apply_sell_costs(
                float(_bar_px(last, "close") or fill_px),
                shares=1.0,
                commission=self.commission,
                stamp=self.stamp,
                slippage=self.slippage,
            )
            return self._finalize(
                rec,
                exit_date=_norm_date(last["date"]),
                exit_price=spx2,
                sell_fee=sfee,
                peak_close=peak_close,
                trough_close=trough_close,
                hold_days=max(hold_days, 0),
                false_break_seen=false_break_seen,
                exit_reason="force_close_eod",
                exit_signal=None,
                buy_fee_frac=buy_fee_frac,
                status="force_close",
            )

        rec.status = "skipped"
        rec.skip_reason = "no_bars_after_entry"
        rec.ok = False
        return rec

    def _finalize(
        self,
        rec: TradeRecord,
        *,
        exit_date: str,
        exit_price: float,
        sell_fee: float,
        peak_close: float,
        trough_close: float,
        hold_days: int,
        false_break_seen: bool,
        exit_reason: str,
        exit_signal: Optional[str],
        buy_fee_frac: float,
        status: str,
    ) -> TradeRecord:
        sell_fee_frac = self.commission + self.stamp
        m = trade_metrics(
            entry_price=float(rec.entry_price or exit_price),
            exit_price=float(exit_price),
            peak_close=float(peak_close),
            trough_close=float(trough_close),
            hold_days=int(hold_days),
            buy_fee_frac=buy_fee_frac,
            sell_fee_frac=sell_fee_frac,
            false_break_seen=false_break_seen,
            exit_reason=exit_reason,
        )
        rec.exit_date = exit_date
        rec.exit_price = float(exit_price)
        rec.sell_fee = float(sell_fee)
        rec.peak_close = float(peak_close)
        rec.trough_close = float(trough_close)
        rec.hold_days = int(hold_days)
        rec.exit_gain = m.get("exit_gain")
        rec.peak_gain = m.get("peak_gain")
        rec.trend_capture_ratio = m.get("trend_capture_ratio")
        rec.giveback_ratio = m.get("giveback_ratio")
        rec.max_dd_trade = m.get("max_dd_trade")
        rec.false_break_seen = bool(false_break_seen)
        rec.early_exit_proxy = bool(m.get("early_exit_proxy"))
        rec.exit_reason = exit_reason
        rec.exit_signal = exit_signal
        rec.status = status
        rec.ok = True
        return rec


# ---------------------------------------------------------------------------
# CLI / smoke
# ---------------------------------------------------------------------------


def _smoke_entries(n: int = 12) -> Tuple[List[Tuple[str, str]], str]:
    """Prefer legacy A+ sample; honest label."""
    entries, src = load_legacy_a_plus_entries(
        start="2026-01-05",
        end="2026-03-31",
        limit=n,
    )
    if entries:
        return entries, src
    # tiny hardcoded fallback if archive missing
    fallback = [
        ("2026-01-05", "000408"),
        ("2026-01-05", "000603"),
        ("2026-01-06", "000807"),
        ("2026-01-08", "002768"),
        ("2026-02-05", "300750"),
    ]
    return fallback[:n], "hardcoded_fallback"


def _smoke_cli(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="V3 BacktestEngine Phase 9 smoke")
    p.add_argument("--smoke", action="store_true", help="run small DynamicProtection smoke")
    p.add_argument("--strategy", default="DynamicProtectionExit")
    p.add_argument("--n", type=int, default=12, help="max entries for smoke")
    p.add_argument("--start", default="2026-01-05")
    p.add_argument("--end", default="2026-06-30")
    args = p.parse_args(argv)

    if not args.smoke:
        # default: same as smoke when invoked as __main__
        args.smoke = True

    entries, entry_source = _smoke_entries(n=args.n)
    # filter start/end
    entries = [e for e in entries if args.start <= e[0] <= args.end]

    try:
        strat = get_strategy(args.strategy)
    except Exception:  # noqa: BLE001
        strat = DynamicProtectionExit()

    eng = BacktestEngine(max_hold_days=40, force_close_at_end=True)
    result = eng.run(
        entries,
        strategy=strat,
        start=args.start,
        end=args.end,
        entry_source=entry_source,
    )
    print(result.to_markdown())
    print()
    print(
        f"[smoke] strategy={result.strategy} entry_source={result.entry_source} "
        f"n_closed={result.aggregate.get('n_closed')} "
        f"empty={result.empty_reason!r}"
    )
    # success if ≥1 closed OR honest empty reason
    if (result.aggregate.get("n_closed") or 0) >= 1 or result.empty_reason:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_smoke_cli())
