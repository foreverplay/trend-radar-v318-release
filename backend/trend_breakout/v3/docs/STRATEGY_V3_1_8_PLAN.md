# 趋势确认突破雷达 TrendConfirmationBot V3.1.8 — TrendGrade ⊥ EntryStatus 双轨

> 忠实落地用户「趋势确认突破 Bot V3.1.8 Plan」。  
> Fixtures：**600396 华电辽能**，**601579 会稽山**，**603087 甘李药业**，**603259 药明康德**，**003006 百亚股份**，**002962 五方光电**，**300725 药石科技**，**605100 华丰股份**。  
> 约束：**无标的/日期特判**；**禁止荐买 / 预测必涨**；**无未来函数**；**不改 ExitEngine 核心**；**现网 V3.1.7 推送路径先不切**（验收通过后由父 agent 确认再切）。  
> 继承 V3.1.7 RealizedTrend / Maturity / FollowThrough / EntryWindow 引擎；本版只把 **TrendGrade 与 Maturity/Entry 拆开**，并恢复 A+/A/B/C 全等级 + RAPID_MAIN_RISE。

---

## 0. 核心原则

> **A+ = 图形/趋势质量最高，不是「今天一定适合介入」；PRIME = 当前成熟入场窗口。**  
> **B/C 不是垃圾池，而是保留「图形开始变好、但还没成熟」的股票。**  
> **主榜可以少，但全榜不能少；主榜负责精度，A+～C 全等级负责发现和跟踪趋势。**

禁止再用 EntryStatus / Maturity / RealizedTrend **强制过滤或压低** TrendGrade。

---

## 一 — 双轨产品

```
TrendConfirmationBot
        │
   ┌────┴────┐
   ↓         ↓
TrendGrade  EntryStatus
趋势质量     入场状态
A+/A/B/C/WATCH   PRIME/CONFIRMED/FOLLOW/EXTENDED/NO_ENTRY
```

| 轴 | 回答 |
|---|---|
| **TrendGrade** | 日线趋势图形到底好不好？ |
| **EntryStatus** | 现在是不是成熟的介入窗口？ |

合法组合示例：`A+ + BUILDING + NO_ENTRY`、`A+ + MATURE + EXTENDED`、`A+ + RAPID_MAIN_RISE + BUILDING + FOLLOW`。

---

## 二 — TrendGrade（恢复全等级）

复用 `v3.types.Grade`，新增 **C**（雏形）。枚举：`A+ | A | B | C | WATCH | FILTER`。

### A+

结构完整 + 日线明确 + 板块/高周期支持 + 持续性强。

硬门槛（**不要求** Maturity=MATURE，**不要求** TrendReturn≥15%）：

| 条件 | 阈值 |
|---|---|
| TrendStructureScore | ≥ 85 |
| ContinuityScore | ≥ 80 |
| SectorScore | ≥ 70（缺失按既有放行） |
| FundRisk | ≠ HIGH |
| TrendFailure | false |

**RAPID_MAIN_RISE 路径**（替代结构门槛的并行路径，不抬旧 Tech 矩阵）：

- 短期涨幅明显（return20≥15% 或 trendReturn≥12% 且 age≤25）
- MA5>MA10>MA20
- HH/HL（或 HL 未破）
- 量价配合 + Continuity≥75
- FundRisk≠HIGH、非 TrendFailure

→ 可给 A+ / A；EntryStatus 最高 **FOLLOW**（不进成熟入场榜的 PRIME）。

### A

强趋势，某一维略低于 A+：`TrendStructure≥80` 且 `Continuity≥75`，无严重风险。

### B

有明显趋势价值，仍在构建/确认：`Structure≥65` 或（Tech≥65 且 Cont≥60），趋势存活。

### C

雏形：`Structure≥50` 或（Tech≥55 且 Cont≥50），有趋势信号。

### WATCH

数据不足 / 结构不稳 / 趋势即将破坏。不用于正常趋势排序。

### FILTER

broken / TrendFailure / 分数不足。

### TrendGrade 使用什么

主要：`TrendStructureScore, ContinuityScore, SectorScore, HigherTimeframe, FundRisk, TrendState`。  
辅助：`TrendStrength / Tech`。  
**禁止**强制过滤：`TrendMaturity, RealizedTrend, EntryWindow`。

`LegacyGrade`（Plan / V311 / V317 降级后的 grade）仅作回归对照，不决定 V318 TrendGrade。

---

## 三 — Maturity 只影响 EntryStatus

`TrendGrade=A+` + `Maturity=EARLY` → `EntryStatus=NO_ENTRY`（或 RAPID→FOLLOW），**不是**把 Grade 降成 C。

Maturity / Realized / FollowThrough / Age 逻辑继承 V3.1.7，不改硬门槛数字。

---

## 四 — EntryStatus（继承 V3.1.7 + RAPID）

| Window | 定义 |
|---|---|
| **PRIME** | Maturity=MATURE + WaveReturn≥8% + FollowThrough=PASS + state∈{MAIN_RISE,NEW_WAVE} + 新鲜槽；Exhaustion HIGH 不可 |
| **CONFIRMED** | MATURE + 结构完成，非新鲜 PRIME；或 ACCELERATION 成熟早期 |
| **FOLLOW** | MATURE 主升中段；**或** RAPID_MAIN_RISE + Maturity∈{EARLY,BUILDING}（趋势有效、未 EXTENDED） |
| **EXTENDED** | ADVANCED 中后段 **或** Exhaustion HIGH **或** 远离结构 |
| **NO_ENTRY** | 无趋势 / broken / TREND_FAILURE / 未成熟且非 RAPID |

