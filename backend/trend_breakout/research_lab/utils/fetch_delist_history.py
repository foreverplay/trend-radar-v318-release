"""获取退市历史数据 — Phase 1 P0

构建退市股票时间线，防止幸存者偏差
"""
import pandas as pd
import akshare as ak
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_lab.config import DELIST_FILE


def fetch_stock_info_all():
    """获取所有 A股信息（包含上市/退市状态）"""
    try:
        # akshare 股票信息（包含退市股票）
        df = ak.stock_info_a_code_name()
        
        if df is None or len(df) == 0:
            print("⚠️ 未获取到股票信息")
            return pd.DataFrame()
        
        print(f"✅ 获取到 {len(df)} 只股票信息")
        return df
        
    except Exception as e:
        print(f"❌ 获取股票信息失败: {e}")
        # 尝试备用接口
        try:
            df = ak.stock_zh_a_spot_em()
            print(f"✅ 备用接口获取到 {len(df)} 只股票")
            return df
        except Exception as e2:
            print(f"❌ 备用接口也失败: {e2}")
            return pd.DataFrame()


def build_delist_history(df: pd.DataFrame) -> pd.DataFrame:
    """从股票信息构建退市历史
    
    注意: akshare 可能不包含完整退市信息
    
    Args:
        df: 股票信息数据
        
    Returns:
        退市历史表 (code, list_date, delist_date, status, name)
    """
    if df.empty:
        return pd.DataFrame(columns=['code', 'list_date', 'delist_date', 'status', 'name', 'exchange'])
    
    # 根据实际列名调整
    history = pd.DataFrame({
        'code': df.get('code', df.get('代码', df.index)).astype(str).str.zfill(6),
        'name': df.get('name', df.get('名称', '')),
        'list_date': None,  # akshare 基础接口可能不包含
        'delist_date': None,
        'status': 'listed',  # 默认在市
        'exchange': df.get('code', df.get('代码', '')).astype(str).apply(
            lambda x: 'SH' if x.startswith('6') else ('SZ' if x.startswith(('0', '3')) else 'BJ')
        )
    })
    
    # 简单标记：名称包含"退"字的可能是退市股票
    history.loc[history['name'].str.contains('退', na=False), 'status'] = 'delisted'
    
    return history


def fetch_delist_history(output_path: Path = DELIST_FILE, save: bool = True) -> pd.DataFrame:
    """获取退市历史
    
    当前版本：基于 akshare 股票清单推断
    未来改进：对接完整上市/退市数据源
    
    Args:
        output_path: 输出文件路径
        save: 是否保存
        
    Returns:
        退市历史数据
    """
    print("=" * 60)
    print("Phase 1 P0: 获取退市历史")
    print("=" * 60)
    
    # 1. 获取股票信息
    stock_info = fetch_stock_info_all()
    
    if stock_info.empty:
        print("⚠️ 无股票信息，生成空表")
        history = pd.DataFrame(columns=['code', 'list_date', 'delist_date', 'status', 'name', 'exchange'])
    else:
        # 2. 构建退市历史
        history = build_delist_history(stock_info)
        delisted_count = (history['status'] == 'delisted').sum()
        print(f"✅ 构建退市历史表: {len(history)} 只股票, {delisted_count} 只退市")
    
    # 3. 元数据
    metadata = {
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'akshare.stock_info_a_code_name / stock_zh_a_spot_em',
        'limitation': '缺少精确退市日期，仅基于名称推断',
        'recommendation': '建议使用 tushare.stock_basic / Wind 获取完整上市退市时间线'
    }
    
    # 4. 保存
    if save and not history.empty:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        history.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"✅ 保存到: {output_path}")
        
        import json
        meta_path = output_path.with_suffix('.meta.json')
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"✅ 元数据: {meta_path}")
    
    print("\n⚠️ 当前限制:")
    print("- 无精确上市/退市日期")
    print("- 退市股票可能不在 akshare 清单中")
    print("- 历史回测仍有幸存者偏差风险")
    print("\n建议:")
    print("- 使用 tushare pro stock_basic (含上市/退市日期)")
    print("- 或对接交易所公告")
    
    return history


if __name__ == "__main__":
    df = fetch_delist_history(save=True)
    print(f"\n最终结果: {len(df)} 只股票")
    if not df.empty:
        print(f"\n状态分布:")
        print(df['status'].value_counts())
        print("\n退市股票示例:")
        delisted = df[df['status'] == 'delisted']
        if not delisted.empty:
            print(delisted.head(10))
