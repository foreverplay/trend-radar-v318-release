# A-Share Strategy Research Lab V1.0 — 仓库分析报告（Phase 0）

**版本核实**: 仓库名 `trend-radar-v318-release`，但代码显示实际版本为 **V3.1.8**（README.md、pyproject.toml 和最新提交均确认）。用户提及 Plan 写 V3.1.7 是早期文档，当前代码库已是 V3.1.8。

**分析时间**: 2026-11-07  
**分析范围**: Phase 0 仅分析现状，不实现任何功能  
**重要约束**: 本文档所有结论基于代码审查和静态分析，**未运行依赖安装或动态测试**（环境未完全配置）

---

## 1. 技术栈与环境

### 1.1 语言与运行时
- **Python**: 要求 >=3.11（pyproject.toml、README.md 明确声明）
- **Node.js**: 要求 >=18（前端 Vite + React + TypeScript）
- **架构**: 前后端分离
  - 后端: FastAPI (`backend/api/app.py`)
  - 前端: Vite + React (`frontend/`)

### 1.2 核心依赖（requirements.txt）
```
pandas>=2.0,<3
numpy>=1.24,<3
pyarrow>=14.0        # Parquet 数据读写
akshare>=1.14        # 行情数据源（多源 failover）
requests>=2.31
python-dateutil>=2.8
pytz>=2023.3
tqdm>=4.66
beautifulsoup4>=4.12  # HTML 解析
lxml>=4.9
fastapi>=0.110
uvicorn[standard]>=0.27
pydantic>=2.0
```

**依赖状态**: 环境未安装（`/tmp/cursor/async-install/install-user.status` 不存在），无法运行 Python 脚本验证。

### 1.3 数据库与存储
- **K线数据**: Parquet 文件 (`data/sample/kline/raw/daily/{code}.parquet`)
- **共享行情库（可选）**: SQLite3 (`MARKET_DB_PATH` 环境变量指向)
  - 表: `daily_bars_qfq` (前复权日线)、`index_bars` (指数)
  - 模块: `backend/trend_breakout/rank_db.py`
- **状态存储**: JSON 文件
  - `backend/trend_breakout/v3/exit_state/store_v318.json` (退出状态)
  - `backend/trend_breakout/cache/` (缓存目录)
  - `backend/trend_breakout/history/` (历史档案)
- **无专用数据库服务**: 没有 PostgreSQL/MySQL 等外部数据库

---

## 2. 现有 A 股行情获取与数据结构

### 2.1 行情获取方式（Failover 机制）
**主路径** (`docs/DATA_SOURCES.md`、`backend/trend_breakout/DATA_SOURCES.md`):
1. 本地 Parquet (`$ASHARE_KLINE_ROOT/raw/daily/{code6}.parquet`)
2. 东财 `stock_zh_a_hist` (akshare)
3. 新浪 `stock_zh_a_daily` (akshare)
4. 腾讯 `stock_zh_a_hist_tx` (akshare)
5. 必要时用 spot 行情补当日 T 日 bar

**行情刷新**:
- 收盘扫描: `backend/scripts/run_close_scan.sh` → `v2_3_scan.py` → `publish_close_v318.py`
- 盘中刷新: `backend/scripts/live_board_v318.py` (默认 25 分钟间隔)
- 共享行情后台（可选）: `MARKET_HUB_DIR` 指向的独立服务，约 2 分钟拉一轮

### 2.2 日线数据结构（`data/sample/kline/meta/schema.md`）
**复权口径**: **前复权（qfq）** 为主口径
- 文件: `raw/daily/{code}.parquet`
- 字段: `date, open, high, low, close, volume, amount, outstanding_share, turnover, change, pct_chg, amplitude, circ_mv`
- 技术指标（若已写入）: MA5/10/20/30/60/120/250, EMA12/26, VOL_MA5/20, MACD_*, RSI14, ATR14

