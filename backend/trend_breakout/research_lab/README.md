# A-Share Strategy Research Lab V1.0

**创建时间**: 2026-11-07  
**当前阶段**: Phase 1 → Phase 2  
**目标**: 构建独立的量化策略研究实验室，与生产代码隔离

---

## 目录结构

```
research_lab/
├── README.md                    # 本文档
├── config.py                    # 实验室配置
├── data_validator.py            # 数据质量检查工具
├── run_phase1_data_preparation.py  # Phase 1 主脚本
├── data/                        # 数据存储
│   ├── st_status_history.csv    # ST 状态历史
│   ├── delist_history.csv       # 退市历史
│   ├── suspend_calendar.csv     # 停牌日历
│   ├── financial_calendar.csv   # 财报披露时点（待补充）
│   ├── industry_history.csv     # 行业分类历史（待补充）
│   └── phase1_status.json       # Phase 1 执行状态
├── utils/                       # 工具脚本
│   ├── fetch_st_history.py      # 获取 ST 历史
│   ├── fetch_delist_history.py  # 获取退市历史
│   └── fetch_suspend_calendar.py  # 获取停牌日历
├── experiments/                 # E01-E12 实验
├── results/                     # 回测结果
└── notebooks/                   # Jupyter 分析笔记本
```

---

## Phase 1: 数据质量与完整性

### 目标
补充核心数据缺口，防止未来函数、幸存者偏差、时间穿越。

### P0 任务（已尝试）
- [x] ST 状态历史（框架完成，数据源限流）
- [x] 退市历史（框架完成，数据源限流）
- [x] 停牌日历（框架完成，接口不稳定）
- [x] 数据质量检查工具（`data_validator.py`）

### 执行状态
```bash
cd /workspace/backend/trend_breakout/research_lab
python3 run_phase1_data_preparation.py
```

**结果**: 
- ✅ 框架和工具完成
- ⚠️ akshare 接口限流，数据为空
- ✅ 已保存空表结构和元数据

### 当前限制

1. **数据源问题**:
   - akshare 东财接口限流（需等待5-40分钟）
   - 深交所 SSL 连接错误
   - 推荐使用 tushare pro（需token）或 Wind（需授权）

2. **数据完整性**:
   - ST历史: 无完整时间线（仅当前状态）
   - 退市历史: 缺精确日期
   - 停牌日历: 建议从成交量推断

3. **全市场K线**:
   - 当前仅 12 只样例股票
   - 需扩展至 ~5000 只（约 4.5GB）

### 替代方案

1. **从日线数据推断停牌**:
   ```python
   # volume=0 且 pct_chg=0 视为停牌
   is_suspended = (df['volume'] == 0) & (df['pct_chg'] == 0)
   ```

2. **使用现有样例数据**:
   - 12 只股票足够验证回测引擎逻辑
   - Phase 2/3 可先基于样例实现框架
   - 后续扩展全市场数据

3. **tushare pro**:
   ```python
   import tushare as ts
   pro = ts.pro_api('your_token')
   # pro.stock_basic() - 上市退市日期
   # pro.suspend_d() - 停牌日历
   # pro.st_events() - ST 时间线
   ```

---

## Phase 2: 统一回测引擎（进行中）

### 目标
- 扩展现有 `v3/backtest/engine.py`
- 实现 as-of 信号生成
- 接入 RuleProvider（按日期/板块加载规则）
- 版本化配置与结果存储

### 核心模块
1. `signal_generator.py` - 交易信号定义
2. `trading_rules.py` - RuleProvider 实现
3. `backtest_runner.py` - 回测执行器
4. `portfolio_manager.py` - 组合管理

### 关键约束
- 不修改 `v3/backtest/engine.py` 核心逻辑
- 可 import 生产模块（技术指标、评分引擎）
- 独立配置、独立结果存储
- 每次回测独立 run_id，结果不可覆盖

---

## Phase 3: E01 趋势基准策略

### 目标
将 V3.1.8 评分系统转化为可执行交易规则

### 交易定义（草案）
```python
# 入场
if (TrendGrade in ['A+', 'A'] and 
    EntryStatus in ['PRIME', 'CONFIRMED']):
    signal = 'BUY'

# 退出
if (ExitSignal != 'HOLD' or 
    EntryStatus == 'NO_ENTRY'):
    signal = 'SELL'

# 执行
# T 日收盘后生成信号 → T+1 日开盘执行
```

### 验证框架
- 3年训练 / 1年验证 / 1年样本外
- 滚动窗口，封存测试集
- Purging & Embargo 处理

---

## 数据质量检查

