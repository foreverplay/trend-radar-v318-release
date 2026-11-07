"""获取 ST 状态历史数据 — Phase 1 P0

使用 akshare 获取 ST 股票历史，构建时间线
"""
import pandas as pd
import akshare as ak
from pathlib import Path
from datetime import datetime
import time
import sys

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_lab.config import ST_STATUS_FILE


def fetch_current_st_stocks():
    """获取当前 ST 股票列表"""
    try:
        # akshare 获取 ST 股票
        df = ak.stock_zh_a_st_em()
        
        if df is None or len(df) == 0:
            print("⚠️ 未获取到 ST 股票数据")
            return pd.DataFrame()
        
        print(f"✅ 获取到 {len(df)} 只当前 ST 股票")
        return df
        
    except Exception as e:
        print(f"❌ 获取 ST 股票失败: {e}")
        return pd.DataFrame()


def build_st_history_from_current(df: pd.DataFrame) -> pd.DataFrame:
    """从当前 ST 列表构建基础历史表
    
    注意: akshare 只提供当前 ST 状态，无历史时间线
    这里仅构建基础框架，实际历史需要其他数据源
    
    Args:
        df: 当前 ST 股票数据
        
    Returns:
        ST 历史表 (code, st_date, st_type, status)
    """
    if df.empty:
        return pd.DataFrame(columns=['code', 'st_date', 'st_type', 'status', 'name'])
    
    # akshare 返回列可能为: 代码, 名称, 涨跌幅, ...
    # 需要根据实际列名调整
    code_col = '代码' if '代码' in df.columns else 'code'
    name_col = '名称' if '名称' in df.columns else 'name'
    
    history = pd.DataFrame({
        'code': df[code_col].astype(str).str.zfill(6),
        'st_date': datetime.now().strftime('%Y-%m-%d'),  # 当前日期
        'st_type': df[name_col].str.extract(r'(\*?ST|S)', expand=False).fillna('ST'),
        'status': 'active',  # 当前激活状态
        'name': df[name_col]
    })
    
    return history


def fetch_st_history(output_path: Path = ST_STATUS_FILE, save: bool = True) -> pd.DataFrame:
    """获取 ST 状态历史
    
    当前版本：仅获取当前 ST 状态
    未来改进：需要对接完整历史数据源（如 tushare pro、Wind）
    
    Args:
        output_path: 输出文件路径
        save: 是否保存到文件
        
    Returns:
        ST 历史数据
    """
    print("=" * 60)
    print("Phase 1 P0: 获取 ST 状态历史")
    print("=" * 60)
    
    # 1. 获取当前 ST 股票
    current_st = fetch_current_st_stocks()
    
    if current_st.empty:
        print("⚠️ 无 ST 数据，生成空表")
        history = pd.DataFrame(columns=['code', 'st_date', 'st_type', 'status', 'name'])
    else:
        # 2. 构建基础历史表
        history = build_st_history_from_current(current_st)
        print(f"✅ 构建 ST 历史表: {len(history)} 条记录")
    
    # 3. 添加元数据
    metadata = {
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'akshare.stock_zh_a_st_em',
        'limitation': '仅包含当前 ST 状态，无完整历史时间线',
        'recommendation': '生产环境建议使用 tushare pro 或 Wind 获取完整历史'
    }
    
    # 4. 保存
    if save and not history.empty:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        history.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"✅ 保存到: {output_path}")
        
        # 保存元数据
        meta_path = output_path.with_suffix('.meta.json')
        import json
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"✅ 元数据: {meta_path}")
    
    print("\n⚠️ 当前限制:")
    print("- akshare 仅提供当前 ST 状态")
    print("- 无历史 ST/摘帽时间线")
    print("- 回测时无法准确判断历史某日是否为 ST")
    print("\n建议:")
    print("- Phase 1 后期对接 tushare pro (需token)")
    print("- 或使用 Wind 数据（需商业授权）")
    print("- 或爬取交易所公告构建时间线")
    
    return history


if __name__ == "__main__":
    df = fetch_st_history(save=True)
    print(f"\n最终结果: {len(df)} 条 ST 记录")
    if not df.empty:
        print("\n前 10 条:")
        print(df.head(10))
