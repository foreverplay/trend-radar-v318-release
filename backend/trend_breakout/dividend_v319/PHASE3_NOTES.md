# TrendConfirmationBot V3.1.9 Phase3–5 — Notes

- 完成时刻：2026-11-04（Asia/Shanghai）
- 包根：`/workspace/trend-breakout/dividend_v319/`
- 约束：未改 `publish_close_v318.py` / `v3/publish_close_v318.py`（md5 与 Phase1/2 一致）；未写假分；非荐买

## 1. 完成项对照

| # | 目标 | 状态 | 产物 |
|---|---|:---:|---|
| Phase3 | DividendQualityEngine 可跑 | ✅ | `engines/dividendQualityEngine.py` + `engines/fin_pit_store.py` |
| Phase4 | DividendSustainabilityEngine 可跑 | ✅ | `engines/dividendSustainabilityEngine.py` |
| Phase5 | Return 批跑接入 | ✅ | `dividendReturnEngine.compute_multi_horizon` + runner 抽样 |
| 编排 | asof 批跑 JSON/MD | ✅ | `run_phase3_scores.py` → `out/phase3/*` |
| 配置 | 分项权重注释「待回测校准」 | ✅ | `config/dividendConfig.py` |

## 2. 批跑主结果（as_of=2025-12-31）

- Universe：`proxy_no_hist_index_cons`，min_years=3 → active **2713**
- Quality 有分：**2713**；Sustainability 有分：**2713**（缺项用中性 50 + DATA 标记，不编造）
- Quality 分布：mean **78.26** / p50 **79.45** / p10 **64.93** / p90 **89.40**（min 42.3 / max 95.09）
- Sustainability 分布：mean **65.15** / p50 **69.10** / p10 **38.50** / p90 **86.80**（min 11.7 / max 88.6）
- Return 抽样：请求 200；有 bfq 并算出 PR/TR：**33**；缺 bfq → `DATA_INSUFFICIENT`：**167**（禁止 qfq+现金）

输出：

- `out/phase3/scores_20251231.json`（全量行）
- `out/phase3/summary_20251231.json`
- `out/phase3/report_20251231.md`
- `out/phase3_scores_latest.json` / `out/phase3_report_latest.md`

## 3. Quality 口径（待回测校准）

权重：连续性 25 / 稳定增长 20 / 支付合理性 20 / 现金能力 25 / 记录质量 10。

- PIT：分红 `disclose_date<=T`；财务 `NOTICE_DATE<=T`（年报 period=`*1231`，universe+hist 面板）
- 连续年终点：`last_complete_report_year = T.year-1`（对 2025-12-31 → 2024）
- 特别分红：`is_special_flag==True` 降权；否则 DPS≥2.5×其余年中位启发式，写入 reasons 并降权（不确定不直接高分）
- 送股：`cash_per_share<=0` 不计
- 支付率代理：年 DPS / BASIC_EPS（非章程口径总分红）
- 现金能力：YJBB `MGJYXJJE`/DPS；FCF≈`NETCASH_OPERATE - CONSTRUCT_LONG_ASSET`

## 4. Sustainability 口径（待回测校准）

分项：OCF/NI、FCF/分红、支付率、利润连续性、收入稳定、杠杆；缺项中性 50 + missing 标记。

不健康（负 FCF 仍分红、支付率>盈利、利润急降等）降分，并给 `risk_hint`（SAFE/WATCH/WARNING/HIGH）供后续 RiskEngine。

**高派息≠高质量**：例 601838 成都银行 Quality≈71（连续性/支付仍有分）但 cash_ability≈3、Sustainability≈42.9、`WARNING`+`negative_fcf_vs_dividend`。

## 5. 人工抽查 3 只（continuity 与分项非空）

| 代码 | 名称 | 连续年 | Q | 分项(连续/稳定/支付/现金/记录) | S |
|---|---|---:|---:|---|---:|
| 601398 | 工商银行 | 10 | 93.65 | 100 / 94 / 86.51 / 95 / 88 | 85.9 |
| 600900 | 长江电力 | 10 | 87.75 | 100 / 96 / 55 / 95 / 88 | 82.9 |
| 601088 | 中国神华 | 10 | 83.75 | 100 / 76 / 55 / 95 / 88 | 82.9 |

工商银行 2024 年 DPS=0.308（中报+年报合计口径与事件表一致）；连续年按报告期年计至 2024。

## 6. Top10 by Quality（as_of=2025-12-31，非荐买）

| # | 代码 | 名称 | 质量 | 可持续 | 连续年 |
|---:|---|---|---:|---:|---:|
| 1 | 603367 | 辰欣药业 | 95.09 | 85.6 | 8 |
| 2 | 601298 | 青岛港 | 95.05 | 88.6 | 7 |
| 3 | 600563 | 法拉电子 | 94.88 | 86.8 | 10 |
| 4 | 002158 | 汉钟精机 | 94.87 | 81.4 | 10 |
| 5 | 688663 | 新风光 | 94.87 | 81.4 | 5 |
| 6 | 000739 | 普洛药业 | 94.76 | 86.2 | 10 |
| 7 | 600970 | 中材国际 | 94.62 | 75.3 | 10 |
| 8 | 600036 | 招商银行 | 94.59 | 85.9 | 10 |
| 9 | 002837 | 英维克 | 94.59 | 84.4 | 9 |
| 10 | 002128 | 电投能源 | 94.53 | 81.2 | 6 |

## 7. 已知偏差 → Phase6+

1. 全市场 bfq 仅 ~33 只样本；Phase6 估值/技术/总回报评分与 Phase12 回测前需扩 bfq
2. 红利指数历史成分仍缺失（proxy）
3. 特别分红强标签弱，依赖启发式
4. FCF/支付率为代理口径，非事务所级自由现金流/分红总额
5. 股票池已是「连续分红」筛选，Quality 分布偏高属选择偏差；应用 Sustainability/Risk 再分层
6. 未实现 Valuation / Technical / Risk / Opportunity / Entry / Grade 综合（Phase6–11）
7. 2025 年 DPS 在 as_of=2025-12-31 多为中期部分，连续性终点用 2024（正确），展示里仍可见 2025 中期金额

## 8. 命令

```bash
cd /workspace/trend-breakout
/workspace/ashare-venv/bin/python -m dividend_v319.run_phase3_scores --as-of 2025-12-31
```
