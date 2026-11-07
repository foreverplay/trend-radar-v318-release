# 趋势分析雷达 V3.1.8（Trend Confirmation Radar）

A 股趋势确认/突破 **观察系统** 的可运行工程包：Python 核心算法 + FastAPI + React（Vite）四榜前端；另含独立 **红利趋势三区（V3.1.9）**。

> **这是观察池，不是荐股软件。**  
> **禁止荐买 / 预测必涨。**  
> **A+ = 图形质量高，≠ 今天必买。**  
> **TrendGrade ⊥ EntryStatus（双轨）。**

详见 [LICENSE_NOTICE.md](./LICENSE_NOTICE.md)。

---

## 1. 这是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 日线趋势结构评分与四榜展示 | 买卖点荐股 / 收益承诺 |
| as-of 可复现的收盘发布流水线 | 实盘下单系统 |
| 多源行情 failover + 本地 parquet 缓存 | 完整 4.5G 全市场 K 线（本包仅样例） |

四榜规则：

| 榜 | 规则 |
|---|---|
| **成熟入场榜** | A+/A + PRIME/CONFIRMED |
| **趋势全榜** | A+/A/B/C |
| **观察池** | B/C + EARLY/BUILDING |
| **过热延伸** | A+/A + EXTENDED |

---

## 2. 目录结构

```
trend-radar-v318-release/
  README.md
  run_dev.sh           # 一键启动 API + 前端 + 盘中刷榜
  LICENSE_NOTICE.md
  requirements.txt
  pyproject.toml
  .env.example
  backend/
    trend_breakout/     # 核心算法（V2 scan + V3.1.8）
    api/                # FastAPI
    scripts/            # run_api / run_close_scan / smoke_test
  frontend/             # Vite + React + TS
  data/
    sample/             # 推送样例、boards JSON、12 只样例 parquet
    cache/              # turnover 等小缓存样例
  docs/                 # 策略 / 架构 / 数据源
```

---

## 3. 环境要求

- Python **3.11+**
- Node.js **18+**（前端）
- 可选：完整本地 K 线库（parquet）以跑全市场扫描

---

## 4. 安装

```bash
cd trend-radar-v318-release

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cd frontend && npm install && cd ..
```

复制环境变量模板：

```bash
cp .env.example .env
# 按需修改 ASHARE_KLINE_ROOT / TREND_BREAKOUT_ROOT
```

---

## 5. 快速体验（只用 sample，不跑全市场）

**一键启动前后端 + 盘中刷榜**（推荐）：

```bash
# 首次请先完成「4. 安装」中的 venv 与 npm install
chmod +x run_dev.sh
./run_dev.sh
# 后端 http://127.0.0.1:8765/api/health
# 前端 http://127.0.0.1:5173
# 盘中刷榜 live_board_v318.py（默认约每 25 分钟写 data/sample/boards_v318_*.json）
```

关闭盘中刷榜或改间隔：

```bash
./run_dev.sh --no-live-board          # 或 SKIP_LIVE_BOARD=1 ./run_dev.sh
LIVE_BOARD_INTERVAL=900 ./run_dev.sh  # 间隔改为 15 分钟（秒）
```

也可分两个终端分别启动：

```bash
bash backend/scripts/run_api.sh
cd frontend && npm run dev
```

前端会优先请求 API；失败时自动回退到 `frontend/public/sample/boards_v318_20261008.json`。

页面右上角可切换 `data/sample/boards_v318_YYYYMMDD.json` 中已有的历史日期。悬停或点击股票名称会在名称右侧弹出详情，箭头指向该代码，左侧名称可以继续切换，不用先关掉：所选日期的分时、日/月 K（含成交量与 MACD；日 K 和月 K 都默认叠加 MA5/10/20/60，可单独开关；鼠标移过时有十字线并标出当前时间，提示只留开高低收和成交量；点任一根 K 线会在下方展开该日 / 该月详情，含开高低收、涨跌与振幅、实体与影线、RSI/ATR、量能、均线乖离与排列、MACD 信号、区间涨跌与位置，日 K 另带当日资金流和异动原因，取不到时注明）、趋势分数和证据说明。分时 / K 线的指标和图表配置放在同级 `market-data-hub/web`，与 longtou-web 共用。异动原因与资金流仅在对应日期能取到时显示。

