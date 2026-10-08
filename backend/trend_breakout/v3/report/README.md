# V3 report (Phase 12–14)

JSON + Markdown only. **Web frontend deferred.**

## Live publish — still V2.3
Close-push / daily board remains **V2.3 RecommendationScore** (`v2_3_scan.py` /
`v2_recommend.py` / `v2_staircase_scan.py`) until an **explicit cutover**.
Do not wire this package into live routines.

## Commands
```bash
cd /workspace/trend-breakout
python -m v3.report.unified_table --codes 002768,301108,300964 --t-date 2026-09-21 --out-dir v3/report/out
python -m v3.tests.phase14_smoke
```

Outputs: `trend_bot_results_{t_date}.json` + `.md` (Plan §26 table + §27 details).
禁止荐买 / 预测必涨.
