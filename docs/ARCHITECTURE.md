# 架构说明 — 趋势分析雷达 V3.1.8

```
┌─────────────┐     parquet/spot      ┌──────────────────┐
│ ashare-kline│ ───────────────────► │ v2_3_scan /      │
│ raw/daily   │   multi-source FO    │ v2_staircase_scan│
└─────────────┘                      └────────┬─────────┘
                                              │ candidates_v2_3.json
                                              ▼
                                     ┌──────────────────┐
                                     │ publish_close_   │
                                     │ v318 (as-of)     │
                                     └────────┬─────────┘
                                              │ push + boards JSON
                      ┌───────────────────────┼───────────────────────┐
                      ▼                       ▼                       ▼
               FastAPI /api/boards/*    push_close_v318_*.md     React UI 四榜
```

## 核心引擎（`backend/trend_breakout/v3/engines/`）

| 引擎 | 职责 |
|---|---|
| `trend_state` | 趋势状态机 |
| `trend_wave` | 波段/结构 |
| `trend_maturity` | 成熟度 EARLY/BUILDING/… |
| `realized_trend` | 已实现趋势涨幅 |
| `entry_signal` | EntryStatus 发射 |
| `rapid_main_rise` | RAPID_MAIN_RISE 并行路径 |
| `exit_engine` | 保护价/破坏价/ExitSignal |
| `technical_engine` / `continuity_engine` / `sector_engine` | Tech/Cont/Sector 分 |
| `market_engine` | 市场 regime / MarketScore |

## 分级

- `v3/grading_v318.py` — TrendGrade A+/A/B/C（**不**被 Maturity/Entry 强制降级）
- `v3/v31/entry_window.py` — V318 Entry overlay
- `v3/v31/live_v317.py` — as-of hist 加载与 V317 层复用

## 路径配置

`paths.py`：`TREND_BREAKOUT_ROOT` + `ASHARE_KLINE_ROOT`。
