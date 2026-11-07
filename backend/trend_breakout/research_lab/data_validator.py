"""数据质量检查工具 — Phase 1

检测未来函数、缺失值、异常值，生成数据质量报告
"""
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import json

from config import DATA_QUALITY_THRESHOLDS


class DataValidator:
    """数据质量验证器"""
    
    def __init__(self, thresholds: Optional[Dict] = None):
        self.thresholds = thresholds or DATA_QUALITY_THRESHOLDS
        self.issues = []
        
    def validate_kline(self, df: pd.DataFrame, code: str) -> Dict:
        """验证 K线数据质量
        
        Args:
            df: K线数据 (需包含 date, open, high, low, close, volume 列)
            code: 股票代码
            
        Returns:
            验证结果字典
        """
        issues = []
        warnings = []
        
        # 1. 必须列检查
        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            issues.append(f"缺少必须列: {missing_cols}")
            return {"code": code, "valid": False, "issues": issues, "warnings": warnings}
        
        # 2. 缺失值检查
        for col in required_cols:
            missing_rate = df[col].isna().sum() / len(df)
            if missing_rate > self.thresholds["max_missing_rate"]:
                issues.append(f"{col} 缺失率 {missing_rate:.2%} 超过阈值 {self.thresholds['max_missing_rate']:.2%}")
        
        # 3. 价格异常检查
        if 'pct_chg' in df.columns:
            extreme_changes = df[df['pct_chg'].abs() > self.thresholds["max_price_change_pct"]]
            if len(extreme_changes) > 0:
                warnings.append(f"发现 {len(extreme_changes)} 天涨跌幅超过 {self.thresholds['max_price_change_pct']}%")
        
        # 4. OHLC 逻辑检查
        invalid_ohlc = df[(df['high'] < df['low']) | 
                         (df['high'] < df['open']) | 
                         (df['high'] < df['close']) |
                         (df['low'] > df['open']) |
                         (df['low'] > df['close'])]
        if len(invalid_ohlc) > 0:
            issues.append(f"OHLC 逻辑错误: {len(invalid_ohlc)} 条记录")
        
        # 5. 成交量异常检查
        zero_volume = df[df['volume'] == 0]
        if len(zero_volume) > 0:
            warnings.append(f"零成交量: {len(zero_volume)} 天")
        
        # 6. 日期连续性检查（简单版本，不考虑节假日）
        if len(df) > 1:
            df_sorted = df.sort_values('date')
            date_diffs = pd.to_datetime(df_sorted['date']).diff().dt.days
            large_gaps = date_diffs[date_diffs > 7]  # 超过7天的间隔
            if len(large_gaps) > 0:
                warnings.append(f"发现 {len(large_gaps)} 个大于7天的日期间隔")
        
        valid = len(issues) == 0
        return {
            "code": code,
            "valid": valid,
            "issues": issues,
            "warnings": warnings,
            "stats": {
                "total_rows": len(df),
                "date_range": f"{df['date'].min()} to {df['date'].max()}",
                "missing_rate": {col: float(df[col].isna().sum() / len(df)) for col in required_cols}
            }
        }
    
    def check_future_function(self, 
                             signal_date: str, 
                             data_date: str,
                             description: str = "") -> Dict:
        """检测未来函数
        
        Args:
            signal_date: 信号生成日期
            data_date: 数据日期
            description: 描述
            
        Returns:
            检测结果
        """
        signal_dt = pd.to_datetime(signal_date)
        data_dt = pd.to_datetime(data_date)
        
        is_future = data_dt > signal_dt
        
        return {
            "has_future_function": is_future,
            "signal_date": signal_date,
            "data_date": data_date,
            "days_ahead": (data_dt - signal_dt).days if is_future else 0,
            "description": description
        }
    
    def validate_backtest_data(self,
                              codes: List[str],
                              start_date: str,
                              end_date: str,
                              kline_reader) -> Dict:
        """验证回测数据集完整性
        
        Args:
            codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            kline_reader: K线数据读取函数 (code, start, end) -> DataFrame
            
        Returns:
            验证报告
        """
        report = {
            "total_codes": len(codes),
            "valid_codes": 0,
            "invalid_codes": [],
            "data_quality_summary": {
                "issues_count": 0,
                "warnings_count": 0
            },
            "coverage": {}
        }
        
        for code in codes:
            try:
                df = kline_reader(code, start_date, end_date)
                if df is None or len(df) == 0:
                    report["invalid_codes"].append({"code": code, "reason": "no_data"})
                    continue
                
                result = self.validate_kline(df, code)
                
                if result["valid"]:
                    report["valid_codes"] += 1
                else:
                    report["invalid_codes"].append({
                        "code": code,
                        "reason": "quality_issues",
                        "issues": result["issues"]
                    })
                
                report["data_quality_summary"]["issues_count"] += len(result.get("issues", []))
                report["data_quality_summary"]["warnings_count"] += len(result.get("warnings", []))
                
            except Exception as e:
                report["invalid_codes"].append({
                    "code": code,
                    "reason": "exception",
                    "error": str(e)
                })
        
        report["coverage"] = {
            "valid_rate": report["valid_codes"] / report["total_codes"] if report["total_codes"] > 0 else 0,
            "invalid_count": len(report["invalid_codes"])
        }
        
        return report
    
    def generate_report(self, report: Dict, output_path: Path):
        """生成数据质量报告
        
        Args:
            report: 验证报告字典
            output_path: 输出文件路径 (支持 .json 或 .md)
        """
        if output_path.suffix == '.json':
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif output_path.suffix == '.md':
            lines = [
                "# 数据质量验证报告",
                "",
                f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "## 概况",
                "",
                f"- 总股票数: {report['total_codes']}",
                f"- 有效股票数: {report['valid_codes']}",
                f"- 无效股票数: {len(report['invalid_codes'])}",
                f"- 有效率: {report['coverage']['valid_rate']:.2%}",
                "",
                "## 数据质量汇总",
                "",
                f"- 问题总数: {report['data_quality_summary']['issues_count']}",
                f"- 警告总数: {report['data_quality_summary']['warnings_count']}",
                "",
            ]
            
            if report['invalid_codes']:
                lines.extend([
                    "## 无效股票明细 (前50)",
                    "",
                    "| 代码 | 原因 | 详情 |",
                    "|------|------|------|",
                ])
                for item in report['invalid_codes'][:50]:
                    code = item['code']
                    reason = item['reason']
                    detail = item.get('error', '') or ', '.join(item.get('issues', []))
                    lines.append(f"| {code} | {reason} | {detail[:100]} |")
            
            output_path.write_text('\n'.join(lines), encoding='utf-8')
        
        return output_path


if __name__ == "__main__":
    # 示例用法
    validator = DataValidator()
    
    # 测试 K线数据验证
    sample_data = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=10),
        'open': [100 + i for i in range(10)],
        'high': [105 + i for i in range(10)],
        'low': [95 + i for i in range(10)],
        'close': [102 + i for i in range(10)],
        'volume': [1000000 + i*10000 for i in range(10)],
    })
    
    result = validator.validate_kline(sample_data, '600000')
    print(json.dumps(result, ensure_ascii=False, indent=2))
