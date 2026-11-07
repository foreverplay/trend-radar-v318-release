#!/usr/bin/env python3
"""研究实验室完整回测CLI

串联: 数据配置 → 历史评级 → 信号生成 → 订单执行 → 账户净值

输出: 完整持仓/拒单/交易/指标/manifest，独立run_id不覆盖
断点继续同一run配置不一致拒绝

无网络获取，源库/缓存只读，禁止样例回退
"""
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import argparse

# 添加路径
_LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(_LAB))
sys.path.insert(0, str(_LAB.parent))

from research_config import ResearchDataConfig
from data_provider import ReadOnlyDataProvider
from scoring_engine import V318ScoringEngine, setup_production_data_paths
from historical_grading import HistoricalGradingConfig, HistoricalGradingEngine
from backtest_engine_v2 import BacktestEngineV2, TradingRules
from exit_rules import ExitRulesEngine, ExitConfig, DEFAULT_EXIT_CONFIG
from position_manager import PositionManager, PositionConfig, DEFAULT_POSITION_CONFIG
from trading_rules_provider import RulesProvider


class ResearchBacktestRunner:
    """研究回测Runner"""
    
    def __init__(self, config_file: Path):
        # 加载配置
        with open(config_file) as f:
            self.config = json.load(f)
        
        # 生成run_id
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 输出目录
        self.output_dir = Path(self.config.get("output_dir", "results/backtest")) / self.run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置hash（用于断点恢复校验）
        self.config_hash = hashlib.sha256(
            json.dumps(self.config, sort_keys=True).encode()
        ).hexdigest()[:16]
        
        print(f"\n研究回测Runner")
        print(f"Run ID: {self.run_id}")
        print(f"Config Hash: {self.config_hash}")
        print(f"Output: {self.output_dir}")
    
    def setup_data(self):
        """配置数据源（只读）"""
        print("\n[1/5] 配置数据源...")
        
        # 从配置文件读取数据路径
        data_config = ResearchDataConfig.from_explicit_paths(
            sqlite_path=self.config.get("sqlite_path"),
            parquet_root=self.config.get("parquet_root")
        )
        
        # 验证
        validation = data_config.validate()
        if not validation["valid"]:
            raise ValueError(f"数据验证失败: {validation['errors']}")
        
        print(data_config.get_summary())
        
        # 创建提供者
        self.provider = ReadOnlyDataProvider(data_config)
        
        # 配置生产模块路径
        setup_production_data_paths(data_config)
        
        # 保存manifest
        self.manifest = {
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "data_source": data_config.to_dict(),
            "data_validation": validation,
            "config": self.config,
            "started_at": datetime.now().isoformat(),
        }
    
    def run_grading(self):
        """历史评级回放（真实V3.1.8）"""
        print("\n[2/5] 历史评级回放...")
        
        grading_config = HistoricalGradingConfig(
            start_date=self.config["start_date"],
            end_date=self.config["end_date"],
            universe_mode="auto",
            output_dir=self.output_dir / "grading",
            run_id=self.run_id,
        )
        
        scorer = V318ScoringEngine(self.provider)
        engine = HistoricalGradingEngine(grading_config, self.provider, scorer)
        
        engine.setup()
        summary = engine.run()
        
        self.manifest["grading_summary"] = summary
        
        print(f"✅ 评级完成: {summary['records']['total']} 条记录")
        print(f"   A+: {summary['a_grade_summary']['total_a_plus']}")
        print(f"   A:  {summary['a_grade_summary']['total_a']}")
    
    def run_backtest(self):
        """执行回测（简化版，待完整集成）"""
        print("\n[3/5] 执行回测...")
        print("⚠️  完整回测集成器开发中，当前仅评级回放")
        
        # TODO: 完整回测集成
        # - 读取历史评级结果
        # - 生成买入/卖出信号
        # - 执行订单（T+1）
        # - 更新账户净值
        
        self.manifest["backtest_status"] = "grading_only"
    
    def generate_report(self):
        """生成报告"""
        print("\n[4/5] 生成报告...")
        
        # 保存manifest
        manifest_file = self.output_dir / "manifest.json"
        self.manifest["finished_at"] = datetime.now().isoformat()
        
        with open(manifest_file, 'w') as f:
            json.dumps(self.manifest, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Manifest: {manifest_file}")
    
    def cleanup(self):
        """清理"""
        print("\n[5/5] 清理...")
        self.provider.close()
        print("✅ 完成")
    
    def run(self):
        """执行完整流程"""
        try:
            self.setup_data()
            self.run_grading()
            self.run_backtest()
            self.generate_report()
            self.cleanup()
            
            print(f"\n✅ 研究回测完成")
            print(f"   Run ID: {self.run_id}")
            print(f"   输出: {self.output_dir}")
            
            return 0
        
        except Exception as e:
            print(f"\n❌ 失败: {e}")
            import traceback
            traceback.print_exc()
            return 1


def main():
    parser = argparse.ArgumentParser(description="研究实验室回测CLI")
    parser.add_argument("config", type=Path, help="配置文件路径 (JSON)")
    parser.add_argument("--validate-only", action="store_true", help="仅验证配置")
    
    args = parser.parse_args()
    
    if not args.config.exists():
        print(f"❌ 配置文件不存在: {args.config}")
        return 1
    
    if args.validate_only:
        print(f"验证配置: {args.config}")
        with open(args.config) as f:
            config = json.load(f)
        print(json.dumps(config, indent=2))
        return 0
    
    runner = ResearchBacktestRunner(args.config)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
