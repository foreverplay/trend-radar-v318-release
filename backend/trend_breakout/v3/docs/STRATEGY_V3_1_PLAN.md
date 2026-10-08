# 趋势确认突破雷达 TrendConfirmationBot V3.1 — Strategy Plan

> Paraphrased / structured from the user's V3.1 upgrade intent + Phase-1 inventory.  
> **Correction (section 二十)**: 华电辽能 code is **600396** (NOT 600550 as in an earlier draft).  
> Regression fixtures: **600396 华电辽能** + **601579 会稽山**.  
> Constraints: no symbol-specific branches; no buy tips; no future data; Discovery universe remains top600; do not touch live AM/noon/close publish in early phases.  
> 禁止荐买 / 预测必涨.

---

## 0. Goals

1. Fix Plan A+ **漏检** of mid-trend leaders (RecScore often A+ while Plan drops to B/WATCH).  
2. Separate **exhaustion / overheat risk** from **trend failure**.  
3. Keep high grades alive across a trend wave via **ActiveTrendPool + hysteresis**.  
4. Soften Fund hard-kill into **FundRisk** when tech/cont/HTF are strong.  
5. Stay additive to V3.0 engines; live publish cutover is a later phase.

---

## 一～十五 — V3.0 baseline (unchanged math)

Retain V3.0 wrappers: Technical / Market / Sector / Fundamental / Risk / Continuity / Lifecycle / Exit.  
Plan grades remain threshold-matrix based (not RecScore≥90).  
See `STRATEGY_V3_0.md` and `V3.1_PHASE1_INVENTORY.md`.

---

## 十六 — Soft Fund → FundRisk

- If `fund_score` present and `fund < 65` → still blocks A+ (hard).  
- If `fund ∈ [65, 70)` and Tech/Cont/Sector/HTF meet strength path → **allow Plan A+**, tag **FundRisk** (observation risk, not filter).  
- Missing fund → skip dim (V3.0 behavior).  
- Rationale: 会稽山 fund≈68.4 can never Plan-A+ under V3.0 hard door.

---

## 十七 — Cont / Tech strength path (confirmed states)

When TrendState ∈ {CONFIRMED, MAIN_RISE, ACCELERATION, NEW_WAVE} **and** HigherTimeframe confirms (e.g. monthly UP/STRONG):

| Path | Tech | Cont |
|---|---:|---:|
| Classic A+ | ≥85 | ≥80 |
| **Strength A+** | ≥80 | ≥75 |

Sector still ≥70 when present. Consumption uses **wave** consumption (十八).  
Covers near-misses (e.g. tech=84, cont=78.5).

---

## 十八 — OVERHEAT / Exhaustion ≠ grade cap

- V3.0: `OVERHEATED` → max WATCH (kills mid-main-rise A+).  
- V3.1: **OVERHEAT / high consumption → ExhaustionRisk annotation**, not automatic grade demotion.  
- Grade may remain A+/A while structure/trend state still valid.  
- Board copy must label exhaustion risk clearly (no buy language).

---

## 十九 — WaveConsumption resets per wave

- `TrendWaveEngine` assigns wave_id; on **NEW_WAVE**, reset wave consumption accumulator.  
- A+ consumption gate uses **wave** consumption, not lifetime/session blob that never resets.  
- ExhaustionEngine may still raise ExhaustionRisk when wave consumption is extreme.

---

## 二十 — Regression fixtures (CORRECTED)

| User label | code6 | name |
|---|---|---|
| 花店辽能 / 华电辽能 | **600396** | 华电辽能 |
| 会稽山 | **601579** | 会稽山 |

**Do not** use 600550. No `if code in {600396, ...}` strategy branches — fixtures are eval-only.  
Deliverable: `diagnostics/V3.1_REGRESSION_HUALIADIAN_HUIJISNAN.md` with unified table + V3.0 vs V3.1 counterfactual columns.  
Do not run full-market before this two-stock report.

