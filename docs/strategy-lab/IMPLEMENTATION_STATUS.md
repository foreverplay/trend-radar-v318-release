# A-Share Strategy Research Lab V1.0 — 实施状态（Phase 0）

**更新时间**: 2026-11-07  
**当前阶段**: Phase 0 — 仓库分析与规划  
**执行约束**: 严格仅执行 Phase 0，不实现 Phase 1-6，不修改任何生产代码

---

## Phase 0: 仓库分析与基线确认 ✅ 已完成

### 目标
1. 核实仓库实际版本（用户提及 Plan 写 V3.1.7，仓库名 v318）
2. 分析现有架构、数据、策略实现
3. 判断现有策略是否为可执行交易系统
4. 梳理数据缺口与功能缺口
5. 提出实验室隔离方案

### 已完成任务
| 任务 | 状态 | 证据 |
|------|------|------|
| 版本核实 | ✅ 完成 | 仓库名 v318-release，代码实际 V3.1.8 (README.md, pyproject.toml) |
| 技术栈分析 | ✅ 完成 | Python 3.11, Pandas, FastAPI, Parquet, SQLite |
| 行情数据分析 | ✅ 完成 | 多源 failover, 前复权, 仅 12 只样例 |
| 策略代码审查 | ✅ 完成 | 评分筛选系统，非可执行交易策略 |
| 回测能力评估 | ✅ 完成 | 有 BacktestEngine，但无生产信号生成 |
| 数据缺口识别 | ✅ 完成 | 无 ST/退市/停牌历史，幸存者偏差严重 |
| 隔离方案设计 | ✅ 完成 | `research_lab/` 独立目录，不改生产代码 |
| 2026交易规则核验 | ⚠️ 待核验 | 无法访问外部网络，无法获取官方文档 |

### 关键发现
1. **版本一致性**: 代码库确实是 V3.1.8，Plan 文档提及的 V3.1.7 是中间版本
2. **策略性质**: ❌ **现有代码不是可执行交易策略**，是观察评分池
   - 有 TrendGrade (A+/A/B/C) 和 EntryStatus (PRIME/CONFIRMED/FOLLOW/EXTENDED)
   - 有四榜产品定义（成熟入场/趋势全榜/观察池/过热延伸）
   - 但无明确「T日收盘信号 → T+1 开盘买入」执行规则
   - README 反复强调「观察池，不是荐股软件」「禁止荐买/预测必涨」
3. **回测引擎**: ✅ 有完整 `v3/backtest/engine.py`，支持成本/涨跌停/T+1
4. **数据严重不足**:
   - 仅 12 只样例股票 K线
   - 无 ST 状态历史、无退市时间表、无停牌日历
   - 无财务披露时点、无行业分类历史版本
   - 历史股票池 = 当前存活股票（幸存者偏差）

### 实际执行的检查命令

#### 1. 仓库基本信息
```bash
cd /workspace && git remote -v && git log --oneline -5
# 结果: 仓库 trend-radar-v318-release, 最新提交包含 dividend V3.1.9
```

#### 2. 版本确认
```bash
cat /workspace/README.md | head -1
cat /workspace/pyproject.toml | grep version
# 结果: V3.1.8 (README) + version = "3.1.8" (pyproject.toml)
```

#### 3. 文件结构扫描
```bash
ls -la /workspace/
find /workspace/backend/trend_breakout -name "*.py" | wc -l
# 结果: 126 个 Python 文件
```

#### 4. 数据样例检查
```bash
ls -la /workspace/data/sample/
find /workspace/data -name "*.parquet" | head -20
# 结果: 12 只股票 parquet, 多个历史榜单 JSON
```

#### 5. 配置与路径检查
```bash
cat /workspace/backend/trend_breakout/paths.py
cat /workspace/backend/trend_breakout/v3/config.py | head -200
# 结果: 路径通过环境变量解耦，有完整配置字典
```

