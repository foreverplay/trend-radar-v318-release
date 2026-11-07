# 研究实验室本地运行指南

**目标用户**: 在本地Mac/Linux环境运行研究实验室回测

**前提条件**:
- 本地项目目录: `/Users/penglei/Documents/文档/product/trend-radar-v318-release/`
- SQLite数据库: `../longtou-web/backend/data/longtou.sqlite3` (4.02GB)
- Parquet缓存: `data/kline_market/raw/daily/` (5429文件)
- Python 3.11+ 与项目venv已安装依赖

---

## 快速开始

### Step 1: Checkout分支

```bash
cd /Users/penglei/Documents/文档/product/trend-radar-v318-release

# 拉取最新代码
git fetch origin

# 切换到研究分支
git checkout cursor/research-backtest-integration-f9c2

# 确认分支
git branch
# 应显示: * cursor/research-backtest-integration-f9c2
```

### Step 2: 配置环境变量

检查 `.env` 文件，确保以下配置存在：

```bash
# 在项目根目录检查
cat .env | grep -E "MARKET_DB_PATH|ASHARE_KLINE_ROOT"

# 应包含:
# MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3
# ASHARE_KLINE_ROOT=./data/kline_market   # 或 ./data/sample/kline（仅12只样例）
```

如果未配置，添加：

```bash
cat >> .env << 'EOF'

# === 研究实验室数据配置 ===
MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3
ASHARE_KLINE_ROOT=./data/kline_market
EOF
```

### Step 3: 激活虚拟环境

```bash
# 如果使用venv
source venv/bin/activate

# 或 conda
# conda activate trend-radar

# 确认Python版本
python3 --version
# 应为 3.11+

# 确认依赖
python3 -c "import pandas; print(pandas.__version__)"
# 应输出版本号，如 2.2.0
```

### Step 4: 验证数据配置

```bash
cd backend/trend_breakout/research_lab

# 测试配置模块
python3 research_config.py
```

**预期输出**:
```
研究实验室数据配置:
  来源: env:MARKET_DB_PATH
  SQLite: /Users/penglei/Documents/文档/product/longtou-web/backend/data/longtou.sqlite3 (4020.5MB)
    SQLite: 5924只股票, 15181781条记录
            1994-07-25 to 2026-11-06
  Parquet: /Users/penglei/Documents/文档/product/trend-radar-v318-release/data/kline_market (5429文件)
  验证状态: ✅ 有效
  ⚠️  使用全量股票池（包含幸存者偏差）
```

**如果失败**:
- 检查SQLite路径是否正确
- 检查Parquet目录是否存在
- 确认文件权限可读

### Step 5: 测试数据提供者

```bash
python3 data_provider.py
```

**预期输出**:
```
数据覆盖摘要:
  source: SQLite
  n_stocks: 5924
  date_range: 1994-07-25 to 2026-11-06
  n_trading_dates: 6789

测试获取贵州茅台数据:
  获取 244 条记录
  日期范围: 2024-01-02 to 2024-12-31
  最后5条:
       date      open     close  ...
  239  2024-12-23  1628.00  1632.50  ...
  240  2024-12-24  1635.00  1640.20  ...
  ...
```

**如果失败**:
- 检查SQLite是否被占用（关闭其他连接）
- 检查数据库表结构（`daily_bars_qfq`, `index_bars`）

### Step 6: 测试评分引擎

```bash
python3 scoring_engine.py
```

**预期输出**:
```
✅ 已配置生产模块数据路径:
   TREND_RANK_DB=/Users/.../longtou.sqlite3
   ASHARE_KLINE_ROOT=/Users/.../data/kline_market

测试评分引擎:
================================================================================

评分 600519 @ 2024-12-31:
  TrendGrade: A
  Grade Path: structure_classic
  Structure Score: 87.5
  Continuity Score: 82.3
  ⚠️  使用简化评分，非完整V3.1.8算法
```

