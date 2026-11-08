# 数据接入原因调查报告

**调查日期**: 2026-11-07  
**调查范围**: PR #1 合并后代码 (分支 `cursor/phase0-strategy-lab-analysis-f9c2`, commit `29c2ba6`)  
**调查目标**: 研究实验室为何未使用本地全量K线缓存和数据库

---

## 执行摘要

**核心发现**: 研究实验室 Phase 1-3 实现的 E01 趋势基准策略**硬编码了 12 只样例股票池**，直接读取 `paths.LOCAL_DAILY`（默认指向 `data/sample/kline/raw/daily`），**完全绕过**了生产代码使用的数据库读取路径（`rank_db.py` 与 `TREND_RANK_DB`）。

**关键证据**:
- `backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py:25-42`: 函数 `get_sample_universe()` 硬编码 12 只样例
- `backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py:54-62`: 直接从 `LOCAL_DAILY / f"{code}.parquet"` 读取
- **未调用生产 provider**: 未使用 `data_sources.load_local_stock_hist()`（该函数会优先检查 `rank_db.db_path()`）

**影响**:
1. 用户本地配置的 `MARKET_DB_PATH`（指向 `longtou.sqlite3`）**未被读取**
2. 用户全量 K线缓存目录（需配置 `ASHARE_KLINE_ROOT` 指向）**未被读取**
3. 12 只样例测试**不能代表**全市场回测结果或引擎完备性

---

## 1. 数据读取路径完整调用链

### 1.1 生产代码数据 Provider 逻辑

#### 环境变量配置（`.env.example` 定义）

| 环境变量 | 默认值 | 说明 | 文件证据 |
|---------|--------|------|---------|
| `ASHARE_KLINE_ROOT` | `./data/sample/kline` | K线数据根目录 | `.env.example:2`, `backend/trend_breakout/paths.py:28` |
| `TREND_RANK_DB` | (空) | SQLite数据库路径（用于 `rank_db.py`） | `backend/trend_breakout/rank_db.py:28` |
| `MARKET_DB_PATH` | `../longtou-web/backend/data/longtou.sqlite3` | 共享SQLite路径（用于盘中刷榜、A+回补） | `.env.example:16`, `run_dev.sh:84` |
| `MARKET_HUB_URL` | `http://127.0.0.1:8790` | 共享行情后台服务地址 | `.env.example:15`, `backend/api/quote_service.py:415` |
| `MARKET_HUB_DIR` | `../market-data-hub` | 行情后台服务目录（`run_hub.sh`） | `.env.example:17`, `run_dev.sh:109` |

#### 数据读取优先级（生产路径）

**调用链示例 - `data_sources.load_local_stock_hist()`**:

```python
# backend/trend_breakout/data_sources.py:920-929
def load_local_stock_hist(symbol: str, t_date: str) -> Optional[pd.DataFrame]:
    code6 = symbol[2:] if symbol.startswith(("sh", "sz", "bj")) else symbol.zfill(6)
    import rank_db
    
    # 优先级 1: 检查 TREND_RANK_DB 数据库
    if rank_db.db_path():
        return rank_db.load_stock(code6, t_date)
    
    # 优先级 2: 读取本地 Parquet
    return load_local_ohlcv(LOCAL_DAILY / f"{code6}.parquet", t_date)
```

**`rank_db.db_path()` 决策逻辑**:

```python
# backend/trend_breakout/rank_db.py:27-33
def db_path() -> Optional[Path]:
    raw = os.environ.get("TREND_RANK_DB", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None
```