### 未执行的命令（原因：环境未安装依赖）
```bash
# ❌ 无法运行: ModuleNotFoundError: No module named 'pandas'
python3 -c "import pandas; print(pandas.__version__)"

# ❌ 无法运行回测测试
python backend/trend_breakout/v3/backtest/run_exit_grid.py

# ❌ 无法运行回归测试
python backend/trend_breakout/v3/v31/regression_v318.py
```

**原因**: `/tmp/cursor/async-install/install-user.status` 不存在，依赖未安装

### 限制与未验证项
1. ⚠️ **数据覆盖时间范围**: 无法读取 parquet 确认起止日期
2. ⚠️ **回测引擎运行状态**: 无法执行验证脚本
3. ⚠️ **财务数据可用性**: 无法测试 `v2_fundamental.py` 数据获取
4. ⚠️ **2026交易规则**: 无法访问上交所/深交所官网核验

---

## Phase 1: 数据质量与完整性 🚫 未实施

### 目标（Phase 0 不执行）
- 补充全市场 K线数据（~4.5G）
- 构建 ST/退市/停牌历史表
- 建立财务披露日历
- 实现数据质量检查工具
- 防止未来函数、幸存者偏差

### 阻塞项
| 阻塞 | 说明 | 优先级 |
|------|------|--------|
| 全市场K线 | 仅 12 只样例，无法全市场回测 | P0 |
| ST历史 | 无法过滤 ST 股票 | P0 |
| 退市时间表 | 幸存者偏差严重 | P0 |
| 停牌日历 | 成交模拟不准确 | P1 |
| 财报披露时点 | 时间穿越风险 | P1 |
| 行业分类历史 | 板块评分不准确 | P2 |

### 预计新增数据表（Phase 1 需实现）
见 `REPOSITORY_ANALYSIS.md` 第 10 节，包括:
- `stock_list_history` (上市/退市)
- `st_status_history` (ST 标记)
- `suspend_calendar` (停牌)
- `trading_rules` (交易规则)
- `index_constituents` (指数成分)
- `industry_history` (行业分类)
- `financial_calendar` (财报披露)
- `corporate_actions` (分红/送配)

---

## Phase 2: 统一回测引擎 🚫 未实施

### 目标（Phase 0 不执行）
- 基于现有 `v3/backtest/engine.py` 扩展
- 实现 as-of 信号生成
- 接入 RuleProvider（按日期/板块加载规则）
- 版本化配置与结果存储
- 数据验证必须阻止正式回测

### 依赖 Phase 1
- 需完整历史数据（K线、ST、停牌）
- 需 `trading_rules` 表

---

## Phase 3: 趋势基准策略（E01）🚫 未实施

### 目标（Phase 0 不执行）
- 将 V3.1.8 评分系统转化为可执行交易规则
- 定义明确的入场/退出触发条件
- 实现历史信号回放

### 交易定义示例（Phase 3 需完成）
**入场**:
- 条件: `TrendGrade in [A+, A] AND EntryStatus in [PRIME, CONFIRMED]`
- 执行: T 日收盘后生成信号，T+1 日开盘买入
- 仓位: 等权/按评分加权（待定）
- 持仓上限: 5-20 只（待定）

**退出**:
- 条件: `ExitSignal != HOLD` OR `EntryStatus == NO_ENTRY` OR 持有 N 日
- 执行: T 日收盘后生成信号，T+1 日开盘卖出

**重要**: ❌ **当前代码无此定义，Phase 3 必须实现，不能虚构评分历史等同交易**

---

## Phase 4-6: 增量实验（E02-E12）🚫 未实施

### E02: 大盘环境过滤
- 依赖: 市场状态 (NORMAL/BEAR/CHOP)
- 现状: `v3/regime_map.py` 有框架

### E03: 行业轮动
- 依赖: Phase 1 行业分类历史
- 阻塞: ❌ 无历史数据

