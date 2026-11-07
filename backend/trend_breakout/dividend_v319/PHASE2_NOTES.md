# TrendConfirmationBot V3.1.9 Phase2 — Notes

- 完成时刻：2026-11-04（Asia/Shanghai）
- 包根：`/workspace/trend-breakout/dividend_v319/`
- 约束：未改 `publish_close_v318.py` / `v3/publish_close_v318.py`（md5 与 Phase1 一致）；未实现 Quality 打分

## 1. 完成项对照

| # | 目标 | 状态 | 产物 |
|---|---|:---:|---|
| 1 | 回填 fhps 2015–2020 | ✅ | `cache/fhps_em_{YYYYMMDD}.parquet` + `cache/fhps_em_2015_2020_all.parquet`（16726 行） |
| 2 | 规范化事件表 | ✅ | `data/dividend_events.parquet`（37699 行）+ sample csv + meta |
| 3 | 未复权抽样 ≥30 | ✅ | `data/bfq_daily/` **33** 只成功（含银行/公用/煤炭/高速/电信 + watch） |
| 4 | TotalReturn 最小引擎 | ✅ | `engines/dividendReturnEngine.py` + `PHASE2_TR_SPEC.md` + `out/tr_samples.json` |
| 5 | Universe.asof(T) | ✅ | `engines/dividendUniverseEngine.py` + `out/universe_asof_sample.json` |
| 6 | 不写 Quality / 不改 v318 | ✅ | stubs 保留；publish md5 未变 |

## 2. 事件表

- 行数：**37699**
- 代码数：**5262**
- 报告期年份覆盖：**2015–2025**（见 `data/dividend_events_meta.json`）
- 来源合并：
  - 本地 `fhps_2021_2025_all.parquet`（`local_fhps_em_rpt_sharebonus_det`）
  - akshare `stock_fhps_em` 回填 2015–2020（`ak.stock_fhps_em`）
- PIT 字段：`disclose_date = min(plan_notice_date, notice_date, publish_date)`（非空）
- `cash_per_share = pretax_bonus_rmb / 10`
- `is_special_flag`：本批数据 **无 True**（本地 profile 无「特别/专项…」命中；ak 回填无方案文本 → null）。规则写在 meta，Phase3 可用 cninfo 分红类型加强。

### 回填失败票/日期

| 报告期 | 结果 | 说明 |
|---|---|---|
| 20150331 | ❌ 仍失败 | 东财接口返回 None / TypeError；中报前一季本就极稀疏 |
| 20161231 / 20171231 / 20201231 | ✅ 重试或 Phase1 全表拷贝后成功 | 见 `cache/fhps_backfill_log.json` |
| 其余 2015–2020 季报日 | ✅ | 年报日为主力；中报/季报行数少属数据源常态 |

## 3. BFQ 样本

- 成功 **33**（含重试成功的 000001）
- 失败：无（重试后）
- 全市场批量：见 `data/bfq_daily/manifest.json` → `bulk_plan`（复用 kline download 脚本 `adjust=''`，建议 Phase6）

## 4. TotalReturn

见 `PHASE2_TR_SPEC.md`。样例：

- 600000 @2021-01-04→2023-12-29：PR **-31.68%**，DivCash **+12.49%**，TR不复投 **-19.20%**，TR复投 **-20.83%**
- 601398 同窗：PR **-4.59%**，DivCash **+17.22%**，TR不复投 **+12.63%**，TR复投 **+14.19%**

## 5. Universe.asof

- `universe_mode=proxy_no_hist_index_cons`：指数腿始终用**当前**中证/上证红利成分缓存 ∪ 事件表 PIT 自主池
- 自主池：`disclose_date<=T` 的实施现金分红；连续年窗口终点 = `min(data_last, T.year-1)`（年报披露滞后）
- 样本（`min_years=3`）：

| as_of | active | autonomous | index | both |
|---|---:|---:|---:|---:|
| 2019-12-31 | 1773 | 1741 | 101 | 69 |
| 2021-12-31 | 1959 | 1938 | 101 | 80 |
| 2023-12-31 | 2298 | 2289 | 101 | 92 |

- latest（`min_years=5` → 2021–2025）：active **2201**（与 Phase1 同量级）

`DividendUniverseEngine.asof(T)` 可跑。

## 6. Phase3 可开工条件

| 条件 | 状态 |
|---|:---:|
| 规范化分红事件表（金额+公告/除权+PIT） | ✅ |
| TR 口径锁定且样例可手算复核 | ✅ |
| Universe.asof PIT 可跑（虽指数历史成分仍为 proxy） | ✅ |
| 财务 PIT 面板（Phase1 已确认 NOTICE_DATE） | ✅ |
| 全市场 bfq | ❌ 非 Phase3 阻断（Quality 先用事件+财务） |
| 指数历史成分 | ❌ 非 Phase3 阻断（继续 proxy 并记录 gap） |
| 特别分红强标签 | ⚠️ 弱（多为 null/False）；Phase3 可用文本/cninfo 增强但不挡开工 |

**结论：Phase3 DividendQualityEngine 可开工。**

## 7. 关键脚本

```bash
/workspace/ashare-venv/bin/python dividend_v319/scripts/backfill_fhps_em.py
/workspace/ashare-venv/bin/python dividend_v319/scripts/backfill_fhps_em.py --retry
/workspace/ashare-venv/bin/python dividend_v319/scripts/build_event_table.py
/workspace/ashare-venv/bin/python dividend_v319/scripts/fetch_bfq_sample.py
cd /workspace/trend-breakout && /workspace/ashare-venv/bin/python -m dividend_v319.engines.dividendUniverseEngine
```
