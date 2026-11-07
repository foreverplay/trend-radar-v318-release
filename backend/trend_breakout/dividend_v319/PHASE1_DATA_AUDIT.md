# TrendConfirmationBot V3.1.9 Phase1 — 数据与架构验收

- 验收时刻：2026-11-04（Asia/Shanghai）
- 范围：分红事件 / 财务 PIT / 复权口径 / 红利指数成分 / 可复用模块与缺口
- 约束：未修改 `publish_close_v318.py` / `v3/publish_close_v318.py`；未改 A+/成熟度模型

## 0. 结论摘要

| 维度 | 状态 | 说明 |
|---|:---:|---|
| 分红事件（金额+公告日/除权日） | ✅ | 本地 fhps 2021–2025；akshare 可延伸更早；cninfo 单票长历史 |
| 财务 PIT（NOTICE_DATE） | ✅ | lrb/xjll/zcfz/yjbb clean panels 均有 NOTICE_DATE；年报 hist 2000–2020 |
| 复权 / TotalReturn | ⚠️ | 本地日线为**前复权 qfq**，无 adj_factor、无 daily_hfq、无未复权主库 |
| 红利指数成分 | ⚠️ | **当前**成分可取（中证/上证红利）；**历史时点成分**本地缺失 |
| 动态历史可交易池（防幸存者） | ⚠️ | 分红面板可按公告日 PIT；指数历史成分与 ST 时间线未齐 |
| Growth 隔离 | ✅ | 新代码在 `dividend_v319/`；publish 路径未改 |

**Phase1 可继续 Phase2（完善 DividendUniverse + 分红事件规范化）**；大规模评分前必须先定 TotalReturn 口径并补未复权或严格使用 hfq 代理且禁止双重计算。

---

## 1. 现有可复用模块

### 1.1 Trend Bot（`/workspace/trend-breakout`）

| 资产 | 路径 | 红利侧用途 |
|---|---|---|
| Live publish V3.1.8 | `publish_close_v318.py` → `v3/publish_close_v318.py` | **勿改**；日后仅建议挂接「并行写红利榜 JSON」 |
| Growth engines | `v3/engines/*`（Technical/Fundamental/Risk/Exit…） | 可参考结构；**不得**用 Growth Grade 决定红利等级 |
| Backtest kit | `v3/backtest/{engine,costs,metrics,walk_forward}.py` | Phase12+ 可复用成本与 WF 骨架 |
| Types/config | `v3/types.py`, `v3/v31/config.py` | 风格参考；红利类型独立在 `dividend_v319/models` |
| Data fallbacks | `data_sources.py`, `DATA_SOURCES.md` | 行情/现货兜底；分红主源不在此 |

> 说明：仓库内已有 `v3/engines/`（非扁平-only）。红利模块按 Plan 放在独立包 `dividend_v319/`，避免污染 Growth。

### 1.2 财务与分红（`/workspace/ashare-financials`）

| 资产 | 路径 | 状态 |
|---|---|---|
| 分红派送面板 | `raw/fhps/fhps_2021_2025_all.parquet`（+分报告期文件） | ✅ 关键 |
| 连续5年现金分红分析 | `analysis/连续现金分红5年_*.csv/md` | ✅ 口径可复用 |
| 腾讯后复权缓存 | `analysis/_hfq_daily_cache/{code}.parquet`（约 2108 票，约 2021-09–2026-09） | ⚠️ 覆盖不全 |
| 利润/现金流/负债/业绩 | `clean/{lrb,xjll,zcfz,yjbb}_universe_panel.parquet` | ✅ 含 NOTICE_DATE |
| 年报 hist 2000–2020 | `clean/*_annual_hist_2000_2020_universe_panel.parquet` | ✅ |
| 非 ST 宇宙 | `meta/universe_listed_non_st.csv` | ✅ 当前截面 |
| 字段字典 | `meta/field_dict_*.json`, `schema/` | ✅ |

### 1.3 行情（`/workspace/ashare-kline`）