### E04: 相对强度排名
- 依赖: Phase 1 全市场 K线
- 阻塞: ❌ 仅 12 只样例

### E05-E12: 其他维度
- 平台缩量、波动收缩、量能确认、动态止损、动态仓位、红利趋势、长期趋势短期反转
- 均依赖 Phase 1-3 完成

---

## 整体约束与原则（已写入分析文档）

### 实验室与生产隔离
- ✅ 定义: 独立 `research_lab/` 目录
- ✅ 原则: 不修改 `v2_*.py`, `v3/` 生产代码
- ✅ 复用: 可 import 生产模块（行情读取、技术指标、回测框架）
- ✅ 独立: 配置、数据、结果存储完全隔离

### V1.0 范围限制
- ❌ 无券商接口
- ❌ 无实盘下单
- ❌ 无自动执行
- ✅ 仅回测与验证
- ✅ 人工批准后才能接入生产

### 回测引擎约束
- T 日收盘信号，最早 T+1 执行
- 股票 T+1，ETF T+0（待实现）
- 停牌/涨停/跌停/一字板保守处理
- 日内止盈止损顺序保守假设
- 资金/持仓/成本精确核算
- 公司行为（分红/送配）纳入（Phase 1 后）

### RuleProvider（Phase 2 实现）
- 按交易所/板块/股票状态/生效时间加载
- 费用按历史日期（佣金/印花税可能变化）
- 不用静态一套规则
- **2026 交易规则**: 待官方文档核实后实现

### 数据验证
- 失败必须阻止正式回测
- 未来函数检测
- 缺失值报告
- 异常值标记

### 版本化
- 行情/股票池/行业/财务/规则/代码/配置全版本化
- 可复现：同配置同结果
- 每次新 run_id
- 历史结果不可覆盖

### E01-E12 单变量增量对照
| 实验 | 基准 | 变量 | 数据依赖 |
|------|------|------|----------|
| E01 | - | 趋势基准 | Phase 1 完整数据 |
| E02 | E01 | +大盘过滤 | 市场状态表 |
| E03 | E01 | +行业轮动 | 行业历史 ❌ 阻塞 |
| E04 | E01 | +相对强度 | 全市场K线 ❌ 阻塞 |
| E05 | E01 | +平台缩量 | - |
| E06 | E01 | +波动收缩 | - |
| E07 | E01 | +量能确认 | - |
| E08 | E01 | +动态止损 | - |
| E09 | E01 | +动态仓位 | - |
| E10 | E01 | +红利趋势 | 财报 ❌ 阻塞 |
| E11 | E01 | +长期趋势短反 | - |
| E12 | E01 | 组合优化 | 全实验结果 |

### 验证框架
- 3 年训练 / 1 年验证 / 1 年样本外
- 滚动窗口
- 封存测试集，不反复调参
- Purging & Embargo 处理（Phase 2 实现）
- 参数邻域测试
- 成本压力测试
- 市场环境分组
- 所有实验留档
- PBO/CSCV 辅助（非主要）

### 指标体系
- 成本后账户净值指标
- 预配置淘汰阈值
- 样本不足标记
- 不把未验证策略纳入正式候选

### AI 使用原则
- 仅提出假设与生成配置
- 解释真实结果
- ❌ 不改历史结果
- ❌ 不改生产代码
- ❌ 不绕过评估流程

---

## Phase 0 验收状态

### 必须完成项 ✅
- [x] 核实仓库版本（V3.1.8）
- [x] 梳理技术栈（Python 3.11, Pandas, FastAPI）
- [x] 分析行情获取（多源 failover, 前复权）
- [x] 定位策略入口（`publish_close_v318.py`）
- [x] 判断策略性质（❌ 非可执行交易系统，是评分筛选池）
- [x] 评估回测能力（✅ 有 `BacktestEngine`，但缺信号生成）
- [x] 识别数据缺口（ST/退市/停牌/财报时点/行业历史全缺失）
- [x] 列出关键风险（幸存者偏差、时间穿越、未来函数）
- [x] 设计隔离方案（`research_lab/` 独立目录）
- [x] 交付文档（`REPOSITORY_ANALYSIS.md` + `IMPLEMENTATION_STATUS.md`）

