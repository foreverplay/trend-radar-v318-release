#!/usr/bin/env python3
"""LEGACY V1 stage2b — prefer v2_staircase_scan.py as primary daily output.

Stage2b: hard gates + score + report inputs (shared hist/industry fallbacks)."""
import json, time, warnings, math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

import data_sources as ds

warnings.filterwarnings("ignore")
OUT = _PKG_ROOT
meta = json.loads((OUT / "market_meta.json").read_text())
T_DATE = meta.get("t_date") or ds.detect_t_date()["value"]["t_date"]
regime = meta["regime"]; ms = meta["market_score"]; cap = meta["cap"]
hs300_ret20 = float(meta.get("hs300_ret20") or 0)
hs300_ret60 = float(meta.get("hs300_ret60") or 0)
data_gaps = list(meta.get("data_gaps") or [])

cand = pd.read_pickle(OUT / "prefilter.pkl").copy()
cand = cand[~cand["名称"].astype(str).str.match(r"^[NC]")]
cand = cand[~cand["名称"].astype(str).str.contains("ST|退", na=False)]
cand["pre_score"] = cand["涨跌幅"].clip(0, 15) * 2 + np.log10(cand["成交额"].clip(1e4)) * 3 + (cand["最新价"]/cand["最高"]).fillna(0)*10
cand = cand.nlargest(60, "pre_score")
print("candidates", len(cand), "T_DATE", T_DATE)

end_compact = T_DATE.replace("-", "")

def fetch_hist(full):
    res = ds.fetch_stock_hist(full, start="20250101", end=end_compact)
    if res["ok"]:
        return full, res["value"], None, res["source"]
    return full, None, "; ".join((res.get("errors") or ["fail"])[:2]), None

rows = list(cand.itertuples(index=False))
code_to_row = {r.代码: r for r in rows}

hists = {}
hist_sources = {}
errs = []
print("fetching...")
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(fetch_hist, r.代码): r.代码 for r in rows}
    done = 0
    for fut in as_completed(futs):
        full, df, err, src = fut.result()
        done += 1
        if df is not None:
            hists[full] = df
            hist_sources[full] = src
        else:
            errs.append({"code": full, "err": err})
        if done % 10 == 0:
            print(f"  fetched {done}/{len(rows)} ok={len(hists)}")

print("hist ok", len(hists), "err", len(errs))
from collections import Counter
print("hist sources", dict(Counter(hist_sources.values())))
data_gaps.append(f"hist fetch failures: {len(errs)}")
data_gaps.append("hist chain: local parquet → eastmoney → sina daily → tencent; T bar from spot when lag")

