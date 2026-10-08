"""
V3.0 grading — Plan §11–12 threshold matrix.

Pure functions. Independent scores only. No TotalScore. No buy / 必涨 language.
A+ ≠ RecommendationScore≥90 (that remains V2.3 live path only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from .config import TREND_BOT_CONFIG, candidate_thresholds_for_regime
from .types import DataStatus, Grade, Lifecycle, MarketRegime, RiskLevel


@dataclass
class GradeResult:
    grade: Grade
    reasons: List[str] = field(default_factory=list)
    failed_checks: List[str] = field(default_factory=list)
    regime_v3: str = "NORMAL"
    thresholds_used: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grade": self.grade.value if isinstance(self.grade, Grade) else self.grade,
            "reasons": list(self.reasons),
            "failed_checks": list(self.failed_checks),
            "regime_v3": self.regime_v3,
            "thresholds_used": dict(self.thresholds_used),
        }


def _as_risk(x: Union[str, RiskLevel, None]) -> str:
    if isinstance(x, RiskLevel):
        return x.value
    return (x or RiskLevel.WATCH.value).strip().upper()


def _as_life(x: Union[str, Lifecycle, None]) -> str:
    if isinstance(x, Lifecycle):
        return x.value
    # accept V2 MAIN_RISE_ACCEL
    s = (x or Lifecycle.NONE.value).strip().upper()
    if s == "MAIN_RISE_ACCEL":
        return Lifecycle.ACCELERATION.value
    return s


def _as_regime(x: Union[str, MarketRegime, None]) -> str:
    if isinstance(x, MarketRegime):
        return x.value
    return (x or MarketRegime.NORMAL.value).strip().upper()


def _as_status(x: Union[str, DataStatus, None]) -> str:
    if isinstance(x, DataStatus):
        return x.value
    return (x or DataStatus.VALID.value).strip().upper()


def _dim_ok(
    value: Optional[float],
    minimum: float,
    *,
    required: bool,
    label: str,
    failed: List[str],
) -> bool:
    """If value is None and not required → skip (do not fake 0)."""
    if value is None:
        if required:
            failed.append(f"{label}=missing")
            return False
        return True
    if float(value) < float(minimum):
        failed.append(f"{label}={value:.1f}<{minimum:.1f}")
        return False
    return True


def _lifecycle_ok(life: str, allowed: Optional[Sequence[str]], failed: List[str], grade_label: str) -> bool:
    if allowed is None:
        return True
    if life not in set(allowed):
        failed.append(f"lifecycle={life} not in {grade_label}.allowed")
        return False
    return True


def _risk_ok(risk: str, allowed: Sequence[str], failed: List[str], grade_label: str) -> bool:
    if risk not in set(allowed):
        failed.append(f"risk={risk} not in {grade_label}.allowed")
        return False
    return True


def _consumption_ok(consumption: Optional[float], maximum: float, failed: List[str]) -> bool:
    if consumption is None:
        return True
    if float(consumption) >= float(maximum):
        failed.append(f"consumption={consumption:.1f}>={maximum:.1f}")
        return False
    return True


def _meets_band(
    *,
    band: str,
    block: Dict[str, Any],
    tech: Optional[float],
    cont: Optional[float],
    fund: Optional[float],
    sector: Optional[float],
    consumption: Optional[float],
    risk: str,
    life: str,
    require_tech_cont: bool = True,
) -> tuple[bool, List[str]]:
    failed: List[str] = []
    _dim_ok(tech, block["tech_min"], required=require_tech_cont, label="tech", failed=failed)
    _dim_ok(cont, block["cont_min"], required=require_tech_cont, label="cont", failed=failed)
    # fund/sector optional dims — only enforce when present
    _dim_ok(fund, block["fund_min"], required=False, label="fund", failed=failed)
    _dim_ok(sector, block["sector_min"], required=False, label="sector", failed=failed)
    _consumption_ok(consumption, block["consumption_max"], failed)
    _risk_ok(risk, block.get("allowed_risk") or (), failed, band)
    _lifecycle_ok(life, block.get("allowed_lifecycle"), failed, band)
    return (len(failed) == 0), failed


def grade_candidate(
    *,
    tech_score: Optional[float],
    continuity_score: Optional[float],
    fund_score: Optional[float] = None,
    sector_score: Optional[float] = None,
    risk_level: Union[str, RiskLevel] = RiskLevel.WATCH,
    lifecycle: Union[str, Lifecycle] = Lifecycle.NONE,
    regime_v3: Union[str, MarketRegime] = MarketRegime.NORMAL,
    consumption_score: Optional[float] = None,
    overheated: bool = False,
    broken: bool = False,
    data_status: Union[str, DataStatus] = DataStatus.VALID,
    config: Optional[Dict[str, Any]] = None,
) -> GradeResult:
    """
    Grade one candidate by Plan §11–12 matrix (+ §30 bear/chop raises).

    Parameters are independent scores — there is no TotalScore / RecScore input.
    Returns GradeResult with grade in {A+, A, B, WATCH, FILTER}.
    """
    cfg = config or TREND_BOT_CONFIG
    regime = _as_regime(regime_v3)
    risk = _as_risk(risk_level)
    life = _as_life(lifecycle)
    status = _as_status(data_status)
    cand = candidate_thresholds_for_regime(regime, cfg)
    filt = cand["filter"]

    reasons: List[str] = []
    hard_fail: List[str] = []

    if status == DataStatus.ERROR.value:
        return GradeResult(
            grade=Grade.FILTER,
            reasons=["data_status=ERROR"],
            failed_checks=["data_status=ERROR"],
            regime_v3=regime,
            thresholds_used={"filter": filt},
        )
    if status == DataStatus.DATA_INSUFFICIENT.value:
        # insufficient → cannot be A+/A; may still WATCH if floors pass later
        reasons.append("DATA_INSUFFICIENT: cap at WATCH")

    if broken or risk == RiskLevel.BROKEN.value or life in set(filt.get("block_lifecycle") or ()):
        hard_fail.append("broken_or_trend_failure")
    if risk in set(filt.get("block_risk") or ()):
        hard_fail.append(f"risk={risk}")

    # absolute floors
    if tech_score is None or continuity_score is None:
        hard_fail.append("tech_or_cont_missing")
    else:
        if float(tech_score) < float(filt["tech_min"]):
            hard_fail.append(f"tech<{filt['tech_min']}")
        if float(continuity_score) < float(filt["cont_min"]):
            hard_fail.append(f"cont<{filt['cont_min']}")

    if hard_fail:
        return GradeResult(
            grade=Grade.FILTER,
            reasons=["硬过滤"] + hard_fail,
            failed_checks=hard_fail,
            regime_v3=regime,
            thresholds_used={"filter": filt, "regime": regime},
        )

    # overheated: never core A+/A/B
    if overheated or life == Lifecycle.OVERHEATED.value:
        reasons.append("OVERHEATED: max WATCH")
        ok_w, fail_w = _meets_band(
            band="WATCH",
            block=cand["WATCH"],
            tech=tech_score,
            cont=continuity_score,
            fund=fund_score,
            sector=sector_score,
            consumption=consumption_score,
            risk=risk,
            life=life if life != Lifecycle.OVERHEATED.value else Lifecycle.OBSERVE.value,
        )
        # force lifecycle check bypass for OH → still WATCH if scores ok-ish
        grade = Grade.WATCH if (tech_score or 0) >= cand["WATCH"]["tech_min"] else Grade.FILTER
        return GradeResult(
            grade=grade,
            reasons=reasons + ([f"WATCH floors ok"] if grade == Grade.WATCH else fail_w),
            failed_checks=[] if grade == Grade.WATCH else fail_w,
            regime_v3=regime,
            thresholds_used={"WATCH": cand["WATCH"], "regime": regime},
        )

    # DATA_INSUFFICIENT: skip A+/A
    skip_core = status == DataStatus.DATA_INSUFFICIENT.value

    order = [("A+", Grade.A_PLUS), ("A", Grade.A), ("B", Grade.B), ("WATCH", Grade.WATCH)]
    last_fail: List[str] = []
    for band, grade_enum in order:
        if skip_core and band in ("A+", "A"):
            last_fail = [f"skip {band}: DATA_INSUFFICIENT"]
            continue
        ok, failed = _meets_band(
            band=band,
            block=cand[band],
            tech=tech_score,
            cont=continuity_score,
            fund=fund_score,
            sector=sector_score,
            consumption=consumption_score,
            risk=risk,
            life=life,
        )
        if ok:
            reasons.append(f"满足 {band} 阈值矩阵 (regime={regime})")
            if regime == "BEAR":
                reasons.append("BEAR raised bars applied")
            elif regime == "CHOP":
                reasons.append("CHOP raised bars applied")
            return GradeResult(
                grade=grade_enum,
                reasons=reasons,
                failed_checks=[],
                regime_v3=regime,
                thresholds_used={band: cand[band], "regime": regime},
            )
        last_fail = failed

    return GradeResult(
        grade=Grade.FILTER,
        reasons=["未达 WATCH 阈值"] + last_fail,
        failed_checks=last_fail,
        regime_v3=regime,
        thresholds_used={"WATCH": cand["WATCH"], "regime": regime},
    )


__all__ = ["GradeResult", "grade_candidate"]
