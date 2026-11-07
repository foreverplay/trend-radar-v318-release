# Phase2 TotalReturn 口径规格（锁定）

- 时刻：2026-11-04（Asia/Shanghai）
- 实现：`engines/dividendReturnEngine.py`
- 样例输出：`out/tr_samples.json`（由真实 bfq + dividend_events 计算，非编造）

## 一句话口径

**用未复权（bfq）收盘价做价格路径，现金分红只从事件表按 `ex_dividend_date` 入账（`cash_per_share = pretax_bonus_rmb/10`）；禁止在 qfq/hfq 收益率上再加现金。**

## 三项拆分

| 指标 | 公式 | 说明 |
|---|---|---|
| PriceReturn | `P_end / P_start - 1` | bfq close；起止对齐到窗口内首/末个交易日 |
| DividendCashReturn | `Σ cash_ps / P_start` | 期初买 1 股；现金不复投；仅 `ex ∈ (start, end]` 且 `assign_progress∈{实施分配}` |
| TotalReturn（不复投） | `(P_end + Σ cash_ps) / P_start - 1` | 分红现金留作现金 |
| TotalReturn（复投） | `shares_end * P_end / P_start - 1` | 除息日用当日/之后首个 bfq close 把该日现金买回股份（默认对照开关） |

PIT：默认 `require_disclosed_by_end=True`，丢弃 `disclose_date > end` 的事件（防未来函数）。

## 样例数字（实测）

窗口统一：`2021-01-04` → `2023-12-29`（若非交易日则对齐到窗口内交易日）。

### 600000 浦发银行

| 项 | 值 |
|---|---:|
| P_start (2021-01-04) | 9.69 |
| P_end (2023-12-29) | 6.62 |
| 现金合计 Σ cash_ps | 1.21（0.48+0.41+0.32，三次实施分配） |
| PriceReturn | **-0.316821** |
| DividendCashReturn | **0.124871** |
| TotalReturn 不复投 | **-0.191950** |
| TotalReturn 复投 | **-0.208298** |

手算复核复投：

```text
shares0=1
ex1: +0.48/9.55 → 1.05026178
ex2: +0.41*shares/7.33 → 1.10900766
ex3: +0.32*shares/7.12 → 1.15885071
TR_re = 1.15885071*6.62/9.69 - 1 = -0.208298
```

与引擎输出一致。

### 601398 工商银行

| 项 | 值 |
|---|---:|
| P_start | 5.01 |
| P_end | 4.78 |
| Σ cash_ps | 0.8628（3 次） |
| PriceReturn | **-0.045908** |
| DividendCashReturn | **0.172216** |
| TotalReturn 不复投 | **0.126307** |
| TotalReturn 复投 | **0.141854** |

### 600900 长江电力（附加对照）

| 项 | 值 |
|---|---:|
| P_start | 18.90 |
| P_end | 23.34 |
| Σ cash_ps | 2.3686（3 次） |
| PriceReturn | **0.234921** |
| DividendCashReturn | **0.125323** |
| TotalReturn 不复投 | **0.360243** |
| TotalReturn 复投 | **0.375461** |

## 明确禁止

1. 使用 `ashare-kline/raw/daily`（qfq）再叠加 `cash_per_share`
2. 使用后复权差价后再加现金
3. 用未披露（`disclose_date > as-of`）的预案计入当时可得分红

## 数据依赖

- 价格：`data/bfq_daily/{code}.parquet`（Phase2 样本 ≥30；全市场批量计划见 `data/bfq_daily/manifest.json`）
- 事件：`data/dividend_events.parquet`

Phase3 Quality **不**依赖完整全市场 bfq；但 Phase5/6/12 全历史 TotalReturn 评分前需扩大 bfq 覆盖。
