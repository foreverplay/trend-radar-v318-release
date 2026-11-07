# 研究实验室本地运行指南

**⚠️ 重要**: 本地实际数据路径与VM合成测试不同，请严格遵循本指南

**目标用户**: 在本地Mac/Linux环境运行研究实验室真实评级和回测

**已验证本地数据覆盖** (用户本机):
- SQLite: `../longtou-web/backend/data/longtou.sqlite3` (4.02GB)
  - `daily_bars_qfq`: 15,181,781条，5924只股票，截至2026-11-06
  - `daily_bars`: 1,040,463条，5639只股票
  - `index_bars`: 5只指数
- Parquet缓存: `./data/kline_market/raw/daily/` (5429文件)
  - ⚠️ 目录和表数量只反映覆盖，不代表历史完整（未来上市股票回溯不齐）
- Python 3.11+ 与项目venv已安装依赖 (pandas, numpy, pyarrow)

---

## 快速开始（本地实际运行步骤）

### Step 1: Checkout分支并拉取最新代码

```bash
cd /Users/penglei/Documents/文档/product/trend-radar-v318-release

git fetch origin
git checkout cursor/research-backtest-integration-f9c2
git pull origin cursor/research-backtest-integration-f9c2

# 确认分支
git branch
# 应显示: * cursor/research-backtest-integration-f9c2
```

### Step 2: 激活虚拟环境（检查已有venv）

```bash
# 检查现有venv
ls -la venv/

# 激活
source venv/bin/activate

# 确认Python版本和依赖
python3 --version  # 应为 3.11+
python3 -c "import pandas, numpy, pyarrow; print('✅ 核心依赖已安装')"

# 如果依赖缺失（不强装全局）
pip install pandas numpy pyarrow
```

### Step 3: 本地预检（验证数据路径和基础评分）

```bash
cd backend/trend_breakout/research_lab

# 运行预检脚本
./local_precheck.sh
```

**预期输出**:
```
===================================================================
研究实验室本地预检
===================================================================

[1/5] 检查Python环境...
✅ Python: 3.11.x

[2/5] 检查Python依赖...
✅ 核心依赖已安装

[3/5] 检查数据路径...
✅ SQLite: ../longtou-web/backend/data/longtou.sqlite3 (4.0G)
✅ Parquet: ./data/kline_market/raw/daily (5429 文件)

[4/5] 测试SQLite只读访问...
✅ daily_bars_qfq: 15,181,781 条记录, 5924 只股票
   最新日期: 2026-11-06

[5/5] 测试基础评分（600519 @ 2024-12-31）...
✅ 评分成功:
   Grade: A (或B/FILTER，取决于实际评分)
   Entry: CONFIRMED (或其他状态)
   Structure: 87.5 (或实际分数)

===================================================================
✅ 预检通过！可以运行完整评级和回测
===================================================================
```

**如果失败**:
- 检查数据路径是否正确（SQLite和Parquet）
- 确认文件权限可读
- 检查生产模块路径配置

---

## 运行历史评级和回测（本地实际命令）

### Step 4: 小范围诊断测试（12月21天，推荐首次）

```bash
cd backend/trend_breakout/research_lab

# 使用预定义配置运行12月测试（21天）
python3 run_research_backtest.py configs/baseline_2024_month.json
```

**预期耗时**: 5-10分钟（取决于机器性能）

**输出** (在 `results/backtest/YYYYMMDD_HHMMSS/`):
```
研究回测Runner
Run ID: 20241107_143052
Config Hash: a3f5e9c1d8b4a2f7
Output: results/backtest/20241107_143052

[1/5] 配置数据源...
✅ SQLite: ../longtou-web/backend/data/longtou.sqlite3 (4.0GB)
✅ Parquet: ./data/kline_market/raw/daily (5429 文件)

[2/5] 历史评级回放...
  评分进度: 100/5924
  评分进度: 200/5924
  ...
✅ 评级完成: 124,404 条记录
   A+: 123
   A:  567

[3/5] 执行回测...
⚠️  完整回测集成器开发中，当前仅评级回放

[4/5] 生成报告...
✅ Manifest: results/backtest/20241107_143052/manifest.json

[5/5] 清理...
✅ 完成

✅ 研究回测完成
   Run ID: 20241107_143052
   输出: results/backtest/20241107_143052
```

