"""E01: 趋势基准策略 — Phase 3

将 V3.1.8 评分系统转化为可执行交易规则，基于 12 只样例股票验证回测引擎
"""
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

# 添加路径
_LAB = Path(__file__).resolve().parents[1]
_ROOT = _LAB.parent
sys.path.insert(0, str(_LAB))
sys.path.insert(0, str(_ROOT))

from signal_generator import SignalGenerator
from trading_rules import RuleProvider
from portfolio_manager import PortfolioManager
from backtest_runner import BacktestRunner
from paths import LOCAL_DAILY
from v3.grading_v318 import grade_trend_v318
from v3.v31.entry_window import classify_entry_window


def get_sample_universe():
    """获取样例股票池（12只）"""
    return [
        '600519',  # 贵州茅台
        '600396',  # 华电辽能
        '601579',  # 会稽山
        '603087',  # 甘李药业
        '603259',  # 药明康德
        '000858',  # 五粮液
        '002962',  # 五方光电
        '000001',  # 平安银行
        '605100',  # 华丰股份
        '300750',  # 宁德时代
        '003006',  # 百亚股份
        '300725',  # 药石科技
    ]


def load_kline_data(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """加载 K线数据
    
    Args:
        code: 股票代码
        start_date: 开始日期（可选）
        end_date: 结束日期（可选）
        
    Returns:
        K线 DataFrame
    """
    parquet_file = LOCAL_DAILY / f"{code}.parquet"
    
    if not parquet_file.exists():
        return pd.DataFrame()
    
    try:
        df = pd.read_parquet(parquet_file)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        if start_date:
            df = df[df['date'] >= start_date]
        if end_date:
            df = df[df['date'] <= end_date]
        
        return df
    except Exception as e:
        print(f"⚠️ 加载 {code} 失败: {e}")
        return pd.DataFrame()


def calculate_trend_score_simple(df: pd.DataFrame, as_of_date: str) -> dict:
    """简化的趋势评分（基于技术指标）
    
    注意：这是简化版本，生产版本应使用完整的 V3.1.8 引擎
    
    Args:
        df: K线数据
        as_of_date: 截至日期
        
    Returns:
        trend_data 字典
    """
    # 截取到 as_of_date
    df_asof = df[df['date'] <= as_of_date].copy()
    
    if len(df_asof) < 60:
        return {
            'trend_grade': 'FILTER',
            'entry_status': 'NO_ENTRY',
            'exit_signal': 'HOLD',
            'broken': True,
            'structure_score': 0
        }
    
    # 计算技术指标
    close = df_asof['close'].values
    volume = df_asof['volume'].values
    
    # MA均线
    ma5 = pd.Series(close).rolling(5).mean().iloc[-1]
    ma10 = pd.Series(close).rolling(10).mean().iloc[-1]
    ma20 = pd.Series(close).rolling(20).mean().iloc[-1]
    ma60 = pd.Series(close).rolling(60).mean().iloc[-1]
    
    current_close = close[-1]
    
    # 简化的趋势评分逻辑
    # 1. 均线多头排列 → 高分
    ma_bullish = (ma5 > ma10 > ma20 > ma60)
    above_ma20 = (current_close > ma20)
    above_ma60 = (current_close > ma60)
    
    # 2. 近期涨幅
    ret_20 = (close[-1] / close[-20] - 1) if len(close) >= 20 else 0
    ret_60 = (close[-1] / close[-60] - 1) if len(close) >= 60 else 0
    
    # 3. 成交量（最近5日均量 vs 20日均量）
    vol5 = volume[-5:].mean() if len(volume) >= 5 else 0
    vol20 = volume[-20:].mean() if len(volume) >= 20 else 0
    vol_ratio = vol5 / vol20 if vol20 > 0 else 1
    
    # 评分规则（简化）
    score = 50
    
    if ma_bullish:
        score += 20
    if above_ma20:
        score += 10
    if above_ma60:
        score += 5
    if ret_20 > 0.1:
        score += 10
    if ret_60 > 0.2:
        score += 5
    if vol_ratio > 1.2:
        score += 5
    
    # Grade 映射
    if score >= 85:
        grade = 'A+'
    elif score >= 75:
        grade = 'A'
    elif score >= 65:
        grade = 'B'
    elif score >= 55:
        grade = 'C'
    else:
        grade = 'FILTER'
    
    # EntryStatus 简化判断
    if grade in ['A+', 'A'] and above_ma20 and ret_20 > 0:
        if ma_bullish and vol_ratio > 1.3:
            entry_status = 'PRIME'
        else:
            entry_status = 'CONFIRMED'
    elif grade in ['A+', 'A', 'B']:
        entry_status = 'FOLLOW'
    else:
        entry_status = 'NO_ENTRY'
    
    return {
        'trend_grade': grade,
        'entry_status': entry_status,
        'exit_signal': 'HOLD',
        'broken': False,
        'structure_score': score,
        'ma_bullish': ma_bullish,
        'ret_20': ret_20
    }


def get_trend_data_func(code: str, date: str) -> dict:
    """获取趋势数据（as-of）"""
    df = load_kline_data(code)
    if df.empty:
        return None
    
    return calculate_trend_score_simple(df, date)


def get_price_data_func(code: str, date: str) -> pd.DataFrame:
    """获取价格数据（as-of）"""
    df = load_kline_data(code)
    if df.empty:
        return pd.DataFrame()
    
    # 返回截至 date 的最后一行
    df_asof = df[df['date'] <= date]
    return df_asof


def run_e01_backtest():
    """运行 E01 趋势基准策略回测"""
    print("\n" + "="*80)
    print("E01: 趋势基准策略回测")
    print("="*80)
    
    # 1. 配置
    signal_config = {
        'entry_grades': ['A+', 'A'],
        'entry_statuses': ['PRIME', 'CONFIRMED'],
        'exit_on_no_entry': True,
        'exit_on_filter': True
    }
    
    portfolio_config = {
        'initial_capital': 1000000,
        'max_positions': 5,  # 12只样例，最多持5只
        'position_sizing': 'equal_weight',
        'single_position_limit': 0.3,
        'reserve_cash_rate': 0.05
    }
    
    backtest_config = {
        'start_date': '2023-01-01',
        'end_date': '2024-12-31',
        'slippage': 0.001
    }
    
    # 2. 初始化组件
    signal_gen = SignalGenerator(signal_config)
    portfolio_mgr = PortfolioManager(portfolio_config)
    rule_provider = RuleProvider()
    
    runner = BacktestRunner(
        signal_generator=signal_gen,
        portfolio_manager=portfolio_mgr,
        rule_provider=rule_provider,
        config=backtest_config
    )
    
    # 3. 获取股票池
    universe = get_sample_universe()
    print(f"\n股票池: {universe}")
    
    # 4. 获取交易日历（从第一只股票的数据中提取）
    sample_df = load_kline_data(universe[0])
    if sample_df.empty:
        print("❌ 无法获取交易日历")
        return
    
    trading_dates = sample_df[
        (sample_df['date'] >= backtest_config['start_date']) &
        (sample_df['date'] <= backtest_config['end_date'])
    ]['date'].dt.strftime('%Y-%m-%d').tolist()
    
    print(f"交易日数量: {len(trading_dates)}")
    print(f"日期范围: {trading_dates[0]} 至 {trading_dates[-1]}")
    
    # 5. 执行回测
    result = runner.run(
        universe=universe,
        trend_data_func=get_trend_data_func,
        price_data_func=get_price_data_func,
        trading_dates=trading_dates
    )
    
    # 6. 保存结果
    output_dir = _LAB / "results" / "E01_trend_baseline"
    result_file = runner.save_results(result, output_dir)
    
    # 7. 打印摘要
    print("\n" + "="*80)
    print("回测结果摘要")
    print("="*80)
    print(f"Run ID: {result['run_id']}")
    print(f"日期范围: {result['start_date']} 至 {result['end_date']}")
    print(f"交易日数: {result['trading_days']}")
    print(f"初始资金: {result['initial_nav']:,.0f}")
    print(f"期末净值: {result['final_nav']:,.0f}")
    print(f"总收益率: {result['total_return']:.2%}")
    print(f"最大回撤: {result['max_drawdown']:.2%}")
    print(f"交易次数: {result['trade_count']}")
    print(f"\n结果文件: {result_file}")
    print("="*80)
    
    return result


if __name__ == "__main__":
    try:
        result = run_e01_backtest()
        print("\n✅ E01 回测完成")
    except Exception as e:
        print(f"\n❌ E01 回测失败: {e}")
        import traceback
        traceback.print_exc()
