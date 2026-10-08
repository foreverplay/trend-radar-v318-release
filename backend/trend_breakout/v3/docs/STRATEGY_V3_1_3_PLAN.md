# 趋势确认突破雷达 TrendConfirmationBot V3.1.3 — Major/Sub Wave Hierarchy

> Paraphrased from the user's V3.1.3 fragmentation-fix intent after V3.1.2 regression (华电辽能 / 会稽山).  
> Fixtures (eval-only): **600396 华电辽能**, **601579 会稽山**.  
> Replay: **2024-01-02 → 2026-09-21** (same FeatureSnapshot path as V3.1.2).  
> Constraints: **no symbol/date if-branches**; **no buy tips**; **no frontend**; live AM/noon/close publish **untouched**.  
> **Do NOT retune** A+ / Tech / Fund / Sector / Exhaustion thresholds.  
> **Do NOT change** Hysteresis core or ActiveTrendPool exit rules.  
> Keep V3.1.2 hygiene: TrendFailure / TrendId / WaveAnchorValid=0 / ReuseAfterFailure=0 / MAIN_RISE / COOLING→NEW_WAVE.  
> 禁止荐买 / 预测必涨. Additive / regression-only path. No lookahead.

---

## 0. Why V3.1.3

V3.1.2 fixed TrendFailure / TrendId / WaveAnchor / MAIN_RISE / COOLING→NEW_WAVE, but **WaveCount still fragments**:

1. Every COOLING rebreakout immediately births a formal wave_id (e.g. 600396 trend8: 01-20→04-07 Major sliced by 04-08 / 04-14 short REBREAKOUTs).  
2. Short shallow rebreaks dilute ValidWave / WaveRecognitionRate (27.8% / 40%).  
3. No Candidate gate before formal wave; no Major vs Sub hierarchy; no merge diagnostics.  
4. WaveConsumption conflates Major vs Sub; Exhaustion / EntryWindow cannot prefer the right axis.  
5. TrendGrade must never use WaveCount (keep / assert).

Focus validation (structure, not date-branches):

- 600396: 2026-01-20→04-07 = **one Major**; 04-08 / 04-14 short segments = **Sub / Candidate / MERGED**.  
- 601579: 2026-08→09 = **one Major** with Subs under parent.

---

## 一 — Non-goals (unchanged)

- No frontend / LLM / recommender / buy language.  
- No live AM/noon/close publish cutover.  
- No `if code in {600396, 601579}` or date-special cases.  
- No retune of A+/Tech/Fund/Sector/Exhaustion score thresholds.  
- No Hysteresis core change; no ActiveTrendPool exit-rule change.  
- Discovery universe stays top600 when discovery is involved.

---

## 二 — CandidateWave (before formal wave)

On COOLING rebreakout (same min cooling / pullback gates as V3.1.2):

1. Emit **CANDIDATE** (not yet MAJOR/SUB formal).  
2. Eligibility: `cooling_days ≥ 5` AND (`pullback_from_peak ≥ 8%` OR `consolidation_bars ≥ 5`).  
3. Hold **1–2 days** with **MA20 slope up** + **HH/HL structure intact** before promote.  
4. If structure fails / MA20 turns down during hold → **FAILED** (or **MERGED** into parent if parent Major exists).

First wave of a new TrendId may promote Candidate→MAJOR on confirm (or direct MAJOR on CONFIRMED create when no cooling path).

---

## 三 — WaveLevel MAJOR | SUB | MERGED

| Level | Meaning |
|---|---|
| CANDIDATE | Pending confirm; not counted as formal Major/Sub yet |
| MAJOR | Primary wave of a trend segment; Exhaustion default ref |
| SUB | Short / shallow rebreak under an active parent Major |
| MERGED | Absorbed into parent (kept for diagnostics) |

**Promote rules (config, not symbol-specific):**

- Parent Major active + rebreak **shallow/short** (e.g. age after confirm \< `min_major_age`, or advance from pivot \< `min_major_advance_pct`, or pullback depth \< deep threshold) → **SUB** (parent_major_id set).  
- Else → new **MAJOR** (prior Major COMPLETED).  
- SUB that dies immediately (age \< `min_sub_age` or false-break) → **MERGED** into parent; keep record.

---

## 四 — WaveMerge + event log

Keep MERGED records. Wave event log rows:

`CANDIDATE | CONFIRMED | MERGED | FAILED | COMPLETED` + reason + ids/dates.

---

## 五 — Consumption split

- **MajorWaveConsumption**: accumulates across Subs under same Major; resets on new Major.  
- **SubWaveConsumption**: resets on each Sub / Candidate promote.  
- **TrendConsumption**: unchanged (across Majors within TrendId).  
- **Exhaustion** refs **MajorWaveConsumption** by default (fallback session).  
- **EntryWindow** may ref **SubWaveConsumption** when `wave_level=SUB` (else Major/session).  
- **Neither** is an A+ hard gate (V3.1.1). **TrendGrade must NOT use WaveCount.**

