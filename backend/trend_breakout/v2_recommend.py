#!/usr/bin/env python3
"""
V2.3 RecommendationScore + ResonanceBonus + RecommendationReasonEngine.

RecommendationScore = 规则优先观察强度 (NOT return probability).
No TotalScore summing all dimensions.
No buy / must-rise language.

ResonanceBonus — ONE coherent additive path (cap 15), folding §12–15 discrete adds:
  Documented scheme (avoids double-counting with Base weights):
    Stock monthly STRONG_UP: +7
    Stock monthly UP:        +4
    Stock monthly DOWN:      -5   (tag MONTHLY_CONFLICT; NOT hard-filter)
    Sector daily STRONG_UP:  +5
    Sector daily UP:         +3
    Sector monthly UP/STRONG:+3
    RS20 leader (>=8):       +3 ; (>=3): +2
    Continuity >=80:         +2
  Cap = min(15, sum). Consumption penalty applied AFTER base+bonus.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# Base weights (sum=1.0 when all present)
_WEIGHTS = {
    "tech": 0.25,
    "continuity": 0.25,
    "fund": 0.15,
    "sector": 0.10,  # legacy SectorScore
    "stock_monthly": 0.10,
    "sector_daily": 0.10,
    "risk_adj": 0.05,  # (100 - RiskNumeric)
}

_RISK_MAP = {"HIGH": 80.0, "MID": 40.0, "LOW": 15.0}

_LEVELS = [
    (90, "A+"),
    (80, "A"),
    (70, "B"),
    (60, "C"),
    (0, "D"),
]


def _level_of(score: float) -> str:
    for thr, lab in _LEVELS:
        if score >= thr:
            return lab
    return "D"


def _risk_numeric(risk_level: Any, risk_dict: Optional[Dict] = None) -> Tuple[Optional[float], str]:
    """
    Return (risk_numeric_0_100, source).
    Prefer numeric RiskScore if present; else map HIGH/MID/LOW.
    """
    if risk_dict:
        # some engines may expose numeric
        for k in ("risk_numeric", "score_numeric", "numeric_score"):
            v = risk_dict.get(k)
            if v is not None:
                try:
                    return float(v), "numeric"
                except Exception:
                    pass
        # risk_score may already be numeric 0-100
        rs = risk_dict.get("risk_score")
        if isinstance(rs, (int, float)) and not isinstance(rs, bool):
            return float(rs), "numeric"
        if isinstance(rs, str) and rs.replace(".", "", 1).isdigit():
            return float(rs), "numeric"
        if isinstance(rs, str) and rs.upper() in _RISK_MAP:
            return _RISK_MAP[rs.upper()], "mapped"

    if risk_level is not None:
        if isinstance(risk_level, (int, float)):
            return float(risk_level), "numeric"
        s = str(risk_level).upper()
        if s in _RISK_MAP:
            return _RISK_MAP[s], "mapped"
    return None, "missing"


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except Exception:
        return None


@dataclass
class RecommendationResult:
    ok: bool = False
    score: Optional[float] = None
    level: str = "D"
    label: str = "规则优先观察强度"  # never "收益概率"
    base_score: Optional[float] = None
    resonance_bonus: float = 0.0
    consumption_penalty: float = 0.0
    resonanceTags: List[str] = field(default_factory=list)
    reason: List[str] = field(default_factory=list)
    risk: List[str] = field(default_factory=list)
    checklist: Dict[str, str] = field(default_factory=dict)
    hard_filtered: bool = False
    hard_filter_reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    weights_used: Dict[str, float] = field(default_factory=dict)
    components: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RecommendationReasonEngine:
    """RULE TEMPLATES only — no LLM."""

    @staticmethod
    def build(
        *,
        tech: Optional[float],
        cont: Optional[float],
        fund: Optional[float],
        sector_legacy: Optional[float],
        monthly_trend: Optional[str],
        monthly_score: Optional[float],
        sector_daily_trend: Optional[str],
        sector_daily_score: Optional[float],
        sector_monthly_trend: Optional[str],
        rs20: Optional[float],
        rs_bucket: Optional[str],
        consumption: Optional[float],
        risk_level: Optional[str],
        phase: Optional[str],
        phase_cn: Optional[str],
        tags: List[str],
        level: str,
        score: Optional[float],
    ) -> Tuple[List[str], List[str], Dict[str, str]]:
        reasons: List[str] = []
        risks: List[str] = []
        ck: Dict[str, str] = {}

        # Core reason templates (observation language only)
        if tech is not None and tech >= 80:
            reasons.append("技术结构评分达标（≥80），阶梯式突破结构成立")
            ck["技术结构"] = "✓"
        elif tech is not None and tech >= 70:
            reasons.append("技术结构评分可观察（≥70）")
            ck["技术结构"] = "✓"
        else:
            ck["技术结构"] = "✗" if tech is not None else "⚠"

        if cont is not None and cont >= 80:
            reasons.append("趋势持续性评分偏高（≥80），结构延续状态较好")
            ck["持续性"] = "✓"
        elif cont is not None and cont >= 60:
            reasons.append("趋势持续性评分尚可（≥60）")
            ck["持续性"] = "✓"
        else:
            ck["持续性"] = "✗" if cont is not None else "⚠"

        if monthly_trend == "MONTHLY_STRONG_UP":
            reasons.append("个股月线趋势为强势向上（MA多头+斜率共振）")
            ck["月线趋势"] = "✓"
        elif monthly_trend == "MONTHLY_UP":
            reasons.append("个股月线趋势向上")
            ck["月线趋势"] = "✓"
        elif monthly_trend == "MONTHLY_DOWN":
            reasons.append("个股月线趋势向下（仅降低观察强度，不作硬过滤）")
            ck["月线趋势"] = "⚠"
            risks.append("月线趋势向下，与日线可能冲突（MONTHLY_CONFLICT）")
        else:
            ck["月线趋势"] = "—"

        if sector_daily_trend == "STRONG_UP":
            reasons.append("所属板块日线趋势强势向上，同行等权均价结构偏强")
            ck["板块日趋势"] = "✓"
        elif sector_daily_trend == "UP":
            reasons.append("所属板块日线趋势向上")
            ck["板块日趋势"] = "✓"
        elif sector_daily_trend == "DOWN":
            reasons.append("所属板块日线趋势向下（仅降权，不作硬过滤）")
            ck["板块日趋势"] = "⚠"
            risks.append("板块日线偏弱，个股相对独立上行需谨慎解读")
        else:
            ck["板块日趋势"] = "—"

        if sector_monthly_trend in ("UP", "STRONG_UP"):
            reasons.append("所属板块月线趋势向上，中期板块环境偏顺")
            ck["板块月趋势"] = "✓"
        elif sector_monthly_trend == "DOWN":
            ck["板块月趋势"] = "⚠"
        else:
            ck["板块月趋势"] = "—"

        if "SECTOR_LEADER" in tags or (rs20 is not None and rs20 >= 3):
            reasons.append(f"相对板块强度偏强（RS20={rs20}，{rs_bucket or ''}）")
            ck["相对强度"] = "✓"
        elif rs20 is not None and rs20 < 0:
            risks.append(f"相对板块偏弱（RS20={rs20}）")
            ck["相对强度"] = "⚠"
        else:
            ck["相对强度"] = "—"

        if fund is not None and fund >= 60:
            reasons.append("基本面维度达到观察门槛（≥60）")
            ck["基本面"] = "✓"
        elif fund is None:
            ck["基本面"] = "⚠"
        else:
            ck["基本面"] = "—"

        if phase in ("SECOND_BREAKOUT", "MAIN_RISE", "MAIN_RISE_ACCEL", "FIRST_BREAKOUT"):
            reasons.append(f"结构阶段为{phase_cn or phase}，属于趋势确认观察区间")
            ck["结构阶段"] = "✓"
        else:
            ck["结构阶段"] = "—"

        if "DAILY_UP" in tags or (tech is not None and tech >= 70 and phase not in ("TREND_FAILURE", "NONE", None)):
            if "日线结构偏多头/突破确认观察" not in "".join(reasons):
                pass  # covered by tech/phase

        # Multi-period resonance summary
        if (
            monthly_trend in ("MONTHLY_UP", "MONTHLY_STRONG_UP")
            and sector_daily_trend in ("UP", "STRONG_UP")
        ):
            reasons.append("多周期共振：个股月线向上 + 板块日线向上（观察强度加权）")
            ck["多周期共振"] = "✓"
        else:
            ck["多周期共振"] = "—"

        # Risks / consumption
        if consumption is not None and consumption >= 80:
            risks.append("趋势消耗度偏高（≥80），存在透支风险标签 HIGH_CONSUMPTION")
            ck["消耗度"] = "✗"
        elif consumption is not None and consumption >= 70:
            risks.append("趋势消耗度偏高（70–79），观察强度已扣减")
            ck["消耗度"] = "⚠"
        elif consumption is not None and consumption >= 50:
            risks.append("趋势消耗度中等（50–69），仅提示不扣分")
            ck["消耗度"] = "—"
        else:
            ck["消耗度"] = "✓"

        rl = str(risk_level or "").upper()
        if rl == "HIGH":
            risks.append("交易风险评级为 HIGH")
            ck["交易风险"] = "✗"
        elif rl == "MID":
            ck["交易风险"] = "⚠"
        else:
            ck["交易风险"] = "✓" if rl == "LOW" else "—"

        if "MONTHLY_CONFLICT" in tags:
            if not any("月线" in r for r in risks):
                risks.append("日线与月线方向可能冲突（MONTHLY_CONFLICT）")

        # Ensure A+/A have non-empty reasons
        if level in ("A+", "A") and not reasons:
            reasons.append(
                f"规则观察强度等级 {level}（推荐分仅表示规则优先观察强度，非收益概率）"
            )

        # Never use "推荐分92" alone as the only reason — always keep structural lines above.
        # Append strength label as trailing note only when other reasons exist.
        if score is not None and reasons:
            reasons.append(
                f"规则优先观察强度={score:.0f}（等级{level}；非收益预测、非买入建议）"
            )

        return reasons, risks, ck


class RecommendationEngine:
    """Compute RecommendationScore + resonance + hard-filter flag."""

    def evaluate(self, rec: Dict[str, Any]) -> RecommendationResult:
        out = RecommendationResult()
        tech = _f(rec.get("tech_score", rec.get("score")))
        cont = _f(rec.get("continuity_score"))
        fund = _f(rec.get("fund_score"))
        sector_legacy = _f(rec.get("sector_score"))
        consumption = _f(rec.get("consumption_score"))
        phase = rec.get("phase")
        phase_cn = rec.get("phase_cn")

        monthly = rec.get("stockMonthlyTrend") or rec.get("stock_monthly") or {}
        if isinstance(monthly, dict):
            monthly_trend = monthly.get("trend")
            monthly_score = _f(monthly.get("score"))
        else:
            monthly_trend, monthly_score = None, None

        sector_tr = rec.get("sectorTrend") or rec.get("sector_trend") or {}
        if isinstance(sector_tr, dict):
            sec_daily_trend = sector_tr.get("dailyTrend")
            sec_daily_score = _f(sector_tr.get("dailyTrendScore"))
            sec_monthly_trend = sector_tr.get("monthlyTrend")
            sec_monthly_score = _f(sector_tr.get("monthlyTrendScore"))
            rs20 = _f(sector_tr.get("relativeStrength20"))
            rs_bucket = sector_tr.get("relativeStrengthBucket")
        else:
            sec_daily_trend = sec_daily_score = sec_monthly_trend = None
            sec_monthly_score = rs20 = None
            rs_bucket = None

        risk_d = rec.get("risk") or {}
        risk_level = rec.get("risk_level") or risk_d.get("risk_score")
        risk_num, risk_src = _risk_numeric(risk_level, risk_d if isinstance(risk_d, dict) else None)
        risk_adj = (100.0 - risk_num) if risk_num is not None else None

        # ---- BaseRecommendationScore with reweight on missing ----
        components = {
            "tech": tech,
            "continuity": cont,
            "fund": fund,
            "sector": sector_legacy,
            "stock_monthly": monthly_score,
            "sector_daily": sec_daily_score,
            "risk_adj": risk_adj,
        }
        available = {k: v for k, v in components.items() if v is not None}
        notes: List[str] = []
        if len(available) < len(components):
            missing = [k for k in components if k not in available]
            notes.append("DATA_INSUFFICIENT:" + ",".join(missing))
        if not available:
            out.notes = notes + ["无可用维度，无法计算推荐观察强度"]
            out.hard_filtered = True
            out.hard_filter_reasons = ["NO_SCORES"]
            return out

        w_sum = sum(_WEIGHTS[k] for k in available)
        weights_used = {k: _WEIGHTS[k] / w_sum for k in available}
        base = sum(available[k] * weights_used[k] for k in available)
        base = float(round(base, 2))

        # ---- ResonanceBonus (single coherent path, cap 15) ----
        # Folds §12–15 discrete adds (+10/+7/+8/+12/-8/+5/+3/-5/+5 RS) into one scheme.
        bonus = 0.0
        tags: List[str] = []

        # Daily structure "up" proxy
        daily_up = (
            tech is not None
            and tech >= 70
            and phase not in ("TREND_FAILURE", "NONE", None, "OVERHEATED")
        )
        if daily_up:
            tags.append("DAILY_UP")

        if monthly_trend == "MONTHLY_STRONG_UP":
            bonus += 7.0
            tags.append("MONTHLY_STRONG_UP")
            if daily_up:
                bonus += 1.0  # mild daily+monthly strong resonance (kept small to avoid double-count)
        elif monthly_trend == "MONTHLY_UP":
            bonus += 4.0
            tags.append("MONTHLY_UP")
        elif monthly_trend == "MONTHLY_DOWN":
            bonus -= 5.0
            tags.append("MONTHLY_CONFLICT")
            tags.append("MONTHLY_DOWN")

        if sec_daily_trend == "STRONG_UP":
            bonus += 5.0
            tags.append("SECTOR_DAILY_STRONG")
        elif sec_daily_trend == "UP":
            bonus += 3.0
            tags.append("SECTOR_DAILY_UP")
        elif sec_daily_trend == "DOWN":
            tags.append("SECTOR_DAILY_DOWN")
            # soft: small negative already partly in sector_daily score weight; tiny tag-only

        if sec_monthly_trend in ("UP", "STRONG_UP"):
            bonus += 3.0
            tags.append("SECTOR_MONTHLY_UP")
        elif sec_monthly_trend == "DOWN":
            tags.append("SECTOR_MONTHLY_DOWN")

        if rs20 is not None:
            if rs20 >= 8:
                bonus += 3.0
                tags.append("SECTOR_LEADER")
            elif rs20 >= 3:
                bonus += 2.0
                tags.append("SECTOR_LEADER")

        if cont is not None and cont >= 80:
            bonus += 2.0
            tags.append("CONTINUITY_HIGH")

        # Cap resonance at 15 (floor -8 for conflict-heavy cases before consumption)
        bonus = float(max(-8.0, min(15.0, bonus)))

        # ---- TrendConsumption penalty (after bonus) ----
        cons_pen = 0.0
        if consumption is not None:
            if consumption >= 80:
                cons_pen = 10.0
                tags.append("HIGH_CONSUMPTION")
            elif consumption >= 70:
                cons_pen = 5.0
            elif consumption >= 50:
                notes.append("消耗度50–69：仅提示，不扣推荐观察强度")

        raw = base + bonus - cons_pen
        score = float(round(min(100.0, max(0.0, raw)), 1))
        level = _level_of(score)

        # ---- Hard filter (recommendation list only) ----
        hard_reasons: List[str] = []
        if tech is None or tech < 70:
            hard_reasons.append("Tech<70")
        if cont is None or cont < 60:
            hard_reasons.append("Continuity<60")
        rl = str(risk_level or "").upper()
        if rl == "HIGH":
            hard_reasons.append("Risk=HIGH")
        if consumption is not None and consumption >= 90:
            hard_reasons.append("Consumption>=90")
        if phase == "TREND_FAILURE" or (isinstance(rec.get("tags"), list) and "TREND_FAILURE" in rec["tags"]):
            hard_reasons.append("TREND_FAILURE")
        # also check continuity tags / structure risks
        risks_list = rec.get("risks") or []
        if "TREND_FAILURE" in (risks_list if isinstance(risks_list, list) else []):
            if "TREND_FAILURE" not in hard_reasons:
                hard_reasons.append("TREND_FAILURE")

        hard_filtered = len(hard_reasons) > 0

        reason, risk_msgs, checklist = RecommendationReasonEngine.build(
            tech=tech,
            cont=cont,
            fund=fund,
            sector_legacy=sector_legacy,
            monthly_trend=monthly_trend,
            monthly_score=monthly_score,
            sector_daily_trend=sec_daily_trend,
            sector_daily_score=sec_daily_score,
            sector_monthly_trend=sec_monthly_trend,
            rs20=rs20,
            rs_bucket=rs_bucket,
            consumption=consumption,
            risk_level=str(risk_level) if risk_level is not None else None,
            phase=phase,
            phase_cn=phase_cn,
            tags=tags,
            level=level,
            score=score,
        )

        out.ok = True
        out.score = score
        out.level = level
        out.base_score = base
        out.resonance_bonus = bonus
        out.consumption_penalty = cons_pen
        out.resonanceTags = tags
        out.reason = reason
        out.risk = risk_msgs
        out.checklist = checklist
        out.hard_filtered = hard_filtered
        out.hard_filter_reasons = hard_reasons
        out.notes = notes
        out.weights_used = {k: round(v, 4) for k, v in weights_used.items()}
        out.components = {
            "values": components,
            "risk_numeric": risk_num,
            "risk_src": risk_src,
            "base": base,
            "bonus": bonus,
            "consumption_penalty": cons_pen,
        }
        return out


def passes_recommendation_hard_filter(rec: Dict[str, Any]) -> bool:
    """True if eligible for recommendation_list."""
    r = rec.get("recommendation") or {}
    if r.get("hard_filtered"):
        return False
    return True


def sort_recommendation_key(rec: Dict[str, Any]):
    """RecScore DESC, Cont, Tech, SectorDailyTrendScore, Consumption ASC."""
    r = rec.get("recommendation") or {}
    st = rec.get("sectorTrend") or {}
    return (
        -float(r.get("score") or 0),
        -float(rec.get("continuity_score") or 0),
        -float(rec.get("tech_score") or rec.get("score") or 0),
        -float(st.get("dailyTrendScore") or 0),
        float(rec.get("consumption_score") or 0),
    )


__all__ = [
    "RecommendationEngine",
    "RecommendationReasonEngine",
    "RecommendationResult",
    "passes_recommendation_hard_filter",
    "sort_recommendation_key",
]
