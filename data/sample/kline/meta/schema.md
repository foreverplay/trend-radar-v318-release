# A股日线 K 线字段说明（样例包）

数据根目录：由环境变量 `ASHARE_KLINE_ROOT` 指定（本包默认 `data/sample/kline`）。

## 文件布局
- `raw/daily/{code}.parquet`：前复权（qfq）主数据，code 为 6 位数字代码
- `meta/universe_sample.csv`：样例股票宇宙
- `meta/schema.md`：本说明
- `scripts/download_sample_or_universe.py`：用 akshare 扩充本地 parquet

## universe_sample.csv
| 字段 | 说明 |
|------|------|
| code | 6 位股票代码 |
| symbol | 带市场前缀，如 sh600000 / sz000001 |
| name | 股票简称 |
| exchange | 交易所：SH / SZ / BJ |
| list_source | 清单来源 |

## raw/daily 行情列（前复权）
| 字段 | 说明 |
|------|------|
| date | 交易日期 |
| open/high/low/close | 前复权 OHLC |
| volume | 成交量（股） |
| amount | 成交额（元） |
| outstanding_share | 流通股本 |
| turnover | 换手率（小数） |
| change / pct_chg / amplitude / circ_mv | 衍生行情 |

## 衍生技术指标（若已写入）
MA5/10/20/30/60/120/250、EMA12/26、VOL_MA5/20、MACD_*、RSI14、ATR14

## 数据源（下载脚本）
- 主：akshare `stock_zh_a_daily`（新浪）
- 扫描侧多源failover 见包内 `docs/DATA_SOURCES.md`
