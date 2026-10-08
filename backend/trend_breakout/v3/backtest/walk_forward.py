"""
Walk-Forward / OOS helpers for Phase 10/11 exit grid.

Data reality: Plan-grade daily A+ history for 2019–2023 does NOT exist.
Available: legacy RecScore≥90 archive ``history/a_plus_daily_2026.parquet``
(2026-01-05…2026-09-21). Therefore this module uses a **2026 YTD proxy**
split — NOT the Plan's 2019–2025 ideal.

wf_window = ``2026YTD_proxy``

禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

# Trading-calendar tuned bounds (A+ archive days)
WF_WINDOW_LABEL = "2026YTD_proxy"
WF_SPLITS: Dict[str, Tuple[str, str]] = {
    # Train: Jan–Apr
    "train": ("2026-01-05", "2026-04-30"),
    # Val: first post-Labor-Day session … Jun
    "val": ("2026-05-06", "2026-06-30"),
    # Test: Jul … archive end
    "test": ("2026-07-01", "2026-09-21"),
}

DEFERRED_NOTE = (
    "Full Plan A+ Walk-Forward 2019–2025 is DEFERRED: Plan-grade daily A+ "
    "lists for 2019–2023 do not exist locally. This run is a 2026YTD_proxy "
    "only (legacy RecScore≥90 archive + kline parquets)."
)


@dataclass
class WFSplit:
    name: str
    start: str
    end: str

    def contains(self, t_date: str) -> bool:
        d = pd.Timestamp(t_date).strftime("%Y-%m-%d")
        return self.start <= d <= self.end


@dataclass
class WFSpec:
    label: str = WF_WINDOW_LABEL
    splits: Dict[str, WFSplit] = field(default_factory=dict)
    deferred_note: str = DEFERRED_NOTE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wf_window": self.label,
            "splits": {k: {"start": v.start, "end": v.end} for k, v in self.splits.items()},
            "deferred_note": self.deferred_note,
        }


def default_wf_spec() -> WFSpec:
    splits = {k: WFSplit(name=k, start=a, end=b) for k, (a, b) in WF_SPLITS.items()}
    return WFSpec(label=WF_WINDOW_LABEL, splits=splits)


def filter_entries_for_split(
    entries: Sequence[Tuple[str, str]],
    split: WFSplit,
) -> List[Tuple[str, str]]:
    return [(d, c) for d, c in entries if split.contains(d)]


def _sample_range(
    df: pd.DataFrame,
    *,
    start: str,
    end: str,
    top_n_per_day: int,
    max_entries: int,
) -> List[Tuple[str, str]]:
    sub = df[(df["t_date"] >= pd.Timestamp(start)) & (df["t_date"] <= pd.Timestamp(end))]
    if len(sub) == 0:
        return []
    picked: List[Tuple[str, str]] = []
    for t, g in sub.groupby("t_date", sort=True):
        for _, row in g.head(int(top_n_per_day)).iterrows():
            code = str(row["code6"]).strip()
            if code.startswith(("sh", "sz", "bj")):
                code = code[2:]
            if "." in code:
                code = code.split(".")[0]
            code = code.zfill(6)
            picked.append((pd.Timestamp(t).strftime("%Y-%m-%d"), code))
            if len(picked) >= int(max_entries):
                return picked
    return picked


def sample_legacy_entries(
    path: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    top_n_per_day: int = 5,
    max_entries: int = 300,
    stratified: bool = True,
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """
    Sample legacy RecScore A+ archive.

    Default **stratified** across WF splits so Train/Val/Test all get entries:
      budget shares train=0.45, val=0.30, test=0.25 of max_entries
      (within each split: top N/day by recommendation_score).

    If stratified=False: chronological top N/day from ``start``…``end`` (may
    exhaust budget in early months).
    """
    df = pd.read_parquet(path)
    if "t_date" not in df.columns or "code6" not in df.columns:
        return [], {"error": "bad_schema", "path": path}
    df = df.copy()
    df["t_date"] = pd.to_datetime(df["t_date"])
    score_col = "recommendation_score" if "recommendation_score" in df.columns else None
    if score_col:
        df = df.sort_values(["t_date", score_col], ascending=[True, False])
    else:
        df = df.sort_values(["t_date"], ascending=[True])

    per_split_counts: Dict[str, int] = {}
    picked: List[Tuple[str, str]] = []

    if stratified:
        shares = {"train": 0.45, "val": 0.30, "test": 0.25}
        # ensure at least 1 if max_entries small
        budgets = {}
        remaining = int(max_entries)
        keys = list(shares.keys())
        for i, k in enumerate(keys):
            if i == len(keys) - 1:
                budgets[k] = max(0, remaining)
            else:
                b = max(1, int(round(int(max_entries) * shares[k])))
                budgets[k] = min(b, remaining)
                remaining -= budgets[k]
        for k, (a, b) in WF_SPLITS.items():
            part = _sample_range(
                df, start=a, end=b, top_n_per_day=top_n_per_day, max_entries=budgets.get(k, 0)
            )
            per_split_counts[k] = len(part)
            picked.extend(part)
        sampling_rule = (
            f"stratified WF shares train/val/test=0.45/0.30/0.25; "
            f"top{top_n_per_day}/day by recommendation_score; max_entries={max_entries}"
        )
        date_start = WF_SPLITS["train"][0]
        date_end = WF_SPLITS["test"][1]
    else:
        a = start or WF_SPLITS["train"][0]
        b = end or WF_SPLITS["test"][1]
        picked = _sample_range(
            df, start=a, end=b, top_n_per_day=top_n_per_day, max_entries=max_entries
        )
        sampling_rule = (
            f"top{top_n_per_day}/day by recommendation_score, max_entries={max_entries} "
            f"(chronological, non-stratified)"
        )
        date_start = a
        date_end = b
        for k, (sa, sb) in WF_SPLITS.items():
            per_split_counts[k] = sum(1 for d, _ in picked if sa <= d <= sb)

    # de-dup preserve order
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for e in picked:
        if e in seen:
            continue
        seen.add(e)
        uniq.append(e)

    meta = {
        "entry_source": "legacy_rec_score",
        "path": str(path),
        "sampling_rule": sampling_rule,
        "top_n_per_day": int(top_n_per_day),
        "max_entries": int(max_entries),
        "n_entries": len(uniq),
        "per_split_counts": per_split_counts,
        "stratified": bool(stratified),
        "date_start": date_start,
        "date_end": date_end,
        "note": "NOT Plan-grade A+; legacy RecScore≥90 archive",
    }
    return uniq, meta


__all__ = [
    "WF_WINDOW_LABEL",
    "WF_SPLITS",
    "DEFERRED_NOTE",
    "WFSplit",
    "WFSpec",
    "default_wf_spec",
    "filter_entries_for_split",
    "sample_legacy_entries",
]
