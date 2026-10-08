# Data source fallback order

Shared module: `data_sources.py`. Every helper returns `{ok, value, source, errors}`.
Do **not** invent numbers; if all sources fail, leave gaps in `data_gaps`.

## T-date
1. `tool_trade_date_hist_sina` calendar + Asia/Shanghai clock (before 15:05 → previous closed day)
2. Weekday heuristic if calendar unavailable

## Two-market turnover (亿元)
1. `ak.stock_sse_deal_daily` + `ak.stock_szse_summary` (both must parse)
2. Eastmoney index kline amounts: `http://push2his.eastmoney.com` secid `1.000001` + `0.399001` (sum)
3. Sina HQ / Tencent QT index amounts (`sh000001` + `sz399001`)
4. Spot sum: `stock_zh_a_spot_em` then `stock_zh_a_spot` (sina)
5. **Never** blank if any spot/index sum works. **Never** use crude SSE×2.2 scale as primary hist method.

## Turnover MA5 / MA20
1. Persist successful daily amounts to `cache/turnover_daily.json`
2. Merge Eastmoney SH+SZ index kline history
3. Compute MA from cache points ≤ T; mark thin series honestly (`gap_flags`)

## Limit-up / limit-down
1. `stock_zt_pool_em` / `stock_zt_pool_dtgc_em` (or direct push2ex DT HTTP)
2. `stock_market_activity_legu` (真实涨停/真实跌停)
3. Spot estimate: 涨跌幅 ≥9.8 / ≤-9.8 (ChiNext/STAR/ST ≈19.8)
4. Prefer **max**(legu, pool, spot-estimate) with source tags — do not leave dt=0 silently after SSL failure without spot estimate

## Spot universe
1. `stock_zh_a_spot_em` (retry+timeout)
2. `stock_zh_a_spot` (sina)
3. Normalize to common schema (`sh`/`sz` codes)

## Stock daily history
1. Local parquet `/workspace/ashare-kline/raw/daily/{code6}.parquet`
2. `stock_zh_a_hist` (eastmoney)
3. `stock_zh_a_daily` (sina)
4. `stock_zh_a_hist_tx` (tencent)
5. Append T bar from spot when hist lags

## Index spot / hist
- Spot: sina HQ direct (`hq.sinajs.cn`) then `stock_zh_index_spot_sina`
- Hist: `stock_zh_index_daily` then Eastmoney index kline HTTP

## Industry
1. `stock_individual_info_em`
2. Eastmoney `CompanySurveyAjax` (`sshy` / `sszjhhy`)
3. Last resort: `未知` + 信息不足

## Pipeline wiring
- `run_screen.py` → turnover, breadth/zt-dt, spot, indices via `data_sources`
- `stage2b.py` → `fetch_stock_hist` / `fetch_industry` / `append_t_bar_from_spot`
- Smoke: `test_fallbacks.py` → `fallback_status.json`
- `build_near_pool.py` / end of `stage2b.py` → `near_breakout_pool.json`
- `intraday_watch.py` → batch quotes (see Intraday section)
- `dingpan_scrape.py` → optional external_hint (best-effort)

## Intraday batch quotes (near-breakout watch)
Scripts:
- `intraday_watch.py` — **one run = one compressed batch** for all pool codes (never per-stock heavy loops)
- `intraday_session_loop.py` — **authoritative cadence**: after EACH query finishes, wait
  `clamp(180 + random(0..120), 150, 600)` seconds, then query again (not wall-clock cron every 3m;
  platform `@every` floor is 5 minutes)
- `probe_rate_limits.py` — gentle probe (≤~20 req) → `cache/rate_limit_notes.json`

Fallback order (stop when ≥50% codes quoted):
1. **Sina HQ batch** — `https://hq.sinajs.cn/list=sz301072,sh600000,...`
   - Prefer **single** `list=` for whole pool when ≤80; Referer `finance.sina.com.cn`; timeout 12s
   - Inter-chunk sleep from `rate_limit_notes.json` (default **0.05–0.2s**); back off on 403/429/empty
2. **Tencent QT batch** — `http://qt.gtimg.cn/q=sz301072,...`
   - **Only if Sina insufficient**; same chunk/sleep policy
3. **Eastmoney push2 ulist** — `https://push2.eastmoney.com/api/qt/ulist.np/get?secids=...`
   - **Last resort**; chunk ≤40; timeout 20s; sleep 0.6–1.2s; use rarely

Cache: `cache/intraday_last.json`. Outputs: `intraday_snapshot.json`, `intraday_alert.md`, `intraday_status.json`.
Session loop: `cache/session_loop_state.json`, `cache/session_loop.pid`, `cache/session_loop.log`.

Rate-limit / cadence rules:
- Do **not** hammer; session-loop post-completion sleep ~3m + jitter (total wait ≤10m)
- Outside 09:30–11:30 / 13:00–15:00 Asia/Shanghai weekdays → quiet exit (`OUTSIDE_SESSION`)
- Prefer Sina → Tencent; EM only if both fail
- Push only on material events; quiet on `NO_CHANGE`; no buy advice

## Near-breakout pool (close-side)
- Builder: `build_near_pool.py` (also called at end of `stage2b.py`)
- Files: `near_breakout_pool.json` / `.md`
- Caps by regime: R0=0, R1≤15, R2≤25, R3≤40, R4≤50
- Does **not** loosen S/A hard gates; pool is separate from core push

## 盯盘助手 / external scrape

- Status: **盯盘助手 proper = not usable** (no stable public URL without login)
- Best-effort: Eastmoney `push2ex.getTopicZTPool` (1 req, timeout 8s) — **worked** on 2026-09-21 smoke (n=34 hints, 0 overlap with near pool)
- Helper: `dingpan_scrape.py` → `cache/dingpan_scrape_status.json`; merges overlap as `external_hint`
- If blocked/SSL/login-walled: skip, do not hang pipeline

