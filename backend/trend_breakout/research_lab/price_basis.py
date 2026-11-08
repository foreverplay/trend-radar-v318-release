"""判断行情表是不是可交易的原始名义价。

表名不是 daily_bars_raw，不能据此说原始数据不存在。
daily_bars 没有复权标记时，口径无法证明，成交必须拒绝，也不能改读前复权。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BASIS_COLUMNS = ("price_basis", "adjust", "adjust_type", "fq", "adj_type", "rehab", "adjustment")
_RAW = {"raw", "none", "unadjusted", "bfq", "不复权"}
_QFQ = {"qfq", "forward", "前复权"}
_HFQ = {"hfq", "backward", "后复权"}
_BAR_TABLES = ("daily_bars_raw", "daily_bars", "daily_bars_qfq", "daily_bars_hfq")


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    if not _IDENT.fullmatch(table):
        return []
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _distinct(conn: sqlite3.Connection, table: str, column: str, limit: int = 8) -> List[str]:
    if not (_IDENT.fullmatch(table) and _IDENT.fullmatch(column)):
        return []
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT {int(limit)}'
    )
    return [str(row[0]).strip() for row in rows]


def classify_table(conn: sqlite3.Connection, table: str) -> Dict[str, Any]:
    columns = _columns(conn, table)
    basis_column = next((name for name in _BASIS_COLUMNS if name in columns), None)
    if basis_column:
        values = {item.lower() for item in _distinct(conn, table, basis_column)}
        if values and values <= _RAW:
            basis = "raw"
            detail = f"列 {basis_column} 全部是不复权标记"
        elif values and values <= _QFQ:
            basis = "qfq"
            detail = f"列 {basis_column} 标记为前复权"
        elif values and values <= _HFQ:
            basis = "hfq"
            detail = f"列 {basis_column} 标记为后复权"
        else:
            basis = "unproven"
            detail = f"列 {basis_column} 的取值无法归成单一口径: {sorted(values) or ['空']}"
        return {"table": table, "price_basis": basis, "basis_source": f"column:{basis_column}", "detail": detail, "columns": columns}

    if table == "daily_bars_qfq":
        return {
            "table": table,
            "price_basis": "qfq",
            "basis_source": "table_name",
            "detail": "表名 daily_bars_qfq 表示前复权，不能当作名义成交价",
            "columns": columns,
        }
    if table == "daily_bars_hfq":
        return {
            "table": table,
            "price_basis": "hfq",
            "basis_source": "table_name",
            "detail": "表名 daily_bars_hfq 表示后复权，不能当作名义成交价",
            "columns": columns,
        }
    if table == "daily_bars_raw":
        return {
            "table": table,
            "price_basis": "raw",
            "basis_source": "table_name",
            "detail": "表名 daily_bars_raw 明确标成原始行情",
            "columns": columns,
        }
    if table == "daily_bars":
        return {
            "table": table,
            "price_basis": "unproven",
            "basis_source": "none",
            "detail": "存在 daily_bars，但没有复权口径列或来源标记，不能证明它是原始名义价，也不能把它当成前复权",
            "columns": columns,
        }
    return {
        "table": table,
        "price_basis": "unproven",
        "basis_source": "none",
        "detail": "表名没有复权口径",
        "columns": columns,
    }


def inspect_connection(conn: sqlite3.Connection) -> Dict[str, Any]:
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    reports = [classify_table(conn, name) for name in _BAR_TABLES if name in present]
    proven = [item for item in reports if item["price_basis"] == "raw"]
    if len(proven) == 1:
        chosen = proven[0]["table"]
        block = None
    elif len(proven) > 1:
        chosen = None
        block = "多张表都被标成原始行情，拒绝猜测用哪一张"
    else:
        chosen = None
        if any(item["table"] == "daily_bars" and item["price_basis"] == "unproven" for item in reports):
            block = (
                "存在 daily_bars，但没有复权口径列或单一来源标记，不能证明它是原始名义价。"
                "这不是因为缺少名为 daily_bars_raw 的表。证明口径之前，拒绝用 daily_bars，也不改用 daily_bars_qfq。"
            )
        elif any(item["price_basis"] == "qfq" for item in reports):
            block = "只有前复权行情，不能把它当作可交易名义价格计算涨跌停和费用"
        elif not reports:
            block = "没有识别到日线行情表"
        else:
            block = "没有一张日线表被证明是原始名义价"
    return {"tables": reports, "execution_table": chosen, "execution_block": block}


def coverage_row(conn: sqlite3.Connection, table: str) -> Optional[Dict[str, Any]]:
    report = classify_table(conn, table)
    columns = report["columns"]
    symbol = "symbol" if "symbol" in columns else "code" if "code" in columns else None
    date = "trade_date" if "trade_date" in columns else "date" if "date" in columns else None
    out: Dict[str, Any] = {
        "present": True,
        "price_basis": report["price_basis"],
        "basis_source": report["basis_source"],
        "detail": report["detail"],
        "rows": None,
        "symbols": None,
        "max_date": None,
    }
    if symbol and date and _IDENT.fullmatch(symbol) and _IDENT.fullmatch(date) and _IDENT.fullmatch(table):
        row = conn.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT "{symbol}"), MAX("{date}") FROM "{table}"'
        ).fetchone()
        out["rows"] = row[0]
        out["symbols"] = row[1]
        out["max_date"] = row[2]
    return out
