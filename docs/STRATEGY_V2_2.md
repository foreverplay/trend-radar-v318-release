# 趋势确认突破雷达 V2.2 — ContinuityScore + TrendConsumption

## 相对 V2.1
- **不变**：V2.0 `v2_structure.py` 技术评分 / 阶段 / KJS / OH
- **不变**：V2.1 Fund / Sector / Risk **公式**（仅 classify 门槛升级）
- **新增**：`v2_continuity.py` ContinuityScore + TrendConsumption + ContinuityType + Delta
- **禁止**：把 Tech/Fund/Sector/Risk/Continuity 加总成 TotalScore；荐买 / 必涨；把持续性写成未来收益预测

## ContinuityScore 0–100（状态分）
| 维 | 权重 | 要点 |
|---|---:|---|
| 结构 Structure | 25 | MA 多头、斜率、20日 HH/HL |
| 回撤/强度 Drawdown | 15 | MaxDD20 分档、PullbackRecovery、Close vs MA（过远 → OVEREXTENDED） |
| 量价 Volume | 15 | 上涨量/下跌量、突破后缩量、处罚 vol-stall |
| 基本面持续 Fund | 20 | 多季营收/扣非增长、加速、现金流同步（ashare-financials / v2_fundamental） |
| 板块持续 Sector | 15 | 板块 MA 广度、收益、RS；弱板块持续性≤5 强制观察 |
| 空间 Space | 10 | 距 20/60/120 高、新鲜突破 |

缺基本面/板块持续性输入 → **按可用权重归一的部分分** + `DATA_INSUFFICIENT` 备注，**禁止整分清零伪装**。

星级：90+极强 / 80–89强 / 70–79良好 / 60–69一般 / <60较弱。

## TrendConsumption 0–100
连涨天数、ret10>30、ret20>50、MA20Deviation、ATR/Close 扩张。越高越偏“趋势消耗/透支”。

## ContinuityType
- **TYPE_A** 健康延续
- **TYPE_B** 加速延续
- **TYPE_C** 高位加速
- **TYPE_D** 趋势衰减（Cont<60）

## Delta
每日写入 `cache/continuity_history/{code}.json`；算 ContinuityDelta1/3/5 → IMPROVING / DETERIORATING / STABLE（**监测，非预测**）。

## CORE_TREND（= CORE_CANDIDATE 别名）
Tech≥80 & Fund≥60 & Sector≥60 & Continuity≥75 & Risk≠HIGH & Consumption<70  
& phase ∈ {SECOND_BREAKOUT, MAIN_RISE, MAIN_RISE_ACCEL}  
Continuity<70 → 不可核心。板块持续性≤5 → 强制 OBSERVATION。

排序：Continuity → Tech → Sector → Fund → Risk。

## 四池
① 新趋势 FIRST_BREAKOUT  
② 二次确认 SECOND_BREAKOUT + Cont≥75  
③ 主升延续 MAIN_RISE(_ACCEL) + Cont≥80 + Cons<70  
④ 趋势衰减 Cont<60  

主表列：股票|技术|基本面|板块|持续性|消耗度|阶段|类型  
详情：有利因素 / 限制因素 / ContinuityDelta

## 调用
```bash
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_staircase_scan.py --t-date 2026-09-21
# 或
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_2_scan.py --t-date 2026-09-21 --force-codes 002011,601579
```
