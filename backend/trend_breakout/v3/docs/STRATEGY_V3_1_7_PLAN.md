# 趋势确认突破雷达 TrendConfirmationBot V3.1.7 — 已实现趋势成熟确认

> 忠实落地用户「趋势确认突破 Bot V3.1.7 Plan」锁定意图。  
> Fixtures（仅评估）：**600396 华电辽能**，**601579 会稽山**，以及  
> 000949 / 002209 / 600985 / 603268 / 601666 / 603038 / 300483 / 600313 / 600026 / 600316 / 601975 / 002246 / 301149。  
> Replay：**2024-01-02 → 2026-09-21**（FeatureSnapshot 与 V3.1.5/V3.1.6 相同）。  
> 约束：**无标的/日期特判**；**禁止荐买 / 预测必涨**；**无前端**；live AM/noon/close publish **不动**。  
> **不调高** A+ / Tech / Fund / Sector / Continuity / Exhaustion **旧阈值本身**（只能在其上增加成熟度门，不能把 Tech 抬到 90）。  
> 保留 V3.1.5 MajorMoveLabel（标签 only）与 V3.1.6 成熟回归能力；V3.1.7 在其上叠加 RealizedTrend / FollowThrough / 更严 MATURE。  
> **禁止** `if date >= 某日` 或任何 symbol/date 策略分支。  
> **禁止**用未来 PeakGain / MajorLabel 决定实时 Maturity / Entry / A+。

---

## 0. 核心原则（verbatim intent）

> **二次突破本身不能直接成为 A+ 或 PRIME。A+ 必须证明趋势已经走出来。**  
> 最早发现 → 成熟后确认。CONFIRMED = 检测，不是默认入场。  
> Entry / PRIME 只在日线结构成熟、已实现涨幅与突破后延续被证明之后发出。  
> 宁可错过最早一段利润，也不把「刚开始筑势 / 仅二次突破日」的股票算作有效入场。  
> Exhaustion HIGH ≠ TrendFailure；ADVANCED 或 Exhaustion HIGH → EXTENDED，不杀趋势。

### V3.1.6 遗留问题（动机）

V3.1.6 已做到：CONFIRMED≠Entry；MATURE 后才 Entry。  
但仍可能：结构刚完成 / 二次突破当日即 MATURE/PRIME，而累计已实现涨幅或突破后延续不足。  
V3.1.7：用 **RealizedTrend**（只用 T 及以前）证明趋势已走出来；用 **BreakoutFollowThrough** 证明突破后有效延续；用更严的 MATURE 硬门槛。

---

## 一 — 五层架构（相对 V3.1.6 增量）

| 层 | 角色 | 时序 |
|---|---|---|
| **1. TrendDetection** | 趋势结构存在；CONFIRMED = 检测 | as-of ≤ t |
| **2. RealizedTrend** | 已实现趋势强度（累计/20/40/60/创新高/扩张） | **仅 T 及以前** |
| **3. TrendMaturity** | 成熟度 0–100；EARLY\|BUILDING\|MATURE\|ADVANCED；含 FollowThrough / MainRiseQuality / Structure | as-of ≤ t |
| **4. EntrySignal / EntryWindow** | PRIME/Entry 需 MATURE + WaveReturn + FollowThrough=PASS；A+ 硬条件加成熟度门 | **仅 T 及以前** |
| **5. MajorMove Evaluation** | **仅回测标签**（继承 V3.1.5）；永不喂入 Maturity/Entry/A+ | 可用未来 bar |

### 状态语义（继承 V3.1.6）

- **CONFIRMED** = detection，NOT default entry
- **MAIN_RISE / ACCELERATION / NEW_WAVE** = 可配 MATURE（仍须通过 Realized / FollowThrough / Age 硬门槛）
- CONFIRMED 最高 BUILDING，不得成熟 A+

---

## 二 — RealizedTrendEngine（新增）

**只用 T 及以前。** 禁止未来 PeakGain / MajorLabel。

### 组成权重（sum=100）

| 分量 | 权重 |
|---|---:|
| 趋势累计涨幅 TrendReturn | 30 |
| 20 日涨幅 return20 | 20 |
| 40 日涨幅 return40 | 15 |
| 60 日涨幅 return60 | 10 |
| 创新高进度 newHighProgress | 10 |
| 价格扩张质量 priceExpansion | 15 |

### 输出字段

`score`, `trendReturn`, `return20`, `return40`, `return60`, `priceExpansion`, `newHighProgress`, `realizedDays`, `currentWaveReturn`, `trendAnchorPrice`, `waveAnchorPrice`, `reasons[]`.

### 定义

