"""
Trading costs + limit-up/down constraints (A-share approx).

Sibling pattern from ashare-tech-backtest:
  commission 万三 (both sides), stamp 千一 (sell only), slip 0.1%.

Limit bands (approx usable threshold, not exact exchange tick math):
  main 60xxxx/00xxxx → ±9.8%
  ChiNext 300 / STAR 688 → ±19.8%
  BJ 8/4 → ±29.8%

Skip/delay buy when limit-up; skip/delay sell when limit-down.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

# Align with /workspace/ashare-tech-backtest/config.py (do not import that package)
COMMISSION_RATE = 0.0003  # 万三 both sides
STAMP_TAX_RATE = 0.001  # 千一 sell only
SLIPPAGE = 0.001  # 0.1%

# Usable limit thresholds (slightly inside official ±10/20/30)
LIMIT_MAIN = 0.098
LIMIT_CHINEXT_STAR = 0.198
LIMIT_BJ = 0.298


def _norm_code(code: Optional[str]) -> str:
    if not code:
        return "000000"
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def board_limit_frac(code: Optional[str]) -> float:
    """Return approx one-side limit fraction for the board."""
    c = _norm_code(code)
    if c.startswith(("300", "301", "688")):
        return LIMIT_CHINEXT_STAR
    if c.startswith(("8", "4")) and not c.startswith(("60", "00", "30")):
        # BJ / older NEEQ-style codes 8xxxxx / 4xxxxx
        if c[0] in ("8", "4"):
            return LIMIT_BJ
    if c.startswith(("83", "87", "43", "92")):
        return LIMIT_BJ
    return LIMIT_MAIN


def _safe_pct(pct_chg: Any) -> Optional[float]:
    if pct_chg is None:
        return None
    try:
        v = float(pct_chg)
    except (TypeError, ValueError):
        return None
    # hist may store percent (9.8) or fraction (0.098)
    if abs(v) > 1.5:  # clearly percent points
        return v / 100.0
    return v


def is_limit_up(pct_chg: Any, code: Optional[str] = None, *, tol: float = 0.002) -> bool:
    """True when day's return ≈ board limit-up (cannot reasonably buy)."""
    p = _safe_pct(pct_chg)
    if p is None:
        return False
    lim = board_limit_frac(code)
    return p >= (lim - tol)


def is_limit_down(pct_chg: Any, code: Optional[str] = None, *, tol: float = 0.002) -> bool:
    """True when day's return ≈ board limit-down (cannot reasonably sell)."""
    p = _safe_pct(pct_chg)
    if p is None:
        return False
    lim = board_limit_frac(code)
    return p <= -(lim - tol)


def buy_fill_price(open_px: float, slippage: float = SLIPPAGE) -> float:
    """Aggressive buy: pay up through slip."""
    return float(open_px) * (1.0 + float(slippage))


def sell_fill_price(open_px: float, slippage: float = SLIPPAGE) -> float:
    """Aggressive sell: receive down through slip."""
    return float(open_px) * (1.0 - float(slippage))


def apply_buy_costs(
    open_px: float,
    *,
    shares: float = 1.0,
    commission: float = COMMISSION_RATE,
    slippage: float = SLIPPAGE,
) -> Tuple[float, float, float]:
    """
    Returns (fill_price, fee, total_cash_out).

    Slippage is in the fill price; fee = notional * commission.
    total_cash_out = fill_price * shares + fee.
    """
    px = buy_fill_price(open_px, slippage=slippage)
    notional = px * float(shares)
    fee = notional * float(commission)
    return px, fee, notional + fee


def apply_sell_costs(
    open_px: float,
    *,
    shares: float = 1.0,
    commission: float = COMMISSION_RATE,
    stamp: float = STAMP_TAX_RATE,
    slippage: float = SLIPPAGE,
) -> Tuple[float, float, float]:
    """
    Returns (fill_price, fee, total_cash_in).

    Slippage in fill price; fee = notional * (commission + stamp).
    total_cash_in = fill_price * shares - fee.
    """
    px = sell_fill_price(open_px, slippage=slippage)
    notional = px * float(shares)
    fee = notional * (float(commission) + float(stamp))
    return px, fee, notional - fee


def roundtrip_cost_drag(
    *,
    commission: float = COMMISSION_RATE,
    stamp: float = STAMP_TAX_RATE,
    slippage: float = SLIPPAGE,
) -> float:
    """Approx one-way×2 cost fraction vs mid (slip both + fee both + stamp sell)."""
    return 2.0 * float(slippage) + 2.0 * float(commission) + float(stamp)


__all__ = [
    "COMMISSION_RATE",
    "STAMP_TAX_RATE",
    "SLIPPAGE",
    "LIMIT_MAIN",
    "LIMIT_CHINEXT_STAR",
    "LIMIT_BJ",
    "board_limit_frac",
    "is_limit_up",
    "is_limit_down",
    "buy_fill_price",
    "sell_fill_price",
    "apply_buy_costs",
    "apply_sell_costs",
    "roundtrip_cost_drag",
]
