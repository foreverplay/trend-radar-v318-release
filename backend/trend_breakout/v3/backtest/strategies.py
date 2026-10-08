"""
Exit strategy interface for Phase 9–11 BacktestEngine.

Phase 10 expands the Plan §18 grid:
  Fixed5/8/10/15%, MA20 (±ATR), SwingLow (±ATR), BreakoutSupport (±ATR),
  StructureSupport-0.5ATR, DynamicProtection, DynamicProtection+MaxDrawdown.

confirm_days: level strategies use consecutive Close-below streak;
DynamicProtection* passes confirm_days into ExitEngine config.

禁止荐买 / 预测必涨. Backtest does not feed live selection.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from v3.config import get_config
from v3.engines.exit_engine import ExitEngine, ExitEngineResult
from v3.types import ExitSignal


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def _ma20(hist: pd.DataFrame) -> Optional[float]:
    if hist is None or "close" not in hist.columns or len(hist) < 20:
        return None
    return _safe_float(hist["close"].astype(float).iloc[-20:].mean())


def _atr14(hist: pd.DataFrame) -> Optional[float]:
    if hist is None or len(hist) == 0:
        return None
    if "ATR14" in hist.columns:
        v = _safe_float(hist["ATR14"].iloc[-1])
        if v is not None and v > 0:
            return v
    need = {"high", "low", "close"}
    if not need.issubset(set(hist.columns)) or len(hist) < 2:
        return None
    h = hist["high"].astype(float)
    l = hist["low"].astype(float)
    c = hist["close"].astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    window = tr.iloc[-14:] if len(tr) >= 14 else tr.iloc[1:]
    if len(window) == 0:
        return None
    v = _safe_float(window.mean())
    return v if v is not None and v > 0 else None


def _swing_low(hist: pd.DataFrame, lookback: int = 10) -> Optional[float]:
    if hist is None or "low" not in hist.columns or len(hist) == 0:
        return None
    n = max(1, int(lookback))
    return _safe_float(hist["low"].astype(float).iloc[-n:].min())


def _breakout_support(hist: pd.DataFrame, lookback: int = 40, platform: int = 10) -> Optional[float]:
    """
    OHLCV proxy for BreakoutSupport (no TechnicalEngine / no lookahead).

    Find the bar of the highest high in the last ``lookback`` bars; take the
    min low over the ``platform`` bars ending at that high (incl.). Fallback:
    swing low.
    """
    if hist is None or len(hist) < 5:
        return _swing_low(hist)
    need = {"high", "low"}
    if not need.issubset(set(hist.columns)):
        return _swing_low(hist)
    n = min(int(lookback), len(hist))
    window = hist.iloc[-n:]
    highs = window["high"].astype(float)
    if highs.isna().all():
        return _swing_low(hist)
    # position of max high within window
    rel = int(highs.values.argmax())
    plat = max(1, int(platform))
    start = max(0, rel - plat + 1)
    lows = window["low"].astype(float).iloc[start : rel + 1]
    v = _safe_float(lows.min())
    if v is None:
        return _swing_low(hist)
    return v


def _structure_support(hist: pd.DataFrame, swing_lookback: int = 10) -> Optional[float]:
    """StructureSupport ≈ max(SwingLow, BreakoutSupport, MA20) when available."""
    parts: List[float] = []
    for v in (
        _swing_low(hist, lookback=swing_lookback),
        _breakout_support(hist),
        _ma20(hist),
    ):
        if v is not None:
            parts.append(v)
    if not parts:
        return None
    return max(parts)


@dataclass
class BarContext:
    """Per-bar inputs for ``ExitStrategy.on_bar`` (no lookahead — hist ends at t)."""

    code: str
    t_date: str
    hist: pd.DataFrame
    close: float
    low: Optional[float]
    high: Optional[float]
    open_: Optional[float]
    entry_price: float
    entry_date: str
    peak_close: float
    trough_close: float
    hold_days: int
    prior_state: Dict[str, Any] = field(default_factory=dict)
    exit_result: Optional[ExitEngineResult] = None


@dataclass
class StrategySignal:
    """Exit decision for the bar (engine sells next open on action=EXIT)."""

    action: str  # "HOLD" | "EXIT"
    reason: str = ""
    exit_signal: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExitStrategy(Protocol):
    """Phase 10 plug-in contract."""

    name: str
    requires_exit_engine: bool

    def reset(self) -> None: ...

    def on_bar(self, ctx: BarContext) -> StrategySignal: ...


class _ConfirmHelper:
    """Consecutive Close-below confirmation across bars of one trade."""

    def __init__(self, confirm_days: int = 0) -> None:
        self.confirm_days = int(confirm_days)
        self._streak = 0
        self._entry_key: Optional[tuple] = None

    def reset(self) -> None:
        self._streak = 0
        self._entry_key = None

    def confirmed(self, ctx: BarContext, below: bool) -> bool:
        key = (ctx.code, ctx.entry_date)
        if key != self._entry_key:
            self._entry_key = key
            self._streak = 0
        if below:
            self._streak += 1
        else:
            self._streak = 0
        if self.confirm_days <= 0:
            return bool(below)
        return self._streak >= self.confirm_days


# ---------------------------------------------------------------------------
# DynamicProtection (ExitEngine)
# ---------------------------------------------------------------------------


class DynamicProtectionExit:
    """
    Uses ExitEngine SELL / PROTECT rules.

    Default: EXIT on SELL (structure TREND_BROKEN).
    ``exit_on_protect=True`` also exits on PROTECT (aggressive; optional).
    REDUCE_RISK alone does not exit (observation / risk trim — not full exit).
    """

    name = "DynamicProtection"
    requires_exit_engine = True

    def __init__(
        self,
        *,
        exit_on_protect: bool = False,
        exit_on_reduce_risk: bool = False,
        confirm_days: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        base = config if config is not None else get_config()
        self.config = deepcopy(base)
        if confirm_days is not None:
            self.config.setdefault("exit", {})
            self.config["exit"]["confirm_days"] = int(confirm_days)
        self.confirm_days = int(
            (self.config.get("exit") or {}).get("confirm_days", 0) or 0
        )
        self.exit_on_protect = bool(exit_on_protect)
        self.exit_on_reduce_risk = bool(exit_on_reduce_risk)
        self._engine = ExitEngine(config=self.config)
        # keep legacy alias visible in rankings
        self.name = "DynamicProtection" if self.confirm_days == 0 else f"DynamicProtection_c{self.confirm_days}"

    def reset(self) -> None:
        return None

    def evaluate_exit_engine(
        self, ctx: BarContext, prior_state: Optional[Dict[str, Any]] = None
    ) -> ExitEngineResult:
        return self._engine.evaluate(
            ctx.hist,
            t_date=ctx.t_date,
            code=ctx.code,
            prior_state=prior_state if prior_state is not None else ctx.prior_state,
            config=self.config,
        )

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        r = ctx.exit_result
        if r is None:
            r = self.evaluate_exit_engine(ctx)
        sig = r.exit_signal
        sig_s = sig.value if isinstance(sig, ExitSignal) else str(sig or "HOLD")
        tags = list(r.tags or [])
        if r.false_break:
            tags.append("FALSE_BREAK")

        if sig_s == ExitSignal.SELL.value or r.broken_confirmed:
            return StrategySignal(
                action="EXIT",
                reason="ExitEngine_SELL",
                exit_signal=sig_s,
                tags=tags,
                meta={"broken_confirmed": r.broken_confirmed, "false_break": r.false_break},
            )
        if self.exit_on_protect and sig_s == ExitSignal.PROTECT.value:
            return StrategySignal(
                action="EXIT",
                reason="ExitEngine_PROTECT",
                exit_signal=sig_s,
                tags=tags,
                meta={"false_break": r.false_break},
            )
        if self.exit_on_reduce_risk and sig_s == ExitSignal.REDUCE_RISK.value:
            return StrategySignal(
                action="EXIT",
                reason="ExitEngine_REDUCE_RISK",
                exit_signal=sig_s,
                tags=tags,
            )
        return StrategySignal(
            action="HOLD",
            reason=f"ExitEngine_{sig_s}",
            exit_signal=sig_s,
            tags=tags,
            meta={"false_break": bool(r.false_break)},
        )


class DynamicProtectionMaxDD(DynamicProtectionExit):
    """
    DynamicProtection + hard trade max-drawdown from PeakClose.

    Exits when:
      - ExitEngine SELL (same as parent), or
      - (peak_close - close) / peak_close >= max_dd  (default from exit.maxDrawdown)
    """

    name = "DynamicProtection+MaxDrawdown"

    def __init__(
        self,
        max_dd: Optional[float] = None,
        *,
        exit_on_protect: bool = False,
        confirm_days: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            exit_on_protect=exit_on_protect,
            confirm_days=confirm_days,
            config=config,
        )
        if max_dd is None:
            ex = (self.config or {}).get("exit") or {}
            if ex.get("maxDrawdown") is not None:
                v = float(ex["maxDrawdown"])
                max_dd = v / 100.0 if v > 1.0 else v
            else:
                max_dd = float(ex.get("max_dd_pct", 15.0)) / 100.0
        self.max_dd = float(max_dd)
        base = "DynamicProtection+MaxDrawdown"
        self.name = base if self.confirm_days == 0 else f"{base}_c{self.confirm_days}"

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        base = super().on_bar(ctx)
        if base.action == "EXIT":
            return base
        peak = _safe_float(ctx.peak_close) or _safe_float(ctx.close)
        close = _safe_float(ctx.close)
        if peak is not None and close is not None and peak > 0:
            dd = (peak - close) / peak
            if dd >= self.max_dd:
                return StrategySignal(
                    action="EXIT",
                    reason=f"MaxDD>={self.max_dd:.0%}",
                    exit_signal=base.exit_signal,
                    tags=list(base.tags) + ["MAX_DD_STOP"],
                    meta={"trade_dd": dd, "max_dd": self.max_dd},
                )
        return base


# ---------------------------------------------------------------------------
# Level / band exits (Plan §18 grid)
# ---------------------------------------------------------------------------


class LevelExit:
    """Exit when Close < level(ctx); optional ATR cushion + confirm_days."""

    name = "LevelExit"
    requires_exit_engine = False

    def __init__(
        self,
        *,
        name: str,
        level_fn: Callable[[BarContext], Optional[float]],
        atr_mult: float = 0.0,
        confirm_days: int = 0,
        reason_tag: str = "LEVEL_BREAK",
    ) -> None:
        self.name = str(name)
        self._level_fn = level_fn
        self.atr_mult = float(atr_mult)
        self.confirm_days = int(confirm_days)
        self.reason_tag = reason_tag
        self._confirm = _ConfirmHelper(confirm_days)

    def reset(self) -> None:
        self._confirm.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        level = self._level_fn(ctx)
        if level is None:
            return StrategySignal(action="HOLD", reason="level_unavailable")
        thr = level
        atr = None
        if self.atr_mult > 0:
            atr = _atr14(ctx.hist)
            if atr is None:
                return StrategySignal(action="HOLD", reason="atr_unavailable")
            thr = level - self.atr_mult * atr
        below = ctx.close < thr
        if self._confirm.confirmed(ctx, below):
            return StrategySignal(
                action="EXIT",
                reason=f"Close<{self.name}",
                tags=[self.reason_tag],
                meta={"level": level, "threshold": thr, "atr14": atr, "streak": self._confirm._streak},
            )
        return StrategySignal(
            action="HOLD",
            reason="above_level",
            meta={"level": level, "threshold": thr, "atr14": atr, "streak": self._confirm._streak},
        )


class MA20:
    """Exit when Close < MA20."""

    name = "MA20"
    requires_exit_engine = False

    def __init__(self, confirm_days: int = 0) -> None:
        self.confirm_days = int(confirm_days)
        self.name = "MA20" if self.confirm_days == 0 else f"MA20_c{self.confirm_days}"
        self._inner = LevelExit(
            name=self.name,
            level_fn=lambda ctx: _ma20(ctx.hist),
            atr_mult=0.0,
            confirm_days=confirm_days,
            reason_tag="MA20_BREAK",
        )

    def reset(self) -> None:
        self._inner.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        return self._inner.on_bar(ctx)


class MA20_ATR:
    """Exit when Close < MA20 - atr_mult*ATR14."""

    name = "MA20_ATR"
    requires_exit_engine = False

    def __init__(self, atr_mult: float = 0.5, confirm_days: int = 0) -> None:
        self.atr_mult = float(atr_mult)
        self.confirm_days = int(confirm_days)
        label = f"MA20-{self.atr_mult:g}ATR"
        if self.confirm_days:
            label = f"{label}_c{self.confirm_days}"
        self.name = label
        self._inner = LevelExit(
            name=self.name,
            level_fn=lambda ctx: _ma20(ctx.hist),
            atr_mult=self.atr_mult,
            confirm_days=confirm_days,
            reason_tag="MA20_ATR_BREAK",
        )

    def reset(self) -> None:
        self._inner.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        return self._inner.on_bar(ctx)


# Back-compat thin aliases
class MA20_0p5ATR(MA20_ATR):
    def __init__(self, atr_mult: float = 0.5, confirm_days: int = 0) -> None:
        super().__init__(atr_mult=atr_mult, confirm_days=confirm_days)


class MA20_1ATR(MA20_ATR):
    def __init__(self, atr_mult: float = 1.0, confirm_days: int = 0) -> None:
        super().__init__(atr_mult=atr_mult, confirm_days=confirm_days)


class SwingLow:
    """Exit when Close < RecentSwingLow."""

    name = "SwingLow"
    requires_exit_engine = False

    def __init__(self, lookback: int = 10, atr_mult: float = 0.0, confirm_days: int = 0) -> None:
        self.lookback = int(lookback)
        self.atr_mult = float(atr_mult)
        self.confirm_days = int(confirm_days)
        if self.atr_mult > 0:
            label = f"SwingLow-{self.atr_mult:g}ATR"
        else:
            label = "SwingLow"
        if self.confirm_days:
            label = f"{label}_c{self.confirm_days}"
        self.name = label
        lb = self.lookback
        self._inner = LevelExit(
            name=self.name,
            level_fn=lambda ctx, _lb=lb: _swing_low(ctx.hist, lookback=_lb),
            atr_mult=self.atr_mult,
            confirm_days=confirm_days,
            reason_tag="SWING_LOW_BREAK",
        )

    def reset(self) -> None:
        self._inner.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        return self._inner.on_bar(ctx)


class BreakoutSupportExit:
    """Exit when Close < BreakoutSupport (− atr_mult*ATR)."""

    name = "BreakoutSupport"
    requires_exit_engine = False

    def __init__(self, atr_mult: float = 0.0, confirm_days: int = 0) -> None:
        self.atr_mult = float(atr_mult)
        self.confirm_days = int(confirm_days)
        if self.atr_mult > 0:
            label = f"BreakoutSupport-{self.atr_mult:g}ATR"
        else:
            label = "BreakoutSupport"
        if self.confirm_days:
            label = f"{label}_c{self.confirm_days}"
        self.name = label
        self._inner = LevelExit(
            name=self.name,
            level_fn=lambda ctx: _breakout_support(ctx.hist),
            atr_mult=self.atr_mult,
            confirm_days=confirm_days,
            reason_tag="BREAKOUT_SUPPORT_BREAK",
        )

    def reset(self) -> None:
        self._inner.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        return self._inner.on_bar(ctx)


class StructureSupportATR:
    """Exit when Close < StructureSupport - atr_mult*ATR14 (Plan TrendBreak band)."""

    name = "StructureSupport-0.5ATR"
    requires_exit_engine = False

    def __init__(self, atr_mult: float = 0.5, swing_lookback: int = 10, confirm_days: int = 0) -> None:
        self.atr_mult = float(atr_mult)
        self.swing_lookback = int(swing_lookback)
        self.confirm_days = int(confirm_days)
        label = f"StructureSupport-{self.atr_mult:g}ATR"
        if self.confirm_days:
            label = f"{label}_c{self.confirm_days}"
        self.name = label
        lb = self.swing_lookback
        self._inner = LevelExit(
            name=self.name,
            level_fn=lambda ctx, _lb=lb: _structure_support(ctx.hist, swing_lookback=_lb),
            atr_mult=self.atr_mult,
            confirm_days=confirm_days,
            reason_tag="STRUCTURE_SUPPORT_ATR_BREAK",
        )

    def reset(self) -> None:
        self._inner.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        return self._inner.on_bar(ctx)


class FixedPct:
    """
    Fixed % band from entry: stop at -pct, take-profit at +pct.

    Plan §18 Fixed5/8/10/15% baselines.
    """

    name = "FixedPct"
    requires_exit_engine = False

    def __init__(
        self,
        pct: float = 0.08,
        *,
        take_profit: bool = True,
        stop: bool = True,
        confirm_days: int = 0,
    ) -> None:
        self.pct = float(pct)
        self.take_profit = bool(take_profit)
        self.stop = bool(stop)
        self.confirm_days = int(confirm_days)
        pct_label = f"{self.pct * 100:.0f}"
        label = f"Fixed{pct_label}%"
        if self.confirm_days:
            label = f"{label}_c{self.confirm_days}"
        self.name = label
        self._confirm = _ConfirmHelper(confirm_days)

    def reset(self) -> None:
        self._confirm.reset()

    def on_bar(self, ctx: BarContext) -> StrategySignal:
        entry = _safe_float(ctx.entry_price)
        close = _safe_float(ctx.close)
        if entry is None or entry <= 0 or close is None:
            return StrategySignal(action="HOLD", reason="entry_unavailable")
        ret = (close - entry) / entry
        stop_hit = self.stop and ret <= -self.pct
        tp_hit = self.take_profit and ret >= self.pct
        # TP is immediate (no confirm); stop uses confirm_days
        if tp_hit:
            return StrategySignal(
                action="EXIT",
                reason=f"tp_+{self.pct:g}",
                tags=["FIXED_TP"],
                meta={"ret": ret, "pct": self.pct},
            )
        if self._confirm.confirmed(ctx, stop_hit):
            return StrategySignal(
                action="EXIT",
                reason=f"stop_-{self.pct:g}",
                tags=["FIXED_STOP"],
                meta={"ret": ret, "pct": self.pct, "streak": self._confirm._streak},
            )
        return StrategySignal(action="HOLD", reason="inside_band", meta={"ret": ret})


# ---------------------------------------------------------------------------
# Grid builders / registry
# ---------------------------------------------------------------------------


def build_plan18_grid(confirm_days: int = 0) -> List[Any]:
    """Instantiate Plan §18 strategy set for one confirm_days value."""
    c = int(confirm_days)
    return [
        FixedPct(0.05, confirm_days=c),
        FixedPct(0.08, confirm_days=c),
        FixedPct(0.10, confirm_days=c),
        FixedPct(0.15, confirm_days=c),
        MA20(confirm_days=c),
        MA20_ATR(atr_mult=0.5, confirm_days=c),
        MA20_ATR(atr_mult=1.0, confirm_days=c),
        SwingLow(atr_mult=0.0, confirm_days=c),
        SwingLow(atr_mult=0.5, confirm_days=c),
        SwingLow(atr_mult=1.0, confirm_days=c),
        BreakoutSupportExit(atr_mult=0.0, confirm_days=c),
        BreakoutSupportExit(atr_mult=0.5, confirm_days=c),
        BreakoutSupportExit(atr_mult=1.0, confirm_days=c),
        StructureSupportATR(atr_mult=0.5, confirm_days=c),
        DynamicProtectionExit(confirm_days=c),
        DynamicProtectionMaxDD(confirm_days=c),
    ]


STRATEGY_REGISTRY: Dict[str, Any] = {
    "DynamicProtection": DynamicProtectionExit,
    "DynamicProtectionExit": DynamicProtectionExit,  # alias
    "DynamicProtection+MaxDrawdown": DynamicProtectionMaxDD,
    "DynamicProtectionMaxDD": DynamicProtectionMaxDD,  # alias
    "MA20": MA20,
    "MA20_0p5ATR": MA20_0p5ATR,
    "MA20-0.5ATR": MA20_0p5ATR,
    "MA20_1ATR": MA20_1ATR,
    "MA20-1ATR": MA20_1ATR,
    "FixedPct": FixedPct,
    "Fixed5%": lambda **kw: FixedPct(0.05, **kw),
    "Fixed8%": lambda **kw: FixedPct(0.08, **kw),
    "Fixed10%": lambda **kw: FixedPct(0.10, **kw),
    "Fixed15%": lambda **kw: FixedPct(0.15, **kw),
    "FixedPct(0.08)": lambda **kw: FixedPct(0.08, **kw),
    "SwingLow": SwingLow,
    "SwingLow-0.5ATR": lambda **kw: SwingLow(atr_mult=0.5, **kw),
    "SwingLow-1ATR": lambda **kw: SwingLow(atr_mult=1.0, **kw),
    "BreakoutSupport": BreakoutSupportExit,
    "BreakoutSupport-0.5ATR": lambda **kw: BreakoutSupportExit(atr_mult=0.5, **kw),
    "BreakoutSupport-1ATR": lambda **kw: BreakoutSupportExit(atr_mult=1.0, **kw),
    "StructureSupport-0.5ATR": StructureSupportATR,
}


def get_strategy(name: str, **kwargs: Any) -> Any:
    """Construct a strategy by registry name."""
    key = str(name).strip()
    if key not in STRATEGY_REGISTRY:
        raise KeyError(f"unknown strategy {key!r}; known={list(STRATEGY_REGISTRY)}")
    factory = STRATEGY_REGISTRY[key]
    if isinstance(factory, type):
        return factory(**kwargs)
    return factory(**kwargs)


__all__ = [
    "BarContext",
    "StrategySignal",
    "ExitStrategy",
    "DynamicProtectionExit",
    "DynamicProtectionMaxDD",
    "MA20",
    "MA20_0p5ATR",
    "MA20_1ATR",
    "MA20_ATR",
    "FixedPct",
    "SwingLow",
    "BreakoutSupportExit",
    "StructureSupportATR",
    "LevelExit",
    "build_plan18_grid",
    "STRATEGY_REGISTRY",
    "get_strategy",
    "_ma20",
    "_atr14",
    "_swing_low",
    "_breakout_support",
    "_structure_support",
]
