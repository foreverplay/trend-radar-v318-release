"""组合管理器 — Phase 2

负责仓位分配、持仓上限、资金管理
"""
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np


class PortfolioManager:
    """组合管理器
    
    职责：
    1. 根据信号分配仓位
    2. 管理持仓上限
    3. 计算每只股票的目标仓位
    4. 处理超额信号（排序、过滤）
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Args:
            config: 组合配置，包含:
                - initial_capital: 初始资金 (default: 1000000)
                - max_positions: 最大持仓数 (default: 10)
                - position_sizing: 仓位分配方式 ('equal_weight', 'grade_weight')
                - single_position_limit: 单只最大仓位 (default: 0.2 = 20%)
                - reserve_cash_rate: 预留现金比例 (default: 0.05 = 5%)
        """
        self.config = config or {}
        
        self.initial_capital = self.config.get('initial_capital', 1000000)
        self.max_positions = self.config.get('max_positions', 10)
        self.position_sizing = self.config.get('position_sizing', 'equal_weight')
        self.single_position_limit = self.config.get('single_position_limit', 0.2)
        self.reserve_cash_rate = self.config.get('reserve_cash_rate', 0.05)
        
        # 当前状态
        self.current_capital = self.initial_capital
        self.positions = {}  # {code: {'shares': int, 'avg_cost': float}}
        
    def calculate_target_positions(self,
                                   buy_signals: Dict[str, Dict],
                                   sell_signals: List[str],
                                   current_positions: Dict[str, Dict],
                                   prices: Dict[str, float],
                                   available_cash: float) -> Dict[str, Dict]:
        """计算目标仓位
        
        Args:
            buy_signals: {code: signal_data} 买入信号
            sell_signals: [code, ...] 卖出信号
            current_positions: {code: {'shares': int, 'avg_cost': float}}
            prices: {code: current_price}
            available_cash: 可用现金
            
        Returns:
            {code: {'action': 'BUY'/'SELL'/'HOLD', 'target_shares': int, 'weight': float}}
        """
        targets = {}
        
        # 1. 处理卖出
        for code in sell_signals:
            if code in current_positions:
                targets[code] = {
                    'action': 'SELL',
                    'target_shares': 0,
                    'current_shares': current_positions[code]['shares'],
                    'weight': 0.0
                }
        
        # 2. 过滤已卖出的持仓
        remaining_positions = {k: v for k, v in current_positions.items() 
                              if k not in sell_signals}
        
        # 3. 计算可用于新开仓的数量
        open_slots = self.max_positions - len(remaining_positions)
        
        if open_slots <= 0:
            # 已满仓，不再开新仓
            return targets
        
        # 4. 筛选买入信号（排除已持仓）
        new_buy_signals = {k: v for k, v in buy_signals.items() 
                          if k not in remaining_positions}
        
        if not new_buy_signals:
            return targets
        
        # 5. 按优先级排序（Grade > Entry > Structure Score）
        sorted_signals = self._sort_signals(new_buy_signals)
        
        # 6. 取前 open_slots 个信号
        selected_codes = sorted_signals[:open_slots]
        
        # 7. 计算每只股票的目标权重
        weights = self._calculate_weights(selected_codes, buy_signals)
        
        # 8. 预留现金后可用资金
        investable_cash = available_cash * (1 - self.reserve_cash_rate)
        
        # 9. 计算每只股票的目标金额和股数
        for code in selected_codes:
            if code not in prices:
                continue
            
            weight = weights.get(code, 0)
            target_value = investable_cash * weight
            price = prices[code]
            
            # 计算股数（A股100股起，整百股）
            target_shares = int(target_value / price / 100) * 100
            
            if target_shares > 0:
                targets[code] = {
                    'action': 'BUY',
                    'target_shares': target_shares,
                    'current_shares': 0,
                    'weight': weight,
                    'estimated_value': target_shares * price
                }
        
        return targets
    
    def _sort_signals(self, signals: Dict[str, Dict]) -> List[str]:
        """信号排序（优先级）
        
        排序规则：
        1. TrendGrade (A+ > A > B > C)
        2. EntryStatus (PRIME > CONFIRMED > FOLLOW)
        3. TrendStructureScore (降序)
        
        Args:
            signals: {code: signal_data}
            
        Returns:
            [code, ...] 排序后的代码列表
        """
        grade_rank = {'A+': 0, 'A': 1, 'B': 2, 'C': 3, 'WATCH': 4, 'FILTER': 5}
        entry_rank = {'PRIME': 0, 'CONFIRMED': 1, 'FOLLOW': 2, 'EXTENDED': 3, 'NO_ENTRY': 4}
        
        items = []
        for code, data in signals.items():
            grade = data.get('grade', 'FILTER')
            entry = data.get('entry_status', 'NO_ENTRY')
            structure = data.get('structure_score', 0)
            
            items.append((
                code,
                grade_rank.get(grade, 5),
                entry_rank.get(entry, 4),
                -structure  # 降序
            ))
        
        # 多级排序
        items.sort(key=lambda x: (x[1], x[2], x[3]))
        
        return [item[0] for item in items]
    
    def _calculate_weights(self, codes: List[str], signals: Dict[str, Dict]) -> Dict[str, float]:
        """计算仓位权重
        
        Args:
            codes: 选中的股票代码
            signals: 信号数据
            
        Returns:
            {code: weight}
        """
        if not codes:
            return {}
        
        if self.position_sizing == 'equal_weight':
            # 等权
            weight = 1.0 / len(codes)
            return {code: min(weight, self.single_position_limit) for code in codes}
        
        elif self.position_sizing == 'grade_weight':
            # 按 Grade 加权（A+ 权重更高）
            grade_weights = {'A+': 1.5, 'A': 1.2, 'B': 1.0, 'C': 0.8}
            
            raw_weights = {}
            total = 0
            for code in codes:
                grade = signals[code].get('grade', 'C')
                w = grade_weights.get(grade, 1.0)
                raw_weights[code] = w
                total += w
            
            # 归一化
            weights = {code: min(w / total, self.single_position_limit) 
                      for code, w in raw_weights.items()}
            
            return weights
        
        else:
            # 默认等权
            weight = 1.0 / len(codes)
            return {code: min(weight, self.single_position_limit) for code in codes}
    
    def update_positions(self, 
                        executions: Dict[str, Dict],
                        prices: Dict[str, float]):
        """更新持仓（成交后）
        
        Args:
            executions: {code: {'action': 'BUY'/'SELL', 'shares': int, 'price': float, 'fee': float}}
            prices: {code: current_price}
        """
        for code, exec_data in executions.items():
            action = exec_data['action']
            shares = exec_data['shares']
            price = exec_data['price']
            fee = exec_data.get('fee', 0)
            
            if action == 'BUY':
                # 买入
                if code in self.positions:
                    # 已有持仓，更新平均成本
                    old_shares = self.positions[code]['shares']
                    old_cost = self.positions[code]['avg_cost']
                    new_shares = old_shares + shares
                    new_cost = ((old_shares * old_cost + shares * price + fee) / new_shares)
                    self.positions[code] = {'shares': new_shares, 'avg_cost': new_cost}
                else:
                    # 新建持仓
                    self.positions[code] = {'shares': shares, 'avg_cost': (price + fee / shares)}
                
                # 扣除现金
                self.current_capital -= (shares * price + fee)
            
            elif action == 'SELL':
                # 卖出
                if code in self.positions:
                    self.positions[code]['shares'] -= shares
                    if self.positions[code]['shares'] <= 0:
                        del self.positions[code]
                
                # 增加现金
                self.current_capital += (shares * price - fee)
    
    def get_portfolio_value(self, prices: Dict[str, float]) -> Dict:
        """获取组合市值
        
        Args:
            prices: {code: current_price}
            
        Returns:
            {'cash': float, 'stock_value': float, 'total_value': float, 'positions': {...}}
        """
        stock_value = 0
        position_details = {}
        
        for code, pos in self.positions.items():
            if code in prices:
                market_value = pos['shares'] * prices[code]
                stock_value += market_value
                position_details[code] = {
                    'shares': pos['shares'],
                    'avg_cost': pos['avg_cost'],
                    'current_price': prices[code],
                    'market_value': market_value,
                    'pnl': market_value - pos['shares'] * pos['avg_cost'],
                    'pnl_pct': (prices[code] / pos['avg_cost'] - 1) * 100
                }
        
        return {
            'cash': self.current_capital,
            'stock_value': stock_value,
            'total_value': self.current_capital + stock_value,
            'positions': position_details,
            'position_count': len(self.positions)
        }


if __name__ == "__main__":
    # 测试
    config = {
        'initial_capital': 1000000,
        'max_positions': 10,
        'position_sizing': 'equal_weight',
        'single_position_limit': 0.2,
        'reserve_cash_rate': 0.05
    }
    
    pm = PortfolioManager(config)
    
    # 模拟买入信号
    buy_signals = {
        '600519': {'grade': 'A+', 'entry_status': 'PRIME', 'structure_score': 90},
        '600036': {'grade': 'A', 'entry_status': 'CONFIRMED', 'structure_score': 85},
        '000858': {'grade': 'A', 'entry_status': 'CONFIRMED', 'structure_score': 82},
    }
    
    prices = {'600519': 1800, '600036': 30, '000858': 150}
    
    targets = pm.calculate_target_positions(
        buy_signals=buy_signals,
        sell_signals=[],
        current_positions={},
        prices=prices,
        available_cash=1000000
    )
    
    print("目标仓位:")
    for code, target in targets.items():
        print(f"  {code}: {target}")
    
    print("\n✅ PortfolioManager 测试完成")
