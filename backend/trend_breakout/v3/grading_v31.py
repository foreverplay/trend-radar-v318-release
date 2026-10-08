"""
V3.1 grading — additive; does NOT replace v3.grading.grade_candidate (live).

Implements plan §§十六–二十六:
  soft fund → FundRisk; Cont≥75/Tech≥80 strength path; OVERHEAT does not cap;
  wave consumption; hysteresis while in ActiveTrendPool; ExhaustionRisk annotate only.

禁止荐买 / 预测必涨. No symbol-specific branches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from v3.types import DataStatus, Grade, Lifecycle, MarketRegime, RiskLevel
from v3.v31.config import V31_CONFIG, get_v31_config
from v3.v31.types import ExhaustionLevel, TrendState, V31GradeContext, WaveInfo


@dataclass
class GradeResultV31:
    grade: Grade
    reasons: List[str] = field(default_factory=list)
    failed_checks: List[str] = field(default_factory=list)
    fund_risk: bool = False
    exhaustion: str = ExhaustionLevel.NORMAL.value
    trend_state: str = TrendState.NONE.value
    path: str = ""  # classic|strength|hysteresis|watch|filter
    tags: List[str] = field(default_factory=list)
    thresholds_used: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grade": self.grade.value if isinstance(self.grade, Grade) else self.grade,
            "reasons": list(self.reasons),
            "failed_checks": list(self.failed_checks),
            "fund_risk": self.fund_risk,
            "exhaustion": self.exhaustion,
            "trend_state": self.trend_state,
            "path": self.path,
            "tags": list(self.tags),
            "thresholds_used": dict(self.thresholds_used),
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
    # fallback map
    m = {
        Lifecycle.TREND_FAILURE.value: TrendState.TREND_FAILURE.value,
        Lifecycle.ACCELERATION.value: TrendState.ACCELERATION.value,
        Lifecycle.OVERHEATED.value: TrendState.ACCELERATION.value,
        Lifecycle.MAIN_RISE.value: TrendState.MAIN_RISE.value,
        Lifecycle.SECOND_BREAKOUT.value: TrendState.CONFIRMED.value,
        Lifecycle.FIRST_BREAKOUT.value: TrendState.CONFIRMED.value,
    }
    return m.get(lifecycle, TrendState.OBSERVE.value)


def _consumption_for_gate(
    consumption_score: Optional[float],
    ctx: Optional[V31GradeContext],
    cfg: Dict[str, Any],
) -> Optional[float]:
    if cfg.get("use_wave_consumption") and ctx and ctx.wave and ctx.wave.wave_consumption is not None:
        return float(ctx.wave.wave_consumption)
    return float(consumption_score) if consumption_score is not None else None


def _fund_allows_a_plus(
    fund: Optional[float],
    *,
    strength_ok: bool,
    cfg: Dict[str, Any],
    failed: List[str],
    tags: List[str],
) -> tuple[bool, bool]:
    """Returns (ok, fund_risk)."""
    soft = cfg.get("fund_soft") or {}
    hard_min = float(soft.get("hard_min_for_a_plus", 65))
    classic = float(soft.get("classic_min", 70))
    if fund is None:
        return True, False
    f = float(fund)
    if f < hard_min:
        failed.append(f"fund={f:.1f}<{hard_min:.1f}")
        return False, False
    if f < classic:
        if strength_ok:
            if "FUND_RISK" not in tags:
                tags.append("FUND_RISK")
            return True, True
        failed.append(f"fund={f:.1f}<{classic:.1f} (no strength path)")
        return False, True
    return True, False


def grade_candidate_v31(
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
    ctx: Optional[V31GradeContext] = None,
    config: Optional[Dict[str, Any]] = None,
) -> GradeResultV31:
    """
    V3.1 Plan grade. Independent of RecScore. No TotalScore.
    OVERHEAT does not force WATCH. Exhaustion is annotation only.
    """
    cfg = config or get_v31_config()
    risk = _risk(risk_level)
    life = _life(lifecycle)
    status = (
        data_status.value if isinstance(data_status, DataStatus) else str(data_status or "VALID")
    ).upper()
    state = _state(ctx, life)
    exh = (
        ctx.exhaustion.value
        if ctx and isinstance(ctx.exhaustion, ExhaustionLevel)
        else (ctx.exhaustion if ctx else ExhaustionLevel.NONE.value)
    )
    if isinstance(exh, ExhaustionLevel):
        exh = exh.value
    tags: List[str] = list(ctx.tags) if ctx else []
    reasons: List[str] = []
    failed: List[str] = []

    if overheated or life == Lifecycle.OVERHEATED.value:
        tags.append("OVERHEAT_NO_CAP")
        reasons.append("OVERHEAT→ExhaustionRisk only (no WATCH cap)")

    filt = cfg.get("filter") or {}
    if status == "ERROR":
        return GradeResultV31(
            grade=Grade.FILTER,
            reasons=["data_status=ERROR"],
            failed_checks=["data_status=ERROR"],
            exhaustion=str(exh),
            trend_state=state,
            path="filter",
        )

    if broken or risk == RiskLevel.BROKEN.value or state in set(filt.get("block_states") or ()):
        return GradeResultV31(
            grade=Grade.FILTER,
            reasons=["broken_or_trend_failure"],
            failed_checks=["broken_or_trend_failure"],
            exhaustion=str(exh),
            trend_state=state,
            path="filter",
            tags=tags,
        )
    if risk in set(filt.get("block_risk") or ()):
        return GradeResultV31(
            grade=Grade.FILTER,
            reasons=[f"risk={risk}"],
            failed_checks=[f"risk={risk}"],
            exhaustion=str(exh),
            trend_state=state,
            path="filter",
            tags=tags,
        )

    if tech_score is None or continuity_score is None:
        return GradeResultV31(
            grade=Grade.FILTER,
            reasons=["tech_or_cont_missing"],
            failed_checks=["tech_or_cont_missing"],
            exhaustion=str(exh),
            trend_state=state,
            path="filter",
            tags=tags,
        )

    tech = float(tech_score)
    cont = float(continuity_score)
    cons = _consumption_for_gate(consumption_score, ctx, cfg)
    htf_ok = bool(ctx.htf_ok) if ctx else False
    strength_states = set((cfg.get("strength_path") or {}).get("states") or ())
    in_strength_state = state in strength_states
    strength_ok = in_strength_state and htf_ok

    if status == "DATA_INSUFFICIENT":
        reasons.append("DATA_INSUFFICIENT: cap at WATCH")
        # fall through to watch/filter only
        strength_ok = False

    # --- try A+ classic ---
    classic = cfg.get("classic_a_plus") or {}
    sp = cfg.get("strength_path") or {}
    cons_max = float(cfg.get("wave_consumption_max_a_plus", classic.get("consumption_max", 70)))

    def sector_ok(min_v: float, fails: List[str]) -> bool:
        if sector_score is None:
            return True
        if float(sector_score) < float(min_v):
            fails.append(f"sector={float(sector_score):.1f}<{float(min_v):.1f}")
            return False
        return True

    def cons_ok(fails: List[str]) -> bool:
        if cons is None:
            return True
        if float(cons) >= cons_max:
            fails.append(f"wave_cons={float(cons):.1f}>={cons_max:.1f}")
            return False
        return True

    relax_states = set(cfg.get("relax_high_risk_in_states") or ())
    allowed_risk = set(classic.get("allowed_risk") or ("SAFE", "WATCH"))
    if state in relax_states:
        allowed_risk = set(classic.get("allowed_risk_relaxed") or allowed_risk | {"HIGH"})

    def risk_ok(fails: List[str]) -> bool:
        if risk not in allowed_risk:
            fails.append(f"risk={risk} not allowed")
            return False
        return True

    # Classic A+
    if status != "DATA_INSUFFICIENT":
        f_classic: List[str] = []
        ok_tech = tech >= float(classic["tech_min"])
        ok_cont = cont >= float(classic["cont_min"])
        if not ok_tech:
            f_classic.append(f"tech={tech:.1f}<{float(classic['tech_min']):.1f}")
        if not ok_cont:
            f_classic.append(f"cont={cont:.1f}<{float(classic['cont_min']):.1f}")
        fund_ok, fund_risk = _fund_allows_a_plus(
            fund_score, strength_ok=strength_ok or (ok_tech and ok_cont and htf_ok),
            cfg=cfg, failed=f_classic, tags=tags,
        )
        if (
            ok_tech
            and ok_cont
            and fund_ok
            and sector_ok(float(classic["sector_min"]), f_classic)
            and cons_ok(f_classic)
            and risk_ok(f_classic)
        ):
            reasons.append("满足 A+ classic 阈值 (V3.1)")
            if fund_risk:
                reasons.append("FundRisk: fund soft band")
            return GradeResultV31(
                grade=Grade.A_PLUS,
                reasons=reasons,
                fund_risk=fund_risk,
                exhaustion=str(exh),
                trend_state=state,
                path="classic",
                tags=tags,
                thresholds_used={"classic": classic, "cons_max": cons_max},
            )

        # Strength A+
        f_s: List[str] = []
        if strength_ok:
            ok_t = tech >= float(sp.get("tech_min", 80))
            ok_c = cont >= float(sp.get("cont_min", 75))
            if not ok_t:
                f_s.append(f"tech={tech:.1f}<{float(sp.get('tech_min', 80)):.1f}")
            if not ok_c:
                f_s.append(f"cont={cont:.1f}<{float(sp.get('cont_min', 75)):.1f}")
            fund_ok2, fund_risk2 = _fund_allows_a_plus(
                fund_score, strength_ok=True, cfg=cfg, failed=f_s, tags=tags
            )
            if (
                ok_t
                and ok_c
                and fund_ok2
                and sector_ok(float(sp.get("sector_min", 70)), f_s)
                and cons_ok(f_s)
                and risk_ok(f_s)
            ):
                reasons.append("满足 A+ strength path (state+HTF, Cont≥75/Tech≥80)")
                tags.append("STRENGTH_PATH")
                if fund_risk2:
                    reasons.append("FundRisk: fund soft band")
                return GradeResultV31(
                    grade=Grade.A_PLUS,
                    reasons=reasons,
                    fund_risk=fund_risk2,
                    exhaustion=str(exh),
                    trend_state=state,
                    path="strength",
                    tags=tags,
                    thresholds_used={"strength": sp, "cons_max": cons_max},
                )
            failed = f_classic + f_s
        else:
            failed = f_classic
            if in_strength_state and not htf_ok:
                failed.append("strength_path blocked: HTF not ok")

    # A band
    a_cfg = cfg.get("a") or {}
    if status != "DATA_INSUFFICIENT":
        f_a: List[str] = []
        if tech >= float(a_cfg.get("tech_min", 75)) and cont >= float(a_cfg.get("cont_min", 70)):
            fund_a_ok = True
            if fund_score is not None and float(fund_score) < float(a_cfg.get("fund_min", 60)):
                fund_a_ok = False
                f_a.append(f"fund<{float(a_cfg.get('fund_min', 60))}")
            sec_a = True
            if sector_score is not None and float(sector_score) < float(a_cfg.get("sector_min", 60)):
                sec_a = False
                f_a.append("sector<a_min")
            cons_a = True
            if cons is not None and float(cons) >= float(a_cfg.get("consumption_max", 80)):
                cons_a = False
                f_a.append("cons>=a_max")
            risk_a = risk in set(a_cfg.get("allowed_risk") or ("SAFE", "WATCH", "WARNING", "HIGH"))
            if not risk_a:
                f_a.append(f"risk={risk}")
            if fund_a_ok and sec_a and cons_a and risk_a:
                reasons.append("满足 A 阈值 (V3.1)")
                return GradeResultV31(
                    grade=Grade.A,
                    reasons=reasons,
                    exhaustion=str(exh),
                    trend_state=state,
                    path="a",
                    tags=tags,
                    thresholds_used={"a": a_cfg},
                )
            failed.extend(f_a)

    # --- hysteresis floor: while in pool, persist prior A+/A if scores only dipped ---
    hyst = cfg.get("hysteresis") or {}
    prior = (ctx.prior_grade if ctx else None) or None
    in_pool = bool(ctx.in_pool) if ctx else False
    if (
        hyst.get("enabled")
        and in_pool
        and prior in set(hyst.get("persist_grades") or ())
        and state not in set(hyst.get("clear_on_states") or ())
    ):
        slack_c = float(hyst.get("cont_slack", 5))
        slack_t = float(hyst.get("tech_slack", 5))
        classic_h = cfg.get("classic_a_plus") or {}
        if prior == "A+" and tech >= float(classic_h["tech_min"]) - slack_t and cont >= float(classic_h["cont_min"]) - slack_c:
            fund_ok, fund_risk = _fund_allows_a_plus(
                fund_score, strength_ok=True, cfg=cfg, failed=[], tags=tags
            )
            if fund_ok or fund_score is None:
                reasons.append(f"hysteresis persist A+ (prior={prior}, in_pool)")
                tags.append("HYSTERESIS")
                return GradeResultV31(
                    grade=Grade.A_PLUS,
                    reasons=reasons,
                    fund_risk=fund_risk,
                    exhaustion=str(exh),
                    trend_state=state,
                    path="hysteresis",
                    tags=tags,
                    thresholds_used={"hysteresis": hyst, "prior": prior},
                )
        if prior in ("A+", "A") and tech >= 75.0 - slack_t and cont >= 70.0 - slack_c:
            reasons.append(f"hysteresis persist A (prior={prior}, in_pool)")
            tags.append("HYSTERESIS")
            return GradeResultV31(
                grade=Grade.A,
                reasons=reasons,
                fund_risk=False,
                exhaustion=str(exh),
                trend_state=state,
                path="hysteresis",
                tags=tags,
                thresholds_used={"hysteresis": hyst, "prior": prior},
            )

    # B
    b_cfg = cfg.get("b") or {}
    if tech >= float(b_cfg.get("tech_min", 65)) and cont >= float(b_cfg.get("cont_min", 60)):
        reasons.append("满足 B 阈值 (V3.1)")
        return GradeResultV31(
            grade=Grade.B,
            reasons=reasons,
            failed_checks=failed,
            exhaustion=str(exh),
            trend_state=state,
            path="b",
            tags=tags,
            thresholds_used={"b": b_cfg},
        )

    # WATCH
    w_cfg = cfg.get("watch") or {}
    if tech >= float(w_cfg.get("tech_min", 55)) and cont >= float(w_cfg.get("cont_min", 50)):
        reasons.append("WATCH floors")
        return GradeResultV31(
            grade=Grade.WATCH,
            reasons=reasons,
            failed_checks=failed,
            exhaustion=str(exh),
            trend_state=state,
            path="watch",
            tags=tags,
        )

    return GradeResultV31(
        grade=Grade.FILTER,
        reasons=["未达 WATCH"] + failed,
        failed_checks=failed,
        exhaustion=str(exh),
        trend_state=state,
        path="filter",
        tags=tags,
    )


__all__ = ["GradeResultV31", "grade_candidate_v31"]
