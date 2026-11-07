"""
Point-in-time annual financial store for Dividend V3.1.9 Phase3+.

Loads yjbb / lrb / xjll / zcfz annual rows (period endswith 1231) from
clean universe panels + 2000–2020 hist panels. Callers filter NOTICE_DATE <= as_of.
Does not invent missing fields.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from dividend_v319.config.dividendConfig import FIN_CLEAN

ANNUAL_SUFFIX = "1231"


def _z6(code) -> str:
    return str(code).strip().zfill(6)


def _read_annual(paths: List[Path], cols: List[str]) -> pd.DataFrame:
    frames = []
    for p in paths:
        if not p.exists():
            continue
        use = [c for c in cols if True]
        df = pd.read_parquet(p)
        keep = [c for c in use if c in df.columns]
        df = df[keep].copy()
        if "SECURITY_CODE" not in df.columns:
            continue
        df["SECURITY_CODE"] = df["SECURITY_CODE"].map(_z6)
        if "period" not in df.columns:
            continue
        df["period"] = df["period"].astype(str)
        df = df[df["period"].str.endswith(ANNUAL_SUFFIX)]
        if "NOTICE_DATE" in df.columns:
            df["NOTICE_DATE"] = pd.to_datetime(df["NOTICE_DATE"], errors="coerce")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # prefer later notice for same code+period (updated filings)
    out = out.sort_values(["SECURITY_CODE", "period", "NOTICE_DATE"])
    out = out.drop_duplicates(["SECURITY_CODE", "period"], keep="last")
    return out


@lru_cache(maxsize=1)
def load_yjbb_annual() -> pd.DataFrame:
    cols = [
        "SECURITY_CODE",
        "SECURITY_NAME_ABBR",
        "period",
        "NOTICE_DATE",
        "BASIC_EPS",
        "DEDUCT_BASIC_EPS",
        "PARENT_NETPROFIT",
        "TOTAL_OPERATE_INCOME",
        "MGJYXJJE",
        "BPS",
        "WEIGHTAVG_ROE",
        "ZXGXL",
    ]
    paths = [
        FIN_CLEAN / "yjbb_universe_panel.parquet",
        FIN_CLEAN / "yjbb_annual_hist_2000_2020_universe_panel.parquet",
    ]
    return _read_annual(paths, cols)


@lru_cache(maxsize=1)
def load_lrb_annual() -> pd.DataFrame:
    cols = [
        "SECURITY_CODE",
        "SECURITY_NAME_ABBR",
        "INDUSTRY_NAME",
        "period",
        "NOTICE_DATE",
        "PARENT_NETPROFIT",
        "DEDUCT_PARENT_NETPROFIT",
        "TOTAL_OPERATE_INCOME",
        "OPERATE_PROFIT",
        "TOTAL_PROFIT",
    ]
    paths = [
        FIN_CLEAN / "lrb_universe_panel.parquet",
        FIN_CLEAN / "lrb_annual_hist_2000_2020_universe_panel.parquet",
    ]
    return _read_annual(paths, cols)


@lru_cache(maxsize=1)
def load_xjll_annual() -> pd.DataFrame:
    cols = [
        "SECURITY_CODE",
        "period",
        "NOTICE_DATE",
        "NETCASH_OPERATE",
        "NETCASH_INVEST",
        "NETCASH_FINANCE",
        "CONSTRUCT_LONG_ASSET",
        "CCE_ADD",
    ]
    paths = [
        FIN_CLEAN / "xjll_universe_panel.parquet",
        FIN_CLEAN / "xjll_annual_hist_2000_2020_universe_panel.parquet",
    ]
    return _read_annual(paths, cols)


@lru_cache(maxsize=1)
def load_zcfz_annual() -> pd.DataFrame:
    cols = [
        "SECURITY_CODE",
        "period",
        "NOTICE_DATE",
        "TOTAL_ASSETS",
        "TOTAL_LIABILITIES",
        "TOTAL_EQUITY",
        "DEBT_ASSET_RATIO",
        "SHORT_LOAN",
        "BORROW_FUND",
    ]
    paths = [
        FIN_CLEAN / "zcfz_universe_panel.parquet",
        FIN_CLEAN / "zcfz_annual_hist_2000_2020_universe_panel.parquet",
    ]
    return _read_annual(paths, cols)


def pit_filter(df: pd.DataFrame, as_of: str) -> pd.DataFrame:
    if df.empty:
        return df
    t = pd.Timestamp(as_of).normalize()
    out = df[df["NOTICE_DATE"].notna() & (df["NOTICE_DATE"] <= t)].copy()
    return out


def annual_by_code(
    df: pd.DataFrame, code: str, as_of: str
) -> pd.DataFrame:
    code = _z6(code)
    sub = pit_filter(df[df["SECURITY_CODE"] == code], as_of)
    if sub.empty:
        return sub
    return sub.sort_values("period")


def last_complete_report_year(as_of: str) -> int:
    """Conservative: annual report for year Y usually disclosed in Y+1 → last complete = T.year-1."""
    return pd.Timestamp(as_of).year - 1
