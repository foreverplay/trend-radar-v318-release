"""Read ranking bars from the shared market DB when that day is already complete.

Parquet remains the historical path. This module is used only while TREND_RANK_DB
points at the sqlite cache, so a finished quote pass can be ranked without walking
every local parquet file. Stock volume is stored in lots; callers that used to read
patched parquet expect shares, so stock volume is multiplied by 100 here.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

INDEX_SYMBOLS = ("sh000001", "sz399001", "sh000300", "sh000852", "sz399006")
_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH = ""
_STOCKS: dict[str, pd.DataFrame] = {}
_INDEXES: dict[str, pd.DataFrame] = {}


def db_path() -> Optional[Path]:
    raw = os.environ.get("TREND_RANK_DB", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _connect(path: Path) -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    key = str(path)
    if _CONN is not None and _CONN_PATH == key:
        return _CONN
    if _CONN is not None:
        _CONN.close()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _CONN = conn
    _CONN_PATH = key
    _STOCKS.clear()
    _INDEXES.clear()
    return conn


def _code_full(code6: str) -> str:
    # 与 data_sources._code_full_local 一致，保证行情表代码键不变。
    c6 = str(code6).zfill(6)
    if c6.startswith(("4", "8", "92", "83", "87", "43")):
        return "bj" + c6
    return ("sh" if c6.startswith(("5", "6", "9")) else "sz") + c6


def coverage(path: Path, day: str) -> dict:
    """A day is complete when almost every name from the previous session has a bar."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        today = int(
            conn.execute(
                "SELECT COUNT(*) FROM daily_bars_qfq WHERE trade_date=? AND length(symbol)=6",
                (day,),
            ).fetchone()[0]
            or 0
        )
        prev_row = conn.execute(
            "SELECT MAX(trade_date) FROM daily_bars_qfq WHERE trade_date < ?",
            (day,),
        ).fetchone()
        prev = str(prev_row[0]) if prev_row and prev_row[0] else ""
        prev_n = 0
        if prev:
            prev_n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM daily_bars_qfq WHERE trade_date=? AND length(symbol)=6",
                    (prev,),
                ).fetchone()[0]
                or 0
            )
        marks = ",".join("?" for _ in INDEX_SYMBOLS)
        indexes = int(
            conn.execute(
                f"SELECT COUNT(*) FROM index_bars WHERE trade_date=? AND symbol IN ({marks})",
                (day, *INDEX_SYMBOLS),
            ).fetchone()[0]
            or 0
        )
        try:
            needs = int(
                conn.execute("SELECT COUNT(*) FROM qfq_sync_state WHERE needs_full=1").fetchone()[0] or 0
            )
        except sqlite3.OperationalError:
            needs = 0
    finally:
        conn.close()
    allowed_gap = max(20, int(prev_n * 0.01)) if prev_n else 0
    complete = bool(
        prev_n >= 3000
        and today >= prev_n - allowed_gap
        and indexes >= len(INDEX_SYMBOLS)
        and needs == 0
    )
    return {
        "complete": complete,
        "today": today,
        "prev": prev,
        "prev_n": prev_n,
        "indexes": indexes,
        "needs_full": needs,
        "allowed_gap": allowed_gap,
    }


def _bars_frame(rows: list, scale_volume: bool) -> pd.DataFrame:
    cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        df["pct_chg"] = []
        return df
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if scale_volume:
        df["volume"] = df["volume"] * 100.0
    df["pct_chg"] = df["close"].pct_change() * 100.0
    return df[["date", "open", "close", "high", "low", "volume", "amount", "pct_chg"]]


def _cached_stock(code6: str) -> Optional[pd.DataFrame]:
    path = db_path()
    if path is None:
        return None
    code6 = str(code6).zfill(6)
    with _LOCK:
        hit = _STOCKS.get(code6)
        if hit is not None:
            return hit.copy()
        conn = _connect(path)
        rows = conn.execute(
            """
            SELECT trade_date, open, high, low, close, volume, amount
            FROM daily_bars_qfq
            WHERE symbol=?
            ORDER BY trade_date
            """,
            (code6,),
        ).fetchall()
        if not rows:
            return None
        frame = _bars_frame([tuple(row) for row in rows], scale_volume=True)
        if len(_STOCKS) > 4000:
            _STOCKS.clear()
        _STOCKS[code6] = frame
        return frame.copy()


