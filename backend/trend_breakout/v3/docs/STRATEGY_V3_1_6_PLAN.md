# 趋势确认突破雷达 TrendConfirmationBot V3.1.6 — 成熟后确认入场

> 忠实落地用户 V3.1.6 锁定意图（华电辽能 / 会稽山回归窗口）。  
> Fixtures（仅评估）：**600396 华电辽能**，**601579 会稽山**。  
> Replay：**2024-01-02 → 2026-09-21**（FeatureSnapshot 与 V3.1.5 相同）。  
> 约束：**无标的/日期特判**；**禁止荐买 / 预测必涨**；**无前端**；live AM/noon/close publish **不动**。  
> **不调高** A+ / Tech / Fund / Sector / Continuity / Exhaustion 阈值。  
> 保留 V3.1.5 MajorMoveLabel（标签 only）；EntrySignal 不再在首次突破即触发。  
> **禁止** `if date >= 2026-03-06` 或任何 symbol/date 策略分支。2026-03-06 仅为回归参照。

---

## 0. 核心原则（verbatim intent）

> **最早发现 → 成熟后确认。**  
> CONFIRMED = 检测，不是默认入场。  
> EntrySignal 只在日线结构成熟之后：突破 → 健康回撤 → 更高低点 → 再突破 → 站稳，  
> 且在大行情被证明（MajorLabel）**之前**发出——不是首次突破，也不是 MajorLabel 之后。  
> 宁可错过最早一段利润，也不把「刚开始筑势」的股票算作有效入场。

### V3.1.5 问题

V3.1.5 EntrySignal：CONFIRMED + tech≥75 + cont≥70 + EntryWindow PRIME → **立即** rising-edge。  
反例：600396 **2026-01-22** grade A（tech 89, cont 71）→ EntrySignal=true，但结构仍是 EARLY/BUILDING（首次突破刚站稳）。

---

## 一 — 四层架构

| 层 | 角色 | 时序 |
|---|---|---|
| **1. TrendDetection** | 趋势结构存在；CONFIRMED = 检测 | as-of ≤ t |
| **2. TrendMaturity** | 结构成熟度 0–100；EARLY\|BUILDING\|MATURE\|ADVANCED | as-of ≤ t |
| **3. EntrySignal** | 成熟态买入信号：state∈{MAIN_RISE,ACCELERATION,NEW_WAVE} + maturity∈{MATURE,ADVANCED} + 既有 grade/sector/HTF/fund + EntryWindow∈{PRIME,CONFIRMED} | **仅 T 及以前** |
| **4. MajorMove Evaluation** | **仅回测标签**（继承 V3.1.5）；永不喂入 EntrySignal | 可用未来 bar |

### 状态语义

- **CONFIRMED** = detection，NOT default entry
- **MAIN_RISE** = mature rising（可配 MATURE）
- **ACCELERATION** = accelerating（可配 MATURE/ADVANCED）
- **NEW_WAVE** = new mature leg（可配 MATURE）

Allowed pairs（正常态）：

| TrendState | Maturity |
|---|---|
| CONFIRMED | EARLY / BUILDING |
| MAIN_RISE | MATURE（亦可 BUILDING 若结构未完） |
| ACCELERATION | MATURE / ADVANCED |
| NEW_WAVE | MATURE |

CONFIRMED+ADVANCED 不是正常态（引擎 clamp：CONFIRMED 最高 BUILDING）。

### EntrySignal（不抬阈值）

相对 V3.1.5 **current rule** 增量：

- TrendState ∈ {**MAIN_RISE, ACCELERATION, NEW_WAVE**}（**不含** CONFIRMED）
- TrendMaturity.level ∈ {**MATURE, ADVANCED**}
- EntryWindow ∈ {PRIME, CONFIRMED}（V3.1.6 重写定义）
- Tech/Cont 达既有 A 档地板（tech≥75, cont≥70）— **不抬**
- Sector「正常」：缺失或 ≥ 60
- FundRisk HIGH → 阻断 EntrySignal
- TrendFailure / broken → 阻断
- Exhaustion HIGH → 不可 PRIME（不杀趋势；可为 EXTENDED）
- **不读** `major_id` / `major_label` / `future_peak_gain` / MajorLabelDate

**不**要求未来 PeakGain≥30% 才发信号。失败信号仍进分母。