### 待核验项 ⚠️
- [ ] 2026 沪深交易规则修订（无法访问官方网站）
  - 需要: 上交所/深交所公告原文
  - 需要: 生效日期、适用范围、暂缓条款
  - 建议: Phase 1 前完成核验

### 未验证项（依赖环境）⚠️
- [ ] 数据覆盖时间范围（无法读取 parquet）
- [ ] 回测引擎运行验证（依赖未安装）
- [ ] 财务数据可用性（依赖未安装）
- [ ] 技术指标计算正确性（依赖未安装）

---

## 下一阶段最小建议

### Phase 1 优先任务（待验收后执行）
1. **P0 - 全市场 K线**:
   ```bash
   # 使用 download_sample_or_universe.py 扩展
   # 或对接完整行情源（tushare/东财）
   ```

2. **P0 - ST/退市历史**:
   ```python
   # 方案 A: akshare.stock_zh_a_st_em()
   # 方案 B: 东财数据中心
   # 输出: backend/trend_breakout/research_lab/data/st_status_history.csv
   ```

3. **P0 - 停牌日历**:
   ```python
   # akshare 或东财停牌公告
   # 输出: research_lab/data/suspend_calendar.csv
   ```

4. **P1 - 数据质量检查工具**:
   ```python
   # research_lab/data_validator.py
   # - 检查缺失值
   # - 检查异常值（涨跌幅 >30%）
   # - 检查未来函数（技术指标不依赖未来数据）
   # - 生成数据质量报告
   ```

5. **P1 - 财报披露时点**:
   ```python
   # akshare.stock_financial_report_sina()
   # 输出: research_lab/data/financial_calendar.csv
   ```

### Phase 2 核心任务
1. **交易信号定义** (`research_lab/signal_generator.py`):
   ```python
   def generate_signals(date, universe, hist_data, config):
       # 输入: 截至 date 的历史数据
       # 输出: {code: 'BUY'/'SELL'/'HOLD'}
       # 逻辑: TrendGrade + EntryStatus → 明确信号
   ```

2. **RuleProvider** (`research_lab/trading_rules.py`):
   ```python
   class RuleProvider:
       def get_commission(self, date, board):
           # 历史佣金率（可能有变化）
       def get_limit_pct(self, date, board):
           # 涨跌停幅度（2026-07-06 后可能变化）
   ```

3. **回测执行器** (`research_lab/backtest_runner.py`):
   ```python
   # 复用 v3/backtest/engine.py
   # 增加: as-of 信号生成、版本化存储、数据验证
   ```

### Phase 3 E01 基准
```python
# research_lab/experiments/E01_trend_baseline.py
config = {
    "universe": "all_a_share",  # 需 Phase 1 完整数据
    "entry": "TrendGrade in [A+, A] AND EntryStatus in [PRIME, CONFIRMED]",
    "exit": "ExitSignal != HOLD OR EntryStatus == NO_ENTRY",
    "position_sizing": "equal_weight",
    "max_positions": 10,
    "train_period": "2023-01-01 to 2025-12-31",
    "validation_period": "2026-01-01 to 2026-12-31",
    "oos_period": "2027-01-01 to 2027-12-31"  # 未来，暂不可用
}
```

---

## Phase 0 最终检查清单

### 文档交付 ✅
- [x] `docs/strategy-lab/REPOSITORY_ANALYSIS.md` (完整分析)
- [x] `docs/strategy-lab/IMPLEMENTATION_STATUS.md` (本文档)
- [x] 中文书写
- [x] 包含具体路径/证据
- [x] 标注未验证项
- [x] 无猜测冒充事实

