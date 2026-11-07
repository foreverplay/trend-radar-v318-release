#!/usr/bin/env python3
"""
Trend Confirmation Breakout Radar V2.3 — multi-period resonance on V2.2.

Primary daily close routine path. Uses data_sources for spot/hist.
Efficient prefilter → hist → V2.0 structure → V2.1 Fund/Sector/Risk → V2.2 Continuity
→ V2.3 Monthly/SectorTrend/RecommendationScore → report.

Usage:
  python v2_staircase_scan.py --t-date 2026-09-21
  python v2_3_scan.py --t-date 2026-09-21

Outputs: report_v2.md, candidates_v2.json (+ optional report_v2_3.md / candidates_v2_3.json)
No buy advice. Never invent prices. No TotalScore sum. No ZhuangStock.
RecommendationScore = 规则优先观察强度 (not return probability).
"""
from __future__ import annotations

import argparse
import json
import re
import time
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import data_sources as ds
from v2_structure import (
    PHASE_CN,
    analyze_hist,
    apply_regime_gates,
    map_regime_v2,
)
from v2_fundamental import FundamentalEngine
from v2_sector import SectorEngine
from v2_risk import RiskEngine
from v2_classify import classify_v21, classify_v22
from v2_continuity import ContinuityEngine
from v2_monthly import StockMonthlyTrendEngine
from v2_sector_trend import SectorTrendEngine
from v2_recommend import (
    RecommendationEngine,
    passes_recommendation_hard_filter,
    sort_recommendation_key,
)

warnings.filterwarnings("ignore")

try:
    from paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY
except ImportError:  # packaged / flat layout fallback
    try:
        from trend_breakout.paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY
    except ImportError:
        import os as __os

        _PKG_ROOT = Path(__os.environ.get("TREND_BREAKOUT_ROOT", str(Path(__file__).resolve().parent)))
        _k = Path(__os.environ.get("ASHARE_KLINE_ROOT", str(_PKG_ROOT.parent.parent / "data" / "sample" / "kline")))
        _PKG_LOCAL_DAILY = _k / "raw" / "daily"

OUT = _PKG_ROOT
TZ = ZoneInfo("Asia/Shanghai")
PY = "python"
LOCAL_DAILY = _PKG_LOCAL_DAILY
def _code_full(code: str) -> str:
    c = str(code)
    if c.startswith(("sh", "sz")):
        return c
    c6 = c.zfill(6)
    return ("sh" if c6.startswith(("5", "6", "9")) else "sz") + c6


def _is_st_or_new(name: str) -> bool:
    n = str(name or "")
    return ("ST" in n) or ("退" in n) or n.startswith(("N", "C"))


