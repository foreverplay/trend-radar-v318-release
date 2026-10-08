# 缓存布局说明

运行时缓存默认写在 `backend/trend_breakout/cache/`（可用 `TREND_BREAKOUT_ROOT` 覆盖）。

| 路径 | 作用 |
|---|---|
| `cache/turnover_daily.json` | 两市成交额日序列，供 MA5/MA20 |
| `cache/continuity_history/` | 连续性引擎按标的历史片段 |
| `cache/intraday_last.json` | 盘中批量报价最近一次 |
| `cache/session_loop_state.json` | 盘中循环状态 |
| `cache/rate_limit_notes.json` | 行情源限速探测结果 |
| `v3/v31/hist_cache/` | as-of 回放日线切片（大，勿打包） |
| `v3/v31/snap_cache/` | feature snapshot 缓存（大，勿打包） |
| `v3/exit_state/store*.json` | ExitEngine 状态持久化 |

本目录附带一份样例 `turnover_daily.json` 供离线演示。完整扫描会在本地重新生成其余缓存。
