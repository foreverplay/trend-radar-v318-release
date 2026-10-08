#!/usr/bin/env python3
"""
V2.2 tag classification — CORE_TREND / OBSERVATION / HIGH_RISK / FILTER.

CORE_TREND / CORE_CANDIDATE (alias):
  Tech≥80 & Fund≥60 & Sector≥60 & Continuity≥75
  & Risk≠HIGH & Consumption<70
  & phase in (SECOND_BREAKOUT, MAIN_RISE, MAIN_RISE_ACCEL)

Continuity<70 → cannot be core (OBSERVATION or HIGH_RISK as appropriate).
Weak sector continuity (component ≤5 / 15) → force OBSERVATION even if tech strong.

If Fund DATA_INSUFFICIENT: cannot be CORE solely on tech.
No TotalScore sum. No buy advice. No ZhuangStock.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

CORE_PHASES = {"SECOND_BREAKOUT", "MAIN_RISE_ACCEL", "MAIN_RISE"}


def _tech_score(rec: Dict[str, Any]) -> float:
    try:
        return float(rec.get("score") or rec.get("tech_score") or 0)
    except Exception:
        return 0.0


def _cont_fields(rec: Dict[str, Any], continuity: Optional[Dict[str, Any]]):
    cont = continuity or rec.get("continuity") or {}
    try:
        c_score = cont.get("continuity_score")
        c_score_f = float(c_score) if c_score is not None else None
    except Exception:
        c_score_f = None
    try:
        cons = cont.get("consumption_score")
        cons_f = float(cons) if cons is not None else None
    except Exception:
        cons_f = None
    try:
        sec_c = cont.get("sector_continuity")
        # also peek components.sector.points
        if sec_c is None:
            sec_c = ((cont.get("components") or {}).get("sector") or {}).get("points")
        sec_c_f = float(sec_c) if sec_c is not None else None
    except Exception:
        sec_c_f = None
    ctype = cont.get("continuity_type") or cont.get("type")
    return c_score_f, cons_f, sec_c_f, ctype, cont


def classify_v22(
    rec: Dict[str, Any],
    fund: Optional[Dict[str, Any]] = None,
    sector: Optional[Dict[str, Any]] = None,
    risk: Optional[Dict[str, Any]] = None,
    continuity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns dict with:
      tag, tag_cn, why (list), notes, dimensions checklist
    """
    fund = fund or rec.get("fundamental") or {}
    sector = sector or rec.get("sector") or {}
    risk = risk or rec.get("risk") or {}
    c_score_f, cons_f, sec_c_f, ctype, cont = _cont_fields(rec, continuity)

    tech = _tech_score(rec)
    phase = str(rec.get("phase") or "")
    overheated = bool(
        rec.get("overheated")
        or phase == "OVERHEATED"
        or rec.get("grade") in ("OH",)
    )

    fund_insuff = bool(fund.get("data_insufficient"))
    fund_score = fund.get("score")
    try:
        fund_score_f = float(fund_score) if fund_score is not None else None
    except Exception:
        fund_score_f = None

    sec_insuff = bool(sector.get("data_insufficient"))
    sec_score = sector.get("score")
    try:
        sec_score_f = float(sec_score) if sec_score is not None else None
    except Exception:
        sec_score_f = None

    risk_level = str(risk.get("risk_score") or "MID").upper()
    broken = bool(risk.get("broken")) or ("BROKEN" in (risk.get("tags") or [])) or phase in (
        "TREND_FAILURE",
        "BROKEN",
    )

    why: List[str] = []
    notes: List[str] = []

    # FILTER first
    if broken or tech < 65:
        tag = "FILTER"
        tag_cn = "过滤"
        if broken:
            why.append("BROKEN/趋势破坏")
        if tech < 65:
            why.append(f"技术分{tech:.0f}<65")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # Weak sector continuity (≤5 of 15) → force OBSERVATION even if tech strong
    weak_sector_cont = sec_c_f is not None and sec_c_f <= 5.0

    # HIGH_RISK: strong tech but weak fund or HIGH risk / overheated
    strong_tech = tech >= 75
    fund_weak = (fund_score_f is not None and fund_score_f < 50) or (
        not fund_insuff and fund_score_f is None and fund.get("level") == "风险"
    )
    if (
        (strong_tech and (fund_weak or risk_level == "HIGH" or overheated))
        or (overheated and tech >= 65)
        or (risk_level == "HIGH" and tech >= 65 and not broken)
    ):
        tag = "HIGH_RISK"
        tag_cn = "高风险"
        if fund_weak:
            why.append(f"基本面偏弱({fund_score_f})")
        if risk_level == "HIGH":
            why.append("交易风险=HIGH")
        if overheated:
            why.append("过热/高潮")
        why.append(f"技术仍强({tech:.0f})")
        if c_score_f is not None and c_score_f < 70:
            why.append(f"持续性{c_score_f:.0f}<70不可核心")
        if weak_sector_cont:
            notes.append("板块持续性≤5，即使技术强也不得进核心")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # Continuity gate: <70 never core → OBSERVATION (if not already HIGH_RISK)
    if c_score_f is not None and c_score_f < 70 and tech >= 65:
        # fall through to observation path below, but mark reason early
        pass

    # CORE_TREND
    fund_ok = (not fund_insuff) and fund_score_f is not None and fund_score_f >= 60
    sec_ok = (not sec_insuff) and sec_score_f is not None and sec_score_f >= 60
    risk_ok = risk_level != "HIGH"
    phase_ok = phase in CORE_PHASES
    cont_ok = c_score_f is not None and c_score_f >= 75
    cons_ok = cons_f is not None and cons_f < 70
    # If continuity not computed yet, cannot CORE
    cont_present = c_score_f is not None and cons_f is not None

    if (
        tech >= 80
        and fund_ok
        and sec_ok
        and risk_ok
        and phase_ok
        and not overheated
        and cont_present
        and cont_ok
        and cons_ok
        and not weak_sector_cont
    ):
        tag = "CORE_TREND"
        tag_cn = "核心趋势"
        why.append(f"技术{tech:.0f}≥80")
        why.append(f"基本面{fund_score_f:.0f}≥60")
        why.append(f"板块{sec_score_f:.0f}≥60")
        why.append(f"持续性{c_score_f:.0f}≥75")
        why.append(f"消耗度{cons_f:.0f}<70")
        why.append(f"风险{risk_level}")
        why.append(f"阶段{phase}")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # Weak sector continuity force OBSERVATION
    if weak_sector_cont and tech >= 65:
        tag = "OBSERVATION"
        tag_cn = "观察"
        notes.append("板块持续性≤5：强制观察（即使技术强）")
        why.append(f"板块持续性{sec_c_f:.1f}≤5")
        why.append(f"技术{tech:.0f}")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # Continuity <70 → cannot be core
    if c_score_f is not None and c_score_f < 70 and tech >= 75:
        tag = "OBSERVATION"
        tag_cn = "观察"
        why.append(f"持续性{c_score_f:.0f}<70不可核心")
        why.append(f"技术{tech:.0f}")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # Fund insufficient → cannot CORE on tech alone
    if fund_insuff and tech >= 75:
        tag = "OBSERVATION"
        tag_cn = "观察"
        notes.append("基本面DATA_INSUFFICIENT：不可仅凭技术进核心")
        why.append(f"技术{tech:.0f}达标但基本面不足")
        if not phase_ok:
            why.append(f"阶段非二次/主升({phase})")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # OBSERVATION: Tech≥75 else weaker dimensions / no second break / cont/cons miss
    if tech >= 75:
        tag = "OBSERVATION"
        tag_cn = "观察"
        why.append(f"技术{tech:.0f}≥75")
        if not fund_ok:
            why.append("基本面未达60或不足")
        if not sec_ok:
            why.append("板块未达60或不足")
        if not phase_ok:
            why.append(f"非核心阶段({phase})")
        if not risk_ok:
            why.append(f"风险{risk_level}")
        if not cont_present:
            why.append("持续性未计算")
        elif not cont_ok:
            why.append(f"持续性未达75({c_score_f})")
        if cont_present and not cons_ok:
            why.append(f"消耗度≥70({cons_f})")
        return _pack(
            tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
            fund, sector, risk, rec, c_score_f, cons_f, ctype,
        )

    # 65≤tech<75 → observation (weaker)
    tag = "OBSERVATION"
    tag_cn = "观察"
    why.append(f"技术{tech:.0f}观察档")
    if not phase_ok:
        why.append(f"阶段{phase}")
    if c_score_f is not None and c_score_f < 70:
        why.append(f"持续性{c_score_f:.0f}<70")
    return _pack(
        tag, tag_cn, why, notes, tech, fund_score_f, sec_score_f, risk_level,
        fund, sector, risk, rec, c_score_f, cons_f, ctype,
    )