def load_stock(code6: str, t_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    frame = _cached_stock(code6)
    if frame is None or frame.empty:
        return None
    if t_date:
        frame = frame[frame["date"] <= pd.Timestamp(t_date)]
    if frame.empty:
        return None
    return frame.reset_index(drop=True)


def load_index(symbol: str, t_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    path = db_path()
    if path is None:
        return None
    symbol = str(symbol).lower()
    with _LOCK:
        hit = _INDEXES.get(symbol)
        if hit is None:
            conn = _connect(path)
            rows = conn.execute(
                """
                SELECT trade_date, open, high, low, close, volume, amount
                FROM index_bars
                WHERE symbol=?
                ORDER BY trade_date
                """,
                (symbol,),
            ).fetchall()
            if not rows:
                return None
            hit = _bars_frame([tuple(row) for row in rows], scale_volume=False)
            _INDEXES[symbol] = hit
        frame = hit.copy()
    if t_date:
        frame = frame[frame["date"] <= pd.Timestamp(t_date)]
    if frame.empty:
        return None
    return frame.reset_index(drop=True)


def spot(t_date: str) -> pd.DataFrame:
    """T-day quote table. Previous close is that symbol's last bar before T."""
    path = db_path()
    columns = ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "最高", "最低", "今开", "昨收"]
    if path is None:
        return pd.DataFrame(columns=columns)
    with _LOCK:
        conn = _connect(path)
        prev_row = conn.execute(
            "SELECT MAX(trade_date) FROM daily_bars_qfq WHERE trade_date < ?",
            (t_date,),
        ).fetchone()
        prev = str(prev_row[0]) if prev_row and prev_row[0] else ""
        rows = conn.execute(
            """
            SELECT t.symbol, t.open, t.high, t.low, t.close, t.volume, t.amount, p.close AS prev_close, q.name
            FROM daily_bars_qfq t
            LEFT JOIN daily_bars_qfq p ON p.symbol=t.symbol AND p.trade_date=?
            LEFT JOIN stock_quotes q ON q.symbol=t.symbol
            WHERE t.trade_date=? AND length(t.symbol)=6
            """,
            (prev, t_date),
        ).fetchall()
        missing = [str(row["symbol"]) for row in rows if row["prev_close"] is None]
        older: dict[str, float] = {}
        if missing:
            marks = ",".join("?" for _ in missing)
            found = conn.execute(
                f"""
                SELECT symbol, close FROM (
                    SELECT symbol, close,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
                    FROM daily_bars_qfq
                    WHERE trade_date < ? AND symbol IN ({marks})
                ) WHERE rn=1
                """,
                (t_date, *missing),
            ).fetchall()
            older = {str(row["symbol"]): float(row["close"]) for row in found}
    out = []
    for row in rows:
        code6 = str(row["symbol"])
        close = float(row["close"] or 0)
        prev_close = row["prev_close"]
        if prev_close is None:
            prev_close = older.get(code6)
        prev_close = float(prev_close or 0)
        if close <= 0 or prev_close <= 0:
            continue
        name = str(row["name"] or "").strip() or code6
        out.append(
            {
                "代码": _code_full(code6),
                "名称": name,
                "最新价": close,
                "涨跌幅": (close / prev_close - 1.0) * 100.0,
                "成交量": float(row["volume"] or 0) * 100.0,
                "成交额": float(row["amount"] or 0),
                "最高": float(row["high"] or close),
                "最低": float(row["low"] or close),
                "今开": float(row["open"] or close),
                "昨收": prev_close,
            }
        )
    if not out:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(out)


def recent_bars(t_date: str, calendar_days: int = 320) -> pd.DataFrame:
    """Close and amount over a window long enough for the 80-bar prefilter."""
    path = db_path()
    empty = pd.DataFrame(columns=["symbol", "date", "close", "amount"])
    if path is None:
        return empty
    start = (datetime.strptime(t_date[:10], "%Y-%m-%d") - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    with _LOCK:
        conn = _connect(path)
        frame = pd.read_sql_query(
            """
            SELECT symbol, trade_date AS date, close, amount
            FROM daily_bars_qfq
            WHERE trade_date>=? AND trade_date<=? AND length(symbol)=6
            """,
            conn,
            params=(start, t_date[:10]),
        )
    if frame.empty:
        return empty
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    return frame