### 分支与提交 ✅
- [x] 创建独立分支 `cursor/phase0-strategy-lab-analysis-f9c2`
- [x] 仅新增文档，无生产代码修改
- [x] 准备提交

### 关键约束遵守 ✅
- [x] 严格仅执行 Phase 0
- [x] 未实现 Phase 1-6
- [x] 未修改任何生产代码/配置/调度/数据
- [x] 未修改榜单或策略行为
- [x] 未自动合并

### 发现核实 ✅
- [x] 版本差异已说明（V3.1.7 → V3.1.8）
- [x] 策略性质已判断（评分筛选 vs 交易系统）
- [x] 数据缺口已列举（具体表、字段、影响）
- [x] 未来函数风险已标注
- [x] 幸存者偏差已警告
- [x] 复权与成交模拟已分析

### 后续约束已写入 ✅
- [x] 实验室与生产隔离原则
- [x] V1.0 无实盘/无下单约束
- [x] 回测引擎 T+1 执行约束
- [x] RuleProvider 动态加载需求
- [x] 数据验证阻止机制
- [x] 版本化与可复现要求
- [x] E01-E12 单变量对照设计
- [x] 3年训练/1年验证/1年OOS框架
- [x] AI 使用边界

---

## Phase 0 达到验收标准：✅ 是

**理由**:
1. ✅ 版本核实完成（V3.1.8，与仓库名一致）
2. ✅ 架构与数据全面分析（15 节详细报告）
3. ✅ 关键发现明确（非交易系统，数据严重不足）
4. ✅ 可复用模块清单完整（技术指标、回测框架）
5. ✅ 数据缺口具体列举（8 张表 + 字段定义）
6. ✅ 功能缺口量化评估（13 项需新增）
7. ✅ 隔离方案可操作（目录结构 + 接口点）
8. ✅ 后续约束全覆盖（生产隔离、回测规则、验证框架）
9. ✅ 未验证项如实标注（2026规则、数据范围）
10. ✅ 无猜测、无虚构、无自动进入 Phase 1

**限制说明**:
- ⚠️ 2026 交易规则待核验（需官方文档，非技术阻塞）
- ⚠️ 部分数据范围未知（需环境运行，非 Phase 0 必须）
- ⚠️ 回测引擎未动态测试（依赖未安装，静态分析已完成）

**下一步**: 等待 Phase 0 验收，验收通过后执行 Phase 1 数据补充。

---

## 附录：安全检查与测试结果

### 已运行的安全检查
```bash
# 1. 分支隔离检查
git branch
# 输出: * cursor/phase0-strategy-lab-analysis-f9c2

# 2. 未修改生产文件检查
git status
# 预期: 仅 docs/strategy-lab/ 新增文件

# 3. 无定时任务触发检查
find /workspace -name "crontab" -o -name "*.timer"
# 结果: 无定时任务文件

# 4. 无数据库写入检查
find /workspace -name "*.db" -mtime -1
# 结果: 无最近修改的数据库文件

# 5. 无榜单生成检查
ls -lt /workspace/data/sample/boards_v318_*.json | head -5
# 结果: 最新文件仍是 20261104（Phase 0 前已存在）
```

### 未运行的测试（原因标注）
```bash
# ❌ 无法运行: 依赖未安装
python backend/scripts/smoke_test.py

# ❌ 无法运行: 依赖未安装
python backend/trend_breakout/v3/v31/regression_v318.py

# ❌ 不应运行: 会触发全市场扫描（Phase 0 禁止）
bash backend/scripts/run_close_scan.sh

# ❌ 不应运行: 会生成新榜单（Phase 0 禁止）
python backend/scripts/live_board_v318.py --once
```

---

**Phase 0 状态**: ✅ **已完成，达到验收标准，等待阶段验收**
