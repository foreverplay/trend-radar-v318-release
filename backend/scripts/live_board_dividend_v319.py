#!/usr/bin/env python3
"""Live Dividend V3.1.9 board refresher.

Pulls latest qfq bars from MARKET_DB into DIVIDEND_KLINE_ROOT, runs
universe → phase3 → phase6 for the latest trade day, and publishes slim
boards into data/sample/ for the API.

Does not touch Growth V3.1.8 grading / publish.
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
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SHANGHAI = ZoneInfo("Asia/Shanghai")
REFRESH_PATH = ROOT / "data" / "cache" / "dividend_board_refresh.json"
SAMPLE_DIR = ROOT / "data" / "sample"
SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 5)
KLINE_MARKET_DAILY = ROOT / "data" / "kline_market" / "raw" / "daily"


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


def _resolve_path(raw: str, default: Path) -> Path:
    value = raw or ""
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


def write_refresh(state: str, day: str, message: str, **extra) -> None:
    REFRESH_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "day": day,
        "message": message,
        "at": shanghai_now().isoformat(timespec="seconds"),
        **extra,
    }
    REFRESH_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_rank_day(now: datetime) -> str:
    """交易时段用当天；开盘前或周末用最近一个已过开盘的交易日。"""
    if now.weekday() < 5 and now.time() > SESSION_CLOSE:
        return now.strftime("%Y-%m-%d")
    day = now
    if now.weekday() >= 5 or now.time() < SESSION_OPEN:
        day = now - timedelta(days=1)
        while day.weekday() >= 5:
            day = day - timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def in_session(now: datetime | None = None) -> bool:
    current = now or shanghai_now()
    if current.weekday() >= 5:
        return False
    return SESSION_OPEN <= current.time() <= SESSION_CLOSE


def resolve_db() -> Path:
    raw = os.environ.get("MARKET_DB_PATH") or _env_file("MARKET_DB_PATH")
    if not raw:
        raw = "../longtou-web/backend/data/longtou.sqlite3"
    return _resolve_path(raw, ROOT / "../longtou-web/backend/data/longtou.sqlite3")


def resolve_kline_daily() -> Path:
    raw = os.environ.get("DIVIDEND_KLINE_ROOT") or _env_file("DIVIDEND_KLINE_ROOT")
    default = ROOT / "data" / "cache" / "dividend_kline" / "raw" / "daily"
    path = _resolve_path(raw, default)
    if path.name != "daily":
        cand = path / "raw" / "daily"
        path = cand
    path.mkdir(parents=True, exist_ok=True)
    os.environ["DIVIDEND_KLINE_ROOT"] = str(path)
    return path


def resolve_trend_root() -> Path:
    raw = os.environ.get("TREND_BREAKOUT_ROOT") or _env_file("TREND_BREAKOUT_ROOT")
    path = _resolve_path(raw, ROOT / "backend" / "trend_breakout")
    os.environ["TREND_BREAKOUT_ROOT"] = str(path)
    return path


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def latest_trade_day_in_db(db_path: Path, hint: str | None = None) -> str:
    conn = connect(db_path)
    try:
        if hint:
            row = conn.execute(
                "SELECT trade_date FROM daily_bars_qfq WHERE trade_date = ? LIMIT 1",
                (hint,),
            ).fetchone()
            if row:
                return str(row["trade_date"])
        row = conn.execute(
            "SELECT trade_date FROM daily_bars_qfq ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError(f"no bars in {db_path}")
    return str(row["trade_date"])


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


def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" not in out.columns and "trade_date" in out.columns:
        out = out.rename(columns={"trade_date": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    close = out["close"]
    out["MA20"] = close.rolling(20, min_periods=20).mean()
    out["MA60"] = close.rolling(60, min_periods=60).mean()
    out["MA120"] = close.rolling(120, min_periods=120).mean()
    out["MA250"] = close.rolling(250, min_periods=250).mean()
    if {"high", "low", "close"}.issubset(out.columns):
        prev = close.shift(1)
        tr = pd.concat(
            [
                (out["high"] - out["low"]).abs(),
                (out["high"] - prev).abs(),
                (out["low"] - prev).abs(),
            ],
            axis=1,
        ).max(axis=1)
        out["ATR14"] = tr.rolling(14, min_periods=14).mean()
    else:
        out["ATR14"] = np.nan
    return out


def _load_seed_frame(code: str) -> pd.DataFrame | None:
    seed = KLINE_MARKET_DAILY / f"{code}.parquet"
    if not seed.is_file():
        return None
    try:
        df = pd.read_parquet(seed)
    except Exception:
        return None
    if "date" not in df.columns and "trade_date" in df.columns:
        df = df.rename(columns={"trade_date": "date"})
    if "date" not in df.columns:
        return None
    return df


def _export_symbol_from_db(conn: sqlite3.Connection, code: str) -> pd.DataFrame | None:
    rows = conn.execute(
        """
        SELECT trade_date AS date, open, high, low, close, volume, amount
        FROM daily_bars_qfq
        WHERE symbol = ?
        ORDER BY trade_date
        """,
        (code,),
    ).fetchall()
    if not rows:
        return None
    return pd.DataFrame([dict(r) for r in rows])


def _write_code_parquet(path: Path, df: pd.DataFrame) -> None:
    enriched = enrich_indicators(df)
    cols = [
        c
        for c in (
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "MA20",
            "MA60",
            "MA120",
            "MA250",
            "ATR14",
        )
        if c in enriched.columns
    ]
    tmp = path.with_suffix(".parquet.tmp")
    enriched[cols].to_parquet(tmp, index=False)
    tmp.replace(path)


def sync_kline_for_codes(
    db_path: Path,
    kline_dir: Path,
    codes: list[str],
    as_of: str,
    workers: int = 8,
    force: bool = False,
) -> dict[str, int]:
    kline_dir.mkdir(parents=True, exist_ok=True)
    stats = {"wrote": 0, "skipped": 0, "missing": 0, "error": 0, "from_seed": 0, "from_db": 0}

    def one(code: str) -> str:
        code = _z6(code)
        out = kline_dir / f"{code}.parquet"
        if out.is_file() and not force:
            try:
                existing = pd.read_parquet(out)
                if "date" in existing.columns and {"MA20", "ATR14"}.issubset(existing.columns):
                    last = str(pd.to_datetime(existing["date"]).max().date())
                    if last >= as_of:
                        return "skipped"
            except Exception:
                pass

        seed = _load_seed_frame(code)
        df = None
        source = "from_db"
        if seed is not None and not seed.empty:
            seed = seed.copy()
            if "date" not in seed.columns and "trade_date" in seed.columns:
                seed = seed.rename(columns={"trade_date": "date"})
            seed["date"] = pd.to_datetime(seed["date"]).dt.strftime("%Y-%m-%d")
            keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in seed.columns]
            df = seed[keep]
            source = "from_seed"

        # Always prefer MARKET_DB tip / full history when seed missing or stale.
        conn = connect(db_path)
        try:
            if df is None:
                db_df = _export_symbol_from_db(conn, code)
                if db_df is None or db_df.empty:
                    return "missing"
                df = db_df
                source = "from_db"
            else:
                last = str(pd.to_datetime(df["date"]).max().date())
                tip = conn.execute(
                    """
                    SELECT trade_date AS date, open, high, low, close, volume, amount
                    FROM daily_bars_qfq
                    WHERE symbol = ? AND trade_date = ?
                    """,
                    (code, as_of),
                ).fetchone()
                if tip is not None:
                    tip_df = pd.DataFrame([dict(tip)])
                    tip_df["date"] = pd.to_datetime(tip_df["date"]).dt.strftime("%Y-%m-%d")
                    keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
                    df = pd.concat([df[df["date"] < as_of][keep], tip_df[keep]], ignore_index=True)
                elif last < as_of:
                    # Seed stale and no tip row — fall back to full DB export
                    db_df = _export_symbol_from_db(conn, code)
                    if db_df is not None and not db_df.empty:
                        df = db_df
                        source = "from_db"
        finally:
            conn.close()

        if df is None or df.empty:
            return "missing"
        try:
            _write_code_parquet(out, df)
        except Exception:
            return "error"
        return source

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, c): c for c in codes}
        done = 0
        total = len(futs)
        for fut in as_completed(futs):
            done += 1
            try:
                kind = fut.result()
            except Exception as exc:  # noqa: BLE001
                stats["error"] += 1
                print(f"  sync failed {futs[fut]}: {exc}", flush=True)
                kind = "error"
            stats[kind] = stats.get(kind, 0) + 1
            if kind in ("from_seed", "from_db"):
                stats["wrote"] += 1
            if done % 200 == 0 or done == total:
                print(
                    f"  sync progress {done}/{total} wrote={stats['wrote']} "
                    f"skip={stats['skipped']} miss={stats['missing']} err={stats['error']}",
                    flush=True,
                )
    return stats



def build_universe_codes(as_of: str, trend_root: Path) -> list[str]:
    if str(trend_root) not in sys.path:
        sys.path.insert(0, str(trend_root))
    from dividend_v319.engines.dividendUniverseEngine import DividendUniverseEngine
    from dividend_v319.config.dividendConfig import DIVIDEND_CONFIG

    uni = DividendUniverseEngine(DIVIDEND_CONFIG).asof(as_of, min_years=3)
    members = uni.get("members") or []
    codes = []
    for m in members:
        if isinstance(m, dict):
            codes.append(_z6(m.get("symbol") or m.get("code") or ""))
        else:
            # UniverseMember dataclass
            codes.append(_z6(getattr(m, "symbol", None) or getattr(m, "code", "") or ""))
    codes = [c for c in codes if c and c.isdigit() and len(c) == 6]
    print(
        f"universe as_of={as_of} active={len(codes)} mode={uni.get('universe_mode')}",
        flush=True,
    )
    return codes


def run_module(trend_root: Path, module: str, as_of: str, extra_args: list[str] | None = None) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(trend_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    cmd = [sys.executable, "-m", module, "--as-of", as_of]
    if extra_args:
        cmd.extend(extra_args)
    print(f"run: {' '.join(cmd)} (cwd={trend_root})", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=str(trend_root),
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{module} exited {proc.returncode}")


def _slim_boards(boards: dict, limit: int = 100) -> dict:
    out = {}
    for title, rows in (boards or {}).items():
        if isinstance(rows, list):
            out[title] = rows[:limit]
        elif isinstance(rows, dict):
            out[title] = list(rows.get("rows") or [])[:limit]
        else:
            out[title] = []
    return out


def publish_sample(as_of: str, trend_root: Path) -> dict:
    from dividend_v319.config.dividendConfig import OUT_DIR

    tag = as_of.replace("-", "")
    src_dir = OUT_DIR / "boards"
    summary_src = src_dir / f"dividend_board_{tag}_summary.json"
    full_src = src_dir / f"dividend_board_{tag}.json"
    md_src = src_dir / f"dividend_board_{tag}.md"
    if not summary_src.is_file() and full_src.is_file():
        # Derive slim payload from full
        full = json.loads(full_src.read_text(encoding="utf-8"))
        payload = {k: v for k, v in full.items() if k != "rows"}
        payload["boards"] = _slim_boards(payload.get("boards") or {}, 100)
    elif summary_src.is_file():
        payload = json.loads(summary_src.read_text(encoding="utf-8"))
        payload["boards"] = _slim_boards(payload.get("boards") or {}, 100)
    else:
        raise FileNotFoundError(f"phase6 board missing under {src_dir}")

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    board_path = SAMPLE_DIR / f"dividend_board_{tag}.json"
    summary_path = SAMPLE_DIR / f"dividend_board_{tag}_summary.json"
    md_path = SAMPLE_DIR / f"dividend_board_{tag}.md"

    board_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_src.is_file():
        md_text = md_src.read_text(encoding="utf-8")
    else:
        md_text = f"# 红利趋势榜 V3.1.9\nas_of: {as_of}\n"
    md_path.write_text(md_text, encoding="utf-8")
    latest_json = json.dumps(payload, ensure_ascii=False, indent=2)
    (SAMPLE_DIR / "dividend_board_latest.json").write_text(latest_json, encoding="utf-8")
    (SAMPLE_DIR / "dividend_board_latest.md").write_text(md_text, encoding="utf-8")
    (SAMPLE_DIR / "dividend_board_latest_summary.json").write_text(latest_json, encoding="utf-8")
    # Also keep package out/ aliases if phase6 already wrote them
    return {
        "as_of": payload.get("as_of") or as_of,
        "n_scored": payload.get("n_scored"),
        "grade_counts": payload.get("grade_counts") or {},
        "board_counts": {
            k: len(v) if isinstance(v, list) else len((v or {}).get("rows") or [])
            for k, v in (payload.get("boards") or {}).items()
        },
        "paths": {
            "board": str(board_path),
            "summary": str(summary_path),
            "md": str(md_path),
        },
    }


def run_cycle(as_of: str, db_path: Path, args: argparse.Namespace) -> dict:
    t0 = time.time()
    write_refresh("running", as_of, f"正在同步行情并重算红利榜 {as_of}")
    trend_root = resolve_trend_root()
    kline_dir = resolve_kline_daily()
    # Ensure financials env for config re-import in child processes
    fin = os.environ.get("ASHARE_FINANCIALS_ROOT") or _env_file("ASHARE_FINANCIALS_ROOT")
    if fin:
        os.environ["ASHARE_FINANCIALS_ROOT"] = str(_resolve_path(fin, ROOT / "data" / "ashare-financials"))

    n_bars = bar_count(db_path, as_of)
    print(f"MARKET_DB bars on {as_of}: {n_bars}", flush=True)
    if n_bars < args.min_bars and not args.force:
        msg = f"skip: bars {n_bars} < {args.min_bars}"
        write_refresh("skipped", as_of, msg)
        raise RuntimeError(msg)

    codes = build_universe_codes(as_of, trend_root)
    if args.limit and args.limit > 0:
        codes = codes[: args.limit]
        print(f"limit codes -> {len(codes)}", flush=True)
    if not codes:
        raise RuntimeError("empty dividend universe")

    print(f"sync kline -> {kline_dir} codes={len(codes)}", flush=True)
    stats = sync_kline_for_codes(
        db_path,
        kline_dir,
        codes,
        as_of,
        workers=args.patch_workers,
        force=bool(args.force_sync),
    )
    print(f"sync done {stats}", flush=True)

    phase_extra: list[str] = []
    if args.limit and args.limit > 0:
        phase_extra = ["--limit", str(args.limit)]
    run_module(trend_root, "dividend_v319.run_phase3_scores", as_of, phase_extra)
    # phase6 has --limit too
    run_module(trend_root, "dividend_v319.run_phase6_board", as_of, phase_extra)
    published = publish_sample(as_of, trend_root)
    published["elapsed_sec"] = round(time.time() - t0, 1)
    published["sync"] = stats
    write_refresh("done", as_of, f"{as_of} 红利榜已导出", **published)
    print(json.dumps(published, ensure_ascii=False, indent=2), flush=True)
    return published


def main() -> int:
    parser = argparse.ArgumentParser(description="红利趋势 V3.1.9 盘中/收盘刷榜")
    parser.add_argument("--market-db", default="")
    parser.add_argument("--as-of", default="", help="override as-of date YYYY-MM-DD")
    parser.add_argument("--min-bars", type=int, default=1000)
    parser.add_argument("--interval", type=int, default=1800, help="loop interval seconds")
    parser.add_argument("--patch-workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="debug: score only first N universe codes")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--force", action="store_true", help="非交易时段也排一轮")
    parser.add_argument("--force-sync", action="store_true", help="强制重写 kline parquet")
    args = parser.parse_args()

    db_path = Path(args.market_db).expanduser().resolve() if args.market_db else resolve_db()
    if not db_path.is_file():
        raise SystemExit(f"找不到共享行情库 {db_path}")

    # Ensure child processes see dividend kline / financials roots.
    resolve_kline_daily()
    fin = os.environ.get("ASHARE_FINANCIALS_ROOT") or _env_file("ASHARE_FINANCIALS_ROOT") or "./data/ashare-financials"
    os.environ["ASHARE_FINANCIALS_ROOT"] = str(_resolve_path(fin, ROOT / "data" / "ashare-financials"))

    booted = False
    while True:
        now = shanghai_now()
        trading = in_session(now)
        if args.as_of:
            rank_day = args.as_of
        else:
            hint = latest_rank_day(now)
            rank_day = latest_trade_day_in_db(db_path, hint=hint)

        if not trading and not args.force and not booted:
            booted = True
            print(f"startup refresh {rank_day}", flush=True)
            try:
                run_cycle(rank_day, db_path, args)
            except Exception as exc:  # noqa: BLE001
                print(f"startup dividend rank failed: {exc}", flush=True)
                write_refresh("failed", rank_day, str(exc))
            if args.once:
                return 0
            time.sleep(20)
            continue

        if not trading and not args.force:
            if args.once:
                return 0
            time.sleep(20)
            continue

        booted = True
        started = time.time()
        try:
            run_cycle(rank_day, db_path, args)
        except Exception as exc:  # noqa: BLE001
            print(f"live dividend rank failed: {exc}", flush=True)
            write_refresh("failed", rank_day, str(exc))
        if args.once:
            return 0
        delay = args.interval - (time.time() - started)
        if delay > 0:
            time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
