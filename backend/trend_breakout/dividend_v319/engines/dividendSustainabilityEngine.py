"""
DividendSustainabilityEngine — V3.1.9 Phase4.

DividendSustainabilityScore 0–100 from financial PIT panels.
Missing inputs → component neutral (~50) + DATA mark (no fabrication).
Unhealthy patterns lower score; severe → risk_hint for later RiskEngine.
Quality/yield alone must NOT dominate — weak cash / falling profit get punished here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIVIDEND_CONFIG, EVENTS_PATH, DividendConfig
from dividend_v319.engines.fin_pit_store import (
    annual_by_code,
    last_complete_report_year,
    load_lrb_annual,
    load_xjll_annual,
    load_yjbb_annual,
    load_zcfz_annual,
)
from dividend_v319.models.dividendModels import DataStatus

def _z6(code: str) -> str:
    return str(code).strip().zfill(6)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


@dataclass
class SustainabilityResult:
    symbol: str
    name: str
    as_of: str
    dividend_sustainability_score: Optional[float]
    component_scores: Dict[str, Optional[float]] = field(default_factory=dict)
    risk_hint: str = "SAFE"  # SAFE|WATCH|WARNING|HIGH — for RiskEngine
    unhealthy_flags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    data_status: str = DataStatus.DATA_INSUFFICIENT.value
    missing_components: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DividendSustainabilityEngine:
    def __init__(self, cfg: Optional[DividendConfig] = None, events_path=EVENTS_PATH):
        self.cfg = cfg or DIVIDEND_CONFIG
        self.events_path = events_path
        self._events = None
        self._yjbb = None
        self._lrb = None
        self._xjll = None
        self._zcfz = None

    def _ensure(self) -> None:
        if self._yjbb is None:
            self._yjbb = load_yjbb_annual()
            self._lrb = load_lrb_annual()
            self._xjll = load_xjll_annual()
            self._zcfz = load_zcfz_annual()
        if self._events is None:
            df = pd.read_parquet(self.events_path)
            df["code"] = df["code"].map(_z6)
            df["disclose_date"] = pd.to_datetime(df["disclose_date"], errors="coerce")
            df["cash_per_share"] = pd.to_numeric(df["cash_per_share"], errors="coerce")
            df["report_period"] = df["report_period"].astype(str)
            self._events = df

    def _dps_by_year(self, code: str, as_of: str) -> Dict[str, float]:
        self._ensure()
        t = pd.Timestamp(as_of).normalize()
        ok = set(self.cfg.assign_progress_ok)
        cash = self._events[
            (self._events["code"] == _z6(code))
            & (self._events["assign_progress"].isin(ok))
            & (self._events["cash_per_share"].fillna(0) > 0)
            & (self._events["disclose_date"].notna())
            & (self._events["disclose_date"] <= t)
        ].copy()
        if cash.empty:
            return {}
        cash["year"] = cash["report_period"].str[:4]
        out: Dict[str, float] = {}
        for y, g in cash.groupby("year"):
            if str(y).isdigit():
                out[str(y)] = float(g["cash_per_share"].sum())
        return out

    def _comp_ocf_over_ni(
        self, code: str, as_of: str, last_y: int
    ) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
        reasons: List[str] = []
        det: Dict[str, Any] = {}
        yj = annual_by_code(self._yjbb, code, as_of)
        xj = annual_by_code(self._xjll, code, as_of)
        ratios = []
        for y in range(last_y - 2, last_y + 1):
            period = f"{y}1231"
            ry = yj[yj["period"] == period]
            rx = xj[xj["period"] == period]
            if ry.empty or rx.empty:
                continue
            ni = ry.iloc[-1].get("PARENT_NETPROFIT")
            ocf = rx.iloc[-1].get("NETCASH_OPERATE")
            if pd.isna(ni) or pd.isna(ocf) or float(ni) == 0:
                continue
            ratios.append(float(ocf) / float(ni))
        det["ocf_ni_ratios"] = ratios
        if not ratios:
            return None, ["经营现金流/净利润对齐不足→中性"], det
        med = float(np.median(ratios))
        det["ocf_ni_median"] = med
        if med >= 1.0:
            score = 90.0
            reasons.append(f"经营现金流/净利润中位{med:.2f}，现金质量较好")
        elif med >= 0.7:
            score = 75.0
            reasons.append(f"经营现金流/净利润中位{med:.2f}")
        elif med >= 0.3:
            score = 55.0
            reasons.append(f"经营现金流/净利润中位{med:.2f}，偏弱")
        elif med >= 0:
            score = 35.0
            reasons.append(f"经营现金流显著低于净利润({med:.2f})")
        else:
            score = 15.0
            reasons.append(f"经营现金流为负相对净利润({med:.2f})")
        return score, reasons, det

    def _comp_fcf_over_div(
        self, code: str, as_of: str, last_y: int, dps: Dict[str, float]
    ) -> Tuple[Optional[float], List[str], Dict[str, Any], List[str]]:
        reasons: List[str] = []
        flags: List[str] = []
        det: Dict[str, Any] = {}
        yj = annual_by_code(self._yjbb, code, as_of)
        xj = annual_by_code(self._xjll, code, as_of)
        vals = []
        for y in range(last_y - 2, last_y + 1):
            ys = str(y)
            d = dps.get(ys, 0.0)
            if d <= 0:
                continue
            period = f"{y}1231"
            ry = yj[yj["period"] == period]
            rx = xj[xj["period"] == period]
            if ry.empty or rx.empty:
                continue
            ocf = rx.iloc[-1].get("NETCASH_OPERATE")
            capex = rx.iloc[-1].get("CONSTRUCT_LONG_ASSET")
            ni = ry.iloc[-1].get("PARENT_NETPROFIT")
            eps = ry.iloc[-1].get("BASIC_EPS")
            if pd.isna(ocf) or pd.isna(ni) or pd.isna(eps) or float(eps) == 0:
                continue
            shares = float(ni) / float(eps)
            if shares <= 0:
                continue
            fcf = float(ocf) - (float(capex) if pd.notna(capex) else 0.0)
            total_div = d * shares
            if total_div <= 0:
                continue
            vals.append(fcf / total_div)
        det["fcf_div_ratios"] = vals
        if not vals:
            return None, ["FCF/分红对齐不足→中性"], det, flags
        med = float(np.median(vals))
        det["fcf_div_median"] = med
        if med >= 1.2:
            score = 92.0
            reasons.append(f"FCF/现金分红中位{med:.2f}x，覆盖充足")
        elif med >= 0.8:
            score = 78.0
            reasons.append(f"FCF/现金分红中位{med:.2f}x")
        elif med >= 0.3:
            score = 55.0
            reasons.append(f"FCF覆盖分红偏紧({med:.2f}x)")
        elif med >= 0:
            score = 32.0
            flags.append("weak_fcf_coverage")
            reasons.append(f"FCF难以覆盖分红({med:.2f}x)")
        else:
            score = 12.0
            flags.append("negative_fcf_vs_dividend")
            reasons.append(f"FCF为负仍分红(中位{med:.2f}x)")
        return score, reasons, det, flags

    def _comp_payout(
        self, code: str, as_of: str, last_y: int, dps: Dict[str, float]
    ) -> Tuple[Optional[float], List[str], Dict[str, Any], List[str]]:
        reasons: List[str] = []
        flags: List[str] = []
        det: Dict[str, Any] = {}
        yj = annual_by_code(self._yjbb, code, as_of)
        ratios = []
        for y in range(last_y - 2, last_y + 1):
            ys = str(y)
            d = dps.get(ys, 0.0)
            if d <= 0:
                continue
            period = f"{y}1231"
            ry = yj[yj["period"] == period]
            if ry.empty:
                continue
            eps = ry.iloc[-1].get("BASIC_EPS")
            if pd.isna(eps):
                continue
            eps = float(eps)
            if eps <= 0:
                ratios.append(9.0)
                flags.append("dividend_with_nonpositive_eps")
                continue
            ratios.append(d / eps)
        det["payout_ratios"] = ratios
        if not ratios:
            return None, ["支付率对齐不足→中性"], det, flags
        med = float(np.median(ratios))
        det["payout_median"] = med
        if 0.15 <= med <= 0.75:
            score = 88.0
            reasons.append(f"支付率中位{med:.1%}，可持续区间")
        elif med <= 1.0:
            score = 50.0
            reasons.append(f"支付率中位{med:.1%}，偏高或偏低")
        else:
            score = 22.0
            flags.append("payout_above_earnings")
            reasons.append(f"支付率中位{med:.1%}，超过盈利")
        return score, reasons, det, flags

    def _comp_profit_continuity(
        self, code: str, as_of: str, last_y: int
    ) -> Tuple[Optional[float], List[str], Dict[str, Any], List[str]]:
        reasons: List[str] = []
        flags: List[str] = []
        det: Dict[str, Any] = {}
        lrb = annual_by_code(self._lrb, code, as_of)
        if lrb.empty:
            lrb = annual_by_code(self._yjbb, code, as_of)
        nis = []
        for y in range(last_y - 4, last_y + 1):
            period = f"{y}1231"
            ry = lrb[lrb["period"] == period]
            if ry.empty:
                continue
            ni = ry.iloc[-1].get("PARENT_NETPROFIT")
            if pd.notna(ni):
                nis.append((y, float(ni)))
        det["ni_series"] = nis
        if len(nis) < 2:
            return None, ["净利润序列不足→中性"], det, flags
        pos = sum(1 for _, v in nis if v > 0)
        score = 20.0 + 16.0 * pos  # 5y all positive ~100
        # declining trend
        if len(nis) >= 3:
            recent = [v for _, v in nis[-3:]]
            if recent[0] > 0 and recent[-1] < recent[0] * 0.6:
                score -= 18.0
                flags.append("profit_sharp_decline")
                reasons.append("近三年净利润显著下滑")
            elif recent[-1] > recent[0] * 1.05:
                reasons.append("近三年净利润未恶化")
        if pos < len(nis):
            flags.append("loss_years_present")
            reasons.append(f"近窗{len(nis)-pos}年亏损")
        else:
            reasons.append(f"近{len(nis)}年净利润均为正")
        return _clip(score), reasons, det, flags

    def _comp_revenue_stability(
        self, code: str, as_of: str, last_y: int
    ) -> Tuple[Optional[float], List[str], Dict[str, Any]]:
        reasons: List[str] = []
        det: Dict[str, Any] = {}
        lrb = annual_by_code(self._lrb, code, as_of)
        if lrb.empty:
            lrb = annual_by_code(self._yjbb, code, as_of)
        revs = []
        for y in range(last_y - 4, last_y + 1):
            period = f"{y}1231"
            ry = lrb[lrb["period"] == period]
            if ry.empty:
                continue
            v = ry.iloc[-1].get("TOTAL_OPERATE_INCOME")
            if pd.notna(v) and float(v) > 0:
                revs.append(float(v))
        det["revenue_n"] = len(revs)
        if len(revs) < 3:
            return None, ["收入序列不足→中性"], det
        arr = np.array(revs, dtype=float)
        cv = float(arr.std() / arr.mean()) if arr.mean() else 9.0
        det["revenue_cv"] = cv
        if cv <= 0.15:
            score = 90.0
            reasons.append(f"营收波动低(CV={cv:.2f})")
        elif cv <= 0.35:
            score = 72.0
            reasons.append(f"营收波动适中(CV={cv:.2f})")
        elif cv <= 0.60:
            score = 50.0
            reasons.append(f"营收波动偏高(CV={cv:.2f})")
        else:
            score = 28.0
            reasons.append(f"营收波动很高(CV={cv:.2f})")
        return score, reasons, det

    def _comp_leverage(
        self, code: str, as_of: str, last_y: int
    ) -> Tuple[Optional[float], List[str], Dict[str, Any], List[str]]:
        reasons: List[str] = []
        flags: List[str] = []
        det: Dict[str, Any] = {}
        z = annual_by_code(self._zcfz, code, as_of)
        if z.empty:
            return None, ["资产负债数据不足→中性"], det, flags
        rows = []
        for y in range(last_y - 2, last_y + 1):
            period = f"{y}1231"
            rz = z[z["period"] == period]
            if rz.empty:
                continue
            r = rz.iloc[-1]
            dar = r.get("DEBT_ASSET_RATIO")
            if pd.isna(dar):
                ta, tl = r.get("TOTAL_ASSETS"), r.get("TOTAL_LIABILITIES")
                if pd.notna(ta) and float(ta) > 0 and pd.notna(tl):
                    dar = float(tl) / float(ta) * 100.0
            if pd.notna(dar):
                rows.append((y, float(dar)))
        det["debt_asset_ratio"] = rows
        if not rows:
            return None, ["资产负债率缺失→中性"], det, flags
        latest = rows[-1][1]
        # banks naturally high — soften via industry if available
        lrb = annual_by_code(self._lrb, code, as_of)
        ind = ""
        if not lrb.empty and "INDUSTRY_NAME" in lrb.columns:
            ind = str(lrb.iloc[-1].get("INDUSTRY_NAME") or "")
        det["industry"] = ind
        bankish = any(k in ind for k in ("银行", "保险", "证券"))
        if bankish:
            # banks: focus on direction not absolute 80%+
            score = 70.0
            reasons.append(f"金融行业资产负债率{latest:.1f}%按行业中性处理")
            if len(rows) >= 2 and rows[-1][1] > rows[0][1] + 5:
                score -= 8.0
                reasons.append("近窗负债率上升")
        else:
            if latest <= 40:
                score = 88.0
            elif latest <= 60:
                score = 72.0
            elif latest <= 75:
                score = 50.0
            else:
                score = 28.0
                flags.append("high_leverage")
            reasons.append(f"资产负债率约{latest:.1f}%")
            if len(rows) >= 2 and rows[-1][1] - rows[0][1] >= 8:
                score -= 10.0
                flags.append("leverage_rising")
                reasons.append("资产负债率明显上升")
        return _clip(score), reasons, det, flags

    def score(self, code: str, as_of: str, name: str = "") -> SustainabilityResult:
        self._ensure()
        code = _z6(code)
        last_y = last_complete_report_year(as_of)
        dps = self._dps_by_year(code, as_of)
        if not name:
            ev = self._events[self._events["code"] == code]
            if not ev.empty:
                name = str(ev["name"].iloc[-1])

        comps: Dict[str, Optional[float]] = {}
        missing: List[str] = []
        reasons: List[str] = []
        flags: List[str] = []
        details: Dict[str, Any] = {"last_complete_report_year": last_y}

        s1, r1, d1 = self._comp_ocf_over_ni(code, as_of, last_y)
        comps["ocf_over_ni"] = s1
        reasons.extend(r1)
        details["ocf_over_ni"] = d1
        if s1 is None:
            missing.append("ocf_over_ni")

        s2, r2, d2, f2 = self._comp_fcf_over_div(code, as_of, last_y, dps)
        comps["fcf_over_div"] = s2
        reasons.extend(r2)
        flags.extend(f2)
        details["fcf_over_div"] = d2
        if s2 is None:
            missing.append("fcf_over_div")

        s3, r3, d3, f3 = self._comp_payout(code, as_of, last_y, dps)
        comps["payout"] = s3
        reasons.extend(r3)
        flags.extend(f3)
        details["payout"] = d3
        if s3 is None:
            missing.append("payout")

        s4, r4, d4, f4 = self._comp_profit_continuity(code, as_of, last_y)
        comps["profit_continuity"] = s4
        reasons.extend(r4)
        flags.extend(f4)
        details["profit_continuity"] = d4
        if s4 is None:
            missing.append("profit_continuity")

        s5, r5, d5 = self._comp_revenue_stability(code, as_of, last_y)
        comps["revenue_stability"] = s5
        reasons.extend(r5)
        details["revenue_stability"] = d5
        if s5 is None:
            missing.append("revenue_stability")

        s6, r6, d6, f6 = self._comp_leverage(code, as_of, last_y)
        comps["leverage"] = s6
        reasons.extend(r6)
        flags.extend(f6)
        details["leverage"] = d6
        if s6 is None:
            missing.append("leverage")

        # weights 待回测校准 — equal among available; missing → 50 neutral
        weights = getattr(self.cfg, "sustainability_component_weights", None) or {
            "ocf_over_ni": 0.20,
            "fcf_over_div": 0.20,
            "payout": 0.15,
            "profit_continuity": 0.20,
            "revenue_stability": 0.10,
            "leverage": 0.15,
        }
        total = 0.0
        wsum = 0.0
        for k, w in weights.items():
            v = comps.get(k)
            if v is None:
                v = 50.0
            total += v * w
            wsum += w
        score = total / wsum if wsum else None

        # unhealthy aggregate penalty
        severe = {
            "negative_fcf_vs_dividend",
            "payout_above_earnings",
            "dividend_with_nonpositive_eps",
            "profit_sharp_decline",
        }
        if score is not None:
            if any(f in severe for f in flags):
                score = max(0.0, score - 12.0)
                reasons.append("不健康分红特征→可持续分下调")
            if len([f for f in flags if f in severe]) >= 2:
                score = max(0.0, score - 10.0)

        # risk hint
        if any(f in ("negative_fcf_vs_dividend", "dividend_with_nonpositive_eps") for f in flags) and (
            comps.get("profit_continuity") is not None and comps["profit_continuity"] < 40
        ):
            risk = "HIGH"
        elif any(f in severe for f in flags) or (score is not None and score < 40):
            risk = "WARNING"
        elif flags or (score is not None and score < 55):
            risk = "WATCH"
        else:
            risk = "SAFE"

        data_status = DataStatus.VALID.value
        if len(missing) >= 4 or not dps:
            data_status = DataStatus.DATA_INSUFFICIENT.value
            reasons.append("DATA_INSUFFICIENT: 关键财务/分红对齐不足")
        if not dps:
            reasons.append("无PIT现金分红，可持续性难证伪也难证实")

        return SustainabilityResult(
            symbol=code,
            name=name,
            as_of=as_of,
            dividend_sustainability_score=None if score is None else round(score, 2),
            component_scores={k: (None if v is None else round(v, 2)) for k, v in comps.items()},
            risk_hint=risk,
            unhealthy_flags=sorted(set(flags)),
            reasons=reasons,
            data_status=data_status,
            missing_components=missing,
            details={**details, "weights": weights, "weights_note": "待回测校准"},
        )

    def score_many(
        self, codes: List[str], as_of: str, names: Optional[Dict[str, str]] = None
    ) -> List[SustainabilityResult]:
        names = names or {}
        return [self.score(c, as_of, name=names.get(_z6(c), "")) for c in codes]


def run(code: str, as_of: str, **kwargs) -> Dict[str, Any]:
    return DividendSustainabilityEngine().score(code, as_of, **kwargs).to_dict()
