# 趋势确认突破雷达 TrendConfirmationBot V3.1.5 — EntrySignal 与大行情标签解耦

> 忠实落地用户 V3.1.5 锁定意图（华电辽能 / 会稽山回归窗口）。  
> Fixtures（仅评估）：**600396 华电辽能**，**601579 会稽山**。  
> Replay：**2024-01-02 → 2026-09-21**（FeatureSnapshot 与 V3.1.4 相同）。  
> 约束：**无标的/日期特判**；**禁止荐买 / 预测必涨**；**无前端**；live AM/noon/close publish **不动**。  
> **不调高** A+ / Tech / Fund / Sector / Continuity / Exhaustion 阈值。  
> 保留 V3.1.2 卫生度与 V3.1.3 Major/Sub 结构；**降级 V3.1.4 PeakGate**，使其不驱动 A+ / EntrySignal / Pool。  
> **30%/20d 规则仅存在于标签引擎**。禁止前瞻写入信号特征。

---

## 0. 核心原则（verbatim intent）

> **大行情是历史标签，不是买入条件；买入信号必须在大行情被证明之前产生。**  
> **真正要优化的是「信号纯度 + 大行情覆盖率 + 提前量」，而不是事后把符合 30% 涨幅的股票筛出来以后得到一个漂亮的 100% 胜率。**

### V3.1.4 问题（禁止继续耦合）

V3.1.4 在 `PeakGain≥30% AND Age≥20` 时 Candidate→Major，且 MajorWinRate = 这些 Major 中 end-return>0（FailedCandidate 排除）。这是**事后过滤器**，不是入场胜率。

反例：600396 Entry-like 日 2026-01-20 仍是 Candidate（peak≈4%）；Major 要到 2026-03-10（peak 已≈31%）。**MajorPromotionDate 绝不能当作 EntryDate。**

---

## 一 — 三层架构

| 层 | 角色 | 时序 |
|---|---|---|
| **1. Candidate** | 趋势结构存在（保留 V3.1.3/3.1.4 wave 机械，结构用） | as-of ≤ t |
| **2. EntrySignal** | 实时买入信号：趋势已确认 + TrendStrength/Cont 达标 + Sector/HTF 正常 + EntryWindow∈{PRIME,CONFIRMED} | **仅 T 及以前** |
| **3. MajorMoveLabel** | **仅回测标签**：信号之后用 ForwardWindow 内未来 bar 计算 | 可用未来 bar，**禁止写回信号特征** |

### EntrySignal（不抬阈值）

使用**既有** V3.1.1 / V3.1.4 阈值，**禁止抬高** Tech/Cont/Fund/Sector/A+：

- 趋势状态 ∈ {CONFIRMED, MAIN_RISE, ACCELERATION, NEW_WAVE}
- EntryWindow ∈ {PRIME, CONFIRMED}
- Tech/Cont 达既有 A 档地板（tech≥75, cont≥70）—「existing strength/cont」
- Sector「正常」：缺失或 ≥ A 档 sector_min(60)
- 非 TREND_FAILURE / 非 broken
- **不读** `major_id` / `major_label` / `future_peak_gain` / `wave_level==MAJOR`

对比矩阵（不选最高胜率）：

| Entry规则 | 定义 |
|---|---|
| 当前规则 | EntryWindow PRIME\|CONFIRMED + 既有 tech/cont（上） |
| Classic | A_PLUS_PATH_CONFIG CLASSIC（tech≥85, cont≥80, sector≥70…） |
| Strength | A_PLUS_PATH_CONFIG STRENGTH（tech≥80, cont≥75 + state + HTF） |

### MajorMoveLabel（标签 only）

从 `signalDate` 锚定，`entryPrice = signal-day close`（可比性优先；若现有 exit 引擎用次日开盘则在报告中声明——本版固定 **signal close**）。

Forward windows：20 / 40 / 60 / 120 交易日

- 20D Hit：窗口内 max high 相对 entry ≥ **+15%**
- 40D / 60D / 120D Hit：max high ≥ **+30%**

Move buckets（信号后 PeakGain）：

- SMALL_MOVE：PeakGain < 10%
- MEDIUM_MOVE：10% ~ 30%
- MAJOR_MOVE：≥ 30%

`PeakToEndGiveback = (PeakPrice - EndPrice) / (PeakPrice - EntryPrice)`（分母≤0 时记 0）

MajorMoveType：

- **SUSTAINED_MAJOR**：PeakGain≥30% **且** 至少一个 ForwardWindow hit **且** TerminalReturn>0 **且** 非极端 Peak-to-End giveback（giveback < SPIKE_GIVEBACK_MIN，默认 0.50）
- **SPIKE_MAJOR**：PeakGain≥30% 但大幅回吐（例：600396 2025-04-24 波 peak+38.6% end+7.66% → SPIKE）
- **NON_MAJOR**：从未触及大行情门槛
- **ONGOING**：前向窗口未走完且尚不足以定性

记录：PeakGain, TerminalReturn, MaxDrawdown, PeakDate。

---

## 二 — 指标（分母 = 全部 EntrySignal，含后来失败）

