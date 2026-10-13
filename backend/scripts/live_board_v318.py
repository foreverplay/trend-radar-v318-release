#!/usr/bin/env python3
"""盘中用共享库里的当日临时 K 线刷新本地 parquet，并重算一次趋势榜。

全市场快照由行情后台在交易时段写入。本脚本只等这一轮写完（通常一两分钟），
再补当日最后一根并排行。默认每 25 分钟一轮，收盘后再用收盘快照排一次。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
SHANGHAI = ZoneInfo("Asia/Shanghai")
SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 5)
INTRADAY_NOTE = (
    "盘中临时行情：当日K线来自腾讯全市场快照的开高低收、成交量和成交额，会随刷新改写，不是收盘定稿。"
    "历史K线仍是前复权。全市场快照写完后重算本榜。"
)
CLOSE_NOTE = (
    "收盘快照：当日K线已用收盘后的全市场行情覆盖盘中临时值。历史K线仍是前复权。"
    "基本面、异动原因和无法按日还原的资金字段不参与评分。"
)


def _env_file(key: str) -> str:
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    found = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        name, value = raw.split("=", 1)
        if name.strip() == key:
            found = value.strip().strip('"').strip("'")
    return found


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def in_session(now: datetime | None = None) -> bool:
    # 和行情后台的交易时段一致，午休也用最近一次快照。
    current = now or shanghai_now()
    if current.weekday() >= 5:
        return False
    return SESSION_OPEN <= current.time() <= SESSION_CLOSE


def resolve_db() -> Path:
    raw = os.environ.get("MARKET_DB_PATH") or _env_file("MARKET_DB_PATH")
    if not raw:
        raw = "../longtou-web/backend/data/longtou.sqlite3"
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def bar_count(db_path: Path, day: str) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM daily_bars_qfq WHERE trade_date = ?",
            (day,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"] or 0)


def wait_bars(db_path: Path, day: str, min_bars: int, timeout: float) -> int:
    deadline = time.time() + timeout
    count = 0
    while True:
        count = bar_count(db_path, day)
        print(f"today bars {day} count={count}", flush=True)
        if count >= min_bars or time.time() >= deadline:
            return count
        time.sleep(5)


def _rows(db_path: Path, sql: str, day: str) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(row) for row in conn.execute(sql, (day,))]
    finally:
        conn.close()


def patch_file(path: Path, bar: dict, scale_volume: bool) -> str:
    if not path.is_file():
        return "missing"
    frame = pd.read_parquet(path)
    if "date" not in frame.columns or not len(frame):
        return "bad"
    dates = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    day = str(bar["date"])[:10]
    if dates.iloc[-1] > day:
        return "future"
    volume = float(bar["volume"] or 0) * (100.0 if scale_volume else 1.0)
    replacing = dates.iloc[-1] == day
    prev_close = None
    if replacing and len(frame) >= 2:
        prev_close = float(frame["close"].iloc[-2])
    elif not replacing:
        prev_close = float(frame["close"].iloc[-1])
    pct = (float(bar["close"]) / prev_close - 1.0) * 100.0 if prev_close else float("nan")
    values = {
        "date": day,
        "open": float(bar["open"]),
        "close": float(bar["close"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "volume": volume,
        "amount": float(bar["amount"] or 0),
        "pct_chg": pct,
    }
    if replacing:
        idx = frame.index[-1]
        for key, value in values.items():
            if key in frame.columns:
                frame.at[idx, key] = value
    else:
        frame = pd.concat([frame, pd.DataFrame([values])], ignore_index=True)
    tmp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)
    return "replace" if replacing else "append"


def patch_day(db_path: Path, kline_root: Path, day: str, workers: int) -> dict[str, int]:
    stocks = _rows(
        db_path,
        """
        SELECT symbol, trade_date AS date, open, high, low, close, volume, amount
        FROM daily_bars_qfq WHERE trade_date = ?
        """,
        day,
    )
    indexes = _rows(
        db_path,
        """
        SELECT symbol, trade_date AS date, open, high, low, close, volume, amount
        FROM index_bars WHERE trade_date = ?
        """,
        day,
    )
    daily = kline_root / "raw" / "daily"
    index = kline_root / "raw" / "index"
    jobs: list[tuple[Path, dict, bool]] = []
    for row in stocks:
        code = str(row["symbol"])
        if code.isdigit() and len(code) == 6:
            jobs.append((daily / f"{code}.parquet", row, True))
    for row in indexes:
        jobs.append((index / f"{row['symbol']}.parquet", row, False))
    stats = {"replace": 0, "append": 0, "missing": 0, "bad": 0, "future": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(patch_file, path, row, scale) for path, row, scale in jobs]
        for fut in as_completed(futures):
            try:
                kind = fut.result()
            except Exception as exc:  # noqa: BLE001
                stats["error"] += 1
                print(f"  patch failed {exc}", flush=True)
                continue
            stats[kind] = stats.get(kind, 0) + 1
    print(
        f"patched day={day} replace={stats['replace']} append={stats['append']} "
        f"missing={stats['missing']} error={stats['error']}",
        flush=True,
    )
    return stats


def wait_close_pass(day: str, timeout: float) -> bool:
    port = os.environ.get("HUB_PORT") or _env_file("HUB_PORT") or "8790"
    url = f"http://127.0.0.1:{port}/api/status"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                payload = json.loads(resp.read().decode())
            live = payload.get("live") or {}
            if live.get("mode") == "close" and str(live.get("date") or "") == day:
                print(f"close pass ready {live}", flush=True)
                return True
        except Exception:
            pass
        time.sleep(5)
    print("close pass not confirmed, ranking with the latest stored bars", flush=True)
    return False


def run_cycle(day: str, db_path: Path, kline_root: Path, args: argparse.Namespace, note: str, mode: str) -> bool:
    count = wait_bars(db_path, day, args.min_bars, args.wait)
    if count < args.min_bars:
        print(f"skip rank, today bars {count} < {args.min_bars}", flush=True)
        return False
    stats = patch_day(db_path, kline_root, day, args.patch_workers)
    ready = stats["replace"] + stats["append"]
    if ready < args.min_bars:
        print(f"skip rank, patched {ready} < {args.min_bars}", flush=True)
        return False
    sys.path.insert(0, str(SCRIPTS))
    import backfill_boards_v318 as backfill

    backfill.run_day(
        day,
        kline_root,
        args.max_prefilter,
        args.workers,
        asof_note=note,
        asof_mode=mode,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="盘中临时日K刷新并重算趋势榜")
    parser.add_argument("--kline-root", default=str(ROOT / "data" / "kline_market"))
    parser.add_argument("--market-db", default="")
    parser.add_argument("--min-bars", type=int, default=3000)
    parser.add_argument("--wait", type=int, default=600)
    parser.add_argument("--interval", type=int, default=1500, help="两轮排行的最短间隔秒数，默认 25 分钟")
    parser.add_argument("--max-prefilter", type=int, default=250)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patch-workers", type=int, default=4)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--force", action="store_true", help="不在交易时段也排一轮")
    args = parser.parse_args()
    db_path = Path(args.market_db).expanduser().resolve() if args.market_db else resolve_db()
    kline_root = Path(args.kline_root).expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"找不到共享行情库 {db_path}")
    need_settle = False
    settled_day = ""
    while True:
        now = shanghai_now()
        day = now.strftime("%Y-%m-%d")
        trading = in_session(now)
        if not trading and not args.force:
            if need_settle and settled_day != day and now.time() > SESSION_CLOSE:
                wait_close_pass(day, args.wait)
                try:
                    run_cycle(day, db_path, kline_root, args, CLOSE_NOTE, "close_snapshot")
                except Exception as exc:  # noqa: BLE001
                    print(f"close rank failed: {exc}", flush=True)
                settled_day = day
                need_settle = False
            if args.once:
                return 0
            time.sleep(20)
            continue
        started = time.time()
        need_settle = trading
        try:
            run_cycle(day, db_path, kline_root, args, INTRADAY_NOTE, "intraday_temp")
        except Exception as exc:  # noqa: BLE001
            print(f"live rank failed: {exc}", flush=True)
        if args.once:
            return 0
        delay = args.interval - (time.time() - started)
        if delay > 0:
            time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
