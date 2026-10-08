"""
As-of MarketRegime / RISK_OFF reader for V3.1.1 regression.

Uses history/market_regime_daily.{jsonl,csv} when present.
Never reads future rows relative to t_date.
Missing → NORMAL / risk_off=False.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

ROOT = _PKG_ROOT
HISTORY = ROOT / "history"
ARCHIVE_JSONL = HISTORY / "market_regime_daily.jsonl"
ARCHIVE_CSV = HISTORY / "market_regime_daily.csv"


@lru_cache(maxsize=1)
def _load_archive_df() -> pd.DataFrame:
    rows = []
    if ARCHIVE_JSONL.is_file():
        with ARCHIVE_JSONL.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows and ARCHIVE_CSV.is_file():
        df = pd.read_csv(ARCHIVE_CSV)
        return df
    if not rows:
        return pd.DataFrame(
            columns=["t_date", "regime_v3", "legacy_regime", "risk_off", "market_score", "cap"]
        )
    return pd.DataFrame(rows)


def regime_asof(t_date: str) -> Dict[str, Any]:
    """
    Return regime row as-of t_date (last available on or before t_date).
    If none: default NORMAL, risk_off=False, source=default.
    """
    td = str(t_date)[:10]
    df = _load_archive_df()
    if df is None or df.empty or "t_date" not in df.columns:
        return {
            "t_date": td,
            "regime_v3": "NORMAL",
            "legacy_regime": "",
            "risk_off": False,
            "market_score": None,
            "cap": None,
            "source": "default_missing_archive",
            "archive_asof": None,
        }
    d = df.copy()
    d["t_date"] = d["t_date"].astype(str).str[:10]
    sub = d[d["t_date"] <= td].sort_values("t_date")
    if sub.empty:
        return {
            "t_date": td,
            "regime_v3": "NORMAL",
            "legacy_regime": "",
            "risk_off": False,
            "market_score": None,
            "cap": None,
            "source": "default_before_archive",
            "archive_asof": None,
        }
    row = sub.iloc[-1].to_dict()
    risk_off = row.get("risk_off")
    if isinstance(risk_off, str):
        risk_off = risk_off.strip() in ("1", "true", "True", "YES", "yes")
    else:
        risk_off = bool(risk_off)
    return {
        "t_date": td,
        "regime_v3": str(row.get("regime_v3") or "NORMAL"),
        "legacy_regime": str(row.get("legacy_regime") or ""),
        "risk_off": risk_off,
        "market_score": row.get("market_score"),
        "cap": row.get("cap"),
        "source": "archive",
        "archive_asof": str(row.get("t_date"))[:10],
    }


def clear_regime_cache() -> None:
    _load_archive_df.cache_clear()


__all__ = ["regime_asof", "clear_regime_cache", "ARCHIVE_JSONL", "ARCHIVE_CSV"]