**重要发现**:
- **仅样例数据**: `data/sample/kline/raw/daily/` 只含 12 只股票 (600519贵州茅台、600396华电辽能、601579会稽山、603087甘李药业、603259药明康德、000858五粮液、002962五方光电、000001平安银行、605100华丰股份、300750宁德时代、003006百亚股份、300725药石科技)
- **全市场数据未包含**: README 明确「不含全市场 ~4.5G 日线；仅 12 只 fixtures」
- **下载脚本**: `data/sample/kline/scripts/download_sample_or_universe.py` (使用 akshare 扩充)

### 2.3 复权处理与公司行为
**代码证据**:
- `backend/trend_breakout/v2_staircase_scan.py:658`: `adjust="qfq"`
- `backend/trend_breakout/dividend_v319/` 目录下有 `bfq` (不复权) 处理逻辑，但标注「全市场 bfq 仅~33只」
- `backend/trend_breakout/dividend_v319/run_phase12_backtest.py:479`: 「全市场未复权/bfq 覆盖不足，**组合净值仅用前复权价格路径**」
- **前/后/不复权处理**: 
  - 前复权 (qfq) 主口径，处理除权除息
  - 不复权 (bfq) 仅在红利模块使用，且覆盖不足
  - 后复权 (hfq) 未见使用

**关键风险**:
- **公司行为时间线缺失**: 没有找到分红、送股、配股的除权除息日期表
- **历史股票池问题**: `backend/scripts/backfill_a_plus_history.py:8` 明确「清单口径：跑榜时库里当前 A 股列表；已退市代码不在内」
- **时点穿越风险**: README.md:155「基本面、异动、资金等无法按日还原的字段不参与历史评分」

---

## 3. 股票基础信息与历史数据

### 3.1 上市/退市/ST/停牌
**现状检查**:
- **ST 检测**: `docs/STRATEGY_V2.md:13` 提及「非 ST/停牌/过新」过滤
- **退市标记**: 
  - `README.md:155`: 「已退市代码不会出现在历史档案中」
  - `backend/trend_breakout/dividend_v319/PHASE1_DATA_AUDIT.md:149`: 「ST / 退市 / 长期停牌时间线（现仅有当前 non_st + 名称关键字）」
- **停牌处理**: 未找到停牌日历表或 `is_trading` 标记字段

**数据缺口**:
- ❌ **无历史 ST 状态时间线**
- ❌ **无退市时间表**（只能依赖当前股票清单）
- ❌ **无停牌日历**（回测无法准确模拟无法成交）
- ⚠️ 名称包含 "ST" 关键字过滤不可靠（改名、摘帽无时间线）

### 3.2 板块与行业分类
**代码路径**:
- `backend/trend_breakout/v2_sector.py` (板块趋势评分)
- `backend/trend_breakout/v2_fundamental.py:220`: `INDUSTRY_NAME` 字段
- 数据源: `stock_individual_info_em` (东财) → CompanySurveyAjax

**现状**:
- ✅ 有行业字段获取逻辑
- ⚠️ **行业分类无历史版本**: 代码只获取当前行业，无历史变更记录
- ⚠️ 板块评分 `SectorScore` 影响趋势等级（A+ 要求 >=70），但无时点准确性保证

### 3.3 指数成分
**搜索结果**: 未找到指数成分股历史表（如沪深300、中证500成分变更）

---

## 4. 指标计算模块

### 4.1 技术指标引擎
**核心模块** (`backend/trend_breakout/`):
- `v2_structure.py`: 趋势结构评分（平台、突破、回撤）
- `v2_continuity.py`: 趋势延续性评分（W_STRUCTURE=25.0 权重）
- `v2_monthly.py`: 月线趋势判断（MONTHLY_STRONG_UP/UP/NEUTRAL/DOWN）
- `v2_fundamental.py`: 基本面（ROE、营收增长 YSTZ/SJLTZ）
- `v2_sector.py`: 板块趋势
- `v2_risk.py`: 风险等级

