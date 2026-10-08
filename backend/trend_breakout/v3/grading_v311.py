"""
V3.1.1 grading — additive; does NOT replace v3.grading.grade_candidate (live).

Corrections vs V3.1:
  - TrendGrade ⊥ EntryWindow (EntryWindow never folds into grade)
  - Hysteresis = small jitter only + force-invalidate list
  - Consumption NOT an A+ hard gate (→ EntryWindow / Exhaustion / RiskWarning)
  - Formal A_PLUS_PATH_CONFIG CLASSIC vs STRENGTH
  - FundRisk NONE|WATCH|HIGH|DATA_INSUFFICIENT; only HIGH blocks A+
  - ExhaustionRisk never alone → TREND_FAILURE / TrendGrade=WATCH

禁止荐买 / 预测必涨. No symbol-specific branches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from v3.types import DataStatus, Grade, Lifecycle, MarketRegime, RiskLevel
from v3.v31.config import A_PLUS_PATH_CONFIG, get_v311_config
from v3.v31.entry_window import classify_entry_window
from v3.v31.types import (
    EntryWindow,
    ExhaustionLevel,
    FundRiskLevel,
    TrendState,
    V31GradeContext,
)


@dataclass
class GradeResultV311:
    grade: Grade
    entry_window: str = EntryWindow.NO_ENTRY.value
    reasons: List[str] = field(default_factory=list)
    failed_checks: List[str] = field(default_factory=list)
    fund_risk: bool = False  # True when WATCH or HIGH (compat)
    fund_risk_level: str = FundRiskLevel.NONE.value
    exhaustion: str = ExhaustionLevel.NORMAL.value
    trend_state: str = TrendState.NONE.value
    path: str = ""  # classic|strength|hysteresis|a|b|watch|filter
    tags: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    thresholds_used: Dict[str, Any] = field(default_factory=dict)
    hysteresis_invalidated: bool = False
    invalidate_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grade": self.grade.value if isinstance(self.grade, Grade) else self.grade,
            "entry_window": self.entry_window,
            "reasons": list(self.reasons),
            "failed_checks": list(self.failed_checks),
            "fund_risk": self.fund_risk,
            "fund_risk_level": self.fund_risk_level,
            "exhaustion": self.exhaustion,
            "trend_state": self.trend_state,
            "path": self.path,
            "tags": list(self.tags),
            "risk_warnings": list(self.risk_warnings),
            "thresholds_used": dict(self.thresholds_used),
            "hysteresis_invalidated": self.hysteresis_invalidated,
            "invalidate_reasons": list(self.invalidate_reasons),
        }


def _risk(x: Union[str, RiskLevel, None]) -> str:
    if isinstance(x, RiskLevel):
        return x.value
    return (x or RiskLevel.WATCH.value).strip().upper()


def _life(x: Union[str, Lifecycle, None]) -> str:
    if isinstance(x, Lifecycle):
        return x.value
    s = (x or Lifecycle.NONE.value).strip().upper()
    if s == "MAIN_RISE_ACCEL":
        return Lifecycle.ACCELERATION.value
    return s


def _state(ctx: Optional[V31GradeContext], lifecycle: str) -> str:
    if ctx and ctx.trend_state:
        return (
            ctx.trend_state.value
            if isinstance(ctx.trend_state, TrendState)
            else str(ctx.trend_state)
        )
    m = {
        Lifecycle.TREND_FAILURE.value: TrendState.TREND_FAILURE.value,
        Lifecycle.ACCELERATION.value: TrendState.ACCELERATION.value,
        Lifecycle.OVERHEATED.value: TrendState.ACCELERATION.value,
        Lifecycle.MAIN_RISE.value: TrendState.MAIN_RISE.value,
        Lifecycle.SECOND_BREAKOUT.value: TrendState.CONFIRMED.value,
        Lifecycle.FIRST_BREAKOUT.value: TrendState.CONFIRMED.value,
    }
    return m.get(lifecycle, TrendState.OBSERVE.value)


def classify_fund_risk(
    fund: Optional[float],
    *,
    fund_insufficient: bool = False,
    cfg: Optional[Dict[str, Any]] = None,
) -> FundRiskLevel:
    fr = (cfg or get_v311_config()).get("fund_risk") or {}
    watch_min = float(fr.get("watch_min", 65))
    none_min = float(fr.get("none_min", 70))
    if fund_insufficient or fund is None:
        return FundRiskLevel.DATA_INSUFFICIENT
    f = float(fund)
    if f >= none_min:
        return FundRiskLevel.NONE
    if f >= watch_min:
        return FundRiskLevel.WATCH
    return FundRiskLevel.HIGH


def _hysteresis_invalidate(ctx: Optional[V31GradeContext], state: str) -> List[str]:
    reasons: List[str] = []
    if state == TrendState.TREND_FAILURE.value:
        reasons.append("TREND_FAILURE")
    if ctx is None:
        return reasons
    if ctx.structure_rebreak:
        reasons.append("STRUCTURE_REBREAK")
    if (
        ctx.close is not None
        and ctx.trend_break_price is not None
        and float(ctx.close) < float(ctx.trend_break_price)
    ):
        reasons.append("CLOSE_BELOW_BREAK")
    if ctx.ma20_slope_neg:
        reasons.append("MA20_SLOPE_NEG")
    if ctx.higher_low_broken:
        reasons.append("HIGHER_LOW_BREAK")
    if int(ctx.cont_below_70_streak or 0) >= 2:
        reasons.append("CONT_BELOW_70_2D")
    return reasons


def grade_candidate_v311(
    *,
    tech_score: Optional[float],
    continuity_score: Optional[float],
    fund_score: Optional[float] = None,
    fund_insufficient: bool = False,
    sector_score: Optional[float] = None,
    risk_level: Union[str, RiskLevel] = RiskLevel.WATCH,
    lifecycle: Union[str, Lifecycle] = Lifecycle.NONE,
    regime_v3: Union[str, MarketRegime] = MarketRegime.NORMAL,
    consumption_score: Optional[float] = None,
    overheated: bool = False,
    broken: bool = False,
    data_status: Union[str, DataStatus] = DataStatus.VALID,
    ext_ma20_pct: Optional[float] = None,
    ctx: Optional[V31GradeContext] = None,
    config: Optional[Dict[str, Any]] = None,
) -> GradeResultV311:
    """
    V3.1.1 Plan grade. EntryWindow is parallel output — never folded into grade.
    OVERHEAT / high consumption do not force WATCH. Exhaustion is annotation only.
    """
    cfg = config or get_v311_config()
    paths = cfg.get("a_plus_paths") or A_PLUS_PATH_CONFIG
    classic_path = paths.get("CLASSIC") or A_PLUS_PATH_CONFIG["CLASSIC"]
    strength_path = paths.get("STRENGTH") or A_PLUS_PATH_CONFIG["STRENGTH"]

    risk = _risk(risk_level)
    life = _life(lifecycle)
    status = (
        data_status.value if isinstance(data_status, DataStatus) else str(data_status or "VALID")
    ).upper()
    state = _state(ctx, life)
    exh = ExhaustionLevel.coerce(ctx.exhaustion if ctx else ExhaustionLevel.NORMAL)
    tags: List[str] = list(ctx.tags) if ctx else []
    reasons: List[str] = []
    failed: List[str] = []
    risk_warnings: List[str] = list(ctx.risk_warnings) if ctx and ctx.risk_warnings else []

    if overheated or life == Lifecycle.OVERHEATED.value:
        tags.append("OVERHEAT_NO_CAP")
        reasons.append("OVERHEAT→ExhaustionRisk/EntryWindow only (no WATCH cap)")
        risk_warnings.append("OVERHEAT")

    # EntryWindow (parallel axis)
    wave = ctx.wave if ctx else None
    ew, ew_notes = classify_entry_window(
        trend_state=state,
        wave=wave,
        session_consumption=consumption_score,
        exhaustion=exh,
        ext_ma20_pct=ext_ma20_pct,
        broken=broken,
        overheated=overheated,
        config=cfg,
    )
    if wave and wave.wave_consumption is not None and float(wave.wave_consumption) >= 70:
        risk_warnings.append("WAVE_CONS_HIGH")
        tags.append("CONS_TO_ENTRY_WINDOW")
    elif consumption_score is not None and float(consumption_score) >= 70:
        risk_warnings.append("SESSION_CONS_HIGH")
        tags.append("CONS_TO_ENTRY_WINDOW")

    fund_lvl = classify_fund_risk(
        fund_score, fund_insufficient=fund_insufficient, cfg=cfg
    )
    if fund_lvl == FundRiskLevel.WATCH:
        tags.append("FUND_RISK_WATCH")
    elif fund_lvl == FundRiskLevel.HIGH:
        tags.append("FUND_RISK_HIGH")

    filt = cfg.get("filter") or {}
    base_kw = dict(
        entry_window=ew.value,
        exhaustion=exh.value,
        trend_state=state,
        fund_risk=(fund_lvl in (FundRiskLevel.WATCH, FundRiskLevel.HIGH)),
        fund_risk_level=fund_lvl.value,
        tags=tags,
        risk_warnings=risk_warnings,
    )

    if status == "ERROR":
        return GradeResultV311(
            grade=Grade.FILTER,
            reasons=["data_status=ERROR"],
            failed_checks=["data_status=ERROR"],
            path="filter",
            **base_kw,
        )

    if broken or risk == RiskLevel.BROKEN.value or state in set(filt.get("block_states") or ()):
        return GradeResultV311(
            grade=Grade.FILTER,
            reasons=["broken_or_trend_failure"],
            failed_checks=["broken_or_trend_failure"],
            path="filter",
            **base_kw,
        )
    if risk in set(filt.get("block_risk") or ()):
        return GradeResultV311(
            grade=Grade.FILTER,
            reasons=[f"risk={risk}"],
            failed_checks=[f"risk={risk}"],
            path="filter",
            **base_kw,
        )

    if tech_score is None or continuity_score is None:
        return GradeResultV311(
            grade=Grade.FILTER,
            reasons=["tech_or_cont_missing"],
            failed_checks=["tech_or_cont_missing"],
            path="filter",
            **base_kw,
        )

    tech = float(tech_score)
    cont = float(continuity_score)
    htf_ok = bool(ctx.htf_ok) if ctx else False
    strength_states = set(strength_path.get("states") or ())
    in_strength_state = state in strength_states
    strength_eligible = (
        (not strength_path.get("require_strength_state") or in_strength_state)
        and (not strength_path.get("require_htf") or htf_ok)
    )

    if status == "DATA_INSUFFICIENT":
        reasons.append("DATA_INSUFFICIENT: cap at WATCH")
        strength_eligible = False

    relax_states = set(cfg.get("relax_high_risk_in_states") or ())

    def sector_ok(min_v: float, fails: List[str]) -> bool:
        if sector_score is None:
            return True
        if float(sector_score) < float(min_v):
            fails.append(f"sector={float(sector_score):.1f}<{float(min_v):.1f}")
            return False
        return True

    def fund_ok_for_a_plus(fails: List[str]) -> bool:
        # Only HIGH blocks A+
        if fund_lvl == FundRiskLevel.HIGH:
            fails.append(f"fund_risk=HIGH fund={fund_score}")
            return False
        return True

    def risk_ok(path_cfg: Dict[str, Any], fails: List[str]) -> bool:
        allowed = set(path_cfg.get("allowed_risk") or ("SAFE", "WATCH"))
        if state in relax_states:
            allowed = set(path_cfg.get("allowed_risk_relaxed") or (allowed | {"HIGH"}))
        if risk not in allowed:
            fails.append(f"risk={risk} not allowed")
            return False
        return True

    # --- Classic A+ (formal config) ---
    if status != "DATA_INSUFFICIENT":
        f_classic: List[str] = []
        ok_tech = tech >= float(classic_path["tech_min"])
        ok_cont = cont >= float(classic_path["cont_min"])
        if not ok_tech:
            f_classic.append(f"tech={tech:.1f}<{float(classic_path['tech_min']):.1f}")
        if not ok_cont:
            f_classic.append(f"cont={cont:.1f}<{float(classic_path['cont_min']):.1f}")
        if (
            ok_tech
            and ok_cont
            and fund_ok_for_a_plus(f_classic)
            and sector_ok(float(classic_path["sector_min"]), f_classic)
            and risk_ok(classic_path, f_classic)
        ):
            reasons.append("满足 A+ CLASSIC (A_PLUS_PATH_CONFIG)")
            if fund_lvl == FundRiskLevel.WATCH:
                reasons.append("FundRisk=WATCH (does not block)")
            # NOTE: consumption intentionally NOT checked
            return GradeResultV311(
                grade=Grade.A_PLUS,
                reasons=reasons + ew_notes[:1],
                path="classic",
                thresholds_used={"CLASSIC": classic_path},
                **base_kw,
            )

        # --- Strength A+ (only when config-eligible) ---
        f_s: List[str] = []
        if strength_eligible:
            ok_t = tech >= float(strength_path["tech_min"])
            ok_c = cont >= float(strength_path["cont_min"])
            if not ok_t:
                f_s.append(f"tech={tech:.1f}<{float(strength_path['tech_min']):.1f}")
            if not ok_c:
                f_s.append(f"cont={cont:.1f}<{float(strength_path['cont_min']):.1f}")
            if (
                ok_t
                and ok_c
                and fund_ok_for_a_plus(f_s)
                and sector_ok(float(strength_path["sector_min"]), f_s)
                and risk_ok(strength_path, f_s)
            ):
                reasons.append("满足 A+ STRENGTH (A_PLUS_PATH_CONFIG + state + HTF)")
                tags.append("STRENGTH_PATH")
                if fund_lvl == FundRiskLevel.WATCH:
                    reasons.append("FundRisk=WATCH (does not block)")
                return GradeResultV311(
                    grade=Grade.A_PLUS,
                    reasons=reasons + ew_notes[:1],
                    path="strength",
                    thresholds_used={"STRENGTH": strength_path},
                    **{**base_kw, "tags": tags},
                )
            failed = f_classic + f_s
        else:
            failed = f_classic
            if in_strength_state and strength_path.get("require_htf") and not htf_ok:
                failed.append("strength_path blocked: HTF not ok")

    # A band
    a_cfg = cfg.get("a") or {}
    if status != "DATA_INSUFFICIENT":
        f_a: List[str] = []
        if tech >= float(a_cfg.get("tech_min", 75)) and cont >= float(a_cfg.get("cont_min", 70)):
            fund_a_ok = True
            if fund_score is not None and float(fund_score) < float(a_cfg.get("fund_min", 60)):
                # A band still uses soft floor; HIGH fund risk already blocked A+
                if fund_lvl == FundRiskLevel.HIGH:
                    fund_a_ok = False
                    f_a.append("fund_risk=HIGH")
            sec_a = True
            if sector_score is not None and float(sector_score) < float(a_cfg.get("sector_min", 60)):
                sec_a = False
                f_a.append("sector<a_min")
            risk_a = risk in set(a_cfg.get("allowed_risk") or ("SAFE", "WATCH", "WARNING", "HIGH"))
            if not risk_a:
                f_a.append(f"risk={risk}")
            if fund_a_ok and sec_a and risk_a:
                reasons.append("满足 A 阈值 (V3.1.1)")
                return GradeResultV311(
                    grade=Grade.A,
                    reasons=reasons,
                    path="a",
                    thresholds_used={"a": a_cfg},
                    failed_checks=failed,
                    **base_kw,
                )
            failed.extend(f_a)

    # --- Hysteresis: small jitter only; force-invalidate list ---
    hyst = cfg.get("hysteresis") or {}
    prior = (ctx.prior_grade if ctx else None) or None
    in_pool = bool(ctx.in_pool) if ctx else False
    inv = _hysteresis_invalidate(ctx, state)
    if inv:
        tags.append("HYSTERESIS_INVALIDATED")
    if (
        hyst.get("enabled")
        and in_pool
        and prior in set(hyst.get("persist_grades") or ())
        and not inv
        and state not in set(hyst.get("clear_on_states") or ())
    ):
        slack_c = float(hyst.get("cont_slack", 2))
        slack_t = float(hyst.get("tech_slack", 2))
        # Persist A+ only if within small slack of CLASSIC floors (jitter damp)
        if prior == "A+" and tech >= float(classic_path["tech_min"]) - slack_t and cont >= float(
            classic_path["cont_min"]
        ) - slack_c:
            if fund_lvl != FundRiskLevel.HIGH:
                reasons.append(f"hysteresis jitter-persist A+ (prior={prior}, slack={slack_t}/{slack_c})")
                tags.append("HYSTERESIS")
                return GradeResultV311(
                    grade=Grade.A_PLUS,
                    reasons=reasons,
                    path="hysteresis",
                    thresholds_used={"hysteresis": hyst, "prior": prior},
                    **{**base_kw, "tags": tags},
                )
        if prior in ("A+", "A") and tech >= 75.0 - slack_t and cont >= 70.0 - slack_c:
            reasons.append(f"hysteresis jitter-persist A (prior={prior})")
            tags.append("HYSTERESIS")
            return GradeResultV311(
                grade=Grade.A,
                reasons=reasons,
                path="hysteresis",
                thresholds_used={"hysteresis": hyst, "prior": prior},
                **{**base_kw, "tags": tags},
            )
    elif inv and in_pool:
        reasons.append(f"hysteresis invalidated: {','.join(inv)}")

    # B
    b_cfg = cfg.get("b") or {}
    if tech >= float(b_cfg.get("tech_min", 65)) and cont >= float(b_cfg.get("cont_min", 60)):
        reasons.append("满足 B 阈值 (V3.1.1)")
        return GradeResultV311(
            grade=Grade.B,
            reasons=reasons,
            failed_checks=failed,
            path="b",
            hysteresis_invalidated=bool(inv),
            invalidate_reasons=inv,
            **base_kw,
        )

    # WATCH — exhaustion alone must NOT force this without structure/score floors
    w_cfg = cfg.get("watch") or {}
    if tech >= float(w_cfg.get("tech_min", 55)) and cont >= float(w_cfg.get("cont_min", 50)):
        reasons.append("WATCH floors (scores, not exhaustion alone)")
        return GradeResultV311(
            grade=Grade.WATCH,
            reasons=reasons,
            failed_checks=failed,
            path="watch",
            hysteresis_invalidated=bool(inv),
            invalidate_reasons=inv,
            **base_kw,
        )

    return GradeResultV311(
        grade=Grade.FILTER,
        reasons=["未达 WATCH"] + failed,
        failed_checks=failed,
        path="filter",
        hysteresis_invalidated=bool(inv),
        invalidate_reasons=inv,
        **base_kw,
    )


__all__ = [
    "GradeResultV311",
    "grade_candidate_v311",
    "classify_fund_risk",
]
