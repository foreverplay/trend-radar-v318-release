"""
Per-stock detail block — Phase 13 / Plan §27.

Rules-only text (no LLM). Sections:
  structure/phase, continuity deltas, risk, protection/break prices,
  ExitSignal, lifecycle nodes (optional simple marker list).

禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Union

from v3.types import (
    ExitSignal,
    Grade,
    Lifecycle,
    MarketRegime,
    RiskLevel,
    TrendBotResult,
)

# Staircase order for marker list (Plan lifecycle path; specials appended)
_LIFECYCLE_NODES: tuple = (
    Lifecycle.ACCUMULATION,
    Lifecycle.PLATFORM,
    Lifecycle.FIRST_BREAKOUT,
    Lifecycle.HEALTHY_PULLBACK,
    Lifecycle.PLATFORM_HOLD,
    Lifecycle.SECOND_BREAKOUT,
    Lifecycle.MAIN_RISE,
    Lifecycle.ACCELERATION,
)

_LIFECYCLE_CN = {
    Lifecycle.ACCUMULATION: "蓄势",
    Lifecycle.PLATFORM: "平台",
    Lifecycle.FIRST_BREAKOUT: "首破",
    Lifecycle.HEALTHY_PULLBACK: "分歧",
    Lifecycle.PLATFORM_HOLD: "平台不破",
    Lifecycle.SECOND_BREAKOUT: "二破",
    Lifecycle.MAIN_RISE: "主升",
    Lifecycle.ACCELERATION: "加速",
    Lifecycle.OVERHEATED: "过热",
    Lifecycle.TREND_FAILURE: "趋势破坏",
    Lifecycle.OBSERVE: "观察",
    Lifecycle.NONE: "无",
}


def _enum_val(x: Any, default: str = "") -> str:
    if x is None:
        return default
    if hasattr(x, "value"):
        return str(x.value)
    return str(x)


def _fmt(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if digits == 0:
        return str(int(round(v)))
    s = f"{v:.{digits}f}"
    if digits > 0:
        s = s.rstrip("0").rstrip(".")
    return s


def _lifecycle(r: TrendBotResult) -> Lifecycle:
    life = r.lifecycle
    if isinstance(life, Lifecycle):
        return life
    try:
        return Lifecycle(str(life or "NONE").strip().upper())
    except ValueError:
        return Lifecycle.map_from_v2_phase(str(life))


def lifecycle_node_markers(
    current: Union[Lifecycle, str, None],
    *,
    nodes: Optional[Sequence[Lifecycle]] = None,
) -> str:
    """
    Simple text marker list (no LLM).

    Current stage → ``[● 二破]``; others → ``[○ 蓄势]``.
    Specials (OVERHEATED / TREND_FAILURE / OBSERVE / NONE) shown as a tag
    when not on the main staircase.
    """
    path = tuple(nodes) if nodes is not None else _LIFECYCLE_NODES
    if isinstance(current, Lifecycle):
        cur = current
    else:
        try:
            cur = Lifecycle(str(current or "NONE").strip().upper())
        except ValueError:
            cur = Lifecycle.map_from_v2_phase(str(current))

    parts: List[str] = []
    on_path = False
    for node in path:
        cn = _LIFECYCLE_CN.get(node, node.value)
        if node == cur:
            parts.append(f"[● {cn}]")
            on_path = True
        else:
            parts.append(f"[○ {cn}]")
    line = " → ".join(parts)
    if not on_path:
        cn = _LIFECYCLE_CN.get(cur, cur.value if isinstance(cur, Lifecycle) else str(cur))
        line = f"{line}  |  当前特例: [{cn}/{cur.value if isinstance(cur, Lifecycle) else cur}]"
    return line


def format_stock_detail(result: TrendBotResult) -> str:
    """
    Plan §27 per-stock detail block (Markdown text).

    Sections: 结构/阶段 · 持续性Δ · 风险 · 保护/破坏价 · ExitSignal · 生命周期节点.
    """
    r = result
    code = r.code or ""
    name = (r.name or "").strip()
    title = f"{name}（{code}）" if name else code
    grade = _enum_val(r.grade, Grade.FILTER.value)
    life = _lifecycle(r)
    life_s = life.value
    regime = _enum_val(r.regime_v3, MarketRegime.NORMAL.value)
    legacy = r.legacy_regime or ""
    risk_off = bool(r.risk_off)

    snap = r.snapshot
    phase_v2 = ""
    platform_high = platform_low = ma20 = ma60 = None
    vol_ratio = dist = None
    kjs = None
    overheated = False
    cont_type = cont_type_cn = delta_label = None
    d1 = d3 = d5 = None
    risk_tags: List[str] = []
    industry = None
    if snap is not None:
        phase_v2 = snap.phase_v2 or ""
        platform_high = snap.platform_high
        platform_low = snap.platform_low
        ma20 = snap.ma20
        ma60 = snap.ma60
        vol_ratio = snap.vol_ratio
        dist = snap.dist_ma20_pct
        kjs = snap.kjs_tag
        overheated = bool(snap.overheated)
        risk_tags = list(snap.risk_tags or [])
        industry = snap.industry
        cont = snap.continuity
        if cont is not None:
            cont_type = cont.continuity_type
            cont_type_cn = cont.continuity_type_cn
            delta_label = cont.continuity_delta_label
            d1 = cont.continuity_delta1
            d3 = cont.continuity_delta3
            d5 = cont.continuity_delta5

    risk = _enum_val(r.risk_level, RiskLevel.WATCH.value)
    exit_sig = _enum_val(r.exit_signal, ExitSignal.HOLD.value)
    markers = lifecycle_node_markers(life)

    lines: List[str] = [
        f"### {title} — {grade} / {life_s}",
        "",
        f"> 观察明细（规则文本，非荐买）。regime_v3={regime} legacy={legacy} "
        f"risk_off={risk_off} data_status={_enum_val(r.data_status, 'VALID')}",
        "",
        "#### 结构 / 阶段",
        f"- lifecycle: `{life_s}`" + (f" （phase_v2=`{phase_v2}`）" if phase_v2 else ""),
        f"- tech_score: {_fmt(r.tech_score, 1)}",
        f"- close: {_fmt(r.close, 2)}  ma20: {_fmt(ma20, 2)}  ma60: {_fmt(ma60, 2)}",
        f"- platform_high/low: {_fmt(platform_high, 2)} / {_fmt(platform_low, 2)}",
        f"- vol_ratio: {_fmt(vol_ratio, 2)}  dist_ma20%: {_fmt(dist, 2)}",
        f"- industry: {industry or '—'}  kjs: {kjs or '—'}  overheated: {overheated}",
        "",
        "#### 持续性 / 消耗（deltas）",
        f"- continuity_score: {_fmt(r.continuity_score, 1)}  "
        f"type: {cont_type or '—'}{(' / ' + cont_type_cn) if cont_type_cn else ''}",
        f"- delta1: {_fmt(d1, 1)}  delta3: {_fmt(d3, 1)}  delta5: {_fmt(d5, 1)}  "
        f"label: {delta_label or '—'}",
        f"- consumption_score: {_fmt(r.consumption_score, 1)}",
        f"- fund_score: {_fmt(r.fund_score, 1)}  sector_score: {_fmt(r.sector_score, 1)}",
        "",
        "#### 风险",
        f"- risk_level: `{risk}`  broken: {bool(getattr(snap, 'broken', False) if snap else False)}",
        f"- risk_tags: {', '.join(risk_tags) if risk_tags else '—'}",
        f"- risks: {'; '.join(r.risks) if r.risks else '—'}",
        "",
        "#### 保护 / 破坏价位",
        f"- close: {_fmt(r.close, 2)}",
        f"- warning_price: {_fmt(r.warning_price, 2)}",
        f"- protection_price: {_fmt(r.protection_price, 2)}",
        f"- break_price: {_fmt(r.break_price, 2)}",
        "",
        "#### ExitSignal",
        f"- `{exit_sig}`  （观察退出状态；SELL ≠ 预测必跌，禁止荐买）",
        "",
        "#### 生命周期节点（规则标记，非 LLM）",
        f"- {markers}",
        "",
    ]
    if r.reasons:
        lines.append("#### 等级理由（摘要）")
        for reason in list(r.reasons)[:12]:
            lines.append(f"- {reason}")
        lines.append("")
    if r.notes:
        lines.append("#### 备注")
        for note in list(r.notes)[:12]:
            lines.append(f"- {note}")
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "format_stock_detail",
    "lifecycle_node_markers",
]
