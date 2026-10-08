# 趋势分析雷达 V3.1.8（Trend Confirmation Radar）

A 股趋势确认/突破 **观察系统** 的可运行工程包：Python 核心算法 + FastAPI + React（Vite）四榜前端。

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
  run_dev.sh           # 一键启动 API + 前端
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

**一键启动前后端**（推荐）：

```bash
# 首次请先完成「4. 安装」中的 venv 与 npm install
chmod +x run_dev.sh
./run_dev.sh
# 后端 http://127.0.0.1:8765/api/health
# 前端 http://127.0.0.1:5173
```

也可分两个终端分别启动：

```bash
bash backend/scripts/run_api.sh
cd frontend && npm run dev
```

前端会优先请求 API；失败时自动回退到 `frontend/public/sample/boards_v318_20261008.json`。

离线 smoke（不启服务）：

```bash
python backend/scripts/smoke_test.py
```

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
