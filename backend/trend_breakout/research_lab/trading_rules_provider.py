"""交易规则提供者 - 按交易所/板块/历史状态生效区间解析

基于已核验的官方历史规则：
- 涨跌停限制：主板10%，科创板20%，北交所30%，ST 5%
- 印花税：2008-04-24后千一单向卖出，之前千三双向
- 佣金：历史变化（万八→万三→万二.五），机构券商不同

⚠️ 2026规则需用户核验
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class Market(Enum):
    """市场/板块"""
    SH_MAIN = "SH_MAIN"  # 上海主板
    SZ_MAIN = "SZ_MAIN"  # 深圳主板
    SH_STAR = "SH_STAR"  # 科创板 (688)
    SZ_GEM = "SZ_GEM"    # 创业板 (300)
    BJ_MAIN = "BJ_MAIN"  # 北交所 (43/83/87)
    UNKNOWN = "UNKNOWN"


class SecurityType(Enum):
    """证券类型"""
    NORMAL = "NORMAL"  # 正常
    ST = "ST"          # ST/\*ST
    DELISTED = "DELISTED"  # 退市
    UNKNOWN = "UNKNOWN"


def infer_market_from_code(code: str) -> Market:
    """从股票代码推断市场/板块
    
    规则：
    - 688xxx: 科创板
    - 300xxx: 创业板  
    - 43xxxx, 83xxxx, 87xxxx: 北交所
    - 600xxx, 601xxx, 603xxx, 605xxx: 上海主板
    - 000xxx, 001xxx, 002xxx: 深圳主板
    """
    code6 = str(code).zfill(6)
    
    if code6.startswith('688'):
        return Market.SH_STAR
    elif code6.startswith('300'):
        return Market.SZ_GEM
    elif code6.startswith(('43', '83', '87')):
        return Market.BJ_MAIN
    elif code6.startswith(('600', '601', '603', '605', '900')):
        return Market.SH_MAIN
    elif code6.startswith(('000', '001', '002', '200')):
        return Market.SZ_MAIN
    else:
        return Market.UNKNOWN


@dataclass
class TradingRule:
    """单条交易规则"""
    
    # 生效区间
    start_date: str  # YYYY-MM-DD，含
    end_date: Optional[str] = None  # YYYY-MM-DD，含；None表示至今
    
    # 适用范围
    market: Optional[Market] = None  # None表示所有市场
    security_type: Optional[SecurityType] = None  # None表示所有类型
    
    # 涨跌停
    limit_up_pct: float = 10.0
    limit_down_pct: float = 10.0
    
    # 交易单位
    min_trade_unit: int = 100  # 股
    allow_odd_lot_sell: bool = True  # 允许零股卖出
    
    # T+N
    settlement_days: int = 1
    
    # 印花税（仅卖出）
    stamp_tax_rate: float = 0.001  # 千一
    stamp_tax_both_sides: bool = False  # 双向征收（历史规则）
    
    # 佣金（双向）
    commission_rate: float = 0.0003  # 万三
    commission_min: float = 5.0  # 最低5元
    
    # 来源与说明
    source: str = "unknown"  # 规则来源
    notes: str = ""  # 说明
    
    def is_effective(self, trade_date: str) -> bool:
        """判断规则在指定日期是否生效"""
        if trade_date < self.start_date:
            return False
        if self.end_date and trade_date > self.end_date:
            return False
        return True
    
    def matches(self, market: Market, security_type: SecurityType) -> bool:
        """判断规则是否适用于指定市场和证券类型"""
        if self.market and self.market != market:
            return False
        if self.security_type and self.security_type != security_type:
            return False
        return True


# ============================================================================
# 已核验的官方历史规则（有来源/生效日期）
# ============================================================================

VERIFIED_RULES = [
    # 印花税历史规则
    TradingRule(
        start_date="2008-04-24",
        end_date=None,  # 至今
        stamp_tax_rate=0.001,  # 千一
        stamp_tax_both_sides=False,  # 仅卖出
        source="财政部、国家税务总局2008年4月23日通知",
        notes="2008-04-24起印花税单边征收（仅卖出千一）"
    ),
    
    TradingRule(
        start_date="1990-01-01",
        end_date="2008-04-23",
        stamp_tax_rate=0.003,  # 千三
        stamp_tax_both_sides=True,  # 双向
        source="历史印花税规则",
        notes="2008-04-24前印花税双边征收（买卖千三）"
    ),
    
    # 主板涨跌停 10%
    TradingRule(
        start_date="1996-12-16",
        end_date=None,
        market=Market.SH_MAIN,
        security_type=SecurityType.NORMAL,
        limit_up_pct=10.0,
        limit_down_pct=10.0,
        source="沪深交易所涨跌幅限制规则",
        notes="主板正常股票涨跌停10%"
    ),
    
    TradingRule(
        start_date="1996-12-16",
        end_date=None,
        market=Market.SZ_MAIN,
        security_type=SecurityType.NORMAL,
        limit_up_pct=10.0,
        limit_down_pct=10.0,
        source="沪深交易所涨跌幅限制规则",
        notes="主板正常股票涨跌停10%"
    ),
    
    # ST 涨跌停 5%（所有市场，优先级高）
    TradingRule(
        start_date="1998-04-22",
        end_date=None,
        market=None,  # 所有市场
        security_type=SecurityType.ST,
        limit_up_pct=5.0,
        limit_down_pct=5.0,
        source="沪深交易所ST涨跌幅限制",
        notes="ST股票涨跌停5%（覆盖市场规则）"
    ),
    
    # 科创板 20%（2019-07-22上市）
    TradingRule(
        start_date="2019-07-22",
        end_date=None,
        market=Market.SH_STAR,
        security_type=SecurityType.NORMAL,
        limit_up_pct=20.0,
        limit_down_pct=20.0,
        source="上交所科创板交易规则",
        notes="科创板涨跌停20%"
    ),
    
    # 创业板改革后 20%（2020-08-24）
    TradingRule(
        start_date="2020-08-24",
        end_date=None,
        market=Market.SZ_GEM,
        security_type=SecurityType.NORMAL,
        limit_up_pct=20.0,
        limit_down_pct=20.0,
        source="深交所创业板改革交易规则",
        notes="创业板改革后涨跌停20%"
    ),
    
    # 创业板改革前 10%
    TradingRule(
        start_date="2009-10-30",
        end_date="2020-08-23",
        market=Market.SZ_GEM,
        security_type=SecurityType.NORMAL,
        limit_up_pct=10.0,
        limit_down_pct=10.0,
        source="深交所创业板交易规则",
        notes="创业板改革前涨跌停10%"
    ),
    
    # 北交所 30%（2021-11-15）
    TradingRule(
        start_date="2021-11-15",
        end_date=None,
        market=Market.BJ_MAIN,
        security_type=SecurityType.NORMAL,
        limit_up_pct=30.0,
        limit_down_pct=30.0,
        source="北京证券交易所交易规则",
        notes="北交所涨跌停30%"
    ),
    
    # 佣金（万三基准，实际券商不同）
    TradingRule(
        start_date="2000-01-01",
        end_date=None,
        commission_rate=0.0003,  # 万三
        commission_min=5.0,
        source="券商佣金基准（实际可能更低）",
        notes="万三佣金+最低5元（实际券商可能万二.五或万一）"
    ),
    
    # T+1（1995-01-01起）
    TradingRule(
        start_date="1995-01-01",
        end_date=None,
        settlement_days=1,
        source="沪深交易所T+1交易制度",
        notes="A股T+1交易（当日买入次日可卖）"
    ),
]


class RulesProvider:
    """交易规则提供者
    
    根据日期、市场、证券类型返回适用的交易规则
    """
    
    def __init__(self, rules: Optional[list[TradingRule]] = None):
        self.rules = rules or VERIFIED_RULES
        
        # 按生效日期排序（新规则优先）
        self.rules.sort(key=lambda r: r.start_date, reverse=True)
    
    def get_rule(self,
                 trade_date: str,
                 code: Optional[str] = None,
                 market: Optional[Market] = None,
                 security_type: Optional[SecurityType] = None) -> TradingRule:
        """获取适用的交易规则
        
        Args:
            trade_date: 交易日期 YYYY-MM-DD
            code: 股票代码（可选，用于推断市场）
            market: 市场/板块（可选）
            security_type: 证券类型（可选）
            
        Returns:
            适用的交易规则（合并多条规则）
        """
        # 推断市场
        if market is None and code:
            market = infer_market_from_code(code)
        
        # 默认值
        if market is None:
            market = Market.UNKNOWN
        if security_type is None:
            security_type = SecurityType.NORMAL
        
        # 查找所有适用的规则（优先级：具体→通用）
        # 按优先级排序：security_type具体 > market具体 > 通用
        applicable_rules = []
        
        for rule in self.rules:
            if not rule.is_effective(trade_date):
                continue
            if not rule.matches(market, security_type):
                continue
            
            # 计算具体性得分（越高越具体）
            specificity = 0
            if rule.security_type is not None:
                specificity += 2
            if rule.market is not None:
                specificity += 1
            
            applicable_rules.append((specificity, rule))
        
        if not applicable_rules:
            # 无适用规则 → Fail-closed
            raise ValueError(
                f"未找到适用规则: date={trade_date}, market={market.value}, "
                f"type={security_type.value}。正式模式阻断，或配置诊断模式假设规则"
            )
        
        # 按具体性降序排序（具体规则优先）
        applicable_rules.sort(key=lambda x: x[0], reverse=True)
        applicable_rules = [rule for _, rule in applicable_rules]
        # 合并规则（从通用到具体覆盖）
        merged = TradingRule(
            start_date=trade_date,
            market=market,
            security_type=security_type,
        )
        
        # 按优先级覆盖字段
        for rule in reversed(applicable_rules):  # 从通用到具体
            for field in ['limit_up_pct', 'limit_down_pct', 'min_trade_unit', 
                         'allow_odd_lot_sell', 'settlement_days', 
                         'stamp_tax_rate', 'stamp_tax_both_sides',
                         'commission_rate', 'commission_min']:
                if hasattr(rule, field):
                    setattr(merged, field, getattr(rule, field))
            
            # 合并来源
            if merged.source == "unknown":
                merged.source = rule.source
            else:
                merged.source += f"; {rule.source}"
        
        return merged
    
    def get_limit_pct(self, trade_date: str, code: str, 
                     security_type: SecurityType = SecurityType.NORMAL) -> tuple[float, float]:
        """获取涨跌停限制（百分比）
        
        Returns:
            (limit_up_pct, limit_down_pct)
        """
        rule = self.get_rule(trade_date, code=code, security_type=security_type)
        return (rule.limit_up_pct, rule.limit_down_pct)
    
    def get_fees(self, trade_date: str, code: str, 
                 trade_value: float, is_sell: bool) -> Dict[str, float]:
        """计算交易费用
        
        Args:
            trade_date: 交易日期
            code: 股票代码
            trade_value: 交易金额
            is_sell: 是否卖出
            
        Returns:
            {"commission": 佣金, "stamp_tax": 印花税, "total": 总费用}
        """
        rule = self.get_rule(trade_date, code=code)
        
        # 佣金（双向）
        commission = max(trade_value * rule.commission_rate, rule.commission_min)
        
        # 印花税（卖出或双向）
        if is_sell or rule.stamp_tax_both_sides:
            stamp_tax = trade_value * rule.stamp_tax_rate
        else:
            stamp_tax = 0.0
        
        return {
            "commission": commission,
            "stamp_tax": stamp_tax,
            "total": commission + stamp_tax,
            "rule_source": rule.source,
        }
    
    def validate_trade_unit(self, quantity: int, is_sell: bool, 
                           position_quantity: int = 0) -> bool:
        """验证交易数量是否符合规则
        
        Args:
            quantity: 交易数量
            is_sell: 是否卖出
            position_quantity: 当前持仓（卖出时检查零股）
            
        Returns:
            True if valid
        """
        # 买入必须100股整数倍
        if not is_sell:
            return quantity % 100 == 0
        
        # 卖出：如果是清仓，允许零股
        if quantity == position_quantity:
            return True
        
        # 卖出：非清仓，必须100股整数倍
        return quantity % 100 == 0


if __name__ == "__main__":
    provider = RulesProvider()
    
    # 测试不同市场
    test_cases = [
        ("2024-01-01", "600519", SecurityType.NORMAL, "主板正常"),
        ("2024-01-01", "688001", SecurityType.NORMAL, "科创板"),
        ("2024-01-01", "300750", SecurityType.NORMAL, "创业板"),
        ("2024-01-01", "600519", SecurityType.ST, "主板ST"),
        ("2007-01-01", "600519", SecurityType.NORMAL, "印花税双向"),
        ("2010-01-01", "300001", SecurityType.NORMAL, "创业板改革前"),
    ]
    
    print("交易规则测试:")
    print("=" * 80)
    
    for date, code, sec_type, desc in test_cases:
        print(f"\n{desc} ({code} @ {date}):")
        try:
            rule = provider.get_rule(date, code=code, security_type=sec_type)
            print(f"  涨跌停: ±{rule.limit_up_pct}%")
            print(f"  印花税: {rule.stamp_tax_rate*10000:.1f}‱, 双向={rule.stamp_tax_both_sides}")
            print(f"  佣金: {rule.commission_rate*10000:.1f}‱, 最低{rule.commission_min}元")
            print(f"  T+{rule.settlement_days}")
            print(f"  来源: {rule.source}")
        except ValueError as e:
            print(f"  ❌ {e}")
    
    # 测试费用计算
    print("\n\n费用计算测试:")
    print("=" * 80)
    
    fees_2024 = provider.get_fees("2024-01-01", "600519", 10000, is_sell=True)
    print(f"\n2024年卖出1万元:")
    print(f"  佣金: {fees_2024['commission']:.2f}元")
    print(f"  印花税: {fees_2024['stamp_tax']:.2f}元")
    print(f"  总费用: {fees_2024['total']:.2f}元")
    
    fees_2007 = provider.get_fees("2007-01-01", "600519", 10000, is_sell=True)
    print(f"\n2007年卖出1万元（双向印花税）:")
    print(f"  佣金: {fees_2007['commission']:.2f}元")
    print(f"  印花税: {fees_2007['stamp_tax']:.2f}元")
    print(f"  总费用: {fees_2007['total']:.2f}元")
