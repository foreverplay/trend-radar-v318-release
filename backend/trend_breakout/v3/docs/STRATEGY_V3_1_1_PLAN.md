# 趋势确认突破雷达 TrendConfirmationBot V3.1.1 — Correction Plan

> Paraphrased from the user's V3.1.1 correction intent after V3.1 regression (华电辽能 / 会稽山).  
> Fixtures (eval-only): **600396 华电辽能**, **601579 会稽山**.  
> Constraints: **no symbol/date if-branches**; **no buy tips**; **no frontend**; live AM/noon/close publish **untouched**.  
> 禁止荐买 / 预测必涨. Additive / feature-flag / regression-only path.

---

## 0. Why V3.1.1

V3.1 fixed Plan A+ 漏检 on critical samples, but left structural debt:

1. **TrendGrade conflated with entry timing** — hysteresis / OH / consumption mixed into one grade axis.  
2. **Hysteresis too strong** — could keep A+ through OVERHEAT/HIGH exhaustion (cover-up), not just score jitter.  
3. **WaveConsumption still an A+ hard gate** — mid-wave leaders still die on cons≥70.  
4. **Strength path implicit** — `path=strength` without a formal CLASSIC vs STRENGTH matrix config.  
5. **FundRisk was boolean** — Fund∈[65,70) soft-ok, but no NONE|WATCH|HIGH|DATA_INSUFFICIENT ladder; only HIGH should block A+.  
6. **ExhaustionRisk levels / role** — must stay risk layer; never alone → TREND_FAILURE or TrendGrade=WATCH without structure damage.  
7. **Dual CSV truth** — `kill_reason_critical_fullpatch.csv` vs `kill_reason_samples_fullsec.csv` disagreed on sector (e.g. 2024-05-15: 88 vs 62); regression must use **one FeatureSnapshot builder**.  
8. **Critical-sample-only replay** — need full day-by-day hist walk + persistence / false-dropout / wave metrics.

---

## 一 — Non-goals (unchanged)

- No frontend / LLM / recommender.  
- No live AM/noon/close publish cutover.  
- No `if code in {600396, 601579}` or date-special cases.  
- No buy language. Discovery universe stays top600 when discovery is involved.

---

## 二 — TrendGrade vs EntryWindow (split)

| Axis | Values | Role |
|---|---|---|
| **TrendGrade** | A+ / A / B / WATCH / FILTER (same Grade enum) | Trend quality / Plan band |
| **EntryWindow** | `PRIME` \| `CONFIRMED` \| `FOLLOW` \| `EXTENDED` \| `NO_ENTRY` | Timing / position / consumption / extension |

**Hard rule:** EntryWindow **never folds into** TrendGrade. Overheat / high wave consumption / extension → EntryWindow + ExhaustionRisk / RiskWarning, not automatic grade demotion.

---

## 三 — Hysteresis (tightened)

Purpose: **dampen small Cont/Tech jitter only** while in ActiveTrendPool.

**May persist** prior A+/A when dips ≤ configured slack (default tech/cont slack **2.0**, not 5.0) and no invalidate fires.

**Force-invalidate** (clear hysteresis + allow natural regrade; pool exit only on TREND_FAILURE):

1. `TrendState == TREND_FAILURE`  
2. `Close < TrendBreakPrice` (structure breakout reference when known)  
3. `MA20Slope < 0` confirmed (e.g. MA20(T) < MA20(T−5))  
4. HigherLow break (structure / swing HL broken)  
5. `Cont < 70` for **2 consecutive** graded days  
6. Structure re-break / BROKEN risk  

**Forbidden:** using hysteresis to paper over OH→WATCH (OH must not cap TrendGrade; hysteresis is not the OH fix).

---

## 四 — WaveConsumption / consumption

- **Remove** WaveConsumption / session consumption as **A+ hard gate**.  
- Route consumption into: **EntryWindow**, **ExhaustionRisk**, **RiskWarning** tags only.  
- Wave engine still emits `wave_id` / `wave_consumption` for metrics and risk.

---

## 五 — A_PLUS_PATH_CONFIG (formal)

```text
A_PLUS_PATH_CONFIG = {
  CLASSIC:  tech≥85, cont≥80, sector≥70 (if present), fund per FundRisk rules
  STRENGTH: tech≥80, cont≥75, sector≥70 (if present),
            requires TrendState ∈ {CONFIRMED, MAIN_RISE, ACCELERATION, NEW_WAVE}
            AND HTF_OK
}
```

No `path=strength` without matching this config. Classic tried first; strength second when eligible.

---

## 六 — FundRisk ladder

| FundScore | FundRisk | Blocks A+? |
|---|---|---|
| missing / insufficient | `DATA_INSUFFICIENT` | No (skip dim, V3.0-like) |
| ≥ 70 | `NONE` | No |
| [65, 70) | `WATCH` | **No** |
| < 65 | `HIGH` | **Yes** |

Only **HIGH** blocks A+. WATCH is observation risk (会稽山 ≈68.4).

---

## 七 — ExhaustionRisk (risk layer)

Levels (V3.1.1): `NORMAL` | `ELEVATED` | `HIGH`  
(Aliases: NONE→NORMAL, WATCH→ELEVATED for V3.1 compat.)

- Inputs: wave/session consumption, limit-up density, extension vs MA, OH flag, **price-efficiency** (gain vs volume / upper-wick waste) when feasible.  
- **Never alone** → `TREND_FAILURE` or TrendGrade=`WATCH` without structure damage.  
- Board / regression annotate clearly; no buy language.

---

## 八 — ActiveTrendPool