- EntrySignalCount
- MajorHitRate = (SUSTAINED_MAJOR + SPIKE_MAJOR) / all signals（触及 ≥30% peak）
- SignalPrecision = SUSTAINED_MAJOR / all signals（剔除 SPIKE；定义写进报告）
- SmallMoveRate / MediumMoveRate
- 20/40/60/120D HitRate
- AvgForwardReturn / MedianForwardReturn / MaxForwardGain / AverageMaxDrawdown
- MajorRecall = 历史真实大行情中，在 label date **之前**已有 EntrySignal 的比例 / 全部历史大行情

历史真实大行情（Recall 分母）：结构波在 as-of 上曾达到 PeakGain≥30% 且 Age≥20 的 Major 标签日（与 V3.1.4 gate 同阈值，但**仅作标签/召回**，不驱动入场）。

---

## 三 — SignalEvaluation 字段

```
symbol, signalDate, entryPrice,
forward20/40/60/120dMaxReturn,
maxDrawdown20/40/60,
majorHit, majorType (SUSTAINED_MAJOR|SPIKE_MAJOR|NON_MAJOR|ONGOING)
```

另附：PeakGain, TerminalReturn, PeakDate, PeakToEndGiveback, moveBucket, EntryWindow, rule。

---

## 四 — 硬约束

1. EntrySignal / TrendGrade / EntryWindow / ActiveTrendPool：**无前瞻**。特征名 `major_id`/`major_label`/`future_peak_gain` 不得喂 live grade。若信号路径读到未来 → `LOOKAHEAD_ERROR`。
2. Forward returns **仅**在 MajorMoveLabelEngine / evaluation 使用，**禁止写回**信号特征。
3. **不改** A+ / Tech / Fund / Sector / Continuity / Exhaustion 阈值。
4. **不改** live `publish_close_v3`、routines、frontend。
5. 无 symbol/date 特判。
6. 保留 V3.1.2 卫生 + V3.1.3 Major/Sub；**降级 V3.1.4 PeakGate**（`gate_drives_live=False`）：结构 Major 在 structure confirm 后即可（V3.1.3 行为），30%/20d 只在标签引擎。
7. 评估锚定 signalDate；entryPrice = 当日 close。

---

## 五 — Focus 检查（报告必须回答）

1. **600396**：2026-01-20 波的**最早** EntrySignal — **不是** 2026-03-10 Major 日。写明日期、价格、相对 label 提前交易日、后续 PeakGain。
2. **601579**：比较 2026-08-12 vs 2026-08-18；谁是首个有效 EntrySignal；**为何 8/12 是否 confirmed**（当日 as-of 的 EntryWindow / strength / cont）。
3. **解释 V3.1.3→V3.1.4 回报变化**：分解小行情剔除、大行情保留、提前量是否牺牲、SignalCount 变化、MajorHitRate — **不要**只说涨跌。MajorWinRate **不再**是决策指标。

---

## 六 — Deliverables

| 件 | 路径 |
|---|---|
| Plan | `v3/docs/STRATEGY_V3_1_5_PLAN.md` |
| Config | `v3/v31/config.py`（V315_*） |
| Types | `v3/v31/types.py` |
| EntrySignal | `v3/engines/entry_signal.py` |
| MajorMoveLabel | `v3/engines/major_move_label.py` |
| TrendWave demote | `v3/engines/trend_wave.py`（`gate_drives_live`） |
| Regression | `v3/v31/regression_v315.py` |
| Report | `diagnostics/V3.1.5_REGRESSION_HUALIADIAN_HUIJISNAN.md` |
| Artifacts | `diagnostics/v315_signals.csv`, `v315_signal_eval.json`, `v315_vs_v314_metrics.json`, `v315_regression_summary.json`, `v315_daily_replay.csv` |

---

## §三十五 — Acceptance checklist

1. Plan 已保存至 `STRATEGY_V3_1_5_PLAN.md`。
2. MajorGate / 30%/20d **仅**标签引擎；不驱动 EntrySignal。
3. EntrySignal 独立于 MajorPromotionDate。
4. 每个 EntrySignal 都进分母（含后来失败 / SMALL / SPIKE）。
5. Forward 20/40/60/120 均计算；Hit 阈值 20D+15% / 40+60+120D+30%。
6. Small / Medium / Major move buckets。
7. PeakToEndGiveback + SUSTAINED vs SPIKE。
8. MajorRecall + SignalPrecision（定义文档化）。
9. 无前瞻：信号特征不含 future_*；违规则 LOOKAHEAD_ERROR。
10. 两标的完整 replay（2024-01-02→2026-09-21）。
11. 各大行情波找到最早 EntrySignal；华电≠2026-03-10。
12. V3.1.3→V3.1.4 回报变化已分解说明。
13. A+ / Tech / Fund / Sector / Cont / Exhaustion 阈值未改。
14. `publish_close_v3` md5 不变；无 frontend 改动。
15. 无 symbol/date 策略分支；禁止荐买语言。
16. Precision/Recall 矩阵含 当前规则 / Classic / Strength。
17. 报告含 Table1/2/3 + §三十五 PASS/FAIL 证据 + Focus 回答。

---

## Code layout（additive）

Grading 仍 `grade_candidate_v311`。Live publish 仍 V3.0。V3.1.5 为 regression-only 路径。
