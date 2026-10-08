# 趋势确认突破雷达 V2.0 — 阶梯式主升突破模型

## 目标结构（非涨幅榜）
底部蓄势 → 首次突破 → 健康分歧 → **平台不破 → 二次突破 → 主升加速**  
焦点：**④ PLATFORM_HOLD → ⑤ SECOND_BREAKOUT → ⑥ MAIN_RISE_ACCEL**

`KJS_PATTERN`：首次突破 + 健康分歧 2–8 日 + 未破平台低 + 二次突破 + 5 日均线斜率上行。  
- **KJS_NOW**：T 日当日满足  
- **KJS_HIST**：近 60 个交易日曾满足（`kjs_ever_60d` / `kjs_last_date`）；T 日过热亦可挂历史标签  
报告/JSON 中 `kjs_pattern` 在 NOW 或 HIST 时为真，用 `kjs_tag` 区分。

## 硬过滤（摘要）
1. 非 ST/停牌/过新；20 日均额 ≥ 3000 万  
2. 均线：MA5>MA10>MA20 优先；MA20≥MA60（略低可）；MA20Slope>1%；斜率>0 优先  
3. 平台 5–20 日：收盘高低计算；区间 ≤15%（优 ≤12%）；后半低 ≥ 前半低；ClosePosition≥0.65（优≥0.80）  
4. 首次突破：Close>平台高×1.01 且 Vol/VolMA20≥1.3（优≥1.8）  
5. 分歧 2–8 日：可跌破突破日高但不破平台低；回撤≤12%（优≤8%）；无连续 3 大阴；Close≥MA20；量缩  
6. **二次突破★**：Close>突破高且>平台高；VR≥1.2；ClosePos≥0.8；日涨≥5% → 主升启动候选  
7. 加速：Return5>Return10/2 且 MA5Slope>MA10Slope；Return5>25 降分  
8. **过热（不静默剔除）**：Return5>35 或近段≥3 涨停 → phase=OVERHEATED，grade=`OH`，保留分数，`drop=False` 入报告「🔥过热/高潮观察」；`pushable_core=False`，**不作为普通二次突破/S/A 候选**  
9. 其他降权/剔除：单日>15% 无平台；无健康分歧追涨；放量滞涨；放量砸；Close&lt;MA20+VR&gt;1.5 → TREND_FAILURE

## 评分 /100
均线 20 + 平台 20 + 首次突破 15 + 分歧 20 + 二次突破 15 + 主升加速 10（含量能加成）

## 等级
- S≥85（需完整结构）  
- A 75–84；B 65–74 观察；C 55–64；&lt;55 DROP  
- **OH**：过热观察（非 S/A 核心）

## MarketRegime（indices + breadth + turnover）
STRONG / NORMAL / WEAK / RISK_OFF  
- NORMAL：S 门槛 +5（完整结构 score≥80 可记 S）  
- WEAK：仅 ≥85 可推  
- RISK_OFF：仅观察，不推普通候选  

## 预筛
- 默认 `--max-prefilter 250`  
- `local_prefilter_boost`：**扫描全部**本地日线 parquet（`/workspace/ashare-kline/raw/daily/`），保留流动性/MA 门控  
- `--force-codes` / 环境变量 `V2_FORCE_CODES` / `meta/force_codes.txt` 强制并入候选后再拉 hist  

## 实现
- `v2_structure.py` — 平台/首次/分歧/二次/过热/KJS 历史检测与打分  
- `v2_staircase_scan.py` — 主扫描（预筛 → hist → 结构）  
- 产物：`report_v2.md` / `candidates_v2.json` / `all_results_v2.json`；`report.md` 与 V2 对齐  
- V1 `run_screen.py` / `stage2b.py` 保留但标 LEGACY  

禁止荐买 / 预测必涨。算法细节以本文件 + 用户规格为准。
