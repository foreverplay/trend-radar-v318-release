"""
Daily market-regime archive writer (Phase 2 wiring).

Paths:
  history/market_regime_daily.jsonl
  history/market_regime_daily.csv

Full MarketEngine rewrite is Phase 4. Here: schema + callable that can write
from existing market_meta.json. Empty/partial archive OK.
Idempotent by t_date (re-append same day replaces/skips duplicate).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .regime_map import market_score_from_meta, pair_from_market_meta

try:
    from paths import ROOT as _PKG_ROOT
except ImportError:  # packaged / flat layout fallback
    from trend_breakout.paths import ROOT as _PKG_ROOT

ROOT = _PKG_ROOT
HISTORY = ROOT / "history"
ARCHIVE_JSONL = HISTORY / "market_regime_daily.jsonl"
ARCHIVE_CSV = HISTORY / "market_regime_daily.csv"

CSV_COLUMNS = (
    "t_date",
    "regime_v3",
    "legacy_regime",
    "risk_off",
    "market_score",
    "cap",
    "notes",
)

DEFAULT_META_PATH = ROOT / "market_meta.json"


def _ensure_history_dir() -> None:
    HISTORY.mkdir(parents=True, exist_ok=True)


def _row_from_parts(
    *,
    t_date: str,
    regime_v3: str,
    legacy_regime: str,
    risk_off: bool,
    market_score: float,
    cap: int,
    notes: str = "",
) -> Dict[str, Any]:
    return {
        "t_date": str(t_date),
        "regime_v3": str(regime_v3),
        "legacy_regime": str(legacy_regime),
        "risk_off": bool(risk_off),
        "market_score": float(market_score),
        "cap": int(cap),
        "notes": notes or "",
    }


def row_from_market_meta(meta: dict) -> Dict[str, Any]:
    """Convert market_meta.json-shaped dict → archive row."""
    ms = market_score_from_meta(meta)
    notes = " | ".join(ms.notes) if ms.notes else ""
    # keep notes short for csv
    if len(notes) > 240:
        notes = notes[:237] + "..."
    return _row_from_parts(
        t_date=ms.t_date,
        regime_v3=ms.regime_v3.value,
        legacy_regime=ms.legacy_regime,
        risk_off=ms.risk_off,
        market_score=ms.market_score,
        cap=ms.cap,
        notes=notes,
    )


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = dict(r)
            out["risk_off"] = "1" if out.get("risk_off") else "0"
            w.writerow({k: out.get(k, "") for k in CSV_COLUMNS})


def _upsert_by_t_date(existing: List[Dict[str, Any]], row: Dict[str, Any]) -> tuple[List[Dict[str, Any]], bool]:
    """
    Replace row with same t_date or append.
    Returns (new_rows, changed).
    """
    t = row["t_date"]
    changed = True
    out: List[Dict[str, Any]] = []
    found = False
    for r in existing:
        if str(r.get("t_date")) == t:
            # identical → no-op
            if (
                str(r.get("regime_v3")) == str(row["regime_v3"])
                and str(r.get("legacy_regime")) == str(row["legacy_regime"])
                and bool(r.get("risk_off")) == bool(row["risk_off"])
                and float(r.get("market_score") or 0) == float(row["market_score"])
                and int(r.get("cap") or 0) == int(row["cap"])
            ):
                out.append(r)
                found = True
                changed = False
            else:
                out.append(row)
                found = True
                changed = True
        else:
            out.append(r)
    if not found:
        out.append(row)
        changed = True
    # stable sort by t_date
    out.sort(key=lambda x: str(x.get("t_date") or ""))
    return out, changed


def append_daily_regime(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upsert one daily regime row into jsonl + csv.
    Idempotent by t_date.
    Returns {"wrote": bool, "path_jsonl": str, "path_csv": str, "row": row}.
    """
    _ensure_history_dir()
    required = ["t_date", "regime_v3", "legacy_regime", "risk_off", "market_score", "cap"]
    for k in required:
        if k not in row:
            raise ValueError(f"archive row missing {k}")
    norm = _row_from_parts(
        t_date=row["t_date"],
        regime_v3=row["regime_v3"],
        legacy_regime=row["legacy_regime"],
        risk_off=bool(row["risk_off"]),
        market_score=float(row["market_score"]),
        cap=int(row["cap"]),
        notes=str(row.get("notes") or ""),
    )
    existing = _load_jsonl(ARCHIVE_JSONL)
    merged, changed = _upsert_by_t_date(existing, norm)
    if changed or not ARCHIVE_CSV.exists() or not ARCHIVE_JSONL.exists():
        _write_jsonl(ARCHIVE_JSONL, merged)
        _write_csv(ARCHIVE_CSV, merged)
    return {
        "wrote": changed,
        "path_jsonl": str(ARCHIVE_JSONL),
        "path_csv": str(ARCHIVE_CSV),
        "row": norm,
        "n_rows": len(merged),
    }


def append_from_market_meta(meta: dict) -> Dict[str, Any]:
    """Read existing market_meta shape and append/upsert one archive row."""
    row = row_from_market_meta(meta)
    return append_daily_regime(row)


def append_from_market_meta_file(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load market_meta.json if present and append. Returns None if missing."""
    p = Path(path) if path else DEFAULT_META_PATH
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        meta = json.load(f)
    if not isinstance(meta, dict) or not meta.get("t_date"):
        return None
    return append_from_market_meta(meta)


# Phase 4 hook: MarketEngine can call this with a full series later.
def write_regime_series_stub(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Stub for future MarketEngine daily series dump.
    Upserts each row; OK if list is empty/partial.
    """
    _ensure_history_dir()
    last: Optional[Dict[str, Any]] = None
    n_wrote = 0
    for r in rows or []:
        last = append_daily_regime(r)
        if last.get("wrote"):
            n_wrote += 1
    return {
        "n_input": len(rows or []),
        "n_wrote": n_wrote,
        "path_jsonl": str(ARCHIVE_JSONL),
        "path_csv": str(ARCHIVE_CSV),
        "last": last,
    }


__all__ = [
    "ARCHIVE_CSV",
    "ARCHIVE_JSONL",
    "CSV_COLUMNS",
    "append_daily_regime",
    "append_from_market_meta",
    "append_from_market_meta_file",
    "row_from_market_meta",
    "write_regime_series_stub",
]
