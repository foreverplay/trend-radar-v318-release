#!/usr/bin/env python3
"""Backfill Eastmoney fhps via akshare stock_fhps_em for report dates 2015-2020.

Writes:
  cache/fhps_em_{YYYYMMDD}.parquet
  cache/fhps_em_2015_2020_all.parquet
  cache/fhps_backfill_log.json
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
from dividend_v319.config.dividendConfig import CACHE_DIR

CACHE = CACHE_DIR
CST = ZoneInfo("Asia/Shanghai")

# Match local raw/fhps cadence: quarterly report dates
YEARS = range(2015, 2021)
QUARTERS = ("0331", "0630", "0930", "1231")


def report_dates() -> list[str]:
    return [f"{y}{q}" for y in YEARS for q in QUARTERS]


def fetch_one(date: str) -> pd.DataFrame:
    import akshare as ak

    return ak.stock_fhps_em(date=date)


def main(sleep_s: float = 0.45, force: bool = False) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    log = {
        "started_at": datetime.now(CST).isoformat(),
        "source": "akshare.stock_fhps_em",
        "ok": [],
        "fail": [],
        "skipped_existing": [],
    }
    frames: list[pd.DataFrame] = []
    for d in report_dates():
        out = CACHE / f"fhps_em_{d}.parquet"
        if out.exists() and not force:
            df = pd.read_parquet(out)
            frames.append(df.assign(_report_period=d, _source="ak.stock_fhps_em"))
            log["skipped_existing"].append({"date": d, "rows": len(df)})
            print(f"skip existing {d} n={len(df)}")
            continue
        try:
            time.sleep(sleep_s)
            df = fetch_one(d)
            if df is None or len(df) == 0:
                log["fail"].append({"date": d, "error": "empty"})
                print(f"FAIL empty {d}")
                continue
            df = df.copy()
            df.to_parquet(out, index=False)
            frames.append(df.assign(_report_period=d, _source="ak.stock_fhps_em"))
            log["ok"].append({"date": d, "rows": int(len(df)), "path": str(out)})
            print(f"OK {d} n={len(df)}")
        except Exception as e:
            log["fail"].append(
                {"date": d, "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-500:]}
            )
            print(f"FAIL {d}: {type(e).__name__}: {e}")

    if frames:
        all_df = pd.concat(frames, ignore_index=True)
        all_path = CACHE / "fhps_em_2015_2020_all.parquet"
        all_df.to_parquet(all_path, index=False)
        log["merged"] = {"path": str(all_path), "rows": int(len(all_df)), "dates": sorted(all_df["_report_period"].unique().tolist())}
        print("merged", all_path, len(all_df))
    else:
        log["merged"] = None
        print("no frames to merge")

    log["ended_at"] = datetime.now(CST).isoformat()
    (CACHE / "fhps_backfill_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("log written", CACHE / "fhps_backfill_log.json")
    print("ok", len(log["ok"]), "fail", len(log["fail"]), "skip", len(log["skipped_existing"]))


def retry_failed(sleep_s: float = 1.0, max_tries: int = 4) -> None:
    """Re-fetch dates listed in fhps_backfill_log.json fail list."""
    import akshare as ak
    log_path = CACHE / "fhps_backfill_log.json"
    if not log_path.exists():
        print("no log")
        return
    log = json.loads(log_path.read_text())
    still = []
    recovered = []
    for item in log.get("fail", []):
        d = item["date"]
        out = CACHE / f"fhps_em_{d}.parquet"
        if out.exists():
            recovered.append({"date": d, "rows": len(pd.read_parquet(out)), "note": "already_present"})
            continue
        ok = False
        last_err = None
        for i in range(max_tries):
            try:
                time.sleep(sleep_s * (i + 1))
                df = ak.stock_fhps_em(date=d)
                if df is None or len(df) == 0:
                    last_err = "empty"
                    continue
                df.to_parquet(out, index=False)
                recovered.append({"date": d, "rows": int(len(df)), "tries": i + 1})
                print(f"retry OK {d} n={len(df)}")
                ok = True
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                print(f"retry fail {d} try={i+1}: {last_err[:120]}")
        if not ok:
            still.append({"date": d, "error": last_err})
    # rebuild merge
    frames = []
    for d in report_dates():
        path = CACHE / f"fhps_em_{d}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path).assign(_report_period=d, _source="ak.stock_fhps_em"))
    if frames:
        all_df = pd.concat(frames, ignore_index=True)
        all_path = CACHE / "fhps_em_2015_2020_all.parquet"
        all_df.to_parquet(all_path, index=False)
        print("remerged", all_path, len(all_df))
    log["retry"] = {"recovered": recovered, "still_fail": still, "ended_at": datetime.now(CST).isoformat()}
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print("recovered", len(recovered), "still", len(still))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.45)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retry", action="store_true")
    args = ap.parse_args()
    if args.retry:
        retry_failed(sleep_s=max(args.sleep, 1.0))
    else:
        main(sleep_s=args.sleep, force=args.force)

