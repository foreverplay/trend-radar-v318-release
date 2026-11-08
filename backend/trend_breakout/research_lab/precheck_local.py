#!/usr/bin/env python3
"""本机只读预检。不改 .env，不安装依赖，不读取 VM 路径。

在仓库根目录执行:
  ./venv/bin/python backend/trend_breakout/research_lab/precheck_local.py
若 venv 不在 ./venv，改用实际存在的 .venv/bin/python。不要用缺少 pandas 的系统 python 做全局安装。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    root = Path.cwd()
    sqlite_path = (root / ".." / "longtou-web" / "backend" / "data" / "longtou.sqlite3").resolve()
    parquet_dir = root / "data" / "kline_market" / "raw" / "daily"
    print(f"repo: {root}")
    print(f"python: {sys.executable}")
    missing = []
    for name in ("pandas", "numpy", "pyarrow"):
        try:
            __import__(name)
            print(f"import {name}: ok")
        except Exception as exc:
            missing.append(name)
            print(f"import {name}: {exc}")
    if missing:
        print("当前解释器缺少依赖。先确认仓库里的 venv 或 .venv，再使用那个解释器。不要对系统 python 做全局安装。")
        return 2
    if not sqlite_path.is_file():
        print(f"SQLite 不存在: {sqlite_path}")
        return 2
    print(f"sqlite: {sqlite_path} bytes={sqlite_path.stat().st_size}")
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        print(f"tables: {sorted(tables)}")
        for table, symbol_col, date_col in (
            ("daily_bars_qfq", "symbol", "trade_date"),
            ("daily_bars", "symbol", "trade_date"),
            ("index_bars", "symbol", "trade_date"),
        ):
            if table not in tables:
                print(f"{table}: missing")
                continue
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            print(f"{table} columns: {cols}")
            if symbol_col in cols and date_col in cols:
                row = conn.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT {symbol_col}), MAX({date_col}) FROM {table}"
                ).fetchone()
                print(f"{table}: rows={row[0]} symbols={row[1]} max_date={row[2]}")
        has_raw = "daily_bars_raw" in tables
        print(f"daily_bars_raw: {has_raw}")
        if not has_raw:
            print("阻断: 库里没有 daily_bars_raw。前复权不能当作名义成交价，正式和诊断成交都会拒绝。")
    finally:
        conn.close()
    if parquet_dir.is_dir():
        print(f"parquet daily files: {len(list(parquet_dir.glob('*.parquet')))} at {parquet_dir}")
    else:
        print(f"parquet daily dir missing: {parquet_dir}")
    print("预检只确认文件和表。它不是回测，也没有在这台机器上证明收益。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