| 资产 | 路径 | 状态 |
|---|---|---|
| 日线主库 | `raw/daily/{code6}.parquet` | ✅ 存在；**schema 标明前复权 qfq** |
| 后复权目录 | `raw/daily_hfq/` | ❌ 目录不存在 |
| 宇宙 | `meta/universe.csv` | ✅ |
| 字段说明 | `meta/schema.md` | ✅ |

### 1.4 旧银行股息回测（`/workspace/ashare-tech-backtest`）

| 资产 | 说明 |
|---|---|
| `run_bank_dividend_backtest.py` + `results_bank_dividend/` | 银行代理池 + 技术信号组合；**不是**事件分红总回报引擎，仅作对照参考 |

---

## 2. 分红事件探测（真实文件 / 实拉）

### 2.1 本地 fhps（主）

- 文件：`/workspace/ashare-financials/raw/fhps/fhps_2021_2025_all.parquet`
- 形状：20973 × 31；报告期 20210331–20251231（20 个）
- 关键字段：
  - `PRETAX_BONUS_RMB`：税前「10 派 X」中的 X（现金）
  - `PLAN_NOTICE_DATE` / `PUBLISH_DATE` / `NOTICE_DATE`：公告相关日
  - `EX_DIVIDEND_DATE` / `EQUITY_RECORD_DATE`：除权除息 / 股权登记
  - `ASSIGN_PROGRESS`：实施分配 / 预披露 / 取消…
  - `IMPL_PLAN_PROFILE`：方案文本（可辅助识别特别分红）
  - `DIVIDENT_RATIO`：面板股息率字段（勿盲目当作自算 TTM）
- 样本（600000 浦发）：2021–2025 年报期均有实施现金分红，含预案公告日与除权日
- 口径交叉验证：`analysis/连续现金分红5年_后复权五年年化收益排行_口径.md`（东财 RPT_SHAREBONUS_DET / ak `stock_fhps_em`）

### 2.2 akshare 延伸样本（已落 `dividend_v319/cache/`）

| 探测 | 结果 |
|---|---|
| `stock_fhps_em(20201231)` | ✅ 3123 行；含预案公告日/除权除息日/现金分红比例 |
| `stock_fhps_em(20191231)` | ✅ 2778 行（cache 存 head50） |
| `stock_history_dividend_detail(600000)` | ✅ 26 行；公告日期+除权除息日+派息 |
| `stock_dividend_cninfo(600000)` | ✅ 26 行；实施方案公告日期+分红类型+派息比例；可回溯至 2000 |

**特别分红**：本地 fhps 无独立 boolean；需用 `IMPL_PLAN_PROFILE` / cninfo `分红类型` + 规则（Phase3 Quality）。Phase1 未打标。

---

## 3. 财务 PIT

| 面板 | NOTICE_DATE | 报告期覆盖 |
|---|---|---|
| yjbb / lrb / xjll / zcfz universe | ✅ | 约 20210331–20260630 |
| *_annual_hist_2000_2020 | ✅ | 2000–2020 年报 |

回测规则：T 日仅使用 `NOTICE_DATE <= T` 的财务行（Phase4+ 强制）。

---

## 4. 复权与 TotalReturn 方案

### 4.1 事实

1. `ashare-kline/raw/daily` = **前复权 qfq**（`meta/schema.md`），列无 `adj_factor`。
2. `raw/daily_hfq/` **不存在**。
3. 分析用腾讯 hfq 缓存：仅 `date,close`，股票子集、年份窗口有限。
4. akshare `stock_zh_a_daily(..., adjust='')` 本环境可拉**未复权**样本（已烟测 601398）。

### 4.2 禁止

- 在 **qfq 或 hfq** 收益率上再叠加现金分红 → 双重计算。

### 4.3 推荐算法（一句话）

**目标口径：用未复权收盘价序列 + 现金分红事件（每股现金 = `PRETAX_BONUS_RMB`/10，在 `EX_DIVIDEND_DATE` 入账；PIT 可见性用公告日）分别计算 PriceReturn、DividendCashReturn（不复投）与 TotalReturn（除息日按未复权价再投资）；在未复权全市场落地前，允许用后复权收盘价差作为 TotalReturn 代理，但不得再加现金，且 DividendCashReturn 必须单独来自事件流。**