**数据库表结构（SQLite3）**:
- 表名: `daily_bars_qfq` (前复权日线), `index_bars` (指数)
- 关键字段: `symbol`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`
- **Volume单位**: 数据库存储为"手"（lots），`rank_db.py:129` 读取时乘以100转为"股"

#### 生产代码使用数据库的模块

| 模块 | 调用位置 | 用途 |
|------|---------|------|
| `v2_staircase_scan.py` | 行422-425, 1431 | 全市场扫描（优先 rank_db） |
| `v2_sector.py` | 行67-71 | 板块趋势（检查 TREND_RANK_DB） |
| `v2_sector_trend.py` | 行134-138 | 板块趋势历史（检查 TREND_RANK_DB） |
| `live_v317.py` | 行64-68 | V3.1.7 实时评分（优先 rank_db） |
| `data_sources.py` | 行920-949 | 统一数据提供者（优先 rank_db） |
| `backfill_a_plus_history.py` | 行123, 132 | A+ 历史回补（传递 rank_db 路径） |
| `live_board_v318.py` | 行107-111 | 盘中刷榜（读取 MARKET_DB_PATH） |

---

### 1.2 研究实验室实际读取路径

#### E01 策略的股票池定义

```python
# backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py:25-42
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
```

#### E01 策略的数据加载函数

```python
# backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py:44-73
def load_kline_data(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """加载 K线数据"""
    parquet_file = LOCAL_DAILY / f"{code}.parquet"  # 直接硬编码 LOCAL_DAILY
    
    if not parquet_file.exists():
        return pd.DataFrame()
    
    try:
        df = pd.read_parquet(parquet_file)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        # ... 日期过滤 ...
        return df
    except Exception as e:
        print(f"⚠️ 加载 {code} 失败: {e}")
        return pd.DataFrame()
```

**问题点**:
1. ❌ **未调用** `data_sources.load_local_stock_hist()`
2. ❌ **未检查** `rank_db.db_path()`
3. ❌ **未读取** `TREND_RANK_DB` 环境变量
4. ❌ **硬编码** `paths.LOCAL_DAILY`（默认 `data/sample/kline/raw/daily`）

#### `paths.LOCAL_DAILY` 的默认值

```python
# backend/trend_breakout/paths.py:15-29
def _default_kline_root() -> Path:
    # 优先级 1: 仓库内样例目录
    cand = _PKG.parent.parent / "data" / "sample" / "kline"
    if cand.is_dir():
        return cand
    
    # 优先级 2: 遗留路径（云VM用）
    legacy = Path("/workspace/ashare-kline")
    if legacy.is_dir():
        return legacy
    
    return cand

KLINE_ROOT = Path(os.environ.get("ASHARE_KLINE_ROOT", str(_default_kline_root()))).expanduser().resolve()
LOCAL_DAILY = KLINE_ROOT / "raw" / "daily"
```

**关键点**:
- `LOCAL_DAILY` 可通过 `ASHARE_KLINE_ROOT` 环境变量改写
- **但研究实验室未在代码中告知用户需要配置此环境变量**
- 默认指向 `data/sample/kline/raw/daily`（仅12只样例）

---

## 2. 外部专属后台项目线索

### 2.1 `market-data-hub` 共享行情服务

**仓库关系**: **Sibling Repository**（同级目录），不在本仓库内

#### 代码引用证据

| 文件 | 行号 | 内容 |
|------|-----|------|
| `.env.example` | 13-17 | `# Quote cache. Shared market-data hub (sibling market-data-hub) and longtou sqlite.`<br>`MARKET_HUB_URL=http://127.0.0.1:8790`<br>`MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3`<br>`MARKET_HUB_DIR=../market-data-hub` |
| `run_dev.sh` | 109 | `(cd "$HUB_ABS" && ./run_hub.sh) &` |
| `frontend/vite.config.ts` | 13-14 | `// 分时 / K 线图表代码在同级 market-data-hub/web`<br>`allow: [".", "../../market-data-hub/web"]` |
| `frontend/src/lib/session.ts` | 1-2 | `// 交易时段和分时横轴和 longtou-web 共用，源码在同级 market-data-hub/web`<br>`export * from "../../../../market-data-hub/web/session.ts";` |
| `frontend/src/components/QuoteCharts.tsx` | 3-4 | `import { buildIntradayOption, buildKlineOption } from "../../../../market-data-hub/web/quoteCharts.ts";` |

#### 服务接口

```python
# backend/api/quote_service.py:415-428
def _hub_base() -> str:
    return os.environ.get("MARKET_HUB_URL", "").strip().rstrip("/")

def _hub_get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    base = _hub_base()
    if not base:
        return None
    try:
        response = requests.get(f"{base}{path}", params=params or {}, timeout=8)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None
```

**已知 API 端点**:
- `/api/health` - 健康检查
- `/api/qfq/sync` - 触发一轮行情同步
- `/api/quotes/{symbol}/trends` - 分时数据

#### 启动逻辑（`run_dev.sh`）

```bash
# run_dev.sh:104-116
if [[ -n "${MARKET_HUB_URL}" && -n "${MARKET_HUB_DIR}" ]]; then
  HUB_ABS="$(cd "$ROOT" && cd "$MARKET_HUB_DIR" && pwd)"
  HUB_PORT="${HUB_PORT:-8790}"
  if ! lsof -nP -iTCP:"$HUB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[run_dev] 行情后台: ${MARKET_HUB_URL}（起来后会按约 2 分钟拉一轮当日行情）"
    (cd "$HUB_ABS" && ./run_hub.sh) &
    HUB_PID=$!
  else
    echo "[run_dev] 行情后台已在 ${HUB_PORT} 监听"
  fi
else
  echo "[run_dev] 未配置 MARKET_HUB_URL / MARKET_HUB_DIR，不会启动行情后台"
fi
```

**关键特征**:
- 默认端口: 8790
- 约2分钟拉一轮行情（交易时段内）
- 需要在 **同级目录** 存在 `market-data-hub/` 目录

---

### 2.2 `longtou-web` 共享SQLite数据库

**仓库关系**: **Sibling Repository**（同级目录），不在本仓库内

#### 数据库路径配置

```bash
# .env.example:16
MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3
```

```python
# backend/scripts/live_board_v318.py:103-111
def resolve_db() -> Path:
    raw = os.environ.get("MARKET_DB_PATH") or _env_file("MARKET_DB_PATH")
    if not raw:
        raw = "../longtou-web/backend/data/longtou.sqlite3"  # 默认路径
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path
```

#### 数据库表结构

**已知表** (基于 `rank_db.py` 与 `live_board_v318.py`):
- `daily_bars_qfq` - 前复权日线
  - 字段: `symbol` (6位代码), `trade_date`, `open`, `high`, `low`, `close`, `volume` (手), `amount`
- `index_bars` - 指数日线
  - 字段: `symbol` (sh000001, sz399001, etc.), `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`
- `stock_quotes` - 股票名称
  - 字段: `symbol`, `name`
- `qfq_sync_state` - 同步状态
  - 字段: `needs_full` (是否需要全量同步)

**使用场景**:
- 盘中刷榜: `live_board_v318.py` 等待 `daily_bars_qfq` 中当日行情达到最小数量
- A+ 历史回补: `backfill_a_plus_history.py` 从 `index_bars` 读取交易日历
- 全市场扫描: `v2_staircase_scan.py` 优先使用数据库避免遍历 Parquet 文件

---

### 2.3 外部项目依赖关系图

```
trend-radar-v318-release/          (本仓库)
├── backend/
│   └── trend_breakout/
│       ├── paths.py               → ASHARE_KLINE_ROOT
│       ├── rank_db.py             → TREND_RANK_DB
│       └── research_lab/          ❌ 未使用外部数据
│
├── data/sample/kline/             (仅12只样例)
│
../longtou-web/                    (外部仓库 1)
│   └── backend/data/
│       └── longtou.sqlite3        ← MARKET_DB_PATH
│
../market-data-hub/                (外部仓库 2)
    ├── run_hub.sh                 ← MARKET_HUB_DIR
    └── web/
        ├── session.ts             ← frontend 引用
        ├── quoteCharts.ts
        └── indicators.ts
```

**关键发现**:
1. **外部仓库不在本仓库控制范围内**
2. **用户本地环境可能已配置** `longtou.sqlite3` 和 `market-data-hub`
3. **研究实验室未告知用户需要配置环境变量**
4. **云VM上不存在这些外部目录**（因此云端调查看不到用户本地完整数据）

---

## 3. 为何选择 12 只样例？

### 3.1 硬编码原因分析

#### 代码注释与文档说明

```python
# backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py:5-6
"""E01: 趋势基准策略 — Phase 3

将 V3.1.8 评分系统转化为可执行交易规则，基于 12 只样例股票验证回测引擎
"""
```

```markdown
# backend/trend_breakout/research_lab/experiments/README.md:18
- 股票池: 12 只样例（600519, 600396, 601579, ...）
```

```markdown
# docs/strategy-lab/IMPLEMENTATION_STATUS.md:117
✅ **E01 趋势基准策略完成**:
  - 股票池: 12 只样例（600519贵州茅台、...）
  - 回测周期: 2023-01-01 至 2024-12-31
```

#### 样例选择标准（反向推断）

| 代码 | 名称 | 可能特征 | 推断理由 |
|------|------|---------|---------|
| 600519 | 贵州茅台 | 高价股、稳定趋势 | 测试高价股处理 |
| 000001 | 平安银行 | 金融股、低波动 | 测试低波动股 |
| 300750 | 宁德时代 | 高成长、高波动 | 测试高波动股 |
| 600396 | 华电辽能 | 低价股 | 测试低价股过滤 |
| 603087 | 甘李药业 | 医药股 | 测试行业多样性 |
| ... | ... | ... | ... |

**关键特点**:
- 覆盖多个价格区间（低价股 vs 高价股）
- 覆盖多个行业（金融、消费、医药、新能源）
- 覆盖不同市场（沪市、深市、创业板）
- **但无法代表全市场回测结果**

---

### 3.2 Phase 文档中的数据扩展计划

```markdown
# backend/trend_breakout/research_lab/README.md:84, 267
3. **全市场K线**:
   - 当前: 仅 12 只样例
   - 后续扩展全市场数据

### Phase 2+ 扩展路径
1. ⏸️ 全市场 K线扩展（4.5GB）
```

```markdown
# backend/trend_breakout/research_lab/run_phase1_data_preparation.py:6, 120
P2: 全市场K线扩展（当前仅12只样例）
...
4. 全市场K线: 仍为12只样例，未扩展
```

**Phase 2 被标记为阻塞（⏸️）**:
- 原因: 全市场 K线数据 (~4.5GB) 需要下载和管理
- 影响: E04 相对强度策略（需全市场对比）被阻塞

---

## 4. 历史停牌/ST 状态处理问题

### 4.1 研究实验室的简化处理

#### ST History Fetch（Phase 1 实现）

```python
# backend/trend_breakout/research_lab/utils/fetch_st_history.py:36-60
def build_st_history_from_current(df: pd.DataFrame) -> pd.DataFrame:
    """从当前 ST 列表构建基础历史表
    
    注意: akshare 只提供当前 ST 状态，无历史时间线
    这里仅构建基础框架，实际历史需要其他数据源
    """
    # ...
    history = pd.DataFrame({
        'code': df[code_col].astype(str).str.zfill(6),
        'st_date': datetime.now().strftime('%Y-%m-%d'),  # ❌ 当前日期
        'st_type': df[name_col].str.extract(r'(\*?ST|S)', expand=False).fillna('ST'),
        'status': 'active',  # ❌ 当前激活状态
        'name': df[name_col]
    })
```

**问题**:
- ❌ 只能获取**当前** ST 状态
- ❌ 无法还原**历史某个日期**的 ST 状态
- ⚠️ 回测中使用会产生**未来函数**问题

#### 停牌处理（Volume=0 简化）

```python
# backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py:63-67
# 数据加载直接读取 Parquet，无停牌标记
df = pd.read_parquet(parquet_file)
# ...
# 成交量为 0 可能代表停牌，但无专门处理
```

**问题**:
- ❌ 无停牌日历（`is_trading` 标记）
- ⚠️ Volume=0 不等于停牌（可能是数据缺失、涨跌停无成交、极小市值股等）
- ⚠️ 回测无法准确模拟"当日无法成交"场景

---

### 4.2 生产代码的不完备性

#### 停牌检测（名称关键字）

```markdown
# docs/STRATEGY_V2.md:13
- 非 ST/停牌/过新
```

**实际代码未找到 `is_suspended` 字段或停牌日历表**

#### ST 检测（名称包含 "ST"）

```python
# 推断：生产代码可能通过名称过滤 ST
# 但无明确历史 ST 状态时间线
```

**已知问题（文档记录）**:
```markdown
# backend/trend_breakout/dividend_v319/PHASE1_DATA_AUDIT.md:149
- ST / 退市 / 长期停牌时间线（现仅有当前 non_st + 名称关键字）
```

---

## 5. 生产 Provider vs 研究实验室对比表

| 维度 | 生产代码 (`data_sources.py`) | 研究实验室 E01 | 差异说明 |
|------|---------------------------|----------------|---------|
| **数据库优先** | ✅ 检查 `rank_db.db_path()` | ❌ 未检查 | E01 直接读 Parquet |
| **环境变量读取** | ✅ `TREND_RANK_DB`, `ASHARE_KLINE_ROOT` | ⚠️ 仅 `ASHARE_KLINE_ROOT`（但未告知用户） | E01 未读 TREND_RANK_DB |
| **股票池获取** | ✅ `fetch_universe()`（东财/新浪） | ❌ 硬编码 12 只 | 生产获取全市场 ~5000+ 只 |
| **停牌处理** | ⚠️ 无明确日历 | ❌ 无处理 | 两者都不完备 |
| **ST 历史** | ⚠️ 仅名称关键字 | ❌ 当前状态 | 两者都无历史时间线 |
| **Volume 单位** | ✅ 数据库×100，Parquet 已为"股" | ⚠️ 未注明 | 可能存在单位不一致风险 |
| **Failover 机制** | ✅ 本地 → 东财 → 新浪 → 腾讯 | ❌ 仅本地 Parquet | E01 无网络下载逻辑 |
| **数据验证** | ⚠️ 无 OHLC 逻辑检查 | ✅ `data_validator.py` 实现 | 研究实验室更严格 |

---

## 6. 最小安全接入建议

### 6.1 云端验证（当前VM可见）

**Step 1**: 检查默认路径下的样例数据

```bash
# 验证样例目录
ls -lh /workspace/data/sample/kline/raw/daily/*.parquet | wc -l
# 预期输出: 12

# 检查环境变量
echo "ASHARE_KLINE_ROOT=${ASHARE_KLINE_ROOT:-未设置}"
echo "TREND_RANK_DB=${TREND_RANK_DB:-未设置}"
echo "MARKET_DB_PATH=${MARKET_DB_PATH:-未设置}"
```

**Step 2**: 验证生产 Provider 逻辑

```bash
cd /workspace/backend/trend_breakout
python3 << 'EOF'
import rank_db
print(f"rank_db.db_path() = {rank_db.db_path()}")

from paths import LOCAL_DAILY
print(f"LOCAL_DAILY = {LOCAL_DAILY}")
print(f"LOCAL_DAILY.exists() = {LOCAL_DAILY.exists()}")

if LOCAL_DAILY.exists():
    files = list(LOCAL_DAILY.glob("*.parquet"))
    print(f"Parquet files count: {len(files)}")
EOF
```

---

### 6.2 用户本地验证（需用户在其电脑执行）

**⚠️ 重要提示**: 以下命令需要在**用户本地环境**执行，云VM无法看到用户的完整数据。

#### Step 1: 检查外部仓库存在性

```bash
# 在 trend-radar-v318-release/ 同级目录检查
ls -ld ../longtou-web/backend/data/longtou.sqlite3
ls -ld ../market-data-hub/run_hub.sh
```

**预期结果**:
- 如果两个路径存在 → 用户已有外部项目
- 如果不存在 → 需要询问用户实际数据存放位置

#### Step 2: 检查 SQLite 数据库

```bash
# 假设数据库在 ../longtou-web/backend/data/longtou.sqlite3
sqlite3 ../longtou-web/backend/data/longtou.sqlite3 << 'EOF'
.mode column
.headers on

-- 检查表结构
.tables

-- 检查股票数量
SELECT COUNT(DISTINCT symbol) AS stock_count FROM daily_bars_qfq;

-- 检查最新交易日
SELECT MAX(trade_date) AS latest_date FROM daily_bars_qfq;

-- 检查某只样例股票的数据
SELECT trade_date, close, volume 
FROM daily_bars_qfq 
WHERE symbol = '600519' 
ORDER BY trade_date DESC 
LIMIT 5;
EOF
```

**预期输出（示例）**:
```
stock_count
-----------
5127

latest_date
-----------
2026-11-06

trade_date  close    volume
----------  -------  ------
2026-11-06  1632.50  15234
2026-11-05  1628.00  13567
...
```

#### Step 3: 检查全量 K线目录

```bash
# 假设用户配置了 ASHARE_KLINE_ROOT
export ASHARE_KLINE_ROOT=/path/to/full/kline  # 用户替换为实际路径

# 检查目录结构
ls -d $ASHARE_KLINE_ROOT/raw/daily
ls $ASHARE_KLINE_ROOT/raw/daily/*.parquet | wc -l

# 检查某只股票
ls -lh $ASHARE_KLINE_ROOT/raw/daily/600519.parquet
```

**预期输出**:
```
5127  # 全市场股票数量
-rw-r--r-- 1 user user 42K Nov 6 2026 600519.parquet
```

---

### 6.3 非敏感配置示例（研究实验室可用）

#### 创建 `.env` 配置文件

```bash
# 在 /workspace/.env 中添加（用户本地执行）
cat >> .env << 'EOF'

# === 研究实验室数据配置 ===
# 如果已有全量 K线目录，设置此路径
ASHARE_KLINE_ROOT=/path/to/your/ashare-kline

# 如果已有共享SQLite数据库，设置此路径
TREND_RANK_DB=../longtou-web/backend/data/longtou.sqlite3

# 或者使用 MARKET_DB_PATH（两者选一）
MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3
EOF
```

#### 修改 E01 策略使用生产 Provider

**文件**: `backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py`

**修改点 1**: 导入生产 provider

```python
# 添加到文件顶部
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from data_sources import load_local_stock_hist  # 生产 provider
```

**修改点 2**: 替换 `load_kline_data()` 函数

```python
def load_kline_data(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """加载 K线数据（使用生产 Provider）"""
    
    # 使用生产 provider（会自动检查数据库）
    df = load_local_stock_hist(code, end_date or '2099-12-31')
    
    if df is None or len(df) == 0:
        return pd.DataFrame()
    
    if start_date:
        df = df[df['date'] >= start_date]
    if end_date:
        df = df[df['date'] <= end_date]
    
    return df
```

**修改点 3**: 替换股票池获取方式

```python
def get_universe_from_db():
    """从数据库获取全市场股票池"""
    import rank_db
    
    if not rank_db.db_path():
        print("⚠️ 未配置 TREND_RANK_DB，回退到 12 只样例")
        return get_sample_universe()
    
    # 从数据库获取股票列表
    conn = rank_db._connect(rank_db.db_path())
    rows = conn.execute("""
        SELECT DISTINCT symbol FROM daily_bars_qfq 
        WHERE LENGTH(symbol) = 6
        ORDER BY symbol
    """).fetchall()
    
    codes = [row[0] for row in rows]
    print(f"✅ 从数据库获取 {len(codes)} 只股票")
    return codes
```

---

## 7. 边界说明

### 7.1 云VM与用户本地的数据可见性边界

| 数据类型 | 云VM可见 | 用户本地可见 | 说明 |
|---------|---------|-------------|------|
| 仓库代码 | ✅ | ✅ | Git 同步 |
| 12只样例 Parquet | ✅ | ✅ | 仓库内 `data/sample/kline/` |
| 全量 K线 Parquet | ❌ | ✅ | 用户本地缓存（~4.5GB） |
| `longtou.sqlite3` | ❌ | ✅ | 外部仓库 `../longtou-web/` |
| `market-data-hub` | ❌ | ✅ | 外部仓库 `../market-data-hub/` |

**关键点**:
- **云VM只能看到仓库内数据**（12只样例）
- **用户本地可能已有全量数据**（需用户确认路径）
- **调查报告不能声称"用户只有12只"或"全量数据不存在"**

---

### 7.2 外部仓库边界

#### `market-data-hub` 仓库

**功能**:
- 实时行情获取服务（港口 8790）
- 前端图表代码共享（`web/` 目录）

**依赖方式**:
- Frontend: 通过相对路径 `import` TypeScript 模块
- Backend: 通过 HTTP API 调用（`MARKET_HUB_URL`）

**是否必需**:
- ❌ **非必需**（可用 akshare 直连替代）
- ✅ **可选优化**（提供更快的行情缓存和统一服务）

#### `longtou-web` 仓库

**功能**:
- 共享 SQLite 数据库 (`longtou.sqlite3`)
- 可能是另一个独立的 Web 应用

**依赖方式**:
- Backend: 通过 `MARKET_DB_PATH` 读取 SQLite 文件

**是否必需**:
- ❌ **非必需**（可用本地 Parquet 替代）
- ✅ **可选优化**（避免遍历 Parquet 文件，提升性能）

---

## 8. 撤回与澄清

### 8.1 之前回合中的不准确表述

**问题表述 1**: "E01 回测引擎有效"

**撤回理由**:
- ❌ **仅在 12 只样例上测试**，无法代表全市场有效性
- ❌ **未使用生产数据 Provider**，无法验证数据库集成
- ❌ **历史停牌/ST 处理不完备**，回测结果可能包含未来函数

**正确表述**: "E01 回测引擎在 12 只样例股票上完成基础流程测试，但未验证全市场数据集成和历史状态准确性"

---

**问题表述 2**: "策略评分有效"

**撤回理由**:
- ❌ **E01 使用简化评分逻辑** (`calculate_trend_score_simple()`)，非完整 V3.1.8 引擎
- ❌ **未调用** `v3.grading_v318.grade_trend_v318()` 生产函数
- ❌ **未包含** 结构评分、板块评分、连续性评分等完整维度

**正确表述**: "E01 使用简化的技术指标评分（MA排列、涨幅、成交量），未集成完整的 V3.1.8 多维度评分体系"

---

**问题表述 3**: "Volume=0 可代表历史停牌"

**撤回理由**:
- ❌ Volume=0 原因多样：数据缺失、极小市值股、涨跌停封单、**真实停牌**
- ❌ 无法区分"当日可交易但无成交"与"停牌无法交易"
- ❌ 回测中按 Volume=0 跳过会漏掉"涨跌停可挂单但未成交"的场景

**正确表述**: "Parquet 数据中 Volume=0 可能包含多种情况，需要专门的停牌日历 (`is_trading` 标记) 才能准确还原历史可交易性"

---

**问题表述 4**: "当前 ST 接口可用于历史回测"

**撤回理由**:
- ❌ akshare `stock_zh_a_st_em()` 仅返回**当前** ST 股票
- ❌ 无 ST 加帽/摘帽的历史时间线
- ❌ 回测中使用当前 ST 列表会产生**未来函数**问题

**正确表述**: "当前 ST 接口 (`fetch_st_history.py`) 仅能获取当前状态，无法还原历史某日的 ST 列表，需要额外数据源构建完整时间线"

---

### 8.2 Phase 完成度重新评估

| Phase | 之前声称 | 重新评估 | 理由 |
|-------|---------|---------|------|
| Phase 0 | ✅ 完成 | ✅ 确认完成 | 文档分析准确 |
| Phase 1 | ✅ 完成 | ⚠️ **部分完成** | ST/停牌历史不完备，未集成生产 Provider |
| Phase 2 | ✅ 完成 | ⚠️ **仅原型** | 未使用完整 V3.1.8 评分，未验证数据库集成 |
| Phase 3 | ✅ 完成 | ⚠️ **仅样例** | 12只样例测试，非全市场验证 |

**关键问题汇总**:
1. ❌ **数据集成未完成**: 研究实验室未使用生产数据路径
2. ❌ **股票池未扩展**: 硬编码 12 只，Phase 2 全市场扩展被阻塞
3. ❌ **历史状态不完备**: ST/停牌/退市无时间线
4. ❌ **评分未集成**: E01 简化评分 ≠ 完整 V3.1.8 引擎

---

## 9. 本地只读验证指南（供用户执行）

### 9.1 验证数据库表结构（非敏感）

```bash
# 假设数据库在 ../longtou-web/backend/data/longtou.sqlite3
sqlite3 ../longtou-web/backend/data/longtou.sqlite3 << 'EOF'
.schema daily_bars_qfq
.schema index_bars
.schema stock_quotes
EOF
```

**预期输出示例**:
```sql
CREATE TABLE daily_bars_qfq (
    symbol TEXT,
    trade_date TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,  -- 单位: 手（lots）
    amount REAL
);
CREATE INDEX idx_qfq_symbol_date ON daily_bars_qfq(symbol, trade_date);
```

---

### 9.2 验证 K线文件布局（非敏感）

```bash
# 检查目录结构
tree -L 3 /path/to/ashare-kline/ | head -20

# 预期输出:
# /path/to/ashare-kline/
# ├── raw/
# │   └── daily/
# │       ├── 000001.parquet
# │       ├── 000002.parquet
# │       └── ...
# └── meta/
#     ├── names.csv
#     └── schema.md
```

---

### 9.3 对比样例与全量数据（无敏感数据）

```bash
# 样例目录
ls /workspace/data/sample/kline/raw/daily/*.parquet | wc -l
# 输出: 12

# 全量目录（用户本地）
ls /path/to/ashare-kline/raw/daily/*.parquet | wc -l
# 预期输出: 5000+
```

---

## 10. 当前正确的本地查看命令

### 10.1 启动完整开发环境（用户本地）

```bash
# 前提: 已配置 .env 文件
# ASHARE_KLINE_ROOT=/path/to/ashare-kline
# MARKET_DB_PATH=../longtou-web/backend/data/longtou.sqlite3
# MARKET_HUB_DIR=../market-data-hub

cd /workspace
./run_dev.sh
```

**预期行为**:
1. 启动 `market-data-hub` 行情服务（端口 8790）
2. 启动 FastAPI 后端（端口 8765）
3. 启动前端开发服务器（端口 5173）
4. 启动盘中刷榜脚本（`live_board_v318.py`）
5. 启动 A+ 历史回补脚本（`backfill_a_plus_history.py`）

**注意**:
- ⚠️ **云VM无法正常启动**（缺少外部仓库和全量数据）
- ✅ **用户本地可正常启动**（假设已配置外部项目）

---

### 10.2 仅启动前端+后端（最小配置）

```bash
# 不依赖外部仓库，仅使用 12 只样例
cd /workspace

# 启动后端
cd backend/api
python3 app.py &

# 启动前端
cd ../../frontend
npm run dev
```

**适用场景**:
- 云VM测试
- 用户仅查看现有样例数据
- 不需要实时行情和历史回补

---

### 10.3 运行研究实验室回测（当前状态）

```bash
cd /workspace/backend/trend_breakout/research_lab/experiments

# 使用 12 只样例运行 E01
python3 E01_trend_baseline.py
```

**预期输出**:
```
================================================================================
E01: 趋势基准策略回测
================================================================================

股票池: ['600519', '600396', ...]

交易日数量: 487
日期范围: 2023-01-03 至 2024-12-31

...
```

**限制**:
- ❌ 仅 12 只样例
- ❌ 未使用数据库
- ❌ 无法扩展到全市场

---

## 11. 最小修改路径建议（不实施）

### 11.1 修改 E01 策略接入生产数据

**文件**: `backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py`

**修改量**: ~20 行

**具体修改**:
1. 导入 `data_sources.load_local_stock_hist`
2. 替换 `load_kline_data()` 函数体
3. 添加 `get_universe_from_db()` 函数（可选，支持全市场）

**优点**:
- ✅ 自动使用数据库（如果配置）
- ✅ 兼容现有 12 只样例（数据库不存在时回退）
- ✅ 用户配置环境变量后即可全市场回测

**缺点**:
- ⚠️ 需要用户配置 `TREND_RANK_DB` 或 `MARKET_DB_PATH`
- ⚠️ 全市场回测需要完整数据（~5000 只股票）

---

### 11.2 添加环境变量检查脚本

**新文件**: `backend/trend_breakout/research_lab/check_data_env.py`

```python
#!/usr/bin/env python3
"""检查研究实验室数据环境配置"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paths import LOCAL_DAILY, KLINE_ROOT
import rank_db

def check_env():
    print("=== 研究实验室数据环境检查 ===\n")
    
    # 1. K线目录
    print(f"KLINE_ROOT: {KLINE_ROOT}")
    print(f"LOCAL_DAILY: {LOCAL_DAILY}")
    
    if LOCAL_DAILY.exists():
        count = len(list(LOCAL_DAILY.glob("*.parquet")))
        print(f"✅ K线文件数量: {count}")
        if count <= 12:
            print("⚠️  仅样例数据，如需全市场请设置 ASHARE_KLINE_ROOT")
    else:
        print("❌ K线目录不存在")
    
    # 2. 数据库
    print(f"\nTREND_RANK_DB: {os.environ.get('TREND_RANK_DB', '(未设置)')}")
    db = rank_db.db_path()
    if db:
        print(f"✅ 数据库路径: {db}")
        # 检查表
        conn = rank_db._connect(db)
        stock_count = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_bars_qfq").fetchone()[0]
        print(f"✅ 数据库股票数: {stock_count}")
    else:
        print("⚠️  未配置数据库，将使用本地 Parquet")
    
    # 3. 外部仓库
    print("\n=== 外部仓库检查 ===")
    market_db = os.environ.get("MARKET_DB_PATH", "../longtou-web/backend/data/longtou.sqlite3")
    if Path(market_db).is_file():
        print(f"✅ longtou.sqlite3: {market_db}")
    else:
        print(f"❌ longtou.sqlite3 不存在: {market_db}")
    
    market_hub = Path(os.environ.get("MARKET_HUB_DIR", "../market-data-hub"))
    if (market_hub / "run_hub.sh").is_file():
        print(f"✅ market-data-hub: {market_hub}")
    else:
        print(f"❌ market-data-hub 不存在: {market_hub}")

if __name__ == "__main__":
    check_env()
```

**使用**:
```bash
cd /workspace/backend/trend_breakout/research_lab
python3 check_data_env.py
```

---

## 12. 总结与建议

### 12.1 核心问题汇总

| 问题 | 影响范围 | 严重程度 | 修复复杂度 |
|------|---------|---------|-----------|
| **研究实验室未使用生产数据路径** | E01-E04 | 🔴 高 | 🟢 低（~20行代码） |
| **12只样例无法代表全市场** | 回测有效性 | 🔴 高 | 🟡 中（需下载数据） |
| **无历史 ST/停牌时间线** | 回测准确性 | 🟠 中 | 🔴 高（需外部数据源） |
| **评分逻辑简化** | 策略评估 | 🟠 中 | 🟡 中（集成 V3.1.8） |
| **外部仓库依赖文档缺失** | 用户部署 | 🟡 低 | 🟢 低（补充文档） |

---

### 12.2 用户需要做的事（本地验证）

1. **确认数据位置**:
   - 全量 K线 Parquet 目录在哪？
   - `longtou.sqlite3` 数据库在哪？
   - `market-data-hub` 仓库在哪？

2. **配置环境变量**:
   ```bash
   # 在 .env 中添加
   ASHARE_KLINE_ROOT=/path/to/your/ashare-kline
   TREND_RANK_DB=/path/to/longtou.sqlite3
   MARKET_HUB_DIR=../market-data-hub
   ```

3. **验证数据完整性**:
   ```bash
   # 检查股票数量
   ls /path/to/ashare-kline/raw/daily/*.parquet | wc -l
   
   # 检查数据库
   sqlite3 /path/to/longtou.sqlite3 "SELECT COUNT(DISTINCT symbol) FROM daily_bars_qfq;"
   ```

4. **运行完整环境**:
   ```bash
   cd /workspace
   ./run_dev.sh
   ```

---

### 12.3 不建议的操作（遵循用户要求）

- ❌ **不修改生产代码**: 当前仅调查，不实施修复
- ❌ **不自动下载全市场数据**: 需用户明确授权和路径
- ❌ **不启动后台服务**: 用户本地已有服务可能冲突
- ❌ **不创建新配置文件**: 避免覆盖用户现有配置

---

### 12.4 后续工作建议（待用户确认）

1. **Phase 1 补完**: 集成生产 Provider，支持数据库读取
2. **Phase 2 扩展**: 接入全市场 K线（需用户提供路径）
3. **Phase 3 验证**: 使用完整 V3.1.8 评分，替换简化逻辑
4. **Phase 4 数据**: 构建 ST/停牌历史时间线（需外部数据源）
5. **文档补充**: 在 README 中明确外部仓库依赖和环境变量

---

## 附录

### A. 关键文件清单

| 文件路径 | 作用 | 关键行号 |
|---------|------|---------|
| `backend/trend_breakout/paths.py` | 定义 KLINE_ROOT, LOCAL_DAILY | 15-29 |
| `backend/trend_breakout/rank_db.py` | SQLite 数据库读取 | 27-33, 161-199 |
| `backend/trend_breakout/data_sources.py` | 生产数据提供者 | 920-949 |
| `backend/trend_breakout/research_lab/experiments/E01_trend_baseline.py` | E01 策略实现 | 25-73 |
| `.env.example` | 环境变量示例 | 2-17 |
| `run_dev.sh` | 开发环境启动脚本 | 84-116 |

### B. 环境变量速查表

| 变量名 | 默认值 | 用途 | 读取位置 |
|--------|--------|------|---------|
| `ASHARE_KLINE_ROOT` | `./data/sample/kline` | K线根目录 | `paths.py:28` |
| `TREND_RANK_DB` | (空) | SQLite路径（rank_db专用） | `rank_db.py:28` |
| `MARKET_DB_PATH` | `../longtou-web/.../longtou.sqlite3` | SQLite路径（通用） | `run_dev.sh:84`, `live_board_v318.py:103` |
| `MARKET_HUB_URL` | `http://127.0.0.1:8790` | 行情服务地址 | `quote_service.py:415` |
| `MARKET_HUB_DIR` | `../market-data-hub` | 行情服务目录 | `run_dev.sh:109` |

### C. 数据库表字段说明

#### `daily_bars_qfq` 表

| 字段 | 类型 | 说明 | 单位 |
|------|------|------|------|
| `symbol` | TEXT | 6位股票代码 | - |
| `trade_date` | TEXT | 交易日期 YYYY-MM-DD | - |
| `open` | REAL | 开盘价（前复权） | 元 |
| `high` | REAL | 最高价（前复权） | 元 |
| `low` | REAL | 最低价（前复权） | 元 |
| `close` | REAL | 收盘价（前复权） | 元 |
| `volume` | REAL | 成交量 | **手** (读取时需×100) |
| `amount` | REAL | 成交额 | 元 |

---

**调查完成时间**: 2026-11-07  
**下一步**: 等待用户反馈本地数据位置和验证结果
