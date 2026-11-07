"""
DividendUniverseEngine — Phase2.

Pool = (current) dividend index constituents ∪ autonomous cash-div screen.
asof(T): autonomous years known only via disclose_date <= T (PIT).
Index side has NO historical constituents → universe_mode=proxy_no_hist_index_cons.

Does NOT score quality. Does NOT invent historical index membership.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from dividend_v319.config.dividendConfig import (
    CACHE_DIR,
    CONT5_CSV,
    DIV_ROOT,
    DIVIDEND_CONFIG,
    EVENTS_PATH,
    FHPS_ALL,
    OUT_DIR,
    UNIVERSE_NON_ST,
    DividendConfig,
)
from dividend_v319.models.dividendModels import DataStatus, UniverseMember

CST = ZoneInfo("Asia/Shanghai")
UNIVERSE_MODE = "proxy_no_hist_index_cons"


def _z6(code: Any) -> str:
    return str(code).strip().zfill(6)


def _load_index_constituents(cfg: DividendConfig) -> Tuple[Dict[str, Set[str]], List[str]]:
    """symbol -> set(index_codes). Uses cache from csindex via akshare (CURRENT snapshot)."""
    out: Dict[str, Set[str]] = {}
    notes: List[str] = []
    for idx in cfg.dividend_index_codes:
        path = CACHE_DIR / f"index_cons_csindex_{idx}.parquet"
        if not path.exists():
            notes.append(f"missing_index_cache:{idx}")
            continue
        df = pd.read_parquet(path)
        code_col = "成分券代码" if "成分券代码" in df.columns else None
        if code_col is None:
            notes.append(f"bad_index_schema:{idx}")
            continue
        asof = None
        if "日期" in df.columns and len(df):
            asof = str(df["日期"].iloc[0])
        notes.append(
            f"index_{idx}_n={len(df)}_asof={asof}_source=ak.index_stock_cons_csindex_CURRENT_ONLY"
        )
        for c in df[code_col].map(_z6):
            out.setdefault(c, set()).add(idx)
    notes.append(
        "universe_mode=proxy_no_hist_index_cons: index leg uses latest constituents at all asof(T)"
    )
    return out, notes


def _load_non_st_universe() -> Set[str]:
    if not UNIVERSE_NON_ST.exists():
        return set()
    u = pd.read_csv(UNIVERSE_NON_ST, dtype=str)
    return set(u["SECURITY_CODE"].map(_z6))


def _name_excluded(name: str, tokens: Tuple[str, ...]) -> Optional[str]:
    n = name or ""
    for t in tokens:
        if t and t in n:
            return f"name_token:{t}"
    return None


def _load_events() -> Optional[pd.DataFrame]:
    if not EVENTS_PATH.exists():
        return None
    df = pd.read_parquet(EVENTS_PATH)
    df["code"] = df["code"].map(_z6)
    df["disclose_date"] = pd.to_datetime(df["disclose_date"], errors="coerce")
    df["ex_dividend_date"] = pd.to_datetime(df["ex_dividend_date"], errors="coerce")
    df["cash_per_share"] = pd.to_numeric(df["cash_per_share"], errors="coerce")
    return df


def _autonomous_from_events(
    cfg: DividendConfig,
    *,
    as_of: Optional[str] = None,
    min_years: Optional[int] = None,
    end_year: Optional[int] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Continuous cash dividend years from normalized events.
    Year attribution = report_period calendar year (same spirit as Phase1 analysis).
    PIT: only rows with disclose_date <= as_of (if as_of given).
    """
    notes: List[str] = []
    ev = _load_events()
    if ev is None:
        notes.append("events_missing_fallback_fhps_all")
        auto, n2 = _autonomous_from_fhps(cfg)
        notes.extend(n2)
        return auto, notes

    ok_prog = set(cfg.assign_progress_ok)
    cash = ev[
        (ev["cash_per_share"].fillna(0) > 0) & (ev["assign_progress"].isin(ok_prog))
    ].copy()
    if as_of:
        t = pd.Timestamp(as_of).normalize()
        before = len(cash)
        cash = cash[cash["disclose_date"].notna() & (cash["disclose_date"] <= t)]
        notes.append(f"pit_filter_disclose_date_<={t.date()}_rows={len(cash)}_from={before}")
    cash["year"] = cash["report_period"].astype(str).str[:4]
    cash = cash[cash["year"].str.fullmatch(r"\d{4}") == True]  # noqa: E712

    n_years = min_years or cfg.min_dividend_years
    # last_y = max report year present in PIT-filtered cash events, capped by as_of calendar year.
    # (Do NOT require a future report year that has no rows yet, e.g. as_of=2026-11 asking for 2026.)
    years_present = sorted({int(y) for y in cash["year"].unique() if str(y).isdigit()})
    if not years_present:
        notes.append("autonomous_no_years_after_pit")
        return {}, notes
    data_last = max(years_present)
    if as_of:
        ts = pd.Timestamp(as_of)
        # Report-year Y cash is typically disclosed in Y+1 (Mar–Jun). For annual continuity,
        # last complete report year at as_of T is T.year - 1 (conservative PIT).
        cal_last = ts.year - 1
        last_y = min(data_last, cal_last)
    elif end_year is not None:
        last_y = min(data_last, int(end_year))
    else:
        last_y = data_last
    need = [str(y) for y in range(last_y - n_years + 1, last_y + 1)]
    notes.append(
        f"autonomous_require_years_pit={need}_n_years={n_years}_data_last={data_last}"
    )

    rows: Dict[str, Dict[str, Any]] = {}
    for code, g in cash.groupby("code"):
        years = sorted(set(g["year"].tolist()))
        name = str(g["name"].iloc[-1]) if "name" in g.columns else ""
        if not set(need).issubset(set(years)):
            continue
        rows[code] = {
            "name": name,
            "cash_dividend_years": years,
            "continuous_cash_years": len(need),
            "required_years": need,
        }
    notes.append(f"autonomous_pit_n={len(rows)}_source=dividend_events")
    return rows, notes