严格回算最近 10 个交易日。配置了 `MARKET_DB_PATH` 时，从共享行情库的前复权表导出，不再单独下载：

```bash
python backend/scripts/backfill_boards_v318.py --days 10 --source market-db
```

没有共享库时仍可 `--source tencent` 下载到 `data/kline_market/`。回算只使用截至该日的本地行情。股票清单是库里当时的当前清单；基本面以及无法按日还原的异动/资金字段不参与历史评分。成交额优先用共享库里的真实成交额，缺失时按收盘价×成交量估算。覆盖不足或最新日期不到该交易日时，脚本会停止，不写出榜单。

盘中出当天趋势榜（行情后台需在跑，它负责 30 分钟内写完全市场当日临时 K）：

```bash
# 推荐：已包含在 ./run_dev.sh（默认开启）
./run_dev.sh

# 或单独跑（不启前后端）：
python backend/scripts/live_board_v318.py
# 单次 / 非交易时段强制一轮：
python backend/scripts/live_board_v318.py --once --force
```

脚本等当天临时日 K 落库后，补进 `data/kline_market` 的当日最后一根，再按严格口径重算一遍。默认每 25 分钟一轮（`LIVE_BOARD_INTERVAL` / `--interval`），非交易时段空转等待，收盘后再用收盘快照重算一次。页面约每 90 秒自动拉最新榜（切到历史日期时暂停跟随）。页面上的说明会标明这是盘中临时行情。不想刷榜时用 `./run_dev.sh --no-live-board` 或 `SKIP_LIVE_BOARD=1`。

悬停行情在设置 `MARKET_HUB_URL`（默认 `http://127.0.0.1:8790`）后优先走共享行情后台，后台不可用时回退直连。`./run_dev.sh` 会在 8790 未监听且配置了 `MARKET_HUB_DIR` 时一并启动后台，并默认拉起盘中刷榜。

离线 smoke（不启服务）：

```bash
python backend/scripts/smoke_test.py
```

### A+ 历史回补（供 longtou 组合寻优）

在配置 `MARKET_DB_PATH` 指向共享 SQLite（如前复权 `daily_bars_qfq`）时，`./run_dev.sh` 默认另起后台 `backfill_a_plus_history.py`：

- 按交易日**从新到旧** strict-asof 重算，结果写入 `backend/trend_breakout/history/a_plus_v318_backfill_by_date/{日期}.csv`。
- **不写**现网 `store_v318.json`、不写 `data/sample/boards_v318_*.json`；可用 `SKIP_A_PLUS_BACKFILL=1` 或 `--no-a-plus-backfill` 关闭。
- **交易时段**（09:15–15:30）暂停，避免与盘中刷榜抢资源。
- 进度见 `history/a_plus_v318_backfill_status.json`。

口径说明：**股票清单为回补任务运行时可从库读到的当前 A 股列表**；已退市代码不会出现在历史档案中；**基本面、异动、资金等无法按日还原的字段不参与历史评分**。

手动：

```bash
export MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3
python backend/scripts/backfill_a_plus_history.py --once --only 2026-11-02,2026-11-03,2026-11-04,2026-11-05,2026-11-06 --compare-live
```

验收时请确认隔离目录 `run_day` 已把 `TREND_RANK_DB` / `ASHARE_KLINE_ROOT` 传给 publish，否则 v317 重放可能 `no_hist`、回补 A+ 数量为 0。2026-11-06 样例：现网约 31 只 A+，回补需与现网集合一致后再批量跑 750 日。

输出目录可用环境变量 `TREND_BREAKOUT_ROOT`（扫描隔离目录）、`TREND_HIST_DIR`（档案根目录，默认 `history`）。

---

## 6. 全量收盘流水线

1. 准备完整 K 线目录（布局同 `data/sample/kline`，`raw/daily/*.parquet`）。
2. 设置：

```bash
export ASHARE_KLINE_ROOT=/path/to/ashare-kline
export TREND_BREAKOUT_ROOT=$(pwd)/backend/trend_breakout
```

3. 运行：

```bash
bash backend/scripts/run_close_scan.sh
# 等价于：
#   cd backend/trend_breakout && python v2_3_scan.py
#   python -m v3.publish_close_v318
```

