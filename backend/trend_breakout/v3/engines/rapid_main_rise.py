"""
V3.1.8 RAPID_MAIN_RISE path — short/fast main-rise without waiting for MATURE.

Allows TrendGrade A+/A with Maturity=BUILDING and EntryStatus=FOLLOW
(never PRIME). Solves 会稽山-class misses. No symbol/date branches.
Observation only. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from v3.v31.config import get_v318_rapid


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


@dataclass
class RapidMainRiseResult:
    active: bool = False
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "score": self.score,
            "reasons": list(self.reasons),
            "failed": list(self.failed),
            "path": self.path,
        }


def detect_rapid_main_rise(
    *,
    trend_state: Optional[str] = None,
    return20: Optional[float] = None,
    trend_return: Optional[float] = None,
    trend_age_days: Optional[int] = None,
    continuity_score: Optional[float] = None,
    ma5_gt_ma10_gt_ma20: Optional[bool] = None,
    higher_low_intact: Optional[bool] = None,
    higher_high: Optional[bool] = None,
    vol_ratio: Optional[float] = None,
    exhaustion: Optional[str] = None,
    broken: bool = False,
    config: Optional[Dict[str, Any]] = None,
) -> RapidMainRiseResult:
    """Generic RAPID_MAIN_RISE detector (as-of ≤ t only)."""
    cfg = config or get_v318_rapid()
    reasons: List[str] = []
    failed: List[str] = []
    state = str(trend_state or "").upper()

    if broken or state in ("TREND_FAILURE", "NONE", "OBSERVE"):
        return RapidMainRiseResult(failed=["dead_or_broken"])

    alive = set(cfg.get("alive_states") or ())
    if state and state not in alive:
        failed.append(f"state={state} not alive")

    if cfg.get("block_if_exhaustion_high") and str(exhaustion or "").upper() == "HIGH":
        failed.append("exhaustion=HIGH")

    r20 = _f(return20)
    tr = _f(trend_return)
    age = int(trend_age_days or 0)
    gain_ok = False
    if r20 is not None and r20 >= float(cfg.get("return20_min", 0.15)):
        gain_ok = True
        reasons.append(f"return20={r20:.1%}>={cfg.get('return20_min', 0.15):.0%}")
    if (
        tr is not None
        and tr >= float(cfg.get("trend_return_min", 0.12))
        and age <= int(cfg.get("trend_return_age_max", 25))
    ):
        gain_ok = True
        reasons.append(f"trendReturn={tr:.1%} age={age}<={cfg.get('trend_return_age_max', 25)}")
    if not gain_ok:
        failed.append("short_gain_insufficient")

    cont = _f(continuity_score)
    cont_min = float(cfg.get("cont_min", 75.0))
    if cont is None or cont < cont_min:
        failed.append(f"cont={cont}<{cont_min}")
    else:
        reasons.append(f"cont={cont:.1f}")

    if cfg.get("require_ma_stack", True):
        if ma5_gt_ma10_gt_ma20 is False:
            failed.append("ma_stack_fail")
        elif ma5_gt_ma10_gt_ma20 is True:
            reasons.append("MA5>MA10>MA20")
        # None → soft: do not hard-fail (metrics may be missing on some paths)
        elif ma5_gt_ma10_gt_ma20 is None:
            reasons.append("ma_stack_unknown")

    if cfg.get("require_hl_intact", True):
        # Early RAPID often has not printed HL yet — only hard-fail when HL is broken.
        # higher_low_intact=False without broken flag → soft (no fail).
        if higher_low_intact is True:
            reasons.append("HL_intact")
        elif higher_low_intact is False:
            reasons.append("HL_not_yet")  # soft for RAPID early main-rise
        if higher_high is True:
            reasons.append("HH")

    vr = _f(vol_ratio)
    vmax = float(cfg.get("vol_ratio_max", 4.0))
    if vr is not None and vr > vmax:
        failed.append(f"vol_ratio={vr:.1f}>{vmax}")
    elif vr is not None:
        reasons.append(f"vol_ok={vr:.1f}")

    active = not failed
    score = 0.0
    if active:
        # soft quality 0–100
        score = 55.0
        if r20 is not None:
            score += min(20.0, max(0.0, (r20 - 0.15) / 0.30 * 20.0))
        if cont is not None:
            score += min(15.0, max(0.0, (cont - 75.0) / 25.0 * 15.0))
        if ma5_gt_ma10_gt_ma20:
            score += 5.0
        if higher_low_intact:
            score += 5.0
        score = min(100.0, score)
        reasons.append("RAPID_MAIN_RISE")

    return RapidMainRiseResult(
        active=active,
        score=round(score, 2),
        reasons=reasons,
        failed=failed,
        path="RAPID_MAIN_RISE" if active else "",
    )


__all__ = ["RapidMainRiseResult", "detect_rapid_main_rise"]