ADVANCED 或 Exhaustion HIGH → EXTENDED，不杀趋势。

---

## 五 — 四榜

### 1. 成熟入场榜（原主榜，保持严格）

```
TrendGrade ∈ {A+, A}
AND EntryStatus ∈ {PRIME, CONFIRMED}
```

可 0–2 只。

### 2. 趋势全榜

```
TrendGrade ∈ {A+, A, B, C}
```

不要求 Mature。排序：

1. TrendGrade  
2. TrendStructureScore  
3. ContinuityScore  
4. TrendMaturityScore  
5. RealizedTrendScore  
6. SectorScore  
7. ExhaustionRisk（惩罚，**不删除**）

### 3. 观察池

```
TrendGrade ∈ {B, C}
AND Maturity ∈ {EARLY, BUILDING}
```

### 4. 过热延伸

```
TrendGrade ∈ {A+, A}
AND EntryStatus = EXTENDED
```

（趋势未失效；比「过热股票」更准确。）

---

## 六 — Score 层级（职责分离）

| Score | 职责 |
|---|---|
| TechnicalScore | 技术结构 |
| TrendStructureScore | 趋势形态质量（Grade 主输入） |
| ContinuityScore | 趋势延续质量 |
| TrendMaturityScore | 成熟程度 → 只进 Entry |
| RealizedTrendScore | 已实现幅度 → 只进 Entry/Maturity |
| ExhaustionRisk | 风险惩罚 |
| EntryWindow | 当前窗口 |

---

## 七 — RAPID_MAIN_RISE

解决会稽山类快速主升因成熟度不足从主榜/全榜消失的问题。

允许：`TrendGrade=A/A+` + `Path=RAPID_MAIN_RISE` + `Maturity=BUILDING` + `EntryStatus=FOLLOW`。  
进趋势全榜，**不进**成熟入场榜。

无股票/日期硬编码。

---

## 八 — Fixtures 目标

| 标的 | 目标 |
|---|---|
| 600396 2026-01 | Grade B/A、BUILDING、NO_ENTRY（不提前 PRIME） |
| 600396 2026-03 主升 | Grade A+、MATURE、PRIME/CONFIRMED |
| 601579 快速启动 | 允许 A+ + RAPID + BUILDING + FOLLOW，不能消失 |
| 603087 甘李 | 当前 MATURE+CONFIRMED 保持进成熟入场榜 |
| 603259/003006/002962 | 若仍 B+BUILDING → 趋势全榜 B，不失踪 |
| 300725/605100 | 回归对照；结构好则 Grade 不被 Maturity 压低 |

---

## 九 — 回测指标（Evaluation only）

新增：`GradePrecision, GradePersistence, GradeUpgradeRate, GradeDowngradeRate, PrimePrecision, PrimeMajorHitRate, RapidMainRiseHitRate`；窗口 20/40/60D。  
MajorMoveLabel **永不**喂入 Grade/Maturity/Entry。

禁止：未来 Major / PeakGain / 涨幅参与实时门。

---

## 十 — 硬约束

1. 无前瞻；无 symbol/date 策略分支。  
2. 不改 ExitEngine 核心。  
3. 不改 Live Publish 之外的无关逻辑；**现网例程名/推送先不切**。  
4. 不抬 V3.1.7 成熟硬门槛数字；只拆 Grade。  
5. 禁止荐买 / 预测必涨。

---

## 十一 — Deliverables

| 件 | 路径 |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_8_PLAN.md` |
| Config | `v3/v31/config.py`（V318_*） |
| TrendGrade | `v3/grading_v318.py` |
| RapidMainRise | `v3/engines/rapid_main_rise.py` |
| EntryWindow | `v3/v31/entry_window.py`（V318 FOLLOW 扩展） |
| Live | `v3/v31/live_v318.py` |
| Publish | `v3/publish_close_v318.py` + 根 `publish_close_v318.py` |
| Regression | `v3/v31/regression_v318.py` |
| Report | `diagnostics/V3.1.8_REGRESSION.md` |

---

## 十二 — 验收清单

1. [ ] A+ / A / B / C 全等级恢复  
2. [ ] TrendGrade 与 Maturity 独立  
3. [ ] TrendGrade 与 EntryStatus 独立  
4. [ ] 成熟度不足不会直接把好图形降成 C  
5. [ ] 成熟入场榜继续保持严格（A+/A + PRIME/CONFIRMED）  
6. [ ] 趋势全榜恢复完整评级  
7. [ ] 观察池保留 BUILDING 图形  
8. [ ] 过热延伸保留 A/A+ 趋势  
9. [ ] RapidMainRise 独立识别  
10. [ ] 会稽山类快速上涨不再消失  
11. [ ] 华电辽能 1 月不会提前变成 PRIME  
12. [ ] 华电辽能 3 月主升能够进入成熟窗口  
13. [ ] 甘李药业当前主榜结果保持  
14. [ ] 不修改 ExitEngine 核心逻辑  
15. [ ] 不修改 Live Publish 计算路径之外的无关逻辑；现网例程未擅自切流  
16. [ ] 无股票/日期特殊分支  
17. [ ] 无未来函数  

---

## 十三 — 执行顺序（Phase 1–15）

Phase1 读 V317 Grade + 本 Plan → Phase2 恢复 A+/A/B/C → Phase3 拆 Maturity → Phase4 拆 EntryStatus → Phase5 RapidMainRise → Phase6 趋势全榜 → Phase7 成熟入场榜 → Phase8 过热延伸 → Phase9–12 fixtures 回归 → Phase13–14 Grade/Prime/Rapid 指标 → Phase15 验收；脚本就绪，**切流待父 agent 确认**。
