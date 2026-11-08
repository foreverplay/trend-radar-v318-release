"""仓位管理器 - 固定最大持仓、确定性排序、现金/仓位上限

集中配置，确定性排序（不随机），支持:
- Equal-weight 等权
- 排序加权（按评分/连续性）
- 同日多候选竞争（确定性选择）
- 现金不足拒单
- 100股整数倍（零股卖出允许）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum


class PositionSizingMode(Enum):
    """仓位分配模式"""
    EQUAL_WEIGHT = "equal_weight"  # 等权
    SCORE_WEIGHTED = "score_weighted"  # 按评分加权
    CONTINUITY_WEIGHTED = "continuity_weighted"  # 按连续性加权


@dataclass
class PositionConfig:
    """仓位配置"""
    
    initial_capital: float = 1000000  # 初始资金
    max_positions: int = 10  # 最多持仓数
    single_position_limit: float = 0.2  # 单只最多20%
    reserve_cash_rate: float = 0.05  # 保留5%现金
    
    sizing_mode: PositionSizingMode = PositionSizingMode.EQUAL_WEIGHT
    
    # 排序规则（确定性）
    candidate_sort_key: str = "structure_score"  # 排序字段
    candidate_sort_desc: bool = True  # 降序
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_capital": self.initial_capital,
            "max_positions": self.max_positions,
            "single_position_limit": self.single_position_limit,
            "reserve_cash_rate": self.reserve_cash_rate,
            "sizing_mode": self.sizing_mode.value,
            "candidate_sort_key": self.candidate_sort_key,
            "candidate_sort_desc": self.candidate_sort_desc,
        }


class PositionManager:
    """仓位管理器"""
    
    def __init__(self, config: PositionConfig):
        self.config = config
    
    def select_candidates(self,
                         candidates: Dict[str, Dict[str, Any]],
                         current_positions: List[str],
                         n_slots: int) -> List[str]:
        """从候选中选择买入目标（确定性排序）
        
        Args:
            candidates: {code: score_result}
            current_positions: 当前持仓代码列表
            n_slots: 可用仓位数
            
        Returns:
            选中的代码列表（确定性排序）
        """
        if n_slots <= 0:
            return []
        
        # 排除已持仓
        available = {k: v for k, v in candidates.items() if k not in current_positions}
        
        if not available:
            return []
        
        # 确定性排序
        sort_key = self.config.candidate_sort_key
        
        # 构建排序元组（评分值, 代码）
        items = []
        for code, score in available.items():
            # 获取排序字段值
            if sort_key in score:
                value = score[sort_key]
            elif sort_key in score.get("_full_result", {}):
                value = score["_full_result"][sort_key]
            else:
                value = 0.0  # 默认值
            
            items.append((value if value is not None else 0.0, code))
        
        # 排序（确定性）
        items.sort(reverse=self.config.candidate_sort_desc)
        
        # 取前N个
        selected = [code for _, code in items[:n_slots]]
        
        return selected
    
    def calculate_position_sizes(self,
                                codes: List[str],
                                candidates: Dict[str, Dict[str, Any]],
                                available_cash: float,
                                current_prices: Dict[str, float]) -> Dict[str, int]:
        """计算买入数量（确定性）
        
        Args:
            codes: 要买入的代码列表
            candidates: {code: score_result}
            available_cash: 可用现金
            current_prices: {code: price}
            
        Returns:
            {code: quantity} 买入数量（100股整数倍）
        """
        if not codes:
            return {}
        
        # 保留现金
        max_invest = available_cash * (1 - self.config.reserve_cash_rate)
        
        if self.config.sizing_mode == PositionSizingMode.EQUAL_WEIGHT:
            # 等权配置
            target_value_per_stock = max_invest / len(codes)
            
            # 限制单只仓位
            max_single_value = available_cash * self.config.single_position_limit
            target_value_per_stock = min(target_value_per_stock, max_single_value)
            
            sizes = {}
            for code in codes:
                price = current_prices.get(code)
                if not price or price <= 0:
                    continue
                
                # 计算数量（100股整数倍）
                quantity = int(target_value_per_stock / price / 100) * 100
                
                if quantity >= 100:
                    sizes[code] = quantity
            
            return sizes
        
        elif self.config.sizing_mode == PositionSizingMode.SCORE_WEIGHTED:
            # 按评分加权
            scores = []
            for code in codes:
                score_val = candidates[code].get("trend_structure_score", 50.0)
                scores.append(max(score_val, 0.0))
            
            total_score = sum(scores)
            if total_score == 0:
                # 回退到等权
                return self.calculate_position_sizes(
                    codes, candidates, available_cash, current_prices
                )
            
            sizes = {}
            for code, score in zip(codes, scores):
                weight = score / total_score
                target_value = max_invest * weight
                
                # 限制单只仓位
                max_single_value = available_cash * self.config.single_position_limit
                target_value = min(target_value, max_single_value)
                
                price = current_prices.get(code)
                if not price or price <= 0:
                    continue
                
                quantity = int(target_value / price / 100) * 100
                
                if quantity >= 100:
                    sizes[code] = quantity
            
            return sizes
        
        else:
            # 其他模式暂不实现
            return self.calculate_position_sizes(
                codes, candidates, available_cash, current_prices
            )
    
    def get_available_slots(self, current_positions_count: int) -> int:
        """获取可用仓位数"""
        return max(0, self.config.max_positions - current_positions_count)


# 预定义配置

DEFAULT_POSITION_CONFIG = PositionConfig(
    initial_capital=1000000,
    max_positions=10,
    single_position_limit=0.2,
    reserve_cash_rate=0.05,
    sizing_mode=PositionSizingMode.EQUAL_WEIGHT,
    candidate_sort_key="trend_structure_score",
    candidate_sort_desc=True,
)

CONSERVATIVE_POSITION_CONFIG = PositionConfig(
    initial_capital=1000000,
    max_positions=5,  # 更集中
    single_position_limit=0.15,  # 更分散
    reserve_cash_rate=0.1,  # 更多现金
    sizing_mode=PositionSizingMode.EQUAL_WEIGHT,
    candidate_sort_key="continuity_score",
    candidate_sort_desc=True,
)

AGGRESSIVE_POSITION_CONFIG = PositionConfig(
    initial_capital=1000000,
    max_positions=15,  # 更分散
    single_position_limit=0.3,  # 可更集中
    reserve_cash_rate=0.02,  # 更少现金
    sizing_mode=PositionSizingMode.SCORE_WEIGHTED,
    candidate_sort_key="trend_structure_score",
    candidate_sort_desc=True,
)


if __name__ == "__main__":
    # 测试仓位管理
    manager = PositionManager(DEFAULT_POSITION_CONFIG)
    
    print("仓位管理测试:")
    print("=" * 80)
    print(f"配置: {DEFAULT_POSITION_CONFIG.to_dict()}")
    
    # 模拟候选
    candidates = {
        "600519": {"trend_structure_score": 92.5, "continuity_score": 85.3},
        "600036": {"trend_structure_score": 88.2, "continuity_score": 82.1},
        "000858": {"trend_structure_score": 86.0, "continuity_score": 80.5},
        "601318": {"trend_structure_score": 84.5, "continuity_score": 78.9},
        "300750": {"trend_structure_score": 90.0, "continuity_score": 87.2},
    }
    
    current_positions = []
    available_cash = 1000000
    current_prices = {
        "600519": 1650.0,
        "600036": 45.5,
        "000858": 125.3,
        "601318": 52.8,
        "300750": 255.6,
    }
    
    # Step 1: 选择候选（最多10个，当前0个持仓）
    n_slots = manager.get_available_slots(len(current_positions))
    print(f"\n可用仓位数: {n_slots}")
    
    selected = manager.select_candidates(candidates, current_positions, n_slots)
    print(f"选中候选（按structure_score降序）: {selected}")
    
    # Step 2: 计算买入数量
    sizes = manager.calculate_position_sizes(
        codes=selected,
        candidates=candidates,
        available_cash=available_cash,
        current_prices=current_prices
    )
    
    print(f"\n买入计划:")
    total_value = 0
    for code in selected:
        if code in sizes:
            quantity = sizes[code]
            price = current_prices[code]
            value = quantity * price
            total_value += value
            print(f"  {code}: {quantity}股 @ {price:.2f} = {value:,.0f}元")
    
    print(f"\n总投入: {total_value:,.0f}元")
    print(f"剩余现金: {available_cash - total_value:,.0f}元")
    print(f"现金比例: {(available_cash - total_value) / available_cash * 100:.1f}%")
