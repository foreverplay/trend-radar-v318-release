"""
DividendTechnicalEngine — V3.1.9 Phase7.

DividendTrendScore 0–100 from long MA structure on **qfq** daily bars.
Cash TotalReturn stays on bfq+events (see PHASE2_TR_SPEC); this engine is
structure-only and must NOT add cash onto qfq.

Allows slow grind / long sideways; persistent downtrends cannot score high.
Weights (待回测校准): long MA 25 / mid trend 20 / drawdown 20 /
relative industry 15 / vol stability 10 / new-high capacity 10.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIVIDEND_CONFIG, KLINE_DAILY, DividendConfig
from dividend_v319.models.dividendModels import DataStatus

def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


@dataclass
class TechnicalResult:
    symbol: str
    as_of: str
    dividend_trend_score: Optional[float]
    components: Dict[str, Optional[float]] = field(default_factory=dict)
    close: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma120: Optional[float] = None
    ma250: Optional[float] = None
    protection_price: Optional[float] = None
    trend_break_price: Optional[float] = None
    price_return_1y_qfq: Optional[float] = None
    max_drawdown_1y: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    data_status: str = DataStatus.DATA_INSUFFICIENT.value
    missing_components: List[str] = field(default_factory=list)
    price_source: str = "qfq_daily"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DividendTechnicalEngine:
    """Trend structure on qfq; documented separation from TR cash口径."""

    WEIGHTS = {
        "long_ma": 0.25,
        "mid_trend": 0.20,
        "drawdown": 0.20,
        "rel_strength": 0.15,
        "vol_stability": 0.10,
        "new_high": 0.10,
    }

    def __init__(
        self,
        cfg: Optional[DividendConfig] = None,
        kline_dir: Path = KLINE_DAILY,
    ):
        self.cfg = cfg or DIVIDEND_CONFIG
        self.kline_dir = kline_dir
        self._industry_ret_1y: Dict[str, List[float]] = {}

    def register_industry_return(self, industry_group: str, ret_1y: Optional[float]) -> None:
        if ret_1y is None or not np.isfinite(ret_1y):
            return
        self._industry_ret_1y.setdefault(industry_group or "其他", []).append(float(ret_1y))

    def _load(self, code: str) -> Optional[pd.DataFrame]:
        path = self.kline_dir / f"{_z6(code)}.parquet"
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
        except Exception:
            return None
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        close = pd.to_numeric(df["close"], errors="coerce")
        for n, col in ((20, "MA20"), (60, "MA60"), (120, "MA120"), (250, "MA250")):
            if col not in df.columns or df[col].isna().all():
                df[col] = close.rolling(n, min_periods=n).mean()
        if "ATR14" not in df.columns or df["ATR14"].isna().all():
            if {"high", "low", "close"}.issubset(df.columns):
                high = pd.to_numeric(df["high"], errors="coerce")
                low = pd.to_numeric(df["low"], errors="coerce")
                prev_close = close.shift(1)
                tr = pd.concat(
                    [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
                    axis=1,
                ).max(axis=1)
                df["ATR14"] = tr.rolling(14, min_periods=14).mean()
            else:
                df["ATR14"] = np.nan
        return df

    def score(
        self,
        code: str,
        as_of: str,
        *,
        industry_group: str = "其他",
        industry_median_1y: Optional[float] = None,
    ) -> TechnicalResult:
        code = _z6(code)
        t = pd.Timestamp(as_of).normalize()
        missing: List[str] = []
        reasons: List[str] = []
        df = self._load(code)
        if df is None or df.empty:
            return TechnicalResult(
                symbol=code,
                as_of=as_of,
                dividend_trend_score=None,
                data_status=DataStatus.DATA_INSUFFICIENT.value,
                missing_components=["qfq_bars"],
                reasons=["缺少前复权日线，技术分不可用"],
            )

        sub = df[df["date"] <= t]
        if len(sub) < 60:
            return TechnicalResult(
                symbol=code,
                as_of=as_of,
                dividend_trend_score=None,
                data_status=DataStatus.DATA_INSUFFICIENT.value,
                missing_components=["short_history"],
                reasons=[f"as_of前可用交易日仅{len(sub)}，不足技术评分"],
            )

        row = sub.iloc[-1]
        close = float(row["close"])
        ma20 = float(row["MA20"]) if pd.notna(row.get("MA20")) else None
        ma60 = float(row["MA60"]) if pd.notna(row.get("MA60")) else None
        ma120 = float(row["MA120"]) if pd.notna(row.get("MA120")) else None
        ma250 = float(row["MA250"]) if pd.notna(row.get("MA250")) else None

        # 1y price return qfq
        start_1y = t - pd.DateOffset(years=1)
        past = sub[sub["date"] <= start_1y]
        if past.empty:
            past = sub.iloc[:1]
            missing.append("1y_price_anchor_short")
        p0 = float(past.iloc[-1]["close"])
        ret_1y = (close / p0 - 1.0) if p0 > 0 else None

        # max drawdown last ~252 bars
        win = sub.tail(252)
        peak = win["close"].cummax()
        dd = (win["close"] / peak - 1.0).min()
        max_dd = float(dd) if pd.notna(dd) else None

        # --- component scores ---
        # long MA structure
        long_score = 50.0
        if ma120 is None or ma250 is None:
            missing.append("ma_long")
            long_score = 50.0
        else:
            pts = 40.0
            if ma120 > ma250:
                pts += 25.0
                reasons.append("MA120位于MA250上方，长期结构偏多")
            else:
                pts -= 15.0
                reasons.append("MA120低于MA250，长期结构偏弱")
            # slope of MA250 over ~60 bars
            if len(sub) >= 60 and pd.notna(sub.iloc[-60].get("MA250")):
                ma250_prev = float(sub.iloc[-60]["MA250"])
                if ma250_prev > 0:
                    slope = ma250 / ma250_prev - 1.0
                    if slope > 0.02:
                        pts += 20.0
                        reasons.append("MA250缓步上行")
                    elif slope < -0.02:
                        pts -= 20.0
                        reasons.append("MA250下行")
                    else:
                        pts += 8.0
                        reasons.append("MA250近似走平（允许横盘）")
            if close >= ma250:
                pts += 15.0
            else:
                pts -= 20.0
                reasons.append("收盘低于MA250")
            long_score = _clip(pts)

        # mid trend
        mid_score = 50.0
        if ma20 is None or ma60 is None:
            missing.append("ma_mid")
        else:
            pts = 45.0
            if close >= ma60:
                pts += 20.0
            else:
                pts -= 15.0
            if ma20 >= ma60:
                pts += 15.0
            else:
                pts -= 5.0  # short dip under MA20 ok for红利
            if ret_1y is not None:
                if ret_1y > 0.05:
                    pts += 15.0
                elif ret_1y > -0.05:
                    pts += 5.0  # sideways ok
                elif ret_1y > -0.20:
                    pts -= 15.0
                else:
                    pts -= 30.0
                    reasons.append("近1年跌幅较大，中期趋势承压")
            mid_score = _clip(pts)

        # drawdown control
        dd_score = 50.0
        if max_dd is None:
            missing.append("drawdown")
        else:
            # milder drawdowns score higher for dividend names
            if max_dd > -0.10:
                dd_score = 90.0
            elif max_dd > -0.20:
                dd_score = 75.0
            elif max_dd > -0.35:
                dd_score = 55.0
            elif max_dd > -0.50:
                dd_score = 35.0
            else:
                dd_score = 15.0
                reasons.append(f"近一年最大回撤约{max_dd:.1%}，波动偏大")

        # relative strength vs industry median (if provided)
        rel_score = 50.0
        if ret_1y is None:
            missing.append("rel_strength")
        elif industry_median_1y is None:
            missing.append("industry_median")
            rel_score = 50.0
        else:
            diff = ret_1y - industry_median_1y
            if diff > 0.10:
                rel_score = 85.0
                reasons.append("近1年相对行业偏强")
            elif diff > 0.0:
                rel_score = 70.0
            elif diff > -0.10:
                rel_score = 50.0
            elif diff > -0.20:
                rel_score = 35.0
            else:
                rel_score = 20.0
                reasons.append("近1年显著弱于行业中位")

        # vol stability via ATR14/close
        vol_score = 50.0
        atr = row.get("ATR14")
        if pd.isna(atr) or close <= 0:
            missing.append("atr")
        else:
            atr_pct = float(atr) / close
            if atr_pct < 0.015:
                vol_score = 90.0
            elif atr_pct < 0.025:
                vol_score = 75.0
            elif atr_pct < 0.04:
                vol_score = 55.0
            elif atr_pct < 0.06:
                vol_score = 35.0
            else:
                vol_score = 20.0
                reasons.append("波动偏高，不符典型红利慢牛特征")

        # new high capacity: distance to 250d high
        nh_score = 50.0
        hi_250 = float(win["close"].max()) if len(win) else None
        if hi_250 is None or hi_250 <= 0:
            missing.append("new_high")
        else:
            dist = close / hi_250 - 1.0
            if dist > -0.02:
                nh_score = 88.0
                reasons.append("接近或创近一年高位")
            elif dist > -0.10:
                nh_score = 70.0
            elif dist > -0.25:
                nh_score = 50.0
            elif dist > -0.40:
                nh_score = 30.0
            else:
                nh_score = 15.0

        comps = {
            "long_ma": long_score,
            "mid_trend": mid_score,
            "drawdown": dd_score,
            "rel_strength": rel_score,
            "vol_stability": vol_score,
            "new_high": nh_score,
        }
        score = 0.0
        wsum = 0.0
        for k, w in self.WEIGHTS.items():
            v = comps.get(k)
            if v is None:
                continue
            score += w * float(v)
            wsum += w
        if wsum <= 0:
            final = None
            status = DataStatus.DATA_INSUFFICIENT.value
        else:
            final = round(score / wsum * (wsum / sum(self.WEIGHTS.values())), 2)
            # renormalize already by wsum
            final = round(_clip(score / wsum), 2)
            # hard cap: severe 1y decline cannot be "优质趋势"
            if ret_1y is not None and ret_1y < -0.25 and final > 55:
                final = 55.0
                reasons.append("近1年跌超25%，趋势分上限压至55")
            status = (
                DataStatus.DATA_INSUFFICIENT.value
                if len(missing) >= 3
                else DataStatus.VALID.value
            )

        # protection / trend-break helpers (not trading advice)
        protection = ma60
        trend_break = ma250 if ma250 is not None else ma120

        return TechnicalResult(
            symbol=code,
            as_of=as_of,
            dividend_trend_score=final,
            components=comps,
            close=close,
            ma20=ma20,
            ma60=ma60,
            ma120=ma120,
            ma250=ma250,
            protection_price=protection,
            trend_break_price=trend_break,
            price_return_1y_qfq=ret_1y,
            max_drawdown_1y=max_dd,
            reasons=reasons,
            data_status=status,
            missing_components=missing,
            price_source="qfq_daily",
        )


def run(*args, **kwargs):
    return DividendTechnicalEngine().score(*args, **kwargs)