**注意**: 
- 如果输出 `_fallback_mode=True`，表示使用简化评分（诊断模式）
- 完整评分需要生产模块正确配置（`rank_db`, `v2_structure`, 等）

---

## 运行历史评级回放

### 小范围诊断（推荐首次运行）

```python
# test_grading_2024.py
from research_config import create_default_config
from data_provider import ReadOnlyDataProvider
from historical_grading import run_historical_grading_for_year

# 创建配置
config = create_default_config()
provider = ReadOnlyDataProvider(config)

# 运行2024年（244个交易日）
print("开始回放2024年历史评级...")
summary = run_historical_grading_for_year(2024, provider, output_dir="results/grading_2024")

# 输出汇总
print("\n汇总结果:")
print(f"  总交易日: {summary['date_range']['n_dates']}")
print(f"  总记录: {summary['records']['total']}")
print(f"  A+评级: {summary['a_grade_summary']['total_a_plus']} (均{summary['a_grade_summary']['avg_a_plus_per_day']:.1f}/日)")
print(f"  A 评级: {summary['a_grade_summary']['total_a']} (均{summary['a_grade_summary']['avg_a_per_day']:.1f}/日)")

provider.close()
```

运行：

```bash
cd backend/trend_breakout/research_lab
python3 test_grading_2024.py
```

**预期耗时**: 
- SQLite优先: ~30-60分钟
- Parquet回退: ~2-4小时

**输出文件** (在 `results/grading_2024/`):
- `grades_{run_id}.parquet`: 完整评级记录（所有股票×所有日期）
- `summary_{run_id}.json`: 汇总统计
- `a_grades_{run_id}.csv`: 每日A评级列表（仅A/A+）
- `checkpoint_{run_id}.json`: 进度检查点（用于断点恢复）

### 查看结果

```python
import pandas as pd
import json

# 读取汇总
with open('results/grading_2024/summary_{run_id}.json') as f:
    summary = json.load(f)

print(f"总记录: {summary['records']['total']}")
print(f"A+评级: {summary['a_grade_summary']['total_a_plus']}")

# 读取完整记录
df = pd.read_parquet('results/grading_2024/grades_{run_id}.parquet')

# 筛选A评级
df_a = df[df['grade'].isin(['A+', 'A'])]

# 按日期统计
daily_count = df_a.groupby('trade_date')['code'].count()
print(daily_count.head(10))

# 查看某一天的A+股票
df_20241231 = df_a[(df_a['trade_date'] == '2024-12-31') & (df_a['grade'] == 'A+')]
print(df_20241231[['code', 'entry_status', 'trend_structure_score']])
```

---

## 运行完整回测 (待集成器完成)

⚠️ **当前状态**: 集成器尚未完成，以下是预期接口

```python
# run_backtest_2024.py (待实现)
from research_config import create_default_config
from data_provider import ReadOnlyDataProvider
from backtest_integration import run_full_backtest

config = create_default_config()
provider = ReadOnlyDataProvider(config)

# 运行完整回测
result = run_full_backtest(
    provider=provider,
    start_date="2024-01-01",
    end_date="2024-12-31",
    initial_capital=1000000,
    
    # 交易规则
    rules={
        "settlement_days": 1,  # T+1
        "commission_rate": 0.0003,  # 万三
        "stamp_tax_rate": 0.001,  # 千一
        "limit_up_pct": 10.0,
        "conservative_limit": True,
        "conservative_suspend": True,
    },
    
    # 信号配置
    signal_config={
        "entry_grades": ["A+", "A"],
        "entry_statuses": ["PRIME", "CONFIRMED"],
        "exit_on_filter": True,
    },
    
    # 仓位配置
    portfolio_config={
        "max_positions": 10,
        "position_sizing": "equal_weight",
        "single_position_limit": 0.2,
        "reserve_cash_rate": 0.05,
    },
    
    output_dir="results/backtest_2024"
)

print(f"总收益: {result['total_return_pct']:.2f}%")
print(f"最大回撤: {result['max_drawdown_pct']:.2f}%")
print(f"交易次数: {result['n_trades']}")

provider.close()
```

