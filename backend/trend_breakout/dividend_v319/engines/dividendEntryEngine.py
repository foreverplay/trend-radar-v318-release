"""
DividendEntryEngine — V3.1.9 Phase11.

EntryStatus is independent of DividendGrade.
操作提示禁用：强烈买入 / 必涨 / 稳赚 等用语。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from dividend_v319.models.dividendModels import DividendEntryStatus

FORBIDDEN_TOKENS = ("强烈买入", "必涨", "稳赚", "稳赚不赔", "保证收益", "荐买")


@dataclass
class EntryResult:
    symbol: str
    as_of: str
    dividend_entry_status: str
    action_hint: str
    reasons: List[str] = field(default_factory=list)
    why_watch: List[str] = field(default_factory=list)
    why_not_now: List[str] = field(default_factory=list)
    wait_for: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _safe_text(s: str) -> str:
    out = s
    for tok in FORBIDDEN_TOKENS:
        out = out.replace(tok, "关注")
    return out


class DividendEntryEngine:
    def score(
        self,
        code: str,
        as_of: str,
        *,
        grade: str,
        opportunity_score: Optional[float],
        quality_score: Optional[float],
        sustainability_score: Optional[float],
        trend_score: Optional[float],
        valuation_score: Optional[float],
        risk_level: str,
        risk_score: Optional[float],
        dividend_yield_trap: bool = False,
        pe_percentile: Optional[float] = None,
        pb_percentile: Optional[float] = None,
        close: Optional[float] = None,
        ma60: Optional[float] = None,
        ma250: Optional[float] = None,
        price_return_1y_qfq: Optional[float] = None,
    ) -> EntryResult:
        reasons: List[str] = []
        why_watch: List[str] = []
        why_not: List[str] = []
        wait_for: List[str] = []

        risk_level = (risk_level or "WATCH").upper()
        grade = grade or "FILTER"

        # hard avoid
        if risk_level == "HIGH" or dividend_yield_trap or grade == "FILTER":
            status = DividendEntryStatus.AVOID.value
            why_not.append("风险过高或未过质量/可持续过滤")
            if dividend_yield_trap:
                why_not.append("存在高股息陷阱标记")
            wait_for.append("分红可持续性与现金流改善，且陷阱标记解除")
            hint = "暂不适合作为红利策略关注对象；先观察分红与现金流是否修复"
            return EntryResult(
                symbol=code,
                as_of=as_of,
                dividend_entry_status=status,
                action_hint=_safe_text(hint),
                reasons=reasons + why_not,
                why_watch=why_watch,
                why_not_now=why_not,
                wait_for=wait_for,
            )

        # trend broken
        if (
            (trend_score is not None and trend_score < 35)
            or (
                close is not None
                and ma250 is not None
                and close < ma250 * 0.92
                and (price_return_1y_qfq is not None and price_return_1y_qfq < -0.2)
            )
        ):
            status = DividendEntryStatus.TREND_BROKEN.value
            why_not.append("长期趋势结构破坏或趋势分过低")
            wait_for.append("价格重新站回长期均线并出现稳定横盘/缓升")
            hint = "长期趋势偏弱，红利策略下优先等待结构修复，而非追高股息率"
            return EntryResult(
                symbol=code,
                as_of=as_of,
                dividend_entry_status=status,
                action_hint=_safe_text(hint),
                reasons=reasons + why_not,
                why_watch=why_watch,
                why_not_now=why_not,
                wait_for=wait_for,
            )

        cheap = (
            (pe_percentile is not None and pe_percentile <= 35)
            or (pb_percentile is not None and pb_percentile <= 35)
            or (valuation_score is not None and valuation_score >= 70)
        )
        expensive = (
            (pe_percentile is not None and pe_percentile >= 75)
            or (pb_percentile is not None and pb_percentile >= 75)
            or (valuation_score is not None and valuation_score <= 40)
        )
        trend_ok = trend_score is not None and trend_score >= 60
        trend_soft = trend_score is not None and 45 <= trend_score < 60
        quals_ok = (
            (quality_score or 0) >= 70
            and (sustainability_score or 0) >= 65
            and grade in ("A+", "A", "B")
        )

        above_ma60 = close is not None and ma60 is not None and close >= ma60
        extended = (
            close is not None
            and ma60 is not None
            and close > ma60 * 1.15
            and expensive
        )

        if extended:
            status = DividendEntryStatus.EXTENDED.value
            why_watch.append("质量尚可但价格相对均线与自身估值偏延伸")
            why_not.append("当前位置追价性价比一般")
            wait_for.append("回撤至均线附近或估值分位回落后再评估")
            hint = "属于优质观察池中的价格偏高状态，适合等待回撤而非追涨"
        elif quals_ok and cheap and trend_ok and risk_level in ("SAFE", "WATCH"):
            status = DividendEntryStatus.TREND_CONFIRM.value
            why_watch.append("分红质量/可持续与长期趋势同时达标")
            why_watch.append("估值分位或估值分相对合理")
            hint = "红利趋势条件相对完整，可纳入跟踪观察名单（非交易指令）"
            reasons.append("质量+可持续+趋势+估值共振")
        elif quals_ok and cheap and (trend_soft or trend_ok):
            status = DividendEntryStatus.ACCUMULATE_WATCH.value
            why_watch.append("基本面红利画像较好且估值不贵")
            if not trend_ok:
                why_not.append("趋势强度一般，更适合耐心跟踪")
                wait_for.append("中期均线结构进一步走稳")
            hint = "可作为红利策略的分批观察对象，等待趋势与价格位置共振"
        elif quals_ok and expensive:
            status = DividendEntryStatus.PULLBACK_WATCH.value
            why_watch.append("长期分红画像仍在优质区")
            why_not.append("当前估值偏贵或价格偏高")
            wait_for.append("估值分位回落或出现缩量回踩")
            hint = "优质红利但当前位置偏贵，优先放入回撤观察"
        elif quals_ok and trend_ok and grade in ("A+", "A") and risk_level == "SAFE":
            status = DividendEntryStatus.ACCUMULATE_WATCH.value
            why_watch.append("高等级红利画像且趋势尚可")
            why_not.append("估值仅中性，未到更优买点描述")
            wait_for.append("估值分进一步抬升或出现缩量回踩")
            hint = "可作为红利策略跟踪观察，等待更舒适的价格位置"
        elif quals_ok:
            status = DividendEntryStatus.HOLD_WATCH.value
            why_watch.append("仍属可跟踪的红利标的")
            why_not.append("估值或趋势尚未形成清晰介入条件")
            wait_for.append("估值回落到自身历史偏低区，或趋势分回到60以上")
            hint = "维持观察；暂未满足更明确的介入条件描述"
        elif risk_level == "WARNING":
            status = DividendEntryStatus.AVOID.value
            why_not.append("风险级别WARNING")
            wait_for.append("风险分下降且可持续性修复")
            hint = "风险偏高，红利策略下先回避"
        else:
            status = DividendEntryStatus.HOLD_WATCH.value
            why_not.append("综合条件一般")
            wait_for.append("质量/可持续/趋势任一维度改善")
            hint = "暂列观察，等待更多条件改善"

        if opportunity_score is not None:
            reasons.append(f"机会分={opportunity_score:.1f}")

        return EntryResult(
            symbol=code,
            as_of=as_of,
            dividend_entry_status=status,
            action_hint=_safe_text(hint),
            reasons=[_safe_text(r) for r in reasons],
            why_watch=[_safe_text(r) for r in why_watch],
            why_not_now=[_safe_text(r) for r in why_not],
            wait_for=[_safe_text(r) for r in wait_for],
        )


def run(*args, **kwargs):
    return DividendEntryEngine().score(*args, **kwargs)
