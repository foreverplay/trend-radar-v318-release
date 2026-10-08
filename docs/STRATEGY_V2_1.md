# 趋势确认突破雷达 V2.1 — 阶梯确认 + 基本面/板块/风险

## 相对 V2.0
V2.0（`v2_structure.py`）技术评分 / 阶段 / KJS / OH **不变**，本版只在扫描结果上叠加三维 enrichment，并打标签。

**禁止**：四维总分（Tech+Fund+Sector+Risk）；荐买 / 必涨措辞；庄股标签。

## 新模块
| 文件 | 职责 |
|---|---|
| `v2_fundamental.py` | FundamentalEngine：本地财报 → FundamentalScore 0–100 / 优秀·良好·一般·风险；缺数 → `DATA_INSUFFICIENT`（不造 0） |
| `v2_sector.py` | SectorEngine：行业 + SectorReturn5/20 + 相对强度；无行业 → `DATA_INSUFFICIENT` |
| `v2_risk.py` | RiskEngine：仅 OHLCV → RiskScore LOW/MID/HIGH |
| `v2_classify.py` | CORE / OBSERVATION / HIGH_RISK / FILTER 标签 |

## 基本面权重（有数才计；缺失跳过并按可用权重归一）
盈利20 · 营收15 · 扣非20 · 现金流20 · 连续性10 · ROE10 · 估值5

要点：扣非质量风险、金融行业跳过朴素现金流比、近4季盈利连续性、ROE分档、PE溢价（有则用）。

## 板块
`RelativeStrength = stock20 − sector20`（同行本地均线收益近似）。标签：`SECTOR_STRONG` / `SECTOR_WEAK` / `LEADER_RELATIVE_STRENGTH`。

## 交易风险（OHLCV）
- HIGH_VOLUME_WEAK_CLOSE（VR≥2 & closePos≤0.3；≥2次升级）
- FREQUENT_UPPER_SHADOW（10日内≥3）；ret20>30% → HIGH_LEVEL_DISTRIBUTION_RISK
- HIGH_LEVEL_VOLUME / HIGH_LEVEL_WEAKNESS
- TREND_FAILURE / BROKEN（收盘<MA20 且 <平台低）

## 标签规则
- **CORE_CANDIDATE**：Tech≥80 & Fund≥60 & Sector≥60 & Risk≠HIGH & phase∈(SECOND_BREAKOUT, MAIN_RISE_ACCEL, MAIN_RISE)
- **OBSERVATION**：Tech≥75，或维度偏弱 / 非二次突破；**基本面不足时不可仅凭技术进 CORE**
- **HIGH_RISK**：技术仍强但 Fund<50 或 Risk=HIGH（仍展示）；过热且 Tech≥65 亦入
- **FILTER**：Tech<65 或 BROKEN

## 报告
- 核心候选 CORE / 观察 OBSERVATION / 高风险 HIGH_RISK / 过热 OH
- 主表：股票|技术|基本面|板块|风险|阶段|标签
- 个股清单：技术/基本面/交易风险/板块 ✓/⚠
- 产物：`report_v2.md`、`candidates_v2.json`（并镜像 `report.md`）

## 调用
```bash
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_staircase_scan.py --t-date 2026-09-21
# 今日 hist 不全时回退
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_staircase_scan.py --t-date 2026-09-18 --force-codes 002011,601579
```