results = []
for full, hist in hists.items():
    row = code_to_row[full]
    name = row.名称
    code6 = full[2:]
    hist = ds.append_t_bar_from_spot(hist, T_DATE, row)
    hist = hist.copy()
    hist["date"] = pd.to_datetime(hist["date"])
    hist = hist.sort_values("date")
    hist = hist[hist["date"] <= T_DATE].reset_index(drop=True)
    if len(hist) < 70:
        continue
    last = hist["date"].iloc[-1].strftime("%Y-%m-%d")
    if last != T_DATE:
        continue

    close = hist["close"].astype(float)
    high = hist["high"].astype(float)
    vol = hist["volume"].astype(float)
    amt = hist["amount"].astype(float)

    cT = float(close.iloc[-1])
    ma20 = float(close.iloc[-20:].mean())
    ma60 = float(close.iloc[-60:].mean())
    ma20_t5 = float(close.iloc[-25:-5].mean())
    prev20high = float(high.iloc[-21:-1].max())
    ma20vol_tm1 = float(vol.iloc[-21:-1].mean())
    vol_T = float(vol.iloc[-1])
    vol_ratio = vol_T / ma20vol_tm1 if ma20vol_tm1 > 0 else np.nan
    ma20amt_tm1 = float(amt.iloc[-21:-1].mean())
    amt_T = float(amt.iloc[-1])
    amt_ratio = amt_T / ma20amt_tm1 if ma20amt_tm1 > 0 else np.nan
    use_ratio = vol_ratio
    ratio_src = "volume"
    if not np.isfinite(use_ratio) or use_ratio > 30:
        use_ratio = amt_ratio; ratio_src = "amount"

    avg20_amt = float(amt.iloc[-20:].mean())
    if avg20_amt < 3e7:
        continue

    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1)
    ret60 = float(close.iloc[-1] / close.iloc[-61] - 1)
    rs20 = ret20 - hs300_ret20
    rs60 = ret60 - hs300_ret60

    gates = {
        "close_gt_ma20": cT > ma20,
        "rs20_gt0": bool(rs20 > 0),
        "rs60_gt0": bool(rs60 > 0),
        "close_gt_prev20high": cT > prev20high,
        "vol_ratio_ge_1_3": bool(use_ratio >= 1.3),
    }
    hard_pass = all(gates.values())

    breakout_strength = (cT / prev20high - 1) * 100
    dist_ma20 = (cT / ma20 - 1) * 100
    ma20_gt_ma60 = ma20 > ma60
    ma20_rising = ma20 > ma20_t5

    hard_core_eligible = dist_ma20 <= 30

    state = "趋势成立未突破"
    if hard_pass:
        prev20high_tm1 = float(high.iloc[-22:-2].max()) if len(high) >= 22 else prev20high
        c_tm1 = float(close.iloc[-2])
        if use_ratio >= 2.5 and breakout_strength < 0.8:
            state = "疑似假突破"
        elif breakout_strength >= 2 and use_ratio >= 2.0 and dist_ma20 > 15:
            state = "高位加速"
        elif c_tm1 <= prev20high_tm1 and cT > prev20high:
            state = "突破刚确认"
        else:
            state = "已确认突破"
    else:
        if cT > ma20 and rs20 > 0 and cT <= prev20high and cT >= prev20high * 0.98:
            state = "接近突破"
        elif cT > ma20 and ma20_gt_ma60:
            state = "趋势成立未突破"

    risks = []
    if dist_ma20 > 15:
        risks.append("追高")
    if use_ratio >= 2.5 and dist_ma20 > 10:
        risks.append("情绪爆量")
    if state == "疑似假突破":
        risks.append("假突破")
    pct_today = float(row.涨跌幅) if pd.notna(row.涨跌幅) else 0
    limit_up = bool(getattr(row, "limit_up", False))
    if limit_up or pct_today >= 9.5:
        risks.append("一字板/难成交")

    industry = "未知"
    industry_score = 50
    industry_note = "信息不足"
    risks.append("信息不足")

    market_comp = min(15.0, max(0.0, ms / 100 * 15))
    ind_comp = industry_score / 100 * 15
    rs_comp = 0.0
    if rs20 > 0: rs_comp += min(8, rs20 * 100 * 0.5)
    if rs60 > 0: rs_comp += min(7, rs60 * 100 * 0.25)
    rs_comp = min(15, rs_comp)
    trend_comp = (5 if cT > ma20 else 0) + (5 if ma20_gt_ma60 else 0) + (5 if ma20_rising else 0)
    bo_comp = 0.0
    if cT > prev20high:
        bo_comp = 8
        if breakout_strength >= 2: bo_comp += 5
        elif breakout_strength >= 1: bo_comp += 3
        else: bo_comp += 1
    elif cT >= prev20high * 0.98:
        bo_comp = 4
    bo_comp = min(15, bo_comp)
    if use_ratio >= 2.0: vol_comp = 15
    elif use_ratio >= 1.5: vol_comp = 12
    elif use_ratio >= 1.3: vol_comp = 9
    elif use_ratio >= 1.0: vol_comp = 5
    else: vol_comp = 0
    if dist_ma20 <= 8: pos_comp = 5
    elif dist_ma20 <= 15: pos_comp = 4
    elif dist_ma20 <= 20: pos_comp = 2
    elif dist_ma20 <= 30: pos_comp = 1
    else: pos_comp = 0
    turn_comp = 3 if avg20_amt >= 1e8 else (2 if avg20_amt >= 5e7 else 1)
    if amt_T >= avg20_amt * 1.3:
        turn_comp = min(5, turn_comp + 2)

    score = float(np.clip(market_comp + ind_comp + rs_comp + trend_comp + bo_comp + vol_comp + pos_comp + turn_comp, 0, 100))

    regime_rank = {"R0":0,"R1":1,"R2":2,"R3":3,"R4":4}[regime]
    grade = None
    if (
        regime_rank >= 3 and industry_score >= 75 and rs20 > 0.05 and rs60 > 0.10
        and cT > ma20 and ma20_gt_ma60 and cT > prev20high and use_ratio >= 1.5
        and dist_ma20 <= 15 and score >= 90
    ):
        grade = "S"
    elif score >= 85 and hard_pass and dist_ma20 <= 30:
        grade = "A"
    elif hard_pass and score >= 70:
        grade = "B"
    elif hard_pass:
        grade = "C"

    rec = {
        "code": full, "code6": code6, "name": name,
        "close": round(cT, 3), "pct_today": round(pct_today, 3),
        "ma20": round(ma20, 3), "ma60": round(ma60, 3),
        "ma20_gt_ma60": bool(ma20_gt_ma60), "ma20_rising": bool(ma20_rising),
        "prev20high": round(prev20high, 3),
        "breakout_strength_pct": round(float(breakout_strength), 3),
        "vol_ratio": round(float(use_ratio), 3), "ratio_src": ratio_src,
        "dist_ma20_pct": round(float(dist_ma20), 3),
        "rs20_pct": round(rs20*100, 3), "rs60_pct": round(rs60*100, 3),
        "ret20_pct": round(ret20*100, 3), "ret60_pct": round(ret60*100, 3),
        "avg20_amt_yi": round(avg20_amt/1e8, 3), "amt_T_yi": round(amt_T/1e8, 3),
        "gates": gates, "hard_pass": bool(hard_pass),
        "hard_core_eligible": bool(hard_pass and hard_core_eligible),
        "score": round(score, 2),
        "score_breakdown": {
            "market": round(market_comp,2), "industry": round(ind_comp,2),
            "stockRS": round(rs_comp,2), "trend": round(trend_comp,2),
            "breakout": round(bo_comp,2), "volume": round(vol_comp,2),
            "pricePos": round(pos_comp,2), "turnoverStruct": round(turn_comp,2),
        },
        "grade": grade, "state": state, "risks": risks,
        "industry": industry, "industry_score": industry_score, "industry_note": industry_note,
        "limit_up": limit_up,
        "hist_source": hist_sources.get(full),
    }
    results.append(rec)
    if hard_pass:
        print(f"PASS {full} {name} sc={score:.1f} g={grade} VR={use_ratio:.2f} BS={breakout_strength:.2f} dMA={dist_ma20:.1f} RS20={rs20*100:.1f}")