---

## 二 — TrendMaturityEngine

Score **0–100**；level **EARLY | BUILDING | MATURE | ADVANCED**；`reasons[]`。

### 权重（非「满 20 天即成熟」；时间仅辅助）

| 分量 | 权重 | 说明 |
|---|---:|---|
| 结构成熟度 | 30 | HH+HL+MA20 向上；MA5>MA10>MA20 |
| 突破确认度 | 25 | 区分 FIRST_BREAKOUT vs REBREAKOUT/SECOND_BREAKOUT；站稳天数 |
| 趋势持续度 | 20 | 已确认家族天数、anchor 推进、MAIN_RISE/ACCEL |
| 量价确认 | 15 | 突破放量、回撤缩量 |
| 高周期确认 | 10 | HTF ok |

**不是** TechnicalScore 单独决定（Tech 89 + BUILDING 合法）。

### StructuralConfirmation

字段：`firstBreakout`, `healthyPullback`, `higherLow`, `reBreakout`, `breakoutHold`, `completed`。

- **completed** 要求核心：firstBreakout + healthyPullback + higherLow + reBreakout。
- **breakoutHold**：再突破后至少连续 **2** 日站稳；极强突破日可通过**一条通用规则**放宽 hold（高量比 + 大阳 + 收盘远超突破位）。
- **健康分歧/回撤**：不破结构、缩量、MA20 向上、HL。

### MAIN_RISE_CONFIRMED → MATURE

当同时满足：MA5>MA10>MA20、MA20 slope>0、HH、HL、valid breakout、consecutive confirm，且（StructuralConfirmation.completed **或** MatureContinuation）。

### MatureContinuation（通用路径，非特判）

已确立趋势（MAIN_RISE/ACCELERATION/NEW_WAVE）+ 收缩整理 + 放量再破 — 全标的同一规则。

---

## 三 — EntryWindow 重写（保留名称）

| Window | 定义（V3.1.6） |
|---|---|
| **PRIME** | 仅 MATURE + (MAIN_RISE\|NEW_WAVE) + 刚完成 structural confirm。CONFIRMED-early **不能** PRIME。Exhaustion HIGH **不能** PRIME。 |
| **CONFIRMED** | mature + structure complete，但不在新鲜 rebreak 槽 |
| **FOLLOW** | mature、仍有效、主升中段 |
| **EXTENDED** | 仍有效但远离结构 **或** Exhaustion HIGH。Grade 可仍 A+；EntrySignal=false |
| **NO_ENTRY** | 无趋势 / broken / TREND_FAILURE / 未成熟检测态 |

FundRisk HIGH 阻断 EntrySignal（window 可仍计算）。TrendFailure → NO_ENTRY。  
EntrySignal ≠ holding state（ActiveTrendPool 仍跟踪）。无 EntrySignal ≠ 无趋势。

### SignalMaturity

解释当日：`structure` / `breakout` / `continuation` / `volumePrice` / `htf` 子分。

---

## 四 — Labels（保持 V3.1.5，仅回测）

MajorMoveLabel：SUSTAINED / SPIKE / NON_MAJOR。  
**永不**喂入 EntrySignal / TrendGrade / EntryWindow / pool。

LeadDays = MajorLabelDate − EntrySignalDate（仅评估；信号时 bot 不知未来标签）。

Buckets（仅评估）：

| LeadDays | Bucket |
|---|---|
| 0–5 | OPTIMAL |
| 6–10 | ACCEPTABLE |
| 11–15 | EARLY |
| >15 | TOO_EARLY |

Metrics：TooEarlyRate（Lead>15 / all signals）、AcceptableLeadRate（Lead 0–10 among true major signals）、LateSignalRate（signal date ≥ label date）、MajorHit、Precision、SmallMove、40D、60D、SignalCount、Recall。

**不**优化 max MajorHitRate。**不**从分母剔除失败信号。**不**要求未来 PeakGain≥30% 才发信号。

Quality matrix：Early / Mature / Advanced × SignalCount, MajorHit, SmallMove, TooEarly, Late, 40D, 60D。

PostSignalPath：信号后状态序列直至 cooling / major label。

Per signal：SignalDate, TrendState, TrendMaturity, EntryWindow, SignalPrice, MajorLabelDate, LeadDays, Peak20/40/60/120, MaxDD20/40/60/120。

