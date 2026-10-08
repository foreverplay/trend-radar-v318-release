"""
ExitEngine — Phase 8 dynamic Warning / Protection / BreakTrend prices + ExitSignal.

Plan §§13–16 (observation language only — SELL = structure exit condition, not predict drop).

Rules
-----
- StructureSupport = max(RecentSwingLow, BreakoutSupport, MA20) when available
- TrendBreakPrice  = StructureSupport - ATR14 * atrMultiplier  (default 0.5)
- WarningPrice     = StructureSupport  (documented choice: alert at structural support;
                     typically sits between Close and BreakTrend when trend intact;
                     equivalently "slightly above BreakTrend" by atrMultiplier*ATR)
- ProtectionPrice  = ratchet UP only:
                     structureProtection = StructureSupport
                     PeakProtection      = PeakClose * (1 - maxDrawdown)
                     FinalProtection     = max(priorProtection, structureProtection, PeakProtection)
- TREND_BROKEN / SELL only when ALL of:
    Close < TrendBreakPrice  AND  Close < MA20  AND  MA20Slope < 0
  False break (intraday low < Break but Close >= Break) → PROTECT/WATCH, never SELL
- ExitSignal: HOLD | WATCH | PROTECT | REDUCE_RISK | SELL
  - WATCH: continuity drop / consumption rise
  - PROTECT: near protection / false break / close approaching StructureSupport
  - REDUCE_RISK: Close < MA20 with weakening slope / risk up / decay
  - SELL: confirmed structure break (above)
- confirm_days: config 0/1/2; Phase 8 implements 0-day close confirm; multi-day hooked via
  prior_state["broken_streak"] when confirm_days > 0

No fixed -5%/-8% as sole sell rule.
禁止荐买 / 预测必涨.
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
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import TREND_BOT_CONFIG, get_config  # noqa: E402
from v3.types import DataStatus, ExitSignal, RiskLevel  # noqa: E402

LOCAL_DAILY = _PKG_LOCAL_DAILY
# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class ExitEngineResult:
    """
    Phase 8 exit evaluation as-of T.

    ``break_trend_price`` is the structural break level (TrendBreakPrice).
    ``broken_confirmed`` is True only when SELL / TREND_BROKEN conditions hold
    after confirm_days logic.
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    code: Optional[str] = None
    t_date: Optional[str] = None
    # prices
    warning_price: Optional[float] = None
    protection_price: Optional[float] = None
    break_trend_price: Optional[float] = None
    structure_support: Optional[float] = None
    peak_close: Optional[float] = None
    # signal
    exit_signal: ExitSignal = ExitSignal.HOLD
    broken_confirmed: bool = False
    # components (debug / ratchet)
    recent_swing_low: Optional[float] = None
    breakout_support: Optional[float] = None
    ma20: Optional[float] = None
    atr14: Optional[float] = None
    ma20_slope: Optional[float] = None
    close: Optional[float] = None
    low: Optional[float] = None
    structure_protection: Optional[float] = None
    peak_protection: Optional[float] = None
    prior_protection: Optional[float] = None
    false_break: bool = False
    broken_streak: int = 0
    confirm_days: int = 0
    notes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        d["exit_signal"] = (
            self.exit_signal.value
            if isinstance(self.exit_signal, ExitSignal)
            else self.exit_signal
        )
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


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


def _truncate_hist(hist: Optional[pd.DataFrame], t_date: Optional[str]) -> Optional[pd.DataFrame]:
    if hist is None or not isinstance(hist, pd.DataFrame) or len(hist) == 0:
        return hist
    h = hist.copy()
    if "date" in h.columns:
        h["date"] = pd.to_datetime(h["date"])
        h = h.sort_values("date")
        if t_date:
            h = h[h["date"] <= pd.Timestamp(t_date)]
    return h.reset_index(drop=True)


def _atr14(hist: pd.DataFrame) -> Optional[float]:
    """ATR14 from columns if present, else Wilder-style TR mean of last 14."""
    if hist is None or len(hist) == 0:
        return None
    for col in ("ATR14", "atr14", "atr"):
        if col in hist.columns:
            v = _safe_float(hist[col].iloc[-1])
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


def _ma20_from_hist(hist: pd.DataFrame) -> Optional[float]:
    if hist is None or "close" not in hist.columns or len(hist) < 20:
        return None
    return _safe_float(hist["close"].astype(float).iloc[-20:].mean())