**指标类型**:
- 均线: MA5/10/20/30/60/120/250
- MACD: EMA12/26, DIF, DEA, MACD柱
- RSI14, ATR14
- 波段识别: `v3/engines/wave_structure.py`

### 4.2 V3 核心引擎 (`backend/trend_breakout/v3/`)
- `grading_v318.py`: **TrendGrade 评级** (A+/A/B/C/WATCH/FILTER)
- `v31/entry_window.py`: **入场窗口** (PRIME/CONFIRMED/FOLLOW/EXTENDED/NO_ENTRY)
- `engines/exit_engine.py`: **退出引擎** (保护价、破坏价)
- `engines/rapid_main_rise.py`: 快速主升浪识别
- `engines/continuity_engine.py`: 连续性
- `engines/active_trend_pool.py`: 活跃趋势池

---

## 5. 趋势雷达策略实现（生产代码）

### 5.1 策略入口与流程
**主流程** (`backend/scripts/run_close_scan.sh`):
```bash
1. v2_3_scan.py  # 多周期共振扫描，生成候选
2. v3.publish_close_v318  # V3.1.8 发布，生成四榜
```

**核心策略文档**: `docs/STRATEGY_V3_1_8_PLAN.md` (264行)

### 5.2 策略定义：评分/筛选 vs 交易系统

**关键发现 — 这是观察评分系统，不是完整交易策略**:

#### 5.2.1 TrendGrade（趋势等级）
- **定义**: `backend/trend_breakout/v3/grading_v318.py`
- **输出**: A+ / A / B / C / WATCH / FILTER
- **输入**: `TrendStructureScore, ContinuityScore, SectorScore, TechScore, FundRisk, TrendState`
- **用途**: 评价日线趋势图形质量

#### 5.2.2 EntryStatus（入场状态）
- **定义**: `backend/trend_breakout/v3/v31/entry_window.py`
- **输出**: PRIME / CONFIRMED / FOLLOW / EXTENDED / NO_ENTRY
- **判断依据**: 成熟度 (Maturity)、结构确认、波段消耗、Exhaustion

#### 5.2.3 四榜产品定义 (`README.md:22-30`)
| 榜单 | 规则 | 用途 |
|------|------|------|
| 成熟入场榜 | A+/A + PRIME/CONFIRMED | 高精度窗口 |
| 趋势全榜 | A+/A/B/C | 发现与跟踪 |
| 观察池 | B/C + EARLY/BUILDING | 图形开始变好但未成熟 |
| 过热延伸 | A+/A + EXTENDED | 高质量但过热 |

**关键约束** (README.md 和 STRATEGY_V3_1_8_PLAN.md 反复强调):
```
> **这是观察池，不是荐股软件。**  
> **禁止荐买 / 预测必涨。**  
> **A+ = 图形质量高，≠ 今天必买。**  
> **TrendGrade ⊥ EntryStatus（双轨）。**
```

#### 5.2.4 是否存在可执行交易规则？

**审查结果**: ❌ **现有策略仅是评分筛选，不具备明确入场/退出执行规则**

证据:
1. **入场**: 
   - `EntryStatus=PRIME/CONFIRMED` 仅表示「成熟窗口」，无具体买入价格、时点、仓位规则
   - 没有「T日收盘信号 → T+1 开盘买入」的明确执行逻辑（回测有，生产无）
   
2. **退出**: 
   - `ExitEngine` (`v3/engines/exit_engine.py`) **仅用于回测验证**
   - README.md:3 明确「观察池，不是实盘下单系统」
   - `backend/trend_breakout/v3/backtest/engine.py:11`: 「Does NOT feed live selection. 禁止荐买 / 预测必涨」
   
3. **持仓管理**: 无仓位分配、资金管理、组合构建逻辑

