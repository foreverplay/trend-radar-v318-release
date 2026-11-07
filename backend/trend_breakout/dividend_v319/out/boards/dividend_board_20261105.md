# 红利趋势榜 V3.1.9 Phase6–11（as_of=2026-11-05）

- generated_at: 2026-11-06T09:18:34.789102+08:00（Asia/Shanghai）
- universe_scored: 2909
- 非荐买；操作提示不含「强烈买入/必涨/稳赚」
- TotalReturn：有bfq用真TR；无bfq用 price_qfq_only 弱代理（向50压缩），**禁止 qfq+现金**
- 技术趋势：qfq MA 结构（与现金TR口径分离）

## 等级计数

| 等级 | 数量 |
|---|---:|
| A+ | 3 |
| A | 118 |
| B | 486 |
| C | 1489 |
| FILTER | 813 |

- Risk: {"SAFE": 1124, "HIGH": 884, "WARNING": 639, "WATCH": 262}
- Entry: {"ACCUMULATE_WATCH": 105, "AVOID": 1341, "HOLD_WATCH": 547, "TREND_BROKEN": 814, "PULLBACK_WATCH": 75, "EXTENDED": 26, "TREND_CONFIRM": 1}
- return_proxy: {"bfq_plus_cash": 33, "price_qfq_only": 2876}
- DIVIDEND_TRAP flags: 52

## 当前机会 Top15（按 EntryOpportunityRank）

| Rank | 代码 | 名称 | Grade | Opp | Q | S | Trend | Val | Risk | Entry | 股息率TTM | 行业组 |
|---:|---|---|---|---:|---:|---:|---:|---:|---|---|---:|---|
| 46 | 603939 | 益丰药房 | A | 78.84 | 94.13 | 87.6 | 69.05 | 58.79 | SAFE | ACCUMULATE_WATCH | 1.50% | 其他 |
| 70 | 603198 | 迎驾贡酒 | A | 77.5 | 94.22 | 84.2 | 67.05 | 63.79 | SAFE | ACCUMULATE_WATCH | 3.26% | 其他 |
| 92 | 603658 | 安图生物 | A | 76.18 | 91.84 | 89.0 | 62.05 | 59.89 | SAFE | ACCUMULATE_WATCH | 3.27% | 其他 |

## 优质红利 Top10（按 QualityRank）

| Rank | 代码 | 名称 | Grade | Q | S | Opp | Risk |
|---:|---|---|---|---:|---:|---:|---|
| 18 | 603198 | 迎驾贡酒 | A | 94.22 | 84.2 | 77.5 | SAFE |
| 21 | 603939 | 益丰药房 | A | 94.13 | 87.6 | 78.84 | SAFE |
| 57 | 601900 | 南方传媒 | A | 93.27 | 89.4 | 76.23 | SAFE |
| 110 | 603658 | 安图生物 | A | 91.84 | 89.0 | 76.18 | SAFE |
| 835 | 000700 | 模塑科技 | A | 84.28 | 86.2 | 75.82 | WATCH |

## 风险观察示例

| 代码 | 名称 | Risk | Trap | Q | S | Opp | 主要原因 |
|---|---|---|---|---:|---:|---:|---|
| 603728 | 鸣志电器 | HIGH | False | 65.65 | 43.0 | 43.12 | 近3年(2023-2025)现金分红连续；连续现金分红约9年(截至报告期年2025) |
| 688772 | 珠海冠宇 | HIGH | False | 73.45 | 52.4 | 43.1 | 近3年(2023-2025)现金分红连续；连续现金分红约5年(截至报告期年2025) |
| 300133 | 华策影视 | HIGH | False | 73.4 | 42.7 | 53.43 | 近3年(2023-2025)现金分红连续；连续现金分红约11年(截至报告期年2025) |
| 300633 | 开立医疗 | HIGH | False | 80.82 | 72.9 | 53.42 | 近3年(2023-2025)现金分红连续；连续现金分红约5年(截至报告期年2025) |

## 已知偏差

- 三区均对齐当日趋势观察优质评级（TrendGrade∈{A+,A}）；当前机会的红利等级也须为 A+/A。无同日趋势观察发布时不套用该闸门
- 全市场 bfq 仅~33只；多数 historical_total_return_score 为 price_qfq_only 弱代理（已向50压缩）
- PE/PB 用最近年报 EPS/BPS × qfq 价，非券商一致预期 TTM；历史百分位用年末截面近似
- 行业分组为粗分（银行/煤炭/公用/交运/其他），组内可比性有限
- 红利指数历史成分仍缺失（universe_mode=proxy_no_hist_index_cons）
- 特别分红强标签弱，Quality 侧仍靠 DPS 启发式
- 权重与等级阈值待回测校准（Plan §27 初始值）
- 技术相对强度用同组1年收益中位，非正式行业指数
- Phase3→Phase6：在原 Q/S 上叠加 Trend/Val/Risk/Opp，分布会整体重排
