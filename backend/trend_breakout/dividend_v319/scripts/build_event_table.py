#!/usr/bin/env python3
"""Build normalized dividend_events.parquet from local fhps + akshare backfill."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import sys
_TB = Path(__file__).resolve().parents[2]
if str(_TB) not in sys.path:
    sys.path.insert(0, str(_TB))
from dividend_v319.config.dividendConfig import CACHE_DIR, DATA_DIR, DIV_ROOT, FHPS_ALL

CACHE = CACHE_DIR
DATA = DATA_DIR
FHPS_LOCAL = FHPS_ALL
CST = ZoneInfo("Asia/Shanghai")

SPECIAL_RE = re.compile(r"特别|专项|一次性|额外分红|特别分红")


def _z6(x) -> str:
    return str(x).strip().zfill(6)


def _to_date(s):
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return pd.NaT
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return pd.NaT
    return ts.normalize()


def _pit_disclose(plan, notice, publish) -> pd.Timestamp:
    """Earliest available among plan_notice / notice / publish as disclosed-as-of date."""
    cands = [d for d in (plan, notice, publish) if pd.notna(d)]
    if not cands:
        return pd.NaT
    return min(cands)


def _special_flag(profile: str, progress: str) -> object:
    """Infer special dividend; null if cannot tell confidently from text."""
    text = f"{profile or ''} {progress or ''}"
    if not str(profile or "").strip():
        return None
    if SPECIAL_RE.search(text):
        return True
    # Chinese normal schemes usually like 10派X元 — treat as not-special only when
    # profile looks like a standard cash scheme without special keywords.
    if re.search(r"10派|每10股派|派息", text) and not SPECIAL_RE.search(text):
        return False
    return None


def from_local_fhps(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    out = pd.DataFrame(
        {
            "code": df["SECURITY_CODE"].map(_z6),
            "name": df["SECURITY_NAME_ABBR"].astype(str),
            "plan_notice_date": df["PLAN_NOTICE_DATE"].map(_to_date),
            "notice_date": df["NOTICE_DATE"].map(_to_date),
            "publish_date": df["PUBLISH_DATE"].map(_to_date),
            "ex_dividend_date": df["EX_DIVIDEND_DATE"].map(_to_date),
            "equity_record_date": df["EQUITY_RECORD_DATE"].map(_to_date),
            "pretax_bonus_rmb": pd.to_numeric(df["PRETAX_BONUS_RMB"], errors="coerce"),
            "dividend_ratio": pd.to_numeric(df.get("DIVIDENT_RATIO"), errors="coerce"),
            "assign_progress": df["ASSIGN_PROGRESS"].astype(str),
            "impl_plan_profile": df.get("IMPL_PLAN_PROFILE", pd.Series([""] * len(df))).astype(str),
            "report_period": df["报告期"].astype(str),
            "source": "local_fhps_em_rpt_sharebonus_det",
        }
    )
    return out


def from_ak_cache(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    # Expect Chinese column names from stock_fhps_em; tolerate merged with _report_period
    colmap = {
        "代码": "code",
        "名称": "name",
        "现金分红-现金分红比例": "pretax_bonus_rmb",
        "现金分红-股息率": "dividend_ratio",
        "预案公告日": "plan_notice_date",
        "最新公告日期": "notice_date",
        "股权登记日": "equity_record_date",
        "除权除息日": "ex_dividend_date",
        "方案进度": "assign_progress",
    }
    missing = [c for c in ("代码", "现金分红-现金分红比例") if c not in df.columns]
    if missing:
        raise ValueError(f"ak cache missing cols {missing} in {path}")
    out = pd.DataFrame(
        {
            "code": df["代码"].map(_z6),
            "name": df["名称"].astype(str) if "名称" in df.columns else "",
            "plan_notice_date": df["预案公告日"].map(_to_date) if "预案公告日" in df.columns else pd.NaT,
            "notice_date": df["最新公告日期"].map(_to_date) if "最新公告日期" in df.columns else pd.NaT,
            "publish_date": pd.NaT,  # ak Chinese schema has no separate publish; use notice/plan
            "ex_dividend_date": df["除权除息日"].map(_to_date) if "除权除息日" in df.columns else pd.NaT,
            "equity_record_date": df["股权登记日"].map(_to_date) if "股权登记日" in df.columns else pd.NaT,
            "pretax_bonus_rmb": pd.to_numeric(df["现金分红-现金分红比例"], errors="coerce"),
            "dividend_ratio": pd.to_numeric(df["现金分红-股息率"], errors="coerce")
            if "现金分红-股息率" in df.columns
            else np.nan,
            "assign_progress": df["方案进度"].astype(str) if "方案进度" in df.columns else "",
            "impl_plan_profile": "",  # not in ak Chinese table
            "report_period": df["_report_period"].astype(str)
            if "_report_period" in df.columns
            else "",
            "source": "ak.stock_fhps_em",
        }
    )
    # If publish missing, leave NaT; PIT will use plan/notice
    return out


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cash_per_share"] = df["pretax_bonus_rmb"] / 10.0
    df["is_special_flag"] = [
        _special_flag(p, a) for p, a in zip(df["impl_plan_profile"], df["assign_progress"])
    ]
    df["disclose_date"] = [
        _pit_disclose(a, b, c)
        for a, b, c in zip(df["plan_notice_date"], df["notice_date"], df["publish_date"])
    ]
    # Keep useful extras but required schema first
    ordered = [
        "code",
        "name",
        "plan_notice_date",
        "notice_date",
        "publish_date",
        "ex_dividend_date",
        "equity_record_date",
        "pretax_bonus_rmb",
        "dividend_ratio",
        "assign_progress",
        "is_special_flag",
        "cash_per_share",
        "source",
        "disclose_date",
        "report_period",
        "impl_plan_profile",
    ]
    return df[ordered]


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Prefer local source over ak when same code+report_period+ex_date+cash."""
    df = df.copy()
    df["_src_rank"] = df["source"].map(
        {"local_fhps_em_rpt_sharebonus_det": 0, "ak.stock_fhps_em": 1}
    ).fillna(9)
    df["_ex"] = df["ex_dividend_date"].astype(str)
    df["_rp"] = df["report_period"].astype(str)
    df["_cash"] = df["pretax_bonus_rmb"].round(6)
    df = df.sort_values(["code", "_rp", "_ex", "_cash", "_src_rank"])
    df = df.drop_duplicates(subset=["code", "_rp", "_ex", "_cash"], keep="first")
    return df.drop(columns=["_src_rank", "_ex", "_rp", "_cash"])


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    frames = []
    notes = []
    if FHPS_LOCAL.exists():
        loc = from_local_fhps(FHPS_LOCAL)
        frames.append(loc)
        notes.append({"source": "local", "path": str(FHPS_LOCAL), "rows": len(loc)})
    else:
        notes.append({"source": "local", "error": "missing"})

    ak_all = CACHE / "fhps_em_2015_2020_all.parquet"
    if ak_all.exists():
        akdf = from_ak_cache(ak_all)
        frames.append(akdf)
        notes.append({"source": "ak_merged", "path": str(ak_all), "rows": len(akdf)})
    else:
        # fall back to individual cache files
        n_files = 0
        rows = 0
        for p in sorted(CACHE.glob("fhps_em_20*.parquet")):
            if "2021" in p.name or "sample" in p.name or "head" in p.name:
                # skip partial samples / future — local covers 2021+
                if p.name.startswith("fhps_em_2021") or p.name.startswith("fhps_em_2022") or p.name.startswith("fhps_em_2023") or p.name.startswith("fhps_em_2024") or p.name.startswith("fhps_em_2025"):
                    continue
            if "sample" in p.name or "head" in p.name:
                continue
            # only 2015-2020
            m = re.search(r"fhps_em_(20\d{6})", p.name)
            if not m:
                continue
            y = int(m.group(1)[:4])
            if y < 2015 or y > 2020:
                continue
            part = pd.read_parquet(p)
            if "_report_period" not in part.columns:
                part = part.assign(_report_period=m.group(1))
            part_n = from_ak_cache(
                # write temp view via DataFrame path workaround
                p
            )
            # fix report_period if builder used file without column
            if part_n["report_period"].eq("").all():
                part_n["report_period"] = m.group(1)
            frames.append(part_n)
            n_files += 1
            rows += len(part_n)
        notes.append({"source": "ak_files", "files": n_files, "rows": rows})

    if not frames:
        raise SystemExit("no dividend source frames")

    raw = pd.concat(frames, ignore_index=True)
    norm = normalize(raw)
    ded = dedupe(norm)

    # Sort
    ded = ded.sort_values(["code", "disclose_date", "ex_dividend_date", "report_period"]).reset_index(drop=True)

    out_pq = DATA / "dividend_events.parquet"
    out_csv = DATA / "dividend_events_sample.csv"
    ded.to_parquet(out_pq, index=False)
    ded.head(200).to_csv(out_csv, index=False)

    # year coverage by report_period and by disclose year
    rp_years = (
        ded["report_period"].astype(str).str[:4].value_counts().sort_index().to_dict()
    )
    disc_years = (
        ded["disclose_date"].dropna().dt.year.astype(int).value_counts().sort_index().to_dict()
    )
    special_vc = ded["is_special_flag"].value_counts(dropna=False).to_dict()
    special_vc = {str(k): int(v) for k, v in special_vc.items()}

    meta = {
        "generated_at": datetime.now(CST).isoformat(),
        "rows": int(len(ded)),
        "raw_rows_before_dedupe": int(len(norm)),
        "codes": int(ded["code"].nunique()),
        "report_period_year_counts": {str(k): int(v) for k, v in rp_years.items()},
        "disclose_year_counts": {str(k): int(v) for k, v in disc_years.items()},
        "assign_progress_top": ded["assign_progress"].value_counts().head(10).to_dict(),
        "is_special_flag_counts": special_vc,
        "pit_rule": "disclose_date = min(plan_notice_date, notice_date, publish_date) among non-null; as-of filter uses disclose_date <= T",
        "cash_per_share_rule": "pretax_bonus_rmb / 10  (东财 10派X)",
        "is_special_flag_rule": "True if IMPL_PLAN_PROFILE/progress matches 特别|专项|一次性|额外分红|特别分红; False if looks like standard 10派 scheme without those words; else null (ak backfill often null because no profile text)",
        "sources": notes,
        "paths": {"parquet": str(out_pq), "csv_sample": str(out_csv)},
        "date_min_disclose": str(ded["disclose_date"].min()),
        "date_max_disclose": str(ded["disclose_date"].max()),
        "date_min_ex": str(ded["ex_dividend_date"].min()),
        "date_max_ex": str(ded["ex_dividend_date"].max()),
    }
    (DATA / "dividend_events_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({k: meta[k] for k in ("rows", "codes", "report_period_year_counts", "paths")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
