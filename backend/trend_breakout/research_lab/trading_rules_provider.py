"""费用与交易约束。

只把已经核对到原文的印花税区间写成可引用规则。
2008-04-24 是税率从 3‰ 降到 1‰，当时仍是双边；单边征收从 2008-09-19 开始。
本模块不把 2008-04-24 记成单边千一。

涨跌幅、最小申报单位、佣金、滑点没有被收成一条可以覆盖全部历史板块的官方法规，
正式模式一律拒绝未知项。诊断模式只有在配置里写明 assumptions 时才使用这些数字，
并且结果不能叫做真实历史成交。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class RuleError(ValueError):
    """正式模式遇到无法核验的规则，或诊断模式没有显式假设。"""


@dataclass(frozen=True)
class StampRule:
    start: str
    end: Optional[str]
    rate: float
    seller_only: bool
    source: str

    def covers(self, trade_date: str) -> bool:
        if trade_date < self.start:
            return False
        if self.end is not None and trade_date > self.end:
            return False
        return True


# 2008-09-19 之前的多次税率调整没有在本次运行中逐条核对原文，正式模式拒绝。
VERIFIED_STAMP_RULES = (
    StampRule(
        start="2008-09-19",
        end="2023-08-27",
        rate=0.001,
        seller_only=True,
        source=(
            "财政部、国家税务总局2008-09-19决定：自2008-09-19起，A股、B股股权转让书据"
            "由买卖双方按1‰征收改为仅对出让方按1‰征收。"
            "https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/200809/t20080919_76432.htm"
        ),
    ),
    StampRule(
        start="2023-08-28",
        end=None,
        rate=0.0005,
        seller_only=True,
        source=(
            "财政部 税务总局公告2023年第39号：自2023-08-28起证券交易印花税减半征收。"
            "减半前的已核验规则是出让方1‰，因此按出让方0.5‰执行。"
            "截至2026-11-08未检索到后续调整公告；这不是交易所2026年交易规则。"
            "https://www.gov.cn/zhengce/zhengceku/202308/content_6900443.htm"
        ),
    ),
)


def stamp_rule(trade_date: str) -> StampRule:
    matches = [rule for rule in VERIFIED_STAMP_RULES if rule.covers(trade_date)]
    if len(matches) != 1:
        raise RuleError(
            f"印花税在 {trade_date} 没有唯一的已核验区间。"
            "2008-04-24 只是1‰双边降税，不是单边征收；该日及更早区间这里拒绝。"
        )
    return matches[0]


def stamp_tax(trade_date: str, amount: float, is_sell: bool, mode: str, assumptions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    override = (assumptions or {}).get("stamp_tax_override")
    if override:
        if mode != "diagnostic":
            raise RuleError("正式模式不能使用 stamp_tax_override")
        rate = float(override["rate"])
        seller_only = bool(override["seller_only"])
        source = "diagnostic_assumption:stamp_tax_override"
    else:
        rule = stamp_rule(trade_date)
        rate = rule.rate
        seller_only = rule.seller_only
        source = rule.source
    charge = 0.0 if seller_only and not is_sell else amount * rate
    return {"stamp_tax": charge, "rate": rate, "seller_only": seller_only, "source": source}


def commission(amount: float, mode: str, assumptions: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """佣金由券商决定，没有单一官方历史费率。正式模式拒绝。"""
    if mode != "diagnostic":
        raise RuleError("佣金没有已核验的单一官方历史费率，正式模式拒绝成交")
    if not assumptions or "commission_rate" not in assumptions or "commission_min" not in assumptions:
        raise RuleError("诊断模式使用佣金前必须显式配置 commission_rate 和 commission_min")
    rate = float(assumptions["commission_rate"])
    minimum = float(assumptions["commission_min"])
    return {
        "commission": max(amount * rate, minimum),
        "rate": rate,
        "min": minimum,
        "source": "diagnostic_assumption:commission",
    }


def limit_pct(board: str, st_status: str, days_listed: Optional[int], mode: str, assumptions: Optional[Dict[str, Any]]) -> float:
    if st_status != "NORMAL":
        raise RuleError(f"ST/退市/未知状态 {st_status} 的历史涨跌幅未核验，拒绝")
    if days_listed is None:
        raise RuleError("缺少上市交易日数，无法判断上市初期不设涨跌幅的区间，拒绝")
    if int(days_listed) <= 5:
        raise RuleError("上市后前5个交易日的涨跌幅限制依赖板块和日期，当前未逐条核验，拒绝")
    if mode != "diagnostic":
        raise RuleError(
            "正式模式不把单一涨跌幅写成全历史规则。"
            "上交所交易规则（2026年修订）自2026-07-06施行，同时仍有暂缓实施条文，"
            "不能倒推成全部历史成交约束。"
        )
    table = (assumptions or {}).get("price_limit_pct_by_board") or {}
    if board not in table:
        raise RuleError(f"诊断模式没有板块 {board} 的显式涨跌幅假设，拒绝")
    return float(table[board])


def min_buy_lot(board: str, mode: str, assumptions: Optional[Dict[str, Any]]) -> int:
    if mode != "diagnostic":
        raise RuleError("正式模式不假定所有板块的最小买入单位都是100股")
    table = (assumptions or {}).get("min_buy_lot_by_board") or {}
    if board not in table:
        raise RuleError(f"诊断模式没有板块 {board} 的显式最小买入单位，拒绝")
    lot = int(table[board])
    if lot <= 0:
        raise RuleError("最小买入单位必须为正")
    return lot


def execution_assumptions_ok(mode: str, assumptions: Optional[Dict[str, Any]]) -> None:
    if mode == "formal":
        raise RuleError("正式模式在佣金、板块最小交易单位和涨跌幅特殊期未核验完整前不能成交")
    if mode != "diagnostic":
        raise RuleError(f"未知模式 {mode}")
    required = ("commission_rate", "commission_min", "slippage_bps", "price_limit_pct_by_board", "min_buy_lot_by_board")
    missing = [key for key in required if not assumptions or key not in assumptions]
    if missing:
        raise RuleError("诊断模式缺少显式假设: " + ",".join(missing))
