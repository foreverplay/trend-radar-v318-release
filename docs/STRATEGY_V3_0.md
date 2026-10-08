# 趋势确认突破雷达 TrendConfirmationBot V3.0

## Goals / 目标
- Stay **Python** under `/workspace/trend-breakout/`（不迁 TypeScript）
- 观察池 / 结构确认，**禁止荐买 / 预测必涨**
- A+ / A / B 以 **Plan 阈值矩阵** 为准（**不是** RecommendationScore≥90）
- 市场状态：引入 **BULL / NORMAL / BEAR / CHOP**，并保留映射到 V2 **STRONG / NORMAL / WEAK / RISK_OFF**（**不删除 RISK_OFF**）
- 统一结果形态：`TrendBotResult` + Markdown/JSON 表（Phase 7 Bot + Phase 8 ExitEngine 价位 + Phase 9–11 Backtest / exit grid / WF + Phase 12–14 unified report；不进 live）

## Grade rules（Plan §11–12 + §30 candidate）
独立分数，无 TotalScore：

| Grade | tech | cont | fund* | sector* | risk | lifecycle（摘要） |
|-------|-----:|-----:|------:|--------:|------|------------------|
| A+ | ≥85 | ≥80 | ≥70 | ≥70 | SAFE/WATCH | SECOND_BREAKOUT / MAIN_RISE / ACCELERATION |
| A | ≥75 | ≥70 | ≥60 | ≥60 | +WARNING | + PLATFORM_HOLD / HEALTHY_PULLBACK |
| B | ≥65 | ≥60 | ≥50 | ≥50 | +HIGH | 含 FIRST_BREAKOUT / PLATFORM / OBSERVE |
| WATCH | ≥55 | ≥50 | — | — | 放宽 | 观察 |
| FILTER | 硬过滤 | | | | BROKEN / TREND_FAILURE / 分数不足 / ERROR | |

\* fund/sector 缺省时**跳过该维**（不伪 0）。  
**BEAR**：tech/cont/fund/sector +5，consumption 上限 −5。  
**CHOP**：tech/cont/sector +3。  
**OVERHEATED**：最高 WATCH，不可进 A+/A/B 核心。

## Regime mapping

| legacy_regime (V2) | regime_v3 (Plan) | risk_off | notes |
|--------------------|------------------|----------|-------|
| STRONG | BULL | false | 强趋势 |
| NORMAL | NORMAL | false | 中性 |
| WEAK | CHOP | false | 弱势/震荡偏弱（非 RISK_OFF） |
| RISK_OFF | BEAR | **true** | 极弱；RISK_OFF 永不丢 |
| BEAR + risk_off=false | BEAR | false | 软熊；legacy=WEAK |

`MarketScore` / archive 行同时写 `regime_v3` + `legacy_regime` + `risk_off`。

## Lifecycle map（V2 phase → V3）
- `MAIN_RISE_ACCEL` → **ACCELERATION**（documented split）
- 其余同名：`PLATFORM_HOLD` / `SECOND_BREAKOUT` / `OVERHEATED` / `TREND_FAILURE` …

## File map
| Path | Role |
|------|------|
| `v3/config.py` | `TREND_BOT_CONFIG`（§30 thresholds + grade matrix） |
| `v3/types.py` | StockSnapshot / TrendBotResult / enums |
| `v3/regime_map.py` | bidirectional legacy ↔ v3 |
| `v3/grading.py` | `grade_candidate(...)` pure |
| `v3/market_regime_archive.py` | daily jsonl/csv writer |
| `v3/engines/technical_engine.py` | **Phase 3** `TechnicalEngine` → wraps `v2_structure.analyze_hist` |
| `v3/engines/market_engine.py` | **Phase 4** `MarketEngine` → wraps `compute_market_meta` + archive |
| `v3/engines/sector_engine.py` | **Phase 4** `SectorEngine` → wraps `v2_sector` + `v2_sector_trend` |
| `v3/engines/fundamental_engine.py` | **Phase 5** `FundamentalEngine` → wraps `v2_fundamental` |
| `v3/engines/risk_engine.py` | **Phase 5** `RiskEngine` → wraps `v2_risk` + `RiskLevel.map_from_v2` |
| `v3/engines/continuity_engine.py` | **Phase 6** `ContinuityEngine` → wraps `v2_continuity.ContinuityEngine` |
| `v3/engines/lifecycle_engine.py` | **Phase 6** `LifecycleEngine` → maps phase → `Lifecycle` + priority flag |
| `v3/bot/trend_confirmation_bot.py` | **Phase 7+8** `TrendConfirmationBot` orchestrator → `TrendBotResult` |
| `v3/engines/exit_engine.py` | **Phase 8** `ExitEngine` → Warning/Protection/BreakTrend + ExitSignal |
| `v3/exit_state/` | **Phase 8** ratchet state helper (`update_state`) |
| `v3/backtest/` | **Phase 9–11** BacktestEngine + exit grid / WF / ExitQualityScore (not live) |
| `v3/report/unified_table.py` | **Phase 12** `write_unified_report` → JSON + Markdown (Plan §26) |
| `v3/report/detail.py` | **Phase 13** `format_stock_detail` / lifecycle markers (Plan §27；无 LLM) |
| `v3/tests/phase14_smoke.py` | **Phase 14** regression smoke |
| `v3/snapshot.py` | optional `build_stock_snapshot_from_hist` |
| `history/market_regime_daily.{jsonl,csv}` | regime archive |
| `STRATEGY_V3_0.md` / `HANDOFF_V3_0.md` | this + handoff |

