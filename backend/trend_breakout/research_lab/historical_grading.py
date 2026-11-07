"""历史评级回放器 - 按日迭代重算历史A评级池

确保:
1. 每个历史日只用截至该日数据
2. TrendGrade A/A+分别存储展示
3. EntryStatus独立保留
4. 记录数据缺口和不可算股票
5. 支持断点恢复和进度记录
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import pandas as pd
from dataclasses import dataclass, field

from data_provider import ReadOnlyDataProvider
from scoring_engine import V318ScoringEngine


@dataclass
class HistoricalGradingConfig:
    """历史评级配置"""
    
    # 日期范围
    start_date: str
    end_date: str
    
    # 股票池
    universe_mode: str = "auto"  # auto, explicit, file
    explicit_universe: List[str] = field(default_factory=list)
    universe_file: Optional[Path] = None
    
    # Warmup
    warmup_days: int = 250  # 约1年交易日
    
    # 评分选项（已移除fallback）
    # 仅使用真实V3.1.8评分链，失败的跳过为不可计算
    
    # 输出
    output_dir: Path = Path("results/historical_grading")
    run_id: Optional[str] = None
    
    # 进度控制
    checkpoint_interval: int = 10  # 每N天保存一次检查点
    resume_from_checkpoint: bool = True
    
    def __post_init__(self):
        if self.run_id is None:
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "universe_mode": self.universe_mode,
            "warmup_days": self.warmup_days,
            "use_fallback_scoring": self.use_fallback_scoring,
            "run_id": self.run_id,
        }


@dataclass
class DailyGradeRecord:
    """单日评级记录"""
    
    trade_date: str
    code: str
    
    # 核心评级
    grade: str  # A+, A, B, C, FILTER
    entry_status: str  # PRIME, CONFIRMED, FOLLOW, NO_ENTRY
    
    # 分数
    trend_structure_score: Optional[float] = None
    continuity_score: Optional[float] = None
    sector_score: Optional[float] = None
    tech_score: Optional[float] = None
    
    # 状态
    trend_state: Optional[str] = None
    maturity_level: Optional[str] = None
    rapid_main_rise: bool = False
    
    # 诊断
    data_source: str = "unknown"
    scoring_mode: str = "production"  # production, fallback
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "code": self.code,
            "grade": self.grade,
            "entry_status": self.entry_status,
            "trend_structure_score": self.trend_structure_score,
            "continuity_score": self.continuity_score,
            "sector_score": self.sector_score,
            "tech_score": self.tech_score,
            "trend_state": self.trend_state,
            "maturity_level": self.maturity_level,
            "rapid_main_rise": self.rapid_main_rise,
            "data_source": self.data_source,
            "scoring_mode": self.scoring_mode,
            "warnings": self.warnings,
        }


class HistoricalGradingEngine:
    """历史评级引擎"""
    
    def __init__(self,
                 config: HistoricalGradingConfig,
                 data_provider: ReadOnlyDataProvider,
                 scoring_engine: V318ScoringEngine):
        self.config = config
        self.provider = data_provider
        self.scorer = scoring_engine
        
        self.trading_dates: List[str] = []
        self.universe: List[str] = []
        self.records: List[DailyGradeRecord] = []
        
        self.checkpoint_file = self.config.output_dir / f"checkpoint_{config.run_id}.json"
        self.progress = {
            "completed_dates": [],
            "failed_dates": {},
            "last_checkpoint": None,
        }
    
    def setup(self):
        """初始化：获取交易日历和股票池"""
        print("\n初始化历史评级引擎...")
        print("=" * 80)
        
        # 1. 获取交易日历
        print(f"获取交易日历: {self.config.start_date} to {self.config.end_date}")
        self.trading_dates = self.provider.get_trading_dates(
            start_date=self.config.start_date,
            end_date=self.config.end_date
        )
        print(f"  找到 {len(self.trading_dates)} 个交易日")
        
        if not self.trading_dates:
            raise ValueError("未找到交易日，请检查数据源和日期范围")
        
        # 2. 获取股票池
        print(f"获取股票池: mode={self.config.universe_mode}")
        
        if self.config.universe_mode == "explicit":
            self.universe = self.config.explicit_universe
        
        elif self.config.universe_mode == "file":
            if self.config.universe_file and self.config.universe_file.exists():
                with open(self.config.universe_file) as f:
                    self.universe = [line.strip() for line in f if line.strip()]
            else:
                raise ValueError(f"股票池文件不存在: {self.config.universe_file}")
        
        else:  # auto
            # 使用全部有数据的股票（⚠️ 幸存者偏差）
            self.universe = self.provider.get_all_universe()
            print(f"  ⚠️  使用全量股票池（包含幸存者偏差）: {len(self.universe)} 只")
        
        print(f"  股票池大小: {len(self.universe)}")
        
        # 3. 加载检查点
        if self.config.resume_from_checkpoint and self.checkpoint_file.exists():
            self._load_checkpoint()
    
    def _load_checkpoint(self):
        """加载检查点"""
        try:
            with open(self.checkpoint_file) as f:
                self.progress = json.load(f)
            
            print(f"\n✅ 加载检查点: {len(self.progress['completed_dates'])} 日已完成")
            print(f"   最后检查点: {self.progress.get('last_checkpoint')}")
            
        except Exception as e:
            print(f"⚠️ 加载检查点失败: {e}")
    
    def _save_checkpoint(self):
        """保存检查点"""
        self.progress["last_checkpoint"] = datetime.now().isoformat()
        
        with open(self.checkpoint_file, 'w') as f:
            json.dump(self.progress, f, indent=2)
    
    def run(self) -> Dict[str, Any]:
        """执行历史评级回放"""
        print("\n开始历史评级回放...")
        print("=" * 80)
        
        start_time = datetime.now()
        
        # 跳过已完成的日期
        completed_set = set(self.progress["completed_dates"])
        pending_dates = [d for d in self.trading_dates if d not in completed_set]
        
        print(f"总交易日: {len(self.trading_dates)}")
        print(f"已完成: {len(completed_set)}")
        print(f"待处理: {len(pending_dates)}")
        
        if not pending_dates:
            print("\n✅ 所有日期已完成")
            return self._generate_summary()
        
        # 逐日评分
        for i, trade_date in enumerate(pending_dates):
            print(f"\n[{i+1}/{len(pending_dates)}] 评分日期: {trade_date}")
            
            try:
                daily_records = self._score_one_date(trade_date)
                self.records.extend(daily_records)
                
                # 记录完成
                self.progress["completed_dates"].append(trade_date)
                
                # 保存检查点
                if (i + 1) % self.config.checkpoint_interval == 0:
                    self._save_checkpoint()
                    self._save_interim_results()
                
            except Exception as e:
                print(f"❌ 日期 {trade_date} 失败: {e}")
                self.progress["failed_dates"][trade_date] = str(e)
        
        # 最终保存
        self._save_checkpoint()
        summary = self._generate_summary()
        self._save_final_results(summary)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n历史评级完成，耗时: {elapsed:.1f}秒")
        
        return summary
    
    def _score_one_date(self, trade_date: str) -> List[DailyGradeRecord]:
        """评分单日所有股票（仅真实V3.1.8评分）"""
        daily_records = []
        
        # 批量评分（失败的跳过）
        scores = self.scorer.batch_score_universe(
            codes=self.universe,
            as_of_date=trade_date
        )
        
        print(f"  成功评分: {len(scores)}/{len(self.universe)} 只股票")
        
        # 转换为记录
        for code, result in scores.items():
            record = DailyGradeRecord(
                trade_date=trade_date,
                code=code,
                grade=result.get("grade_v318", "UNKNOWN"),
                entry_status=result.get("entry_window_v318", "NO_ENTRY"),
                trend_structure_score=result.get("trend_structure_score"),
                continuity_score=result.get("continuity_score"),
                sector_score=result.get("sector_score"),
                tech_score=result.get("tech_score"),
                trend_state=result.get("trend_state"),
                maturity_level=result.get("maturity_level"),
                rapid_main_rise=result.get("rapid_main_rise", False),
                data_source=result.get("structure_source", "unknown"),
                scoring_mode="production",  # 仅生产评分
                warnings=[],  # 无fallback警告
            )
            
            daily_records.append(record)
        
        # 统计A评级
        a_plus = [r for r in daily_records if r.grade == "A+"]
        a_grade = [r for r in daily_records if r.grade == "A"]
        
        print(f"  A+ 评级: {len(a_plus)} 只")
        print(f"  A  评级: {len(a_grade)} 只")
        
        return daily_records
    
    def _save_interim_results(self):
        """保存中间结果"""
        output_file = self.config.output_dir / f"interim_{self.config.run_id}.parquet"
        
        df = pd.DataFrame([r.to_dict() for r in self.records])
        df.to_parquet(output_file, index=False)
        
        print(f"  💾 保存中间结果: {len(self.records)} 条记录 → {output_file}")
    
    def _generate_summary(self) -> Dict[str, Any]:
        """生成汇总统计"""
        df = pd.DataFrame([r.to_dict() for r in self.records])
        
        if df.empty:
            return {
                "run_id": self.config.run_id,
                "config": self.config.to_dict(),
                "n_dates": 0,
                "n_records": 0,
            }
        
        # 按日期统计
        date_stats = df.groupby('trade_date').agg({
            'code': 'count',
            'grade': lambda x: (x == 'A+').sum(),
        }).rename(columns={'code': 'n_scored', 'grade': 'n_a_plus'})
        
        date_stats['n_a'] = df[df['grade'] == 'A'].groupby('trade_date').size()
        
        summary = {
            "run_id": self.config.run_id,
            "config": self.config.to_dict(),
            "execution_time": datetime.now().isoformat(),
            
            "date_range": {
                "start": self.config.start_date,
                "end": self.config.end_date,
                "n_dates": len(self.trading_dates),
                "completed": len(self.progress["completed_dates"]),
                "failed": len(self.progress["failed_dates"]),
            },
            
            "universe": {
                "n_stocks": len(self.universe),
                "survivorship_bias_warning": self.config.universe_mode == "auto",
            },
            
            "records": {
                "total": len(self.records),
                "by_grade": df['grade'].value_counts().to_dict(),
                "by_entry_status": df['entry_status'].value_counts().to_dict(),
            },
            
            "a_grade_summary": {
                "total_a_plus": len(df[df['grade'] == 'A+']),
                "total_a": len(df[df['grade'] == 'A']),
                "avg_a_plus_per_day": date_stats['n_a_plus'].mean(),
                "avg_a_per_day": date_stats['n_a'].mean(),
            },
            
            "data_quality": {
                "scoring_modes": df['scoring_mode'].value_counts().to_dict(),
                "with_warnings": len(df[df['warnings'].apply(len) > 0]),
            }
        }
        
        return summary
    
    def _save_final_results(self, summary: Dict[str, Any]):
        """保存最终结果"""
        # 1. 保存完整记录（Parquet）
        records_file = self.config.output_dir / f"grades_{self.config.run_id}.parquet"
        df = pd.DataFrame([r.to_dict() for r in self.records])
        df.to_parquet(records_file, index=False)
        print(f"\n💾 保存评级记录: {records_file}")
        
        # 2. 保存汇总（JSON）
        summary_file = self.config.output_dir / f"summary_{self.config.run_id}.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"💾 保存汇总报告: {summary_file}")
        
        # 3. 保存每日A评级列表（CSV）
        df_a = df[df['grade'].isin(['A+', 'A'])].copy()
        if not df_a.empty:
            a_list_file = self.config.output_dir / f"a_grades_{self.config.run_id}.csv"
            df_a[['trade_date', 'code', 'grade', 'entry_status']].to_csv(a_list_file, index=False)
            print(f"💾 保存A评级列表: {a_list_file}")


def run_historical_grading_for_year(year: int, 
                                     data_provider: ReadOnlyDataProvider,
                                     output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """便捷函数：回放指定年份的历史评级（仅真实V3.1.8评分）
    
    Args:
        year: 年份（如2024）
        data_provider: 数据提供者
        output_dir: 输出目录
        
    Returns:
        汇总统计
    """
    from scoring_engine import V318ScoringEngine
    
    config = HistoricalGradingConfig(
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31",
        universe_mode="auto",
        output_dir=output_dir or Path(f"results/historical_grading/{year}"),
    )
    
    scorer = V318ScoringEngine(data_provider)
    engine = HistoricalGradingEngine(config, data_provider, scorer)
    
    engine.setup()
    return engine.run()


if __name__ == "__main__":
    from research_config import create_default_config
    
    # 创建配置和提供者
    config = create_default_config()
    provider = ReadOnlyDataProvider(config)
    
    # 运行2024年历史评级
    print("\n测试：回放2024年历史A评级")
    summary = run_historical_grading_for_year(2024, provider)
    
    print("\n汇总结果:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    
    provider.close()
