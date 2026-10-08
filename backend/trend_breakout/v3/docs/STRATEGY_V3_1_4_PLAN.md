# 趋势确认突破雷达 TrendConfirmationBot V3.1.4 — Major Big-Move Gate

> Paraphrased from the user's V3.1.4 locked decision after V3.1.3 regression.  
> Fixtures (eval-only): **600396 华电辽能**, **601579 会稽山**.  
> Replay: **2024-01-02 → 2026-09-21** (same FeatureSnapshot path as V3.1.3).  
> Constraints: **no symbol/date if-branches**; **no buy tips**; **no frontend**; live AM/noon/close publish **untouched**.  
> **Do NOT retune** A+ / Tech / Fund / Sector / Exhaustion thresholds.  
> **Do NOT change** Hysteresis core or ActiveTrendPool exit rules.  
> Keep V3.1.2 hygiene + V3.1.3 CandidateWave / Major|Sub / WaveMerge / consumption split.  
> 禁止荐买 / 预测必涨. Additive / regression-only path. **No lookahead**.

---

## 0. Why V3.1.4

V3.1.3 stopped within-trend Wave fragmentation (Major/Sub hierarchy), but **every Trend still got a Major on first confirm**. Small / failed moves (+1%~+8%, losers) diluted Major win-rate and 回报明细.

User wants only **大行情** as Major:

- 华电: MajorCount → ~1 (the +475% wave 2026-01-20→06-11); small Majors gone.
- 会稽: MajorCount → ~2 (+82% and +128%); small ones gone.

---

## 一 — Non-goals (unchanged)

- No frontend / LLM / recommender / buy language.
- No live AM/noon/close publish cutover.
- No `if code in {600396, 601579}` or date-special cases.
- No retune of A+/Tech/Fund/Sector/Exhaustion score thresholds.
- No Hysteresis core change; no ActiveTrendPool exit-rule change.
- Keep V3.1.2: TrendFailure / TrendId / WaveAnchorValid=0 / ReuseAfterFailure=0 / MAIN_RISE / COOLING→NEW_WAVE / Exhaustion decoupled.
- Keep V3.1.3: CandidateWave → Major/Sub + WaveMerge; WaveConsumption Major/Sub split; Exhaustion→Major default; EntryWindow can→Sub; A+ WaveCount-free.
- Do not retune EntryWindow thresholds except to reference Major vs Candidate correctly if needed.

---

## 二 — Major big-move gate (locked)

Candidate → Major promotion requires **as-of (data ≤ t only, no lookahead)**:

1. `PeakGainFromAnchor >= 30%`  
   - Peak = max(peak high, max close) since WaveAnchor up to t  
   - Gain = Peak / AnchorPrice − 1
2. **AND** `WaveAge >= 20` trading days (since WaveAnchor)
3. **PLUS** existing V3.1.3 confirm rules (breakout held 1–2 days, MA20 up, HigherLow / HH-HL intact, etc.)

Constants (config, not symbol-specific):

```
MAJOR_MIN_PEAK_GAIN_PCT = 0.30
MAJOR_MIN_AGE_DAYS = 20
```

---

## 三 — Behavior rules

1. Until peak gain ≥30% **AND** age ≥20, a confirmed structure stays **CANDIDATE** (or Developing Candidate), **NOT** Major.
2. If Trend fails before the gate → **FailedCandidate**; never count as Major in metrics / win-rate denominator.
3. Rebreakouts while still Candidate (no confirmed Major yet): stay Candidate / merge; **do NOT invent a Major** just to attach a Sub. Sub only attaches under an **existing confirmed Major**.
4. Once Major is promoted, subsequent cooling→rebreakout Sub rules **unchanged** from V3.1.3.
5. Metrics: report MajorWinRate only on Majors that passed the big gate; keep CandidateFalseBreak separate. Also report MajorCount vs V3.1.3.

---

## 四 — PeakGainFromAnchor (no lookahead)

On each day t while Candidate (or Major tracking):

- Maintain `peak_high_since_anchor` and `peak_close_since_anchor` from WaveAnchor date through t only.
- `peak_px = max(peak_high, peak_close)` (whichever defined).
- `PeakGainFromAnchor = peak_px / anchor − 1` when anchor > 0.
- Never peek at future bars.