## Phase status
| Phase | Status |
|-------|--------|
| Phase 2 types/config/grading/regime archive | **done** |
| Phase 3 TechnicalEngine (wrap analyze_hist) | **done** |
| Phase 4 MarketEngine + SectorEngine + daily archive wire | **done** |
| Phase 5 FundamentalEngine + RiskEngine wrappers | **done** |
| Phase 6 ContinuityEngine + LifecycleEngine wrappers | **done** |
| **Phase 7 TrendConfirmationBot orchestrator** | **done** |
| **Phase 8 ExitEngine** | **done** |
| **Phase 9 BacktestEngine** | **done** (Exit validation only) |
| **Phase 10 Walk-Forward / OOS strategy grid** | **done** (`wf_window=2026YTD_proxy`) |
| **Phase 11 DEFAULT_EXIT from measured winner** | **done** (`Fixed5%`, confirm_days=0) |
| **Phase 12 unified table writer (JSON+MD)** | **done** |
| **Phase 13 per-stock detail block** | **done** (rules text; no LLM) |
| **Phase 14 regression smoke** | **done** |
| Frontend web | deferred (unchecked) |
| Plan A+ 2019–2025 full WF | deferred (archives missing; 2026YTD_proxy only) |

## MarketEngine（Phase 4）
```python
from v3.engines import MarketEngine

r = MarketEngine().evaluate("2026-09-23")  # prefers market_meta.json when t_date matches
# or: MarketEngine().evaluate(t_date, spot=spot_df)  → compute_market_meta
# r.regime_v3 / r.legacy_regime / r.risk_off / r.market_score / r.trend / r.cap
# on success → append_from_market_meta (idempotent by t_date)
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.market_engine --t-date 2026-09-23
```

## SectorEngine（Phase 4）
```python
from v3.engines import SectorEngine
import pandas as pd

hist = pd.read_parquet("/workspace/ashare-kline/raw/daily/002768.parquet")
r = SectorEngine().evaluate("002768", hist=hist, t_date="2026-09-21")
# r.sector_score / r.sector_trend (UP|FLAT|DOWN) / r.relative_strength / r.breadth_score
# hist truncated to date <= t_date (no lookahead)
# STRONG_UP/UP → UP; NEUTRAL → FLAT; DOWN → DOWN
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.sector_engine --code 002768 --t-date 2026-09-21
```


## FundamentalEngine（Phase 5）
```python
from v3.engines import FundamentalEngine

r = FundamentalEngine().evaluate("002768")  # optional pe=, industry_pe=
# r.fund_score / r.level / r.data_status / r.industry / r.tags / r.notes
# DATA_INSUFFICIENT → fund_score is None (never fake 0)
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.fundamental_engine --code 002768
```

## RiskEngine（Phase 5）
```python
from v3.engines import RiskEngine
import pandas as pd

hist = pd.read_parquet("/workspace/ashare-kline/raw/daily/002768.parquet")
r = RiskEngine().evaluate(hist=hist, t_date="2026-09-21", code="002768")
# r.risk_level (SAFE/WATCH/HIGH/BROKEN/…) via RiskLevel.map_from_v2
# r.risk_score_v2 (LOW/MID/HIGH) / r.broken / r.tags
# hist truncated to date <= t_date (no lookahead)
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.risk_engine --code 002768 --t-date 2026-09-21
```


