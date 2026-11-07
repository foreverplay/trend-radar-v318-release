"""研究实验室评分引擎 - 调用完整V3.1.8生产评分链

确保:
1. 使用真正的 v3.grading_v318.grade_trend_v318()
2. 调用生产依赖指标计算（v2_structure, v2_continuity, v2_sector等）
3. 严格 as-of 时间截断，HTF不看未来完整周期
4. 不使用简化评分、代理或当前榜单倒推
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import pandas as pd

# 添加父目录到路径以导入生产模块
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# 导入生产评分模块
from v3.grading_v318 import grade_trend_v318, GradeResultV318
from v3.v31.entry_window import classify_entry_window, apply_v318_entry_overlay, EntryWindow
from v3.v31.rapid_detection import detect_rapid_main_rise
from v3.v31.live_v318 import evaluate_v318_asof, load_hist_to_t, make_builder, DEFAULT_WARMUP

# 导入研究数据提供者
from data_provider import ReadOnlyDataProvider


class V318ScoringEngine:
    """V3.1.8完整评分引擎包装器
    
    调用生产评分链，确保历史 as-of 正确性
    """
    
    def __init__(self, data_provider: ReadOnlyDataProvider):
        self.provider = data_provider
        self._warmup_days = DEFAULT_WARMUP  # 约250交易日
    
    def score_stock_asof(self,
                        code: str,
                        as_of_date: str,
                        sector_score: Optional[float] = None,
                        tech_score: Optional[float] = None,
                        fund_risk_level: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """对单只股票进行 as-of 评分
        
        调用 v3.v31.live_v318.evaluate_v318_asof() 生产评分链
        
        Args:
            code: 6位股票代码
            as_of_date: 截止日期 YYYY-MM-DD
            sector_score: 板块评分（可选，如果无法计算板块）
            tech_score: 技术评分（可选）
            fund_risk_level: 资金风险等级（可选）
            
        Returns:
            评分结果字典，包含:
            - grade_v318: TrendGrade (A+, A, B, C, FILTER)
            - entry_window_v318: EntryStatus (PRIME, CONFIRMED, FOLLOW, NO_ENTRY)
            - trend_structure_score, continuity_score, sector_score, tech_score
            - rapid_main_rise, trend_state, maturity_level, exhaustion
            - 其他诊断信息
            
            如果数据不足或计算失败返回 None
        """
        code6 = str(code).zfill(6)
        
        try:
            # 使用生产评分入口
            result = evaluate_v318_asof(
                symbol=code6,
                t_date=as_of_date,
                sector_score=sector_score,
                tech_score=tech_score,
                fund_risk_level=fund_risk_level,
                # 注意: 内部会调用 load_hist_to_t() 和 rank_db
                # 我们需要确保 rank_db.db_path() 指向正确的只读数据库
            )
            
            if result is None:
                return None
            
            # 提取关键字段
            return {
                # 核心评分
                "grade_v318": result.get("grade_v318"),
                "grade_path": result.get("grade_v318_path"),
                "entry_window_v318": result.get("entry_window_v318"),
                "entry_window_v317": result.get("entry_window_v317"),
                
                # 各维度分数
                "trend_structure_score": result.get("trend_structure_score"),
                "structure_score": result.get("structure_score"),
                "structure_source": result.get("structure_source"),
                "continuity_score": result.get("continuity_score"),
                "sector_score": result.get("sector") or sector_score,
                "tech_score": result.get("tech") or tech_score,
                
                # 趋势状态
                "trend_state": result.get("state"),
                "maturity_level": result.get("maturity_level"),
                "exhaustion": result.get("exhaustion"),
                "broken": result.get("broken"),
                
                # RAPID检测
                "rapid_main_rise": result.get("rapid_main_rise"),
                "rapid": result.get("rapid"),
                
                # HTF
                "htf_ok": result.get("htf_ok"),
                "monthly_state": result.get("monthly_state"),
                
                # 诊断信息
                "grade_reasons": result.get("grade_v318_reasons"),
                "grade_tags": result.get("grade_v318_tags"),
                "entry_notes": result.get("entry_window_notes_v318"),
                
                # 原始完整结果（供诊断）
                "_full_result": result,
            }
            
        except Exception as e:
            print(f"❌ 评分失败 {code6} @ {as_of_date}: {e}")
            return None
    
    # REMOVED: score_stock_asof_fallback()
    # 禁止使用简化fallback评分，必须使用真实V3.1.8评分链
    # 如果evaluate_v318_asof()失败，应该明确失败/跳过为不可计算
    
    def batch_score_universe(self,
                            codes: list[str],
                            as_of_date: str) -> Dict[str, Dict[str, Any]]:
        """批量评分股票池（仅真实V3.1.8评分）
        
        Args:
            codes: 股票代码列表
            as_of_date: 截止日期
            
        Returns:
            {code: score_result, ...}
            仅返回成功评分的股票，失败的跳过（不伪造A评级）
        """
        results = {}
        failed = []
        
        for i, code in enumerate(codes):
            if (i + 1) % 100 == 0:
                print(f"  评分进度: {i + 1}/{len(codes)}")
            
            try:
                result = self.score_stock_asof(code, as_of_date)
                if result:
                    results[code] = result
                else:
                    failed.append((code, "no_result"))
            except Exception as e:
                failed.append((code, str(e)))
        
        if failed:
            print(f"  ⚠️  评分失败/跳过: {len(failed)} 只股票")
            # 记录前10个失败
            for code, reason in failed[:10]:
                print(f"     {code}: {reason}")
        
        return results
    
    def get_a_grade_stocks(self,
                          scores: Dict[str, Dict[str, Any]],
                          include_a_plus: bool = True,
                          include_a: bool = True) -> Dict[str, Dict[str, Any]]:
        """筛选A评级股票
        
        Args:
            scores: batch_score_universe() 返回的评分结果
            include_a_plus: 是否包含A+
            include_a: 是否包含A
            
        Returns:
            {code: score_result, ...} 仅A评级
        """
        grades = []
        if include_a_plus:
            grades.append("A+")
        if include_a:
            grades.append("A")
        
        return {
            code: result
            for code, result in scores.items()
            if result.get("grade_v318") in grades
        }
    
    def get_entry_candidates(self,
                            scores: Dict[str, Dict[str, Any]],
                            entry_statuses: Optional[list[str]] = None) -> Dict[str, Dict[str, Any]]:
        """筛选入场候选
        
        Args:
            scores: 评分结果
            entry_statuses: 允许的EntryStatus列表，默认 ['PRIME', 'CONFIRMED']
            
        Returns:
            {code: score_result, ...} 满足入场条件
        """
        if entry_statuses is None:
            entry_statuses = ['PRIME', 'CONFIRMED']
        
        return {
            code: result
            for code, result in scores.items()
            if result.get("entry_window_v318") in entry_statuses
        }


def setup_production_data_paths(config):
    """配置生产模块使用的数据路径
    
    确保 rank_db.py, paths.py 等使用研究配置的数据源
    
    ⚠️ 这会临时修改环境变量，需要在进程启动时调用
    """
    import os
    
    if config.sqlite_path:
        os.environ["TREND_RANK_DB"] = str(config.sqlite_path)
        os.environ["MARKET_DB_PATH"] = str(config.sqlite_path)
    
    if config.parquet_root:
        os.environ["ASHARE_KLINE_ROOT"] = str(config.parquet_root)
    
    print("✅ 已配置生产模块数据路径:")
    print(f"   TREND_RANK_DB={os.environ.get('TREND_RANK_DB')}")
    print(f"   ASHARE_KLINE_ROOT={os.environ.get('ASHARE_KLINE_ROOT')}")


if __name__ == "__main__":
    from research_config import create_default_config
    
    # 创建配置和数据提供者
    config = create_default_config()
    provider = ReadOnlyDataProvider(config)
    
    # 配置生产模块路径
    setup_production_data_paths(config)
    
    # 创建评分引擎
    engine = V318ScoringEngine(provider)
    
    # 测试评分（仅真实V3.1.8）
    print("\n测试评分引擎（真实V3.1.8评分链）:")
    print("=" * 80)
    
    test_date = "2024-12-31"
    test_code = "600519"  # 贵州茅台
    
    print(f"\n评分 {test_code} @ {test_date}:")
    try:
        result = engine.score_stock_asof(test_code, test_date)
        
        if result:
            print(f"  ✅ TrendGrade: {result['grade_v318']}")
            print(f"  Grade Path: {result['grade_path']}")
            print(f"  EntryStatus: {result['entry_window_v318']}")
            print(f"  Structure Score: {result.get('trend_structure_score')}")
            print(f"  Continuity Score: {result.get('continuity_score')}")
            print(f"  RAPID: {result.get('rapid_main_rise')}")
        else:
            print("  ❌ 评分返回None（数据不足或不可计算）")
    except Exception as e:
        print(f"  ❌ 评分失败: {e}")
        import traceback
        traceback.print_exc()
    
    provider.close()