---

## 五 — Lifecycle sketch

```
Trend CONFIRM / create_wave
  → birth CANDIDATE (Developing; WaveAnchor = breakout/confirm)
  → hold 1–2d + MA20↑ + HH/HL  (V3.1.3 structure confirm; still Candidate)
  → each day: update PeakGain / WaveAge
  → if PeakGain≥30% AND Age≥20 AND structure_ok → PROMOTE MAJOR
  → if TREND_FAILURE before promote → FailedCandidate

COOLING rebreakout while still Candidate (no Major):
  → Candidate merge / continue; no Sub; no invented Major

COOLING rebreakout after confirmed Major:
  → V3.1.3 Candidate→SUB (shallow) or Candidate→new Major (deep+gate)
```

---

## 六 — Metrics

| Metric | Note |
|---|---|
| MajorCount / MajorWinRate | only Majors that passed big gate |
| CandidateFalseBreak / FailedCandidate | separate; not in Major denominator |
| MajorCount vs V3.1.3 | side-by-side |
| Fragmentation / AnchorInvalid / ReuseAfterFailure | hygiene; last two must stay 0 |
| Demoted list | which V3.1.3 Majors never promoted / FailedCandidate |

Win-rate definition (report clearly): end-to-end Major return > 0 (or peak return — state in report). Prefer **end-to-end** (close1/close0 − 1 > 0) unless noted.

---

## 七 — Focus checks

- 华电 2026-01-20→06-11 still **ONE** Major (peak≥30% age≥20).
- 会稽 2026-08-12→09-21 still **ONE** Major (peak≥30% age≥20).
- 会稽 +82% wave (2025-04) should remain as Major if gate passes as-of.

---

## 八 — Implementation order

1. Save this plan → `v3/docs/STRATEGY_V3_1_4_PLAN.md`.
2. Config: `V314_MAJOR_GATE` with `MAJOR_MIN_PEAK_GAIN_PCT=0.30`, `MAJOR_MIN_AGE_DAYS=20`.
3. Types: PeakGainFromAnchor / FailedCandidate fields on WaveInfo / events as needed.
4. TrendWaveEngine: create_wave → Candidate; daily gate; FailedCandidate on fail; no Sub without Major.
5. TrendStateEngine: pass `high` as-of for peak gain; keep state core.
6. Regression `regression_v314.py` mirroring v313; window 2024-01-02→2026-09-21; fixtures only.
7. Report + artifacts under `diagnostics/`.
8. Verify `publish_close_v3` md5 unchanged.

---

## § Acceptance checklist

1. Plan doc saved at `STRATEGY_V3_1_4_PLAN.md`.
2. Major gate: PeakGain≥30% AND Age≥20 (as-of, no lookahead).
3. V3.1.3 confirm rules still required before Major.
4. Confirmed structure stays Candidate until gate.
5. Fail before gate → FailedCandidate (not Major).
6. No Sub without existing confirmed Major; no invented Major.
7. Post-Major Sub rules unchanged from V3.1.3.
8. Keep V3.1.2/3 hygiene; A+ WaveCount-free; no A+/threshold retune.
9. Full replay both fixtures; hierarchy + gate metrics emitted.
10. Report: Trend / MajorWave / SubWave / Wave诊断 + vs V3.1.3 + demoted list.
11. AnchorInvalid=0, ReuseAfterFailure=0.
12. 华电 Jan–Jun ONE Major; 会稽 Aug–Sep ONE Major.
13. No symbol/date strategy branches; no buy tips; no frontend; no lookahead.
14. `publish_close_v3` / live AM/noon/close **untouched**.

---

## Code layout (additive)

| Piece | Path |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_4_PLAN.md` |
| Config | `v3/v31/config.py` (`V314_MAJOR_GATE`) |
| Types | `v3/v31/types.py` |
| TrendWave | `v3/engines/trend_wave.py` |
| TrendState | `v3/engines/trend_state.py` (pass high; keep state core) |
| Regression | `v3/v31/regression_v314.py` |
| Report | `diagnostics/V3.1.4_REGRESSION_HUALIADIAN_HUIJISNAN.md` |
| Artifacts | `diagnostics/v314_*` |

Grading stays `grade_candidate_v311`. Live publish stays V3.0.