## ContinuityEngine（Phase 6）
```python
from v3.engines import ContinuityEngine
import pandas as pd

hist = pd.read_parquet("/workspace/ashare-kline/raw/daily/002768.parquet")
r = ContinuityEngine().evaluate("002768", hist=hist, t_date="2026-09-21",
                                phase=None, persist=False)
# r.continuity_score / r.consumption_score / r.continuity_type
# r.delta1 / r.delta3 / r.delta5 / r.delta_label / r.tags / r.notes
# hist truncated to date <= t_date (no lookahead)
# Reuses v2_continuity.ContinuityEngine.evaluate
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.continuity_engine --code 002768 --t-date 2026-09-21
```

## LifecycleEngine（Phase 6）
```python
from v3.engines import LifecycleEngine, TechnicalEngine
import pandas as pd

hist = pd.read_parquet("/workspace/ashare-kline/raw/daily/002768.parquet")
tech = TechnicalEngine().evaluate(hist, t_date="2026-09-21")
r = LifecycleEngine().evaluate(technical_result=tech)
# or: LifecycleEngine().evaluate(phase=tech.phase)
# r.lifecycle via Lifecycle.map_from_v2_phase
# r.is_priority_candidate True for SECOND_BREAKOUT / MAIN_RISE / ACCELERATION
# Does NOT invent phase detection — delegates to structure/technical
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.lifecycle_engine --code 002768 --t-date 2026-09-21
```



## TrendConfirmationBot（Phase 7+8）
```python
from v3.bot import TrendConfirmationBot, results_to_markdown_table

bot = TrendConfirmationBot()
r = bot.evaluate_one("301108", t_date="2026-09-21")
# r.grade / r.tech_score / r.continuity_score / r.lifecycle / r.exit_signal
# r.warning_price / r.protection_price / r.break_price from ExitEngine

results = bot.evaluate_many(["002768", "301108", "300964"], t_date="2026-09-21")
print(results_to_markdown_table(results))
# columns: 股票/阶段/等级/Tech/Fund/Sector/Cont/消耗/风险/保护价/破坏价/ExitSignal
```

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.bot.trend_confirmation_bot --codes 002768,301108,300964 --t-date 2026-09-21
```

Pipeline: Market (shared) → Sector → Technical → Fundamental → Risk → Continuity → Lifecycle → `grade_candidate` → **ExitEngine**.

## ExitEngine（Phase 8 — Plan §§13–16）
```python
from v3.engines import ExitEngine
from v3.exit_state import update_state, get_state

r = ExitEngine().evaluate(hist, t_date="2026-09-21", code="002768",
                          technical_result=tech, prior_state=get_state("002768"))
# r.warning_price / r.protection_price / r.break_trend_price / r.exit_signal
# r.structure_support / r.peak_close / r.broken_confirmed
update_state("002768", r)  # ratchet persistence (in-memory)
```

Rules (observation only — SELL ≠ predict drop):
- **StructureSupport** = max(RecentSwingLow, BreakoutSupport, MA20)
- **TrendBreakPrice** = StructureSupport − ATR14 × `exit.atrMultiplier` (default 0.5)
- **WarningPrice** = StructureSupport (documented: alert at structural support; sits between Close and Break when intact)
- **ProtectionPrice** = max(prior, StructureSupport, PeakClose×(1−maxDrawdown)); **ratchet UP only**
- **SELL / TREND_BROKEN** only when Close < TrendBreakPrice **AND** Close < MA20 **AND** MA20Slope < 0
- False break (low < Break, close ≥ Break) → PROTECT/WATCH, never SELL
- ExitSignal: HOLD | WATCH | PROTECT | REDUCE_RISK | SELL
- `confirm_days` 0/1/2; Phase 8 default **0-day** close confirm (multi-day via `broken_streak` hook)
- No fixed −5%/−8% as sole sell rule

Smoke:
```bash
cd /workspace/trend-breakout
python -m v3.engines.exit_engine --code 002768 --t-date 2026-09-21
python -m v3.engines.exit_engine --code 002768 --t-date 2026-09-21 --prior-protection 999
```

## BacktestEngine + Exit Grid（Phase 9–11）
```python
from v3.backtest import BacktestEngine, get_strategy

