"""交易规则提供者 — Phase 2

按日期/板块/股票动态加载交易规则（费率、涨跌停幅度、T+N）
"""
from datetime import datetime
from typing import Dict, Optional
import pandas as pd


class RuleProvider:
    """交易规则提供者
    
    职责：
    1. 按历史日期提供佣金/印花税费率
    2. 按板块提供涨跌停幅度
    3. 按日期提供 T+N 规则
    4. 支持未来规则变更（如 2026-07-06）
    """
    
    def __init__(self, rules_config: Optional[Dict] = None):
        """
        Args:
            rules_config: 规则配置字典，包含:
                - commission_history: [{effective_date, rate}]
                - stamp_history: [{effective_date, rate}]
                - limit_rules: [{effective_date, board, limit_pct}]
                - t_plus_n: [{effective_date, asset_type, t_plus_n}]
        """
        self.rules_config = rules_config or self._default_rules()
        
        # 解析佣金历史
        self.commission_history = pd.DataFrame(self.rules_config.get('commission_history', []))
        if not self.commission_history.empty:
            self.commission_history['effective_date'] = pd.to_datetime(
                self.commission_history['effective_date']
            )
            self.commission_history = self.commission_history.sort_values('effective_date')
        
        # 解析印花税历史
        self.stamp_history = pd.DataFrame(self.rules_config.get('stamp_history', []))
        if not self.stamp_history.empty:
            self.stamp_history['effective_date'] = pd.to_datetime(
                self.stamp_history['effective_date']
            )
            self.stamp_history = self.stamp_history.sort_values('effective_date')
        
        # 解析涨跌停规则
        self.limit_rules = pd.DataFrame(self.rules_config.get('limit_rules', []))
        if not self.limit_rules.empty:
            self.limit_rules['effective_date'] = pd.to_datetime(
                self.limit_rules['effective_date']
            )
            self.limit_rules = self.limit_rules.sort_values('effective_date')
        
        # 解析 T+N 规则
        self.t_plus_n_rules = pd.DataFrame(self.rules_config.get('t_plus_n', []))
        if not self.t_plus_n_rules.empty:
            self.t_plus_n_rules['effective_date'] = pd.to_datetime(
                self.t_plus_n_rules['effective_date']
            )
            self.t_plus_n_rules = self.t_plus_n_rules.sort_values('effective_date')
    
    @staticmethod
    def _default_rules() -> Dict:
        """默认交易规则"""
        return {
            # 佣金历史（万三）
            'commission_history': [
                {'effective_date': '2000-01-01', 'rate': 0.0003, 'note': '万三'},
            ],
            # 印花税历史
            'stamp_history': [
                {'effective_date': '2000-01-01', 'rate': 0.001, 'note': '千一卖出'},
            ],
            # 涨跌停规则
            'limit_rules': [
                # 主板/中小板
                {'effective_date': '2000-01-01', 'board': 'main', 'limit_pct': 0.10, 'note': '±10%'},
                # 创业板/科创板
                {'effective_date': '2020-08-24', 'board': 'chinext', 'limit_pct': 0.20, 'note': '创业板注册制±20%'},
                {'effective_date': '2019-07-22', 'board': 'star', 'limit_pct': 0.20, 'note': '科创板±20%'},
                # 北交所
                {'effective_date': '2021-11-15', 'board': 'bj', 'limit_pct': 0.30, 'note': '北交所±30%'},
                # 2026 规则修订（待核验）
                # {'effective_date': '2026-07-06', 'board': 'main', 'limit_pct': 0.15, 'note': '假设修订为±15%（待官方确认）'},
            ],
            # T+N 规则
            't_plus_n': [
                {'effective_date': '2000-01-01', 'asset_type': 'stock', 't_plus_n': 1, 'note': 'A股T+1'},
                {'effective_date': '2000-01-01', 'asset_type': 'etf', 't_plus_n': 0, 'note': 'ETF T+0'},
            ]
        }
    
    def _get_rule_as_of(self, df: pd.DataFrame, date: str, key: str = None) -> Optional[float]:
        """获取截至某日期的规则值
        
        Args:
            df: 规则 DataFrame
            date: 查询日期
            key: 规则键（如 'board'）
            
        Returns:
            规则值或 None
        """
        if df.empty:
            return None
        
        date_dt = pd.to_datetime(date)
        valid_rules = df[df['effective_date'] <= date_dt]
        
        if valid_rules.empty:
            return None
        
        # 返回最近的规则
        return valid_rules.iloc[-1]
    
    def get_commission(self, date: str) -> float:
        """获取佣金率
        
        Args:
            date: 交易日期 (YYYY-MM-DD)
            
        Returns:
            佣金率（如 0.0003 = 万三）
        """
        if self.commission_history.empty:
            return 0.0003  # 默认万三
        
        rule = self._get_rule_as_of(self.commission_history, date)
        return rule['rate'] if rule is not None else 0.0003
    
    def get_stamp_tax(self, date: str) -> float:
        """获取印花税率
        
        Args:
            date: 交易日期
            
        Returns:
            印花税率（如 0.001 = 千一）
        """
        if self.stamp_history.empty:
            return 0.001  # 默认千一
        
        rule = self._get_rule_as_of(self.stamp_history, date)
        return rule['rate'] if rule is not None else 0.001
    
    def get_limit_pct(self, date: str, board: str) -> float:
        """获取涨跌停幅度
        
        Args:
            date: 交易日期
            board: 板块 ('main', 'chinext', 'star', 'bj')
            
        Returns:
            单边涨跌停幅度（如 0.10 = ±10%）
        """
        if self.limit_rules.empty:
            # 默认规则
            default_limits = {
                'main': 0.10,
                'chinext': 0.20,
                'star': 0.20,
                'bj': 0.30
            }
            return default_limits.get(board, 0.10)
        
        date_dt = pd.to_datetime(date)
        valid_rules = self.limit_rules[
            (self.limit_rules['effective_date'] <= date_dt) &
            (self.limit_rules['board'] == board)
        ]
        
        if valid_rules.empty:
            # 回退到默认
            return {'main': 0.10, 'chinext': 0.20, 'star': 0.20, 'bj': 0.30}.get(board, 0.10)
        
        return valid_rules.iloc[-1]['limit_pct']
    
    def get_board_from_code(self, code: str) -> str:
        """从代码推断板块
        
        Args:
            code: 股票代码（6位）
            
        Returns:
            板块标识 ('main', 'chinext', 'star', 'bj')
        """
        c = str(code).zfill(6)
        
        if c.startswith('688'):
            return 'star'  # 科创板
        elif c.startswith(('300', '301')):
            return 'chinext'  # 创业板
        elif c.startswith(('8', '4')) or c.startswith(('83', '87', '43', '92')):
            return 'bj'  # 北交所
        else:
            return 'main'  # 主板/中小板
    
    def get_t_plus_n(self, date: str, asset_type: str = 'stock') -> int:
        """获取 T+N 规则
        
        Args:
            date: 交易日期
            asset_type: 资产类型 ('stock', 'etf')
            
        Returns:
            T+N 天数（如 1 = T+1）
        """
        if self.t_plus_n_rules.empty:
            return 1 if asset_type == 'stock' else 0
        
        date_dt = pd.to_datetime(date)
        valid_rules = self.t_plus_n_rules[
            (self.t_plus_n_rules['effective_date'] <= date_dt) &
            (self.t_plus_n_rules['asset_type'] == asset_type)
        ]
        
        if valid_rules.empty:
            return 1 if asset_type == 'stock' else 0
        
        return int(valid_rules.iloc[-1]['t_plus_n'])
    
    def get_trading_costs(self, date: str, code: str) -> Dict[str, float]:
        """获取综合交易成本
        
        Args:
            date: 交易日期
            code: 股票代码
            
        Returns:
            {'commission': float, 'stamp': float, 'limit_pct': float, 't_plus_n': int, 'board': str}
        """
        board = self.get_board_from_code(code)
        
        return {
            'commission': self.get_commission(date),
            'stamp': self.get_stamp_tax(date),
            'limit_pct': self.get_limit_pct(date, board),
            't_plus_n': self.get_t_plus_n(date, 'stock'),
            'board': board,
            'slippage': 0.001  # 固定 0.1% 滑点
        }


# === 使用示例 ===
if __name__ == "__main__":
    provider = RuleProvider()
    
    # 测试不同日期
    dates = ['2020-01-01', '2023-01-01', '2026-01-01', '2026-08-01']
    codes = ['600519', '300750', '688001', '830010']
    
    print("=" * 70)
    print("交易规则测试")
    print("=" * 70)
    
    for date in dates:
        print(f"\n日期: {date}")
        print("-" * 70)
        
        for code in codes:
            costs = provider.get_trading_costs(date, code)
            print(f"  {code} ({costs['board']}): "
                  f"佣金={costs['commission']:.4f}, "
                  f"印花税={costs['stamp']:.4f}, "
                  f"涨跌停={costs['limit_pct']:.1%}, "
                  f"T+{costs['t_plus_n']}")
    
    print("\n" + "=" * 70)
    print("✅ RuleProvider 测试完成")
    print("=" * 70)
    
    print("\n⚠️ 2026-07-06 规则修订:")
    print("- 当前代码已预留扩展位置")
    print("- 待官方文档核实后取消注释并更新 limit_pct")