4. **榜单输出**: 
   - `data/sample/boards_v318_20261104.json` 包含「保护价 protect」「破坏价 break_price」「退出信号」
   - 但这些是**展示字段**，不是自动执行指令

**结论**: 
- ✅ 现有代码有完整的**评分和筛选**系统
- ✅ 有用于验证的**回测引擎**（`v3/backtest/engine.py`）
- ❌ **没有生产级可执行交易定义**（无券商接口、无下单逻辑、无实盘状态管理）
- ⚠️ **必须在研究实验室中另建独立基准交易定义**，不能虚构现有评分历史就是交易信号

### 5.3 趋势阶段/波段识别
**代码位置**:
- `v3/v31/types.py`: `TrendState`, `TrendMaturityLevel`, `WaveInfo`
- `v3/engines/wave_structure.py`: 波段结构
- **阶段**: NONE, OBSERVE, EARLY_TREND, BUILDING, MAIN_RISE, ACCELERATION, ADVANCED, TREND_FAILURE
- **成熟度**: EARLY, BUILDING, MATURE, ADVANCED

### 5.4 高周期趋势（HTF）与 Exhaustion
- `v3/v31/entry_window.py:72`: `htf_ok` 影响入场
- `v3/v31/types.py`: `ExhaustionLevel` (NORMAL/ELEVATED/HIGH)
- 无明确月线/周线数据存储路径（`v2_monthly.py` 从日线推算）

### 5.5 策略参数配置
**主配置**: `backend/trend_breakout/v3/config.py`
```python
TREND_BOT_CONFIG = {
    "version": "3.0.0",
    "phase": 14,
    "candidate": {
        "A+": {"tech_min": 85.0, "cont_min": 80.0, "sector_min": 70.0},
        "A": {"tech_min": 75.0, "cont_min": 70.0},
        "B": {"tech_min": 65.0, "cont_min": 60.0},
        ...
    },
    "exit": {
        "default_strategy": "Fixed5%",
        "atrMultiplier": 0.5,
        "maxDrawdown": 0.15,
        ...
    },
    "backtest": {
        "commission": 0.0003,  # 万三
        "stamp": 0.001,        # 千一
        "slippage": 0.001,     # 0.1%
        ...
    }
}
```

---

## 6. 已有回测能力

### 6.1 回测引擎 (`backend/trend_breakout/v3/backtest/`)
**核心文件**:
- `engine.py`: 主回测引擎（Phase 9 ExitEngine 验证）
- `strategies.py`: 退出策略（Fixed5%/8%/10%/15%, MA20, SwingLow, DynamicProtection）
- `costs.py`: 交易成本与涨跌停处理
- `metrics.py`: 指标计算
- `walk_forward.py`: Walk-Forward 验证

**成本模型** (`costs.py`):
```python
COMMISSION_RATE = 0.0003  # 万三 双边
STAMP_TAX_RATE = 0.001    # 千一 卖出
SLIPPAGE = 0.001          # 0.1%
```

**涨跌停处理**:
- 主板 60/00: ±9.8%
- 创业板 300/科创板 688: ±19.8%
- 北交所 8/4: ±29.8%
- 涨停无法买入，跌停无法卖出（延迟到下一交易日）

### 6.2 回测输入
**Entry 信号来源** (`engine.py:2-6`):
```python
# Entry: caller-supplied (t_date, code6) list, OR legacy RecScore A+ archive
#   (labeled ``entry_source=legacy_rec_score``). 
# Primary path does NOT pick live stocks.
```

**关键**: 
- ✅ 回测引擎接受外部信号列表
- ✅ 有历史 A+ 档案 (`LEGACY_A_PLUS = _ROOT / "history" / "a_plus_daily_2026.parquet"`)
- ❌ **没有从评分直接生成历史交易的逻辑**（需实验室自建）