eng = BacktestEngine()
result = eng.run(
    entries=[("2026-01-05", "000408"), ("2026-01-06", "000603")],
    strategy=get_strategy("Fixed5%"),  # DEFAULT from Phase 11 measured winner
    start="2026-01-05",
    end="2026-06-30",
    entry_source="caller",
)
print(result.to_markdown())
```

**Does NOT feed live selection.** Plan §18 grid implemented: Fixed5/8/10/15%, MA20 (±ATR), SwingLow (±ATR), BreakoutSupport (±ATR), StructureSupport-0.5ATR, DynamicProtection, DynamicProtection+MaxDrawdown; confirm_days 0/1/2.

### Walk-Forward / DEFAULT（Phase 10–11）
- **wf_window=`2026YTD_proxy`** — Plan-grade A+ 2019–2023 **does not exist** locally; full 2019–2025 WF **deferred**.
- Splits: Train 2026-01-05…04-30 / Val 2026-05-06…06-30 / Test 2026-07-01…09-21
- Entries: legacy RecScore≥90, stratified top5/day, max 300
- ExitQualityScore (§24 proxy) → winner written to `TREND_BOT_CONFIG["exit"]["default_strategy"]`
- Artifacts: `v3/backtest/results/exit_grid_2026proxy/{rankings.csv,summary.md,default_exit_choice.json}`

```bash
cd /workspace/trend-breakout
python -m v3.backtest.engine --smoke
python -m v3.backtest.run_exit_grid --max-entries 300 --confirm-days 0,1,2
```


## Unified report（Phase 12–14 — Plan §§26–27）
```python
from v3.bot import TrendConfirmationBot
from v3.report import write_unified_report, format_stock_detail

bot = TrendConfirmationBot()
results = bot.evaluate_many(["002768", "301108", "300964"], t_date="2026-09-21")
paths = write_unified_report(results, "v3/report/out", "2026-09-21")
# → trend_bot_results_2026-09-21.json + .md
# Plan §26 columns: 股票/阶段/等级/Tech/Fund/Sector/Cont/消耗/风险/保护价/破坏价/ExitSignal
print(format_stock_detail(results[0]))  # Plan §27 detail (rules text; no LLM)
```

CLI / smoke:
```bash
cd /workspace/trend-breakout
python -m v3.report.unified_table --codes 002768,301108,300964 --t-date 2026-09-21 --out-dir v3/report/out
python -m v3.tests.phase14_smoke
```

**Agent note (cutover DONE at publish layer):** live close-push user board is now **V3 Plan grades** via `v3/publish_close_v3.py` (after `v2_3_scan.py` hist/enrich). V2.3 RecScore may remain in internal JSON; **do not** put it in the user push table.

## TechnicalEngine（Phase 3）
```python
from v3.engines import TechnicalEngine

eng = TechnicalEngine()
result = eng.evaluate(hist, t_date="2026-09-21")
# result.technical_score == StructureResult.score
# result.lifecycle = Lifecycle.map_from_v2_phase(phase)
```

## Live path（publish-layer cutover DONE）
- Close pipeline：
  ```bash
  /workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_3_scan.py --t-date YYYY-MM-DD
  /workspace/ashare-venv/bin/python -m v3.publish_close_v3 --t-date YYYY-MM-DD
  ```
- **User-facing board = V3 Plan grades**（A+/A/B/WATCH/FILTER + Tech/Fund/Sector/Cont/消耗/风险/保护价/破坏价/ExitSignal）
  → `report_v3.md` / `push_close_v3_{YYYYMMDD}.md` / `candidates_v3.json` / `v3/report/out/trend_bot_results_{T}.*`
- V2.3 scan **kept** for hist/enrich freshness；RecommendationScore **可不删**于内部 JSON，但 **停推 / 降级为内部**
- AM/noon：复推最新 `report_v3.md` / `push_close_v3_*.md`（不重扫）
- **BacktestEngine / exit grid 仍不进入 live 选股**（DEFAULT_EXIT 仅写 config）
- **不要**改写 `v2_structure` / `compute_market_meta` / `v2_sector*` / `v2_fundamental` / `v2_risk` / `v2_continuity` 数学
- Plan A+ 历史归档：`backfill_plan_a_plus_daily.py` → `history/a_plus_plan_daily_2026.parquet`（fund 非 PIT  caveat 见 README）

禁止荐买 / 预测必涨。
