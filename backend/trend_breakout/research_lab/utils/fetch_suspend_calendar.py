"""获取停牌日历 — Phase 1 P0

构建股票停牌复牌时间线，用于准确成交模拟
"""
import pandas as pd
import akshare as ak
from pathlib import Path
from datetime import datetime, timedelta
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_lab.config import SUSPEND_FILE


def fetch_suspend_info(start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """获取停牌信息
    
    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        
    Returns:
        停牌数据
    """
    if start_date is None:
        # 默认最近1年
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    
    try:
        # akshare 停牌信息（可能需要逐日查询）
        # 注意: akshare 可能没有直接的停牌日历接口
        print(f"尝试获取 {start_date} 至 {end_date} 停牌信息...")
        
        # 方案1: 尝试东财停牌复牌公告
        try:
            df = ak.stock_tfp_em(date=end_date)  # 停复牌信息
            if df is not None and len(df) > 0:
                print(f"✅ 获取到 {len(df)} 条停复牌记录 (日期: {end_date})")
                return df
        except Exception as e:
            print(f"⚠️ 东财停复牌接口失败: {e}")
        
        # 方案2: 从日线数据推断停牌（成交量为0）
        print("⚠️ 直接停牌接口不可用，建议从日线成交量推断")
        return pd.DataFrame()
        
    except Exception as e:
        print(f"❌ 获取停牌信息失败: {e}")
        return pd.DataFrame()


def build_suspend_calendar(df: pd.DataFrame = None) -> pd.DataFrame:
    """构建停牌日历
    
    Args:
        df: 停牌原始数据
        
    Returns:
        停牌日历 (code, suspend_date, resume_date, reason, days)
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=['code', 'suspend_date', 'resume_date', 'reason', 'days'])
    
    # 根据实际列名调整
    calendar = pd.DataFrame({
        'code': df.get('代码', df.get('code', '')).astype(str).str.zfill(6),
        'name': df.get('名称', df.get('name', '')),
        'suspend_date': df.get('停牌时间', df.get('suspend_date', None)),
        'resume_date': df.get('复牌时间', df.get('resume_date', None)),
        'reason': df.get('停牌原因', df.get('reason', '')),
        'days': None
    })
    
    # 计算停牌天数
    if 'suspend_date' in calendar.columns and 'resume_date' in calendar.columns:
        calendar['days'] = (pd.to_datetime(calendar['resume_date']) - 
                           pd.to_datetime(calendar['suspend_date'])).dt.days
    
    return calendar


def fetch_suspend_calendar(
    start_date: str = None,
    end_date: str = None,
    output_path: Path = SUSPEND_FILE,
    save: bool = True
) -> pd.DataFrame:
    """获取停牌日历
    
    当前版本：尝试从 akshare 获取
    未来改进：从日线成交量推断 + 交易所公告补充
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        output_path: 输出路径
        save: 是否保存
        
    Returns:
        停牌日历数据
    """
    print("=" * 60)
    print("Phase 1 P0: 获取停牌日历")
    print("=" * 60)
    
    # 1. 获取停牌信息
    suspend_info = fetch_suspend_info(start_date, end_date)
    
    if suspend_info.empty:
        print("⚠️ 未获取到停牌数据")
        calendar = pd.DataFrame(columns=['code', 'suspend_date', 'resume_date', 'reason', 'days'])
    else:
        # 2. 构建日历
        calendar = build_suspend_calendar(suspend_info)
        print(f"✅ 构建停牌日历: {len(calendar)} 条记录")
    
    # 3. 元数据
    metadata = {
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'date_range': f"{start_date} to {end_date}",
        'source': 'akshare.stock_tfp_em (尝试)',
        'limitation': 'akshare 停牌接口可能不稳定或不完整',
        'alternative': '从日线 volume=0 推断停牌，需结合交易所公告确认'
    }
    
    # 4. 保存
    if save:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not calendar.empty:
            calendar.to_csv(output_path, index=False, encoding='utf-8-sig')
            print(f"✅ 保存到: {output_path}")
        else:
            # 保存空表结构
            pd.DataFrame(columns=['code', 'suspend_date', 'resume_date', 'reason', 'days']).to_csv(
                output_path, index=False, encoding='utf-8-sig'
            )
            print(f"✅ 保存空表结构到: {output_path}")
        
        import json
        meta_path = output_path.with_suffix('.meta.json')
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"✅ 元数据: {meta_path}")
    
    print("\n⚠️ 当前限制:")
    print("- akshare 可能无完整停牌日历接口")
    print("- 建议方案：从日线 volume=0 + 涨跌幅=0 推断停牌")
    print("- 需结合交易所公告确认重大停牌")
    print("\n替代方案:")
    print("- 在回测引擎中检测 volume=0 视为停牌")
    print("- 使用 tushare pro suspend / suspend_d 接口")
    
    return calendar


if __name__ == "__main__":
    df = fetch_suspend_calendar(save=True)
    print(f"\n最终结果: {len(df)} 条停牌记录")
    if not df.empty:
        print("\n前 10 条:")
        print(df.head(10))
