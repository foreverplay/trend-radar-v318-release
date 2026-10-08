# 趋势确认突破雷达 V2.3 — 多周期共振观察

## 相对 V2.2
- **不变**：V2.0 TechnicalScore / phase / KJS / OH；V2.1 Fund / SectorScore / Risk 公式；V2.2 Continuity / Consumption / 四池 / CORE_TREND
- **新增**：StockMonthlyTrendScore、SectorDaily/MonthlyTrend、RecommendationScore + ResonanceTags + 规则模板理由
- **禁止**：TotalScore；荐买/必涨；把 RecommendationScore 写成收益概率
- **不硬过滤**：月线 DOWN、板块日线 DOWN（仅降权 / `MONTHLY_CONFLICT` 等标签）
- **用户-facing 表格/推送**：不展示「共振标签」及 `DAILY_UP` / `MONTHLY_*` 等英文标签；标签仅保留在 JSON 内部。
- **硬过滤（仅推荐观察榜）**：Tech<70 | Cont<60 | Risk=HIGH | Cons≥90 | TREND_FAILURE

## StockMonthlyTrend
聚合日线→自然月（末日收盘，量额求和）。  
STRONG_UP / UP / NEUTRAL / DOWN；Score = 均线结构30 + 斜率20 + 价格位置20 + 20期趋势15 + 成交量结构15。

## SectorTrend
同行等权均价日线（申万风格行业，不炸概念板）；缓存 `(industry, t_date)`。  
日线 Score：MA结构25 + MA20斜率15 + MA60斜率10 + 价格位置15 + ret20 15 + 量能10 + 广度10。  
≥80 STRONG_UP / 60–79 UP / 40–59 NEUTRAL / <40 DOWN。  
RelativeStrength20 = 个股20D − 板块20D。

## RecommendationScore（规则优先观察强度）
Base = Tech×0.25 + Cont×0.25 + Fund×0.15 + SectorScore×0.10  
   + StockMonthly×0.10 + SectorDaily×0.10 + (100−RiskNumeric)×0.05  
缺维按可用权重归一，标记 DATA_INSUFFICIENT，禁止伪 0。

### ResonanceBonus（单一路径，cap 15）
| 条件 | 加分 |
|---|---:|
| 月线 STRONG_UP | +7（+日线up 再 +1） |
| 月线 UP | +4 |
| 月线 DOWN | −5（MONTHLY_CONFLICT） |
| 板块日 STRONG_UP / UP | +5 / +3 |
| 板块月 UP/STRONG | +3 |
| RS20≥8 / ≥3 | +3 / +2 |
| Cont≥80 | +2 |
最终 `min(15, max(-8, bonus))`。消耗度：70–79 −5；≥80 −10 + HIGH_CONSUMPTION。

等级：≥90 A+ / 80–89 A / 70–79 B / 60–69 C / <60 D（D 不进主榜）。

## 理由
`RecommendationReasonEngine`：**仅规则模板**，输出 reason[] / risk[] / 核心理由清单；禁止 LLM。

## 调用
```bash
/workspace/ashare-venv/bin/python /workspace/trend-breakout/v2_3_scan.py --t-date 2026-09-21
```
