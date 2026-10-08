#!/usr/bin/env python3
"""Download daily qfq parquet via akshare into ASHARE_KLINE_ROOT/raw/daily.

Usage:
  export ASHARE_KLINE_ROOT=./data/sample/kline   # or full DB path
  python download_sample_or_universe.py --codes 600396,601579
  python download_sample_or_universe.py --universe-csv ../meta/universe_sample.csv
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd


def code_to_symbol(code: str) -> str:
    c = code.zfill(6)
    if c.startswith(("5", "6", "9")):
        return f"sh{c}"
    return f"sz{c}"


def fetch_one(code: str) -> pd.DataFrame:
    import akshare as ak

    symbol = code_to_symbol(code)
    # sina daily qfq
    df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="comma-separated 6-digit codes")
    ap.add_argument("--universe-csv", default="")
    ap.add_argument("--sleep", type=float, default=0.35)
    args = ap.parse_args()
    root = Path(os.environ.get("ASHARE_KLINE_ROOT", Path(__file__).resolve().parents[1]))
    out = root / "raw" / "daily"
    out.mkdir(parents=True, exist_ok=True)
    codes = []
    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    elif args.universe_csv:
        u = pd.read_csv(args.universe_csv)
        codes = [str(x).zfill(6) for x in u["code"].tolist()]
    else:
        raise SystemExit("Provide --codes or --universe-csv")
    for i, c in enumerate(codes, 1):
        dest = out / f"{c}.parquet"
        try:
            df = fetch_one(c)
            df.to_parquet(dest, index=False)
            print(f"[{i}/{len(codes)}] OK {c} -> {dest} rows={len(df)}")
        except Exception as e:
            print(f"[{i}/{len(codes)}] FAIL {c}: {e}")
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