---

## 五 — Focus 检查（报告必须回答；代码无硬编码）

### 华电 600396

日期：2026-01-22, 02-10, 02-12, 02-26, 03-02, 03-06。

Acceptance：

1. **01-22** 不是成熟 EntrySignal；maturity EARLY 或 BUILDING。
2. 一月仍是 TrendDetection（CONFIRMED 族）。
3. 二月推进 BUILDING→MATURE。
4. 三月初可凭结构达 MATURE。
5. **首个 EntrySignal 在 2026-03-10 MajorLabel 之前**（不是证明之后）。

### 会稽 601579

日期：2026-08-12, 08-18, 09-09, 09-17, 09-21。

1. **08-12** CONFIRMED 不得 entry。
2. **08-18** 可为 maturity candidate。
3. 之后 EXTENDED 与 grade 独立；exhaustion high 不得再发 PRIME；趋势不因价格延伸而被杀死。

---

## 六 — 硬约束

1. 无前瞻：信号特征不含 future_* / major_label。
2. Forward returns **仅**标签引擎。
3. **不改** A+ / Tech / Fund / Sector / Continuity / Exhaustion 阈值。
4. **不改** live `publish_close_v3`、routines、frontend。
5. 无 symbol/date 策略分支。
6. 保留 V3.1.5 MajorMoveLabel；PeakGate 仍 label-only。
7. entryPrice = signal-day close。
8. 禁止荐买 / 预测必涨语言。

---

## 七 — Deliverables

| 件 | 路径 |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_6_PLAN.md` |
| Config | `v3/v31/config.py`（V316_*） |
| Types | `v3/v31/types.py`（Maturity / StructuralConfirmation / SignalMaturity） |
| TrendMaturity | `v3/engines/trend_maturity.py` |
| EntryWindow | `v3/v31/entry_window.py`（重写；保留名称） |
| EntrySignal | `v3/engines/entry_signal.py`（成熟门控） |
| Regression | `v3/v31/regression_v316.py` |
| Report | `diagnostics/V3.1.6_REGRESSION_HUALIADIAN_HUIJISNAN.md` |
| Artifacts | `v316_signals.csv`, `v316_signal_eval.json`, `v316_vs_v315_metrics.json`, `v316_regression_summary.json`, `v316_daily_replay.csv`, `v316_key_dates.csv` |

---

## §四十七 — Acceptance checklist

1. Plan 已保存至 `STRATEGY_V3_1_6_PLAN.md`。
2. TrendMaturityEngine 产出 score/level/reasons；非纯时间、非纯 Tech。
3. StructuralConfirmation 含 firstBreakout→healthyPullback→higherLow→reBreakout→hold；completed 需核心四步。
4. CONFIRMED 不默认 EntrySignal；MAIN_RISE/ACCELERATION/NEW_WAVE + MATURE/ADVANCED 才可。
5. EntryWindow：PRIME 仅成熟刚确认；CONFIRMED-early 非 PRIME；EXTENDED/Exhaustion HIGH 非 PRIME。
6. FundRisk HIGH / TrendFailure 阻断 EntrySignal。
7. 600396 01-22 EntrySignal=false 且 maturity∈{EARLY,BUILDING}。
8. 600396 首个 EntrySignal < 2026-03-10 MajorLabel。
9. 601579 08-12 无 entry；08-18 可为 maturity candidate；后期 EXTENDED 不因 grade 重发 PRIME。
10. MajorMoveLabel 不喂入 EntrySignal；LeadDays/TooEarly 仅评估。
11. 失败信号进分母；不要求未来 Peak≥30% 才发信号。
12. A+/Tech/Fund/Sector/Cont/Exhaustion 阈值未改。
13. `publish_close_v3` md5 不变；无 frontend。
14. 无 symbol/date 策略分支。
15. Table1/2/3 + Quality matrix + §四十七 PASS/FAIL + Focus。
16. 对比 V3.1.5 artifacts（SignalCount / TooEarlyRate）。

---

## Code layout（additive）

Grading 仍 `grade_candidate_v311`（live 阈值不变）。  
Live publish 仍 V3.0。  
V3.1.6 为 regression 路径：成熟引擎 + EntryWindow/EntrySignal 门控叠加在 V3.1.5 之上。
