# 数据源与 Failover

完整原文见算法包内 `backend/trend_breakout/DATA_SOURCES.md`（由现网复制）。摘要：

## 股票日线
1. 本地 parquet `$ASHARE_KLINE_ROOT/raw/daily/{code6}.parquet`
2. `stock_zh_a_hist`（东财）
3. `stock_zh_a_daily`（新浪）
4. `stock_zh_a_hist_tx`（腾讯）
5. 必要时用 spot 补 T 日 bar

## 两市成交额
东财指数 K 线金额 → 新浪/腾讯指数 → spot 合计；成功值写入 `cache/turnover_daily.json`。

## 涨跌停 / 广度 / 行业
见 DATA_SOURCES.md：legu / 东财池 / spot 估计；行业 `stock_individual_info_em` → CompanySurveyAjax。

**原则**：不编造数字；全源失败则记入 `data_gaps`。