- **TrendReturn** = `close / TrendAnchorPrice - 1`（TrendAnchor = 当前 TrendId 确认锚，缺省回退 WaveAnchor）
- **CurrentWaveReturn** = `close / WaveAnchorPrice - 1`（必须存在，防止长期有涨幅但当前波只是小反弹）
- **returnN** = `close / close_N交易日前 - 1`（as-of）
- **newHighProgress**：相对近 60 日高点的接近度（已创新高→满分）
- **priceExpansion**：收盘相对 MA20 扩张且未失控、实体质量（通用规则）
- **realizedDays**：自 TrendAnchor 起的交易日数（= TrendAge 辅助）

### 成熟最低（Realized 硬门槛，不是「未来必须涨 30%」）

同时满足：

1. `TrendReturn >= 15%` **且** `CurrentWaveReturn >= 8%`
2. **并且** `20D >= 8%` **或** `40D >= 12%` **或** `60D >= 15%`（三者任一）

不满足 → 不得 MATURE（最高 BUILDING）。

---

## 三 — TrendMaturityScore（V3.1.7 权重）

Score **0–100**；level **EARLY | BUILDING | MATURE | ADVANCED**。

| 分量 | 权重 |
|---|---:|
| 年龄（TrendAge，软贡献） | 20 |
| 累计涨幅 TrendReturn | 25 |
| 当前 Wave 幅度 CurrentWaveReturn | 20 |
| HH（更高高点结构） | 15 |
| HL（更高低点） | 10 |
| 突破后稳定性 | 10 |

### 时间规则

- **TrendAge >= 15 交易日** 才能 MATURE
- **Age < 15** 最高 BUILDING，不得成熟 A+
- 时间只是最低保护，**不是**固定等 20 天；也不是「满 20 天即成熟」

### 10天+30% 不能直接 MATURE

即便短窗口涨幅大，必须同时有：

- HigherLow
- MA 结构（MA5>MA10>MA20 且 MA20Slope>0 优先）
- 回撤质量（健康回撤 / 未破结构）
- 突破后稳定性（FollowThrough / hold）

### BreakoutFollowThrough（新增）

只用**已发生**的突破后 3 / 5 / 10 日：

- 价格延续（不跌破突破位、收盘位置）
- 成交量（放量突破后的合理承接 / 不失控）
- 收盘位置（相对突破位 / MA）
- 是否跌回突破位

输出：`PASS | WEAK | FAIL` + 子分。  
**当天结构好只能 BUILDING**；有效延续后才 MATURE。

### MainRiseQuality（新增）

主升 + 创新高 + HL + 回撤未破 + 量未失控 → 0–100。  
**MainRiseQuality >= 70** 才能 MATURE。

### TrendStructureScore（独立于 Tech）

HH / HL / MA5>MA10>MA20 / MA20Slope / 平台突破 / 回撤质量 / 二次突破。  
**不是** TechnicalScore 单独决定（Tech 89 + BUILDING 合法）。

### MATURE 综合硬条件（全部）

1. Realized 成熟最低（§二）
2. TrendAge >= 15
3. MainRiseQuality >= 70
4. FollowThrough ≠ FAIL（MATURE 至少 WEAK+结构完成或 PASS；PRIME 必须 PASS）
5. 非「仅二次突破当日」——当天结构好最高 BUILDING
6. CONFIRMED 状态 clamp → 最高 BUILDING

ADVANCED：在 MATURE 之上，扩展更大 / 年龄更长 / 质量更高（Exhaustion HIGH 时 EntryWindow→EXTENDED，不杀趋势）。

---

## 四 — EntryWindow / PRIME（V3.1.7）

| Window | 定义（V3.1.7） |
|---|---|
| **PRIME** | Maturity=**MATURE**（非仅 ADVANCED 中后段）且 WaveReturn>=8% 且 FollowThrough=**PASS** 且 state∈{MAIN_RISE,NEW_WAVE} 且刚确认成熟槽。CONFIRMED-early **不能** PRIME。Exhaustion HIGH **不能** PRIME。 |
| **CONFIRMED** | MATURE + 结构完成，但不在新鲜 PRIME 槽；或 ACCELERATION 成熟早期 |
| **FOLLOW** | MATURE、仍有效、主升中段 |
| **EXTENDED** | ADVANCED 中后段 **或** Exhaustion HIGH **或** 远离结构。Grade 可仍 A+；EntrySignal=false（若 window 非 PRIME/CONFIRMED） |
| **NO_ENTRY** | 无趋势 / broken / TREND_FAILURE / 未成熟检测态 |

**ADVANCED 或 Exhaustion HIGH → EXTENDED**，不杀趋势。  
Exhaustion HIGH ≠ TrendFailure。