def classify_v21(
    rec: Dict[str, Any],
    fund: Optional[Dict[str, Any]] = None,
    sector: Optional[Dict[str, Any]] = None,
    risk: Optional[Dict[str, Any]] = None,
    continuity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Backward-compatible entry; V2.2 gates when continuity present."""
    return classify_v22(rec, fund=fund, sector=sector, risk=risk, continuity=continuity)


def _pack(
    tag,
    tag_cn,
    why,
    notes,
    tech,
    fund_score_f,
    sec_score_f,
    risk_level,
    fund,
    sector,
    risk,
    rec,
    c_score_f=None,
    cons_f=None,
    ctype=None,
) -> Dict[str, Any]:
    tech_mark = "✓" if tech >= 75 else ("⚠" if tech >= 65 else "✗")
    if fund.get("data_insufficient"):
        fund_mark = "⚠"
    elif fund_score_f is None:
        fund_mark = "—"
    else:
        fund_mark = "✓" if fund_score_f >= 60 else "⚠"
    risk_mark = "✓" if risk_level == "LOW" else "⚠"
    if sector.get("data_insufficient"):
        sec_mark = "⚠"
    elif sec_score_f is None:
        sec_mark = "—"
    else:
        sec_mark = "✓" if sec_score_f >= 60 else "⚠"
    if c_score_f is None:
        cont_mark = "—"
    else:
        cont_mark = "✓" if c_score_f >= 75 else ("⚠" if c_score_f >= 60 else "✗")
    if cons_f is None:
        cons_mark = "—"
    else:
        cons_mark = "✓" if cons_f < 70 else "⚠"

    # alias for consumers still checking CORE_CANDIDATE
    tag_out = tag
    aliases = []
    if tag == "CORE_TREND":
        aliases.append("CORE_CANDIDATE")

    return {
        "tag": tag_out,
        "tag_cn": tag_cn,
        "tag_aliases": aliases,
        "why": why,
        "notes": notes,
        "tech_score": round(tech, 1),
        "fund_score": fund_score_f,
        "sector_score": sec_score_f,
        "risk_score": risk_level,
        "continuity_score": c_score_f,
        "consumption_score": cons_f,
        "continuity_type": ctype,
        "checklist": {
            "技术": tech_mark,
            "基本面": fund_mark,
            "交易风险": risk_mark,
            "板块": sec_mark,
            "持续性": cont_mark,
            "消耗度": cons_mark,
        },
    }


__all__ = ["classify_v21", "classify_v22", "CORE_PHASES"]
