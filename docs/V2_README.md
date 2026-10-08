# V2.3 多周期共振观察 — parent handoff

> V2.2 Continuity 仍完整保留；V2.3 叠加月线/板块趋势/RecommendationScore。详见 `STRATEGY_V2_3.md` / `HANDOFF_V2_3.md`。

# V2.2 阶梯式主升突破 + Continuity — (embedded)

## What shipped
| File | Role |
|---|---|
| `v2_structure.py` | **V2.0 frozen** — platform / first BO / pullback / second BO / accel + score/grade/phase/KJS / OH |
| `v2_fundamental.py` | FundamentalEngine (local ashare-financials) — formulas unchanged |
| `v2_sector.py` | SectorEngine — formulas unchanged |
| `v2_risk.py` | RiskEngine — unchanged |
| `v2_continuity.py` | **V2.2** ContinuityScore / TrendConsumption / Type / Delta history |
| `v2_monthly.py` | **V2.3** StockMonthlyTrendEngine |
| `v2_sector_trend.py` | **V2.3** SectorTrendEngine (EW peers, cached) |
| `v2_recommend.py` | **V2.3** RecommendationScore + Resonance + Reason templates |
| `v2_3_scan.py` | Thin wrapper → same entry |
| `v2_classify.py` | CORE_TREND / OBSERVATION / HIGH_RISK / FILTER（含持续性门槛） |
| `v2_staircase_scan.py` | Primary scanner (tech → V2.1 enrich → continuity → boards/pools → reports) |
| `v2_2_scan.py` | Thin wrapper → same entry |
| `v2_1_scan.py` | Thin wrapper (still valid; now runs V2.2 path) |
| `meta/force_codes.txt` | Always-merge watchlist (002011, 601579) |
| `cache/continuity_history/{code}.json` | Daily ContinuityScore history |
| `report_v2.md` / `candidates_v2.json` / `all_results_v2.json` | Outputs |
| `report.md` / `candidates.json` / `all_results.json` | Routine mirrors |
| `STRATEGY_V2.md` / `STRATEGY_V2_1.md` / `STRATEGY_V2_2.md` | Specs |

Skill file update = parent job.

## Invoke (16:00 Asia/Shanghai close routine)
```bash
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_staircase_scan.py --t-date 2026-09-21 --force-codes 002011,601579
# alias
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_2_scan.py --t-date 2026-09-21
# V2.3 alias
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_3_scan.py --t-date 2026-09-21
```
Prefer T with local hist already at T (ashare-kline); else ensure_hist_to_t + spot append (same as V2.1).

Optional: `--max-prefilter 250` `--workers 4` `--force-file`.

## Hard rules
- Do **not** change V2.0 TechnicalScore / phase / KJS / OH in `v2_structure.py`
- Do **not** change V2.1 fund/sector/risk **formulas** (classify gates only)
- Do **not** create TotalScore = sum of dimensions
- ContinuityScore = **state score**, never phrase as future return prediction
- Missing financial/sector continuity inputs → partial + `DATA_INSUFFICIENT` (never invent 0 wipe)
- No buy advice / must-rise language; no ZhuangStock label

## Language
No buy advice in reports. Never invent prices. Focus ④→⑤→⑥ not max gainers.