- Enter on TrendGrade ∈ {A+, A}.  
- **Keep until TREND_FAILURE only** (BROKEN / failure).  
- OVERHEAT / Exhaustion HIGH / EntryWindow EXTENDED do **not** eject.

---

## 九 — FeatureSnapshot (single source of truth)

Unify builder used by **all** V3.1.1 regression:

- tech / lifecycle / OH / close / MAs / structure refs  
- sector (legacy SectorScore as-of T)  
- fund  
- continuity + session consumption  
- weekly/monthly HTF labels  
- market regime / RISK_OFF as-of T  

### Why critical vs fullsec CSVs differed

| Artifact | Sector behavior | Consequence |
|---|---|---|
| `kill_reason_critical_fullpatch.csv` | Peer-patched / correct sector enrich | e.g. 600396 2024-05-15 sector=**88** |
| `kill_reason_samples_fullsec.csv` | Often depressed / incomplete legacy sector on sparse walk | same day sector=**62** → A+ rare |

**Do not mix** those CSVs as dual truth. V3.1.1 regression builds FeatureSnapshot live (or from a single snapshot cache produced by the same builder).

---

## 十 — MarketRegime / RISK_OFF

- Prefer `history/market_regime_daily.{jsonl,csv}` when present.  
- **As-of historical only** (no future rows).  
- Missing day → default `NORMAL` / risk_off=false (document in report); do not invent future regimes.

---

## 十一 — Full day-by-day replay metrics

For each fixture over the continuous hist window actually run:

| Metric | Definition |
|---|---|
| ActiveTrendDuration | Days in ActiveTrendPool (sum / max streak) |
| APlusDuration | Days TrendGrade=A+ |
| TrendPersistenceRate | ActiveTrend days / days with TrendState in confirmed-family |
| FalseDropoutRate | Days grade dropped A+/A→below while state still confirmed-family & no invalidate / no TREND_FAILURE |
| TrendWaveCount | Distinct engine `wave_id` observed |
| ValidWaveCount | Waves with age≥N and advance≥threshold (engine heuristic) |
| WaveRecognitionRate | ValidWaveCount / TrendWaveCount (if manual waves N/A) |

If manual wave labels unavailable: report WaveCount + per-wave duration/return from engine.

---

## 二十六 — Implementation order (this program)

1. Write this plan doc.  
2. Types + `A_PLUS_PATH_CONFIG` / FundRisk / EntryWindow / Exhaustion NORMAL|ELEVATED|HIGH.  
3. FeatureSnapshot builder + MarketRegime as-of reader.  
4. `grade_candidate_v311` (split EntryWindow; remove cons A+ gate; formal paths; FundRisk; tight hysteresis).  
5. Exhaustion price-efficiency tweak (optional, best-effort).  
6. Confirm ActiveTrendPool exit = TREND_FAILURE only.  
7. Full day-by-day replay both fixtures (longest continuous window feasible).  
8. Diagnostics MD + JSON/CSV; verify `publish_close_v3` untouched.

---

## 二十七 — Code layout (additive)

| Piece | Path |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_1_PLAN.md` |
| Types/config | `v3/v31/types.py`, `v3/v31/config.py` (V3.1.1 keys) |
| FeatureSnapshot | `v3/v31/feature_snapshot.py` |
| EntryWindow | `v3/v31/entry_window.py` |
| Regime as-of | `v3/v31/market_regime_asof.py` |
| Grading | `v3/grading_v311.py` |
| Regression | `v3/v31/regression_v311.py` |
| Report | `diagnostics/V3.1.1_REGRESSION_HUALIADIAN_HUIJISNAN.md` |

V3.0 `grade_candidate` + V3.1 `grade_candidate_v31` remain for counterfactual columns. Live publish stays on V3.0.

---

## 二十八 — Live publish

**Do not modify** `v3/publish_close_v3.py` or AM/noon live routines. Cutover is a later explicit approval.

---

## 二十九 — Acceptance checklist (regression)

1. Plan doc saved at `STRATEGY_V3_1_1_PLAN.md`.  
2. TrendGrade ⊥ EntryWindow (EntryWindow never folded into grade).  
3. Hysteresis = jitter only; invalidate list enforced; no OH→WATCH via hysteresis cover-up.  
4. Consumption **not** an A+ hard gate; routed to EntryWindow / Exhaustion / RiskWarning.  
5. `A_PLUS_PATH_CONFIG` CLASSIC vs STRENGTH explicit.  
6. FundRisk NONE|WATCH|HIGH|DATA_INSUFFICIENT; 65–70 → WATCH; only HIGH blocks A+.  
7. ExhaustionRisk risk-layer only; never alone → TREND_FAILURE / TrendGrade=WATCH.  
8. ActiveTrendPool until TREND_FAILURE only.  
9. Single FeatureSnapshot builder; critical vs fullsec dual-truth documented, not mixed.  
10. MarketRegime / RISK_OFF as-of (no future).  
11. Full day-by-day replay both stocks; metrics reported; date span stated.  
12. Table 1 full-cycle summary; Table 2 key dates V3.0 / V3.1 / V3.1.1.  
13. No symbol/date strategy branches; no buy tips; no frontend.  
14. `publish_close_v3` / live AM/noon/close untouched.

---

## 三十 — Fixtures & report

| code6 | name |
|---|---|
| 600396 | 华电辽能 |
| 601579 | 会稽山 |

Deliverable: `diagnostics/V3.1.1_REGRESSION_HUALIADIAN_HUIJISNAN.md` + supporting JSON/CSV under `diagnostics/`.
