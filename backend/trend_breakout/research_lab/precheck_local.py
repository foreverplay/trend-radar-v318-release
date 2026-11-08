#!/usr/bin/env python3
"""本机只读预检。不改 .env，不安装依赖，不把数量写死。

在仓库根目录执行:
  .venv/bin/python backend/trend_breakout/research_lab/precheck_local.py
本机解释器是 .venv，不是 venv。缺少 pandas 时仍检查数据库口径，但成交需要这个 .venv。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from price_basis import coverage_row, inspect_connection

RESULTS = Path(__file__).resolve().parent / "results"
PRECHECK_NAME = "precheck.json"


def _interpreter_note(root: Path) -> dict:
    imports = {}
    missing = []
    for name in ("pandas", "numpy", "pyarrow"):
        try:
            __import__(name)
            imports[name] = "ok"
        except Exception as exc:
            imports[name] = type(exc).__name__
            missing.append(name)
    venv = []
    for rel in (".venv/bin/python", "venv/bin/python"):
        if (root / rel).is_file():
            venv.append(rel)
    return {"imports": imports, "missing": missing, "venv_pythons": venv, "current": sys.executable}


def run_precheck(repo_root: Path, sqlite_path: Path, parquet_dir: Path, results_dir: Path = RESULTS) -> dict:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "local_precheck_not_a_backtest",
        "status": "checked",
        "interpreter": {
            "missing": [],
            "venv_pythons": [],
            "imports": {},
        },
        "sqlite": {"present": sqlite_path.is_file(), "bytes": None, "name": sqlite_path.name},
        "tables": {},
        "parquet_daily_files": None,
        "parquet_daily_present": parquet_dir.is_dir(),
        "execution_table": None,
        "execution_block": None,
        "bfq_daily": _bfq_clue(repo_root),
        "kline_sample": _kline_sample(parquet_dir),
    }
    note = _interpreter_note(repo_root)
    payload["interpreter"] = {
        "missing": note["missing"],
        "venv_pythons": note["venv_pythons"],
        "imports": note["imports"],
    }
    if not sqlite_path.is_file():
        payload["status"] = "sqlite_missing"
        payload["execution_block"] = "没有找到 SQLite，不能报告覆盖数量"
        _write(results_dir, payload)
        return payload
    payload["sqlite"]["bytes"] = sqlite_path.stat().st_size
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ("daily_bars_qfq", "daily_bars", "daily_bars_raw", "daily_bars_hfq", "index_bars"):
            if name not in present:
                payload["tables"][name] = {"present": False}
                continue
            if name == "index_bars":
                row = coverage_row(conn, name)
                row["price_basis"] = "index"
                row["detail"] = "指数表，不用于个股名义成交价"
                payload["tables"][name] = row
            else:
                payload["tables"][name] = coverage_row(conn, name)
        report = inspect_connection(conn)
        payload["execution_table"] = report["execution_table"]
        payload["execution_block"] = report["execution_block"]
    finally:
        conn.close()
    if parquet_dir.is_dir():
        payload["parquet_daily_files"] = len(list(parquet_dir.glob("*.parquet")))
    if payload["execution_block"] or note["missing"]:
        payload["status"] = "blocked" if payload["execution_block"] else "dependencies_missing"
    _write(results_dir, payload)
    return payload


def _bfq_clue(repo_root: Path) -> dict:
    directory = repo_root / "backend" / "trend_breakout" / "dividend_v319" / "data" / "bfq_daily"
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return {"present": False, "accepted_as_execution_prices": False}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"present": True, "accepted_as_execution_prices": False, "detail": "bfq_daily/manifest.json 无法读取"}
    files = [path for path in directory.glob("*.parquet")]
    return {
        "present": True,
        "file_count": len(files),
        "adjust": manifest.get("adjust"),
        "source": manifest.get("source"),
        "generated_at": manifest.get("generated_at"),
        "accepted_as_execution_prices": False,
        "detail": "manifest 里的 adjust 和 source 只是这批文件的来源声明，不能证明 daily_bars 的复权口径，也不能当成全市场原始行情验收。",
    }


def _kline_sample(parquet_dir: Path) -> dict:
    sample = parquet_dir / "000001.parquet"
    if not sample.is_file():
        return {"present": False, "complete": False, "detail": "没有抽到 000001.parquet。文件数本身也不代表长期完整。"}
    rows = None
    try:
        import pyarrow.parquet as pq

        rows = pq.ParquetFile(sample).metadata.num_rows
    except Exception:
        rows = None
    return {
        "present": True,
        "name": "000001.parquet",
        "rows": rows,
        "complete": False,
        "detail": "这只是一个文件的行数，不代表全市场或长期完整。",
    }


def _write(results_dir: Path, payload: dict) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / PRECHECK_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    root = Path.cwd()
    sqlite_path = Path(os.environ["RESEARCH_SQLITE_PATH"]).expanduser() if os.environ.get("RESEARCH_SQLITE_PATH") else (root / ".." / "longtou-web" / "backend" / "data" / "longtou.sqlite3")
    parquet_dir = Path(os.environ["RESEARCH_PARQUET_DIR"]).expanduser() if os.environ.get("RESEARCH_PARQUET_DIR") else root / "data" / "kline_market" / "raw" / "daily"
    results_dir = Path(os.environ["RESEARCH_RESULTS_ROOT"]).expanduser() if os.environ.get("RESEARCH_RESULTS_ROOT") else RESULTS
    payload = run_precheck(root, sqlite_path, parquet_dir, results_dir)
    print(json.dumps({k: payload[k] for k in ("status", "execution_table", "execution_block", "tables", "parquet_daily_files")}, ensure_ascii=False, indent=2))
    print(f"预检文件: {PRECHECK_NAME} 写在研究目录。这不是回测。")
    if payload["interpreter"]["missing"]:
        found = payload["interpreter"]["venv_pythons"]
        print("当前解释器缺少: " + ",".join(payload["interpreter"]["missing"]))
        print("项目解释器: " + (", ".join(found) if found else "未在 ./.venv 或 ./venv 发现"))
        print("不要对系统 python 做全局安装。")
    return 0 if payload["status"] == "checked" else 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