### 6.3 回测执行逻辑 (`engine.py:6-8`)
```
- Buy T+1 open (close fallback); sell next open after exit signal.
- Costs + limit-up/down skip/delay.
- No lookahead: hist truncated to evaluation day; ExitEngine prior_state ratchet.
```

**T+1 约束**: 
- ✅ 买入遵守 T+1（信号日次交易日开盘）
- ✅ 卖出遵守 T+1
- ✅ 停牌、涨跌停延迟处理

### 6.4 回测验证状态
**已完成**:
- Phase 9: ExitEngine validation
- Phase 10: Exit strategy grid
- Phase 11: Walk-Forward (见 `backend/trend_breakout/v3/config.py:157` 注释)

**输出路径**:
- `backend/trend_breakout/results/exit_grid_2026proxy/`（未在仓库中，可能在运行时生成）

### 6.5 回测覆盖度
**数据限制**:
- ⚠️ 仅 12 只样例股票可直接回测
- ⚠️ 全市场回测需完整 4.5G K线库（未包含）
- ⚠️ 历史股票池 = 当前存活股票（幸存者偏差）

---

## 7. 日志、配置与调度

### 7.1 日志
- 目录: `backend/trend_breakout/logs/`
- 代码: `backend/trend_breakout/paths.py:33` 创建 `LOGS_DIR`
- **未找到**: 统一日志配置（logging.yaml）或日志聚合

### 7.2 环境配置
**环境变量** (`.env.example`):
```bash
ASHARE_KLINE_ROOT=./data/sample/kline
TREND_BREAKOUT_ROOT=./backend/trend_breakout
MARKET_DB_PATH=  # 可选：共享 SQLite
MARKET_HUB_URL=http://127.0.0.1:8790
MARKET_HUB_DIR=  # 可选：行情后台目录
```

### 7.3 任务调度
**现状**:
- ❌ **无 cron/systemd 定时任务**
- ❌ **无 Airflow/Celery 调度框架**
- ✅ 手动执行:
  - 收盘: `bash backend/scripts/run_close_scan.sh`
  - 盘中: `python backend/scripts/live_board_v318.py` (loop 模式)
  - 开发: `./run_dev.sh` (前后端 + 盘中刷新)

### 7.4 榜单发布流程
**生成路径** (`v3/publish_close_v318.py`):
1. 读取 `candidates_v2_3.json` (V2 扫描输出)
2. 运行 V3.1.7 成熟度/实现趋势层
3. 计算 V3.1.8 TrendGrade + EntryStatus
4. 分四榜 (成熟入场/趋势全榜/观察池/过热延伸)
5. 输出:
   - `data/sample/boards_v318_YYYYMMDD.json`
   - `push_close_v318_YYYYMMDD.md`

---

## 8. 当前架构总结

### 8.1 模块复用清单
**可直接复用**:
- ✅ Parquet 日线读取 (`paths.py`, `data/sample/kline/`)
- ✅ 多源行情 failover (`DATA_SOURCES.md`)
- ✅ 技术指标计算 (`v2_structure.py`, `v2_continuity.py`, `v2_monthly.py`)
- ✅ V3 评分引擎 (`grading_v318.py`, `entry_window.py`)
- ✅ 回测引擎框架 (`v3/backtest/engine.py`)
- ✅ 交易成本模型 (`v3/backtest/costs.py`)
- ✅ 退出策略库 (`v3/backtest/strategies.py`)

**需适配**:
- ⚠️ 股票池扩展（当前仅 12 只样例）
- ⚠️ 历史数据覆盖（需完整 K线库）
- ⚠️ ST/退市/停牌时间线（当前缺失）

### 8.2 数据缺口
| 数据类型 | 现状 | 影响 |
|---------|------|------|
| 全市场K线 | 仅12只样例 | 无法全市场回测 |
| 历史ST状态 | 无 | 无法防止选入ST股 |
| 退市时间表 | 无 | 幸存者偏差 |
| 停牌日历 | 无 | 成交模拟不准确 |
| 不复权行情 | 覆盖~33只 | 无法精确计算分红再投资 |
| 财务披露时点 | 无 | 时间穿越风险 |
| 指数成分历史 | 无 | 无法回测指数增强 |
| 行业分类历史 | 仅当前 | 板块评分不准确 |