def compute_market_meta(
    t_date: str, spot: Optional[pd.DataFrame], strict_asof: bool = False
) -> Dict[str, Any]:
    data_gaps: List[str] = []
    if strict_asof:
        data_gaps.append("strict_asof: live spot/legu/fund disabled")
        if spot is None or not len(spot):
            br = {"ok": False}
        else:
            pct = pd.to_numeric(spot["涨跌幅"], errors="coerce")
            zt_n0, dt_n0 = ds._count_limit_from_spot(spot)
            br = {
                "ok": True,
                "value": {
                    "up": float((pct > 0).sum()),
                    "down": float((pct < 0).sum()),
                    "zt": float(zt_n0),
                    "dt": float(dt_n0),
                    "real_zt": float(zt_n0),
                    "real_dt": float(dt_n0),
                },
            }
    else:
        br = ds.fetch_breadth_zt_dt(t_date, spot=spot)
    if br["ok"]:
        bv = br["value"]
        up_n = float(bv["up"])
        down_n = float(bv["down"])
        zt_n = float(bv.get("real_zt", bv["zt"]))
        dt_n = float(bv.get("real_dt", bv["dt"]))
    else:
        up_n = down_n = zt_n = dt_n = 0.0
        data_gaps.append("breadth failed")
    up_share = up_n / max(up_n + down_n, 1)

    idx_map = {
        "上证指数": "sh000001",
        "沪深300": "sh000300",
        "中证1000": "sh000852",
        "创业板指": "sz399006",
    }
    idx_res = {"ok": False} if strict_asof else ds.fetch_sina_index_spot(list(idx_map.values()))
    idx_spot = idx_res["value"] if idx_res.get("ok") else None
    index_info: Dict[str, Any] = {}
    for name, code in idx_map.items():
        info: Dict[str, Any] = {"code": code, "name": name}
        if idx_spot is not None:
            m = idx_spot[idx_spot["代码"] == code]
            if len(m):
                info["close_T"] = float(m.iloc[0]["最新价"])
                info["pct_T"] = float(m.iloc[0]["涨跌幅"])
                info["preclose"] = float(m.iloc[0]["昨收"])
        if strict_asof:
            hist = ds.load_local_index_hist(code, t_date)
            if hist is None:
                data_gaps.append(f"strict index missing {code}")
        else:
            ih = ds.fetch_index_hist(code)
            hist = ih["value"] if ih["ok"] else None
        if hist is not None and len(hist):
            hist = hist.copy()
            hist["date"] = pd.to_datetime(hist["date"])
            hist = hist.sort_values("date").reset_index(drop=True)
            last_d = hist["date"].iloc[-1].strftime("%Y-%m-%d")
            if strict_asof and last_d != t_date:
                data_gaps.append(f"strict index stale {code} last={last_d}")
                index_info[name] = info
                continue
            if (not strict_asof) and last_d < t_date and "close_T" in info:
                hist = pd.concat(
                    [
                        hist,
                        pd.DataFrame(
                            [
                                {
                                    "date": pd.Timestamp(t_date),
                                    "open": np.nan,
                                    "high": np.nan,
                                    "low": np.nan,
                                    "close": info["close_T"],
                                    "volume": np.nan,
                                    "amount": np.nan,
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
            info["close"] = float(hist["close"].iloc[-1])
            info["ma20"] = float(hist["close"].tail(20).mean())
            info["above_ma20"] = info["close"] > info["ma20"]
            if "pct_T" not in info and len(hist) >= 2:
                info["pct_T"] = (hist["close"].iloc[-1] / hist["close"].iloc[-2] - 1) * 100
            if len(hist) >= 21:
                info["ret20"] = float(hist["close"].iloc[-1] / hist["close"].iloc[-21] - 1)
            if len(hist) >= 61:
                info["ret60"] = float(hist["close"].iloc[-1] / hist["close"].iloc[-61] - 1)
        index_info[name] = info

    if strict_asof:
        local_to = ds.turnover_from_local(t_date, spot)
        to = local_to
        ma = local_to
    else:
        to = ds.fetch_turnover_t(t_date, spot=spot)
        two_market = float(to["value"]["amt_yi"]) if to["ok"] else None
        hist_series = (to.get("value") or {}).get("hist_series") if to["ok"] else None
        ma = ds.fetch_turnover_ma(t_date, amt_t=two_market, hist_series=hist_series)
    if ma["ok"]:
        mv = ma["value"]
        amt_T = float(mv["amt_yi"])
        ma5 = float(mv["ma5"])
        ma20 = float(mv["ma20"])
        vs5 = float(mv["vs_ma5"] or 1)
        vs20 = float(mv["vs_ma20"] or 1)
    else:
        amt_T = float((to.get("value") or {}).get("amt_yi") or 0) if to.get("ok") else 0.0
        ma5 = ma20 = amt_T or 1.0
        vs5 = vs20 = 1.0
        data_gaps.append("turnover MA failed")

    idx_pcts = [index_info[n].get("pct_T", 0) or 0 for n in idx_map if n in index_info]
    avg_idx_pct = float(np.mean(idx_pcts)) if idx_pcts else 0.0
    n_idx_up = sum(1 for p in idx_pcts if p > 0)
    n_above = sum(1 for n in idx_map if index_info.get(n, {}).get("above_ma20"))

    ms = 50.0
    ms += float(np.clip(avg_idx_pct * 8, -15, 15))
    ms += (n_idx_up - 2) * 4
    ms += (n_above - 2) * 5
    if up_share >= 0.65:
        ms += 12
    elif up_share >= 0.55:
        ms += 6
    elif up_share >= 0.45:
        ms += 0
    elif up_share >= 0.35:
        ms -= 8
    else:
        ms -= 15
    zt_dt = zt_n / max(dt_n, 1)
    if zt_n >= 80 and zt_dt >= 5:
        ms += 10
    elif zt_n >= 50 and zt_dt >= 3:
        ms += 5
    elif zt_n < 20 or zt_dt < 1:
        ms -= 8
    if vs5 >= 1.15 and vs20 >= 1.0:
        ms += 8
    elif vs5 >= 0.95:
        ms += 2
    elif vs5 < 0.8:
        ms -= 8
    else:
        ms -= 3
    ms = float(np.clip(ms, 0, 100))

    regime_v2 = map_regime_v2(ms, up_share, avg_idx_pct, vs5)
    # legacy R-map for near-pool caps
    if regime_v2 == "RISK_OFF":
        regime_legacy = "R0"
    elif regime_v2 == "WEAK":
        regime_legacy = "R1"
    elif ms < 60:
        regime_legacy = "R2"
    elif ms < 75:
        regime_legacy = "R3"
    else:
        regime_legacy = "R4"
    caps = {"R0": 0, "R1": 5, "R2": 10, "R3": 15, "R4": 20}
    cap = caps[regime_legacy]

    conclusion = [
        "四指数涨跌幅: "
        + ", ".join(
            f"{n} {index_info[n].get('pct_T', float('nan')):+.2f}%"
            for n in idx_map
            if n in index_info
        ),
        f"涨跌家数 {int(up_n)}/{int(down_n)} (上涨占比 {up_share*100:.1f}%)",
        f"涨停/跌停 {int(zt_n)}/{int(dt_n)}",
        f"两市成交额约 {amt_T:.0f}亿, vsMA5={vs5:.2f}, vsMA20={vs20:.2f}",
        f"指数站上MA20家数 {n_above}/4",
        f"V2 MarketRegime={regime_v2} (legacy {regime_legacy})",
    ]
    return {
        "t_date": t_date,
        "regime": regime_legacy,
        "regime_v2": regime_v2,
        "market_score": round(ms, 1),
        "cap": cap,
        "breadth": {
            "up": int(up_n),
            "down": int(down_n),
            "zt": int(zt_n),
            "dt": int(dt_n),
            "up_share": round(up_share, 4),
        },
        "turnover": {
            "amt_yi": round(amt_T, 2),
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "vs_ma5": round(vs5, 3),
            "vs_ma20": round(vs20, 3),
            "source": to.get("source"),
            "ma_source": ma.get("source"),
        },
        "indices": {
            n: {
                "pct": index_info[n].get("pct_T"),
                "close": index_info[n].get("close"),
                "ma20": index_info[n].get("ma20"),
                "above_ma20": index_info[n].get("above_ma20"),
            }
            for n in idx_map
            if n in index_info
        },
        "hs300_ret20": index_info.get("沪深300", {}).get("ret20") or 0.0,
        "hs300_ret60": index_info.get("沪深300", {}).get("ret60") or 0.0,
        "data_gaps": data_gaps,
        "market_conclusion": conclusion,
        "model": "V2_staircase",
    }


def prefilter_spot(spot: pd.DataFrame, max_n: int = 120) -> pd.DataFrame:
    df = spot.copy()
    df = df[~df["名称"].astype(str).map(_is_st_or_new)]
    df = df[df["最新价"].notna() & (df["最新价"] > 0)]
    # halt-ish: zero volume / amount
    if "成交额" in df.columns:
        df = df[df["成交额"].fillna(0) >= 3e7]
    if "成交量" in df.columns:
        df = df[df["成交量"].fillna(0) > 0]
    # not max daily gainers only — allow modest green / consolidating near highs
    df = df[df["涨跌幅"].fillna(0) > -5]
    df["near_high"] = (df["最新价"] / df["最高"]).fillna(0)
    df["pre_score"] = (
        df["涨跌幅"].clip(-5, 12) * 1.5
        + np.log10(df["成交额"].clip(1e4)) * 4
        + df["near_high"] * 8
    )
    # prefer not one-word limit-up spam
    def is_lu(row):
        code = str(row["代码"])
        pct = row["涨跌幅"]
        if pd.isna(pct):
            return False
        if code.startswith(("sz300", "sz301", "sh688")):
            return pct >= 19.5
        return pct >= 9.7

    df["limit_up"] = df.apply(is_lu, axis=1)
    non_lu = df[~df["limit_up"]].nlargest(int(max_n * 0.85), "pre_score")
    lu = df[df["limit_up"]].nlargest(int(max_n * 0.15), "成交额")
    cand = pd.concat([non_lu, lu]).drop_duplicates("代码")
    return cand


def _local_prefilter_boost_db(cand: pd.DataFrame, t_date: str, extra: int) -> pd.DataFrame:
    """Same MA / liquidity gates as the parquet scan, fed by the market DB window."""
    import rank_db

    codes = set(cand["代码"].astype(str))
    frame = rank_db.recent_bars(t_date)
    extras: List[Dict[str, Any]] = []
    checked = 0
    if frame is None or frame.empty:
        print("  local_prefilter db empty", flush=True)
        return cand
    for code6, part in frame.groupby("symbol", sort=False):
        code6 = str(code6)
        if not code6.isdigit() or len(code6) != 6:
            continue
        full = _code_full(code6)
        if full in codes:
            continue
        checked += 1
        try:
            df = part.sort_values("date").tail(80)
            if len(df) < 70:
                continue
            amt20 = float(df["amount"].tail(20).mean())
            if amt20 < 3e7:
                continue
            c = df["close"].astype(float)
            ma20 = c.rolling(20).mean()
            ma60 = c.rolling(60).mean()
            if len(c) < 60:
                continue
            if not np.isfinite(ma20.iloc[-1]) or not np.isfinite(ma60.iloc[-1]):
                continue
            s20 = float(ma20.iloc[-1] / ma20.iloc[-11] - 1) * 100
            if s20 <= 1.0:
                continue
            if float(ma20.iloc[-1]) < float(ma60.iloc[-1]) * 0.98:
                continue
            if float(c.iloc[-1]) < float(ma20.iloc[-1]):
                continue
            extras.append(
                {
                    "代码": full,
                    "名称": code6,
                    "最新价": float(c.iloc[-1]),
                    "涨跌幅": float(c.iloc[-1] / c.iloc[-2] - 1) * 100,
                    "成交额": float(df["amount"].iloc[-1]),
                    "最高": float(c.iloc[-1]),
                    "最低": float(c.iloc[-1]),
                    "今开": float(c.iloc[-1]),
                    "昨收": float(c.iloc[-2]),
                    "成交量": np.nan,
                    "pre_score": 50 + s20,
                    "limit_up": False,
                    "from_local_boost": True,
                }
            )
        except Exception:
            continue
        if checked % 1000 == 0:
            print(f"  local_prefilter db checked={checked} hits={len(extras)}", flush=True)
    print(f"  local_prefilter db done checked={checked} hits={len(extras)}", flush=True)
    if not extras:
        return cand
    ex = pd.DataFrame(extras).nlargest(extra, "pre_score")
    return pd.concat([cand, ex], ignore_index=True).drop_duplicates("代码")


def local_prefilter_boost(
    cand: pd.DataFrame, t_date: str, extra: int = 120, batch_size: int = 200
) -> pd.DataFrame:
    """
    Scan ALL local daily parquets under LOCAL_DAILY (no 800-file cap).
    Keep liquidity (avg20 amt≥3000万) and MA gates (MA20Slope>1%, MA20≥MA60*0.98, close≥MA20).
    Rank by pre_score and merge top `extra` into cand.
    """
    import os

    if os.environ.get("TREND_RANK_DB"):
        import rank_db

        if rank_db.db_path():
            return _local_prefilter_boost_db(cand, t_date, extra)
    codes = set(cand["代码"].astype(str))
    files = sorted(LOCAL_DAILY.glob("*.parquet"))
    extras: List[Dict[str, Any]] = []
    checked = 0
    # batch via sequential reads (parquet I/O bound; avoid early break)
    for fp in files:
        code6 = fp.stem
        if not code6.isdigit() or len(code6) != 6:
            continue
        full = _code_full(code6)
        if full in codes:
            continue
        checked += 1
        try:
            df = pd.read_parquet(fp, columns=["date", "close", "amount"])
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] <= t_date].tail(80)
            if len(df) < 70:
                continue
            amt20 = float(df["amount"].tail(20).mean())
            if amt20 < 3e7:
                continue
            c = df["close"].astype(float)
            ma20 = c.rolling(20).mean()
            ma60 = c.rolling(60).mean()
            if len(c) < 60:
                continue
            if not np.isfinite(ma20.iloc[-1]) or not np.isfinite(ma60.iloc[-1]):
                continue
            s20 = float(ma20.iloc[-1] / ma20.iloc[-11] - 1) * 100
            if s20 <= 1.0:
                continue
            if float(ma20.iloc[-1]) < float(ma60.iloc[-1]) * 0.98:
                continue
            if float(c.iloc[-1]) < float(ma20.iloc[-1]):
                continue
            extras.append(
                {
                    "代码": full,
                    "名称": code6,
                    "最新价": float(c.iloc[-1]),
                    "涨跌幅": float(c.iloc[-1] / c.iloc[-2] - 1) * 100,
                    "成交额": float(df["amount"].iloc[-1]),
                    "最高": float(c.iloc[-1]),
                    "最低": float(c.iloc[-1]),
                    "今开": float(c.iloc[-1]),
                    "昨收": float(c.iloc[-2]),
                    "成交量": np.nan,
                    "pre_score": 50 + s20,
                    "limit_up": False,
                    "from_local_boost": True,
                }
            )
        except Exception:
            continue
        if checked % 1000 == 0:
            print(f"  local_prefilter checked={checked}/{len(files)} hits={len(extras)}")
    print(f"  local_prefilter done checked={checked} hits={len(extras)}")
    if not extras:
        return cand
    ex = pd.DataFrame(extras).nlargest(extra, "pre_score")
    out = pd.concat([cand, ex], ignore_index=True).drop_duplicates("代码")
    return out


def _normalize_force_code(raw: str) -> Optional[str]:
    s = str(raw or "").strip()
    if not s or s.startswith("#"):
        return None
    # allow 002011 / sz002011 / sh601579
    if s.startswith(("sh", "sz")) and len(s) >= 8:
        return s[:8]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 6:
        return _code_full(digits[-6:])
    return None


def load_force_codes(
    cli_codes: Optional[str] = None,
    force_file: Optional[Path] = None,
) -> List[str]:
    """
    Merge force codes from (priority high→low):
      1) --force-codes CLI (comma-separated)
      2) env V2_FORCE_CODES
      3) meta/force_codes.txt (default watchlist)
    Returns list of full codes like sz002011.
    """
    import os

    collected: List[str] = []
    seen = set()

    def add_one(raw: str):
        full = _normalize_force_code(raw)
        if full and full not in seen:
            seen.add(full)
            collected.append(full)

    if cli_codes:
        for part in str(cli_codes).replace(";", ",").split(","):
            add_one(part)
    env = os.environ.get("V2_FORCE_CODES") or os.environ.get("FORCE_CODES") or ""
    if env:
        for part in env.replace(";", ",").split(","):
            add_one(part)
    fp = force_file or (OUT / "meta" / "force_codes.txt")
    if fp.exists():
        for line in fp.read_text(encoding="utf-8").splitlines():
            add_one(line)
    return collected


def merge_force_codes(
    cand: pd.DataFrame,
    spot: pd.DataFrame,
    force_codes: List[str],
    t_date: str,
) -> pd.DataFrame:
    """Always merge force-codes into candidate set before hist (even if soft on spot)."""
    if not force_codes:
        return cand
    codes = set(cand["代码"].astype(str))
    name_map = dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))
    spot_by = {str(r["代码"]): r for _, r in spot.iterrows()}
    rows = []
    for full in force_codes:
        if full in codes:
            continue
        code6 = full[2:] if full.startswith(("sh", "sz")) else full.zfill(6)
        if full in spot_by:
            r = spot_by[full]
            rows.append(
                {
                    "代码": full,
                    "名称": r.get("名称", code6),
                    "最新价": float(r.get("最新价") or 0) or np.nan,
                    "涨跌幅": float(r.get("涨跌幅") or 0) if pd.notna(r.get("涨跌幅")) else 0.0,
                    "成交额": float(r.get("成交额") or 0) if pd.notna(r.get("成交额")) else 0.0,
                    "最高": float(r.get("最高") or r.get("最新价") or 0) or np.nan,
                    "最低": float(r.get("最低") or r.get("最新价") or 0) or np.nan,
                    "今开": float(r.get("今开") or r.get("最新价") or 0) or np.nan,
                    "昨收": float(r.get("昨收") or 0) or np.nan,
                    "成交量": float(r.get("成交量") or 0) if pd.notna(r.get("成交量")) else np.nan,
                    "pre_score": 999.0,
                    "limit_up": False,
                    "from_force": True,
                }
            )
        else:
            # try local parquet for a stub row
            fp = LOCAL_DAILY / f"{code6}.parquet"
            if fp.exists():
                try:
                    df = pd.read_parquet(fp, columns=["date", "close", "amount"])
                    df["date"] = pd.to_datetime(df["date"])
                    df = df[df["date"] <= t_date]
                    if len(df):
                        c = float(df["close"].iloc[-1])
                        rows.append(
                            {
                                "代码": full,
                                "名称": name_map.get(full, code6),
                                "最新价": c,
                                "涨跌幅": float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100
                                if len(df) >= 2
                                else 0.0,
                                "成交额": float(df["amount"].iloc[-1]),
                                "最高": c,
                                "最低": c,
                                "今开": c,
                                "昨收": float(df["close"].iloc[-2]) if len(df) >= 2 else c,
                                "成交量": np.nan,
                                "pre_score": 999.0,
                                "limit_up": False,
                                "from_force": True,
                            }
                        )
                        continue
                except Exception:
                    pass
            rows.append(
                {
                    "代码": full,
                    "名称": name_map.get(full, code6),
                    "最新价": np.nan,
                    "涨跌幅": 0.0,
                    "成交额": 0.0,
                    "最高": np.nan,
                    "最低": np.nan,
                    "今开": np.nan,
                    "昨收": np.nan,
                    "成交量": np.nan,
                    "pre_score": 999.0,
                    "limit_up": False,
                    "from_force": True,
                }
            )
    if not rows:
        print(f"force_codes already in cand: {force_codes}")
        return cand
    ex = pd.DataFrame(rows)
    print(f"force_codes merged: {[r['代码'] for r in rows]}")
    out = pd.concat([cand, ex], ignore_index=True).drop_duplicates("代码")
    return out


def ensure_hist_to_t(symbol: str, t_date: str, start: str = "20250101") -> Tuple[Optional[pd.DataFrame], str, List[str]]:
    """Fetch hist; if local stale vs T, try remote fill/merge."""
    end = t_date.replace("-", "")
    res = ds.fetch_stock_hist(symbol, start=start, end=end)
    errors = list(res.get("errors") or [])
    if not res["ok"]:
        return None, "", errors
    df = res["value"].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    last = df["date"].iloc[-1].strftime("%Y-%m-%d")
    src = res["source"]
    if last >= t_date:
        return df[df["date"] <= t_date].reset_index(drop=True), src, errors

    # local/remote lag: try eastmoney/sina explicitly
    try:
        import akshare as ak

        code6 = symbol[2:] if symbol.startswith(("sh", "sz")) else symbol.zfill(6)
        full = _code_full(symbol)
        try:
            rdf = ak.stock_zh_a_hist(
                symbol=code6, period="daily", start_date=start, end_date=end, adjust="qfq"
            )
            if rdf is not None and len(rdf):
                rdf = ds._normalize_hist(rdf)
                rdf = rdf[rdf["date"] <= t_date]
                if len(rdf) and rdf["date"].iloc[-1].strftime("%Y-%m-%d") >= last:
                    return rdf.reset_index(drop=True), "stock_zh_a_hist_fill", errors
        except Exception as e:
            errors.append(f"em_fill: {type(e).__name__}: {e}")
        try:
            rdf = ak.stock_zh_a_daily(
                symbol=full, start_date=start, end_date=end, adjust="qfq"
            )
            if rdf is not None and len(rdf):
                rdf = ds._normalize_hist(rdf)
                rdf = rdf[rdf["date"] <= t_date]
                if len(rdf):
                    return rdf.reset_index(drop=True), "stock_zh_a_daily_fill", errors
        except Exception as e:
            errors.append(f"sina_fill: {type(e).__name__}: {e}")
    except Exception as e:
        errors.append(f"ak_fill: {type(e).__name__}: {e}")

    return df, src + "_stale", errors


def fetch_one(
    full: str, t_date: str, spot_row, strict_asof: bool = False
) -> Tuple[str, Optional[pd.DataFrame], Optional[str], str]:
    if strict_asof:
        df = ds.load_local_stock_hist(full, t_date)
        if df is None or not len(df):
            return full, None, "strict local hist missing", "local_parquet"
        last = df["date"].iloc[-1].strftime("%Y-%m-%d")
        if last != t_date:
            return full, None, f"strict last={last}!={t_date}", "local_parquet"
        return full, df.reset_index(drop=True), None, "local_parquet"
    df, src, errs = ensure_hist_to_t(full, t_date)
    if df is None:
        return full, None, "; ".join(errs[:2]), ""
    df = ds.append_t_bar_from_spot(df, t_date, spot_row)
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"] <= t_date].sort_values("date").reset_index(drop=True)
    last = df["date"].iloc[-1].strftime("%Y-%m-%d") if len(df) else ""
    if last != t_date:
        return full, None, f"last={last}!={t_date}", src
    return full, df, None, src



def enrich_results_v21(
    results: List[Dict[str, Any]],
    hists: Dict[str, pd.DataFrame],
    t_date: str,
    strict_asof: bool = False,
) -> Dict[str, Any]:
    """Attach Fund/Sector/Risk + Continuity + V2.3 monthly/sector-trend/recommend + classify.

    Keeps all V2.2 fields intact; V2.3 ADDS layers.
    Alias: enrich_results_v23.
    """
    fund_eng = FundamentalEngine()
    sec_eng = SectorEngine()
    sec_eng.bind_fundamental(fund_eng)
    risk_eng = RiskEngine()
    cont_eng = ContinuityEngine()
    cont_eng.bind(fund_engine=fund_eng, sector_engine=sec_eng)
    monthly_eng = StockMonthlyTrendEngine()
    sector_trend_eng = SectorTrendEngine(sector_engine=sec_eng)
    sector_trend_eng.bind_fundamental(fund_eng)
    rec_eng = RecommendationEngine()
    try:
        if not strict_asof:
            fund_eng.load()
        sec_eng.load()
        sector_trend_eng.load()
    except Exception as e:
        print("v21/v23 engine load warn:", type(e).__name__, e)

    stats = {
        "fund_ok": 0,
        "fund_insufficient": 0,
        "sector_ok": 0,
        "sector_insufficient": 0,
        "continuity_ok": 0,
        "continuity_partial": 0,
        "monthly_ok": 0,
        "monthly_insufficient": 0,
        "sector_trend_ok": 0,
        "sector_trend_insufficient": 0,
        "recommend_ok": 0,
        "recommend_hard_filtered": 0,
        "recommend_data_insufficient": 0,
        "risk_high": 0,
        "risk_mid": 0,
        "risk_low": 0,
        "tag_counts": {},
        "type_counts": {},
        "rec_level_counts": {},
    }

    for rec in results:
        full = rec.get("code") or ""
        code6 = rec.get("code6") or full[2:]
        hist = hists.get(full)
        # fundamental
        try:
            if strict_asof:
                raise RuntimeError("strict_asof_fund_not_historical")
            fr = fund_eng.evaluate(code6)
            fund_d = fr.to_dict()
        except Exception as e:
            fund_d = {
                "ok": False,
                "data_insufficient": True,
                "score": None,
                "level": "DATA_INSUFFICIENT",
                "tags": ["DATA_INSUFFICIENT"],
                "metrics": {},
                "checklist": {"基本面": "⚠"},
                "notes": [f"fund_err:{type(e).__name__}"],
                "industry": None,
                "source": "error",
            }
        # sector (legacy SectorScore — keep)
        try:
            sr = sec_eng.evaluate(code6, hist=hist, t_date=t_date)
            sec_d = sr.to_dict()
        except Exception as e:
            sec_d = {
                "ok": False,
                "data_insufficient": True,
                "score": None,
                "industry": None,
                "tags": ["DATA_INSUFFICIENT"],
                "metrics": {},
                "checklist": {"板块": "⚠"},
                "notes": [f"sector_err:{type(e).__name__}"],
                "source": "error",
            }
        # risk (OHLCV)
        plat_low = None
        try:
            plat_low = (rec.get("metrics") or {}).get("platform_low")
        except Exception:
            plat_low = None
        try:
            rr = risk_eng.evaluate(
                hist if hist is not None else pd.DataFrame(),
                platform_low=plat_low,
                structure_risks=rec.get("risks") or [],
            )
            risk_d = rr.to_dict()
        except Exception as e:
            risk_d = {
                "risk_score": "MID",
                "tags": ["RISK_EVAL_ERR"],
                "metrics": {},
                "checklist": {"交易风险": "⚠"},
                "notes": [f"risk_err:{type(e).__name__}"],
                "broken": False,
            }

        # continuity (V2.2)
        try:
            cr = cont_eng.evaluate(
                code6,
                hist if hist is not None else pd.DataFrame(),
                t_date=t_date,
                fund=fund_d,
                sector=sec_d,
                phase=rec.get("phase"),
                persist=not strict_asof,
            )
            cont_d = cr.to_dict()
        except Exception as e:
            cont_d = {
                "ok": False,
                "continuity_score": None,
                "consumption_score": None,
                "continuity_type": "TYPE_D",
                "continuity_type_cn": "趋势衰减",
                "data_insufficient": True,
                "notes": [f"cont_err:{type(e).__name__}"],
                "favorable": [],
                "limiting": [],
                "tags": ["DATA_INSUFFICIENT"],
                "sector_continuity": None,
                "delta1": None,
                "delta3": None,
                "delta5": None,
                "delta_label": None,
            }

        rec["tech_score"] = rec.get("score")
        rec["fundamental"] = fund_d
        rec["sector"] = sec_d
        rec["risk"] = risk_d
        rec["continuity"] = cont_d
        rec["fund_score"] = fund_d.get("score")
        rec["sector_score"] = sec_d.get("score")
        rec["risk_level"] = risk_d.get("risk_score")
        rec["industry"] = sec_d.get("industry") or fund_d.get("industry")
        rec["continuity_score"] = cont_d.get("continuity_score")
        rec["consumption_score"] = cont_d.get("consumption_score")
        rec["continuity_type"] = cont_d.get("continuity_type")
        rec["continuity_type_cn"] = cont_d.get("continuity_type_cn")
        rec["continuity_delta1"] = cont_d.get("delta1")
        rec["continuity_delta3"] = cont_d.get("delta3")
        rec["continuity_delta5"] = cont_d.get("delta5")
        rec["continuity_delta_label"] = cont_d.get("delta_label")
        rec["sector_continuity"] = cont_d.get("sector_continuity")

        # ---- V2.3 Stock monthly ----
        try:
            mr = monthly_eng.evaluate(code6, hist=hist, t_date=t_date)
            monthly_d = mr.to_dict()
        except Exception as e:
            monthly_d = {
                "ok": False,
                "data_insufficient": True,
                "trend": "MONTHLY_NEUTRAL",
                "trend_cn": "中性",
                "score": None,
                "metrics": {},
                "tags": ["DATA_INSUFFICIENT"],
                "notes": [f"monthly_err:{type(e).__name__}"],
                "n_months": 0,
            }
        rec["stockMonthlyTrend"] = monthly_d
        rec["stock_monthly_trend"] = monthly_d.get("trend")
        rec["stock_monthly_score"] = monthly_d.get("score")

        # ---- V2.3 Sector trend (cached per industry) ----
        try:
            strr = sector_trend_eng.evaluate(
                code6, hist=hist, t_date=t_date, legacy_sector=sec_d
            )
            st_d = strr.to_dict()
        except Exception as e:
            st_d = {
                "ok": False,
                "data_insufficient": True,
                "name": rec.get("industry"),
                "dailyTrend": "NEUTRAL",
                "dailyTrendCn": "中性",
                "dailyTrendScore": None,
                "monthlyTrend": "NEUTRAL",
                "monthlyTrendCn": "中性",
                "monthlyTrendScore": None,
                "breadthScore": None,
                "relativeStrength20": None,
                "relativeStrengthBucket": "一般",
                "legacySectorScore": sec_d.get("score"),
                "peers_n": 0,
                "metrics": {},
                "tags": ["DATA_INSUFFICIENT"],
                "notes": [f"sector_trend_err:{type(e).__name__}"],
            }
        # V2.3 sector block shape
        rec["sectorTrend"] = {
            "name": st_d.get("name"),
            "dailyTrend": st_d.get("dailyTrend"),
            "dailyTrendCn": st_d.get("dailyTrendCn"),
            "dailyTrendScore": st_d.get("dailyTrendScore"),
            "monthlyTrend": st_d.get("monthlyTrend"),
            "monthlyTrendCn": st_d.get("monthlyTrendCn"),
            "monthlyTrendScore": st_d.get("monthlyTrendScore"),
            "breadthScore": st_d.get("breadthScore"),
            "relativeStrength20": st_d.get("relativeStrength20"),
            "relativeStrengthBucket": st_d.get("relativeStrengthBucket"),
            "legacySectorScore": st_d.get("legacySectorScore"),
            "peers_n": st_d.get("peers_n"),
            "tags": st_d.get("tags") or [],
            "notes": st_d.get("notes") or [],
            "metrics": st_d.get("metrics") or {},
            "ok": st_d.get("ok"),
            "data_insufficient": st_d.get("data_insufficient"),
        }

        # ---- V2.3 Recommendation ----
        try:
            rr = rec_eng.evaluate(rec)
            rec_d = rr.to_dict()
        except Exception as e:
            rec_d = {
                "ok": False,
                "score": None,
                "level": "D",
                "label": "规则优先观察强度",
                "resonanceTags": [],
                "reason": [],
                "risk": [f"recommend_err:{type(e).__name__}"],
                "checklist": {},
                "hard_filtered": True,
                "hard_filter_reasons": ["EVAL_ERR"],
                "notes": [f"recommend_err:{type(e).__name__}"],
            }
        rec["recommendation"] = {
            "score": rec_d.get("score"),
            "level": rec_d.get("level"),
            "label": rec_d.get("label") or "规则优先观察强度",
            "resonanceTags": rec_d.get("resonanceTags") or [],
            "reason": rec_d.get("reason") or [],
            "risk": rec_d.get("risk") or [],
            "checklist": rec_d.get("checklist") or {},
            "hard_filtered": bool(rec_d.get("hard_filtered")),
            "hard_filter_reasons": rec_d.get("hard_filter_reasons") or [],
            "base_score": rec_d.get("base_score"),
            "resonance_bonus": rec_d.get("resonance_bonus"),
            "consumption_penalty": rec_d.get("consumption_penalty"),
            "notes": rec_d.get("notes") or [],
            "weights_used": rec_d.get("weights_used") or {},
            "components": rec_d.get("components") or {},
        }
        rec["recommendation_score"] = rec_d.get("score")
        rec["recommendation_level"] = rec_d.get("level")

        clf = classify_v22(rec, fund_d, sec_d, risk_d, cont_d)
        rec["v21_tag"] = clf["tag"]
        rec["v22_tag"] = clf["tag"]
        rec["v21_tag_cn"] = clf["tag_cn"]
        rec["v22_tag_cn"] = clf["tag_cn"]
        rec["v21_why"] = clf["why"]
        rec["v21_notes"] = clf.get("notes") or []
        rec["v21_checklist"] = clf.get("checklist") or {}
        rec["tag"] = clf["tag"]
        rec["tag_cn"] = clf["tag_cn"]
        rec["tag_aliases"] = clf.get("tag_aliases") or []

        if fund_d.get("data_insufficient"):
            stats["fund_insufficient"] += 1
        else:
            stats["fund_ok"] += 1
        if sec_d.get("data_insufficient"):
            stats["sector_insufficient"] += 1
        else:
            stats["sector_ok"] += 1
        if cont_d.get("ok") and not cont_d.get("data_insufficient"):
            stats["continuity_ok"] += 1
        elif cont_d.get("ok"):
            stats["continuity_partial"] += 1
        if monthly_d.get("ok") and not monthly_d.get("data_insufficient"):
            stats["monthly_ok"] += 1
        else:
            stats["monthly_insufficient"] += 1
        if st_d.get("ok") and not st_d.get("data_insufficient"):
            stats["sector_trend_ok"] += 1
        else:
            stats["sector_trend_insufficient"] += 1
        if rec_d.get("ok"):
            stats["recommend_ok"] += 1
        if rec_d.get("hard_filtered"):
            stats["recommend_hard_filtered"] += 1
        if any("DATA_INSUFFICIENT" in str(x) for x in (rec_d.get("notes") or [])):
            stats["recommend_data_insufficient"] += 1
        rl = str(risk_d.get("risk_score") or "MID").upper()
        if rl == "HIGH":
            stats["risk_high"] += 1
        elif rl == "LOW":
            stats["risk_low"] += 1
        else:
            stats["risk_mid"] += 1
        tc = clf["tag"]
        stats["tag_counts"][tc] = stats["tag_counts"].get(tc, 0) + 1
        ct = cont_d.get("continuity_type") or "?"
        stats["type_counts"][ct] = stats["type_counts"].get(ct, 0) + 1
        lv = rec_d.get("level") or "?"
        stats["rec_level_counts"][lv] = stats["rec_level_counts"].get(lv, 0) + 1

    return stats


# backward-compatible alias
enrich_results_v23 = enrich_results_v21



def build_report_md(payload: Dict[str, Any]) -> str:
    t = payload["t_date"]
    regime = payload["regime_v2"]
    ms = payload["market_score"]
    lines = [
        "# 📡 趋势确认突破雷达 V2.3 — 多周期共振观察",
        "",
        f"**日期：** {t}（T日，完整交易日收盘数据）",
        f"**MarketRegime：** {regime}（legacy {payload.get('regime')}，观察上限 ≤{payload.get('cap')}）",
        f"**MarketScore：** {ms}/100",
        f"**模型：** V2.0 结构 + V2.1 基本面/板块/风险 + V2.2 持续性/消耗度 + V2.3 月线/板块趋势/推荐观察强度（**无 TotalScore**；RecommendationScore=规则优先观察强度，非收益概率）",
        "",
        "## 今日市场结论",
        "",
    ]
    for c in payload.get("market_conclusion") or []:
        lines.append(f"- {c}")
    lines.append("")
    lines.append(
        f"> **环境判断：** MarketRegime=**{regime}**，MarketScore={ms}。"
        " 本报告为结构观察清单，**不构成买入建议，不预判次日必涨**。"
    )
    lines.append("")
    cov = payload.get("v21_coverage") or {}
    if cov:
        lines.append(
            f"> **数据覆盖：** 基本面可用 {cov.get('fund_ok',0)} / 不足 {cov.get('fund_insufficient',0)}；"
            f"板块可用 {cov.get('sector_ok',0)} / 不足 {cov.get('sector_insufficient',0)}；"
            f"持续性完整 {cov.get('continuity_ok',0)} / 部分 {cov.get('continuity_partial',0)}；"
            f"月线可用 {cov.get('monthly_ok',0)} / 不足 {cov.get('monthly_insufficient',0)}；"
            f"板块趋势可用 {cov.get('sector_trend_ok',0)} / 不足 {cov.get('sector_trend_insufficient',0)}；"
            f"推荐硬过滤 {cov.get('recommend_hard_filtered',0)}；"
            f"标签 {cov.get('tag_counts',{})}；类型 {cov.get('type_counts',{})}；"
            f"推荐等级 {cov.get('rec_level_counts',{})}"
        )
        lines.append("")
    lines.append("---")
    lines.append("")

    def _fmt_score(v):
        if v is None:
            return "-"
        try:
            return f"{float(v):.0f}" if float(v) == int(float(v)) else f"{float(v):.1f}"
        except Exception:
            return str(v)

    def _report_text(v):
        """Keep internal resonance tag codes out of user-facing markdown."""
        text = str(v)
        text = re.sub(
            r"\b(?:DAILY_UP|MONTHLY_[A-Z_]+|SECTOR_[A-Z_]+|HIGH_CONSUMPTION|CONTINUITY_HIGH)\b",
            "",
            text,
        )
        text = text.replace("（）", "").replace("()", "")
        text = text.replace("风险标签", "风险提示")
        return re.sub(r"\s{2,}", " ", text).strip()

    def main_table(items: List[Dict]):
        lines.append(
            "| 股票 | 代码 | 技术 | 基本面 | 板块 | 持续性 | 消耗度 | 阶段 | 类型 |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---|---|")
        for r in items:
            fs = r.get("fund_score")
            ss = r.get("sector_score")
            cs = r.get("continuity_score")
            cons = r.get("consumption_score")
            fs_s = "不足" if (r.get("fundamental") or {}).get("data_insufficient") and fs is None else _fmt_score(fs)
            ss_s = "不足" if (r.get("sector") or {}).get("data_insufficient") and ss is None else _fmt_score(ss)
            ctype = r.get("continuity_type_cn") or r.get("continuity_type") or "-"
            lines.append(
                "| {name} | {code6} | {tech} | {fund} | {sec} | {cont} | {cons} | {phase} | {ctype} |".format(
                    name=r.get("name"),
                    code6=r.get("code6"),
                    tech=_fmt_score(r.get("tech_score", r.get("score"))),
                    fund=fs_s,
                    sec=ss_s,
                    cont=_fmt_score(cs),
                    cons=_fmt_score(cons),
                    phase=r.get("phase_cn") or r.get("phase"),
                    ctype=ctype,
                )
            )
        lines.append("")

    def detail_cards(items: List[Dict], limit: int = 8):
        detail = list(items[:limit])
        for r in detail:
            m = r.get("metrics") or {}
            ck = r.get("v21_checklist") or {}
            cont = r.get("continuity") or {}
            lines.append(
                f"### {r.get('name')}（{r.get('code6')}）— 技术{r.get('tech_score', r.get('score'))} | "
                f"持续性{r.get('continuity_score')} | {r.get('tag_cn') or r.get('tag')}"
            )
            lines.append("")
            lines.append(
                f"- **维度：** 技术={r.get('tech_score', r.get('score'))}　"
                f"基本面={r.get('fund_score') if r.get('fund_score') is not None else '不足'}　"
                f"板块={r.get('sector_score') if r.get('sector_score') is not None else '不足'}　"
                f"持续性={r.get('continuity_score')}　消耗度={r.get('consumption_score')}　"
                f"风险={r.get('risk_level') or '-'}"
            )
            lines.append(
                f"- **清单：** 技术{ck.get('技术','—')} / 基本面{ck.get('基本面','—')} / "
                f"交易风险{ck.get('交易风险','—')} / 板块{ck.get('板块','—')} / "
                f"持续性{ck.get('持续性','—')} / 消耗度{ck.get('消耗度','—')}"
            )
            lines.append(
                f"- **阶段/类型：** {r.get('phase')} / {r.get('phase_cn')}　|　"
                f"{r.get('continuity_type')} {r.get('continuity_type_cn') or ''}"
            )
            fav = cont.get("favorable") or []
            lim = cont.get("limiting") or []
            if fav:
                lines.append(f"- **有利因素：** {'；'.join(fav[:8])}")
            if lim:
                lines.append(f"- **限制因素：** {'；'.join(lim[:8])}")
            d1 = r.get("continuity_delta1")
            d3 = r.get("continuity_delta3")
            d5 = r.get("continuity_delta5")
            dlab = r.get("continuity_delta_label")
            if any(x is not None for x in (d1, d3, d5)):
                lines.append(
                    f"- **ContinuityDelta：** Δ1={d1}　Δ3={d3}　Δ5={d5}"
                    + (f"　({dlab})" if dlab else "")
                    + " — 状态监测，非收益预测"
                )
            why = r.get("v21_why") or []
            if why:
                lines.append(f"- **归类理由：** {'；'.join(why)}")
            notes = list(r.get("v21_notes") or []) + list(cont.get("notes") or [])
            if notes:
                lines.append(f"- **备注：** {'；'.join(notes[:6])}")
            lines.append(
                f"- **量价摘要：** close={m.get('close')} VR={m.get('vol_ratio')} "
                f"平台区间={m.get('platform_range_pct')}% Return5={m.get('return5_pct')}"
            )
            lines.append("- **观察要点：** 跟踪结构阶段与持续性/消耗度变化；**非买卖建议**。")
            lines.append("")

    def section(title: str, items: List[Dict], empty_msg: str, annotate: str = ""):
        lines.append(f"## {title}")
        lines.append("")
        if annotate:
            lines.append(annotate)
            lines.append("")
        if not items:
            lines.append(empty_msg)
            lines.append("")
            return
        main_table(items)
        detail_cards(items)

    def _with_force(items, limit=None, force=("601579", "002011")):
        src = list(items or [])
        out = list(src if limit is None else src[:limit])
        seen = {r.get("code6") for r in out}
        for code in force:
            if code in seen:
                continue
            for r in src:
                if r.get("code6") == code:
                    out.append(r)
                    seen.add(code)
                    break
        return out

    core = _with_force(payload.get("core_list") or [])
    obs = _with_force(payload.get("observation_list") or [], 25)
    high = _with_force(payload.get("high_risk_list") or [], 20)
    oh_list = _with_force(payload.get("oh_list") or [], 20)
    pool_new = payload.get("pool_new_trend") or []
    pool_second = payload.get("pool_second_confirm") or []
    pool_main = payload.get("pool_main_continue") or []
    pool_decay = payload.get("pool_trend_decay") or []

    # ---- V2.3 多周期共振观察榜 ----
    rec_list = payload.get("recommendation_list") or []
    lines.append("## 📡 V2.3 多周期共振观察榜")
    lines.append("")
    lines.append(
        "> **RecommendationScore = 规则优先观察强度**（非收益概率、非买入建议）。"
        " 排序：推荐观察强度 → 持续性 → 技术 → 板块日趋势分 → 消耗度升序。"
        " 硬过滤：Tech<70 | Cont<60 | Risk=HIGH | Cons≥90 | TREND_FAILURE。"
        " 月线DOWN / 板块日DOWN **不**硬过滤，仅降权/打标。"
    )
    lines.append("")
    if not rec_list:
        lines.append("**无。** 无同时通过硬过滤的观察候选。")
        lines.append("")
    else:
        lines.append(
            "| 股票 | 代码 | 推荐分 | 等级 | 技术 | 持续性 | 月线 | 板块日 | 消耗 | 阶段 |"
        )
        lines.append("|---|---|---:|---|---:|---:|---|---|---:|---|")
        for r in rec_list[:40]:
            rd = r.get("recommendation") or {}
            sm = r.get("stockMonthlyTrend") or {}
            st = r.get("sectorTrend") or {}
            lines.append(
                "| {name} | {code6} | {score} | {lvl} | {tech} | {cont} | {mt} | {sd} | {cons} | {phase} |".format(
                    name=r.get("name"),
                    code6=r.get("code6"),
                    score=_fmt_score(rd.get("score")),
                    lvl=rd.get("level") or "-",
                    tech=_fmt_score(r.get("tech_score", r.get("score"))),
                    cont=_fmt_score(r.get("continuity_score")),
                    mt=(sm.get("trend_cn") or sm.get("trend") or "-"),
                    sd=(st.get("dailyTrendCn") or st.get("dailyTrend") or "-"),
                    cons=_fmt_score(r.get("consumption_score")),
                    phase=r.get("phase_cn") or r.get("phase") or "-",
                )
            )
        lines.append("")
        # compact reasons for top entries
        for r in rec_list[:15]:
            rd = r.get("recommendation") or {}
            reasons = rd.get("reason") or []
            risks = rd.get("risk") or []
            ck = rd.get("checklist") or {}
            lines.append(
                f"### {r.get('name')}（{r.get('code6')}）— 观察强度 {rd.get('score')} / {rd.get('level')}"
            )
            lines.append("")
            if ck:
                ck_line = "　".join(f"{k}{v}" for k, v in ck.items())
                lines.append(f"- **核心理由清单：** {ck_line}")
            if reasons:
                for rr in reasons[:6]:
                    lines.append(f"- ✓ {_report_text(rr)}")
            if risks:
                for rk in risks[:4]:
                    lines.append(f"- ⚠ {_report_text(rk)}")
            lines.append("- **说明：** 以上为规则模板观察表述，**不构成买入建议**。")
            lines.append("")

    section(
        "🎯 核心趋势 CORE_TREND",
        core,
        "**无。** 无同时满足 技术≥80 + 基本面≥60 + 板块≥60 + 持续性≥75 + 消耗度<70 + 风险≠HIGH + 二次/主升阶段 的标的。",
        annotate="> 排序：持续性 → 技术 → 板块 → 基本面 → 风险。持续性为**状态分**，不预测未来收益。",
    )

    section(
        "① 新趋势 FIRST_BREAKOUT",
        pool_new[:20],
        "**无。**",
        annotate="> 阶段=FIRST_BREAKOUT 的结构观察池。",
    )
    section(
        "② 二次确认 SECOND_BREAKOUT + Cont≥75",
        pool_second[:20],
        "**无。**",
    )
    section(
        "③ 主升延续 MAIN_RISE + Cont≥80 + Cons<70",
        pool_main[:20],
        "**无。**",
        annotate="> 含 MAIN_RISE / MAIN_RISE_ACCEL。",
    )
    section(
        "④ 趋势衰减 Cont<60",
        pool_decay[:20],
        "**无。**",
        annotate="> ContinuityScore<60（TYPE_D 倾向）；状态衰减监测，非买卖信号。",
    )

    section(
        "👀 观察 OBSERVATION",
        obs,
        "**无。**",
    )
    section(
        "⚠ 高风险 HIGH_RISK（仍展示）",
        high,
        "**无。**",
        annotate="> 技术偏强但基本面偏弱 / 交易风险=HIGH / 或过热结构；**仅观察标注，非核心**。",
    )

    lines.append("## 🔥过热 OH")
    lines.append("")
    lines.append(
        "> **过热标注：** Return5>35% 或近段≥3涨停。可观察但不作为普通二次突破核心。"
    )
    lines.append("")
    if not oh_list:
        lines.append("**无。**")
        lines.append("")
    else:
        main_table(oh_list)
        detail_cards(oh_list, limit=6)

    s_list = payload.get("s_list") or []
    a_list = payload.get("a_list") or []
    if s_list or a_list:
        lines.append("## （参考）V2.0 技术 S/A")
        lines.append("")
        lines.append(
            f"- 技术S推送 {len(s_list)} / A推送 {len(a_list)}（未与基本面/持续性合成总分）"
        )
        lines.append("")

    lines.append("## KJS_PATTERN")
    lines.append("")
    kjs = payload.get("kjs_list") or []
    kjs_now = [x for x in kjs if x.get("kjs_tag") == "KJS_NOW" or (x.get("kjs_pattern") and not x.get("kjs_hist"))]
    kjs_hist = [x for x in kjs if x.get("kjs_tag") == "KJS_HIST" or x.get("kjs_hist")]
    lines.append(
        f"- 数量：**{len(kjs)}**（T日 KJS_NOW={len(kjs_now)}，近60日 KJS_HIST={len(kjs_hist)}）"
    )
    if kjs_now:
        lines.append("- KJS_NOW：" + "、".join(f"{x.get('name')}({x.get('code6')})" for x in kjs_now[:15]))
    if kjs_hist:
        lines.append(
            "- KJS_HIST："
            + "、".join(
                f"{x.get('name')}({x.get('code6')},last={x.get('kjs_last_date') or '-'})"
                for x in kjs_hist[:15]
            )
        )
    lines.append("")

    lines.append("## 阶段分布")
    lines.append("")
    lines.append(str(payload.get("phase_counts") or {}))
    lines.append("")

    lines.append("## 最终结论")
    lines.append("")
    lines.append(
        f"- MarketRegime：**{regime}**，MarketScore **{ms}**，legacy {payload.get('regime')} / cap≤{payload.get('cap')}"
    )
    lines.append(
        f"- 预筛候选：{payload.get('prefilter_n')} → hist成功 {payload.get('hist_ok')} → 结构候选 {payload.get('structured_n')}"
    )
    lines.append(
        f"- **CORE_TREND：{payload.get('CORE', len(core))} | OBS：{payload.get('OBSERVATION', len(obs))} | "
        f"HIGH_RISK：{payload.get('HIGH_RISK', len(high))} | OH：{payload.get('OH', len(oh_list))} | "
        f"FILTER：{payload.get('FILTER', 0)}**"
    )
    lines.append(
        f"- 四池：①新趋势{len(pool_new)} ②二次确认{len(pool_second)} "
        f"③主升延续{len(pool_main)} ④趋势衰减{len(pool_decay)}"
    )
    rec_n = len(payload.get("recommendation_list") or [])
    lines.append(
        f"- **V2.3 多周期共振观察榜：{rec_n}**（RecommendationScore=规则优先观察强度，非收益预测）"
    )
    lines.append(
        f"- 基本面不足：{(payload.get('v21_coverage') or {}).get('fund_insufficient', 0)} "
        f"/ 板块不足：{(payload.get('v21_coverage') or {}).get('sector_insufficient', 0)}"
    )
    lines.append("")
    lines.append(
        "> 操作含义（观察层面，非买卖建议）：仅跟踪结构阶段与持续性/消耗度是否延续；"
        "**不构成买入推荐，不预判次日必涨；不把 ContinuityScore / RecommendationScore 当作收益预测**。"
    )
    lines.append("")

    lines.append("## 数据缺口")
    lines.append("")
    for g in (payload.get("data_gaps") or [])[:12]:
        lines.append(f"- {g}")
    if not payload.get("data_gaps"):
        lines.append("- （无）")
    lines.append("")
    lines.append("## 免责声明")
    lines.append("")
    lines.append(
        "本扫描为量化结构观察工具输出，仅供研究与复盘，不构成任何投资建议或买卖推荐。"
    )
    lines.append("")
    return "\n".join(lines)



def run(
    t_date: str,
    max_prefilter: int = 250,
    workers: int = 4,
    force_codes: Optional[List[str]] = None,
    strict_asof: bool = False,
) -> Dict[str, Any]:
    print(f"=== V2.3 staircase+resonance scan T={t_date} strict={strict_asof} ===")
    gaps: List[str] = []

    if strict_asof:
        spot = ds.spot_from_local_daily(t_date)
        if spot is None or not len(spot):
            raise SystemExit(f"strict-asof: no local daily bars on {t_date}")
        import os

        spot_src = "market_db" if os.environ.get("TREND_RANK_DB") else "local_parquet"
        print(f"spot source={spot_src} rows={len(spot)}")
        gaps.append("strict_asof local parquet universe")
        sp = {"ok": True}
    else:
        sp = ds.fetch_spot_universe()
        spot = sp["value"] if sp["ok"] else None
    if (not strict_asof) and sp["ok"]:
        print(f"spot source={sp['source']} rows={len(spot)}")
        spot.to_pickle(OUT / "spot_sina.pkl")
    elif strict_asof:
        pass
    else:
        # try cached
        pkl = OUT / "spot_sina.pkl"
        if pkl.exists():
            spot = pd.read_pickle(pkl)
            gaps.append("spot live failed; using cached spot_sina.pkl")
            print("using cached spot", len(spot))
        else:
            raise SystemExit("spot failed and no cache")

    meta = compute_market_meta(t_date, spot, strict_asof=strict_asof)
    gaps.extend(meta.get("data_gaps") or [])
    regime_v2 = meta["regime_v2"]
    print("REGIME_V2", regime_v2, "legacy", meta["regime"], "MS", meta["market_score"])

    # name map for local-boost codes
    name_map = dict(zip(spot["代码"].astype(str), spot["名称"].astype(str)))

    cand = prefilter_spot(spot, max_n=max_prefilter)
    # full local parquet scan (no 800 cap); boost extras toward ~max_prefilter headroom
    boost_extra = max(80, max_prefilter // 2)
    cand = local_prefilter_boost(cand, t_date, extra=boost_extra)
    # force-codes always merged before hist
    fc = force_codes if force_codes is not None else load_force_codes()
    if fc:
        print("force_codes:", fc)
        cand = merge_force_codes(cand, spot, fc, t_date)
    # refresh names
    cand["名称"] = cand.apply(
        lambda r: name_map.get(str(r["代码"]), r["名称"]), axis=1
    )
    cand = cand[~cand["名称"].astype(str).map(_is_st_or_new)]
    print("prefilter", len(cand))
    cand.to_pickle(OUT / "prefilter_v2.pkl")

    if regime_v2 == "RISK_OFF":
        print("RISK_OFF — observation shell only")

    rows = list(cand.itertuples(index=False))
    # build spot row lookup
    spot_by = {str(r.代码): r for r in spot.itertuples(index=False)}

    hists: Dict[str, pd.DataFrame] = {}
    hist_sources: Dict[str, str] = {}
    errs: List[Dict[str, str]] = []
    print("fetching hist...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for r in rows:
            full = str(r.代码)
            spot_row = spot_by.get(full, r)
            futs[ex.submit(fetch_one, full, t_date, spot_row, strict_asof)] = full
        done = 0
        for fut in as_completed(futs):
            full, df, err, src = fut.result()
            done += 1
            if df is not None:
                hists[full] = df
                hist_sources[full] = src
            else:
                errs.append({"code": full, "err": err or "fail"})
            if done % 20 == 0:
                print(f"  fetched {done}/{len(rows)} ok={len(hists)}")
    print("hist ok", len(hists), "err", len(errs), "sources", dict(Counter(hist_sources.values())))
    gaps.append(f"hist fetch failures: {len(errs)}")

    results: List[Dict[str, Any]] = []
    for full, hist in hists.items():
        row = spot_by.get(full)
        name = name_map.get(full, full[2:])
        if row is not None and hasattr(row, "名称"):
            name = row.名称
        code6 = full[2:]
        sr = analyze_hist(hist)
        d = sr.to_dict()
        # regime gates
        eg, pushable, note = apply_regime_gates(
            d.get("grade"),
            float(d.get("score") or 0),
            regime_v2,
            bool((d.get("flags") or {}).get("full_structure")),
            overheated=bool(d.get("overheated")),
        )
        rec = {
            "code": full,
            "code6": code6,
            "name": name,
            "phase": d["phase"],
            "phase_cn": d["phase_cn"],
            "kjs_pattern": d["kjs_pattern"],
            "kjs_hist": d.get("kjs_hist"),
            "kjs_ever_60d": d.get("kjs_ever_60d"),
            "kjs_last_date": d.get("kjs_last_date"),
            "kjs_tag": d.get("kjs_tag"),
            "overheated": bool(d.get("overheated")),
            "pushable_core": bool(d.get("pushable_core")) and not bool(d.get("overheated")),
            "score": d["score"],
            "grade": eg,
            "grade_raw": d.get("grade"),
            "pushable": pushable and regime_v2 != "RISK_OFF" and not bool(d.get("overheated")),
            "regime_note": note,
            "drop": d["drop"],
            "drop_reason": d["drop_reason"],
            "score_breakdown": d["score_breakdown"],
            "risks": d["risks"],
            "flags": d["flags"],
            "metrics": d["metrics"],
            "hist_source": hist_sources.get(full),
            # near-pool compatibility fields
            "close": (d["metrics"] or {}).get("close"),
            "ma20": (d["metrics"] or {}).get("ma20"),
            "prev20high": (d["metrics"] or {}).get("breakout_high")
            or (d["metrics"] or {}).get("platform_high"),
            "vol_ratio": (d["metrics"] or {}).get("vol_ratio"),
            "dist_ma20_pct": (d["metrics"] or {}).get("dist_ma20_pct"),
            "avg20_amt_yi": (d["metrics"] or {}).get("avg20_amt_yi"),
            "rs20_pct": None,
            "rs60_pct": None,
            "state": d["phase_cn"],
            "hard_pass": bool(d.get("ok")),
        }
        # RS vs HS300 if possible
        try:
            c = hist["close"].astype(float)
            if len(c) >= 21:
                rec["rs20_pct"] = round(
                    (float(c.iloc[-1] / c.iloc[-21] - 1) - float(meta.get("hs300_ret20") or 0))
                    * 100,
                    3,
                )
            if len(c) >= 61:
                rec["rs60_pct"] = round(
                    (float(c.iloc[-1] / c.iloc[-61] - 1) - float(meta.get("hs300_ret60") or 0))
                    * 100,
                    3,
                )
        except Exception:
            pass
        results.append(rec)

    # ---- V2.1 enrichment + V2.2 Continuity ----
    print("enriching V2.1 fund/sector/risk + V2.2 continuity + V2.3 monthly/sector/recommend...")
    v21_stats = enrich_results_v21(results, hists, t_date, strict_asof=strict_asof)
    print("v21/v22/v23 coverage", v21_stats)

    # filter structured (OH kept for reporting — drop=False, grade=OH)
    structured = [
        r
        for r in results
        if not r.get("drop")
        and r.get("grade")
        and r.get("phase")
        not in ("NONE", "TREND_FAILURE")
    ]
    structured.sort(key=lambda x: (-float(x.get("score") or 0), -(x.get("rs20_pct") or 0)))

    oh_list = [
        r
        for r in structured
        if r.get("grade") == "OH" or r.get("overheated") or r.get("phase") == "OVERHEATED"
    ]
    oh_list.sort(key=lambda x: (-float(x.get("score") or 0), -(x.get("metrics") or {}).get("return5_pct") or 0))

    s_list = [
        r
        for r in structured
        if r.get("grade") == "S" and r.get("pushable") and not r.get("overheated")
    ]
    a_list = [
        r
        for r in structured
        if r.get("grade") == "A" and r.get("pushable") and not r.get("overheated")
    ]
    # NORMAL S bar may promote — already in apply_regime_gates
    if regime_v2 == "WEAK":
        # only ≥85 pushable already
        pass
    if regime_v2 == "RISK_OFF":
        s_list, a_list = [], []

    b_list = [r for r in structured if r.get("grade") == "B"]
    # KJS: T-day OR history tag
    kjs_list = [
        r
        for r in structured
        if r.get("kjs_pattern") or r.get("kjs_hist") or r.get("kjs_ever_60d") or r.get("kjs_tag")
    ]
    selected = []
    selected.extend(s_list[:5])
    for r in a_list:
        if len(selected) >= min(10, meta["cap"] or 10):
            break
        selected.append(r)

    # V2.1 classification boards (from all results with grades / structured+filter)
    def _tag_of(r):
        return r.get("v22_tag") or r.get("v21_tag") or r.get("tag") or ""

    def _is_core_tag(t: str) -> bool:
        return t in ("CORE_TREND", "CORE_CANDIDATE")

    def _risk_rank(r):
        lvl = str(r.get("risk_level") or (r.get("risk") or {}).get("risk_score") or "MID").upper()
        return {"LOW": 0, "MID": 1, "HIGH": 2}.get(lvl, 1)

    def _core_sort_key(x):
        # Continuity → Tech → Sector → Fund → Risk
        return (
            -float(x.get("continuity_score") or 0),
            -float(x.get("tech_score") or x.get("score") or 0),
            -float(x.get("sector_score") or 0),
            -float(x.get("fund_score") or 0),
            _risk_rank(x),
        )

    pool = [r for r in results if not r.get("drop") or r.get("overheated")]
    structured_codes = {r.get("code6") for r in structured}
    board_pool = list(structured) + [
        r for r in results
        if r.get("code6") not in structured_codes
        and _tag_of(r) in ("HIGH_RISK", "OBSERVATION", "FILTER", "CORE_CANDIDATE", "CORE_TREND")
        and float(r.get("score") or 0) >= 55
    ]
    seen_c = set()
    uniq_pool = []
    for r in board_pool + pool:
        c6 = r.get("code6")
        if c6 in seen_c:
            continue
        seen_c.add(c6)
        uniq_pool.append(r)

    core_list = [r for r in uniq_pool if _is_core_tag(_tag_of(r))]
    observation_list = [r for r in uniq_pool if _tag_of(r) == "OBSERVATION"]
    high_risk_list = [r for r in uniq_pool if _tag_of(r) == "HIGH_RISK"]
    filter_list = [r for r in uniq_pool if _tag_of(r) == "FILTER"]
    core_list.sort(key=_core_sort_key)
    observation_list.sort(key=lambda x: (-float(x.get("continuity_score") or 0), -float(x.get("tech_score") or x.get("score") or 0)))
    high_risk_list.sort(key=lambda x: (-float(x.get("tech_score") or x.get("score") or 0)))

    # Four observation pools (V2.2)
    MAIN_PHASES = {"MAIN_RISE", "MAIN_RISE_ACCEL"}
    pool_new_trend = [
        r for r in uniq_pool
        if r.get("phase") == "FIRST_BREAKOUT" and not r.get("overheated")
    ]
    pool_second_confirm = [
        r for r in uniq_pool
        if r.get("phase") == "SECOND_BREAKOUT"
        and (r.get("continuity_score") is not None and float(r.get("continuity_score") or 0) >= 75)
        and not r.get("overheated")
    ]
    pool_main_continue = [
        r for r in uniq_pool
        if r.get("phase") in MAIN_PHASES
        and (r.get("continuity_score") is not None and float(r.get("continuity_score") or 0) >= 80)
        and (r.get("consumption_score") is not None and float(r.get("consumption_score") or 0) < 70)
        and not r.get("overheated")
    ]
    pool_trend_decay = [
        r for r in uniq_pool
        if r.get("continuity_score") is not None and float(r.get("continuity_score") or 0) < 60
    ]
    for lst in (pool_new_trend, pool_second_confirm, pool_main_continue, pool_trend_decay):
        lst.sort(key=_core_sort_key)

    phase_counts = dict(Counter(r.get("phase") for r in results if not r.get("drop")))

    # V2.3 recommendation list (hard-filtered)
    recommendation_list = [
        r for r in uniq_pool
        if (r.get("recommendation") or {}).get("score") is not None
        and not (r.get("recommendation") or {}).get("hard_filtered")
        and passes_recommendation_hard_filter(r)
    ]
    recommendation_list.sort(key=sort_recommendation_key)

    payload = {
        "t_date": t_date,
        "model": "V2.3_multi_period_resonance",
        "asof_mode": "strict_local" if strict_asof else "live",
        "regime": meta["regime"],
        "regime_v2": regime_v2,
        "market_score": meta["market_score"],
        "cap": meta["cap"],
        "market_conclusion": meta["market_conclusion"],
        "prefilter_n": len(cand),
        "hist_ok": len(hists),
        "structured_n": len(structured),
        "phase_counts": phase_counts,
        "S": len(s_list),
        "A": len(a_list),
        "B": len(b_list),
        "OH": len(oh_list),
        "CORE": len(core_list),
        "OBSERVATION": len(observation_list),
        "HIGH_RISK": len(high_risk_list),
        "FILTER": len(filter_list),
        "kjs_n": len(kjs_list),
        "s_list": s_list,
        "a_list": a_list,
        "b_list": b_list[:20],
        "oh_list": oh_list[:30],
        "core_list": core_list[:30],
        "observation_list": observation_list[:40],
        "high_risk_list": high_risk_list[:30],
        "filter_list": filter_list[:20],
        "pool_new_trend": pool_new_trend[:30],
        "pool_second_confirm": pool_second_confirm[:30],
        "pool_main_continue": pool_main_continue[:30],
        "pool_trend_decay": pool_trend_decay[:30],
        "POOL_NEW": len(pool_new_trend),
        "POOL_SECOND": len(pool_second_confirm),
        "POOL_MAIN": len(pool_main_continue),
        "POOL_DECAY": len(pool_trend_decay),
        "kjs_list": kjs_list,
        "selected": selected,
        "recommendation_list": recommendation_list[:50],
        "RECOMMEND": len(recommendation_list),
        "v21_coverage": v21_stats,
        "v23_coverage": v21_stats,
        "data_gaps": gaps,
        "built_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S%z"),
    }

    # write outputs
    _cand_payload = {
        **payload,
        "all_hard_pass": structured[:40],
        "meta": {k: v for k, v in meta.items() if k != "data_gaps"},
    }
    _cand_txt = json.dumps(_cand_payload, ensure_ascii=False, indent=2, default=str)
    (OUT / "candidates_v2.json").write_text(_cand_txt, encoding="utf-8")
    (OUT / "candidates_v2_3.json").write_text(_cand_txt, encoding="utf-8")
    (OUT / "all_results_v2.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    # update market_meta with v2 regime
    meta_out = dict(meta)
    meta_out["data_gaps"] = gaps
    (OUT / "market_meta.json").write_text(
        json.dumps(meta_out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    md = build_report_md(payload)
    (OUT / "report_v2.md").write_text(md, encoding="utf-8")
    (OUT / "report_v2_3.md").write_text(md, encoding="utf-8")
    # routine compatibility: report.md tracks V2
    (OUT / "report.md").write_text(md, encoding="utf-8")

    # also write slim candidates.json for near-pool / legacy consumers pointing at V2
    legacy_cand = {
        "t_date": t_date,
        "regime": meta["regime"],
        "regime_v2": regime_v2,
        "market_score": meta["market_score"],
        "cap": meta["cap"],
        "screened": len(results),
        "hard_pass": len(structured),
        "hard_core_eligible": len(structured),
        "S": len(s_list),
        "A": len(a_list),
        "OH": len(oh_list),
        "selected": selected,
        "all_hard_pass": structured[:30],
        "top_B_not_pushed": b_list[:10],
        "oh_list": oh_list[:20],
        "model": "V2.3_multi_period_resonance",
        "kjs_n": len(kjs_list),
        "CORE": len(core_list),
        "OBSERVATION": len(observation_list),
        "HIGH_RISK": len(high_risk_list),
        "core_list": core_list[:20],
        "observation_list": observation_list[:20],
        "high_risk_list": high_risk_list[:15],
        "phase_counts": phase_counts,
        "v21_coverage": v21_stats,
        "data_gaps": gaps,
    }
    (OUT / "candidates.json").write_text(
        json.dumps(legacy_cand, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "all_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    print(
        f"DONE CORE_TREND={len(core_list)} OBS={len(observation_list)} HIGH_RISK={len(high_risk_list)} "
        f"OH={len(oh_list)} pools=①{len(pool_new_trend)}②{len(pool_second_confirm)}"
        f"③{len(pool_main_continue)}④{len(pool_trend_decay)} "
        f"RECOMMEND={len(recommendation_list)} "
        f"S={len(s_list)} A={len(a_list)} KJS={len(kjs_list)} "
        f"fund_insuff={v21_stats.get('fund_insufficient')} "
        f"monthly_insuff={v21_stats.get('monthly_insufficient')} "
        f"sector_trend_insuff={v21_stats.get('sector_trend_insufficient')} "
        f"regime={regime_v2} phases={phase_counts}"
    )

    if not strict_asof:
        # near pool
        try:
            from build_near_pool import build_pool

            build_pool(patch_report=True)
        except Exception as e:
            print("near_pool skip:", type(e).__name__, e)
            gaps.append(f"near_pool: {type(e).__name__}: {e}")

    return payload


def main():
    ap = argparse.ArgumentParser(description="V2 staircase breakout scanner")
    ap.add_argument("--t-date", default=None, help="YYYY-MM-DD; default detect_t_date")
    ap.add_argument("--max-prefilter", type=int, default=250, help="spot prefilter size (default 250)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--strict-asof",
        action="store_true",
        help="rebuild universe from local parquet truncated at T; no live spot or future bars",
    )
    ap.add_argument(
        "--force-codes",
        default=None,
        help="comma-separated codes always merged (e.g. 002011,601579); also V2_FORCE_CODES env / meta/force_codes.txt",
    )
    ap.add_argument(
        "--force-file",
        default=None,
        help="optional path to force_codes watchlist (default meta/force_codes.txt)",
    )
    args = ap.parse_args()
    if args.t_date:
        t_date = args.t_date
    else:
        td = ds.detect_t_date()
        t_date = td["value"]["t_date"]
        print(f"auto T_DATE={t_date} via {td['source']}")
    force_file = Path(args.force_file) if args.force_file else None
    fc = load_force_codes(cli_codes=args.force_codes, force_file=force_file)
    run(
        t_date,
        max_prefilter=args.max_prefilter,
        workers=args.workers,
        force_codes=fc,
        strict_asof=bool(args.strict_asof),
    )


if __name__ == "__main__":
    main()