---

## 六 — Keep from V3.1.2

TrendFailure (Close\<Break + MA20Slope\<0 + HL + 1d false-break), TrendId never reuse across FAILURE, WaveAnchor breakout/confirm-based (AnchorInvalid→0), ReuseAfterFailure=0, MAIN_RISE from CONFIRMED, COOLING→NEW_WAVE path, ACCELERATION ≠ OH default, Exhaustion HIGH ↛ force FAIL/COOLING.

---

## 七 — Metrics (primary = Major/Sub hierarchy)

| Metric | Note |
|---|---|
| MajorWaveCount / SubWaveCount / CandidateWaveCount | primary |
| MergeRate | MERGED / (SUB + MERGED + failed candidates) |
| CandidateFalseBreakRate | FAILED candidates / CandidateWaveCount |
| MajorWaveFragmentation | short/invalid Majors / MajorWaveCount (↓ vs V3.1.2 Wave fragmentation) |
| MajorWaveRecognitionRate | ValidMajors / MajorWaveCount (Valid: age≥5 & return≥5% & anchor_valid) |
| avg Major/Sub duration & return | report |
| WaveAnchorInvalidRate / ReuseAfterFailureRate | must stay **0** |
| A+/A/B | **secondary only** |

---

## 三十四 — Implementation order

1. Save this plan → `v3/docs/STRATEGY_V3_1_3_PLAN.md`.  
2. CandidateWave gate (cooling≥5, pullback≥8% or consol≥5).  
3. Confirm hold 1–2d + MA20 up + HH/HL before promote.  
4. WaveLevel MAJOR|SUB; short shallow → SUB/MERGED under parent.  
5. WaveMerge; keep MERGED records.  
6. MajorWaveConsumption vs SubWaveConsumption; Exhaustion→Major; EntryWindow can→Sub.  
7. Assert TrendGrade does not use WaveCount.  
8. Wave event log CANDIDATE/CONFIRMED/MERGED/FAILED/COMPLETED.  
9. Replay both stocks; emit hierarchy metrics.  
10. Report `diagnostics/V3.1.3_REGRESSION_HUALIADIAN_HUIJISNAN.md` with Trend / MajorWave / SubWave / Wave诊断 + vs V3.1.2 deltas.  
11. Checklist §三十四; confirm `publish_close_v3` untouched.

---

## §三十四 — Acceptance checklist

1. Plan doc saved at `STRATEGY_V3_1_3_PLAN.md`.  
2. CandidateWave before formal wave (cooling≥5, pb≥8% or consol≥5).  
3. Confirm 1–2d hold + MA20 up + HH/HL before promote.  
4. WaveLevel MAJOR|SUB; short shallow rebreaks → SUB or MERGED.  
5. WaveMerge keeps MERGED diagnostics records.  
6. Major vs Sub consumption; Exhaustion→Major default; EntryWindow can→Sub.  
7. TrendGrade does **not** use WaveCount; no A+/threshold retune.  
8. Wave event log with CANDIDATE/CONFIRMED/MERGED/FAILED/COMPLETED + reason.  
9. Full replay both fixtures 2024-01-02→2026-09-21; hierarchy metrics emitted.  
10. Report: Trend / MajorWave / SubWave / Wave诊断 tables + vs V3.1.2 deltas.  
11. V3.1.2 hygiene still PASS (AnchorInvalid=0, Reuse=0, MAIN_RISE/COOLING→NEW_WAVE).  
12. Hysteresis core + ActiveTrendPool exit rules unchanged.  
13. No symbol/date strategy branches; no buy tips; no frontend; no lookahead.  
14. `publish_close_v3` / live AM/noon/close **untouched**.

---

## Code layout (additive)

| Piece | Path |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_3_PLAN.md` |
| Types | `v3/v31/types.py` (+ WaveLevel, WaveEvent, hierarchy fields) |
| Config | `v3/v31/config.py` (`V313_WAVE` only; no A+ retune) |
| TrendWave | `v3/engines/trend_wave.py` |
| TrendState | `v3/engines/trend_state.py` (pass confirm signals; keep state core) |
| Exhaustion | `v3/engines/exhaustion.py` (Major cons default) |
| EntryWindow | `v3/v31/entry_window.py` (Sub cons optional) |
| Regression | `v3/v31/regression_v313.py` |
| Report | `diagnostics/V3.1.3_REGRESSION_HUALIADIAN_HUIJISNAN.md` |

Grading stays `grade_candidate_v311`. Live publish stays V3.0.

---

## Fixtures & report

| code6 | name |
|---|---|
| 600396 | 华电辽能 |
| 601579 | 会稽山 |

Report: `/workspace/trend-breakout/diagnostics/V3.1.3_REGRESSION_HUALIADIAN_HUIJISNAN.md`