---

## 故障排查

### 问题 1: `ModuleNotFoundError: No module named 'pandas'`

**原因**: 虚拟环境未激活或依赖未安装

**解决**:
```bash
# 激活venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 或手动安装核心依赖
pip install pandas pyarrow numpy
```

### 问题 2: `SQLite database is locked`

**原因**: 数据库被其他进程占用

**解决**:
```bash
# 检查占用进程
lsof ../longtou-web/backend/data/longtou.sqlite3

# 关闭占用进程或等待
# 或使用只读模式（已默认）
```

### 问题 3: `验证状态: ❌ 无效 - 未配置SQLite数据库`

**原因**: 环境变量未设置或路径错误

**解决**:
```bash
# 检查 .env
cat .env | grep MARKET_DB_PATH

# 检查文件是否存在
ls -lh ../longtou-web/backend/data/longtou.sqlite3

# 手动设置环境变量（临时）
export MARKET_DB_PATH="../longtou-web/backend/data/longtou.sqlite3"
python3 research_config.py
```

### 问题 4: 评分速度慢

**优化**:
1. 确保使用SQLite优先（比Parquet快10倍）
2. 减少日期范围（先测试1个月）
3. 使用fallback简化评分（`use_fallback_scoring=True`）
4. 关闭其他占用资源的程序

### 问题 5: 内存不足

**症状**: `MemoryError` 或进程被杀

**解决**:
```python
# 减少缓存
config.cache_size = 100  # 减少缓存股票数

# 分批处理
for year in [2022, 2023, 2024]:
    run_historical_grading_for_year(year, provider)
    # 每年处理完清空缓存
    provider._cache.clear()
```

---

## 资源监控

### 查看进程资源

```bash
# 实时监控
top -pid $(pgrep -f "python3.*grading")

# 内存峰值
ps aux | grep python3 | grep grading
```

### 预期资源使用

**2024年回放** (244天, 5924股):
- **CPU**: 单核100% (可并行优化)
- **内存**: 2-3GB峰值
- **磁盘读取**: 
  - SQLite: ~500MB读取
  - Parquet: ~2GB读取
- **磁盘写入**: ~50-100MB (结果Parquet)

---

## 进度与日志

### 查看实时进度

```bash
# 进度检查点
tail -f results/grading_2024/checkpoint_{run_id}.json

# Python日志
# 在脚本中添加
import logging
logging.basicConfig(level=logging.INFO)
```

### 断点恢复

```python
# 如果中途中断，再次运行会自动恢复
config = HistoricalGradingConfig(
    start_date="2024-01-01",
    end_date="2024-12-31",
    resume_from_checkpoint=True,  # 默认启用
    checkpoint_interval=10,  # 每10天保存
)

# 手动清除检查点（重新开始）
import os
os.remove('results/grading_2024/checkpoint_{run_id}.json')
```

---

## 下一步

1. **验证数据**: 确认Step 1-6全部通过
2. **小范围测试**: 运行2024年1个月（2024-12-01 to 2024-12-31）
3. **审查结果**: 检查A评级是否合理
4. **反馈问题**: 报告任何报错或异常
5. **等待集成器**: 完整回测集成器开发中

---

## 联系与反馈

- **PR**: https://github.com/foreverplay/trend-radar-v318-release/pull/2
- **分支**: `cursor/research-backtest-integration-f9c2`
- **文档**: `IMPLEMENTATION_STATUS.md`, `DATA_INTEGRATION_INVESTIGATION.md`

**重要提示**: 
- ⚠️ 当前为诊断模式，非正式回测结果
- ⚠️ 历史ST/停牌/费率等阻塞项未完全解决
- ⚠️ 幸存者偏差警告：股票池包含未来上市股票
- ⚠️ 评分可能使用fallback简化模式（非完整V3.1.8）