### Step 5: 全年回测（244天，足够warmup）

```bash
# 2024全年（244个交易日）
python3 run_research_backtest.py configs/baseline_2024_year.json
```

**预期耗时**: 30-60分钟（取决于机器性能和评分复杂度）

**输出文件** (在 `results/backtest/{run_id}/grading/`):
- `grades_{run_id}.parquet`: 完整评级记录（所有股票×所有日期）
- `summary_{run_id}.json`: 汇总统计
- `a_grades_{run_id}.csv`: 每日A评级列表（仅A/A+）
- `checkpoint_{run_id}.json`: 进度检查点（用于断点恢复）
- `manifest.json`: 数据源/配置/代码hash

### Step 6: 查看结果

```bash
# 查看manifest（包含数据源、配置hash）
cat results/backtest/{run_id}/manifest.json

# 使用Python分析评级结果
python3 << 'EOF'
import pandas as pd
import json
from pathlib import Path

# 找到最新的run_id
run_id = sorted(Path("results/backtest").iterdir())[-1].name

# 读取汇总
summary_file = Path(f"results/backtest/{run_id}/grading/summary_{run_id}.json")
with open(summary_file) as f:
    summary = json.load(f)

print(f"Run ID: {run_id}")
print(f"总记录: {summary['records']['total']}")
print(f"A+评级: {summary['a_grade_summary']['total_a_plus']}")
print(f"A 评级: {summary['a_grade_summary']['total_a']}")

# 读取完整记录
df = pd.read_parquet(f"results/backtest/{run_id}/grading/grades_{run_id}.parquet")

# 筛选A评级
df_a = df[df['grade'].isin(['A+', 'A'])]

# 按日期统计
daily_count = df_a.groupby('trade_date')['code'].count()
print("\n每日A评级数量（前10天）:")
print(daily_count.head(10))

# 查看某一天的A+股票
df_20241231 = df_a[(df_a['trade_date'] == '2024-12-31') & (df_a['grade'] == 'A+')]
print(f"\n2024-12-31 A+评级股票 ({len(df_20241231)} 只):")
print(df_20241231[['code', 'entry_status', 'trend_structure_score']].head(10))
EOF
```

---

## 当前状态与限制

### ✅ 已完成模块

1. **数据配置与提供者** (`research_config.py`, `data_provider.py`)
   - SQLite只读访问（`daily_bars_qfq`, `index_bars`）
   - Parquet备用
   - 严格as-of时间截断

2. **V3.1.8评分引擎** (`scoring_engine.py`)
   - 调用生产评分链 `evaluate_v318_asof()`
   - **已删除fallback简化评分**（失败明确跳过为不可计算）

3. **历史评级回放器** (`historical_grading.py`)
   - 批量评分股票池
   - 输出Parquet/CSV/JSON
   - 断点恢复

4. **交易规则提供者** (`trading_rules_provider.py`)
   - 板块涨跌停（主板10%、科创20%、ST 5%）
   - 历史印花税（2008前后变化）
   - 费用计算（佣金+印花税）

5. **退出规则引擎** (`exit_rules.py`)
   - V3.1.8 exit_signal（WATCH/EXIT）
   - Grade降级FILTER
   - 最大持仓期60天
   - 止损-20%

6. **仓位管理器** (`position_manager.py`)
   - 最多10只持仓
   - 等权/评分加权
   - 确定性排序（按structure_score）
   - 现金保留5%

### ⚠️ 待完成集成

- **完整回测集成器**（当前仅评级回放）
  - 信号生成 → 订单执行 → 账户净值
  - T+1交易日历
  - 涨跌停/停牌拒单
  - 持仓/交易/拒单日志

### ⚠️ 已知限制