def _autonomous_from_fhps(cfg: DividendConfig) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Phase1 fallback: continuous years from local fhps panel (no PIT)."""
    notes: List[str] = []
    if not FHPS_ALL.exists():
        notes.append("fhps_all_missing")
        return {}, notes
    df = pd.read_parquet(FHPS_ALL)
    notes.append(f"fhps_all_rows={len(df)}_path={FHPS_ALL}")
    ok_prog = set(cfg.assign_progress_ok)
    cash = df[
        (df["PRETAX_BONUS_RMB"].fillna(0) > 0) & (df["ASSIGN_PROGRESS"].isin(ok_prog))
    ].copy()
    cash["year"] = cash["报告期"].astype(str).str[:4]
    cash["SECURITY_CODE"] = cash["SECURITY_CODE"].map(_z6)
    need = set(cfg.autonomous_require_years)
    rows: Dict[str, Dict[str, Any]] = {}
    for code, g in cash.groupby("SECURITY_CODE"):
        years = sorted(set(g["year"].tolist()))
        name = str(g["SECURITY_NAME_ABBR"].iloc[-1])
        cont = len(need.intersection(years))
        if not need.issubset(set(years)):
            continue
        rows[code] = {
            "name": name,
            "cash_dividend_years": years,
            "continuous_cash_years": cont,
            "required_years": sorted(need),
        }
    notes.append(
        f"autonomous_cont_years={sorted(need)}_n={len(rows)}_rule=impl_cash_by_report_year"
    )
    return rows, notes


def build_universe(
    cfg: Optional[DividendConfig] = None,
    *,
    as_of: Optional[str] = None,
    min_years: Optional[int] = None,
    use_events_pit: bool = True,
) -> Dict[str, Any]:
    cfg = cfg or DIVIDEND_CONFIG
    as_of = as_of or datetime.now(CST).strftime("%Y-%m-%d")
    notes: List[str] = []

    index_map, n1 = _load_index_constituents(cfg)
    notes.extend(n1)

    if use_events_pit and EVENTS_PATH.exists():
        auto_map, n2 = _autonomous_from_events(cfg, as_of=as_of, min_years=min_years)
        notes.extend(n2)
    else:
        auto_map, n2 = _autonomous_from_fhps(cfg)
        notes.extend(n2)
        notes.append("use_events_pit=false_or_events_missing")

    non_st = _load_non_st_universe()
    notes.append(f"non_st_universe_n={len(non_st)}_path={UNIVERSE_NON_ST}")

    name_by: Dict[str, str] = {c: v["name"] for c, v in auto_map.items()}
    if CONT5_CSV.exists():
        c5 = pd.read_csv(CONT5_CSV, dtype=str)
        for _, r in c5.iterrows():
            name_by.setdefault(_z6(r["股票代码"]), str(r.get("股票简称", "")))
        notes.append(f"cont5_csv_n={len(c5)}")

    # Enrich names from events
    ev = _load_events()
    if ev is not None:
        for code, g in ev.groupby("code"):
            name_by.setdefault(code, str(g["name"].iloc[-1]))

    all_codes = set(index_map) | set(auto_map)
    members: List[UniverseMember] = []
    excluded_count = 0
    for code in sorted(all_codes):
        name = name_by.get(code, "")
        sources: List[str] = []
        if code in index_map:
            sources.append("INDEX")
        if code in auto_map:
            sources.append("AUTONOMOUS")
        years = list(auto_map.get(code, {}).get("cash_dividend_years", []))
        cont_n = int(auto_map.get(code, {}).get("continuous_cash_years", 0))
        excl_reason = _name_excluded(name, cfg.exclude_name_tokens)
        if excl_reason is None and non_st and code not in non_st:
            if "AUTONOMOUS" in sources and "INDEX" not in sources:
                excl_reason = "not_in_universe_listed_non_st"
        data_status = DataStatus.VALID.value
        if not years and "INDEX" in sources:
            data_status = DataStatus.DATA_INSUFFICIENT.value
        excluded = excl_reason is not None
        if excluded:
            excluded_count += 1
        members.append(
            UniverseMember(
                symbol=code,
                name=name,
                sources=sources,
                index_codes=sorted(index_map.get(code, [])),
                cash_dividend_years=years,
                continuous_cash_years=cont_n,
                excluded=excluded,
                exclude_reason=excl_reason,
                data_status=data_status,
            )
        )

    active = [m for m in members if not m.excluded]
    payload = {
        "strategy": "DIVIDEND_TREND",
        "phase": "2-universe",
        "as_of": as_of,
        "timezone": "Asia/Shanghai",
        "universe_mode": UNIVERSE_MODE,
        "config_note": "待回测校准",
        "counts": {
            "raw_union": len(members),
            "active": len(active),
            "excluded": excluded_count,
            "from_index": sum(1 for m in active if "INDEX" in m.sources),
            "from_autonomous": sum(1 for m in active if "AUTONOMOUS" in m.sources),
            "from_both": sum(
                1 for m in active if "INDEX" in m.sources and "AUTONOMOUS" in m.sources
            ),
        },
        "notes": notes,
        "gaps": [
            "No point-in-time historical index constituents — latest ∪ fhps/events (proxy_no_hist_index_cons)",
            "Historical ST membership timeline not in meta — name+current non_st only",
        ],
        "members": [m.to_dict() for m in active],
        "excluded_sample": [m.to_dict() for m in members if m.excluded][:30],
    }
    return payload


def write_universe_latest(
    cfg: Optional[DividendConfig] = None,
    out_path: Optional[Path] = None,
) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # latest: if events exist use PIT as_of today with min_years=5 when possible
    cfg = cfg or DIVIDEND_CONFIG
    min_years = 5 if EVENTS_PATH.exists() else None
    payload = build_universe(cfg, min_years=min_years)
    out_path = out_path or (OUT_DIR / "universe_latest.json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    sample_path = OUT_DIR / "universe_latest_sample.json"
    sample = dict(payload)
    sample["members"] = payload["members"][:40]
    sample["members_truncated"] = True
    sample_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def write_universe_asof_samples(
    dates: Optional[List[str]] = None,
    *,
    min_years: int = 3,
    cfg: Optional[DividendConfig] = None,
) -> Path:
    """Write out/universe_asof_sample.json for 2–3 historical T."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dates = dates or ["2019-12-31", "2021-12-31", "2023-12-31"]
    cfg = cfg or DIVIDEND_CONFIG
    samples = []
    for d in dates:
        payload = build_universe(cfg, as_of=d, min_years=min_years, use_events_pit=True)
        slim = {
            "as_of": payload["as_of"],
            "universe_mode": payload["universe_mode"],
            "counts": payload["counts"],
            "notes": payload["notes"],
            "members_head": payload["members"][:25],
            "members_truncated": True,
            "member_total_active": payload["counts"]["active"],
        }
        samples.append(slim)
        # also write full per-date file
        full_path = OUT_DIR / f"universe_asof_{d.replace('-', '')}.json"
        full_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    out = {
        "strategy": "DIVIDEND_TREND",
        "phase": "2-asof-sample",
        "generated_at": datetime.now(CST).isoformat(),
        "universe_mode": UNIVERSE_MODE,
        "min_years": min_years,
        "dates": dates,
        "samples": samples,
    }
    out_path = OUT_DIR / "universe_asof_sample.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


class DividendUniverseEngine:
    """Thin OOP wrapper."""

    def __init__(self, cfg: Optional[DividendConfig] = None):
        self.cfg = cfg or DIVIDEND_CONFIG

    def build(self, as_of: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        return build_universe(self.cfg, as_of=as_of, **kwargs)

    def asof(self, T: str, min_years: int = 3) -> Dict[str, Any]:
        return build_universe(self.cfg, as_of=T, min_years=min_years, use_events_pit=True)

    def write_latest(self, out_path: Optional[Path] = None) -> Path:
        return write_universe_latest(self.cfg, out_path=out_path)

    def write_asof_samples(self, dates: Optional[List[str]] = None, min_years: int = 3) -> Path:
        return write_universe_asof_samples(dates, min_years=min_years, cfg=self.cfg)


if __name__ == "__main__":
    p = write_universe_latest()
    print("wrote", p)
    if EVENTS_PATH.exists():
        p2 = write_universe_asof_samples()
        print("wrote", p2)
