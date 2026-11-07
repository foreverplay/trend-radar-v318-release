"""交易信号生成器 — Phase 2

将 V3.1.8 评分系统转化为可执行交易规则
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd

# 添加父目录到路径以导入生产模块
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.grading_v318 import grade_trend_v318
from v3.v31.entry_window import classify_entry_window
from v3.v31.types import EntryWindow


class SignalGenerator:
    """交易信号生成器
    
    职责：
    1. 从 TrendGrade + EntryStatus 生成 BUY/SELL/HOLD 信号
    2. as-of 回放（只使用截至信号日的历史数据）
    3. 明确入场/退出触发条件
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Args:
            config: 信号配置，包含:
                - entry_grades: 允许入场的 TrendGrade (default: ['A+', 'A'])
                - entry_statuses: 允许入场的 EntryStatus (default: ['PRIME', 'CONFIRMED'])
                - exit_on_no_entry: EntryStatus=NO_ENTRY 时退出 (default: True)
                - exit_on_filter: TrendGrade=FILTER 时退出 (default: True)
        """
        self.config = config or {}
        
        # 默认配置
        self.entry_grades = self.config.get('entry_grades', ['A+', 'A'])
        self.entry_statuses = self.config.get('entry_statuses', ['PRIME', 'CONFIRMED'])
        self.exit_on_no_entry = self.config.get('exit_on_no_entry', True)
        self.exit_on_filter = self.config.get('exit_on_filter', True)
        
    def generate_signal(self,
                       code: str,
                       date: str,
                       trend_data: Dict,
                       current_position: bool = False) -> Tuple[str, Dict]:
        """生成单只股票的交易信号
        
        Args:
            code: 股票代码
            date: 信号日期 (as-of date)
            trend_data: 趋势数据，包含:
                - trend_grade: TrendGrade (A+/A/B/C/WATCH/FILTER)
                - entry_status: EntryStatus (PRIME/CONFIRMED/FOLLOW/EXTENDED/NO_ENTRY)
                - exit_signal: ExitSignal (HOLD/PROTECT/REDUCE_RISK/SELL/...)
                - broken: bool
                - ... (其他 V3.1.8 输出)
            current_position: 当前是否持仓
            
        Returns:
            (signal, reason_dict)
            - signal: 'BUY' / 'SELL' / 'HOLD'
            - reason_dict: 决策原因
        """
        grade = trend_data.get('trend_grade', 'FILTER')
        entry_status = trend_data.get('entry_status', 'NO_ENTRY')
        exit_signal = trend_data.get('exit_signal', 'HOLD')
        broken = trend_data.get('broken', False)
        
        reasons = {
            'code': code,
            'date': date,
            'grade': grade,
            'entry_status': entry_status,
            'exit_signal': exit_signal,
            'current_position': current_position
        }
        
        # === 退出逻辑（优先）===
        if current_position:
            # 1. 趋势破坏
            if broken or grade == 'FILTER':
                if self.exit_on_filter:
                    reasons['trigger'] = 'trend_broken'
                    return 'SELL', reasons
            
            # 2. 入场窗口消失
            if entry_status == 'NO_ENTRY':
                if self.exit_on_no_entry:
                    reasons['trigger'] = 'no_entry_window'
                    return 'SELL', reasons
            
            # 3. ExitEngine 信号
            if exit_signal and exit_signal != 'HOLD':
                reasons['trigger'] = f'exit_signal_{exit_signal}'
                return 'SELL', reasons
            
            # 4. 持有
            reasons['trigger'] = 'hold_position'
            return 'HOLD', reasons
        
        # === 入场逻辑 ===
        if not current_position:
            # 检查 Grade 和 EntryStatus
            grade_ok = grade in self.entry_grades
            entry_ok = entry_status in self.entry_statuses
            
            if grade_ok and entry_ok:
                reasons['trigger'] = 'entry_signal'
                reasons['grade_match'] = grade
                reasons['entry_match'] = entry_status
                return 'BUY', reasons
            else:
                reasons['trigger'] = 'no_signal'
                reasons['grade_ok'] = grade_ok
                reasons['entry_ok'] = entry_ok
                return 'HOLD', reasons
        
        # 默认
        reasons['trigger'] = 'default_hold'
        return 'HOLD', reasons
    
    def generate_universe_signals(self,
                                  date: str,
                                  universe: List[str],
                                  trend_data_dict: Dict[str, Dict],
                                  current_positions: Dict[str, bool] = None) -> Dict[str, Tuple[str, Dict]]:
        """生成股票池的批量信号
        
        Args:
            date: 信号日期
            universe: 股票代码列表
            trend_data_dict: {code: trend_data}
            current_positions: {code: bool} 当前持仓状态
            
        Returns:
            {code: (signal, reasons)}
        """
        if current_positions is None:
            current_positions = {}
        
        signals = {}
        for code in universe:
            if code not in trend_data_dict:
                # 无趋势数据，跳过
                continue
            
            trend_data = trend_data_dict[code]
            position = current_positions.get(code, False)
            
            signal, reasons = self.generate_signal(code, date, trend_data, position)
            signals[code] = (signal, reasons)
        
        return signals
    
    def to_dataframe(self, signals_dict: Dict[str, Tuple[str, Dict]]) -> pd.DataFrame:
        """将信号字典转为 DataFrame
        
        Args:
            signals_dict: {code: (signal, reasons)}
            
        Returns:
            DataFrame with columns: code, date, signal, grade, entry_status, trigger, ...
        """
        rows = []
        for code, (signal, reasons) in signals_dict.items():
            rows.append({
                'code': code,
                'date': reasons.get('date'),
                'signal': signal,
                'grade': reasons.get('grade'),
                'entry_status': reasons.get('entry_status'),
                'exit_signal': reasons.get('exit_signal'),
                'trigger': reasons.get('trigger'),
                'current_position': reasons.get('current_position'),
            })
        
        return pd.DataFrame(rows)


# === 使用示例 ===
if __name__ == "__main__":
    # 示例配置
    config = {
        'entry_grades': ['A+', 'A'],
        'entry_statuses': ['PRIME', 'CONFIRMED'],
        'exit_on_no_entry': True,
        'exit_on_filter': True
    }
    
    generator = SignalGenerator(config)
    
    # 示例趋势数据
    trend_data_1 = {
        'trend_grade': 'A+',
        'entry_status': 'PRIME',
        'exit_signal': 'HOLD',
        'broken': False
    }
    
    trend_data_2 = {
        'trend_grade': 'A',
        'entry_status': 'CONFIRMED',
        'exit_signal': 'HOLD',
        'broken': False
    }
    
    trend_data_3 = {
        'trend_grade': 'B',
        'entry_status': 'FOLLOW',
        'exit_signal': 'HOLD',
        'broken': False
    }
    
    # 生成信号
    signal1, reason1 = generator.generate_signal('600519', '2024-01-15', trend_data_1, current_position=False)
    print(f"600519: {signal1} - {reason1['trigger']}")  # BUY - entry_signal
    
    signal2, reason2 = generator.generate_signal('600036', '2024-01-15', trend_data_2, current_position=False)
    print(f"600036: {signal2} - {reason2['trigger']}")  # BUY - entry_signal
    
    signal3, reason3 = generator.generate_signal('000858', '2024-01-15', trend_data_3, current_position=False)
    print(f"000858: {signal3} - {reason3['trigger']}")  # HOLD - no_signal
    
    # 持仓退出示例
    trend_data_exit = {
        'trend_grade': 'A+',
        'entry_status': 'NO_ENTRY',
        'exit_signal': 'HOLD',
        'broken': False
    }
    signal_exit, reason_exit = generator.generate_signal('600519', '2024-01-20', trend_data_exit, current_position=True)
    print(f"600519 (持仓): {signal_exit} - {reason_exit['trigger']}")  # SELL - no_entry_window
    
    print("\n✅ 信号生成器测试完成")