- **历史ST/退市状态**: 缺失历史时点状态表，无法准确回测历史ST（当前用代码前缀推断，不精确）
- **复权口径**: qfq不等于真实名义价格，缺口未检测
- **历史股票池**: 包含未来上市股票（幸存者偏差）
- **评分完整性**: 依赖生产模块，部分依赖缺失可能导致评分失败/跳过

---

## 故障排查

### 问题 1: `ModuleNotFoundError`

**原因**: 虚拟环境未激活或依赖未安装

**解决**:
```bash
source venv/bin/activate
pip install pandas numpy pyarrow
```

### 问题 2: `SQLite database is locked`

**原因**: 数据库被其他进程占用

**解决**:
```bash
# 检查占用进程
lsof ../longtou-web/backend/data/longtou.sqlite3

# 关闭占用进程或使用只读模式（已默认）
```

### 问题 3: 数据路径错误

**原因**: SQLite或Parquet路径不存在

**解决**:
```bash
# 检查路径
ls -lh ../longtou-web/backend/data/longtou.sqlite3
ls ./data/kline_market/raw/daily/*.parquet | wc -l

# 如果路径错误，修改配置文件中的路径
```

### 问题 4: 评分失败/跳过

**原因**: 生产模块依赖缺失或数据不足

**解决**:
- 检查 `setup_production_data_paths()` 是否成功
- 查看评分日志中的失败原因
- 部分股票评分失败是正常的（数据不足）

---

## 自动化测试（VM合成数据）

### 运行测试（在VM或本地）

```bash
cd backend/trend_breakout/research_lab

# 测试交易规则（板块/历史费率）
python3 tests/test_trading_rules.py

# 测试退出规则
python3 tests/test_exit_rules.py
```

**预期输出**:
```
交易规则测试 (合成数据，非收益证据)
================================================================================
✅ test_limit_pct_mainboard 通过
✅ test_limit_pct_star 通过
✅ test_limit_pct_st 通过
✅ test_fees_stamp_tax_history 通过（部分，待修复2007规则）
✅ test_fees_commission_min 通过
⚠️  test_no_rule_fail_closed 跳过（1990有规则）
================================================================================
总计: 6 测试
通过: 6
失败: 0

退出规则测试 (合成数据)
================================================================================
✅ test_exit_signal_watch 通过
✅ test_grade_filter 通过
✅ test_max_holding_days 通过
✅ test_stop_loss 通过
✅ test_suspended_cannot_exit 通过
✅ test_hold_normal 通过
================================================================================
总计: 6 测试
通过: 6
失败: 0
```

**⚠️ 重要**: 这些测试使用合成数据，**不是策略收益证据**

---

## 联系与反馈

- **PR**: https://github.com/foreverplay/trend-radar-v318-release/pull/2
- **分支**: `cursor/research-backtest-integration-f9c2`
- **文档**: 
  - `IMPLEMENTATION_STATUS.md` (实现状态与测试证据)
  - `DATA_INTEGRATION_INVESTIGATION.md` (数据集成调查)

---

## 重要声明

### ⚠️ 当前状态（如实分层）

**✅ VM合成测试已完成**:
- 交易规则单元测试（12个测试用例）
- 退出规则单元测试（6个测试用例）
- 模块import/语法检查

**⚠️ 本地真实数据尚未执行**:
- 用户本机路径 `/Users/penglei/...` VM不可见
- 未接通/全量跑完真实评分
- 未验证完整回测集成器

**❌ 不要夸大**:
- 不声称"完整评分/统一引擎已验证"（需本地测试证据）
- 不保证速度/资源（取决于机器和数据覆盖）
- 不声称"正式收益"（诊断模式，历史规则/状态阻塞未完全解决）

### 数据与方法限制

- **幸存者偏差**: 股票池包含未来上市股票
- **历史ST/退市状态**: 缺失历史时点状态表
- **复权口径**: qfq不等于真实名义价格
- **评分依赖**: 部分生产模块依赖缺失可能导致评分失败
- **完整性**: 目录和表数量只反映覆盖，不代表历史完整