FundRisk HIGH 阻断 EntrySignal。TrendFailure → NO_ENTRY。

---

## 五 — EntrySignal / A+ 硬条件（在现有条件上叠加）

### EntrySignal（current 规则，V3.1.7）

相对 V3.1.6 增量：

- TrendState ∈ {MAIN_RISE, ACCELERATION, NEW_WAVE}（不含 CONFIRMED）
- TrendMaturity.level = **MATURE**（ADVANCED 走 EXTENDED，不作为新 PRIME 入场；若 window 仍为 CONFIRMED 且未 EXTENDED 可保留，但 PRIME 必须 MATURE+PASS）
- EntryWindow ∈ {PRIME, CONFIRMED}
- FollowThrough=PASS（PRIME）或结构已延续（CONFIRMED）
- Tech/Cont 达既有 A 档地板（tech≥75, cont≥70）— **不抬**
- Sector 正常：缺失或 ≥ 60
- FundRisk HIGH → 阻断
- TrendFailure / broken → 阻断
- **不读** major_id / major_label / future_peak_gain / MajorLabelDate

### A+ 新硬条件（在现有 CLASSIC/STRENGTH 条件上；**不改旧阈值数字**）

同时：

- TrendState ∈ {MAIN_RISE, ACCELERATION, NEW_WAVE}
- Maturity = **MATURE**
- RealizedTrendScore >= 70
- TrendAge >= 15
- Continuity >= 75
- Sector >= 60（缺失按既有规则）
- FundRisk != HIGH
- TrendFailure = false

**Strength 路径**（既有 Tech≥80 Cont≥75 Sector≥70；计划叙述 Cont≥78/Sector≥80 不作为抬阈，仅成熟度门）：也必须 Maturity=MATURE。  
**Classic 路径**（Tech≥85 Cont≥80 Sector≥70）：同样必须 Maturity=MATURE。

不足则：**Grade 可保留**，但 Entry=false 或 Maturity=BUILDING——按规则真实结果写，**不要为了让样本掉出 A+ 而改数**。

### LOW_REALIZED_TREND

若 Grade 仍为 A+ 且 `TrendReturn < 15%` → 标记 **LOW_REALIZED_TREND**，统计计数。  
不删除样本。

---

## 六 — Labels / 回测指标（继承 V3.1.5/V3.1.6）

MajorMoveLabel：SUSTAINED / SPIKE / NON_MAJOR。**永不**喂入 EntrySignal / Maturity / A+。

命中窗口（V3.1.5）：20D +15%，40/60/120 +30%。  
SMALL <10%、MEDIUM 10–30%、MAJOR ≥30%。  
PeakToEndGiveback ≥ 0.50 → SPIKE。

Lead 桶（V3.1.6）：

| LeadDays | Bucket |
|---|---|
| 0–5 | OPTIMAL |
| 6–10 | ACCEPTABLE |
| 11–15 | EARLY |
| >15 | TOO_EARLY |

### 回测指标（不以 MajorWinRate 为主）

MatureEntryCount、MajorHitRate、SignalPrecision、MajorRecall、SmallMoveRate、TooEarlyRate、LateSignalRate、20/40/60D。  
**不删失败样本。不限制股票数量。**

---

## 七 — Focus 检查（报告必须回答；代码无硬编码）

### 华电 600396

日期参照：2026-01-22, 02 月逐步, 3 月初附近。

1. **2026-01-22** 必须 Detection 且 Maturity=EARLY/BUILDING 且 Entry=false
2. 2 月逐步 BUILDING
3. 3 月初附近（**不晚于约 3/10 MajorLabel 之后才首次**，也不要写死 3/6）自然 MATURE 并产生首次合理 Entry
4. 首个合理 Entry 在 MajorLabel 证明之前

### 会稽 601579

日期参照：2026-08-12, 08-18, 09-17 后。

1. **08-12** CONFIRMED 不直接 Entry
2. **08-18** 查成熟度 / Realized / FollowThrough
3. 只有真正主升后才 MATURE Entry
4. **09-17 后**不得因 Exhaustion 高重发 PRIME

---

## 八 — 硬约束

1. 无前瞻：信号特征不含 future_* / major_label / PeakGain（未来）决定实时门。
2. Forward returns **仅**标签引擎。
3. **不改** A+ / Tech / Fund / Sector / Continuity / Exhaustion **旧阈值本身**（只加成熟度门）。
4. **不改** live `publish_close_v3`、routines、frontend；md5 必须仍为 `afbc8a48a511ac0108e21625ebffe001`。
5. 无 symbol/date 策略分支；禁止 `if date >= 某日`。
6. 保留 V3.1.5 MajorMoveLabel；PeakGate 仍 label-only；保留 V3.1.6 回归能力。
7. entryPrice = signal-day close。
8. 禁止荐买 / 预测必涨语言。
9. 不删失败样本；不限制股票数量。

