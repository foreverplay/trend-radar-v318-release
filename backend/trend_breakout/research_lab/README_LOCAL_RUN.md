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

本机解释器是仓库里的 `.venv/bin/python`，不是 `venv`。当前 `main` 没有研究脚本时，刷新旧页面不会出现研究功能。不要在有未提交榜单和缓存的主工作目录里 reset。把本分支放到独立工作目录，只读打开已有缓存。

```bash
cd <独立研究工作目录>
.venv/bin/python backend/trend_breakout/research_lab/precheck_local.py
```

预检把读到的表口径写进 `research_lab/results/precheck.json`。没有 `daily_bars_raw` 这个表名，不等于没有原始价；`daily_bars` 没有口径列时状态是阻断。`bfq_daily` 的 `adjust=""` 只记录来源声明。kline 抽样行数不代表长期完整。

---

## 本机命令

不修改生产 `.env`，不重启已经占用 8765、5173、8790 的进程。研究 API 用另一个端口，健康检查是 `GET /api/health`。`/health` 在代码里不存在，404 不是故障。

```bash
export RESEARCH_SQLITE_PATH=<已有 sqlite，只读>
export RESEARCH_PARQUET_DIR=<已有 parquet 目录，只读>
export RESEARCH_RESULTS_ROOT=<独立结果目录>
.venv/bin/python backend/trend_breakout/research_lab/precheck_local.py
.venv/bin/python backend/trend_breakout/research_lab/run_research_backtest.py backend/trend_breakout/research_lab/configs/local_formal.json
RESEARCH_API_PORT=8791 backend/scripts/run_research_api.sh
cd frontend && npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
curl -sS http://127.0.0.1:8791/api/health
```

`local_formal.json` 只点名 `600519` 和 2024-12-02 到 2024-12-06。当前库如果只有 `daily_bars_qfq`，命令会阻断并写出 `manifest.status=blocked`，不会把前复权价格当成名义成交价。评分失败时记录不可计算，不生成 A 评级。

不要用 `configs/baseline_2024_month.json` 或 `baseline_2024_year.json` 当成交入口，它们不是这条闭环的配置。全市场完整年度要等原始行情和证券状态表可用后再扩大 `codes` 与日期；这里不估计耗时。

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

### 成交闭环

`run_research_backtest.py` 会写 signals、orders、trades、daily_positions、equity、metrics、manifest、data_quality。
正式模式在佣金、板块最小交易单位、上市特殊期或原始价格缺失时直接阻断。
诊断模式必须在配置里写 assumptions，产物标记为 `diagnostic_not_official_fills`，不是策略收益。

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
- 本机原始行情成交尚未执行。库中若只有 `daily_bars_qfq`，回测命令会阻断。

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
