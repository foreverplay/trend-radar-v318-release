"""可视化回测结果"""
import sys
from pathlib import Path
import json
import pandas as pd

_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LAB))


def visualize_backtest(result_file: Path):
    """可视化回测结果
    
    Args:
        result_file: 回测结果 JSON 文件
    """
    # 加载结果
    with open(result_file, 'r', encoding='utf-8') as f:
        result = json.load(f)
    
    print("\n" + "="*80)
    print(f"回测结果可视化 - Run ID: {result['run_id']}")
    print("="*80)
    
    # 1. 基本信息
    print("\n## 基本信息")
    print(f"- 日期范围: {result['start_date']} 至 {result['end_date']}")
    print(f"- 交易日数: {result['trading_days']}")
    print(f"- 初始资金: {result['initial_nav']:,.0f}")
    print(f"- 期末净值: {result['final_nav']:,.0f}")
    print(f"- 总收益率: {result['total_return']:.2%}")
    print(f"- 最大回撤: {result['max_drawdown']:.2%}")
    print(f"- 交易次数: {result['trade_count']}")
    
    # 2. 净值曲线
    nav_df = pd.DataFrame(result['daily_nav'])
    nav_df['date'] = pd.to_datetime(nav_df['date'])
    
    print("\n## 净值曲线关键点")
    print(f"- 起始净值: {nav_df.iloc[0]['total_value']:,.0f} ({nav_df.iloc[0]['date'].strftime('%Y-%m-%d')})")
    print(f"- 最高净值: {nav_df['total_value'].max():,.0f} ({nav_df.loc[nav_df['total_value'].idxmax(), 'date'].strftime('%Y-%m-%d')})")
    print(f"- 最低净值: {nav_df['total_value'].min():,.0f} ({nav_df.loc[nav_df['total_value'].idxmin(), 'date'].strftime('%Y-%m-%d')})")
    print(f"- 期末净值: {nav_df.iloc[-1]['total_value']:,.0f} ({nav_df.iloc[-1]['date'].strftime('%Y-%m-%d')})")
    
    # 3. 交易统计
    if result['trades']:
        trades_df = pd.DataFrame(result['trades'])
        
        buy_trades = trades_df[trades_df['action'] == 'BUY']
        sell_trades = trades_df[trades_df['action'] == 'SELL']
        
        print("\n## 交易统计")
        print(f"- 总交易次数: {len(trades_df)}")
        print(f"- 买入次数: {len(buy_trades)}")
        print(f"- 卖出次数: {len(sell_trades)}")
        print(f"- 总手续费: {trades_df['fee'].sum():,.2f}")
        
        print("\n## 交易明细（最近10笔）")
        print(trades_df[['date', 'code', 'action', 'shares', 'price', 'notional']].tail(10).to_string(index=False))
    
    # 4. 信号统计
    if result['signals_history']:
        signals_df = pd.DataFrame(result['signals_history'])
        
        print("\n## 信号统计")
        print(f"- 总买入信号: {signals_df['buy_count'].sum()}")
        print(f"- 总卖出信号: {signals_df['sell_count'].sum()}")
        print(f"- 平均每日买入信号: {signals_df['buy_count'].mean():.2f}")
        print(f"- 平均每日卖出信号: {signals_df['sell_count'].mean():.2f}")
    
    # 5. 持仓分析
    print("\n## 持仓分析")
    print(f"- 平均持仓数: {nav_df['position_count'].mean():.2f} 只")
    print(f"- 最大持仓数: {nav_df['position_count'].max()} 只")
    print(f"- 平均现金占比: {(nav_df['cash'] / nav_df['total_value']).mean():.2%}")
    
    # 6. 月度收益
    nav_df['month'] = nav_df['date'].dt.to_period('M')
    monthly_nav = nav_df.groupby('month')['total_value'].last()
    monthly_returns = monthly_nav.pct_change()
    
    print("\n## 月度收益率")
    for month, ret in monthly_returns.items():
        if not pd.isna(ret):
            print(f"  {month}: {ret:7.2%}")
    
    print("\n" + "="*80)
    
    return nav_df, trades_df if result['trades'] else pd.DataFrame()


def compare_results(result_files: list):
    """对比多个回测结果"""
    print("\n" + "="*80)
    print("回测结果对比")
    print("="*80)
    
    comparison = []
    for f in result_files:
        with open(f, 'r', encoding='utf-8') as fp:
            result = json.load(fp)
        
        comparison.append({
            'run_id': result['run_id'],
            'total_return': result['total_return'],
            'max_drawdown': result['max_drawdown'],
            'trade_count': result['trade_count'],
            'final_nav': result['final_nav']
        })
    
    df = pd.DataFrame(comparison)
    print(df.to_string(index=False))
    print("="*80)


if __name__ == "__main__":
    # 查找最新的回测结果
    results_dir = _LAB / "results" / "E01_trend_baseline"
    
    if not results_dir.exists():
        print("❌ 未找到回测结果目录")
        sys.exit(1)
    
    result_files = sorted(results_dir.glob("backtest_*.json"))
    
    if not result_files:
        print("❌ 未找到回测结果文件")
        sys.exit(1)
    
    # 可视化最新结果
    latest_result = result_files[-1]
    print(f"\n📊 可视化最新回测结果: {latest_result.name}")
    
    nav_df, trades_df = visualize_backtest(latest_result)
    
    # 如果有多个结果，对比
    if len(result_files) > 1:
        print(f"\n📊 发现 {len(result_files)} 个回测结果，进行对比:")
        compare_results(result_files[-5:])  # 最近5个