---

## 九 — Deliverables

| 件 | 路径 |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_7_PLAN.md` |
| Config | `v3/v31/config.py`（V317_*） |
| Types | `v3/v31/types.py`（RealizedTrend / FollowThrough / …） |
| RealizedTrend | `v3/engines/realized_trend.py` |
| TrendMaturity | `v3/engines/trend_maturity.py`（V3.1.7 模式） |
| EntryWindow | `v3/v31/entry_window.py` |
| EntrySignal | `v3/engines/entry_signal.py` |
| Regression | `v3/v31/regression_v317.py` |
| Report | `diagnostics/V3.1.7_REGRESSION.md` |

---

## 第四十二节 — 回归对比表（报告必须含）

对比 **V3.1.6 信号** + **现网 A+ 归档** vs **V3.1.7**：

| 指标 | V3.1.6 / 现网 | V3.1.7 |
|---|---|---|
| A+ 数量 | … | … |
| Entry 数（MatureEntryCount） | … | … |
| SmallMoveRate | … | … |
| TooEarlyRate | … | … |
| MajorHitRate | … | … |
| MajorRecall | … | … |
| 40D hit | … | … |
| 60D hit | … | … |
| LOW_REALIZED_TREND 计数 | — | … |

---

## 第四十三节 — 15 候选在其 A+ 日逐条表（报告必须含）

对 08-31 / 09-21 相关 15 只（含华电/会稽及其余 fixtures），在其 A+ 日（或最近相关 A+ 日）给出：

| code | A+日 | Maturity | Age | TrendReturn | WaveReturn | 20/40/60 | FollowThrough | Grade | EntryWindow | EntrySignal |

不足则 Grade 可保留但 Entry=false 或 Maturity=BUILDING——按规则真实结果写。

---

## 第四十九节 — 验收清单

1. Plan 已保存至 `STRATEGY_V3_1_7_PLAN.md`（章节与约束完整）。
2. RealizedTrendEngine 只用 T 及以前；输出 score/trendReturn/return20/40/60/priceExpansion/newHighProgress/realizedDays/currentWaveReturn。
3. 成熟最低：TrendReturn≥15% 且 CurrentWaveReturn≥8%，且（20D≥8% ∨ 40D≥12% ∨ 60D≥15%）。
4. TrendAge≥15 才能 MATURE；Age<15 最高 BUILDING。
5. 10天+30% 不能直接 MATURE（需 HL+MA+回撤质量+突破后稳定性）。
6. TrendMaturityScore 权重：年龄20 / 累计25 / Wave20 / HH15 / HL10 / 稳定性10。
7. BreakoutFollowThrough 用突破后 3/5/10 日；当天结构好只能 BUILDING。
8. MainRiseQuality≥70 才能 MATURE。
9. TrendStructureScore 独立于 Tech。
10. PRIME：Maturity=MATURE 且 WaveReturn≥8% 且 FollowThrough=PASS；ADVANCED/Exhaustion HIGH→EXTENDED。
11. A+ 硬条件叠加成熟度门；Strength/Classic 均需 Maturity=MATURE；旧阈值未抬。
12. TrendReturn<15% 的 A+ 标 LOW_REALIZED_TREND 并计数。
13. 600396 01-22：Detection + EARLY/BUILDING + Entry=false；3 月初附近自然首次合理 Entry（在 MajorLabel 前）。
14. 601579 08-12 无 Entry；08-18 查 Realized/FollowThrough；09-17 后不因 Exhaustion 重发 PRIME。
15. 回测指标含 MatureEntryCount/MajorHit/Precision/Recall/SmallMove/TooEarly/Late/20/40/60D；不删失败样本。
16. Lead 桶沿用 V3.1.6；命中窗口沿用 V3.1.5。
17. `publish_close_v3` md5 = `afbc8a48a511ac0108e21625ebffe001`；无 frontend；无定时任务改动。
18. 无 symbol/date 策略分支；无未来 Peak/Major 决定实时门。
19. 第四十二、四十三节表 + 第四十九节逐条 PASS/FAIL（不虚报）。
20. 对比 V3.1.6 信号与现网 A+ 归档指标已输出。

---

## Code layout（additive）

Grading 仍 `grade_candidate_v311`（live 阈值不变）。  
Live publish 仍 V3.0。  
V3.1.7 为 regression 路径：RealizedTrend + 更严 Maturity/EntryWindow/EntrySignal 门控叠加在 V3.1.6 之上。