---

## 二十一 — TrendStateEngine

States (observation, not advice):

- `NONE` / `OBSERVE`
- `CONFIRMED` — structure + continuity confirm trend
- `MAIN_RISE` — established advance
- `ACCELERATION` — extension / accel
- `NEW_WAVE` — fresh wave after pullback/hold inside larger trend
- `TREND_FAILURE` — structure broken (exits pool)

Maps from / alongside V3 Lifecycle; does not invent buy signals.

---

## 二十二 — TrendWaveEngine

- Detect wave boundaries (pullback→resume / platform→second breakout / new impulse).  
- Emit `wave_id`, `wave_age_days`, `wave_consumption`.  
- Reset consumption on NEW_WAVE (ties to 十九).

---

## 二十三 — HigherTimeframeEngine

- Monthly (and optional weekly) trend: UP / STRONG_UP / FLAT / DOWN.  
- Used as **gate softener** for strength path + hysteresis, not as RecScore rewrite.  
- No lookahead: bars `date <= t_date` only.

---

## 二十四 — ExhaustionEngine

- Inputs: wave consumption, limit-up density, extension vs MA, OH flags.  
- Output: `ExhaustionRisk` level (NONE / WATCH / HIGH) + tags.  
- **ExhaustionRisk ≠ TREND_FAILURE**. Only structure break / broken risk forces failure exit.

---

## 二十五 — ActiveTrendPool + grade hysteresis

- **Discovery**: still slope **top600** (unchanged discovery universe).  
- **Pool entry**: when Plan grade hits A+ or A (V3.1 rules).  
- **Pool stay**: keep until `TREND_FAILURE` (or BROKEN), not until OVERHEAT.  
- **Hysteresis**: while in pool, do not drop below prior core grade on tiny Cont/Tech dips; recompute with persistence floors; failure clears pool + grade.

---

## 二十六 — A+ matrix summary (V3.1)

| Rule | V3.0 | V3.1 |
|---|---|---|
| Tech/Cont classic | 85 / 80 | same |
| Tech/Cont strength | — | 80 / 75 if state+HTF |
| Fund | ≥70 hard | ≥70 or soft [65,70)+FundRisk |
| Sector | ≥70 if present | same |
| Consumption | session &lt;70 | **wave** &lt;70 (reset per wave) |
| OVERHEAT | max WATCH | ExhaustionRisk only |
| RISK=HIGH from limit-up count | blocks A+ | prefer not sole A+ veto in main-rise; BROKEN still hard |
| Lifecycle allow | SB/MR/ACCEL | + CONFIRMED / NEW_WAVE via TrendState |
| Persistence | none | ActiveTrendPool until TREND_FAILURE |

---

## 二十七 — Implementation phases (this program)

| Phase | Work | Live publish |
|---|---|---|
| 1 | Inventory + this plan doc | untouched |
| 2 | Scaffold engines + types_v31 / config_v31 | untouched |
| 3 | `grading_v31.grade_candidate_v31` | untouched |
| 4 | ActiveTrendPool + hysteresis | untouched |
| 5 | Two-stock regression MD | untouched |
| 6+ | Wire bot path behind flag; broader backfill | later approval |
| 9 | Publish cutover | **only with explicit approval** |

---

## 二十八 — Non-goals

- No frontend / LLM / new recommender.  
- No refactor of V2 structure/fund/sector/risk math.  
- No BacktestEngine → live selection.  
- No symbol-specific patches for 600396/601579.

---

## 二十九 — Acceptance (regression)

For 600396 & 601579 report:

1. Unified daily (or critical-day) table: tech/cont/fund/sector/cons/life/state/exhaustion/wave_cons/grade_v30/grade_v31/in_pool.  
2. Count A+ days V3.0 vs V3.1 (counterfactual).  
3. Confirm no lookahead; confirm live publish files unchanged.