### 使用示例
```python
from data_validator import DataValidator
import pandas as pd

validator = DataValidator()

# 验证 K线
df = pd.read_parquet('data/sample/kline/raw/daily/600519.parquet')
result = validator.validate_kline(df, '600519')

if result['valid']:
    print("✅ 数据质量通过")
else:
    print("❌ 数据质量问题:")
    for issue in result['issues']:
        print(f"  - {issue}")

# 检测未来函数
check = validator.check_future_function(
    signal_date='2024-01-15',
    data_date='2024-01-20',
    description='使用未来数据'
)
print(check)  # {'has_future_function': True, 'days_ahead': 5}
```

---

## 与生产代码的隔离

### 可复用生产模块
```python
# 行情读取
from paths import LOCAL_DAILY, KLINE_ROOT

# 技术指标
from v2_structure import calc_structure_score
from v2_continuity import calc_continuity_score

# V3 评分引擎
from v3.grading_v318 import grade_trend_v318
from v3.v31.entry_window import classify_entry_window

# 回测框架
from v3.backtest.engine import BacktestEngine
from v3.backtest.costs import apply_buy_costs, apply_sell_costs
```

### 独立实现
```python
# 研究实验室独有
from research_lab.signal_generator import generate_signals
from research_lab.trading_rules import RuleProvider
from research_lab.portfolio_manager import PortfolioManager
from research_lab.backtest_runner import run_backtest
```

### 不可修改
- ❌ `v2_*.py` (V2 扫描)
- ❌ `v3/` (V3 评分/入场/退出引擎)
- ❌ `publish_close_v318.py` (榜单发布)
- ❌ `v3/backtest/engine.py` 核心逻辑

---

## 执行约束

### V1.0 范围限制
- ❌ 无券商接口
- ❌ 无实盘下单
- ❌ 无自动执行
- ✅ 仅回测与验证
- ✅ 人工批准后才能接入生产

### 回测引擎规则
- T 日收盘信号，最早 T+1 执行
- 股票 T+1，ETF T+0
- 停牌/涨停/跌停/一字板保守处理
- 日内止盈止损顺序保守假设
- 资金/持仓/成本精确核算

### 数据验证
- 失败必须阻止正式回测
- 未来函数检测
- 缺失值报告
- 异常值标记

---

## E01-E12 实验计划

| 实验 | 基准 | 变量 | 数据依赖 | 状态 |
|------|------|------|----------|------|
| E01 | - | 趋势基准 | 样例12只 | 🔄 Phase 3 |
| E02 | E01 | +大盘过滤 | 市场状态表 | ⏸️ |
| E03 | E01 | +行业轮动 | 行业历史 ❌ | ⏸️ 阻塞 |
| E04 | E01 | +相对强度 | 全市场K线 ❌ | ⏸️ 阻塞 |
| E05 | E01 | +平台缩量 | - | ⏸️ |
| E06 | E01 | +波动收缩 | - | ⏸️ |
| E07 | E01 | +量能确认 | - | ⏸️ |
| E08 | E01 | +动态止损 | - | ⏸️ |
| E09 | E01 | +动态仓位 | - | ⏸️ |
| E10 | E01 | +红利趋势 | 财报 ❌ | ⏸️ 阻塞 |
| E11 | E01 | +长期趋势短反 | - | ⏸️ |
| E12 | E01 | 组合优化 | 全实验结果 | ⏸️ |

---

## 下一步行动

### 立即可做（Phase 2）
1. ✅ 创建 `signal_generator.py` - 交易信号定义
2. ✅ 创建 `trading_rules.py` - RuleProvider
3. ✅ 创建 `backtest_runner.py` - 回测执行器
4. ⏸️ 基于 12 只样例验证回测引擎

### 需等待（数据依赖）
1. ⏸️ 全市场 K线扩展（4.5GB）
2. ⏸️ tushare pro token 或 Wind 授权
3. ⏸️ E03/E04/E10 实验（依赖完整数据）

### 验收后（Phase 3）
1. ⏸️ E01 趋势基准策略实现
2. ⏸️ 3年训练/1年验证/1年OOS
3. ⏸️ 策略评估报告

---

## 参考文档

- [Phase 0 仓库分析](../../../docs/strategy-lab/REPOSITORY_ANALYSIS.md)
- [Phase 0 实施状态](../../../docs/strategy-lab/IMPLEMENTATION_STATUS.md)
- [V3.1.8 策略文档](../v3/docs/STRATEGY_V3_1_8_PLAN.md)
- [数据源说明](../../DATA_SOURCES.md)

---

**最后更新**: 2026-11-07  
**维护者**: Research Lab Team
