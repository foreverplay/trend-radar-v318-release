"""退出规则模块 - 明确趋势退出策略

基于V3.1.8的exit_signal，与入场分离，不简单NO_ENTRY=SELL

退出条件（集中配置）:
1. exit_signal触发（趋势破坏/失败）
2. TrendGrade降级到FILTER
3. 最大持仓期限（如60天）
4. 强制止损（如-20%）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import pandas as pd


@dataclass
class ExitConfig:
    """退出规则配置"""
    
    # 基于V3.1.8 exit_signal
    use_v318_exit_signal: bool = True
    exit_signals: list[str] = None  # ['WATCH', 'EXIT'] 触发退出
    
    # 基于TrendGrade降级
    exit_on_filter: bool = True  # FILTER级别退出
    
    # 基于EntryStatus（保守）
    exit_on_no_entry: bool = False  # NO_ENTRY不一定退出（可能只是休整）
    
    # 最大持仓期
    max_holding_days: Optional[int] = 60  # 超过N天强制退出
    
    # 强制止损
    stop_loss_pct: Optional[float] = -20.0  # 亏损超过-20%退出
    
    def __post_init__(self):
        if self.exit_signals is None:
            self.exit_signals = ['WATCH', 'EXIT']
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_v318_exit_signal": self.use_v318_exit_signal,
            "exit_signals": self.exit_signals,
            "exit_on_filter": self.exit_on_filter,
            "exit_on_no_entry": self.exit_on_no_entry,
            "max_holding_days": self.max_holding_days,
            "stop_loss_pct": self.stop_loss_pct,
        }


class ExitRulesEngine:
    """退出规则引擎"""
    
    def __init__(self, config: ExitConfig):
        self.config = config
    
    def should_exit(self,
                   code: str,
                   current_date: str,
                   open_date: str,
                   avg_cost: float,
                   current_price: float,
                   current_score: Optional[Dict[str, Any]]) -> tuple[bool, str]:
        """判断是否应该退出
        
        Args:
            code: 股票代码
            current_date: 当前日期
            open_date: 开仓日期
            avg_cost: 平均成本
            current_price: 当前价格
            current_score: 当前评分结果（可能为None，如停牌）
            
        Returns:
            (should_exit, reason)
        """
        # 1. 停牌无法退出
        if current_score is None:
            return (False, "suspended_cannot_exit")
        
        # 2. 基于V3.1.8 exit_signal
        if self.config.use_v318_exit_signal:
            exit_signal = current_score.get("_full_result", {}).get("exit_signal")
            if exit_signal in self.config.exit_signals:
                return (True, f"v318_exit_signal_{exit_signal}")
        
        # 3. TrendGrade降级到FILTER
        if self.config.exit_on_filter:
            grade = current_score.get("grade_v318")
            if grade == "FILTER":
                return (True, "grade_filter")
        
        # 4. EntryStatus变为NO_ENTRY（可选）
        if self.config.exit_on_no_entry:
            entry_status = current_score.get("entry_window_v318")
            if entry_status == "NO_ENTRY":
                return (True, "entry_no_entry")
        
        # 5. 最大持仓期
        if self.config.max_holding_days:
            open_dt = datetime.strptime(open_date, "%Y-%m-%d")
            current_dt = datetime.strptime(current_date, "%Y-%m-%d")
            holding_days = (current_dt - open_dt).days
            
            if holding_days > self.config.max_holding_days:
                return (True, f"max_holding_days_{holding_days}")
        
        # 6. 强制止损
        if self.config.stop_loss_pct:
            if avg_cost > 0:
                pnl_pct = (current_price / avg_cost - 1) * 100
                if pnl_pct < self.config.stop_loss_pct:
                    return (True, f"stop_loss_{pnl_pct:.1f}pct")
        
        # 不退出
        return (False, "hold")
    
    def batch_check_exits(self,
                         positions: Dict[str, Dict[str, Any]],
                         current_date: str,
                         current_scores: Dict[str, Dict[str, Any]]) -> list[tuple[str, str]]:
        """批量检查所有持仓是否应退出
        
        Args:
            positions: {code: {"open_date", "avg_cost", "current_price", ...}}
            current_date: 当前日期
            current_scores: {code: score_result}
            
        Returns:
            [(code, exit_reason), ...]
        """
        exits = []
        
        for code, pos in positions.items():
            current_score = current_scores.get(code)
            current_price = pos.get("current_price", pos["avg_cost"])
            
            should_exit, reason = self.should_exit(
                code=code,
                current_date=current_date,
                open_date=pos["open_date"],
                avg_cost=pos["avg_cost"],
                current_price=current_price,
                current_score=current_score
            )
            
            if should_exit:
                exits.append((code, reason))
        
        return exits


# 预定义配置

DEFAULT_EXIT_CONFIG = ExitConfig(
    use_v318_exit_signal=True,
    exit_signals=['WATCH', 'EXIT'],
    exit_on_filter=True,
    exit_on_no_entry=False,  # NO_ENTRY不一定退出
    max_holding_days=60,
    stop_loss_pct=-20.0,
)

CONSERVATIVE_EXIT_CONFIG = ExitConfig(
    use_v318_exit_signal=True,
    exit_signals=['WATCH', 'EXIT'],
    exit_on_filter=True,
    exit_on_no_entry=True,  # 更保守：NO_ENTRY也退出
    max_holding_days=30,
    stop_loss_pct=-15.0,
)

AGGRESSIVE_EXIT_CONFIG = ExitConfig(
    use_v318_exit_signal=True,
    exit_signals=['EXIT'],  # 仅EXIT才退出，WATCH继续持有
    exit_on_filter=True,
    exit_on_no_entry=False,
    max_holding_days=90,
    stop_loss_pct=-25.0,
)


if __name__ == "__main__":
    # 测试退出规则
    engine = ExitRulesEngine(DEFAULT_EXIT_CONFIG)
    
    print("退出规则测试:")
    print("=" * 80)
    print(f"配置: {DEFAULT_EXIT_CONFIG.to_dict()}")
    
    # 测试用例
    test_cases = [
        {
            "desc": "正常持有（A评级，持有10天）",
            "code": "600519",
            "current_date": "2024-01-15",
            "open_date": "2024-01-05",
            "avg_cost": 1600.0,
            "current_price": 1650.0,
            "current_score": {
                "grade_v318": "A",
                "entry_window_v318": "CONFIRMED",
                "_full_result": {"exit_signal": "HOLD"}
            }
        },
        {
            "desc": "exit_signal触发（WATCH）",
            "code": "600519",
            "current_date": "2024-01-15",
            "open_date": "2024-01-05",
            "avg_cost": 1600.0,
            "current_price": 1650.0,
            "current_score": {
                "grade_v318": "A",
                "entry_window_v318": "FOLLOW",
                "_full_result": {"exit_signal": "WATCH"}
            }
        },
        {
            "desc": "Grade降级到FILTER",
            "code": "600519",
            "current_date": "2024-01-15",
            "open_date": "2024-01-05",
            "avg_cost": 1600.0,
            "current_price": 1620.0,
            "current_score": {
                "grade_v318": "FILTER",
                "entry_window_v318": "NO_ENTRY",
                "_full_result": {"exit_signal": "HOLD"}
            }
        },
        {
            "desc": "最大持仓期（61天）",
            "code": "600519",
            "current_date": "2024-03-10",
            "open_date": "2024-01-05",
            "avg_cost": 1600.0,
            "current_price": 1700.0,
            "current_score": {
                "grade_v318": "A",
                "entry_window_v318": "CONFIRMED",
                "_full_result": {"exit_signal": "HOLD"}
            }
        },
        {
            "desc": "止损（-21%）",
            "code": "600519",
            "current_date": "2024-01-15",
            "open_date": "2024-01-05",
            "avg_cost": 1600.0,
            "current_price": 1264.0,  # -21%
            "current_score": {
                "grade_v318": "B",
                "entry_window_v318": "FOLLOW",
                "_full_result": {"exit_signal": "HOLD"}
            }
        },
        {
            "desc": "停牌无法退出",
            "code": "600519",
            "current_date": "2024-01-15",
            "open_date": "2024-01-05",
            "avg_cost": 1600.0,
            "current_price": 1600.0,
            "current_score": None  # 停牌
        },
    ]
    
    for test in test_cases:
        print(f"\n{test['desc']}:")
        should_exit, reason = engine.should_exit(
            code=test['code'],
            current_date=test['current_date'],
            open_date=test['open_date'],
            avg_cost=test['avg_cost'],
            current_price=test['current_price'],
            current_score=test['current_score']
        )
        print(f"  退出: {should_exit}, 原因: {reason}")
