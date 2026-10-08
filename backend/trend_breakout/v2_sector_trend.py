#!/usr/bin/env python3
"""
V2.3 SectorTrendEngine — peer equal-weight sector daily/monthly trends + breadth + RS20.

Wraps/calls existing SectorEngine for industry resolve + legacy SectorScore.
Caches sector daily series per (industry, t_date).
Does NOT explode concept boards for scoring.
Sector daily DOWN only tags / lowers RecommendationScore (not hard-filter).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from v2_sector import SectorEngine, LOCAL_DAILY
from v2_monthly import aggregate_daily_to_monthly, _slope_last

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


def _ma_val(close: pd.Series, n: int) -> Optional[float]:
    if close is None or len(close) < n:
        return None
    v = float(close.tail(n).mean())
    return v if np.isfinite(v) else None


def _level_from_score(score: Optional[float]) -> str:
    if score is None:
        return "NEUTRAL"
    if score >= 80:
        return "STRONG_UP"
    if score >= 60:
        return "UP"
    if score >= 40:
        return "NEUTRAL"
    return "DOWN"


_LEVEL_CN = {
    "STRONG_UP": "强势向上",
    "UP": "向上",
    "NEUTRAL": "中性",
    "DOWN": "向下",
}

_RS_BUCKET = [
    (8.0, "强势"),
    (3.0, "较强"),
    (0.0, "一般"),
    (-9999.0, "弱于板块"),
]


def _rs_bucket(rs: Optional[float]) -> str:
    if rs is None:
        return "一般"
    for thr, name in _RS_BUCKET:
        if rs >= thr:
            return name
    return "弱于板块"


@dataclass
class SectorTrendResult:
    ok: bool = False
    data_insufficient: bool = True
    name: Optional[str] = None  # industry name
    dailyTrend: str = "NEUTRAL"
    dailyTrendCn: str = "中性"
    dailyTrendScore: Optional[float] = None
    monthlyTrend: str = "NEUTRAL"
    monthlyTrendCn: str = "中性"
    monthlyTrendScore: Optional[float] = None
    breadthScore: Optional[float] = None
    relativeStrength20: Optional[float] = None
    relativeStrengthBucket: str = "一般"
    legacySectorScore: Optional[float] = None
    peers_n: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SectorTrendEngine:
    """Build sector EW daily series + daily/monthly trend scores + breadth + RS."""

    def __init__(self, sector_engine: Optional[SectorEngine] = None, max_peers: int = 40):
        self.sec_eng = sector_engine or SectorEngine()
        self.max_peers = max_peers
        # cache key: (industry, t_date) -> DataFrame with date,close,volume,amount,up_count,n,above_ma20_count
        self._series_cache: Dict[Tuple[str, str], pd.DataFrame] = {}
        self._close_cache: Dict[str, pd.DataFrame] = {}  # code6 -> OHLCV trimmed

    def bind_fundamental(self, fund_engine) -> None:
        self.sec_eng.bind_fundamental(fund_engine)

    def load(self) -> None:
        self.sec_eng.load()

    def _load_peer_ohlcv(self, code6: str, t_date: Optional[str]) -> Optional[pd.DataFrame]:
        if code6 in self._close_cache:
            df = self._close_cache[code6]
        else:
            p = LOCAL_DAILY / f"{code6}.parquet"
            if not p.exists():
                return None
            try:
                raw = pd.read_parquet(p, columns=["date", "open", "high", "low", "close", "volume", "amount"])
                raw["date"] = pd.to_datetime(raw["date"])
                raw = raw.sort_values("date")
                self._close_cache[code6] = raw
                df = raw
            except Exception:
                return None
        if t_date:
            df = df[df["date"] <= pd.Timestamp(t_date)]
        if len(df) < 25:
            return None
        return df

    def build_sector_daily(
        self,
        industry: str,
        t_date: Optional[str] = None,
        max_peers: Optional[int] = None,
    ) -> pd.DataFrame:
        """Equal-weight average close (+vol/amt) across peers; also breadth columns."""
        t_key = t_date or "ALL"
        cache_key = (industry, t_key)
        if cache_key in self._series_cache:
            return self._series_cache[cache_key]

        self.load()
        peers = list(self.sec_eng._peers.get(industry) or [])
        usable = [c for c in peers if (LOCAL_DAILY / f"{c}.parquet").exists()]
        mp = max_peers or self.max_peers
        if len(usable) > mp:
            usable = sorted(usable)[:mp]

        frames = []
        for c in usable:
            df = self._load_peer_ohlcv(c, t_date)
            if df is None:
                continue
            sub = df[["date", "close", "volume", "amount"]].copy()
            sub = sub.rename(
                columns={
                    "close": f"c_{c}",
                    "volume": f"v_{c}",
                    "amount": f"a_{c}",
                }
            )
            frames.append(sub.set_index("date"))

        if not frames:
            empty = pd.DataFrame()
            self._series_cache[cache_key] = empty
            return empty

        aligned = pd.concat(frames, axis=1, join="outer").sort_index()
        close_cols = [c for c in aligned.columns if c.startswith("c_")]
        vol_cols = [c for c in aligned.columns if c.startswith("v_")]
        amt_cols = [c for c in aligned.columns if c.startswith("a_")]

        close_mat = aligned[close_cols]
        # require at least 3 peers on a day
        n_avail = close_mat.notna().sum(axis=1)
        ew_close = close_mat.mean(axis=1, skipna=True)
        ew_vol = aligned[vol_cols].mean(axis=1, skipna=True) if vol_cols else None
        ew_amt = aligned[amt_cols].mean(axis=1, skipna=True) if amt_cols else None

        # ret1 > 0 breadth
        ret1 = close_mat.pct_change()
        up_count = (ret1 > 0).sum(axis=1)
        # above MA20 per peer (rolling on each)
        above = pd.DataFrame(index=close_mat.index)
        for col in close_cols:
            ma20 = close_mat[col].rolling(20, min_periods=15).mean()
            above[col] = close_mat[col] > ma20
        above_ma20_count = above.sum(axis=1)

        out = pd.DataFrame(
            {
                "date": ew_close.index,
                "close": ew_close.values,
                "n": n_avail.values,
                "up_count": up_count.values,
                "above_ma20_count": above_ma20_count.values,
            }
        )
        if ew_vol is not None:
            out["volume"] = ew_vol.values
        if ew_amt is not None:
            out["amount"] = ew_amt.values
        out = out[out["n"] >= 3].dropna(subset=["close"]).reset_index(drop=True)
        self._series_cache[cache_key] = out
        return out

    def _score_sector_daily(self, sec_df: pd.DataFrame) -> Tuple[Optional[float], Dict[str, Any], List[str]]:
        """
        SectorDailyTrendScore:
          MA结构25 + MA20斜率15 + MA60斜率10 + 价格位置15 + ret20 15 + 量能趋势10 + 内部上涨家数10
        """
        notes: List[str] = []
        if sec_df is None or len(sec_df) < 25:
            return None, {}, ["SECTOR_DAILY_INSUFFICIENT"]

        close = sec_df["close"].astype(float)
        c = float(close.iloc[-1])
        ma5 = _ma_val(close, 5)
        ma10 = _ma_val(close, 10)
        ma20 = _ma_val(close, 20)
        ma60 = _ma_val(close, 60) if len(close) >= 60 else None

        ma20_s = close.rolling(20, min_periods=15).mean()
        ma60_s = close.rolling(60, min_periods=40).mean() if len(close) >= 40 else None
        slope20 = _slope_last(ma20_s.dropna(), 5)
        slope60 = _slope_last(ma60_s.dropna(), 5) if ma60_s is not None else None

        ret20 = _ret_pct(close, 20)
        ret5 = _ret_pct(close, 5)

        # breadth today
        n = int(sec_df["n"].iloc[-1]) if "n" in sec_df.columns else 0
        up_n = int(sec_df["up_count"].iloc[-1]) if "up_count" in sec_df.columns else 0
        above_n = int(sec_df["above_ma20_count"].iloc[-1]) if "above_ma20_count" in sec_df.columns else 0
        pct_up = (up_n / n) if n > 0 else None
        pct_above = (above_n / n) if n > 0 else None
        breadth_score = None
        if pct_up is not None and pct_above is not None:
            breadth_score = round(100.0 * (0.5 * pct_up + 0.5 * pct_above), 1)
        elif pct_up is not None:
            breadth_score = round(100.0 * pct_up, 1)

        parts: Dict[str, float] = {}

        # MA structure 25
        struct = 8.0
        if ma5 is not None and ma10 is not None and ma20 is not None:
            if ma5 > ma10 > ma20:
                struct = 25.0 if (ma60 is None or ma20 > ma60) else 20.0
            elif ma5 > ma10:
                struct = 15.0
            elif ma5 < ma10 < ma20:
                struct = 3.0
            else:
                struct = 10.0
        parts["ma_structure"] = struct

        # MA20 slope 15
        s20 = 7.0
        if slope20 is not None:
            rel = slope20 / max(abs(c), 1e-6)
            if slope20 > 0:
                s20 = 15.0 if rel > 0.001 else 12.0
            elif slope20 < 0:
                s20 = 3.0 if rel < -0.001 else 6.0
            else:
                s20 = 8.0
        parts["ma20_slope"] = s20

        # MA60 slope 10
        s60 = 5.0
        if slope60 is not None:
            if slope60 >= 0:
                s60 = 10.0 if slope60 > 0 else 7.0
            else:
                s60 = 2.0
        else:
            notes.append("MA60不足，斜率维中性")
        parts["ma60_slope"] = s60

        # price position 15
        pos = 7.0
        if ma20 is not None:
            if c > ma20:
                if ma60 is not None and c > ma60:
                    pos = 15.0
                else:
                    pos = 12.0
            else:
                pos = 3.0 if (ma10 is not None and c < ma10) else 6.0
        parts["price_pos"] = pos

        # ret20 15
        r20s = 7.0
        if ret20 is not None:
            if ret20 >= 10:
                r20s = 15.0
            elif ret20 >= 5:
                r20s = 12.0
            elif ret20 >= 0:
                r20s = 9.0
            elif ret20 >= -5:
                r20s = 5.0
            else:
                r20s = 1.0
        parts["ret20"] = r20s

        # volume trend 10
        vols = 5.0
        if "volume" in sec_df.columns and len(sec_df) >= 20:
            vol = sec_df["volume"].astype(float)
            v5 = float(vol.tail(5).mean())
            v20 = float(vol.tail(20).mean())
            if v20 > 0:
                vr = v5 / v20
                if vr >= 1.2:
                    vols = 10.0
                elif vr >= 1.0:
                    vols = 8.0
                elif vr >= 0.8:
                    vols = 5.0
                else:
                    vols = 3.0
                parts["vol_ratio_5_20"] = round(vr, 3)
        else:
            notes.append("板块量能不足，量能维中性")
        parts["volume"] = vols

        # breadth 10
        br = 5.0
        if breadth_score is not None:
            if breadth_score >= 70:
                br = 10.0
            elif breadth_score >= 55:
                br = 8.0
            elif breadth_score >= 40:
                br = 5.0
            else:
                br = 2.0
        parts["breadth"] = br

        score = float(
            round(
                parts["ma_structure"]
                + parts["ma20_slope"]
                + parts["ma60_slope"]
                + parts["price_pos"]
                + parts["ret20"]
                + parts["volume"]
                + parts["breadth"],
                1,
            )
        )
        score = float(min(100.0, max(0.0, score)))

        metrics = {
            "close": round(c, 4),
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "ma20_slope": None if slope20 is None else round(slope20, 6),
            "ma60_slope": None if slope60 is None else round(slope60, 6),
            "ret20": None if ret20 is None else round(ret20, 3),
            "ret5": None if ret5 is None else round(ret5, 3),
            "peers_n": n,
            "pct_up": None if pct_up is None else round(pct_up, 4),
            "pct_above_ma20": None if pct_above is None else round(pct_above, 4),
            "breadth_score": breadth_score,
            "parts": parts,
            "notes": notes,
        }
        return score, metrics, notes

    def _classify_daily_trend(
        self, score: Optional[float], metrics: Dict[str, Any]
    ) -> str:
        """
        UP minimum: MA5>MA10 AND MA10>MA20 AND Close>MA20 AND MA20 slope>0
        STRONG_UP when more conditions and score>=80
        else from score buckets
        """
        if score is None:
            return "NEUTRAL"
        ma5, ma10, ma20, ma60 = metrics.get("ma5"), metrics.get("ma10"), metrics.get("ma20"), metrics.get("ma60")
        c = metrics.get("close")
        slope20 = metrics.get("ma20_slope")
        slope60 = metrics.get("ma60_slope")
        ret20 = metrics.get("ret20")

        up_min = (
            ma5 is not None
            and ma10 is not None
            and ma20 is not None
            and c is not None
            and slope20 is not None
            and ma5 > ma10
            and ma10 > ma20
            and c > ma20
            and slope20 > 0
        )
        strong_extra = (
            up_min
            and score >= 80
            and ma60 is not None
            and ma20 > ma60
            and (slope60 is None or slope60 >= 0)
            and c > ma60
            and (ret20 is None or ret20 > 0)
        )
        if strong_extra or (score >= 80 and up_min):
            return "STRONG_UP"
        if up_min or score >= 60:
            # if score says UP but structure fails, still allow UP by score;
            # if structure fails badly and score <40 → DOWN handled below
            if score < 40:
                return "DOWN"
            if up_min or score >= 60:
                return "UP" if score >= 60 else "NEUTRAL"
        return _level_from_score(score)

    def _score_sector_monthly(self, sec_daily: pd.DataFrame) -> Tuple[str, Optional[float], Dict[str, Any]]:
        """Simpler monthly analog of stock monthly."""
        if sec_daily is None or len(sec_daily) < 80:
            return "NEUTRAL", None, {"notes": ["板块日线不足，月线跳过"]}
        # build synthetic OHLCV for aggregator
        d = sec_daily.copy()
        if "open" not in d.columns:
            d["open"] = d["close"]
        if "high" not in d.columns:
            d["high"] = d["close"]
        if "low" not in d.columns:
            d["low"] = d["close"]
        monthly = aggregate_daily_to_monthly(d)
        n = len(monthly)
        if n < 6:
            return "NEUTRAL", None, {"n_months": n, "notes": ["板块月线不足"]}

        close = monthly["close"].astype(float)
        c = float(close.iloc[-1])
        ma3 = close.rolling(3, min_periods=3).mean()
        ma5 = close.rolling(5, min_periods=5).mean()
        ma10 = close.rolling(10, min_periods=8).mean()
        v_ma3 = float(ma3.iloc[-1]) if pd.notna(ma3.iloc[-1]) else None
        v_ma5 = float(ma5.iloc[-1]) if pd.notna(ma5.iloc[-1]) else None
        v_ma10 = float(ma10.iloc[-1]) if n >= 8 and pd.notna(ma10.iloc[-1]) else None
        s_ma5 = _slope_last(ma5.dropna(), 3)
        s_ma10 = _slope_last(ma10.dropna(), 3) if n >= 10 else None

        trend = "NEUTRAL"
        if v_ma5 is not None and s_ma5 is not None:
            strong = (
                v_ma3 is not None
                and v_ma10 is not None
                and v_ma3 > v_ma5 > v_ma10
                and s_ma5 > 0
                and (s_ma10 is not None and s_ma10 > 0)
                and c > v_ma5
            )
            up = v_ma3 is not None and v_ma3 > v_ma5 and c > v_ma5 and s_ma5 > 0
            down = c < v_ma5 and s_ma5 < 0
            if strong:
                trend = "STRONG_UP"
            elif up:
                trend = "UP"
            elif down:
                trend = "DOWN"

        # simpler score 0-100
        score = 50.0
        if trend == "STRONG_UP":
            score = 85.0
        elif trend == "UP":
            score = 70.0
        elif trend == "DOWN":
            score = 30.0
        if s_ma5 is not None and s_ma5 > 0:
            score += 5
        if v_ma10 is not None and v_ma5 is not None and v_ma5 > v_ma10:
            score += 5
        if n >= 12:
            base = float(close.iloc[-12])
            if base > 0:
                r12 = (c / base - 1) * 100
                if r12 >= 20:
                    score += 8
                elif r12 >= 0:
                    score += 3
                elif r12 < -15:
                    score -= 8
        score = float(min(100.0, max(0.0, round(score, 1))))
        # map to MONTHLY_* style externally
        return trend, score, {
            "n_months": n,
            "close": round(c, 4),
            "ma3": v_ma3,
            "ma5": v_ma5,
            "ma10": v_ma10,
            "ma5_slope": s_ma5,
        }

    def evaluate(
        self,
        code: str,
        hist: Optional[pd.DataFrame] = None,
        t_date: Optional[str] = None,
        legacy_sector: Optional[Dict[str, Any]] = None,
    ) -> SectorTrendResult:
        self.load()
        code6 = _norm_code(code)
        res = SectorTrendResult()

        industry, src = self.sec_eng.resolve_industry(code6)
        res.name = industry
        if not industry:
            res.data_insufficient = True
            res.tags = ["DATA_INSUFFICIENT"]
            res.notes = ["无行业分类"]
            return res

        # legacy SectorScore
        if legacy_sector and legacy_sector.get("score") is not None:
            res.legacySectorScore = legacy_sector.get("score")
        else:
            try:
                lr = self.sec_eng.evaluate(code6, hist=hist, t_date=t_date)
                res.legacySectorScore = lr.score
            except Exception:
                pass

        sec_df = self.build_sector_daily(industry, t_date=t_date)
        if sec_df is None or len(sec_df) < 25:
            res.data_insufficient = True
            res.ok = False
            res.tags = ["DATA_INSUFFICIENT", "SECTOR_DAILY_INSUFFICIENT"]
            res.notes = ["板块日线序列不足（同行本地行情稀疏）"]
            # still try RS from legacy metrics
            if legacy_sector and (legacy_sector.get("metrics") or {}).get("relative_strength") is not None:
                rs = legacy_sector["metrics"]["relative_strength"]
                res.relativeStrength20 = rs
                res.relativeStrengthBucket = _rs_bucket(rs)
            return res

        daily_score, d_metrics, d_notes = self._score_sector_daily(sec_df)
        daily_trend = self._classify_daily_trend(daily_score, d_metrics)
        # enforce score buckets as primary level when structure ambiguous
        bucket = _level_from_score(daily_score)
        # Prefer STRONG_UP/UP from classifier when earned; else bucket
        if daily_trend == "STRONG_UP":
            pass
        elif daily_trend == "UP" and bucket == "DOWN":
            daily_trend = "NEUTRAL"
        elif daily_trend not in ("STRONG_UP", "UP", "DOWN"):
            daily_trend = bucket
        elif bucket == "STRONG_UP" and daily_trend == "UP":
            # keep UP unless strong extras already set
            pass

        res.dailyTrendScore = daily_score
        res.dailyTrend = daily_trend
        res.dailyTrendCn = _LEVEL_CN.get(daily_trend, daily_trend)
        res.breadthScore = (d_metrics or {}).get("breadth_score")
        res.peers_n = int((d_metrics or {}).get("peers_n") or 0)

        # monthly
        m_trend, m_score, m_metrics = self._score_sector_monthly(sec_df)
        res.monthlyTrend = m_trend
        res.monthlyTrendCn = _LEVEL_CN.get(m_trend, m_trend)
        res.monthlyTrendScore = m_score

        # RS20 = stock20 - sector20
        stock_r20 = None
        if hist is not None and len(hist) >= 25 and "close" in hist.columns:
            h = hist.copy()
            if "date" in h.columns:
                h["date"] = pd.to_datetime(h["date"])
                h = h.sort_values("date")
                if t_date:
                    h = h[h["date"] <= pd.Timestamp(t_date)]
            stock_r20 = _ret_pct(h["close"].astype(float).reset_index(drop=True), 20)
        else:
            pdf = self._load_peer_ohlcv(code6, t_date)
            if pdf is not None:
                stock_r20 = _ret_pct(pdf["close"].astype(float).reset_index(drop=True), 20)

        sec_r20 = (d_metrics or {}).get("ret20")
        rs20 = None
        if stock_r20 is not None and sec_r20 is not None:
            rs20 = float(stock_r20) - float(sec_r20)
        elif legacy_sector and (legacy_sector.get("metrics") or {}).get("relative_strength") is not None:
            rs20 = float(legacy_sector["metrics"]["relative_strength"])

        res.relativeStrength20 = None if rs20 is None else round(rs20, 3)
        res.relativeStrengthBucket = _rs_bucket(rs20)

        tags: List[str] = []
        if daily_trend == "STRONG_UP":
            tags.append("SECTOR_DAILY_STRONG")
        elif daily_trend == "UP":
            tags.append("SECTOR_DAILY_UP")
        elif daily_trend == "DOWN":
            tags.append("SECTOR_DAILY_DOWN")
        if m_trend in ("UP", "STRONG_UP"):
            tags.append("SECTOR_MONTHLY_UP")
        elif m_trend == "DOWN":
            tags.append("SECTOR_MONTHLY_DOWN")
        if rs20 is not None and rs20 >= 3:
            tags.append("SECTOR_LEADER")

        res.ok = True
        res.data_insufficient = False
        res.tags = tags
        res.notes = list(d_notes or []) + list((m_metrics or {}).get("notes") or [])
        res.metrics = {
            "industry": industry,
            "industry_source": src,
            "daily": d_metrics,
            "monthly": m_metrics,
            "stock_return20": None if stock_r20 is None else round(stock_r20, 3),
            "sector_return20": sec_r20,
            "relative_strength20": res.relativeStrength20,
            "relative_strength_bucket": res.relativeStrengthBucket,
            "legacy_sector_score": res.legacySectorScore,
            "peers_n": res.peers_n,
        }
        return res


__all__ = ["SectorTrendEngine", "SectorTrendResult"]
