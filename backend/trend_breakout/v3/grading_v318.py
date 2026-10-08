"""
V3.1.8 TrendGrade — independent of Maturity / EntryStatus / RealizedTrend.

TrendGrade answers: is the daily trend structure high quality?
EntryStatus answers: is now a mature entry window?

Maturity / Realized / EntryWindow MUST NOT force-demote a strong structure.
RAPID_MAIN_RISE may award A+/A while Maturity is still BUILDING.
Observation only. 禁止荐买 / 预测必涨. No symbol/date branches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from v3.types import Grade
from v3.v31.config import get_v318_grade
from v3.v31.types import FundRiskLevel, TrendState


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


@dataclass
class GradeResultV318:
    grade: Grade
    path: str = ""
    reasons: List[str] = field(default_factory=list)
    failed_checks: List[str] = field(default_factory=list)
    structure_score: Optional[float] = None
    structure_source: str = ""  # trend_structure | tech_proxy | missing
    rapid_main_rise: bool = False
    legacy_grade: Optional[str] = None  # V317/Plan reference only
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grade": self.grade.value if isinstance(self.grade, Grade) else self.grade,
            "path": self.path,
            "reasons": list(self.reasons),
            "failed_checks": list(self.failed_checks),
            "structure_score": self.structure_score,
            "structure_source": self.structure_source,
            "rapid_main_rise": self.rapid_main_rise,
            "legacy_grade": self.legacy_grade,
            "tags": list(self.tags),
        }


def _sector_ok(sector: Optional[float], min_v: float) -> bool:
    if sector is None:
        return True
    return float(sector) >= float(min_v)


def grade_trend_v318(
    *,
    trend_structure_score: Optional[float] = None,
    continuity_score: Optional[float] = None,
    sector_score: Optional[float] = None,
    tech_score: Optional[float] = None,
    fund_risk_level: Optional[str] = None,
    trend_state: Optional[str] = None,
    broken: bool = False,
    rapid_main_rise: bool = False,
    htf_ok: Optional[bool] = None,
    legacy_grade: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> GradeResultV318:
    """
    Assign TrendGrade from structure quality only.

    Does NOT read Maturity / RealizedTrend / EntryWindow.
    """
    cfg = config or get_v318_grade()
    reasons: List[str] = []
    failed: List[str] = []
    tags: List[str] = []
    state = str(trend_state or "").upper()
    fund = str(fund_risk_level or FundRiskLevel.NONE.value).upper()

    if broken or state == TrendState.TREND_FAILURE.value:
        return GradeResultV318(
            grade=Grade.FILTER,
            path="filter",
            reasons=["broken_or_trend_failure"],
            failed_checks=["broken_or_trend_failure"],
            legacy_grade=legacy_grade,
        )

    struct = _f(trend_structure_score)
    source = "trend_structure"
    if struct is None and cfg.get("structure_proxy_from_tech") and tech_score is not None:
        struct = float(tech_score)
        source = "tech_proxy"
        tags.append("STRUCTURE_PROXY_TECH")
        reasons.append("TrendStructure missing → Tech proxy")
    elif struct is None:
        source = "missing"

    cont = _f(continuity_score)
    tech = _f(tech_score)
    sec = _f(sector_score)

    def fund_blocks(band: Dict[str, Any]) -> bool:
        if band.get("require_fund_not_high") and fund == FundRiskLevel.HIGH.value:
            return True
        return False

    def failure_blocks(band: Dict[str, Any]) -> bool:
        return bool(band.get("block_trend_failure")) and state == TrendState.TREND_FAILURE.value

    # --- A+ classic structure path ---
    ap = cfg.get("a_plus") or {}
    if (
        struct is not None
        and cont is not None
        and struct >= float(ap.get("structure_min", 85))
        and cont >= float(ap.get("cont_min", 80))
        and _sector_ok(sec, float(ap.get("sector_min", 70)))
        and not fund_blocks(ap)
        and not failure_blocks(ap)
    ):
        reasons.append(
            f"A+ structure={struct:.1f}>={ap.get('structure_min')} "
            f"cont={cont:.1f}>={ap.get('cont_min')}"
        )
        if htf_ok:
            tags.append("HTF_OK")
        return GradeResultV318(
            grade=Grade.A_PLUS,
            path="structure_classic",
            reasons=reasons,
            structure_score=struct,
            structure_source=source,
            rapid_main_rise=bool(rapid_main_rise),
            legacy_grade=legacy_grade,
            tags=tags,
        )

    # --- A+ RAPID path ---
    rap = cfg.get("rapid_a_plus") or {}
    if rapid_main_rise and cont is not None:
        struct_ok = struct is not None and struct >= float(rap.get("structure_min", 70))
        tech_ok = tech is not None and tech >= float(rap.get("tech_min", 80))
        if (
            (struct_ok or tech_ok)
            and cont >= float(rap.get("cont_min", 75))
            and _sector_ok(sec, float(rap.get("sector_min", 60)))
            and fund != FundRiskLevel.HIGH.value
        ):
            reasons.append("A+ RAPID_MAIN_RISE path")
            tags.append("RAPID_MAIN_RISE")
            return GradeResultV318(
                grade=Grade.A_PLUS,
                path="rapid_a_plus",
                reasons=reasons,
                structure_score=struct,
                structure_source=source,
                rapid_main_rise=True,
                legacy_grade=legacy_grade,
                tags=tags,
            )

    # --- A ---
    a = cfg.get("a") or {}
    a_cont_min = float(a.get("cont_min", 70))
    # Excellent structure may soften Cont floor slightly (as-of snap Cont can lag live scan)
    if struct is not None and struct >= 90:
        a_cont_min = min(a_cont_min, float(a.get("cont_min_strong_structure", 65)))
    if (
        struct is not None
        and cont is not None
        and struct >= float(a.get("structure_min", 80))
        and cont >= a_cont_min
        and _sector_ok(sec, float(a.get("sector_min", 60)))
        and not fund_blocks(a)
    ):
        reasons.append(
            f"A structure={struct:.1f}>={a.get('structure_min')} cont={cont:.1f}>={a.get('cont_min')}"
        )
        return GradeResultV318(
            grade=Grade.A,
            path="structure_a",
            reasons=reasons,
            structure_score=struct,
            structure_source=source,
            rapid_main_rise=bool(rapid_main_rise),
            legacy_grade=legacy_grade,
            tags=tags,
        )

    # --- A RAPID ---
    ra = cfg.get("rapid_a") or {}
    if rapid_main_rise and cont is not None:
        struct_ok = struct is not None and struct >= float(ra.get("structure_min", 60))
        tech_ok = tech is not None and tech >= float(ra.get("tech_min", 75))
        if (
            (struct_ok or tech_ok)
            and cont >= float(ra.get("cont_min", 70))
            and _sector_ok(sec, float(ra.get("sector_min", 55)))
            and fund != FundRiskLevel.HIGH.value
        ):
            reasons.append("A RAPID_MAIN_RISE path")
            tags.append("RAPID_MAIN_RISE")
            return GradeResultV318(
                grade=Grade.A,
                path="rapid_a",
                reasons=reasons,
                structure_score=struct,
                structure_source=source,
                rapid_main_rise=True,
                legacy_grade=legacy_grade,
                tags=tags,
            )

    # --- B ---
    b = cfg.get("b") or {}
    b_struct = struct is not None and struct >= float(b.get("structure_min", 65))
    b_tech = (
        tech is not None
        and cont is not None
        and tech >= float(b.get("tech_min", 65))
        and cont >= float(b.get("cont_min", 60))
    )
    if b_struct or b_tech:
        reasons.append("B building/confirmed trend value")
        if rapid_main_rise:
            tags.append("RAPID_MAIN_RISE")
        return GradeResultV318(
            grade=Grade.B,
            path="b",
            reasons=reasons,
            failed_checks=failed,
            structure_score=struct,
            structure_source=source,
            rapid_main_rise=bool(rapid_main_rise),
            legacy_grade=legacy_grade,
            tags=tags,
        )

    # --- C ---
    c = cfg.get("c") or {}
    c_struct = struct is not None and struct >= float(c.get("structure_min", 50))
    c_tech = (
        tech is not None
        and cont is not None
        and tech >= float(c.get("tech_min", 55))
        and cont >= float(c.get("cont_min", 50))
    )
    if c_struct or c_tech:
        reasons.append("C early trend setup")
        return GradeResultV318(
            grade=Grade.C,
            path="c",
            reasons=reasons,
            structure_score=struct,
            structure_source=source,
            rapid_main_rise=bool(rapid_main_rise),
            legacy_grade=legacy_grade,
            tags=tags,
        )

    # --- WATCH ---
    w = cfg.get("watch") or {}
    w_struct = struct is not None and struct >= float(w.get("structure_min", 40))
    w_tech = (
        tech is not None
        and cont is not None
        and tech >= float(w.get("tech_min", 50))
        and cont >= float(w.get("cont_min", 45))
    )
    if w_struct or w_tech or source == "missing":
        reasons.append("WATCH unstable/insufficient")
        return GradeResultV318(
            grade=Grade.WATCH,
            path="watch",
            reasons=reasons,
            structure_score=struct,
            structure_source=source,
            legacy_grade=legacy_grade,
            tags=tags,
        )

    return GradeResultV318(
        grade=Grade.FILTER,
        path="filter",
        reasons=["below_watch"],
        structure_score=struct,
        structure_source=source,
        legacy_grade=legacy_grade,
        tags=tags,
    )


__all__ = ["GradeResultV318", "grade_trend_v318"]