def _ma20_slope(hist: pd.DataFrame, ma20: Optional[float] = None) -> Optional[float]:
    """
    MA20 slope ≈ MA20(T) - MA20(T-5) (points), matching v2_structure spirit.
    Positive = rising MA20.
    """
    if hist is None or "close" not in hist.columns or len(hist) < 25:
        return None
    closes = hist["close"].astype(float)
    ma = closes.rolling(20).mean()
    if len(ma) < 6 or not np.isfinite(ma.iloc[-1]):
        return None
    cur = float(ma.iloc[-1])
    prev = float(ma.iloc[-6]) if np.isfinite(ma.iloc[-6]) else None
    if prev is None:
        return None
    return cur - prev


def _recent_swing_low(hist: pd.DataFrame, lookback: int = 10) -> Optional[float]:
    """RecentSwingLow = min(low) over last ``lookback`` bars (incl. T)."""
    if hist is None or "low" not in hist.columns or len(hist) == 0:
        return None
    n = max(1, int(lookback))
    window = hist["low"].astype(float).iloc[-n:]
    return _safe_float(window.min())


def _exit_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return dict((cfg or {}).get("exit") or {})


def _max_drawdown_frac(ex: Dict[str, Any]) -> float:
    """Prefer exit.maxDrawdown (0–1); else max_dd_pct / 100."""
    if ex.get("maxDrawdown") is not None:
        v = float(ex["maxDrawdown"])
        if v > 1.0:  # allow 15 meaning 15%
            return v / 100.0
        return max(0.0, min(v, 0.95))
    pct = ex.get("max_dd_pct", 15.0)
    try:
        return max(0.0, min(float(pct) / 100.0, 0.95))
    except (TypeError, ValueError):
        return 0.15


