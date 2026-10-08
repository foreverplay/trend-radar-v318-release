"""
V3.1.8 live as-of layer — reuses V3.1.7 replay engines (state/maturity/realized),
then applies independent TrendGrade + RAPID_MAIN_RISE + EntryStatus overlay.

Hard rules: hist truncated to date <= T; no symbol/date branches; no future labels
into Grade/Maturity/Entry. Observation only. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from v3.engines.rapid_main_rise import detect_rapid_main_rise
from v3.grading_v318 import grade_trend_v318
from v3.v31.entry_window import apply_v318_entry_overlay
from v3.v31.live_v317 import (
    DEFAULT_WARMUP,
    evaluate_v317_asof,
    load_hist_to_t,
    make_builder,
)
from v3.v31.types import EntryWindow, ExhaustionLevel


def evaluate_v318_asof(
    code6: str,
    name: str,
    t_date: str,
    *,
    hist=None,
    builder: Any = None,
    warmup: int = DEFAULT_WARMUP,
    allow_remote: bool = True,
    # optional live scan scores for Grade (preferred over snap-only)
    tech_score: Optional[float] = None,
    continuity_score: Optional[float] = None,
    sector_score: Optional[float] = None,
    fund_risk_level: Optional[str] = None,
    legacy_grade: Optional[str] = None,
    broken: bool = False,
) -> Dict[str, Any]:
    """Return V3.1.7 row + V3.1.8 TrendGrade / RAPID / EntryStatus overlay."""
    out = evaluate_v317_asof(
        code6,
        name,
        t_date,
        hist=hist,
        builder=builder,
        warmup=warmup,
        allow_remote=allow_remote,
    )
    out["version"] = "3.1.8"
    if not out.get("ok"):
        out["grade_v318"] = None
        out["rapid_main_rise"] = False
        out["entry_window_v318"] = out.get("entry_window")
        return out

    tech = tech_score if tech_score is not None else out.get("tech")
    cont = continuity_score if continuity_score is not None else out.get("cont")
    sector = sector_score if sector_score is not None else out.get("sector")
    fund = fund_risk_level or out.get("fund_risk")
    state = str(out.get("state") or "")
    mat = str(out.get("maturity_level") or "")
    exh = str(out.get("exhaustion") or "NORMAL")

    # MA / HL hints from replay row when present
    ma_stack = out.get("ma5_gt_ma10_gt_ma20")
    if ma_stack is None:
        # soft inference: MAIN_RISE/ACCEL often have stack; leave None
        ma_stack = None
    hl_intact = out.get("struct_higherLow")
    if hl_intact is None and out.get("higher_low_broken") is not None:
        hl_intact = not bool(out.get("higher_low_broken"))

    rapid = detect_rapid_main_rise(
        trend_state=state,
        return20=out.get("return20"),
        trend_return=out.get("trend_return"),
        trend_age_days=out.get("trend_age"),
        continuity_score=cont,
        ma5_gt_ma10_gt_ma20=ma_stack,
        higher_low_intact=hl_intact,
        higher_high=bool(out.get("struct_reBreakout") or out.get("struct_firstBreakout")),
        vol_ratio=out.get("vol_ratio"),
        exhaustion=exh,
        broken=broken or state == "TREND_FAILURE",
    )

    g = grade_trend_v318(
        trend_structure_score=out.get("trend_structure_score"),
        continuity_score=cont,
        sector_score=sector,
        tech_score=tech,
        fund_risk_level=fund,
        trend_state=state,
        broken=broken or state == "TREND_FAILURE",
        rapid_main_rise=bool(rapid.active),
        htf_ok=out.get("htf_ok"),
        legacy_grade=legacy_grade,
    )

    base_ew = str(out.get("entry_window") or EntryWindow.NO_ENTRY.value)
    try:
        ew_enum = EntryWindow(base_ew)
    except Exception:
        ew_enum = EntryWindow.NO_ENTRY
    ew318, ew_notes = apply_v318_entry_overlay(
        entry_window=ew_enum,
        maturity_level=mat,
        rapid_main_rise=bool(rapid.active),
        exhaustion=ExhaustionLevel.coerce(exh),
        notes=[],
    )

    out["rapid_main_rise"] = bool(rapid.active)
    out["rapid"] = rapid.to_dict()
    out["grade_v318"] = g.grade.value
    out["grade_v318_path"] = g.path
    out["grade_v318_tags"] = list(g.tags)
    out["grade_v318_reasons"] = list(g.reasons)
    out["structure_score"] = g.structure_score
    out["structure_source"] = g.structure_source
    out["legacy_grade"] = legacy_grade
    out["entry_window_v317"] = base_ew
    out["entry_window_v318"] = ew318.value
    out["entry_window"] = ew318.value  # publish uses this as EntryStatus
    out["entry_window_notes_v318"] = ew_notes
    return out


__all__ = [
    "evaluate_v318_asof",
    "load_hist_to_t",
    "make_builder",
    "DEFAULT_WARMUP",
]