### 8.3 功能缺口（研究实验室需补充）
| 功能 | 现状 | 需新增 |
|------|------|--------|
| 交易信号定义 | 仅评分筛选 | 明确入场/退出规则 |
| 信号历史回放 | 无 | as-of 信号生成 |
| 仓位管理 | 无 | 资金分配、持仓上限 |
| 组合构建 | 无 | 选股数量、权重分配 |
| 滑点成本模型 | 固定0.1% | 基于流动性动态滑点 |
| 禁止交易规则 | 涨跌停 | ST、退市、停牌、新股锁定期 |
| 数据质量检查 | 无 | 缺失值、异常值、未来函数检测 |
| 参数优化框架 | 无 | Grid Search / Walk-Forward |
| 样本外验证 | 有框架 | 完整3年训练/1年验证/1年OOS流程 |

---

## 9. 生产与研究接口关系

### 9.1 现有生产流程
```
[行情源] → [本地Parquet/共享SQLite] 
    ↓
[V2扫描 v2_3_scan.py]
    ↓
[V3.1.8发布 publish_close_v318.py]
    ↓
[四榜JSON] → [FastAPI] → [Web前端展示]
```

### 9.2 研究实验室隔离方案
**推荐位置**: `backend/trend_breakout/research_lab/` (新建)

**隔离原则**:
1. 不修改 `v2_*.py`, `v3/` 生产代码
2. 不依赖 `publish_close_v318.py` 榜单输出
3. 独立配置、独立数据目录、独立结果存储
4. 可复用 `v3/backtest/engine.py` 和 `v3/backtest/costs.py`

**建议结构**:
```
backend/trend_breakout/research_lab/
├── README.md                    # 实验室文档
├── config.py                    # 独立配置
├── data_validator.py            # 数据质量检查
├── signal_generator.py          # 交易信号定义
├── portfolio_manager.py         # 组合管理
├── backtest_runner.py           # 回测执行
├── experiments/                 # E01-E12 实验
│   ├── E01_trend_baseline.py
│   ├── E02_market_filter.py
│   └── ...
├── results/                     # 结果存档
└── notebooks/                   # 分析笔记本
```

### 9.3 接口点
**复用生产模块**:
- 行情读取: `from paths import LOCAL_DAILY, KLINE_ROOT`
- 技术指标: `from v2_structure import calc_structure_score`
- 评分引擎: `from v3.grading_v318 import grade_trend_v318`
- 回测框架: `from v3.backtest.engine import BacktestEngine`

**独立实现**:
- 交易规则: `research_lab/signal_generator.py`
- 股票池: `research_lab/universe.py`
- 数据验证: `research_lab/data_validator.py`

---

## 10. 必须新增的数据表

### 10.1 核心数据表
| 表名 | 用途 | 字段 | 来源 |
|------|------|------|------|
| `stock_list_history` | 股票上市/退市时间线 | code, list_date, delist_date, status | 东财/tushare |
| `st_status_history` | ST标记历史 | code, date, st_type (ST/\*ST/PT) | 东财/akshare |
| `suspend_calendar` | 停牌日历 | code, suspend_date, resume_date, reason | 东财 |
| `trading_rules` | 交易规则表 | effective_date, board, limit_up_pct, limit_down_pct, t_plus_n | 手动维护 |
| `index_constituents` | 指数成分历史 | index_code, stock_code, in_date, out_date, weight | 中证指数公司 |
| `industry_history` | 行业分类历史 | code, date, industry_l1, industry_l2, industry_l3 | 东财/申万 |
| `financial_calendar` | 财报披露日历 | code, report_period, disclosure_date | 东财 |
| `corporate_actions` | 公司行为 | code, ex_date, dividend, split_ratio, rights_ratio | akshare |

