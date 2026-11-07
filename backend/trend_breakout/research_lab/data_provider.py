"""研究实验室只读数据提供者 - 对接本地缓存和SQLite

确保严格 as-of 时间截断，复用生产 provider 但只读访问
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import pandas as pd

from research_config import ResearchDataConfig


class ReadOnlyDataProvider:
    """只读数据提供者
    
    优先使用SQLite（快），回退到Parquet（慢但完整）
    所有查询都是只读的，严格 as-of 截断
    """
    
    def __init__(self, config: ResearchDataConfig):
        self.config = config
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._sqlite_lock = threading.Lock()
        self._cache: Dict[str, pd.DataFrame] = {}  # 简单内存缓存
        
        # 验证配置
        if not config.validation_results.get("valid"):
            raise ValueError("ResearchDataConfig未通过验证，请先调用 config.validate()")
    
    def _get_sqlite_conn(self) -> Optional[sqlite3.Connection]:
        """获取SQLite连接（只读，线程安全）"""
        if not self.config.sqlite_path:
            return None
        
        with self._sqlite_lock:
            if self._sqlite_conn is None:
                uri = f"file:{self.config.sqlite_path}?mode=ro"
                self._sqlite_conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
                self._sqlite_conn.row_factory = sqlite3.Row
            return self._sqlite_conn
    
    def close(self):
        """关闭连接"""
        with self._sqlite_lock:
            if self._sqlite_conn:
                self._sqlite_conn.close()
                self._sqlite_conn = None
    
    def get_stock_daily_bars(self,
                            code: str,
                            end_date: Optional[str] = None,
                            start_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """获取股票日线数据（前复权）
        
        优先级:
        1. SQLite daily_bars_qfq (最快)
        2. Parquet文件 (回退)
        
        Args:
            code: 6位股票代码
            end_date: 截止日期（含），严格 as-of
            start_date: 开始日期（含），可选
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount, pct_chg
            如果无数据返回 None
        """
        code6 = str(code).zfill(6)
        
        # 尝试SQLite
        conn = self._get_sqlite_conn()
        if conn:
            try:
                query = """
                    SELECT trade_date as date, open, high, low, close, volume, amount
                    FROM daily_bars_qfq
                    WHERE symbol = ?
                """
                params = [code6]
                
                if start_date:
                    query += " AND trade_date >= ?"
                    params.append(start_date)
                
                if end_date:
                    query += " AND trade_date <= ?"
                    params.append(end_date)
                
                query += " ORDER BY trade_date"
                
                df = pd.read_sql_query(query, conn, params=params)
                
                if df.empty:
                    return None
                
                # 转换类型
                df['date'] = pd.to_datetime(df['date'])
                for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                
                # SQLite中volume单位是"手"，需要×100转为"股"
                df['volume'] = df['volume'] * 100.0
                
                # 计算涨跌幅
                df['pct_chg'] = df['close'].pct_change() * 100.0
                
                return df[['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']]
                
            except Exception as e:
                print(f"⚠️ SQLite查询失败 {code6}: {e}，回退到Parquet")
        
        # 回退到Parquet
        if self.config.parquet_daily_dir:
            parquet_file = self.config.parquet_daily_dir / f"{code6}.parquet"
            
            if not parquet_file.exists():
                return None
            
            try:
                df = pd.read_parquet(parquet_file)
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date')
                
                # 截断日期
                if start_date:
                    df = df[df['date'] >= pd.Timestamp(start_date)]
                if end_date:
                    df = df[df['date'] <= pd.Timestamp(end_date)]
                
                if df.empty:
                    return None
                
                # 确保必需列
                required = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount']
                if not all(c in df.columns for c in required):
                    return None
                
                # 计算涨跌幅（如果没有）
                if 'pct_chg' not in df.columns:
                    df['pct_chg'] = df['close'].pct_change() * 100.0
                
                return df[['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 'pct_chg']].reset_index(drop=True)
                
            except Exception as e:
                print(f"❌ Parquet读取失败 {code6}: {e}")
                return None
        
        return None
    
    def get_index_daily_bars(self,
                            symbol: str,
                            end_date: Optional[str] = None,
                            start_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """获取指数日线数据
        
        Args:
            symbol: 指数代码（如 sh000001, sz399001）
            end_date: 截止日期（含）
            start_date: 开始日期（含）
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount
        """
        symbol = str(symbol).lower()
        
        # 尝试SQLite
        conn = self._get_sqlite_conn()
        if conn:
            try:
                query = """
                    SELECT trade_date as date, open, high, low, close, volume, amount
                    FROM index_bars
                    WHERE symbol = ?
                """
                params = [symbol]
                
                if start_date:
                    query += " AND trade_date >= ?"
                    params.append(start_date)
                
                if end_date:
                    query += " AND trade_date <= ?"
                    params.append(end_date)
                
                query += " ORDER BY trade_date"
                
                df = pd.read_sql_query(query, conn, params=params)
                
                if df.empty:
                    return None
                
                df['date'] = pd.to_datetime(df['date'])
                for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                
                return df
                
            except Exception as e:
                print(f"⚠️ SQLite指数查询失败 {symbol}: {e}")
        
        # 回退到Parquet（如果配置了index_daily目录）
        if self.config.parquet_root:
            index_dir = self.config.parquet_root / "raw" / "index"
            if index_dir.is_dir():
                parquet_file = index_dir / f"{symbol}.parquet"
                
                if parquet_file.exists():
                    try:
                        df = pd.read_parquet(parquet_file)
                        df['date'] = pd.to_datetime(df['date'])
                        df = df.sort_values('date')
                        
                        if start_date:
                            df = df[df['date'] >= pd.Timestamp(start_date)]
                        if end_date:
                            df = df[df['date'] <= pd.Timestamp(end_date)]
                        
                        if df.empty:
                            return None
                        
                        return df.reset_index(drop=True)
                        
                    except Exception as e:
                        print(f"❌ 指数Parquet读取失败 {symbol}: {e}")
        
        return None
    
    def get_universe_at_date(self, trade_date: str) -> List[str]:
        """获取指定日期的股票池
        
        注意: 这是幸存者偏差的来源！
        - SQLite返回该日有交易的股票（接近真实但可能缺已退市）
        - Parquet返回所有有数据的股票（包含未来上市的，有偏差）
        
        Args:
            trade_date: 交易日期 YYYY-MM-DD
            
        Returns:
            股票代码列表（6位）
        """
        # 优先SQLite（更准确）
        conn = self._get_sqlite_conn()
        if conn:
            try:
                query = """
                    SELECT DISTINCT symbol
                    FROM daily_bars_qfq
                    WHERE trade_date = ? AND LENGTH(symbol) = 6
                    ORDER BY symbol
                """
                cursor = conn.execute(query, (trade_date,))
                codes = [row[0] for row in cursor.fetchall()]
                
                if codes:
                    return codes
                
            except Exception as e:
                print(f"⚠️ SQLite股票池查询失败: {e}")
        
        # 回退到Parquet（有偏差但可用）
        if self.config.parquet_daily_dir and self.config.parquet_daily_dir.is_dir():
            codes = []
            for f in self.config.parquet_daily_dir.glob("*.parquet"):
                code = f.stem
                if code.isdigit() and len(code) == 6:
                    # 检查该文件是否包含指定日期
                    try:
                        df = pd.read_parquet(f)
                        df['date'] = pd.to_datetime(df['date'])
                        if any(df['date'] == pd.Timestamp(trade_date)):
                            codes.append(code)
                    except:
                        continue
            
            return sorted(codes)
        
        return []
    
    def get_all_universe(self) -> List[str]:
        """获取所有股票代码（用于全历史扫描）
        
        ⚠️ 包含幸存者偏差：返回所有有数据的股票，包括未来上市的
        """
        # 优先SQLite
        conn = self._get_sqlite_conn()
        if conn:
            try:
                query = "SELECT DISTINCT symbol FROM daily_bars_qfq WHERE LENGTH(symbol) = 6 ORDER BY symbol"
                cursor = conn.execute(query)
                return [row[0] for row in cursor.fetchall()]
            except Exception as e:
                print(f"⚠️ SQLite全量股票池查询失败: {e}")
        
        # 回退到Parquet
        if self.config.parquet_daily_dir and self.config.parquet_daily_dir.is_dir():
            codes = []
            for f in self.config.parquet_daily_dir.glob("*.parquet"):
                code = f.stem
                if code.isdigit() and len(code) == 6:
                    codes.append(code)
            return sorted(codes)
        
        return []
    
    def get_trading_dates(self,
                         start_date: Optional[str] = None,
                         end_date: Optional[str] = None) -> List[str]:
        """获取交易日历
        
        从上证指数(sh000001)的交易日期推断
        
        Args:
            start_date: 开始日期（含）
            end_date: 结束日期（含）
            
        Returns:
            交易日期列表 ['YYYY-MM-DD', ...]
        """
        df = self.get_index_daily_bars('sh000001', end_date=end_date, start_date=start_date)
        
        if df is None or df.empty:
            return []
        
        return df['date'].dt.strftime('%Y-%m-%d').tolist()
    
    def get_data_coverage_summary(self) -> Dict[str, Any]:
        """获取数据覆盖摘要（用于诊断）"""
        summary = {
            "source": "unknown",
            "n_stocks": 0,
            "date_range": None,
            "n_trading_dates": 0,
        }
        
        conn = self._get_sqlite_conn()
        if conn:
            try:
                cursor = conn.execute("""
                    SELECT 
                        COUNT(DISTINCT symbol) as n_stocks,
                        MIN(trade_date) as min_date,
                        MAX(trade_date) as max_date
                    FROM daily_bars_qfq
                    WHERE LENGTH(symbol) = 6
                """)
                row = cursor.fetchone()
                
                summary["source"] = "SQLite"
                summary["n_stocks"] = row[0]
                summary["date_range"] = f"{row[1]} to {row[2]}"
                
                # 获取交易日数量
                cursor = conn.execute("""
                    SELECT COUNT(DISTINCT trade_date) as n_dates
                    FROM index_bars
                    WHERE symbol = 'sh000001'
                """)
                summary["n_trading_dates"] = cursor.fetchone()[0]
                
                return summary
                
            except Exception as e:
                print(f"⚠️ 数据覆盖查询失败: {e}")
        
        # 回退到Parquet
        if self.config.parquet_daily_dir and self.config.parquet_daily_dir.is_dir():
            codes = [f.stem for f in self.config.parquet_daily_dir.glob("*.parquet") 
                    if f.stem.isdigit() and len(f.stem) == 6]
            summary["source"] = "Parquet"
            summary["n_stocks"] = len(codes)
        
        return summary


def create_provider_from_env() -> ReadOnlyDataProvider:
    """从环境变量创建数据提供者（便捷函数）"""
    from research_config import create_default_config
    
    config = create_default_config()
    return ReadOnlyDataProvider(config)


if __name__ == "__main__":
    # 测试数据提供者
    provider = create_provider_from_env()
    
    print("\n数据覆盖摘要:")
    summary = provider.get_data_coverage_summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")
    
    print("\n测试获取贵州茅台数据:")
    df = provider.get_stock_daily_bars("600519", end_date="2024-12-31", start_date="2024-01-01")
    if df is not None:
        print(f"  获取 {len(df)} 条记录")
        print(f"  日期范围: {df['date'].min()} to {df['date'].max()}")
        print(f"  最后5条:")
        print(df.tail())
    else:
        print("  无数据")
    
    provider.close()
