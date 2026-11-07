"""
DividendQualityEngine — V3.1.9 Phase3.

DividendQualityScore 0–100 with Plan weights (待回测校准):
  continuity 25 / stability_growth 20 / payout 20 / cash_ability 25 / record_quality 10

PIT: only dividend events with disclose_date <= T; financials with NOTICE_DATE <= T.
Special dividends: event is_special_flag when True; else heuristic (DPS vs median).
Stock dividends ≠ cash (cash_per_share<=0 ignored).
Does not invent scores when data missing — marks DATA_INSUFFICIENT / neutral sub-scores.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIV_ROOT, DIVIDEND_CONFIG, EVENTS_PATH, DividendConfig
from dividend_v319.engines.fin_pit_store import (
    annual_by_code,
    last_complete_report_year,
    load_lrb_annual,
    load_xjll_annual,
    load_yjbb_annual,
)
from dividend_v319.models.dividendModels import DataStatus


def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


@dataclass
class QualityResult:
    symbol: str
    name: str
    as_of: str
    dividend_quality_score: Optional[float]
    continuity_score: Optional[float]
    stability_growth_score: Optional[float]
    payout_score: Optional[float]
    cash_ability_score: Optional[float]
    record_quality_score: Optional[float]
    continuous_cash_years: int
    cash_years: List[str] = field(default_factory=list)
    annual_dps: Dict[str, float] = field(default_factory=dict)
    special_heuristic_years: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    data_status: str = DataStatus.DATA_INSUFFICIENT.value
    missing_components: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DividendQualityEngine:
    def __init__(
        self,
        cfg: Optional[DividendConfig] = None,
        events_path: Path = EVENTS_PATH,
    ):
        self.cfg = cfg or DIVIDEND_CONFIG
        self.events_path = events_path
        self._events: Optional[pd.DataFrame] = None
        self._yjbb = None
        self._lrb = None
        self._xjll = None

    def _ensure_events(self) -> pd.DataFrame:
        if self._events is None:
            df = pd.read_parquet(self.events_path)
            df["code"] = df["code"].map(_z6)
            df["disclose_date"] = pd.to_datetime(df["disclose_date"], errors="coerce")
            df["cash_per_share"] = pd.to_numeric(df["cash_per_share"], errors="coerce")
            df["report_period"] = df["report_period"].astype(str)
            self._events = df
        return self._events

    def _ensure_fin(self) -> None:
        if self._yjbb is None:
            self._yjbb = load_yjbb_annual()
            self._lrb = load_lrb_annual()
            self._xjll = load_xjll_annual()

    def _cash_events_asof(self, code: str, as_of: str) -> pd.DataFrame:
        ev = self._ensure_events()
        t = pd.Timestamp(as_of).normalize()
        ok = set(self.cfg.assign_progress_ok)
        sub = ev[
            (ev["code"] == _z6(code))
            & (ev["assign_progress"].isin(ok))
            & (ev["cash_per_share"].fillna(0) > 0)
            & (ev["disclose_date"].notna())
            & (ev["disclose_date"] <= t)
        ].copy()
        return sub

    def _annual_dps_series(
        self, cash: pd.DataFrame
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """Sum cash_per_share by report_period calendar year. Stock div ignored (cash<=0)."""
        meta: Dict[str, Any] = {"special_flag_years": [], "heuristic_special_years": []}
        if cash.empty:
            return {}, meta
        cash = cash.copy()
        cash["year"] = cash["report_period"].str[:4]
        cash = cash[cash["year"].str.fullmatch(r"\d{4}") == True]  # noqa: E712
        dps: Dict[str, float] = {}
        for y, g in cash.groupby("year"):
            dps[str(y)] = float(g["cash_per_share"].sum())
            if "is_special_flag" in g.columns:
                if g["is_special_flag"].fillna(False).astype(bool).any():
                    meta["special_flag_years"].append(str(y))
        # heuristic special: year DPS > max(2.5 * median of other years, median+2*std)
        if len(dps) >= 3:
            years = sorted(dps.keys())
            vals = [dps[y] for y in years]
            med = float(np.median(vals))
            for y in years:
                others = [dps[z] for z in years if z != y]
                if not others:
                    continue
                omed = float(np.median(others))
                if omed > 0 and dps[y] >= 2.5 * omed and dps[y] >= med * 1.8:
                    meta["heuristic_special_years"].append(y)
        return dps, meta

    def _score_continuity(
        self, dps: Dict[str, float], last_y: int, history_years: int
    ) -> Tuple[float, int, List[str], List[str]]:
        reasons: List[str] = []
        need = [str(y) for y in range(last_y - history_years + 1, last_y + 1)]
        present = [y for y in need if dps.get(y, 0) > 0]
        # longest trailing streak ending at last_y
        streak = 0
        y = last_y
        while str(y) in dps and dps[str(y)] > 0:
            streak += 1
            y -= 1
        # also allow streak if last_y missing but last_y-1 present (late filing) — still count trailing from max present <= last_y
        if streak == 0:
            ys = sorted(int(k) for k, v in dps.items() if v > 0 and int(k) <= last_y)
            if ys:
                streak = 1
                for i in range(len(ys) - 1, 0, -1):
                    if ys[i] == ys[i - 1] + 1:
                        streak += 1
                    else:
                        break
                # only if streak ends near last_y
                if ys[-1] < last_y - 1:
                    streak = min(streak, 1)  # stale
                    reasons.append(f"最近完整年{last_y}无现金分红记录(PIT)")
        if streak >= 5:
            score = 100.0
        elif streak == 4:
            score = 88.0
        elif streak == 3:
            score = 72.0
        elif streak == 2:
            score = 48.0
        elif streak == 1:
            score = 28.0
        else:
            score = 5.0
        # last-3 completeness soft bonus/penalty
        last3 = [str(y) for y in range(last_y - 2, last_y + 1)]
        n3 = sum(1 for y in last3 if dps.get(y, 0) > 0)
        if n3 == 3 and streak >= 3:
            score = min(100.0, score + 3.0)
            reasons.append(f"近3年({last3[0]}-{last3[-1]})现金分红连续")
        elif n3 <= 1 and streak < 3:
            score = max(0.0, score - 8.0)
            reasons.append(f"近3年仅{n3}年有现金分红")
        reasons.append(f"连续现金分红约{streak}年(截至报告期年{last_y})")
        years_list = sorted(k for k, v in dps.items() if v > 0)
        return score, streak, years_list, reasons

    def _score_stability_growth(
        self, dps: Dict[str, float], last_y: int, special_years: List[str]
    ) -> Tuple[float, List[str], Dict[str, Any]]:
        reasons: List[str] = []
        details: Dict[str, Any] = {}
        window = [str(y) for y in range(last_y - 4, last_y + 1)]
        series = []
        for y in window:
            if y in dps and dps[y] > 0:
                # downweight heuristic special for growth path
                v = dps[y]
                if y in special_years:
                    v = float(np.median([dps[z] for z in window if z in dps and z not in special_years] or [v]))
                    reasons.append(f"{y}疑似特别/异常高派息(启发式)，增长计算已降权")
                series.append((y, v))
        details["growth_window"] = series
        if len(series) < 2:
            reasons.append("分红金额序列不足，稳定/增长分中性")
            return 50.0, reasons, details

        yoy = []
        for i in range(1, len(series)):
            a, b = series[i - 1][1], series[i][1]
            if a > 0:
                yoy.append(b / a - 1.0)
        details["yoy"] = yoy
        if not yoy:
            return 50.0, reasons, details

        cuts = sum(1 for r in yoy if r <= -0.30)
        mild_cuts = sum(1 for r in yoy if -0.30 < r < -0.05)
        flat = sum(1 for r in yoy if abs(r) <= 0.05)
        grows = sum(1 for r in yoy if r > 0.05)
        score = 70.0
        score -= cuts * 22.0
        score -= mild_cuts * 8.0
        score += flat * 4.0
        score += min(grows, 3) * 6.0
        # volatility penalty
        if len(yoy) >= 2:
            vol = float(np.std(yoy))
            details["yoy_std"] = vol
            if vol > 0.5:
                score -= 12.0
                reasons.append(f"每股现金分红波动较大(yoy_std={vol:.2f})")
            elif vol < 0.15:
                score += 6.0
                reasons.append("每股现金分红波动较低")
        if cuts:
            reasons.append(f"出现{cuts}次≥30%的分红下调")
        if grows and not cuts:
            reasons.append("分红金额整体稳定或增长")
        return _clip(score), reasons, details

    def _score_payout(
        self, code: str, dps: Dict[str, float], as_of: str, last_y: int
    ) -> Tuple[float, List[str], Dict[str, Any], List[str]]:
        reasons: List[str] = []
        missing: List[str] = []
        details: Dict[str, Any] = {"payout_by_year": {}}
        self._ensure_fin()
        yj = annual_by_code(self._yjbb, code, as_of)
        if yj.empty:
            missing.append("yjbb_eps")
            reasons.append("缺少年报EPS(PIT)，支付合理性中性")
            return 50.0, reasons, details, missing

        ratios = []
        for y in range(last_y - 4, last_y + 1):
            ys = str(y)
            if dps.get(ys, 0) <= 0:
                continue
            period = f"{y}1231"
            row = yj[yj["period"] == period]
            if row.empty:
                continue
            eps = row.iloc[-1].get("BASIC_EPS")
            if pd.isna(eps):
                continue
            eps = float(eps)
            d = dps[ys]
            if eps <= 0:
                details["payout_by_year"][ys] = {"dps": d, "eps": eps, "payout": None, "note": "non_positive_eps"}
                ratios.append(1.5)  # treat as stressed
                reasons.append(f"{ys}利润≤0仍派现，支付合理性承压")
                continue
            payout = d / eps
            details["payout_by_year"][ys] = {"dps": d, "eps": eps, "payout": payout}
            ratios.append(payout)

        if not ratios:
            missing.append("payout_overlap")
            reasons.append("分红与EPS年份无法对齐，支付合理性中性")
            return 50.0, reasons, details, missing

        med = float(np.median(ratios))
        details["payout_median"] = med
        # ideal 15%–70%
        if 0.15 <= med <= 0.70:
            score = 90.0 - abs(med - 0.40) * 40.0
            reasons.append(f"分红/EPS中位约{med:.1%}，支付比例相对合理")
        elif 0.05 <= med < 0.15:
            score = 60.0
            reasons.append(f"分红/EPS中位约{med:.1%}，偏保守")
        elif 0.70 < med <= 1.0:
            score = 55.0
            reasons.append(f"分红/EPS中位约{med:.1%}，偏高需看现金流")
        elif med > 1.0:
            score = 28.0
            reasons.append(f"分红/EPS中位约{med:.1%}，超过当期利润")
        else:
            score = 40.0
            reasons.append(f"分红/EPS中位约{med:.1%}，偏低或不稳定")
        # spike years
        if any(r > 1.2 for r in ratios):
            score -= 10.0
        return _clip(score), reasons, details, missing

    def _score_cash_ability(
        self, code: str, dps: Dict[str, float], as_of: str, last_y: int
    ) -> Tuple[float, List[str], Dict[str, Any], List[str]]:
        reasons: List[str] = []
        missing: List[str] = []
        details: Dict[str, Any] = {"coverage": []}
        self._ensure_fin()
        yj = annual_by_code(self._yjbb, code, as_of)
        xj = annual_by_code(self._xjll, code, as_of)
        if yj.empty and xj.empty:
            missing.append("cashflow_panels")
            reasons.append("缺少现金流/每股经营现金流，现金能力中性")
            return 50.0, reasons, details, missing

        coverages = []
        for y in range(last_y - 4, last_y + 1):
            ys = str(y)
            d = dps.get(ys, 0.0)
            if d <= 0:
                continue
            period = f"{y}1231"
            ocf_ps = None
            fcf_over_div = None
            row_y = yj[yj["period"] == period] if not yj.empty else pd.DataFrame()
            if not row_y.empty and pd.notna(row_y.iloc[-1].get("MGJYXJJE")):
                ocf_ps = float(row_y.iloc[-1]["MGJYXJJE"])
                coverages.append(("ocf_ps", ys, ocf_ps / d if d else None))
            row_x = xj[xj["period"] == period] if not xj.empty else pd.DataFrame()
            if not row_x.empty and not row_y.empty:
                ocf = row_x.iloc[-1].get("NETCASH_OPERATE")
                capex = row_x.iloc[-1].get("CONSTRUCT_LONG_ASSET")
                ni = row_y.iloc[-1].get("PARENT_NETPROFIT")
                eps = row_y.iloc[-1].get("BASIC_EPS")
                if pd.notna(ocf) and pd.notna(ni) and pd.notna(eps) and float(eps) != 0:
                    shares = float(ni) / float(eps)
                    if shares > 0:
                        total_div = d * shares
                        fcf = float(ocf) - (float(capex) if pd.notna(capex) else 0.0)
                        if total_div > 0:
                            fcf_over_div = fcf / total_div
                            coverages.append(("fcf_div", ys, fcf_over_div))
            details["coverage"].append(
                {"year": ys, "dps": d, "ocf_ps": ocf_ps, "fcf_over_div": fcf_over_div}
            )

        if not coverages:
            missing.append("coverage_align")
            reasons.append("现金流与分红年份无法对齐，现金能力中性")
            return 50.0, reasons, details, missing

        ocf_ratios = [v for k, _, v in coverages if k == "ocf_ps" and v is not None]
        fcf_ratios = [v for k, _, v in coverages if k == "fcf_div" and v is not None]
        score = 55.0
        if ocf_ratios:
            med = float(np.median(ocf_ratios))
            details["ocf_ps_over_dps_median"] = med
            if med >= 2.0:
                score += 30.0
                reasons.append(f"每股经营现金流覆盖分红约{med:.2f}倍(中位)")
            elif med >= 1.0:
                score += 18.0
                reasons.append(f"每股经营现金流可覆盖分红(中位{med:.2f}x)")
            elif med >= 0.5:
                score += 5.0
                reasons.append(f"每股经营现金流覆盖偏弱(中位{med:.2f}x)")
            else:
                score -= 25.0
                reasons.append(f"每股经营现金流不足以覆盖分红(中位{med:.2f}x)")
            if any(v < 0 for v in ocf_ratios):
                score -= 15.0
                reasons.append("存在经营现金流为负的年份")
        if fcf_ratios:
            medf = float(np.median(fcf_ratios))
            details["fcf_over_div_median"] = medf
            if medf >= 1.0:
                score += 10.0
            elif medf < 0:
                score -= 12.0
                reasons.append("自由现金流(经营-购建)覆盖分红为负")
        return _clip(score), reasons, details, missing

    def _score_record_quality(
        self,
        cash: pd.DataFrame,
        special_flag_years: List[str],
        heuristic_special: List[str],
        code: str,
        as_of: str,
    ) -> Tuple[float, List[str]]:
        reasons: List[str] = []
        score = 88.0
        # cancelled / rejected in full event history PIT (any progress)
        ev = self._ensure_events()
        t = pd.Timestamp(as_of).normalize()
        hist = ev[
            (ev["code"] == _z6(code))
            & (ev["disclose_date"].notna())
            & (ev["disclose_date"] <= t)
        ]
        bad = hist[hist["assign_progress"].isin(["取消分配", "股东大会否决"])]
        if len(bad):
            score -= min(40.0, 12.0 * len(bad))
            reasons.append(f"历史存在{len(bad)}条取消/否决分红记录")
        if special_flag_years:
            score -= 8.0 * len(set(special_flag_years))
            reasons.append(f"标注特别分红年份: {','.join(sorted(set(special_flag_years)))}")
        if heuristic_special:
            score -= 6.0 * len(set(heuristic_special))
            reasons.append(
                f"启发式异常高派息年份(不确定降权): {','.join(sorted(set(heuristic_special)))}"
            )
        # profile text empty many rows — note uncertainty
        if cash.empty:
            reasons.append("无实施现金分红记录")
            score = 30.0
        else:
            if "impl_plan_profile" in cash.columns:
                nonempty = cash["impl_plan_profile"].fillna("").astype(str).str.len().gt(0).mean()
                if nonempty < 0.2:
                    score -= 3.0
                    reasons.append("方案文本稀疏，特别分红识别不确定")
        if score >= 80 and not special_flag_years and not heuristic_special:
            reasons.append("分红记录未见明显偶发/特别依赖")
        return _clip(score), reasons

    def score(self, code: str, as_of: str, name: str = "") -> QualityResult:
        code = _z6(code)
        cfg = self.cfg
        history_years = int(getattr(cfg, "quality_history_years", 5))
        last_y = last_complete_report_year(as_of)
        cash = self._cash_events_asof(code, as_of)
        if not name and not cash.empty and "name" in cash.columns:
            name = str(cash["name"].iloc[-1])

        dps, meta = self._annual_dps_series(cash)
        special_h = list(meta.get("heuristic_special_years") or [])
        special_f = list(meta.get("special_flag_years") or [])

        missing: List[str] = []
        reasons: List[str] = []

        if not dps:
            return QualityResult(
                symbol=code,
                name=name,
                as_of=as_of,
                dividend_quality_score=None,
                continuity_score=None,
                stability_growth_score=None,
                payout_score=None,
                cash_ability_score=None,
                record_quality_score=None,
                continuous_cash_years=0,
                reasons=["as-of日之前无已披露实施现金分红", "DATA_INSUFFICIENT"],
                data_status=DataStatus.DATA_INSUFFICIENT.value,
                missing_components=["cash_dividends"],
            )

        c_score, streak, years_list, r1 = self._score_continuity(dps, last_y, history_years)
        s_score, r2, d2 = self._score_stability_growth(dps, last_y, special_h)
        p_score, r3, d3, m3 = self._score_payout(code, dps, as_of, last_y)
        a_score, r4, d4, m4 = self._score_cash_ability(code, dps, as_of, last_y)
        q_score, r5 = self._score_record_quality(cash, special_f, special_h, code, as_of)
        missing.extend(m3)
        missing.extend(m4)
        reasons.extend(r1 + r2 + r3 + r4 + r5)

        w = getattr(cfg, "quality_component_weights", None) or {
            "continuity": 0.25,
            "stability_growth": 0.20,
            "payout": 0.20,
            "cash_ability": 0.25,
            "record_quality": 0.10,
        }
        total = (
            c_score * w["continuity"]
            + s_score * w["stability_growth"]
            + p_score * w["payout"]
            + a_score * w["cash_ability"]
            + q_score * w["record_quality"]
        )
        # if too many missing core comps, mark insufficient but still return weighted with neutrals
        data_status = DataStatus.VALID.value
        if len(missing) >= 2 or streak <= 0:
            data_status = DataStatus.DATA_INSUFFICIENT.value
            reasons.append("关键财务对齐不足或连续性不足 → DATA_INSUFFICIENT")

        return QualityResult(
            symbol=code,
            name=name,
            as_of=as_of,
            dividend_quality_score=round(total, 2),
            continuity_score=round(c_score, 2),
            stability_growth_score=round(s_score, 2),
            payout_score=round(p_score, 2),
            cash_ability_score=round(a_score, 2),
            record_quality_score=round(q_score, 2),
            continuous_cash_years=int(streak),
            cash_years=years_list,
            annual_dps={k: round(v, 6) for k, v in sorted(dps.items())},
            special_heuristic_years=special_h,
            reasons=reasons,
            data_status=data_status,
            missing_components=missing,
            details={
                "last_complete_report_year": last_y,
                "stability": d2,
                "payout": d3,
                "cash_ability": d4,
                "weights": w,
                "weights_note": "待回测校准",
                "special_flag_years": special_f,
            },
        )

    def score_many(
        self, codes: List[str], as_of: str, names: Optional[Dict[str, str]] = None
    ) -> List[QualityResult]:
        names = names or {}
        return [self.score(c, as_of, name=names.get(_z6(c), "")) for c in codes]


def run(code: str, as_of: str, **kwargs) -> Dict[str, Any]:
    eng = DividendQualityEngine()
    return eng.score(code, as_of, **kwargs).to_dict()
