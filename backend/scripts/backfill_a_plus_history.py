#!/usr/bin/env python3
"""回填 V3.1.8 逐日 A+ 档案（strict-asof，不写现网 store/榜单）。

从新到旧跑交易日；已有 CSV 跳过。交易时段暂停，默认低优先级后台循环。
结果目录：history/a_plus_v318_backfill_by_date/{YYYY-MM-DD}.csv
状态：history/a_plus_v318_backfill_status.json

清单口径：跑榜时库里当前 A 股列表；已退市代码不在内；基本面不参与历史评分。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
TB = ROOT / "backend" / "trend_breakout"
HIST = TB / "history"
BACKFILL_DIR = HIST / "a_plus_v318_backfill_by_date"
STATUS_PATH = HIST / "a_plus_v318_backfill_status.json"
ISOLATED_ROOT = TB / "cache" / "backfill_isolated"
CST = ZoneInfo("Asia/Shanghai")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trading_days_from_db(db_path: Path, *, limit: int) -> list[str]:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT trade_date FROM index_bars
            WHERE symbol = 'sh000001' AND trade_date IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0])[:10] for r in rows if r and r[0]]


def in_trading_session(now: datetime | None = None) -> bool:
    now = now or datetime.now(CST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30


def load_status() -> dict:
    if STATUS_PATH.is_file():
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "updatedAt": _iso(),
        "completedDays": 0,
        "targetDays": 0,
        "oldestDone": "",
        "newestDone": "",
        "errors": [],
        "paused": False,
    }


def save_status(payload: dict) -> None:
    HIST.mkdir(parents=True, exist_ok=True)
    payload["updatedAt"] = _iso()
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compare_with_live(day: str, live_dir: Path, backfill_dir: Path) -> dict:
    live = live_dir / f"{day}.csv"
    bf = backfill_dir / f"{day}.csv"
    if not live.is_file() or not bf.is_file():
        return {"day": day, "skipped": True}
    import pandas as pd

    a = pd.read_csv(live, dtype={"code6": str})
    b = pd.read_csv(bf, dtype={"code6": str})
    a_plus = set(a.loc[a["grade_v318"] == "A+", "code6"].str.zfill(6))
    b_plus = set(b.loc[b["grade_v318"] == "A+", "code6"].str.zfill(6))
    return {
        "day": day,
        "live_a_plus": len(a_plus),
        "backfill_a_plus": len(b_plus),
        "only_live": sorted(a_plus - b_plus)[:20],
        "only_backfill": sorted(b_plus - a_plus)[:20],
        "match": a_plus == b_plus,
    }


def _run_one_day(
    day: str,
    kline_root: str,
    db_path: str,
    max_prefilter: int,
    workers: int,
) -> tuple[str, str | None]:
    sys.path.insert(0, str(SCRIPTS))
    import backfill_boards_v318 as bb

    out_csv = BACKFILL_DIR / f"{day}.csv"
    if out_csv.is_file() and out_csv.stat().st_size > 0:
        return day, None
    work = ISOLATED_ROOT / day.replace("-", "")
    work.mkdir(parents=True, exist_ok=True)
    rank_db = str(Path(db_path).resolve())
    try:
        bb.run_day(
            day,
            Path(kline_root),
            max_prefilter,
            workers,
            asof_note="A+历史回补 strict-asof；不写现网 store",
            asof_mode="backfill_isolated",
            rank_db=rank_db,
            breakout_root=work,
            skip_exit_persist=True,
            skip_sample_export=True,
            archive_by_date_dir=BACKFILL_DIR,
        )
    except Exception as exc:  # noqa: BLE001
        return day, f"{type(exc).__name__}: {exc}"
    if not out_csv.is_file():
        return day, "archive_csv_missing"
    return day, None


def run_batch(
    days: list[str],
    *,
    db_path: Path,
    kline_root: Path,
    max_prefilter: int,
    workers: int,
    parallel_days: int,
    status: dict,
) -> None:
    pending = [d for d in days if not (BACKFILL_DIR / f"{d}.csv").is_file()]
    if not pending:
        return
    kline_s = str(kline_root.resolve())
    db_s = str(db_path.resolve())
    if parallel_days <= 1:
        for day in pending:
            if in_trading_session():
                status["pausedReason"] = "trading_session"
                save_status(status)
                return
            day, err = _run_one_day(day, kline_s, db_s, max_prefilter, workers)
            if err:
                status.setdefault("errors", []).append({"day": day, "error": err})
            else:
                status["completedDays"] = int(status.get("completedDays") or 0) + 1
                status["newestDone"] = max(status.get("newestDone") or "", day)
                status["oldestDone"] = min(status.get("oldestDone") or day, day) if status.get("oldestDone") else day
            save_status(status)
        return
    with ProcessPoolExecutor(max_workers=parallel_days) as pool:
        futs = {
            pool.submit(_run_one_day, day, kline_s, db_s, max_prefilter, workers): day
            for day in pending
        }
        for fut in as_completed(futs):
            day, err = fut.result()
            if err:
                status.setdefault("errors", []).append({"day": day, "error": err})
            else:
                status["completedDays"] = int(status.get("completedDays") or 0) + 1
                status["newestDone"] = max(status.get("newestDone") or "", day)
                status["oldestDone"] = min(status.get("oldestDone") or day, day) if status.get("oldestDone") else day
            save_status(status)


def daemon_loop(args: argparse.Namespace) -> int:
    db_path = Path(args.market_db).expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"找不到 MARKET_DB_PATH: {db_path}")
    kline_root = Path(args.kline_root).expanduser().resolve()
    status = load_status()
    status["targetDays"] = int(args.days)
    status["paused"] = False
    save_status(status)
    live_dir = HIST / "a_plus_v318_by_date"
    while True:
        if in_trading_session():
            status["pausedReason"] = "trading_session"
            save_status(status)
            time.sleep(60)
            continue
        days = trading_days_from_db(db_path, limit=args.days)
        run_batch(
            days,
            db_path=db_path,
            kline_root=kline_root,
            max_prefilter=args.max_prefilter,
            workers=args.workers,
            parallel_days=args.parallel_days,
            status=status,
        )
        pending = sum(1 for d in days if not (BACKFILL_DIR / f"{d}.csv").is_file())
        status["pendingDays"] = pending
        if pending == 0:
            status["message"] = "目标交易日已全部回补"
        else:
            status["message"] = f"尚有 {pending} 个交易日待回补"
        save_status(status)
        if args.once:
            break
        time.sleep(max(30, int(args.sleep)))
    if args.compare_live:
        live_dir = HIST / "a_plus_v318_by_date"
        reports = []
        for day in sorted(d for d in os.listdir(live_dir) if d.endswith(".csv"))[:10]:
            day_s = day.replace(".csv", "")
            reports.append(compare_with_live(day_s, live_dir, BACKFILL_DIR))
        cmp_path = HIST / "a_plus_v318_backfill_compare.json"
        cmp_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {cmp_path}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="V3.1.8 A+ 历史回补（隔离目录）")
    ap.add_argument("--days", type=int, default=750, help="最多回补多少个历史交易日（从新到旧）")
    ap.add_argument("--kline-root", default=str(ROOT / "data" / "kline_market"))
    ap.add_argument("--market-db", default=os.environ.get("MARKET_DB_PATH", ""))
    ap.add_argument("--max-prefilter", type=int, default=250)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--parallel-days", type=int, default=1, help="同时跑几个交易日（默认 1，稳妥）")
    ap.add_argument("--sleep", type=int, default=300, help="daemon 两轮间隔秒数")
    ap.add_argument("--once", action="store_true", help="跑一轮后退出")
    ap.add_argument("--compare-live", action="store_true", help="结束时与现网 by_date 对比 A+ 集合")
    ap.add_argument("--only", default="", help="只跑这些日期，逗号分隔 YYYY-MM-DD")
    args = ap.parse_args()
    if not args.market_db:
        raise SystemExit("需要 --market-db 或环境变量 MARKET_DB_PATH")
    if args.only.strip():
        days = [d.strip() for d in args.only.split(",") if d.strip()]
        status = load_status()
        run_batch(
            days,
            db_path=Path(args.market_db).expanduser().resolve(),
            kline_root=Path(args.kline_root).expanduser().resolve(),
            max_prefilter=args.max_prefilter,
            workers=args.workers,
            parallel_days=1,
            status=status,
        )
        save_status(status)
        if args.compare_live:
            live_dir = HIST / "a_plus_v318_by_date"
            for day in days:
                print(compare_with_live(day, live_dir, BACKFILL_DIR), flush=True)
        return 0
    try:
        os.nice(10)
    except OSError:
        pass
    return daemon_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
