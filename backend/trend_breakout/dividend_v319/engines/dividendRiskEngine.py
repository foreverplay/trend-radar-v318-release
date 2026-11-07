"""
DividendRiskEngine — V3.1.9 Phase9.

DividendRiskScore 0–100 (higher = riskier) + SAFE/WATCH/WARNING/HIGH.
Consumes Sustainability risk_hint / unhealthy_flags and Valuation trap flags.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from dividend_v319.models.dividendModels import DividendRiskLevel


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


@dataclass
class RiskResult:
    symbol: str
    as_of: str
    dividend_risk_score: float
    dividend_risk: str
    reasons: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_HINT_BASE = {
    "SAFE": 15.0,
    "WATCH": 40.0,
    "WARNING": 65.0,
    "HIGH": 85.0,
}


class DividendRiskEngine:
    def score(
        self,
        code: str,
        as_of: str,
        *,
        sustainability_score: Optional[float] = None,
        risk_hint: str = "SAFE",
        unhealthy_flags: Optional[List[str]] = None,
        dividend_yield_trap: bool = False,
        trap_flags: Optional[List[str]] = None,
        trend_score: Optional[float] = None,
        price_return_1y_qfq: Optional[float] = None,
        quality_score: Optional[float] = None,
    ) -> RiskResult:
        unhealthy_flags = unhealthy_flags or []
        trap_flags = trap_flags or []
        reasons: List[str] = []
        hint = (risk_hint or "SAFE").upper()
        if hint not in _HINT_BASE:
            hint = "WATCH"
            reasons.append(f"未知risk_hint={risk_hint}，按WATCH处理")

        score = _HINT_BASE[hint]
        reasons.append(f"可持续性风险线索={hint}")

        # sustainability level
        if sustainability_score is not None:
            if sustainability_score < 40:
                score += 15.0
                reasons.append("可持续分<40，风险上调")
            elif sustainability_score < 55:
                score += 8.0
            elif sustainability_score >= 80:
                score -= 8.0

        if quality_score is not None and quality_score < 55:
            score += 8.0
            reasons.append("质量分偏低")

        # unhealthy flags
        severe = {
            "negative_fcf_vs_dividend",
            "payout_unsustainable",
            "profit_collapse",
            "ocf_persistently_negative",
        }
        for f in unhealthy_flags:
            if f in severe:
                score += 10.0
                reasons.append(f"不健康标记:{f}")
            else:
                score += 4.0

        if dividend_yield_trap:
            score += 18.0
            reasons.append("DIVIDEND_TRAP_RISK")
        elif trap_flags:
            score += 6.0 * min(2, len(trap_flags))
            reasons.append("陷阱预警:" + ",".join(trap_flags[:3]))

        if trend_score is not None and trend_score < 40:
            score += 12.0
            reasons.append("趋势分<40")
        elif trend_score is not None and trend_score < 55:
            score += 5.0

        if price_return_1y_qfq is not None and price_return_1y_qfq <= -0.30:
            score += 10.0
            reasons.append("近1年跌幅≤-30%")
        elif price_return_1y_qfq is not None and price_return_1y_qfq <= -0.15:
            score += 5.0

        score = _clip(score)

        if score >= 90 or hint == "HIGH" or (
            dividend_yield_trap and score >= 80
        ):
            level = DividendRiskLevel.HIGH.value
        elif score >= 60 or hint == "WARNING":
            level = DividendRiskLevel.WARNING.value
        elif score >= 35 or hint == "WATCH":
            level = DividendRiskLevel.WATCH.value
        else:
            level = DividendRiskLevel.SAFE.value

        # hard escalation
        if dividend_yield_trap and level in (
            DividendRiskLevel.SAFE.value,
            DividendRiskLevel.WATCH.value,
        ):
            level = DividendRiskLevel.WARNING.value
            reasons.append("陷阱标记至少升至WARNING")

        return RiskResult(
            symbol=code,
            as_of=as_of,
            dividend_risk_score=round(score, 2),
            dividend_risk=level,
            reasons=reasons,
            inputs={
                "risk_hint": hint,
                "sustainability_score": sustainability_score,
                "dividend_yield_trap": dividend_yield_trap,
                "unhealthy_flags": unhealthy_flags,
                "trap_flags": trap_flags,
            },
        )


def run(*args, **kwargs):
    return DividendRiskEngine().score(*args, **kwargs)
