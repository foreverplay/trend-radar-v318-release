"""研究实验室独立数据配置 - 只读本地缓存对接

明确与生产环境隔离的数据源配置，确保：
1. 只读访问本地缓存数据和SQLite数据库
2. 不修改源数据、不回退样例、不发网络请求
3. 明确数据来源、schema、复权口径、覆盖范围
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
import json


@dataclass
class ResearchDataConfig:
    """研究实验室数据配置
    
    所有路径都是只读访问，优先使用SQLite数据库（更快），
    回退到Parquet文件（完整历史但较慢）
    """
    
    # SQLite数据库配置（优先）
    sqlite_path: Optional[Path] = None
    sqlite_mode: str = "ro"  # 只读模式
    
    # Parquet缓存目录（回退）
    parquet_root: Optional[Path] = None
    parquet_daily_dir: Optional[Path] = None
    
    # 数据质量验证
    validate_on_init: bool = True
    
    # 元数据
    config_source: str = "unknown"
    validation_results: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_env(cls, 
                 env_prefix: str = "RESEARCH_",
                 fallback_to_production: bool = True) -> 'ResearchDataConfig':
        """从环境变量构建配置
        
        优先级:
        1. RESEARCH_SQLITE_PATH / RESEARCH_PARQUET_ROOT (研究专用)
        2. MARKET_DB_PATH / ASHARE_KLINE_ROOT (生产环境，fallback)
        
        Args:
            env_prefix: 研究环境变量前缀
            fallback_to_production: 是否回退到生产环境变量
            
        Returns:
            ResearchDataConfig实例
        """
        config = cls()
        
        # 1. 尝试读取研究专用环境变量
        sqlite_path_str = os.environ.get(f"{env_prefix}SQLITE_PATH")
        parquet_root_str = os.environ.get(f"{env_prefix}PARQUET_ROOT")
        
        # 2. 回退到生产环境变量
        if fallback_to_production:
            if not sqlite_path_str:
                sqlite_path_str = os.environ.get("MARKET_DB_PATH") or os.environ.get("TREND_RANK_DB")
            if not parquet_root_str:
                parquet_root_str = os.environ.get("ASHARE_KLINE_ROOT")
        
        # 3. 解析SQLite路径
        if sqlite_path_str:
            sqlite_path = Path(sqlite_path_str).expanduser().resolve()
            if sqlite_path.is_file():
                config.sqlite_path = sqlite_path
                config.config_source = f"env:{env_prefix}SQLITE_PATH or MARKET_DB_PATH"
        
        # 4. 解析Parquet根目录
        if parquet_root_str:
            parquet_root = Path(parquet_root_str).expanduser().resolve()
            if parquet_root.is_dir():
                config.parquet_root = parquet_root
                config.parquet_daily_dir = parquet_root / "raw" / "daily"
                if not config.config_source:
                    config.config_source = f"env:{env_prefix}PARQUET_ROOT or ASHARE_KLINE_ROOT"
        
        return config
    
    @classmethod
    def from_explicit_paths(cls,
                           sqlite_path: Optional[str] = None,
                           parquet_root: Optional[str] = None) -> 'ResearchDataConfig':
        """从显式路径构建配置（用于测试或本地脚本）"""
        config = cls()
        
        if sqlite_path:
            p = Path(sqlite_path).expanduser().resolve()
            if p.is_file():
                config.sqlite_path = p
                config.config_source = f"explicit:sqlite={p}"
        
        if parquet_root:
            p = Path(parquet_root).expanduser().resolve()
            if p.is_dir():
                config.parquet_root = p
                config.parquet_daily_dir = p / "raw" / "daily"
                if not config.config_source:
                    config.config_source = f"explicit:parquet={p}"
        
        return config
    
    def validate(self) -> Dict[str, Any]:
        """验证数据源可用性和质量
        
        Returns:
            验证结果字典
        """
        results = {
            "valid": False,
            "sqlite": {},
            "parquet": {},
            "errors": [],
            "warnings": []
        }
        
        # 验证SQLite
        if self.sqlite_path:
            try:
                conn = sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True, timeout=5)
                
                # 检查表结构
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                
                if 'daily_bars_qfq' in tables:
                    # 检查数据量
                    cursor = conn.execute("SELECT COUNT(DISTINCT symbol) as n_stocks, "
                                        "MIN(trade_date) as min_date, "
                                        "MAX(trade_date) as max_date, "
                                        "COUNT(*) as n_rows "
                                        "FROM daily_bars_qfq")
                    row = cursor.fetchone()
                    
                    results["sqlite"] = {
                        "available": True,
                        "path": str(self.sqlite_path),
                        "size_mb": self.sqlite_path.stat().st_size / 1024 / 1024,
                        "tables": tables,
                        "n_stocks": row[0],
                        "date_range": f"{row[1]} to {row[2]}",
                        "n_rows": row[3]
                    }
                    
                    if row[0] < 100:
                        results["warnings"].append(f"SQLite仅有{row[0]}只股票，可能不是完整数据")
                    
                else:
                    results["errors"].append("SQLite缺少 daily_bars_qfq 表")
                
                conn.close()
                
            except Exception as e:
                results["errors"].append(f"SQLite验证失败: {e}")
        else:
            results["warnings"].append("未配置SQLite数据库")
        
        # 验证Parquet
        if self.parquet_daily_dir and self.parquet_daily_dir.is_dir():
            try:
                parquet_files = list(self.parquet_daily_dir.glob("*.parquet"))
                
                results["parquet"] = {
                    "available": True,
                    "root": str(self.parquet_root),
                    "daily_dir": str(self.parquet_daily_dir),
                    "n_files": len(parquet_files),
                }
                
                if len(parquet_files) < 100:
                    results["warnings"].append(f"Parquet仅有{len(parquet_files)}个文件，可能是样例数据")
                
            except Exception as e:
                results["errors"].append(f"Parquet验证失败: {e}")
        else:
            results["warnings"].append("未配置Parquet目录或目录不存在")
        
        # 判断整体有效性
        results["valid"] = (
            (results["sqlite"].get("available") or results["parquet"].get("available"))
            and len(results["errors"]) == 0
        )
        
        self.validation_results = results
        return results
    
    def get_summary(self) -> str:
        """获取配置摘要（用于日志）"""
        lines = ["研究实验室数据配置:"]
        lines.append(f"  来源: {self.config_source}")
        
        if self.sqlite_path:
            size = self.sqlite_path.stat().st_size / 1024 / 1024
            lines.append(f"  SQLite: {self.sqlite_path} ({size:.1f}MB)")
        else:
            lines.append("  SQLite: 未配置")
        
        if self.parquet_root:
            lines.append(f"  Parquet: {self.parquet_root}")
            if self.parquet_daily_dir:
                n_files = len(list(self.parquet_daily_dir.glob("*.parquet"))) if self.parquet_daily_dir.is_dir() else 0
                lines.append(f"    日线文件: {n_files}")
        else:
            lines.append("  Parquet: 未配置")
        
        if self.validation_results:
            lines.append(f"  验证状态: {'✅ 有效' if self.validation_results.get('valid') else '❌ 无效'}")
            
            if self.validation_results.get("sqlite", {}).get("available"):
                sqlite_info = self.validation_results["sqlite"]
                lines.append(f"    SQLite: {sqlite_info['n_stocks']}只股票, {sqlite_info['n_rows']}条记录")
                lines.append(f"            {sqlite_info['date_range']}")
            
            if self.validation_results.get("warnings"):
                for w in self.validation_results["warnings"]:
                    lines.append(f"  ⚠️  {w}")
            
            if self.validation_results.get("errors"):
                for e in self.validation_results["errors"]:
                    lines.append(f"  ❌ {e}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典（用于序列化）"""
        return {
            "sqlite_path": str(self.sqlite_path) if self.sqlite_path else None,
            "sqlite_mode": self.sqlite_mode,
            "parquet_root": str(self.parquet_root) if self.parquet_root else None,
            "parquet_daily_dir": str(self.parquet_daily_dir) if self.parquet_daily_dir else None,
            "config_source": self.config_source,
            "validation_results": self.validation_results,
        }
    
    def save_to_file(self, path: Path):
        """保存配置到文件"""
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding='utf-8')


def create_default_config() -> ResearchDataConfig:
    """创建默认配置（从环境变量）"""
    config = ResearchDataConfig.from_env()
    
    if config.validate_on_init:
        config.validate()
    
    return config


if __name__ == "__main__":
    # 测试配置
    config = create_default_config()
    print(config.get_summary())
