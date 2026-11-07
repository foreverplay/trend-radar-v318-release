#!/usr/bin/env python3
"""Fetch unadjusted (bfq) daily bars for a Phase2 sample (>=30 names).

Uses akshare stock_zh_a_daily(..., adjust='').
Writes dividend_v319/data/bfq_daily/{code}.parquet and a manifest.
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import sys
_TB = Path(__file__).resolve().parents[2]
if str(_TB) not in sys.path:
    sys.path.insert(0, str(_TB))
from dividend_v319.config.dividendConfig import BFQ_DIR, DIV_ROOT

OUT = BFQ_DIR
CST = ZoneInfo("Asia/Shanghai")

# Sample list: banks / utilities / coal / highway / telecom + trap-watch candidates.
# Labels are for documentation only; trap status is NOT scored in Phase2.
SAMPLE = [
    # banks
    ("601398", "银行"),
    ("600036", "银行"),
    ("601288", "银行"),
    ("600000", "银行"),
    ("601166", "银行"),
    ("000001", "银行"),
    ("601988", "银行"),
    ("601328", "银行"),
    # coal
    ("601088", "煤炭"),
    ("601225", "煤炭"),
    ("601898", "煤炭"),
    ("000983", "煤炭"),
    ("601666", "煤炭"),
    ("600188", "煤炭"),
    # power / utility
    ("600900", "电力"),
    ("600886", "电力"),
    ("600025", "水电"),
    ("600236", "电力"),
    ("601158", "水务"),
    ("601139", "燃气"),
    # highway / rail
    ("600377", "高速"),
    ("600350", "高速"),
    ("000429", "高速"),
    ("601006", "铁路"),
    # telecom
    ("601728", "电信"),
    ("600050", "联通"),
    ("600941", "移动"),
    # trap-watch candidates (high-yield / cyclical; for later Risk/Quality — not labeled trap here)
    ("600546", "煤炭_watch"),
    ("600971", "煤炭_watch"),
    ("000537", "电力_watch"),
    ("600101", "电力_watch"),
    ("002601", "化工_watch"),
    ("000651", "家电_watch"),
]


def code_to_sina(code: str) -> str:
    c = code.zfill(6)
    if c.startswith(("6", "9")):
        return "sh" + c
    return "sz" + c


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
    return df.reset_index(drop=True)


def fetch_one(code: str) -> pd.DataFrame:
    import akshare as ak

    sym = code_to_sina(code)
    df = ak.stock_zh_a_daily(
        symbol=sym, start_date="20000101", end_date="20301231", adjust=""
    )
    if df is None or len(df) == 0:
        raise RuntimeError("empty")
    return normalize(df)


def main(sleep_s: float = 0.35, force: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(CST).isoformat(),
        "adjust": "",
        "source": "akshare.stock_zh_a_daily",
        "note": "bfq/unadjusted closes for TotalReturn split; DO NOT add cash on top of qfq/hfq",
        "ok": [],
        "fail": [],
        "bulk_plan": {
            "full_market_path_suggestion": str(DIV_ROOT / "data" / "bfq_daily"),
            "estimated_names": "~5000+",
            "method": "Reuse ashare-kline/scripts/download_all_daily.py pattern with adjust=''; sleep+retry; store under dividend_v319/data/bfq_daily or ashare-kline/raw/daily_bfq/",
            "priority": "Phase6 before full DividendBacktest; Phase2 only needs sample>=30",
        },
    }
    for code, tag in SAMPLE:
        path = OUT / f"{code}.parquet"
        if path.exists() and not force:
            n = len(pd.read_parquet(path, columns=["date"]))
            manifest["ok"].append({"code": code, "tag": tag, "rows": n, "skipped": True, "path": str(path)})
            print(f"skip {code}")
            continue
        try:
            time.sleep(sleep_s)
            df = fetch_one(code)
            df.to_parquet(path, index=False)
            manifest["ok"].append(
                {
                    "code": code,
                    "tag": tag,
                    "rows": int(len(df)),
                    "date_min": str(df["date"].min().date()),
                    "date_max": str(df["date"].max().date()),
                    "path": str(path),
                }
            )
            print(f"OK {code} n={len(df)}")
        except Exception as e:
            manifest["fail"].append(
                {"code": code, "tag": tag, "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-400:]}
            )
            print(f"FAIL {code}: {e}")

    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ok", len(manifest["ok"]), "fail", len(manifest["fail"]))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    main(sleep_s=args.sleep, force=args.force)
