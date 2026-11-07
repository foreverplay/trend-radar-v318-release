"""
dividendReturnEngine — Phase2 minimal TotalReturn calculator.

口径（禁止在 qfq/hfq 上再加现金）:
- PriceReturn: 未复权收盘价 P_end / P_start - 1
- DividendCashReturn: Σ cash_per_share / P_start  （持有期初买入 1 股、分红现金不复投）
- TotalReturn (reinvest=False): (P_end + Σ cash_per_share) / P_start - 1
- TotalReturn (reinvest=True): 除息日用未复权收盘价把现金买回股份，期末 shares*P_end / P_start - 1

事件入账日: ex_dividend_date（须落在 (start, end]）
PIT: 可选 require_disclosed_by=end，仅计入 disclose_date <= end 的事件
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from dividend_v319.config.dividendConfig import BFQ_DIR, DIV_ROOT, EVENTS_PATH

DEFAULT_EVENTS = EVENTS_PATH
DEFAULT_BFQ = BFQ_DIR


@dataclass
class ReturnBreakdown:
    code: str
    start: str
    end: str
    price_start: Optional[float]
    price_end: Optional[float]
    price_return: Optional[float]
    dividend_cash_return: Optional[float]
    total_return_no_reinvest: Optional[float]
    total_return_reinvest: Optional[float]
    cash_sum: Optional[float]
    n_dividends: int
    dividends: List[Dict[str, Any]]
    price_source: str
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def _load_bfq(code: str, bfq_dir: Path) -> pd.DataFrame:
    path = bfq_dir / f"{_z6(code)}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"bfq missing: {path}")
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.dropna(subset=["close"]).sort_values("date")
    return df


def _load_events(events_path: Path) -> pd.DataFrame:
    df = pd.read_parquet(events_path)
    df["code"] = df["code"].map(_z6)
    for c in ("ex_dividend_date", "disclose_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.normalize()
    return df


def _price_on_or_before(df: pd.DataFrame, dt: pd.Timestamp) -> Optional[pd.Series]:
    sub = df[df["date"] <= dt]
    if sub.empty:
        return None
    return sub.iloc[-1]


def _price_on_or_after(df: pd.DataFrame, dt: pd.Timestamp) -> Optional[pd.Series]:
    sub = df[df["date"] >= dt]
    if sub.empty:
        return None
    return sub.iloc[0]


def compute_returns(
    code: str,
    start: Union[str, pd.Timestamp],
    end: Union[str, pd.Timestamp],
    *,
    events_path: Path = DEFAULT_EVENTS,
    bfq_dir: Path = DEFAULT_BFQ,
    assign_progress_ok: tuple = ("实施分配",),
    require_cash: bool = True,
    require_disclosed_by_end: bool = True,
) -> ReturnBreakdown:
    code = _z6(code)
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    notes: List[str] = []

    px = _load_bfq(code, bfq_dir)
    row_s = _price_on_or_after(px, start_ts)
    row_e = _price_on_or_before(px, end_ts)
    if row_s is None or row_e is None:
        return ReturnBreakdown(
            code=code,
            start=str(start_ts.date()),
            end=str(end_ts.date()),
            price_start=None,
            price_end=None,
            price_return=None,
            dividend_cash_return=None,
            total_return_no_reinvest=None,
            total_return_reinvest=None,
            cash_sum=None,
            n_dividends=0,
            dividends=[],
            price_source=str(bfq_dir / f"{code}.parquet"),
            notes=["no_bfq_price_in_window"],
        )

    # Align to actual trade dates used
    start_used = pd.Timestamp(row_s["date"]).normalize()
    end_used = pd.Timestamp(row_e["date"]).normalize()
    p0 = float(row_s["close"])
    p1 = float(row_e["close"])
    if start_used != start_ts:
        notes.append(f"start_aligned_to_trade={start_used.date()}")
    if end_used != end_ts:
        notes.append(f"end_aligned_to_trade={end_used.date()}")

    ev = _load_events(events_path)
    ev = ev[ev["code"] == code].copy()
    if assign_progress_ok:
        ev = ev[ev["assign_progress"].isin(assign_progress_ok)]
    if require_cash:
        ev = ev[ev["cash_per_share"].fillna(0) > 0]
    # Window: ex-date in (start_used, end_used] — buy at start close, get div if ex after buy
    ev = ev[ev["ex_dividend_date"].notna()]
    ev = ev[(ev["ex_dividend_date"] > start_used) & (ev["ex_dividend_date"] <= end_used)]
    if require_disclosed_by_end:
        before = len(ev)
        ev = ev[ev["disclose_date"].isna() | (ev["disclose_date"] <= end_used)]
        dropped = before - len(ev)
        if dropped:
            notes.append(f"dropped_undisclosed_by_end={dropped}")

    div_rows: List[Dict[str, Any]] = []
    cash_sum = 0.0
    for _, r in ev.sort_values("ex_dividend_date").iterrows():
        cps = float(r["cash_per_share"])
        cash_sum += cps
        # price on ex-date for reinvest (bfq close on/after ex)
        px_ex = _price_on_or_after(px, pd.Timestamp(r["ex_dividend_date"]))
        div_rows.append(
            {
                "ex_dividend_date": str(pd.Timestamp(r["ex_dividend_date"]).date()),
                "disclose_date": None
                if pd.isna(r.get("disclose_date"))
                else str(pd.Timestamp(r["disclose_date"]).date()),
                "cash_per_share": cps,
                "ex_close": None if px_ex is None else float(px_ex["close"]),
                "report_period": str(r.get("report_period", "")),
                "assign_progress": str(r.get("assign_progress", "")),
            }
        )

    price_return = p1 / p0 - 1.0
    div_cash_return = cash_sum / p0
    tr_no = (p1 + cash_sum) / p0 - 1.0

    # reinvest path
    shares = 1.0
    cash_pocket = 0.0  # unused when always reinvest same day
    for d in div_rows:
        ex_c = d["ex_close"]
        if ex_c is None or ex_c <= 0:
            notes.append(f"reinvest_skip_no_ex_price:{d['ex_dividend_date']}")
            cash_pocket += d["cash_per_share"] * shares
            continue
        cash = d["cash_per_share"] * shares
        shares += cash / ex_c
    tr_re = (shares * p1 + cash_pocket) / p0 - 1.0

    notes.append("prices=bfq_unadjusted; never_add_cash_on_qfq_hfq")
    return ReturnBreakdown(
        code=code,
        start=str(start_used.date()),
        end=str(end_used.date()),
        price_start=p0,
        price_end=p1,
        price_return=price_return,
        dividend_cash_return=div_cash_return,
        total_return_no_reinvest=tr_no,
        total_return_reinvest=tr_re,
        cash_sum=cash_sum,
        n_dividends=len(div_rows),
        dividends=div_rows,
        price_source=str(bfq_dir / f"{code}.parquet"),
        notes=notes,
    )


class DividendReturnEngine:
    def __init__(
        self,
        events_path: Path = DEFAULT_EVENTS,
        bfq_dir: Path = DEFAULT_BFQ,
    ):
        self.events_path = events_path
        self.bfq_dir = bfq_dir

    def compute(self, code: str, start: str, end: str, **kwargs) -> ReturnBreakdown:
        return compute_returns(
            code,
            start,
            end,
            events_path=self.events_path,
            bfq_dir=self.bfq_dir,
            **kwargs,
        )


def run(*args, **kwargs):
    """CLI-friendly: compute_returns passthrough."""
    return compute_returns(*args, **kwargs)


def compute_multi_horizon(
    code: str,
    as_of: Union[str, pd.Timestamp],
    *,
    horizons_years: tuple = (1, 3, 5),
    events_path: Path = DEFAULT_EVENTS,
    bfq_dir: Path = DEFAULT_BFQ,
) -> Dict[str, Any]:
    """
    Batch helper for Phase5: 1Y/3Y/5Y PriceReturn / DividendCashReturn / TotalReturn.
    Primary: no-reinvest. Reinvest stored as appendix fields.
    Missing bfq → DATA_INSUFFICIENT (never qfq+cash).
    """
    code = _z6(code)
    end_ts = pd.Timestamp(as_of).normalize()
    out: Dict[str, Any] = {
        "code": code,
        "as_of": str(end_ts.date()),
        "data_status": "VALID",
        "horizons": {},
        "notes": [],
    }
    bfq_path = bfq_dir / f"{code}.parquet"
    if not bfq_path.exists():
        out["data_status"] = "DATA_INSUFFICIENT"
        out["notes"].append("bfq_missing_skip_pr_tr_never_use_qfq_plus_cash")
        return out

    for n in horizons_years:
        start_ts = end_ts - pd.DateOffset(years=int(n))
        try:
            br = compute_returns(
                code,
                start_ts,
                end_ts,
                events_path=events_path,
                bfq_dir=bfq_dir,
            )
        except FileNotFoundError:
            out["horizons"][f"{n}y"] = None
            out["data_status"] = "DATA_INSUFFICIENT"
            out["notes"].append(f"{n}y_bfq_missing")
            continue
        out["horizons"][f"{n}y"] = {
            "start": br.start,
            "end": br.end,
            "price_return": br.price_return,
            "dividend_cash_return": br.dividend_cash_return,
            "total_return": br.total_return_no_reinvest,  # primary
            "total_return_reinvest_appendix": br.total_return_reinvest,
            "cash_sum": br.cash_sum,
            "n_dividends": br.n_dividends,
            "notes": br.notes,
        }
        if br.price_return is None:
            out["data_status"] = "DATA_INSUFFICIENT"
    return out


def batch_multi_horizon(
    codes: List[str],
    as_of: str,
    **kwargs,
) -> List[Dict[str, Any]]:
    return [compute_multi_horizon(c, as_of, **kwargs) for c in codes]
