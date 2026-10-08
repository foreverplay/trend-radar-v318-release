#!/usr/bin/env python3
"""
V2.1 SectorEngine — industry relative strength from local peers / EM helpers.

Industry source priority:
  1) FundamentalEngine / snapshot / LRB INDUSTRY_NAME
  2) data_sources.fetch_industry (EM)
Concept boards optional (not required).

DATA_INSUFFICIENT if no industry.
No buy advice. No ZhuangStock.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
try:
    from paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
except ImportError:  # packaged / flat layout fallback
    try:
        from trend_breakout.paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
    except ImportError:
        from pathlib import Path as __P
        import os as __os
        _PKG_ROOT = __P(__os.environ.get("TREND_BREAKOUT_ROOT", __P(__file__).resolve().parent))
        _k = __P(__os.environ.get("ASHARE_KLINE_ROOT", str(_PKG_ROOT.parent.parent / "data" / "sample" / "kline")))
        _PKG_LOCAL_DAILY = _k / "raw" / "daily"
        _PKG_CACHE = _PKG_ROOT / "cache"
        _PKG_CONT_CACHE = _PKG_CACHE / "continuity_history"
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

LOCAL_DAILY = _PKG_LOCAL_DAILY
FIN_SNAP = Path("/workspace/ashare-financials/analysis/merged_latest/company_latest_year_snapshot.csv")


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _ret_pct(close: pd.Series, n: int) -> Optional[float]:
    if close is None or len(close) < n + 1:
        return None
    a = float(close.iloc[-1])
    b = float(close.iloc[-(n + 1)])
    if b == 0 or not np.isfinite(a) or not np.isfinite(b):
        return None
    return (a / b - 1.0) * 100.0


def _ma(close: pd.Series, n: int) -> Optional[float]:
    if close is None or len(close) < n:
        return None
    return float(close.tail(n).mean())


def _load_local_close(code6: str, t_date: Optional[str] = None) -> Optional[pd.Series]:
    p = LOCAL_DAILY / f"{code6}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p, columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        if t_date:
            df = df[df["date"] <= pd.Timestamp(t_date)]
        if len(df) < 25:
            return None
        return df["close"].astype(float).reset_index(drop=True)
    except Exception:
        return None


@dataclass
class SectorResult:
    ok: bool = False
    data_insufficient: bool = True
    score: Optional[float] = None
    industry: Optional[str] = None
    concept: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    checklist: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SectorEngine:
    def __init__(self):
        self._peers: Dict[str, List[str]] = {}
        self._code_ind: Dict[str, str] = {}
        self._loaded = False
        self._fund = None  # optional FundamentalEngine

    def bind_fundamental(self, fund_engine) -> None:
        self._fund = fund_engine

    def load(self) -> None:
        if self._loaded:
            return
        if FIN_SNAP.exists():
            snap = pd.read_csv(FIN_SNAP, dtype={"代码": str}, usecols=["代码", "行业"])
            snap["代码"] = snap["代码"].astype(str).str.zfill(6)
            snap = snap.dropna(subset=["行业"])
            for _, r in snap.iterrows():
                ind = str(r["行业"])
                if ind in ("nan", "None", "--", ""):
                    continue
                c = r["代码"]
                self._code_ind[c] = ind
                self._peers.setdefault(ind, []).append(c)
        self._loaded = True

    def resolve_industry(self, code6: str) -> Tuple[Optional[str], str]:
        self.load()
        code6 = _norm_code(code6)
        if self._fund is not None:
            try:
                ind = self._fund.industry_of(code6)
                if ind:
                    return ind, "fundamental/local"
            except Exception:
                pass
        if code6 in self._code_ind:
            return self._code_ind[code6], "snapshot"
        # live EM fallback
        try:
            import data_sources as ds

            r = ds.fetch_industry(code6)
            if r.get("ok"):
                ind = str(r["value"])
                if ind and ind not in ("未知", "nan", "None", "--"):
                    self._code_ind[code6] = ind
                    return ind, r.get("source") or "fetch_industry"
        except Exception as e:
            return None, f"industry_fail:{type(e).__name__}"
        return None, "none"

    def _peer_avg_returns(
        self, industry: str, t_date: Optional[str], max_peers: int = 40
    ) -> Dict[str, Any]:
        peers = list(self._peers.get(industry) or [])
        if not peers:
            return {"ok": False, "n": 0}
        # prefer peers with local parquet
        usable = [c for c in peers if (LOCAL_DAILY / f"{c}.parquet").exists()]
        if len(usable) > max_peers:
            # deterministic sample
            usable = sorted(usable)[:max_peers]
        r5s, r20s = [], []
        ma_stack_flags = []
        for c in usable:
            cl = _load_local_close(c, t_date)
            if cl is None:
                continue
            r5 = _ret_pct(cl, 5)
            r20 = _ret_pct(cl, 20)
            if r5 is not None:
                r5s.append(r5)
            if r20 is not None:
                r20s.append(r20)
            ma5, ma10, ma20 = _ma(cl, 5), _ma(cl, 10), _ma(cl, 20)
            if None not in (ma5, ma10, ma20):
                ma_stack_flags.append(1 if ma5 > ma10 > ma20 else 0)
        if not r20s and not r5s:
            return {"ok": False, "n": 0, "peers_tried": len(usable)}
        return {
            "ok": True,
            "n": max(len(r5s), len(r20s)),
            "sector_return5": float(np.nanmean(r5s)) if r5s else None,
            "sector_return20": float(np.nanmean(r20s)) if r20s else None,
            "sector_ma_stack_share": float(np.mean(ma_stack_flags)) if ma_stack_flags else None,
            "peers_used": len(usable),
        }

    def evaluate(
        self,
        code: str,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
        concept: Optional[str] = None,
    ) -> SectorResult:
        self.load()
        code6 = _norm_code(code)
        res = SectorResult(concept=concept)
        industry, src = self.resolve_industry(code6)
        res.industry = industry
        res.source = src

        if not industry:
            res.data_insufficient = True
            res.tags = ["DATA_INSUFFICIENT"]
            res.notes = ["无行业分类"]
            res.checklist = {"板块": "⚠"}
            return res

        # stock returns
        stock_r5 = stock_r20 = None
        stock_ma_stack = None
        close = None
        if hist is not None and len(hist) >= 25 and "close" in hist.columns:
            h = hist.copy()
            if "date" in h.columns:
                h["date"] = pd.to_datetime(h["date"])
                h = h.sort_values("date")
                if t_date:
                    h = h[h["date"] <= pd.Timestamp(t_date)]
            close = h["close"].astype(float).reset_index(drop=True)
        if close is None:
            close = _load_local_close(code6, t_date)
        if close is not None:
            stock_r5 = _ret_pct(close, 5)
            stock_r20 = _ret_pct(close, 20)
            ma5, ma10, ma20 = _ma(close, 5), _ma(close, 10), _ma(close, 20)
            if None not in (ma5, ma10, ma20):
                stock_ma_stack = bool(ma5 > ma10 > ma20)

        peer = self._peer_avg_returns(industry, t_date)
        sec_r5 = peer.get("sector_return5")
        sec_r20 = peer.get("sector_return20")
        sec_ma_share = peer.get("sector_ma_stack_share")

        rel_strength = None
        if stock_r20 is not None and sec_r20 is not None:
            rel_strength = stock_r20 - sec_r20

        tags: List[str] = []
        notes: List[str] = []
        checklist: Dict[str, str] = {}

        # Score 0–100
        # If peer stats missing → soft insufficient for RS but keep industry known mid score? Spec: DATA_INSUFFICIENT if no industry (already handled).
        # If peers fail, note and give modest industry-only score based on stock alone.
        score = 50.0
        if not peer.get("ok"):
            notes.append("同行本地行情不足，板块收益用个股近似中性")
            if stock_r20 is not None:
                if stock_r20 >= 15:
                    score = 62.0
                elif stock_r20 >= 5:
                    score = 55.0
                elif stock_r20 >= 0:
                    score = 48.0
                else:
                    score = 35.0
            checklist["板块"] = "⚠"
        else:
            # sector momentum
            if sec_r20 is not None:
                if sec_r20 >= 10:
                    score += 18
                elif sec_r20 >= 5:
                    score += 12
                elif sec_r20 >= 0:
                    score += 5
                elif sec_r20 >= -5:
                    score -= 5
                else:
                    score -= 15
            if sec_r5 is not None:
                if sec_r5 >= 5:
                    score += 8
                elif sec_r5 >= 0:
                    score += 3
                elif sec_r5 < -5:
                    score -= 8
            if sec_ma_share is not None:
                if sec_ma_share >= 0.6:
                    score += 10
                    tags.append("SECTOR_MA_STACK")
                elif sec_ma_share <= 0.3:
                    score -= 8
            if rel_strength is not None:
                if rel_strength >= 8:
                    score += 15
                    tags.append("LEADER_RELATIVE_STRENGTH")
                elif rel_strength >= 3:
                    score += 8
                    tags.append("LEADER_RELATIVE_STRENGTH")
                elif rel_strength >= 0:
                    score += 3
                elif rel_strength >= -5:
                    score -= 5
                else:
                    score -= 12
            if stock_ma_stack:
                score += 5

        score = float(round(min(100.0, max(0.0, score)), 1))

        if score >= 70 or (sec_r20 is not None and sec_r20 >= 5 and (rel_strength or 0) >= 0):
            if "SECTOR_STRONG" not in tags and score >= 65:
                tags.append("SECTOR_STRONG")
        if score <= 40 or (sec_r20 is not None and sec_r20 <= -5):
            tags.append("SECTOR_WEAK")

        if "SECTOR_STRONG" in tags and "SECTOR_WEAK" in tags:
            # prefer weak if both
            tags = [t for t in tags if t != "SECTOR_STRONG"]

        checklist["板块"] = "✓" if score >= 60 else "⚠"

        res.ok = True
        res.data_insufficient = False
        res.score = score
        res.tags = tags
        res.notes = notes
        res.metrics = {
            "industry": industry,
            "concept": concept,
            "stock_return5": stock_r5,
            "stock_return20": stock_r20,
            "sector_return5": sec_r5,
            "sector_return20": sec_r20,
            "relative_strength": None if rel_strength is None else round(rel_strength, 3),
            "sector_ma_stack_share": sec_ma_share,
            "stock_ma_stack": stock_ma_stack,
            "peers_n": peer.get("n"),
            "peers_used": peer.get("peers_used"),
        }
        res.checklist = checklist
        res.source = f"{src}+peer_avg" if peer.get("ok") else src
        return res


__all__ = ["SectorEngine", "SectorResult"]