### 10.2 实验室专用表
| 表名 | 用途 |
|------|------|
| `backtest_runs` | 回测执行记录 (run_id, config, start_time, metrics) |
| `signal_history` | 信号历史档案 (run_id, date, code, signal_type, params) |
| `experiment_results` | E01-E12实验结果 |
| `parameter_grid` | 参数扫描结果 |

---

## 11. 数据质量风险评估

### 11.1 未来函数风险
**高风险区域**:
- ❌ 基本面数据: 无 `disclosure_date`，用 `report_date` 可能穿越
- ❌ 行业分类: 只有当前分类，无变更时间
- ⚠️ 板块评分: 实时计算可能用未来信息
- ⚠️ 异动原因/资金流: `backend/api/quote_service.py` 实时获取，回测不可用

**已防护**:
- ✅ K线数据: as-of 截断 (`engine.py:9` "hist truncated to evaluation day")
- ✅ 技术指标: 基于历史K线计算，无未来函数
- ✅ 退出状态: `ExitEngine prior_state ratchet` (逐日更新)

### 11.2 幸存者偏差
**现状**:
- ❌ **严重**: 历史股票池 = 当前存活股票
- ❌ 无退市时间表
- ❌ 无 ST 历史

**后果**:
- 历史回测收益高估
- 无法评估策略对垃圾股/退市股的筛选能力

### 11.3 前复权与成交模拟
**前复权特性**:
- ✅ 价格连续，适合技术指标计算
- ⚠️ 历史价格会因未来分红而调整（静态复权因子）
- ⚠️ 成交额 `amount` 是否复权？（schema.md 未说明）

**成交模拟保守假设** (`costs.py`):
- ✅ T+1 买卖
- ✅ 涨停不可买，跌停不可卖
- ✅ 固定滑点 0.1%
- ⚠️ 未考虑: 一字板、流动性不足、盘中止损顺序

### 11.4 数据覆盖确切范围
**已知**:
- 样例数据: 12 只股票，日期范围未知（需检查 parquet 文件）
- 历史榜单: `data/sample/boards_v318_202609*.json` 到 `20261104`

**未知**:
- 完整K线库覆盖起始日期
- 财务数据起始年份
- 行业分类数据时间跨度

---

## 12. 2026 沪深交易规则修订（待核验线索）

**用户提供信息**: 「沪深2026修订交易规则自2026-07-06实施且有暂缓条款」

**核验状态**: ❌ **未核验**（无法访问外部网络或官方文档）

**需核验内容**:
1. 上海证券交易所官网公告
2. 深圳证券交易所官网公告
3. 修订内容: 涨跌停幅度、T+N、交易时间、板块规则
4. 生效日期: 2026-07-06
5. 暂缓条款: 适用范围、条件

**影响评估**（假设修订存在）:
- 涨跌停幅度变化 → `costs.py` 需更新 `LIMIT_MAIN/LIMIT_CHINEXT_STAR`
- T+N 变化 → 回测引擎买卖延迟需调整
- 需实现 `RuleProvider` 按日期/板块加载规则

**当前代码处理**:
- `costs.py:25-28`: 硬编码 ±9.8%/19.8%/29.8%
- 无时间维度规则切换逻辑

**建议**: 待官方文件核实后，实现 `research_lab/trading_rules.py` 动态加载。

---

## 13. 技术栈一致性原则

**现有技术栈**:
- Python 3.11+
- Pandas + NumPy + PyArrow
- FastAPI
- SQLite3 (可选)
- 无外部消息队列/任务队列
- 无 Docker（未找到 Dockerfile）