core = [r for r in results if r["hard_core_eligible"]]
core.sort(key=lambda x: (-x["score"], -x["rs20_pct"]))
print("hard_core", len(core))

for r in core[:20]:
    ind_res = ds.fetch_industry(r["code6"])
    ind = ind_res["value"] if ind_res["ok"] else None
    if ind and ind != "未知":
        r["industry"] = ind
        r["industry_source"] = ind_res.get("source")
        r["risks"] = [x for x in r["risks"] if x != "信息不足"]
        r["industry_score"] = 55
        r["industry_note"] = "有行业分类；行业RS未精确计算，行业分按中性偏低"
    else:
        r["industry_source"] = ind_res.get("source")
    time.sleep(0.05)

for r in core:
    sb = r["score_breakdown"]
    sb["industry"] = round(r["industry_score"]/100*15, 2)
    r["score"] = round(sum(sb.values()), 2)
    if r["score"] >= 85 and r["hard_pass"] and r["dist_ma20_pct"] <= 30:
        r["grade"] = "A"
    elif r["hard_pass"] and r["score"] >= 70:
        r["grade"] = "B"
    elif r["hard_pass"]:
        r["grade"] = "C"

core.sort(key=lambda x: (-x["score"], -x["rs20_pct"]))
s_list = [r for r in core if r["grade"] == "S"][:5]
a_list = [r for r in core if r["grade"] == "A"]
selected = []
selected.extend(s_list)
for r in a_list:
    if len(selected) >= min(10, cap):
        break
    selected.append(r)

print("S", len(s_list), "A", len(a_list), "selected", len(selected))
b_top = [r for r in core if r["grade"] == "B"][:10]
print("top B (not pushed):", [(r["code"], r["name"], r["score"]) for r in b_top[:5]])

out = {
    "t_date": T_DATE,
    "regime": regime,
    "market_score": ms,
    "cap": cap,
    "screened": len(results),
    "hard_pass": sum(1 for r in results if r["hard_pass"]),
    "hard_core_eligible": len(core),
    "S": len(s_list),
    "A": len([r for r in selected if r["grade"]=="A"]),
    "selected": selected,
    "all_hard_pass": core[:30],
    "top_B_not_pushed": b_top,
    "failures_n": len(errs),
    "data_gaps": data_gaps,
    "meta": {k:v for k,v in meta.items() if k != "data_gaps"},
}
(OUT/"candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
(OUT/"all_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
print("saved")

# Near-breakout observation pool (separate from S/A core push)
try:
    from build_near_pool import build_pool
    build_pool(patch_report=True)
except Exception as e:
    print("near_pool build skip:", type(e).__name__, e)

