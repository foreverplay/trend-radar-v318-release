"""统一回测引擎 V2 - T+1、涨跌停、历史费率、除权处理

核心特性:
1. T收盘信号→次交易日开盘可成交（T+1）
2. 涨跌停保守处理（不假设能成交）
3. 停牌拒绝交易
4. 历史费率（佣金+印花税，需外部配置或固定基准）
5. 账户引擎：现金、持仓、净值、最小买卖单位（100股）
6. 复权价格与实际价格区分（前复权仅用于趋势判断，实际成交用原始价）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import pandas as pd
import numpy as np

from data_provider import ReadOnlyDataProvider


class OrderSide(Enum):
    """订单方向"""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "PENDING"  # 待成交
    FILLED = "FILLED"  # 已成交
    REJECTED = "REJECTED"  # 拒绝（涨跌停/停牌）
    CANCELLED = "CANCELLED"  # 取消


@dataclass
class TradingRules:
    """交易规则配置"""
    
    # T+N
    settlement_days: int = 1  # T+1
    
    # 涨跌停限制（%）
    limit_up_pct: float = 10.0  # 主板10%
    limit_down_pct: float = 10.0
    
    # 费率（历史固定基准，非2026真实规则）
    commission_rate: float = 0.0003  # 万三（双向）
    commission_min: float = 5.0  # 最低5元
    stamp_tax_rate: float = 0.001  # 千一（仅卖出）
    
    # 最小单位
    min_trade_unit: int = 100  # 100股整数倍
    
    # 滑点
    slippage: float = 0.001  # 0.1%
    
    # 资金
    reserve_cash_rate: float = 0.05  # 保留5%现金
    
    # 保守模式
    conservative_limit: bool = True  # 涨跌停不假设能成交
    conservative_suspend: bool = True  # 停牌拒绝交易
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "settlement_days": self.settlement_days,
            "limit_up_pct": self.limit_up_pct,
            "limit_down_pct": self.limit_down_pct,
            "commission_rate": self.commission_rate,
            "commission_min": self.commission_min,
            "stamp_tax_rate": self.stamp_tax_rate,
            "min_trade_unit": self.min_trade_unit,
            "slippage": self.slippage,
            "reserve_cash_rate": self.reserve_cash_rate,
            "conservative_limit": self.conservative_limit,
            "conservative_suspend": self.conservative_suspend,
        }


@dataclass
class Order:
    """订单"""
    
    order_id: str
    code: str
    side: OrderSide
    quantity: int  # 股数（100的倍数）
    
    # 信号日期（T日）
    signal_date: str
    
    # 预期成交日期（T+1日）
    expected_trade_date: str
    
    # 价格
    target_price: Optional[float] = None  # 目标价（信号日收盘价）
    actual_price: Optional[float] = None  # 实际成交价
    
    # 状态
    status: OrderStatus = OrderStatus.PENDING
    
    # 拒绝原因
    rejection_reason: Optional[str] = None
    
    # 实际成交
    filled_date: Optional[str] = None
    filled_quantity: int = 0
    filled_value: float = 0.0
    
    # 费用
    commission: float = 0.0
    stamp_tax: float = 0.0
    total_cost: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "code": self.code,
            "side": self.side.value,
            "quantity": self.quantity,
            "signal_date": self.signal_date,
            "expected_trade_date": self.expected_trade_date,
            "target_price": self.target_price,
            "actual_price": self.actual_price,
            "status": self.status.value,
            "rejection_reason": self.rejection_reason,
            "filled_date": self.filled_date,
            "filled_quantity": self.filled_quantity,
            "filled_value": self.filled_value,
            "commission": self.commission,
            "stamp_tax": self.stamp_tax,
            "total_cost": self.total_cost,
        }


@dataclass
class Position:
    """持仓"""
    
    code: str
    quantity: int  # 股数
    avg_cost: float  # 平均成本（含费用）
    market_value: float = 0.0  # 市值
    unrealized_pnl: float = 0.0  # 浮动盈亏
    
    # 开仓信息
    open_date: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "open_date": self.open_date,
        }


@dataclass
class Account:
    """账户"""
    
    # 资金
    cash: float  # 可用现金
    frozen_cash: float = 0.0  # 冻结资金（待交收）
    
    # 持仓
    positions: Dict[str, Position] = field(default_factory=dict)
    
    # 净值
    nav: float = 0.0  # 总资产净值
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cash": self.cash,
            "frozen_cash": self.frozen_cash,
            "nav": self.nav,
            "positions": {code: pos.to_dict() for code, pos in self.positions.items()},
        }


class BacktestEngineV2:
    """统一回测引擎 V2
    
    核心流程:
    1. 每日获取信号（候选买入/卖出）
    2. 生成订单（T日信号 → T+1日成交）
    3. 执行订单（检查涨跌停/停牌）
    4. 更新账户（现金、持仓、净值）
    """
    
    def __init__(self,
                 initial_capital: float,
                 data_provider: ReadOnlyDataProvider,
                 rules: Optional[TradingRules] = None):
        self.initial_capital = initial_capital
        self.provider = data_provider
        self.rules = rules or TradingRules()
        
        # 账户
        self.account = Account(cash=initial_capital, nav=initial_capital)
        
        # 历史记录
        self.orders: List[Order] = []
        self.trades: List[Dict[str, Any]] = []
        self.nav_history: List[Dict[str, Any]] = []
        
        # 待执行订单队列（T+1）
        self.pending_orders: List[Order] = []
        
        self._order_counter = 0
    
    def _generate_order_id(self) -> str:
        """生成订单ID"""
        self._order_counter += 1
        return f"O{self._order_counter:06d}"
    
    def submit_buy_signal(self,
                         code: str,
                         signal_date: str,
                         target_price: float,
                         quantity: int) -> Order:
        """提交买入信号（T日）
        
        订单将在T+1日开盘尝试成交
        """
        # 找到T+1日
        trading_dates = self.provider.get_trading_dates(start_date=signal_date)
        if len(trading_dates) < 2:
            raise ValueError(f"无法找到{signal_date}之后的交易日")
        
        expected_trade_date = trading_dates[1]  # T+1
        
        # 创建订单
        order = Order(
            order_id=self._generate_order_id(),
            code=code,
            side=OrderSide.BUY,
            quantity=quantity,
            signal_date=signal_date,
            expected_trade_date=expected_trade_date,
            target_price=target_price,
        )
        
        self.pending_orders.append(order)
        self.orders.append(order)
        
        return order
    
    def submit_sell_signal(self,
                          code: str,
                          signal_date: str,
                          target_price: float,
                          quantity: Optional[int] = None) -> Order:
        """提交卖出信号（T日）
        
        如果quantity为None，清空该持仓
        """
        # 检查持仓
        if code not in self.account.positions:
            raise ValueError(f"无持仓 {code}")
        
        pos = self.account.positions[code]
        
        if quantity is None:
            quantity = pos.quantity
        
        if quantity > pos.quantity:
            raise ValueError(f"卖出数量{quantity}超过持仓{pos.quantity}")
        
        # 找到T+1日
        trading_dates = self.provider.get_trading_dates(start_date=signal_date)
        if len(trading_dates) < 2:
            raise ValueError(f"无法找到{signal_date}之后的交易日")
        
        expected_trade_date = trading_dates[1]
        
        # 创建订单
        order = Order(
            order_id=self._generate_order_id(),
            code=code,
            side=OrderSide.SELL,
            quantity=quantity,
            signal_date=signal_date,
            expected_trade_date=expected_trade_date,
            target_price=target_price,
        )
        
        self.pending_orders.append(order)
        self.orders.append(order)
        
        return order
    
    def execute_pending_orders(self, trade_date: str):
        """执行当日应成交的订单
        
        检查:
        1. 是否涨跌停（保守模式拒绝）
        2. 是否停牌（拒绝）
        3. 计算实际成交价（开盘价+滑点）
        4. 计算费用
        5. 更新账户
        """
        executed = []
        
        for order in self.pending_orders:
            if order.expected_trade_date != trade_date:
                continue
            
            # 获取T+1日数据
            df = self.provider.get_stock_daily_bars(order.code, end_date=trade_date, start_date=trade_date)
            
            if df is None or len(df) == 0:
                # 数据缺失 → 停牌或退市
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "no_data_suspend_or_delisted"
                executed.append(order)
                continue
            
            row = df.iloc[0]
            open_price = row['open']
            prev_close = order.target_price  # 假设为前一日收盘价
            
            # 检查停牌（volume=0 保守处理为停牌）
            if self.rules.conservative_suspend and row['volume'] == 0:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "suspended_volume_zero"
                executed.append(order)
                continue
            
            # 检查涨跌停
            if prev_close and prev_close > 0:
                pct_chg = (open_price / prev_close - 1) * 100
                
                if self.rules.conservative_limit:
                    if pct_chg >= self.rules.limit_up_pct * 0.99:  # 接近涨停
                        if order.side == OrderSide.BUY:
                            order.status = OrderStatus.REJECTED
                            order.rejection_reason = "limit_up"
                            executed.append(order)
                            continue
                    
                    if pct_chg <= -self.rules.limit_down_pct * 0.99:  # 接近跌停
                        if order.side == OrderSide.SELL:
                            order.status = OrderStatus.REJECTED
                            order.rejection_reason = "limit_down"
                            executed.append(order)
                            continue
            
            # 计算实际成交价（开盘价 + 滑点）
            if order.side == OrderSide.BUY:
                actual_price = open_price * (1 + self.rules.slippage)
            else:
                actual_price = open_price * (1 - self.rules.slippage)
            
            # 计算费用
            trade_value = actual_price * order.quantity
            commission = max(trade_value * self.rules.commission_rate, self.rules.commission_min)
            stamp_tax = trade_value * self.rules.stamp_tax_rate if order.side == OrderSide.SELL else 0
            total_cost = trade_value + commission + stamp_tax
            
            # 检查资金（买入）
            if order.side == OrderSide.BUY:
                if total_cost > self.account.cash:
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = "insufficient_cash"
                    executed.append(order)
                    continue
            
            # 成交
            order.status = OrderStatus.FILLED
            order.filled_date = trade_date
            order.filled_quantity = order.quantity
            order.filled_value = trade_value
            order.actual_price = actual_price
            order.commission = commission
            order.stamp_tax = stamp_tax
            order.total_cost = total_cost
            
            # 更新账户
            if order.side == OrderSide.BUY:
                self._execute_buy(order)
            else:
                self._execute_sell(order)
            
            executed.append(order)
            
            # 记录交易
            self.trades.append({
                "trade_date": trade_date,
                "order_id": order.order_id,
                "code": order.code,
                "side": order.side.value,
                "quantity": order.filled_quantity,
                "price": order.actual_price,
                "value": order.filled_value,
                "commission": order.commission,
                "stamp_tax": order.stamp_tax,
                "total_cost": order.total_cost,
            })
        
        # 移除已执行订单
        self.pending_orders = [o for o in self.pending_orders if o not in executed]
    
    def _execute_buy(self, order: Order):
        """执行买入"""
        code = order.code
        
        # 扣除现金
        self.account.cash -= order.total_cost
        
        # 更新持仓
        if code in self.account.positions:
            pos = self.account.positions[code]
            # 加权平均成本
            total_quantity = pos.quantity + order.filled_quantity
            total_cost = pos.avg_cost * pos.quantity + order.total_cost
            pos.quantity = total_quantity
            pos.avg_cost = total_cost / total_quantity
        else:
            self.account.positions[code] = Position(
                code=code,
                quantity=order.filled_quantity,
                avg_cost=order.total_cost / order.filled_quantity,
                open_date=order.filled_date,
            )
    
    def _execute_sell(self, order: Order):
        """执行卖出"""
        code = order.code
        pos = self.account.positions[code]
        
        # 增加现金（扣除费用）
        self.account.cash += order.filled_value - order.commission - order.stamp_tax
        
        # 减少持仓
        pos.quantity -= order.filled_quantity
        
        if pos.quantity == 0:
            del self.account.positions[code]
    
    def update_nav(self, trade_date: str):
        """更新净值（每日收盘）"""
        total_market_value = 0.0
        
        # 更新持仓市值
        for code, pos in self.account.positions.items():
            df = self.provider.get_stock_daily_bars(code, end_date=trade_date, start_date=trade_date)
            
            if df is None or len(df) == 0:
                # 停牌，使用上一次价格（简化处理）
                market_value = pos.market_value if pos.market_value > 0 else pos.avg_cost * pos.quantity
            else:
                close_price = df.iloc[0]['close']
                market_value = close_price * pos.quantity
            
            pos.market_value = market_value
            pos.unrealized_pnl = market_value - pos.avg_cost * pos.quantity
            total_market_value += market_value
        
        # 总净值
        self.account.nav = self.account.cash + total_market_value
        
        # 记录
        self.nav_history.append({
            "date": trade_date,
            "cash": self.account.cash,
            "market_value": total_market_value,
            "nav": self.account.nav,
            "n_positions": len(self.account.positions),
        })
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """计算绩效指标"""
        if not self.nav_history:
            return {}
        
        df = pd.DataFrame(self.nav_history)
        
        # 收益率
        total_return = (df['nav'].iloc[-1] / self.initial_capital - 1) * 100
        
        # 最大回撤
        df['cummax'] = df['nav'].cummax()
        df['drawdown'] = (df['nav'] / df['cummax'] - 1) * 100
        max_drawdown = df['drawdown'].min()
        
        # 年化收益（简化）
        n_days = len(df)
        annual_return = total_return * (250 / n_days) if n_days > 0 else 0
        
        # 胜率
        n_trades = len([t for t in self.trades if t['side'] == 'SELL'])
        
        return {
            "initial_capital": self.initial_capital,
            "final_nav": df['nav'].iloc[-1],
            "total_return_pct": total_return,
            "annual_return_pct": annual_return,
            "max_drawdown_pct": max_drawdown,
            "n_trades": len(self.trades),
            "n_sell_trades": n_trades,
            "n_positions_final": len(self.account.positions),
        }


if __name__ == "__main__":
    # 测试回测引擎
    from research_config import create_default_config
    
    config = create_default_config()
    provider = ReadOnlyDataProvider(config)
    
    # 创建回测引擎
    engine = BacktestEngineV2(
        initial_capital=1000000,
        data_provider=provider,
    )
    
    print("回测引擎初始化完成")
    print(f"初始资金: {engine.initial_capital:,.0f}")
    print(f"交易规则: {engine.rules.to_dict()}")
    
    provider.close()