也可仅发布（已有 `candidates_v2_3.json` / `market_meta.json`）：

```bash
cd backend/trend_breakout
python -m v3.publish_close_v318
```

扩充样例 K 线：

```bash
export ASHARE_KLINE_ROOT=$(pwd)/data/sample/kline
python data/sample/kline/scripts/download_sample_or_universe.py \
  --universe-csv data/sample/kline/meta/universe_sample.csv
```

---

## 7. 核心算法逻辑（简要）

实现位于 `backend/trend_breakout/v3/`：

- **TrendState / TrendWave** — 趋势状态与波段结构  
- **TrendMaturity / RealizedTrend** — 成熟度与已实现涨幅（影响 Entry，不强制压低 TrendGrade）  
- **EntrySignal + entry_window** — PRIME/CONFIRMED/FOLLOW/EXTENDED/NO_ENTRY  
- **RapidMainRise** — 主升浪并行路径（Entry 最高 FOLLOW）  
- **grading_v318** — A+/A/B/C/WATCH/FILTER  
- **ExitEngine** — 保护价 / 破坏价 / ExitSignal  

策略细节：`docs/STRATEGY_V3_1_8_PLAN.md`。

回归：

```bash
cd backend/trend_breakout
python -m v3.v31.regression_v318
```

---

## 8. 数据库与缓存逻辑

### K 线（parquet）

- 根：`ASHARE_KLINE_ROOT`（默认包内 `data/sample/kline`）  
- 文件：`raw/daily/{code6}.parquet`  
- 字段：见 `data/sample/kline/meta/schema.md`

本包**不含**全市场 ~4.5G 日线；仅 12 只 fixtures。

### 多源 failover

见 `docs/DATA_SOURCES.md` / `backend/trend_breakout/DATA_SOURCES.md`。  
日线顺序：本地 parquet → 东财 hist → 新浪 daily → 腾讯 → spot 补 T。

### 缓存

见 `data/cache/README.md`：`turnover_daily.json`、`continuity_history/`、`hist_cache/`、`snap_cache/`、`exit_state/`。

---

## 9. 四榜规则（产品）

- 成熟入场榜：高精度窗口（可为空）  
- 趋势全榜：发现与跟踪（A+～C）  
- 观察池：图形开始变好、尚未成熟  
- 过热延伸：高质量但 Entry=EXTENDED  

---

## 10. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/meta` | 日期列表 + market_meta + 规则 |
| GET | `/api/boards/latest` | 最新四榜 |
| GET | `/api/boards/{date}` | 按日（`YYYY-MM-DD`） |
| POST | `/api/run/publish` | 默认关闭；请用 CLI |

---

## 开发说明

- **不要**把现网 `/workspace/trend-breakout` 的巨型 `candidates_*.json` / `all_results*.json` 提交进仓库。  
- 路径已通过 `backend/trend_breakout/paths.py` 与环境变量解耦。  
- 前端：`cd frontend && npm run build` 产出静态资源。

---

## 红利模块 V3.1.9（细分三区）

与 Growth **V3.1.8 四榜独立**：不改 `grading_v318` / 成熟入场规则；仅附加红利观察。

| 区（中文） | API id | 说明 |
|---|---|---|
| **优质红利** | `quality` | 可持续分红 + 质量优先 |
| **当前机会** | `opportunity` | 质量门槛内的介入条件综合（非最高股息率） |
| **风险观察** | `risk_watch` | 陷阱 / 高风险 / 趋势破坏 |

**目录**

- 引擎包：`backend/trend_breakout/dividend_v319/`
- 样例榜：`data/sample/dividend_board_YYYYMMDD.json`（及 `.md` / `_summary.json`）

**API**

- `GET /api/boards/dividend/latest`
- `GET /api/boards/dividend/{date}`（`YYYY-MM-DD`）
- `GET /api/meta` → `dividend_dates`、`dividend_boards_rules`、`modules.dividend`

**前端**

顶部切换「趋势观察」/「红利趋势」；红利模式下三 Tab 对应上述三区。禁止荐买 / 预测必涨。

> 当前 release 以 **sample 样例** 展示；全市场 Live 红利重算流水线尚未接入 `run_dev.sh`（Growth 收盘发布路径不变）。