def _atr_multiplier(ex: Dict[str, Any]) -> float:
    if ex.get("atrMultiplier") is not None:
        return float(ex["atrMultiplier"])
    if ex.get("atr_multiplier") is not None:
        return float(ex["atr_multiplier"])
    # fall back: do NOT use atr_mult_stop (that's entry stop distance)
    return 0.5


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ExitEngine:
    """
    Dynamic exit levels + ExitSignal (Plan §§13–16).

    ``evaluate(hist, ..., prior_state=..., technical=..., continuity_hints=..., risk_hints=...)``
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config if config is not None else get_config()

    def evaluate(
        self,
        hist: Optional[pd.DataFrame] = None,
        *,
        t_date: Optional[str] = None,
        code: Optional[str] = None,
        prior_state: Optional[Dict[str, Any]] = None,
        # technical metrics (prefer explicit; else derived from hist / technical_result)
        ma20: Optional[float] = None,
        atr: Optional[float] = None,
        atr14: Optional[float] = None,
        platform_low: Optional[float] = None,
        breakout_support: Optional[float] = None,
        ma20_slope: Optional[float] = None,
        close: Optional[float] = None,
        low: Optional[float] = None,
        technical_result: Optional[Any] = None,
        # continuity / consumption / risk hints (Plan §16 mapping)
        continuity_score: Optional[float] = None,
        continuity_delta1: Optional[float] = None,
        continuity_delta_label: Optional[str] = None,
        consumption_score: Optional[float] = None,
        risk_level: Optional[Union[RiskLevel, str]] = None,
        risk_broken: bool = False,
        continuity_hints: Optional[Dict[str, Any]] = None,
        risk_hints: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> ExitEngineResult:
        cfg = config if config is not None else self.config
        if cfg is None:
            cfg = TREND_BOT_CONFIG
        ex = _exit_cfg(cfg)
        code6 = _norm_code(code)
        notes: List[str] = []
        tags: List[str] = []
        prior = dict(prior_state or {})

        # merge hint dicts
        ch = dict(continuity_hints or {})
        rh = dict(risk_hints or {})
        if continuity_score is None:
            continuity_score = _safe_float(ch.get("continuity_score"))
        if continuity_delta1 is None:
            continuity_delta1 = _safe_float(ch.get("delta1") or ch.get("continuity_delta1"))
        if continuity_delta_label is None:
            continuity_delta_label = ch.get("delta_label") or ch.get("continuity_delta_label")
        if consumption_score is None:
            consumption_score = _safe_float(ch.get("consumption_score"))
        if risk_level is None and rh.get("risk_level") is not None:
            risk_level = rh.get("risk_level")
        if not risk_broken:
            risk_broken = bool(rh.get("broken") or False)

        try:
            truncated = _truncate_hist(hist, t_date)
            if truncated is None or not isinstance(truncated, pd.DataFrame) or len(truncated) == 0:
                return ExitEngineResult(
                    ok=False,
                    data_status=DataStatus.DATA_INSUFFICIENT,
                    code=code6,
                    t_date=t_date,
                    exit_signal=ExitSignal.HOLD,
                    notes=["hist_empty"],
                    tags=["hist_empty"],
                    confirm_days=int(ex.get("confirm_days", 0) or 0),
                )

            asof = t_date
            if not asof and "date" in truncated.columns:
                asof = pd.Timestamp(truncated["date"].iloc[-1]).strftime("%Y-%m-%d")

            # --- pull from technical_result if provided ---
            tech = technical_result
            if tech is not None:
                if close is None:
                    close = _safe_float(getattr(tech, "close", None))
                if ma20 is None:
                    ma20 = _safe_float(getattr(tech, "ma20", None))
                if atr is None and atr14 is None:
                    atr = _safe_float(getattr(tech, "atr", None))
                if platform_low is None:
                    platform_low = _safe_float(getattr(tech, "platform_low", None))
                metrics = getattr(tech, "metrics", None) or {}
                if isinstance(metrics, dict):
                    if ma20_slope is None:
                        ma20_slope = _safe_float(metrics.get("ma20_slope"))
                    if breakout_support is None:
                        # platform_low is the breakout/platform support reference
                        breakout_support = _safe_float(
                            metrics.get("breakout_support")
                            or metrics.get("platform_low")
                            or platform_low
                        )

            # --- bar OHLC ---
            row = truncated.iloc[-1]
            if close is None:
                close = _safe_float(row.get("close") if hasattr(row, "get") else row["close"])
            if low is None and "low" in truncated.columns:
                low = _safe_float(row["low"])

            # --- ATR / MA20 / slope ---
            atr_v = _safe_float(atr14 if atr14 is not None else atr)
            if atr_v is None:
                atr_v = _atr14(truncated)
            if ma20 is None:
                ma20 = _ma20_from_hist(truncated)
            if ma20_slope is None:
                ma20_slope = _ma20_slope(truncated, ma20)

            lookback = int(ex.get("swing_lookback", 10) or 10)
            swing = _recent_swing_low(truncated, lookback=lookback)

            # BreakoutSupport: explicit > platform_low > metrics
            bsup = _safe_float(breakout_support)
            if bsup is None:
                bsup = _safe_float(platform_low)

            # StructureSupport = max(available among swing, breakout_support, MA20)
            candidates: List[float] = []
            if swing is not None:
                candidates.append(swing)
            if bsup is not None:
                candidates.append(bsup)
            if ma20 is not None:
                candidates.append(ma20)
            if not candidates:
                return ExitEngineResult(
                    ok=False,
                    data_status=DataStatus.DATA_INSUFFICIENT,
                    code=code6,
                    t_date=asof,
                    close=close,
                    low=low,
                    atr14=atr_v,
                    ma20=ma20,
                    ma20_slope=ma20_slope,
                    recent_swing_low=swing,
                    breakout_support=bsup,
                    exit_signal=ExitSignal.HOLD,
                    notes=["no_structure_support_inputs"],
                    tags=["no_structure_support"],
                    confirm_days=int(ex.get("confirm_days", 0) or 0),
                )
            structure_support = max(candidates)
            notes.append(
                "StructureSupport=max("
                f"swing={swing}, breakout={bsup}, ma20={ma20})→{round(structure_support, 4)}"
            )
            notes.append("WarningPrice=StructureSupport (alert at structural support)")

            atr_mult = _atr_multiplier(ex)
            if atr_v is None:
                # without ATR, BreakTrend degenerates to StructureSupport (conservative)
                break_trend = structure_support
                notes.append("atr_missing→BreakTrend=StructureSupport")
                tags.append("atr_missing")
            else:
                break_trend = structure_support - atr_v * atr_mult
                notes.append(
                    f"TrendBreakPrice=StructureSupport-ATR14*{atr_mult}="
                    f"{round(structure_support, 4)}-{round(atr_v, 4)}*{atr_mult}"
                )

            warning_price = structure_support  # documented choice

            # --- Peak / Protection ratchet ---
            prior_peak = _safe_float(prior.get("peak_close"))
            peak_close = close
            if prior_peak is not None:
                peak_close = max(prior_peak, close) if close is not None else prior_peak
            elif close is not None:
                peak_close = close

            dd = _max_drawdown_frac(ex)
            structure_protection = structure_support
            peak_protection = None
            if peak_close is not None:
                peak_protection = peak_close * (1.0 - dd)

            prior_prot = _safe_float(
                prior.get("protection_price") or prior.get("protection")
            )
            prot_parts: List[float] = [structure_protection]
            if peak_protection is not None:
                prot_parts.append(peak_protection)
            if prior_prot is not None:
                prot_parts.append(prior_prot)
            protection_price = max(prot_parts)
            notes.append(
                "Protection=max(prior, structureSupport, PeakClose*(1-maxDD)); ratchet UP only"
            )

            # --- confirm_days hook (Phase 8: default 0-day close confirm) ---
            confirm_days = int(ex.get("confirm_days", 0) or 0)
            # allow 0/1/2; Phase 8 logic works for all via streak
            prior_streak = int(prior.get("broken_streak") or 0)

            require_slope_neg = bool(ex.get("require_ma20_slope_neg", True))
            close_below_break = (
                close is not None and break_trend is not None and close < break_trend
            )
            close_below_ma20 = close is not None and ma20 is not None and close < ma20
            slope_neg = True
            if require_slope_neg:
                slope_neg = ma20_slope is not None and ma20_slope < 0

            raw_broken = bool(close_below_break and close_below_ma20 and slope_neg)

            # false break: intraday low pierced break but close held above
            false_break = False
            if (
                low is not None
                and break_trend is not None
                and low < break_trend
                and close is not None
                and close >= break_trend
            ):
                false_break = True
                tags.append("FALSE_BREAK")
                notes.append("false_break: low<BreakTrend but close>=BreakTrend → not SELL")

            if raw_broken:
                broken_streak = prior_streak + 1
            else:
                broken_streak = 0

            # confirm_days==0 → same-day close confirm; else need streak >= confirm_days
            if confirm_days <= 0:
                broken_confirmed = raw_broken
            else:
                broken_confirmed = broken_streak >= confirm_days

            if broken_confirmed:
                tags.append("TREND_BROKEN")
                notes.append(
                    "SELL: Close<TrendBreakPrice AND Close<MA20 AND MA20Slope<0 "
                    f"(confirm_days={confirm_days}, streak={broken_streak})"
                )

            # --- ExitSignal priority: SELL > REDUCE_RISK > PROTECT > WATCH > HOLD ---
            signal = ExitSignal.HOLD

            # WATCH hints
            watch_cont_drop = float(ex.get("watch_cont_drop", 3.0) or 3.0)
            watch_cons_rise = float(ex.get("watch_consumption_rise", 5.0) or 5.0)
            cont_drop = False
            if continuity_delta_label and str(continuity_delta_label).upper() == "DETERIORATING":
                cont_drop = True
            if continuity_delta1 is not None and continuity_delta1 <= -abs(watch_cont_drop):
                cont_drop = True
            cons_rise = False
            # consumption rising: use absolute high or hint
            cons_warn = float(ex.get("watch_consumption_level", 70.0) or 70.0)
            if consumption_score is not None and consumption_score >= cons_warn:
                cons_rise = True
            prior_cons = _safe_float(prior.get("consumption_score"))
            if (
                consumption_score is not None
                and prior_cons is not None
                and (consumption_score - prior_cons) >= watch_cons_rise
            ):
                cons_rise = True

            # near protection
            near_pct = float(ex.get("near_protection_pct", 0.02) or 0.02)
            near_protection = False
            if close is not None and protection_price is not None and protection_price > 0:
                if close <= protection_price * (1.0 + near_pct):
                    near_protection = True
            near_support = False
            if close is not None and structure_support is not None and structure_support > 0:
                if close <= structure_support * (1.0 + near_pct):
                    near_support = True

            # risk up / MA break for REDUCE_RISK
            risk_up = False
            rl = risk_level
            if isinstance(rl, RiskLevel):
                rl_s = rl.value
            else:
                rl_s = str(rl or "").upper()
            if rl_s in ("HIGH", "WARNING", "BREAK_RISK", "BROKEN"):
                risk_up = True
            if risk_broken:
                risk_up = True

            reduce_ma = bool(ex.get("reduce_risk_ma_break", True))
            ma_break_weaken = False
            if reduce_ma and close_below_ma20:
                # Close < MA20 and slope not firmly up
                if ma20_slope is None or ma20_slope <= 0:
                    ma_break_weaken = True

            if broken_confirmed:
                signal = ExitSignal.SELL
            elif ma_break_weaken or (risk_up and close_below_ma20):
                signal = ExitSignal.REDUCE_RISK
                tags.append("REDUCE_RISK")
                notes.append("REDUCE_RISK: Close<MA20 decay/risk-up (not yet structure SELL)")
            elif false_break or near_protection or near_support:
                signal = ExitSignal.PROTECT
                tags.append("PROTECT")
                if false_break:
                    notes.append("PROTECT: false break — observe, not SELL")
                elif near_protection:
                    notes.append("PROTECT: close near protection price")
                else:
                    notes.append("PROTECT: close near StructureSupport / WarningPrice")
            elif cont_drop or cons_rise:
                signal = ExitSignal.WATCH
                tags.append("WATCH")
                notes.append("WATCH: continuity drop and/or consumption rise")
            else:
                signal = ExitSignal.HOLD

            # Observation language note
            if signal == ExitSignal.SELL:
                notes.append("observation: SELL = trend structure exit condition, not predict drop")

            metrics = {
                "atr_multiplier": atr_mult,
                "max_drawdown": dd,
                "confirm_days": confirm_days,
                "broken_streak": broken_streak,
                "raw_broken": raw_broken,
                "close_below_break": close_below_break,
                "close_below_ma20": close_below_ma20,
                "slope_neg": slope_neg,
                "near_protection": near_protection,
                "cont_drop": cont_drop,
                "cons_rise": cons_rise,
                "warning_mode": "structure_support",
            }

            return ExitEngineResult(
                ok=True,
                data_status=DataStatus.VALID,
                code=code6,
                t_date=asof,
                warning_price=_round_px(warning_price),
                protection_price=_round_px(protection_price),
                break_trend_price=_round_px(break_trend),
                structure_support=_round_px(structure_support),
                peak_close=_round_px(peak_close),
                exit_signal=signal,
                broken_confirmed=bool(broken_confirmed),
                recent_swing_low=_round_px(swing),
                breakout_support=_round_px(bsup),
                ma20=_round_px(ma20),
                atr14=_round_px(atr_v),
                ma20_slope=_safe_float(ma20_slope),
                close=_round_px(close),
                low=_round_px(low),
                structure_protection=_round_px(structure_protection),
                peak_protection=_round_px(peak_protection),
                prior_protection=_round_px(prior_prot),
                false_break=bool(false_break),
                broken_streak=int(broken_streak),
                confirm_days=confirm_days,
                notes=notes,
                tags=tags,
                metrics=metrics,
            )

        except Exception as exc:  # noqa: BLE001
            return ExitEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code6,
                t_date=t_date,
                exit_signal=ExitSignal.HOLD,
                notes=[f"error:{type(exc).__name__}"],
                tags=["ERROR"],
                error=f"{type(exc).__name__}: {exc}",
                confirm_days=int(_exit_cfg(cfg).get("confirm_days", 0) or 0),
            )


def _round_px(x: Optional[float], nd: int = 4) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, nd)


# ---------------------------------------------------------------------------
# Smoke / CLI
# ---------------------------------------------------------------------------


def _smoke_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ExitEngine Phase 8 smoke")
    parser.add_argument("--code", default="002768")
    parser.add_argument("--t-date", default="2026-09-21")
    parser.add_argument(
        "--prior-protection",
        type=float,
        default=None,
        help="Inject prior protection to assert ratchet UP",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    from v3.engines.technical_engine import TechnicalEngine  # local import

    code = _norm_code(args.code) or args.code
    path = LOCAL_DAILY / f"{code}.parquet"
    hist = pd.read_parquet(path)
    tech = TechnicalEngine().evaluate(hist, t_date=args.t_date)
    prior = None
    if args.prior_protection is not None:
        prior = {"protection_price": float(args.prior_protection), "peak_close": tech.close}

    eng = ExitEngine()
    r = eng.evaluate(
        hist,
        t_date=args.t_date,
        code=code,
        prior_state=prior,
        technical_result=tech,
    )
    print("=== ExitEngine smoke ===")
    print(f"code={r.code} t_date={r.t_date} ok={r.ok} status={r.data_status}")
    print(f"close={r.close} ma20={r.ma20} atr14={r.atr14} slope={r.ma20_slope}")
    print(f"swing={r.recent_swing_low} breakout_sup={r.breakout_support}")
    print(f"StructureSupport={r.structure_support}")
    print(f"Warning={r.warning_price} Protection={r.protection_price} Break={r.break_trend_price}")
    print(f"peak_close={r.peak_close} peak_prot={r.peak_protection} prior_prot={r.prior_protection}")
    print(f"ExitSignal={r.exit_signal} broken_confirmed={r.broken_confirmed} false_break={r.false_break}")
    print(f"notes={r.notes}")
    assert r.ok and r.protection_price is not None and r.break_trend_price is not None
    if args.prior_protection is not None:
        assert r.protection_price >= float(args.prior_protection) - 1e-9, "ratchet must not decrease"
        print("PASS: ratchet holds (protection >= prior)")
    print("PASS: ExitEngine smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_cli())


__all__ = ["ExitEngine", "ExitEngineResult"]
