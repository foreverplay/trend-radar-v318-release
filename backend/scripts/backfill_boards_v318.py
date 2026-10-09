#!/usr/bin/env python3
"""Download a local full-market daily store and backfill strict as-of boards.

The stock universe is whoever is listed when the download runs. Every scan
then truncates prices and indicators to that historical date. Fundamentals and
unrestorable news/flow fields are not used as historical scores.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "backend" / "api"
TB = ROOT / "backend" / "trend_breakout"
SAMPLE = ROOT / "data" / "sample"
UA = {"User-Agent": "Mozilla/5.0 (compatible; trend-radar-backfill/3.1.8)"}
HIS_UT = "7eea3edcaed734bea9cbfc24409ed989"
CLISTS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "http://push2.eastmoney.com/api/qt/clist/get",
)
KLINES = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "http://push2his.eastmoney.com/api/qt/stock/kline/get",
)
INDEXES = {
    "sh000001": "1.000001",
    "sz399001": "0.399001",
    "sh000300": "1.000300",
    "sh000852": "1.000852",
    "sz399006": "0.399006",
}


def _get(url: str, params: dict, headers: Dict[str, str] | None = None, timeout: float = 15):
    last = None
    merged = dict(UA)
    if headers:
        merged.update(headers)
    for i in range(3):
        try:
            response = requests.get(url, params=params, headers=merged, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(last)


def _universe_eastmoney() -> pd.DataFrame:
    rows: List[dict] = []
    page = 1
    total = 1
    params = {
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f14",
    }
    while page <= 80 and len(rows) < total:
        payload = None
        params["pn"] = str(page)
        for url in CLISTS:
            try:
                payload = _get(url, params)
                break
            except Exception:
                payload = None
        if not payload:
            break
        data = payload.get("data") or {}
        total = int(data.get("total") or len(rows))
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not diff:
            break
        before = len(rows)
        for row in diff:
            code = str(row.get("f12") or "").zfill(6)
            if code.isdigit() and len(code) == 6:
                rows.append({"code": code, "name": str(row.get("f14") or code)})
        if len(rows) == before:
            break
        page += 1
    return pd.DataFrame(rows).drop_duplicates("code") if rows else pd.DataFrame(columns=["code", "name"])


def _universe_sina() -> pd.DataFrame:
    rows: List[dict] = []
    for page in range(1, 80):
        payload = _get(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
            {"page": str(page), "num": "100", "sort": "symbol", "asc": "1", "node": "hs_a", "_s_r_a": "page"},
        )
        if not isinstance(payload, list) or not payload:
            break
        for row in payload:
            symbol = str(row.get("symbol") or row.get("code") or "")
            code = symbol[-6:]
            if code.isdigit() and len(code) == 6:
                rows.append({"code": code, "name": str(row.get("name") or code)})
        if len(payload) < 100:
            break
    return pd.DataFrame(rows).drop_duplicates("code") if rows else pd.DataFrame(columns=["code", "name"])


def fetch_universe() -> pd.DataFrame:
    errors = []
    for loader in (_universe_eastmoney, _universe_sina):
        try:
            out = loader()
            if len(out) >= 3000:
                return out
            errors.append(f"{loader.__name__} rows={len(out)}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{loader.__name__}: {type(e).__name__}: {e}")
    raise SystemExit("未能获取足够股票清单，已停止，不会生成榜单：" + " | ".join(errors))


def _bars_from_payload(payload: dict) -> pd.DataFrame:
    lines = [x for x in ((payload.get("data") or {}).get("klines") or []) if isinstance(x, str)]
    rows = []
    for line in lines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "date": parts[0][:10],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]) if len(parts) > 6 else None,
                "pct_chg": float(parts[8]) if len(parts) > 8 and parts[8] else None,
            }
        )
    return pd.DataFrame(rows)


TENCENT_KLINE_URLS = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
)


def tencent_symbol(code: str) -> str:
    if code.startswith(("92", "83", "87", "43", "4", "8")):
        return "bj" + code
    if code.startswith(("5", "6", "9")):
        return "sh" + code
    return "sz" + code


def download_bars(symbol: str, *, index: bool = False) -> pd.DataFrame:
    """Tencent qfq daily bars. Stock amount is close x volume x 100 (volume is in lots)."""
    payload = None
    last = None
    for url in TENCENT_KLINE_URLS:
        try:
            payload = _get(url, {"param": f"{symbol},day,,,320,qfq"}, {"Referer": "https://gu.qq.com/"})
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
    if payload is None:
        raise RuntimeError(last)
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("day") if index else (node.get("qfqday") or node.get("day") or [])
    out = []
    prev = 0.0
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        close = float(row[2])
        volume = float(row[5])
        shares = volume if index else volume * 100.0
        out.append(
            {
                "date": str(row[0])[:10],
                "open": float(row[1]),
                "close": close,
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": shares,
                "amount": None if index else close * shares,
                "pct_chg": ((close / prev) - 1) * 100 if prev else None,
            }
        )
        prev = close or prev
    return pd.DataFrame(out)


def export_market_db(kline_root: Path, db_path: Path) -> pd.DataFrame:
    """Copy shared qfq bars into the parquet layout the scanner already reads."""
    if not db_path.is_file():
        raise SystemExit(f"找不到共享行情库 {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "daily_bars_qfq" not in tables:
        conn.close()
        raise SystemExit("共享库还没有 daily_bars_qfq，请先启动行情后台或运行 hub.seed_parquet")
    names = pd.read_sql_query(
        "SELECT symbol AS code, name FROM stock_quotes WHERE length(symbol) = 6",
        conn,
    )
    if names.empty:
        conn.close()
        raise SystemExit("共享库没有股票清单，已停止")
    daily = kline_root / "raw" / "daily"
    index = kline_root / "raw" / "index"
    meta = kline_root / "meta"
    for path in (daily, index, meta):
        path.mkdir(parents=True, exist_ok=True)
    names.to_csv(meta / "names.csv", index=False)
    frame = pd.read_sql_query(
        "SELECT symbol, trade_date, open, high, low, close, volume, amount FROM daily_bars_qfq",
        conn,
    )
    if frame.empty:
        conn.close()
        raise SystemExit("共享库还没有前复权日线，已停止")
    written = 0
    for symbol, part in frame.groupby("symbol"):
        code = str(symbol)
        if not (code.isdigit() and len(code) == 6):
            continue
        out = part.rename(columns={"trade_date": "date"}).sort_values("date")
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce") * 100.0
        out["pct_chg"] = out["close"].pct_change() * 100.0
        out[["date", "open", "close", "high", "low", "volume", "amount", "pct_chg"]].to_parquet(
            daily / f"{code}.parquet",
            index=False,
        )
        written += 1
        if written % 500 == 0:
            print(f"  exported {written}", flush=True)
    indexes = pd.read_sql_query(
        "SELECT symbol, trade_date, open, high, low, close, volume, amount FROM index_bars",
        conn,
    )
    conn.close()
    for symbol, part in indexes.groupby("symbol"):
        out = part.rename(columns={"trade_date": "date"}).sort_values("date")
        out["pct_chg"] = out["close"].pct_change() * 100.0
        out[["date", "open", "close", "high", "low", "volume", "amount", "pct_chg"]].to_parquet(
            index / f"{symbol}.parquet",
            index=False,
        )
    latest = str(frame["trade_date"].max())
    coverage = written / max(len(names), 1)
    print(f"coverage files={written} universe={len(names)} ratio={coverage:.3f} latest={latest}", flush=True)
    if written < 3000 or coverage < 0.8:
        raise SystemExit("本地日线覆盖不足 3000 或 80%，已停止，不会生成误导性榜单")
    return names.rename(columns={"code": "code"})


def download_market(kline_root: Path, workers: int) -> pd.DataFrame:
    universe = fetch_universe()
    daily = kline_root / "raw" / "daily"
    index = kline_root / "raw" / "index"
    meta = kline_root / "meta"
    for path in (daily, index, meta):
        path.mkdir(parents=True, exist_ok=True)
    universe.to_csv(meta / "names.csv", index=False)
    pending = []
    for row in universe.itertuples(index=False):
        dest = daily / f"{row.code}.parquet"
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        pending.append(row.code)
    print(f"universe={len(universe)} pending={len(pending)}", flush=True)

    def _one(code: str):
        df = download_bars(tencent_symbol(code))
        if df.empty:
            return code, 0
        df.to_parquet(daily / f"{code}.parquet", index=False)
        return code, len(df)

    done = 0
    failed = 0
    first_error = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, code) for code in pending]
        for fut in as_completed(futs):
            done += 1
            try:
                code, n = fut.result()
                if n <= 0:
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                if first_error is None:
                    first_error = exc
                    print(f"  first failure {exc}", flush=True)
            if done % 100 == 0:
                print(f"  downloaded {done}/{len(pending)} failed={failed}", flush=True)
    for name in INDEXES:
        df = download_bars(name, index=True)
        if df.empty:
            raise SystemExit(f"指数 {name} 下载为空，已停止")
        df.to_parquet(index / f"{name}.parquet", index=False)
    files = list(daily.glob("*.parquet"))
    coverage = len(files) / max(len(universe), 1)
    print(f"coverage files={len(files)} universe={len(universe)} ratio={coverage:.3f} failed={failed}", flush=True)
    if len(files) < 3000 or coverage < 0.8:
        raise SystemExit("本地日线覆盖不足 3000 或 80%，已停止，不会生成误导性榜单")
    return universe


def recent_trade_days(n: int) -> List[str]:
    sys.path.insert(0, str(TB))
    import data_sources as ds

    detected = ds.detect_t_date()
    end = detected["value"]["t_date"]
    days: List[str] = []
    if ds.ak is not None:
        cal = ds.ak.tool_trade_date_hist_sina()
        all_days = [str(x)[:10] for x in cal["trade_date"].tolist() if str(x)[:10] <= end]
        days = all_days[-n:]
    if len(days) < n:
        raise SystemExit(f"交易日历不足 {n} 天（得到 {len(days)}），已停止")
    return days


def export_board(day: str, kline_root: Path) -> None:
    sys.path.insert(0, str(API))
    from boards_loader import parse_push_markdown

    ymd = day.replace("-", "")
    md_path = TB / f"push_close_v318_{ymd}.md"
    if not md_path.is_file():
        raise SystemExit(f"缺少发布文件 {md_path.name}")
    payload = parse_push_markdown(md_path.read_text(encoding="utf-8"), t_date=day)
    meta_path = TB / "market_meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        payload["regime"] = payload.get("regime") or meta.get("regime_v2") or meta.get("regime")
        payload["market_score"] = payload.get("market_score") if payload.get("market_score") is not None else meta.get("market_score")
    payload["asof_mode"] = "strict_local"
    payload["asof_note"] = (
        "严格历史口径：价格与指标截断到该交易日；股票清单是下载时点可得的当前清单；"
        "基本面、异动原因和无法按日还原的资金字段不参与历史评分。个股成交额按收盘价×成交量估算。"
    )
    payload["kline_root"] = str(kline_root)
    dest = SAMPLE / f"boards_v318_{ymd}.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (SAMPLE / md_path.name).write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"exported {dest.name}", flush=True)


def run_day(day: str, kline_root: Path, max_prefilter: int, workers: int) -> None:
    env = os.environ.copy()
    env["ASHARE_KLINE_ROOT"] = str(kline_root)
    env["TREND_BREAKOUT_ROOT"] = str(TB)
    env["PYTHONPATH"] = str(TB)
    print(f"=== scan {day} ===", flush=True)
    subprocess.run(
        [sys.executable, "v2_3_scan.py", "--t-date", day, "--strict-asof", "--max-prefilter", str(max_prefilter), "--workers", str(workers)],
        cwd=TB,
        env=env,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from v3.market_regime_archive import append_from_market_meta_file; from v3.v31.market_regime_asof import clear_regime_cache; print(append_from_market_meta_file()); clear_regime_cache()",
        ],
        cwd=TB,
        env=env,
        check=True,
    )
    print(f"=== publish {day} ===", flush=True)
    subprocess.run(
        [sys.executable, "-m", "v3.publish_close_v318", "--t-date", day],
        cwd=TB,
        env=env,
        check=True,
    )
    export_board(day, kline_root)


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict 10-day V3.1.8 board backfill")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--kline-root", default=str(ROOT / "data" / "kline_market"))
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--max-prefilter", type=int, default=250)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--download-workers", type=int, default=6)
    ap.add_argument(
        "--source",
        choices=("market-db", "tencent"),
        default="market-db" if os.environ.get("MARKET_DB_PATH") else "tencent",
    )
    ap.add_argument("--market-db", default=os.environ.get("MARKET_DB_PATH", ""))
    ap.add_argument("--resume-from", default="", help="只跑该日（YYYY-MM-DD）及之后的交易日，用于中断后续跑")
    args = ap.parse_args()
    kline_root = Path(args.kline_root).expanduser().resolve()
    if args.skip_download:
        pass
    elif args.source == "market-db":
        db_path = Path(args.market_db).expanduser().resolve() if args.market_db else None
        if db_path is None:
            raise SystemExit("market-db 需要 MARKET_DB_PATH 或 --market-db")
        export_market_db(kline_root, db_path)
        sys.path.insert(0, str(TB))
        import data_sources as ds

        latest = str(ds.detect_t_date()["value"]["t_date"])
        uri = f"file:{db_path}?mode=ro"
        probed = sqlite3.connect(uri, uri=True)
        max_date = probed.execute("SELECT MAX(trade_date) FROM daily_bars_qfq").fetchone()[0]
        probed.close()
        if str(max_date) < latest:
            raise SystemExit(f"前复权日线最新日期 {max_date} 早于交易日 {latest}，已停止")
    else:
        download_market(kline_root, args.download_workers)
    days = recent_trade_days(args.days)
    if args.resume_from:
        days = [day for day in days if day >= args.resume_from]
    print("trade days", ",".join(days), flush=True)
    for day in days:
        run_day(day, kline_root, args.max_prefilter, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
