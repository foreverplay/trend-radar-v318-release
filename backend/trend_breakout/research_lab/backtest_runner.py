"""回测执行器 — Phase 2

扩展 v3/backtest/engine.py，增加 as-of 信号生成、版本化存储
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import json

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from research_lab.signal_generator import SignalGenerator
from research_lab.trading_rules import RuleProvider
from research_lab.portfolio_manager import PortfolioManager
from v3.backtest.costs import is_limit_up, is_limit_down


class BacktestRunner:
    """回测执行器
    
    职责：
    1. as-of 信号生成（每日根据历史数据生成信号）
    2. 执行买卖（T+1、涨跌停、停牌处理）
    3. 组合管理（仓位分配、持仓更新）
    4. 结果记录（每日净值、交易记录）
    5. 版本化存储（独立 run_id）
    """
    
    def __init__(self,
                 signal_generator: SignalGenerator,
                 portfolio_manager: PortfolioManager,
                 rule_provider: RuleProvider,
                 config: Optional[Dict] = None):
        """
        Args:
            signal_generator: 信号生成器
            portfolio_manager: 组合管理器
            rule_provider: 交易规则提供者
            config: 回测配置
        """
        self.signal_gen = signal_generator
        self.portfolio_mgr = portfolio_manager
        self.rule_provider = rule_provider
        self.config = config or {}
        
        # 回测参数
        self.start_date = self.config.get('start_date')
        self.end_date = self.config.get('end_date')
        self.slippage = self.config.get('slippage', 0.001)
        
        # 结果记录
        self.daily_nav = []  # 每日净值
        self.trades = []  # 交易记录
        self.signals_history = []  # 信号历史
        
        # 运行 ID
        self.run_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    def run(self,
            universe: List[str],
            trend_data_func,
            price_data_func,
            trading_dates: List[str]) -> Dict:
        """执行回测
        
        Args:
            universe: 股票池
            trend_data_func: 获取趋势数据函数 (code, date) -> trend_dict
            price_data_func: 获取价格数据函数 (code, date) -> {'open', 'close', 'high', 'low', 'volume', 'pct_chg'}
            trading_dates: 交易日列表（升序）
            
        Returns:
            回测结果字典
        """
        print(f"\n{'='*70}")
        print(f"回测执行器 - Run ID: {self.run_id}")
        print(f"{'='*70}")
        print(f"股票池: {len(universe)} 只")
        print(f"日期范围: {trading_dates[0]} 至 {trading_dates[-1]} ({len(trading_dates)} 个交易日)")
        print(f"初始资金: {self.portfolio_mgr.initial_capital:,.0f}")
        print(f"{'='*70}\n")
        
        # 初始化净值
        current_positions = {}  # {code: bool} 持仓标记
        pending_orders = {}  # {code: {'action': 'BUY'/'SELL', 'signal_date': str}}
        
        for i, date in enumerate(trading_dates):
            # === 1. 获取当前价格 ===
            prices = {}
            price_data = {}
            for code in universe:
                try:
                    data = price_data_func(code, date)
                    if data is not None and not data.empty:
                        prices[code] = float(data.iloc[-1]['close'])
                        price_data[code] = data.iloc[-1]
                except Exception:
                    continue
            
            if not prices:
                continue
            
            # === 2. 执行昨日挂单（T+1）===
            if pending_orders:
                self._execute_orders(pending_orders, price_data, date, current_positions)
                pending_orders = {}
            
            # === 3. 生成今日信号 ===
            trend_data_dict = {}
            for code in universe:
                try:
                    trend = trend_data_func(code, date)
                    if trend:
                        trend_data_dict[code] = trend
                except Exception:
                    continue
            
            if not trend_data_dict:
                # 无趋势数据，跳过
                self._record_nav(date, prices)
                continue
            
            signals = self.signal_gen.generate_universe_signals(
                date=date,
                universe=universe,
                trend_data_dict=trend_data_dict,
                current_positions=current_positions
            )
            
            # === 4. 信号分类 ===
            buy_signals = {code: reasons for code, (sig, reasons) in signals.items() if sig == 'BUY'}
            sell_codes = [code for code, (sig, _) in signals.items() if sig == 'SELL']
            
            # 记录信号
            self.signals_history.append({
                'date': date,
                'buy_count': len(buy_signals),
                'sell_count': len(sell_codes),
                'buy_codes': list(buy_signals.keys()),
                'sell_codes': sell_codes
            })
            
            # === 5. 组合管理：计算目标仓位 ===
            available_cash = self.portfolio_mgr.current_capital
            
            targets = self.portfolio_mgr.calculate_target_positions(
                buy_signals=buy_signals,
                sell_signals=sell_codes,
                current_positions=self.portfolio_mgr.positions,
                prices=prices,
                available_cash=available_cash
            )
            
            # === 6. 生成挂单（次日执行）===
            for code, target in targets.items():
                action = target['action']
                if action == 'BUY':
                    pending_orders[code] = {'action': 'BUY', 'signal_date': date, 'target_shares': target['target_shares']}
                    current_positions[code] = True  # 标记将持仓
                elif action == 'SELL':
                    pending_orders[code] = {'action': 'SELL', 'signal_date': date, 'shares': target['current_shares']}
                    if code in current_positions:
                        del current_positions[code]  # 标记将清仓
            
            # === 7. 记录每日净值 ===
            self._record_nav(date, prices)
            
            # 进度
            if (i + 1) % 50 == 0 or (i + 1) == len(trading_dates):
                portfolio = self.portfolio_mgr.get_portfolio_value(prices)
                print(f"[{i+1}/{len(trading_dates)}] {date}: "
                      f"净值={portfolio['total_value']:,.0f}, "
                      f"持仓={portfolio['position_count']}只, "
                      f"现金={portfolio['cash']:,.0f}")
        
        # === 8. 汇总结果 ===
        result = self._summarize_results()
        
        print(f"\n{'='*70}")
        print(f"回测完成")
        print(f"{'='*70}")
        print(f"总交易次数: {len(self.trades)}")
        print(f"期末净值: {result['final_nav']:,.0f}")
        print(f"总收益率: {result['total_return']:.2%}")
        print(f"{'='*70}\n")
        
        return result
    
    def _execute_orders(self, 
                       orders: Dict[str, Dict],
                       price_data: Dict[str, pd.Series],
                       date: str,
                       current_positions: Dict[str, bool]):
        """执行挂单
        
        Args:
            orders: {code: {'action': 'BUY'/'SELL', ...}}
            price_data: {code: price_series}
            date: 当前日期
            current_positions: 持仓标记
        """
        executions = {}
        
        for code, order in orders.items():
            if code not in price_data:
                # 无价格数据，跳过
                continue
            
            data = price_data[code]
            action = order['action']
            
            # 获取开盘价
            open_price = float(data.get('open', data.get('close', 0)))
            if open_price <= 0:
                continue
            
            # 检查涨跌停
            pct_chg = data.get('pct_chg', 0)
            board = self.rule_provider.get_board_from_code(code)
            
            if action == 'BUY':
                # 买入：涨停不可买
                if is_limit_up(pct_chg, code):
                    continue
                
                # 计算成交价（开盘价 + 滑点）
                fill_price = open_price * (1 + self.slippage)
                shares = order.get('target_shares', 0)
                
                if shares <= 0:
                    continue
                
                # 计算费用
                costs = self.rule_provider.get_trading_costs(date, code)
                notional = fill_price * shares
                fee = notional * costs['commission']
                
                executions[code] = {
                    'action': 'BUY',
                    'shares': shares,
                    'price': fill_price,
                    'fee': fee,
                    'date': date
                }
                
                # 记录交易
                self.trades.append({
                    'code': code,
                    'date': date,
                    'signal_date': order.get('signal_date'),
                    'action': 'BUY',
                    'shares': shares,
                    'price': fill_price,
                    'fee': fee,
                    'notional': notional
                })
            
            elif action == 'SELL':
                # 卖出：跌停不可卖
                if is_limit_down(pct_chg, code):
                    continue
                
                # 计算成交价（开盘价 - 滑点）
                fill_price = open_price * (1 - self.slippage)
                shares = order.get('shares', 0)
                
                if shares <= 0:
                    continue
                
                # 计算费用（佣金 + 印花税）
                costs = self.rule_provider.get_trading_costs(date, code)
                notional = fill_price * shares
                fee = notional * (costs['commission'] + costs['stamp'])
                
                executions[code] = {
                    'action': 'SELL',
                    'shares': shares,
                    'price': fill_price,
                    'fee': fee,
                    'date': date
                }
                
                # 记录交易
                self.trades.append({
                    'code': code,
                    'date': date,
                    'signal_date': order.get('signal_date'),
                    'action': 'SELL',
                    'shares': shares,
                    'price': fill_price,
                    'fee': fee,
                    'notional': notional
                })
        
        # 更新持仓
        if executions:
            # 需要价格字典
            prices = {code: float(price_data[code]['close']) for code in price_data.keys()}
            self.portfolio_mgr.update_positions(executions, prices)
    
    def _record_nav(self, date: str, prices: Dict[str, float]):
        """记录每日净值"""
        portfolio = self.portfolio_mgr.get_portfolio_value(prices)
        
        self.daily_nav.append({
            'date': date,
            'cash': portfolio['cash'],
            'stock_value': portfolio['stock_value'],
            'total_value': portfolio['total_value'],
            'position_count': portfolio['position_count']
        })
    
    def _summarize_results(self) -> Dict:
        """汇总回测结果"""
        if not self.daily_nav:
            return {'error': 'no_data'}
        
        nav_df = pd.DataFrame(self.daily_nav)
        
        initial_nav = self.portfolio_mgr.initial_capital
        final_nav = nav_df.iloc[-1]['total_value']
        total_return = (final_nav / initial_nav - 1)
        
        # 计算收益率序列
        nav_df['nav'] = nav_df['total_value']
        nav_df['daily_return'] = nav_df['nav'].pct_change()
        
        # 最大回撤
        cummax = nav_df['nav'].cummax()
        drawdown = (nav_df['nav'] - cummax) / cummax
        max_drawdown = drawdown.min()
        
        # 交易统计
        trades_df = pd.DataFrame(self.trades) if self.trades else pd.DataFrame()
        
        result = {
            'run_id': self.run_id,
            'start_date': nav_df.iloc[0]['date'],
            'end_date': nav_df.iloc[-1]['date'],
            'trading_days': len(nav_df),
            'initial_nav': initial_nav,
            'final_nav': final_nav,
            'total_return': total_return,
            'max_drawdown': max_drawdown,
            'trade_count': len(self.trades),
            'daily_nav': nav_df.to_dict('records'),
            'trades': trades_df.to_dict('records') if not trades_df.empty else [],
            'signals_history': self.signals_history,
            'config': self.config
        }
        
        return result
    
    def save_results(self, result: Dict, output_dir: Path):
        """保存回测结果
        
        Args:
            result: 回测结果字典
            output_dir: 输出目录
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        run_id = result['run_id']
        
        # 保存完整结果（JSON）
        result_file = output_dir / f"backtest_{run_id}.json"
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(f"✅ 结果已保存: {result_file}")
        
        # 保存净值曲线（CSV）
        nav_df = pd.DataFrame(result['daily_nav'])
        nav_file = output_dir / f"nav_{run_id}.csv"
        nav_df.to_csv(nav_file, index=False, encoding='utf-8-sig')
        print(f"✅ 净值曲线: {nav_file}")
        
        # 保存交易记录（CSV）
        if result['trades']:
            trades_df = pd.DataFrame(result['trades'])
            trades_file = output_dir / f"trades_{run_id}.csv"
            trades_df.to_csv(trades_file, index=False, encoding='utf-8-sig')
            print(f"✅ 交易记录: {trades_file}")
        
        return result_file


if __name__ == "__main__":
    print("BacktestRunner 已创建")
    print("完整测试见 experiments/E01_trend_baseline.py")
