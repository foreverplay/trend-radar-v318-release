# 趋势确认突破雷达 TrendConfirmationBot V3.1.2 — Trend/Wave/State Lifecycle Fix

> Paraphrased from the user's V3.1.2 lifecycle correction intent after V3.1.1 regression (华电辽能 / 会稽山).  
> Fixtures (eval-only): **600396 华电辽能**, **601579 会稽山**.  
> Replay: **2024-01-02 → 2026-09-21** (same as V3.1.1).  
> Constraints: **no symbol/date if-branches**; **no buy tips**; **no frontend**; live AM/noon/close publish **untouched**.  
> **Do NOT retune** A+ / Tech / Fund / Sector / Exhaustion thresholds — lifecycle/state/wave only.  
> 禁止荐买 / 预测必涨. Additive / regression-only path.

---

## 0. Why V3.1.2

V3.1.1 fixed Grade/EntryWindow/FundRisk/hysteresis, but left **Trend/Wave/State structural debt**:

1. **TrendFailure** not aligned with ExitEngine structure (false breaks / slope / HL).  
2. **TrendId** missing — wave_ids reused across failures; no clean trend identity.  
3. **WaveAnchor** = arbitrary trough from hist start → WaveAnchorInvalid (e.g. wave 76 age=192 from 2024-01-02).  
4. Wave create/end too loose — not gated on CONFIRMED/MAIN_RISE/NEW_WAVE + min pullback/consolidation.  
5. **MAIN_RISE** rarely transitions from CONFIRMED (lifecycle map collapses to ACCELERATION).  
6. No **COOLING → NEW_WAVE** path; **ACCELERATION** overused as default (OH→ACCEL).  
7. Exhaustion HIGH must **not** force COOLING/FAILURE.  
8. WaveConsumption vs TrendConsumption not cleanly separated; neither is A+ hard gate (already true in 3.1.1 — keep).  
9. No unified **TrendStateSnapshot** + **State Transition Log**.

---

## 一 — Non-goals (unchanged)

- No frontend / LLM / recommender.  
- No live AM/noon/close publish cutover.  
- No `if code in {600396, 601579}` or date-special cases.  
- No retune of A+/Tech/Fund/Sector/Exhaustion score thresholds.  
- No buy language. Discovery universe stays top600 when discovery is involved.

---

## 二 — TrendFailure lifecycle (shared structure with ExitEngine)

`TREND_FAILURE` confirmed only when **all** of:

1. `Close < TrendBreakPrice` (ExitEngine StructureSupport − ATR14×mult, or snap break ref)  
2. `MA20Slope < 0` (MA20(T) < MA20(T−5))  
3. HigherLow break (structure / swing HL broken)

**1-day false-break allowance:** if Close briefly pierces Break but reclaims next day (or same-day low pierces / close holds), do **not** confirm FAILURE — tag `FALSE_BREAK`, stay prior state / PROTECT observation.

Exhaustion HIGH / OVERHEAT / EntryWindow EXTENDED **never alone** → TREND_FAILURE.

---

## 三 — TrendId

- Allocate monotonic `trend_id` when entering CONFIRMED (or NEW_WAVE start of a fresh trend after OBSERVE).  
- On confirmed **TREND_FAILURE**, retire trend_id; next confirm gets **new** trend_id.  
- **Never reuse** trend_id or wave_id sequence across FAILURE. Wave ids restart (or continue globally but keyed by trend_id — report both).

---

## 四 — WaveAnchor

- Anchor = **breakout / confirm** reference, not arbitrary hist low:  
  - first CONFIRMED day: platform/breakout support or confirm-day low/close  
  - NEW_WAVE: rebreakout day low/close after healthy cooling  
- `WaveAnchorInvalid` when anchor missing, below structure nonsense, or carried from pre-FAILURE hist trough.  
- Target metric: **WaveAnchorInvalidRate → 0**.

---

## 五 — Wave create / end

**Create** only while state ∈ `{CONFIRMED, MAIN_RISE, NEW_WAVE}` (or transitioning into them).  
**End** current wave when:

- TREND_FAILURE, or  
- healthy pullback/consolidation completes and **NEW_WAVE** rebreakout fires.

Min pullback / consolidation before NEW_WAVE (config, not symbol-specific): e.g. ≥5% pullback from wave peak **or** ≥N bar platform hold with shrinking consumption.

---

## 六 — MAIN_RISE from CONFIRMED

CONFIRMED → MAIN_RISE when structure intact AND (any):

- lifecycle emits MAIN_RISE, or  
- age_in_confirmed ≥ N and advance from WaveAnchor ≥ min_pct and Cont supportive, or  
- SECOND_BREAKOUT sustained with positive MA slope.

Do **not** skip CONFIRMED straight into ACCELERATION by default.

---

## 七 — COOLING → NEW_WAVE; ACCELERATION ≠ default

| State | Meaning |
|---|---|
| COOLING | Healthy pullback/consolidation inside active trend (Exhaustion may be HIGH) |
| NEW_WAVE | Rebreakout after cooling |
| ACCELERATION | Real extension / accel only (steep slope, limit-up cluster, large ext) — **not** OH alias |