拆分：

| 指标 | 算法 |
|---|---|
| PriceReturn | 未复权收盘价比 | 
| DividendCashReturn | Σ (每股现金 / 期初未复权买入价)，不复投 |
| TotalReturn | 未复权价路径 + 除息日现金再投资；或 hfq 代理（无额外加息） |

---

## 5. 红利指数成分

| 指数 | 当前成分 | 历史时点成分 |
|---|---|---|
| 000922 中证红利 | ✅ ak `index_stock_cons_csindex` → cache（100） | ❌ 未入库 |
| 000015 上证红利 | ✅ 同上（50） | ❌ |
| 399324 深证红利 | ❌ 本次 csindex 拉取 Excel 引擎失败 | ❌ |

`index_stock_cons` 对 000922 返回当前成分+纳入日期，**不能**还原已被调出的历史成员 → 回测指数池仍有幸存者风险，Phase2 需另寻中证历史成分或退化为「自主分红池 PIT」。

---

## 6. 缺口清单（Gap List）

1. **未复权全市场日线**（或官方总回报指数）— TotalReturn 拆分刚需  
2. **本地 fhps 2021 前全量回填**（接口已验证可拉）  
3. **红利指数历史成分 PIT**  
4. **ST / 退市 / 长期停牌时间线**（现仅有当前 non_st + 名称关键字）  
5. **特别分红标准化标签**  
6. **PE/PB/股息率历史面板**（估值百分位；yjbb 有部分字段但未验收为估值时序）  
7. **行业分类稳定映射**（估值分位按行业）  
8. **将 Growth publish 与红利榜并行输出的挂接点**（仅文档建议，本 Phase 未改代码）

---

## 7. DividendUniverse Phase1 脚手架结果

命令：

```bash
/workspace/ashare-venv/bin/python /workspace/trend-breakout/dividend_v319/run_universe.py
```

输出：`dividend_v319/out/universe_latest.json`

| 计数 | 值 |
|---|---:|
| raw_union | 2307 |
| active（非排除） | 2201 |
| excluded | 106 |
| from_index | 101 |
| from_autonomous（连续现金分红 2021–2025） | 2196 |
| from_both | 96 |

规则摘要：指数当前成分 ∪ fhps 连续五年实施现金分红；排除名称含 ST/退/整理；自主池还要求落在 `universe_listed_non_st.csv`。

> Plan 目录名 `types/` 在实现中为 `models/`，避免与 Python 标准库 `types` 冲突。

---

## 8. 建议的 Live 挂接点（未实施）

在 **不改变** V3.1.8 成长榜行为的前提下，未来可：

1. `v3/publish_close_v318.py` 主流程末尾 **可选** 调用 `dividend_v319` 写 `push_close_dividend_v319_{T}.md/json`；或  
2. 独立 routine / 脚本 `publish_close_dividend_v319.py` 与成长榜并行。

Phase1 **零改动** publish。

---

## 9. 验收勾选（Phase1 子集）

```text
[x] 检查 V3.1.8 架构与数据源
[x] 确认分红事件至少若干票可拿（本地+实拉 cache）
[x] 确认财务 NOTICE_DATE 可用于 PIT
[x] 标明复权口径与 TotalReturn 方案（禁止双重计算）
[x] 红利指数当前成分可取；历史成分缺口已记录
[x] PHASE1_DATA_AUDIT.md + meta.json
[x] dividend_v319 脚手架 + universe_latest.json
[x] 未修改 publish_close_v318
[ ] 历史时点指数成分（Phase2+）
[ ] 未复权全市场落地（Phase2/6）
[ ] Quality 及后续评分引擎（Phase3+）
```

---

## 10. Phase2 下一步（具体）

见同目录 `meta.json` 字段 `phase2_next`，以及本回报「Phase2 命令」。
