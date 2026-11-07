"""
DividendValuationEngine — V3.1.9 Phase8.

ValuationScore 0–100 from PE_TTM / PB / dividend yields + own-history percentiles
and coarse industry groups. Missing inputs → neutral component + DATA mark.
High yield alone must NOT score high when price/profit/cash deteriorate
(DividendYieldTrap heuristic).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import (
    DIVIDEND_CONFIG,
    UNIVERSE_NON_ST,
    EVENTS_PATH,
    KLINE_DAILY,
    DividendConfig,
)
from dividend_v319.engines.fin_pit_store import annual_by_code, load_lrb_annual, load_yjbb_annual
from dividend_v319.models.dividendModels import DataStatus

def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def map_industry_group(industry_name: Optional[str]) -> str:
    """Coarse groups: 银行 / 煤炭 / 公用 / 交运 / 其他."""
    if industry_name is None or (isinstance(industry_name, float) and np.isnan(industry_name)):
        return "其他"
    s = str(industry_name).strip()
    if s.lower() == "nan":
        return "其他"
    if not s:
        return "其他"
    if "银行" in s:
        return "银行"
    if "煤炭" in s or "焦炭" in s:
        return "煤炭"
    if any(k in s for k in ("电力", "燃气", "水务", "环保", "环境治理")):
        return "公用"
    if any(
        k in s
        for k in (
            "铁路",
            "公路",
            "港口",
            "航运",
            "机场",
            "航空",
            "物流",
            "公交",
            "运输",
        )
    ):
        return "交运"
    return "其他"


@dataclass
class ValuationResult:
    symbol: str
    as_of: str
    valuation_score: Optional[float]
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    dividend_yield_ttm: Optional[float] = None
    dividend_yield_3y_avg: Optional[float] = None
    dividend_yield_5y_avg: Optional[float] = None
    pe_percentile: Optional[float] = None  # 0–100, lower PE → lower pct (cheaper)
    pb_percentile: Optional[float] = None
    yield_percentile: Optional[float] = None  # higher yield → higher pct
    industry_name: Optional[str] = None
    industry_group: str = "其他"
    industry_yield_median: Optional[float] = None
    dividend_yield_trap: bool = False
    trap_flags: List[str] = field(default_factory=list)
    components: Dict[str, Optional[float]] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    data_status: str = DataStatus.DATA_INSUFFICIENT.value
    missing_components: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DividendValuationEngine:
    def __init__(
        self,
        cfg: Optional[DividendConfig] = None,
        events_path: Path = EVENTS_PATH,
        kline_dir: Path = KLINE_DAILY,
    ):
        self.cfg = cfg or DIVIDEND_CONFIG
        self.events_path = events_path
        self.kline_dir = kline_dir
        self._events: Optional[pd.DataFrame] = None
        self._yjbb = None
        self._lrb = None
        self._industry_map: Dict[str, str] = {}
        self._industry_name: Dict[str, str] = {}
        self._yield_by_group: Dict[str, List[float]] = {}
        self._px_cache: Dict[str, pd.DataFrame] = {}

    def _ensure(self) -> None:
        if self._events is None:
            ev = pd.read_parquet(self.events_path)
            ev["code"] = ev["code"].map(_z6)
            for c in ("ex_dividend_date", "disclose_date"):
                if c in ev.columns:
                    ev[c] = pd.to_datetime(ev[c], errors="coerce").dt.normalize()
            ev["cash_per_share"] = pd.to_numeric(ev["cash_per_share"], errors="coerce")
            self._events = ev
        if self._yjbb is None:
            self._yjbb = load_yjbb_annual()
            self._lrb = load_lrb_annual()
        if not self._industry_map:
            # prefer fin meta universe
            meta = UNIVERSE_NON_ST
            if meta.exists():
                u = pd.read_csv(meta, dtype=str)
                for _, r in u.iterrows():
                    code = _z6(r["SECURITY_CODE"])
                    ind = r.get("INDUSTRY_NAME") or ""
                    self._industry_name[code] = ind
                    self._industry_map[code] = map_industry_group(ind)
            # fallback from lrb latest industry
            if self._lrb is not None and not self._lrb.empty:
                tmp = self._lrb.sort_values("period").drop_duplicates(
                    "SECURITY_CODE", keep="last"
                )
                for _, r in tmp.iterrows():
                    code = _z6(r["SECURITY_CODE"])
                    if code not in self._industry_name or not self._industry_name[code]:
                        ind = r.get("INDUSTRY_NAME") or ""
                        self._industry_name[code] = str(ind)
                        self._industry_map[code] = map_industry_group(str(ind))

    def industry_group(self, code: str) -> str:
        self._ensure()
        return self._industry_map.get(_z6(code), "其他")

    def industry_name(self, code: str) -> str:
        self._ensure()
        return self._industry_name.get(_z6(code), "")

    def _px_df(self, code: str) -> Optional[pd.DataFrame]:
        code = _z6(code)
        if code in self._px_cache:
            return self._px_cache[code]
        path = self.kline_dir / f"{code}.parquet"
        if not path.exists():
            self._px_cache[code] = None  # type: ignore
            return None
        df = pd.read_parquet(path, columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.dropna(subset=["close"]).sort_values("date")
        self._px_cache[code] = df
        return df

    def _price_on(self, code: str, as_of: str) -> Optional[Tuple[float, pd.Timestamp]]:
        df = self._px_df(code)
        if df is None or df.empty:
            return None
        t = pd.Timestamp(as_of).normalize()
        sub = df[df["date"] <= t]
        if sub.empty:
            return None
        row = sub.iloc[-1]
        return float(row["close"]), pd.Timestamp(row["date"])

    def _cash_ttm(self, code: str, as_of: str) -> float:
        self._ensure()
        t = pd.Timestamp(as_of).normalize()
        start = t - pd.DateOffset(years=1)
        ev = self._events
        ok = set(self.cfg.assign_progress_ok)
        sub = ev[
            (ev["code"] == _z6(code))
            & (ev["assign_progress"].isin(ok))
            & (ev["cash_per_share"].fillna(0) > 0)
            & (ev["ex_dividend_date"].notna())
            & (ev["ex_dividend_date"] > start)
            & (ev["ex_dividend_date"] <= t)
            & (ev["disclose_date"].isna() | (ev["disclose_date"] <= t))
        ]
        return float(sub["cash_per_share"].sum()) if len(sub) else 0.0

    def _avg_yield_ny(
        self, code: str, as_of: str, n_years: int, price: float
    ) -> Optional[float]:
        """Average of trailing yearly cash/price using as_of price (simple proxy)."""
        if price <= 0:
            return None
        self._ensure()
        t = pd.Timestamp(as_of).normalize()
        ok = set(self.cfg.assign_progress_ok)
        yields = []
        for i in range(n_years):
            end = t - pd.DateOffset(years=i)
            start = end - pd.DateOffset(years=1)
            sub = self._events[
                (self._events["code"] == _z6(code))
                & (self._events["assign_progress"].isin(ok))
                & (self._events["cash_per_share"].fillna(0) > 0)
                & (self._events["ex_dividend_date"].notna())
                & (self._events["ex_dividend_date"] > start)
                & (self._events["ex_dividend_date"] <= end)
                & (
                    self._events["disclose_date"].isna()
                    | (self._events["disclose_date"] <= t)
                )
            ]
            cash = float(sub["cash_per_share"].sum()) if len(sub) else 0.0
            # price at each year-end for better avg
            px = self._price_on(code, str(end.date()))
            p = px[0] if px else price
            if p > 0 and cash > 0:
                yields.append(cash / p)
        if not yields:
            return None
        return float(np.mean(yields))

    def _hist_pe_pb(
        self, code: str, as_of: str
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], List[float], List[float]]:
        """
        Current PE/PB and percentiles among year-end snapshots (PIT EPS/BPS).
        Percentile: fraction of history with PE >= current PE → cheap if high? 
        Plan: 当前PB历史百分位=25% means PB lower than ~75% of history → cheap.
        We store percentile as 0-100 where **lower = cheaper** (rank of current in ascending series).
        """
        self._ensure()
        t = pd.Timestamp(as_of).normalize()
        yj = annual_by_code(self._yjbb, code, as_of)
        if yj.empty:
            return None, None, None, None, [], []

        pe_hist: List[float] = []
        pb_hist: List[float] = []
        # year-end anchors last 8 calendar years including as_of year
        years = list(range(t.year - 7, t.year + 1))
        for y in years:
            # use Dec 31 of y, but not after as_of
            end = min(pd.Timestamp(f"{y}-12-31"), t)
            px = self._price_on(code, str(end.date()))
            if px is None:
                continue
            price, _ = px
            # latest annual EPS/BPS with NOTICE_DATE <= end
            yj_end = yj[yj["NOTICE_DATE"] <= end]
            if yj_end.empty:
                continue
            last = yj_end.iloc[-1]
            eps = pd.to_numeric(last.get("BASIC_EPS"), errors="coerce")
            bps = pd.to_numeric(last.get("BPS"), errors="coerce")
            if pd.notna(eps) and float(eps) > 0 and price > 0:
                pe_hist.append(price / float(eps))
            if pd.notna(bps) and float(bps) > 0 and price > 0:
                pb_hist.append(price / float(bps))

        # current
        px_now = self._price_on(code, as_of)
        if px_now is None:
            return None, None, None, None, pe_hist, pb_hist
        price_now = px_now[0]
        last = yj.iloc[-1]
        eps = pd.to_numeric(last.get("BASIC_EPS"), errors="coerce")
        bps = pd.to_numeric(last.get("BPS"), errors="coerce")
        pe = (price_now / float(eps)) if pd.notna(eps) and float(eps) > 0 else None
        pb = (price_now / float(bps)) if pd.notna(bps) and float(bps) > 0 else None

        def pct_rank(series: List[float], cur: Optional[float]) -> Optional[float]:
            if cur is None or len(series) < 3:
                return None
            arr = np.array(series, dtype=float)
            # percentile of current in ascending distribution
            return float((arr <= cur).mean() * 100.0)

        return pe, pb, pct_rank(pe_hist, pe), pct_rank(pb_hist, pb), pe_hist, pb_hist

    def score(
        self,
        code: str,
        as_of: str,
        *,
        price_return_1y_qfq: Optional[float] = None,
        profit_deteriorating: bool = False,
        cash_deteriorating: bool = False,
        industry_yield_median: Optional[float] = None,
    ) -> ValuationResult:
        self._ensure()
        code = _z6(code)
        missing: List[str] = []
        reasons: List[str] = []
        ind_name = self.industry_name(code)
        ind_grp = self.industry_group(code)

        px = self._price_on(code, as_of)
        if px is None:
            return ValuationResult(
                symbol=code,
                as_of=as_of,
                valuation_score=None,
                industry_name=ind_name,
                industry_group=ind_grp,
                data_status=DataStatus.DATA_INSUFFICIENT.value,
                missing_components=["price"],
                reasons=["缺少as_of价格，估值不可用"],
            )
        price, _ = px
        cash_ttm = self._cash_ttm(code, as_of)
        y_ttm = (cash_ttm / price) if price > 0 else None
        y3 = self._avg_yield_ny(code, as_of, 3, price)
        y5 = self._avg_yield_ny(code, as_of, 5, price)

        pe, pb, pe_pct, pb_pct, pe_hist, pb_hist = self._hist_pe_pb(code, as_of)
        if pe is None:
            missing.append("pe_ttm")
        if pb is None:
            missing.append("pb")
        if y_ttm is None or y_ttm <= 0:
            missing.append("dividend_yield_ttm")

        # yield own-history percentile (higher yield = higher percentile)
        yield_pct = None
        if y_ttm and y_ttm > 0:
            # build crude hist yields at year ends
            t = pd.Timestamp(as_of).normalize()
            yh = []
            for y in range(t.year - 7, t.year + 1):
                end = min(pd.Timestamp(f"{y}-12-31"), t)
                px_e = self._price_on(code, str(end.date()))
                if not px_e:
                    continue
                c = self._cash_ttm(code, str(end.date()))
                if px_e[0] > 0 and c > 0:
                    yh.append(c / px_e[0])
            if len(yh) >= 3:
                yield_pct = float((np.array(yh) <= y_ttm).mean() * 100.0)

        # component scores
        # NOTE: own-history PE/PB using qfq year-end prices is distorted (past qfq
        # prices pulled down by later dividends). Dampen hist percentile and
        # lean on absolute industry bands + yield.
        def _abs_pe_score(pe_v: Optional[float], grp: str) -> float:
            if pe_v is None or pe_v <= 0:
                return 50.0
            bands = {
                "银行": (4.0, 8.0, 12.0),
                "煤炭": (5.0, 10.0, 16.0),
                "公用": (10.0, 18.0, 28.0),
                "交运": (8.0, 15.0, 25.0),
                "其他": (8.0, 20.0, 40.0),
            }
            lo, mid, hi = bands.get(grp, bands["其他"])
            if pe_v <= lo:
                return 85.0
            if pe_v <= mid:
                return 70.0
            if pe_v <= hi:
                return 50.0
            if pe_v <= hi * 1.5:
                return 35.0
            return 20.0

        def _abs_pb_score(pb_v: Optional[float], grp: str) -> float:
            if pb_v is None or pb_v <= 0:
                return 50.0
            bands = {
                "银行": (0.35, 0.70, 1.10),
                "煤炭": (0.70, 1.20, 2.00),
                "公用": (0.80, 1.50, 2.50),
                "交运": (0.70, 1.30, 2.20),
                "其他": (0.80, 2.00, 4.00),
            }
            lo, mid, hi = bands.get(grp, bands["其他"])
            if pb_v <= lo:
                return 88.0
            if pb_v <= mid:
                return 72.0
            if pb_v <= hi:
                return 52.0
            if pb_v <= hi * 1.4:
                return 35.0
            return 20.0

        pe_abs = _abs_pe_score(pe, ind_grp)
        pb_abs = _abs_pb_score(pb, ind_grp)
        pe_hist_s = 50.0
        if pe_pct is None:
            if pe is not None:
                missing.append("pe_percentile")
            pe_hist_s = 50.0
        else:
            # dampen: 50 + 0.35*((100-pct)-50)
            pe_hist_s = _clip(50.0 + 0.35 * ((100.0 - pe_pct) - 50.0))
            reasons.append(
                f"PE自身历史分位约{pe_pct:.0f}%（qfq历史价有扭曲，仅弱参考）"
            )
        pb_hist_s = 50.0
        if pb_pct is None:
            missing.append("pb_percentile")
            pb_hist_s = 50.0
        else:
            pb_hist_s = _clip(50.0 + 0.35 * ((100.0 - pb_pct) - 50.0))
            reasons.append(
                f"PB自身历史分位约{pb_pct:.0f}%（qfq历史价有扭曲，仅弱参考）"
            )
        pe_score = _clip(0.65 * pe_abs + 0.35 * pe_hist_s)
        pb_score = _clip(0.65 * pb_abs + 0.35 * pb_hist_s)
        if pe is not None:
            reasons.append(f"PE≈{pe:.1f}（行业组绝对带评分{pe_abs:.0f}）")
        if pb is not None:
            reasons.append(f"PB≈{pb:.2f}（行业组绝对带评分{pb_abs:.0f}）")

        # yield score: moderate high is good; extreme high suspicious (trap handled separately)
        y_score = 50.0
        if y_ttm is None or y_ttm <= 0:
            y_score = 50.0
        else:
            # 2–6% sweet; >10% caution unless supported
            y_pct_pts = y_ttm * 100.0
            if 2.0 <= y_pct_pts <= 6.0:
                y_score = 80.0
            elif 1.0 <= y_pct_pts < 2.0:
                y_score = 60.0
            elif 6.0 < y_pct_pts <= 9.0:
                y_score = 70.0
            elif y_pct_pts > 9.0:
                y_score = 55.0  # not automatic high
                reasons.append(f"TTM股息率约{y_pct_pts:.1f}%，需结合可持续性防陷阱")
            else:
                y_score = 40.0
            if industry_yield_median is not None and industry_yield_median > 0:
                if y_ttm >= industry_yield_median * 1.2:
                    y_score = min(100.0, y_score + 8.0)
                    reasons.append("股息率高于行业组中位")
                elif y_ttm < industry_yield_median * 0.7:
                    y_score = max(0.0, y_score - 10.0)

        # vs own yield history: high percentile + not trap → mild bonus
        yh_score = 50.0
        if yield_pct is not None:
            if yield_pct >= 70:
                yh_score = 70.0
            elif yield_pct <= 30:
                yh_score = 40.0
            else:
                yh_score = 55.0

        comps = {
            "pe": pe_score,
            "pb": pb_score,
            "yield_level": y_score,
            "yield_history": yh_score,
        }
        # weights 待回测校准
        w = {"pe": 0.30, "pb": 0.30, "yield_level": 0.25, "yield_history": 0.15}
        score = sum(w[k] * comps[k] for k in w)
        final = round(_clip(score), 2)

        # DividendYieldTrap heuristic
        trap = False
        trap_flags: List[str] = []
        high_yield = y_ttm is not None and y_ttm >= 0.05
        if industry_yield_median is not None and y_ttm is not None:
            if y_ttm >= max(0.05, industry_yield_median * 1.5):
                high_yield = True
        price_down = price_return_1y_qfq is not None and price_return_1y_qfq <= -0.15
        if high_yield and price_down:
            trap_flags.append("high_yield_with_price_decline")
        if high_yield and profit_deteriorating:
            trap_flags.append("high_yield_with_profit_deterioration")
        if high_yield and cash_deteriorating:
            trap_flags.append("high_yield_with_cash_deterioration")
        if (
            "high_yield_with_price_decline" in trap_flags
            and (
                "high_yield_with_profit_deterioration" in trap_flags
                or "high_yield_with_cash_deterioration" in trap_flags
            )
        ):
            trap = True
            reasons.append("标记DIVIDEND_TRAP_RISK：高股息叠加价跌与基本面恶化")
            final = round(_clip(final - 25.0), 2)
        elif "high_yield_with_price_decline" in trap_flags and len(trap_flags) >= 1:
            # price-down + high yield alone is enough for trap watch in Phase6 heuristic
            trap = True
            reasons.append("标记DIVIDEND_TRAP_RISK：高股息且近1年股价显著下跌")
            final = round(_clip(final - 18.0), 2)
        elif len(trap_flags) >= 2:
            trap = True
            reasons.append("标记DIVIDEND_TRAP_RISK：多项陷阱启发式触发")
            final = round(_clip(final - 20.0), 2)
        elif trap_flags:
            reasons.append("存在股息陷阱预警线索：" + ",".join(trap_flags))
            final = round(_clip(final - 8.0), 2)

        status = (
            DataStatus.DATA_INSUFFICIENT.value
            if len(missing) >= 3
            else DataStatus.VALID.value
        )
        if y_ttm and y_ttm > 0:
            reasons.insert(0, f"TTM股息率约{y_ttm*100:.2f}%（现金/价格）")

        return ValuationResult(
            symbol=code,
            as_of=as_of,
            valuation_score=final,
            pe_ttm=None if pe is None else round(pe, 4),
            pb=None if pb is None else round(pb, 4),
            dividend_yield_ttm=None if y_ttm is None else round(y_ttm, 6),
            dividend_yield_3y_avg=None if y3 is None else round(y3, 6),
            dividend_yield_5y_avg=None if y5 is None else round(y5, 6),
            pe_percentile=None if pe_pct is None else round(pe_pct, 2),
            pb_percentile=None if pb_pct is None else round(pb_pct, 2),
            yield_percentile=None if yield_pct is None else round(yield_pct, 2),
            industry_name=ind_name,
            industry_group=ind_grp,
            industry_yield_median=industry_yield_median,
            dividend_yield_trap=trap,
            trap_flags=trap_flags,
            components=comps,
            reasons=reasons,
            data_status=status,
            missing_components=missing,
            details={
                "n_pe_hist": len(pe_hist),
                "n_pb_hist": len(pb_hist),
                "cash_ttm": cash_ttm,
                "price": price,
                "note": "PE/PB用最近年报EPS/BPS与qfq价；非券商一致预期TTM",
            },
        )


def run(*args, **kwargs):
    return DividendValuationEngine().score(*args, **kwargs)