**研究实验室约束**:
- ✅ 沿用 Python 3.11
- ✅ 继续用 Pandas/NumPy
- ✅ 结果存 JSON/Parquet/CSV
- ❌ **禁止引入**: TensorFlow/PyTorch (无需机器学习)
- ❌ **禁止引入**: Redis/RabbitMQ (过度设计)
- ⚠️ 可考虑: `joblib` (并行回测), `pytest` (测试)

---

## 14. 关键结论

### 14.1 版本差异说明
- **仓库名**: trend-radar-v318-release
- **实际版本**: V3.1.8 (代码、文档、配置一致)
- **Plan 文档**: `docs/STRATEGY_V3_1_8_PLAN.md` (最新)
- **差异**: 用户提及 V3.1.7 是中间版本，已被 V3.1.8 替代

### 14.2 核心发现
1. ✅ **策略代码完整**: V3.1.8 趋势雷达评分系统已实现
2. ✅ **回测框架存在**: 有完整 BacktestEngine
3. ❌ **不是交易策略**: 现有代码是**观察评分池**，不是可执行交易系统
4. ❌ **数据严重不足**: 无全市场K线、无ST/退市/停牌历史、无财务披露时点
5. ⚠️ **幸存者偏差**: 历史股票池 = 当前存活股票
6. ⚠️ **时间穿越风险**: 基本面、行业分类无时点版本

### 14.3 研究实验室必须自建
1. **交易信号定义**: 明确从 TrendGrade + EntryStatus → 买卖信号的规则
2. **信号历史生成**: as-of 回放，生成历史每日信号
3. **数据质量层**: 未来函数检测、缺失值处理、异常值过滤
4. **股票池管理**: ST/退市/停牌过滤，新股锁定期
5. **组合构建**: 持仓数量、权重分配、换手约束
6. **RuleProvider**: 按日期/板块加载交易规则（费率、涨跌停）
7. **版本化系统**: 每次回测独立 run_id，结果不可覆盖

### 14.4 Phase 0 验收状态
- ✅ 仓库版本已核实 (V3.1.8)
- ✅ 技术栈已梳理
- ✅ 数据源已分析
- ✅ 策略入口已定位
- ✅ 回测能力已评估
- ✅ 数据缺口已列举
- ✅ 隔离方案已提出
- ⚠️ 2026 交易规则**未核验**（待官方文档）
- ⚠️ 数据覆盖范围**部分未知**（需运行时检查）

---

## 15. 附录：关键文件路径清单

### 15.1 策略核心
- `backend/trend_breakout/v3/grading_v318.py` - TrendGrade 评级
- `backend/trend_breakout/v3/v31/entry_window.py` - 入场窗口
- `backend/trend_breakout/v3/publish_close_v318.py` - 榜单发布
- `docs/STRATEGY_V3_1_8_PLAN.md` - 策略文档

### 15.2 回测引擎
- `backend/trend_breakout/v3/backtest/engine.py` - 回测主引擎
- `backend/trend_breakout/v3/backtest/strategies.py` - 退出策略
- `backend/trend_breakout/v3/backtest/costs.py` - 交易成本
- `backend/trend_breakout/v3/backtest/metrics.py` - 指标计算

### 15.3 数据层
- `backend/trend_breakout/paths.py` - 路径配置
- `backend/trend_breakout/rank_db.py` - SQLite 行情读取
- `data/sample/kline/meta/schema.md` - K线字段说明
- `docs/DATA_SOURCES.md` - 数据源文档

### 15.4 配置与入口
- `backend/trend_breakout/v3/config.py` - 主配置
- `backend/scripts/run_close_scan.sh` - 收盘流水线
- `run_dev.sh` - 开发环境启动
- `.env.example` - 环境变量模板

---

**分析结论**: Phase 0 仓库分析已完成，发现现有系统是**观察评分池而非交易系统**，研究实验室必须另建独立交易定义，且存在严重数据缺口（ST/退市/停牌历史、幸存者偏差、时间穿越风险）。建议优先补充 Phase 1 数据质量层，再实施 Phase 2 统一回测引擎。
