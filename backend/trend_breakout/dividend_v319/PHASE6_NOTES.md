# TrendConfirmationBot V3.1.9 Phase6–11 — Notes

- 完成时刻：2026-11-04（Asia/Shanghai）
- 包根：`/workspace/trend-breakout/dividend_v319/`
- 约束：未改 `publish_close_v318.py` / `v3/publish_close_v318.py`（md5 与 Phase1–3 一致）；非荐买；未编造缺失估值字段

## 1. 完成项

| # | 模块 | 状态 | 文件 |
|---|---|:---:|---|
| Phase6/8 | DividendValuationEngine + YieldTrap | ✅ | `engines/dividendValuationEngine.py` |
| Phase7 | DividendTechnicalEngine（qfq MA） | ✅ | `engines/dividendTechnicalEngine.py` |
| Phase9 | DividendRiskEngine | ✅ | `engines/dividendRiskEngine.py` |
| Phase5/10 | HistoricalTotalReturnScore + Opportunity/Grade | ✅ | `engines/dividendTrendEngine.py` |
| Phase11 | DividendEntryEngine | ✅ | `engines/dividendEntryEngine.py` |
| 榜单 | JSON + Markdown 三区 | ✅ | `out/boards/dividend_board_20251231.*` |
| 编排 | 批跑 | ✅ | `run_phase6_board.py` |

## 2. 口径要点

### 技术趋势 vs 总回报（必须分离）

- **TrendScore**：只用 `ashare-kline/raw/daily` **qfq** 的 MA20/60/120/250 与波动/回撤结构。
- **TotalReturn**：有 `data/bfq_daily` 时用 **bfq + 除息现金**（PHASE2_TR_SPEC）；**禁止 qfq+现金**。
- 无 bfq：`return_proxy=price_qfq_only`，分数 = `50 + 0.45*(raw-50)` 向中性压缩，并标记 DATA。

### 估值

- PE/PB ≈ 最近年报 EPS/BPS（PIT NOTICE_DATE≤T）× as_of qfq 收盘（最新交易日 qfq≈未复权，可接受）。
- 自身历史 PE/PB 分位受 **历史 qfq 下拉** 扭曲 → **降权 35%**，主看行业组绝对带（银行/煤炭/公用/交运/其他）。
- 股息率 TTM = 近1年除息现金 / 价格；另给 3Y/5Y 均值代理。
- `DividendYieldTrap`：高股息（≥5% 或显著高于组中位）+ 价跌/盈利恶化/现金恶化启发式。

### 综合分 / 等级（待回测校准）

```
Opp = 0.20Q + 0.25S + 0.25Trend + 0.15TR + 0.10Val + 0.05*(100-Risk)
A+ ≥85 且 Q≥75 且 S≥75 且 Risk!=HIGH
A≥75 / B≥65 / C≥50 / else FILTER
```

EntryStatus **独立于** Grade；禁用「强烈买入/必涨/稳赚」。

### 三区榜

- **优质红利**：Grade∈{A+,A} 且 S≥70 且 Risk∈{SAFE,WATCH}
- **当前机会**：Entry∈{TREND_CONFIRM,ACCUMULATE_WATCH,PULLBACK_WATCH} 且 Grade∈{A+,A,B} 且 Risk∈{SAFE,WATCH} 且非陷阱 且 **TTM股息率≥1%**
- **风险观察**：Trap 或 HIGH，或 WARNING 且 (Q<70 或 S<55)

## 3. 主结果（as_of=2025-12-31）

见 `out/boards/dividend_board_20251231_summary.json`：

- 全池 **2713** 均有 Grade（含 FILTER）
- A+ 样本满足 Q/S/Risk 门槛（违规列表为空）
- DIVIDEND_TRAP 标记约 31 只；风险观察示例优先陷阱

## 4. 与 Phase3 的已知偏差

1. Phase3 只排 Quality；Phase6 引入 Trend/Val/Risk/TR 后 **排序重排**（银行等有真 bfq TR 的更易冲高 Opp）。
2. Phase3 Sustainability `WARNING` 面大 → Phase6 Risk WARNING/HIGH 仍偏多（阈值待校准）。
3. 多数标的 TR 为价格弱代理 → Opp 上限被压，**A+ 稀少**（全窗仅少数有完整 bfq）。
4. 估值历史分位方法论变化（降权 qfq 扭曲），与「纯分位便宜」叙事不同。

## 5. Phase12 回测前缺口

1. 全市场 bfq/未复权行情（真 TR）
2. 红利指数 **历史成分**（去幸存者）
3. Walk-forward / OOS 校准权重与 A+/介入阈值
4. 交易成本 + 对照中证红利等基准
5. 特别分红标签增强
6. （可选）组内横截面 PE/PB 分位

## 6. 命令

```bash
cd /workspace/trend-breakout
/workspace/ashare-venv/bin/python -m dividend_v319.run_phase6_board --as-of 2025-12-31
```

输出：

- `out/boards/dividend_board_20251231.json`（含全量 rows）
- `out/boards/dividend_board_20251231.md`
- `out/boards/dividend_board_20251231_summary.json`