Rules:

- Exhaustion HIGH → annotate risk; **may** allow transition to COOLING if pullback forms; **must not** force FAILURE.  
- OVERHEATED lifecycle → Exhaustion / EntryWindow, **not** auto ACCELERATION.  
- COOLING + rebreakout → NEW_WAVE (reset WaveConsumption).

---

## 八 — Consumption split

- **WaveConsumption**: resets on NEW_WAVE (and new wave create).  
- **TrendConsumption**: accumulates across waves within same trend_id; resets on new TrendId.  
- **Neither** is an A+ hard gate (V3.1.1 already removed — keep). Route to EntryWindow / Exhaustion / RiskWarning only.

---

## 九 — TrendStateSnapshot + State Transition Log

Unified daily snapshot:

`trend_id, wave_id, state, prior_state, transition, wave_anchor, wave_anchor_date, wave_anchor_kind, wave_age_days, wave_consumption, trend_consumption, trend_break_price, false_break_pending, exhaustion, tags, notes`

Emit transition log rows whenever `state != prior_state` (or wave_id / trend_id changes).

---

## 十 — Metrics (primary = state/wave)

| Metric | Target / note |
|---|---|
| WaveAnchorInvalidRate | → **0** |
| WaveReuseAfterFailureRate | = **0** |
| WaveRecognitionRate | vs V3.1.1 (expect ↑ or cleaner ValidWave) |
| WaveCount / ValidWaveCount | report deltas |
| MAIN_RISE_Days | expect ↑ vs V3.1.1 (was lifecycle-starved) |
| COOLING_Days / NEW_WAVE_Days / ACCELERATION_Days | distribution sanity |
| TrendFailure confirm vs false-break | log counts |

A+/A/B grades reported **only after** state/wave OK — secondary, no threshold retune.

---

## 四十四 — Implementation order

1. Save this plan → `v3/docs/STRATEGY_V3_1_2_PLAN.md`.  
2. Fix TrendFailure lifecycle (ExitEngine-aligned + 1-day false break).  
3. TrendId: new Trend after FAILURE; never reuse across FAILURE.  
4. WaveAnchor (breakout/confirm based).  
5. Wave create/end rules + NEW_WAVE after healthy cooling+rebreakout.  
6. MAIN_RISE properly from CONFIRMED.  
7. COOLING → NEW_WAVE; ACCELERATION ≠ default; Exhaustion HIGH ↛ COOLING/FAILURE force.  
8. WaveConsumption reset; separate TrendConsumption; neither A+ hard gate.  
9. Unified TrendStateSnapshot + State Transition Log.  
10. Full day replay both stocks (2024-01-02 → 2026-09-21).  
11. Report with 4 tables + metrics.  
12. Only after state/wave OK, report A+/A/B as secondary.

---

## 四十五 — Acceptance checklist

1. Plan doc saved at `STRATEGY_V3_1_2_PLAN.md`.  
2. TrendFailure = Close\<Break + MA20Slope\<0 + HL break; 1-day false break allowed.  
3. TrendId new after FAILURE; WaveReuseAfterFailureRate = 0.  
4. WaveAnchor breakout/confirm-based; WaveAnchorInvalidRate → 0.  
5. Wave create/end only from CONFIRMED/MAIN_RISE/NEW_WAVE (+ cooling path); min pullback/consolidation.  
6. MAIN_RISE transitions from CONFIRMED (not starved).  
7. COOLING → NEW_WAVE path exists; ACCELERATION ≠ OH default; Exhaustion HIGH does not force COOLING/FAILURE.  
8. WaveConsumption resets on new wave; TrendConsumption separate; neither A+ hard gate.  
9. TrendStateSnapshot + State Transition Log emitted.  
10. Full day-by-day replay both fixtures; span stated.  
11. Report: 4 tables + key metrics vs V3.1.1.  
12. A+/A/B secondary only; **no** A+/Tech/Fund/Sector/Exhaustion threshold retune.  
13. No symbol/date strategy branches; no buy tips; no frontend.  
14. `publish_close_v3` / live AM/noon/close **untouched**.

---

## Code layout (additive)

| Piece | Path |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_2_PLAN.md` |
| Types | `v3/v31/types.py` (+ COOLING, TrendStateSnapshot, …) |
| Config | `v3/v31/config.py` (V312 lifecycle keys only) |
| TrendState | `v3/engines/trend_state.py` |
| TrendWave | `v3/engines/trend_wave.py` |
| Regression | `v3/v31/regression_v312.py` |
| Report | `diagnostics/V3.1.2_REGRESSION_HUALIADIAN_HUIJISNAN.md` |

Grading stays `grade_candidate_v311` (no threshold retune). Live publish stays V3.0.

---

## Fixtures & report

| code6 | name |
|---|---|
| 600396 | 华电辽能 |
| 601579 | 会稽山 |

Deliverable: `diagnostics/V3.1.2_REGRESSION_HUALIADIAN_HUIJISNAN.md` + supporting CSV/JSON under `diagnostics/`.
