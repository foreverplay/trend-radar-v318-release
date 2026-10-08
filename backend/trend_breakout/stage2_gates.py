#!/usr/bin/env python3
"""Apply hard gates + scoring on prefiltered candidates."""
import json, time, warnings, math
from pathlib import Path
import numpy as np
import pandas as pd
import akshare as ak

warnings.filterwarnings("ignore")
OUT = _PKG_ROOT
T_DATE = "2026-09-15"

meta = json.loads((OUT / "market_meta.json").read_text())
regime = meta["regime"]
ms = meta["market_score"]
cap = meta["cap"]
hs300_ret20 = float(meta.get("hs300_ret20") or 0)
hs300_ret60 = float(meta.get("hs300_ret60") or 0)
data_gaps = list(meta.get("data_gaps") or [])

cand = pd.read_pickle(OUT / "prefilter.pkl")
spot = pd.read_pickle(OUT / "spot_sina.pkl")

# industry board map (eastmoney)
ind_map = {}
try:
    boards = ak.stock_board_industry_name_em()
    # sample top industries by change for industry RS approx
    boards.to_pickle(OUT / "industry_boards.pkl")
    print("industry boards", len(boards), boards.columns.tolist())
except Exception as e:
    data_gaps.append(f"industry boards: {e}")
    boards = None

def fetch_hist(code6, market_prefix):
    """code like 300913, market sh/sz -> use stock_zh_a_hist"""
    symbol = code6
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date="20250101",
                end_date="20260915",
                adjust="qfq",
            )
            if df is not None and len(df):
                return df
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(0.4 * (attempt + 1))
    return None

def to_code6(full):
    # sh600869 -> 600869
    return full[2:]

results = []
failures = []

# Also try industry membership for candidates later
print("fetching hist for", len(cand))
for i, row in cand.iterrows():
    full = row["代码"]
    name = row["名称"]
    code6 = to_code6(full)
    # exclude new listing markers
    if str(name).startswith(("N", "C")) or "ST" in str(name):
        continue
    hist = fetch_hist(code6, full[:2])
    if hist is None or len(hist) < 70:
        failures.append({"code": full, "name": name, "reason": "hist_short_or_fail", "n": 0 if hist is None else len(hist)})
        time.sleep(0.05)
        continue
    hist = hist.copy()
    # normalize columns
    # akshare: 日期 开盘 收盘 最高 最低 成交量 成交额 振幅 涨跌幅 涨跌额 换手率
    hist.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
        "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct",
        "换手率": "turnover",
    }, inplace=True)
    hist["date"] = pd.to_datetime(hist["date"])
    hist = hist.sort_values("date").reset_index(drop=True)
    # ensure T-day row exists; if last is T-1, append from spot
    last = hist["date"].iloc[-1].strftime("%Y-%m-%d")
    if last < T_DATE:
        # append T from spot
        hist = pd.concat([hist, pd.DataFrame([{
            "date": pd.Timestamp(T_DATE),
            "open": row["今开"],
            "close": row["最新价"],
            "high": row["最高"],
            "low": row["最低"],
            "volume": row["成交量"],  # sina volume unit: shares? often hands-inconsistent
            "amount": row["成交额"],
            "pct": row["涨跌幅"],
            "turnover": np.nan,
        }])], ignore_index=True)
    elif last > T_DATE:
        hist = hist[hist["date"] <= T_DATE].reset_index(drop=True)

    if len(hist) < 70:
        failures.append({"code": full, "name": name, "reason": "hist_lt70", "n": len(hist)})
        continue

    # listing age: require >= 60 trading days in hist (approx)
    if len(hist) < 60:
        continue

    close = hist["close"].astype(float)
    high = hist["high"].astype(float)
    vol = hist["volume"].astype(float)
    amt = hist["amount"].astype(float)

    cT = float(close.iloc[-1])
    # MA20 / MA60
    ma20 = float(close.iloc[-20:].mean())
    ma60 = float(close.iloc[-60:].mean())
    ma20_t5 = float(close.iloc[-25:-5].mean()) if len(close) >= 25 else float(close.iloc[-20:].mean())
    # Previous20High = MAX(High[T-20:T-1]) EXCLUDE T
    prev20high = float(high.iloc[-21:-1].max())
    # VolumeRatio = Volume(T) / MA20Volume(T-1)
    # Prefer amount-based vol ratio if volume units inconsistent when appended
    ma20vol_tm1 = float(vol.iloc[-21:-1].mean())
    vol_T = float(vol.iloc[-1])
    vol_ratio = vol_T / ma20vol_tm1 if ma20vol_tm1 > 0 else np.nan
    # also amount ratio as cross-check
    ma20amt_tm1 = float(amt.iloc[-21:-1].mean())
    amt_T = float(amt.iloc[-1])
    amt_ratio = amt_T / ma20amt_tm1 if ma20amt_tm1 > 0 else np.nan
    # Use amount ratio if volume ratio looks broken (e.g. after spot append unit mismatch)
    if last < T_DATE or (not np.isfinite(vol_ratio)) or vol_ratio > 20 or vol_ratio < 0.05:
        use_ratio = amt_ratio
        ratio_src = "amount"
    else:
        use_ratio = vol_ratio
        ratio_src = "volume"
        # if amt_ratio more plausible and differs a lot, prefer amt
        if np.isfinite(amt_ratio) and abs(math.log((amt_ratio+1e-9)/(vol_ratio+1e-9))) > 0.5:
            use_ratio = amt_ratio
            ratio_src = "amount"

    # liquidity 20d avg amount
    avg20_amt = float(amt.iloc[-20:].mean())
    if avg20_amt < 3e7:  # 3000万
        continue

    # returns
    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else np.nan
    ret60 = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) >= 61 else np.nan
    rs20 = ret20 - hs300_ret20
    rs60 = ret60 - hs300_ret60

    # HARD GATES
    gates = {
        "close_gt_ma20": cT > ma20,
        "rs20_gt0": rs20 > 0,
        "rs60_gt0": rs60 > 0,
        "close_gt_prev20high": cT > prev20high,
        "vol_ratio_ge_1_3": use_ratio >= 1.3,
    }
    hard_pass = all(gates.values())

    breakout_strength = (cT / prev20high - 1) * 100 if prev20high > 0 else np.nan
    dist_ma20 = (cT / ma20 - 1) * 100 if ma20 > 0 else np.nan
    ma20_gt_ma60 = ma20 > ma60
    ma20_rising = ma20 > ma20_t5

    # exclude dist > 30%
    prefer_ok_dist = dist_ma20 <= 30

    # state
    state = "趋势成立未突破"
    if hard_pass:
        if breakout_strength >= 2 and use_ratio >= 2.0 and dist_ma20 > 15:
            state = "高位加速"
        elif breakout_strength >= 0 and use_ratio >= 1.3:
            # first day breakout vs already above
            # check if T-1 was already above prev20high computed then
            prev20high_tm1 = float(high.iloc[-22:-2].max()) if len(high) >= 22 else prev20high
            c_tm1 = float(close.iloc[-2])
            if c_tm1 <= prev20high_tm1 and cT > prev20high:
                state = "突破刚确认"
            else:
                state = "已确认突破"
        if use_ratio >= 3 and breakout_strength < 1:
            state = "疑似假突破"
    else:
        if cT > ma20 and rs20 > 0 and (cT / prev20high) >= 0.98 and cT <= prev20high:
            state = "接近突破"
        elif cT > ma20 and ma20_gt_ma60 and not gates["close_gt_prev20high"]:
            state = "趋势成立未突破"

    # risks
    risks = []
    if dist_ma20 > 15:
        risks.append("追高")
    if use_ratio >= 2.5 and dist_ma20 > 10:
        risks.append("情绪爆量")
    if state == "疑似假突破" or (use_ratio >= 2 and breakout_strength < 0.5):
        risks.append("假突破")
    pct_today = float(row["涨跌幅"]) if pd.notna(row["涨跌幅"]) else None
    if row.get("limit_up", False) or (pct_today is not None and pct_today >= 9.5):
        risks.append("一字板/难成交")
    if not prefer_ok_dist:
        risks.append("偏离MA20过大")

    # industry placeholder
    industry = "未知"
    industry_score = 50  # neutral when unknown
    industry_rs_note = "信息不足"
    risks.append("信息不足") if industry == "未知" else None

    # TrendScore components (0-100 total)
    # market15
    market_comp = min(15, max(0, ms / 100 * 15))
    # industry15
    ind_comp = industry_score / 100 * 15
    # stockRS15
    rs_comp = 0
    if rs20 > 0:
        rs_comp += min(8, rs20 * 100 * 0.5)
    if rs60 > 0:
        rs_comp += min(7, rs60 * 100 * 0.25)
    rs_comp = min(15, rs_comp)
    # trend15
    trend_comp = 0
    if cT > ma20:
        trend_comp += 5
    if ma20_gt_ma60:
        trend_comp += 5
    if ma20_rising:
        trend_comp += 5
    # breakout15
    bo_comp = 0
    if cT > prev20high:
        bo_comp += 8
        if breakout_strength >= 2:
            bo_comp += 5
        elif breakout_strength >= 1:
            bo_comp += 3
        else:
            bo_comp += 1
    elif cT >= prev20high * 0.98:
        bo_comp += 4
    bo_comp = min(15, bo_comp)
    # volume15
    vol_comp = 0
    if use_ratio >= 2.0:
        vol_comp = 15
    elif use_ratio >= 1.5:
        vol_comp = 12
    elif use_ratio >= 1.3:
        vol_comp = 9
    elif use_ratio >= 1.0:
        vol_comp = 5
    # pricePos5
    if dist_ma20 <= 8:
        pos_comp = 5
    elif dist_ma20 <= 15:
        pos_comp = 4
    elif dist_ma20 <= 20:
        pos_comp = 2
    elif dist_ma20 <= 30:
        pos_comp = 1
    else:
        pos_comp = 0
    # turnoverStruct5
    turn_comp = 3 if avg20_amt >= 1e8 else (2 if avg20_amt >= 5e7 else 1)
    if amt_T >= avg20_amt * 1.3:
        turn_comp = min(5, turn_comp + 2)

    score = market_comp + ind_comp + rs_comp + trend_comp + bo_comp + vol_comp + pos_comp + turn_comp
    score = float(np.clip(score, 0, 100))

    # Grades
    grade = None
    core = hard_pass and prefer_ok_dist and dist_ma20 <= 30
    # S: Market>=R3 AND IndustryScore>=75 AND RS20>5% AND RS60>10% AND Close>MA20 AND MA20>MA60 AND breakout AND VolRatio>=1.5 AND DistMA20<=15% AND Score>=90
    regime_rank = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}[regime]
    if (
        regime_rank >= 3
        and industry_score >= 75
        and rs20 > 0.05
        and rs60 > 0.10
        and cT > ma20
        and ma20_gt_ma60
        and cT > prev20high
        and use_ratio >= 1.5
        and dist_ma20 <= 15
        and score >= 90
    ):
        grade = "S"
    elif score >= 85 and hard_pass and cT > ma20 and rs20 > 0 and rs60 > 0 and cT > prev20high and use_ratio >= 1.3 and dist_ma20 <= 30:
        grade = "A"
    elif hard_pass and score >= 70:
        grade = "B"
    elif hard_pass:
        grade = "C"

    rec = {
        "code": full,
        "code6": code6,
        "name": name,
        "close": round(cT, 3),
        "pct_today": round(float(row["涨跌幅"]), 3) if pd.notna(row["涨跌幅"]) else None,
        "ma20": round(ma20, 3),
        "ma60": round(ma60, 3),
        "ma20_gt_ma60": bool(ma20_gt_ma60),
        "ma20_rising": bool(ma20_rising),
        "prev20high": round(prev20high, 3),
        "breakout_strength_pct": round(float(breakout_strength), 3),
        "vol_ratio": round(float(use_ratio), 3) if np.isfinite(use_ratio) else None,
        "ratio_src": ratio_src,
        "dist_ma20_pct": round(float(dist_ma20), 3),
        "rs20_pct": round(float(rs20) * 100, 3),
        "rs60_pct": round(float(rs60) * 100, 3),
        "ret20_pct": round(float(ret20) * 100, 3),
        "ret60_pct": round(float(ret60) * 100, 3),
        "avg20_amt_yi": round(avg20_amt / 1e8, 3),
        "amt_T_yi": round(amt_T / 1e8, 3),
        "gates": gates,
        "hard_pass": bool(hard_pass),
        "score": round(score, 2),
        "score_breakdown": {
            "market": round(market_comp, 2),
            "industry": round(ind_comp, 2),
            "stockRS": round(rs_comp, 2),
            "trend": round(trend_comp, 2),
            "breakout": round(bo_comp, 2),
            "volume": round(vol_comp, 2),
            "pricePos": round(pos_comp, 2),
            "turnoverStruct": round(turn_comp, 2),
        },
        "grade": grade,
        "state": state,
        "risks": risks,
        "industry": industry,
        "industry_score": industry_score,
        "industry_note": industry_rs_note,
        "limit_up": bool(row.get("limit_up", False)),
    }
    results.append(rec)
    if hard_pass:
        print(f"PASS {full} {name} score={score:.1f} grade={grade} VR={use_ratio:.2f} BS={breakout_strength:.2f}% dist={dist_ma20:.1f}% RS20={rs20*100:.1f}%")
    time.sleep(0.08)

print("done screened", len(results), "failures", len(failures), "hard_pass", sum(1 for r in results if r["hard_pass"]))

# Enrich industry via stock_board_industry_cons_em is too slow; try individual stock info
def get_industry(code6):
    try:
        # individual info
        info = ak.stock_individual_info_em(symbol=code6)
        if info is not None and len(info):
            m = info[info["item"].astype(str).str.contains("行业")]
            if len(m):
                return str(m.iloc[0]["value"])
    except Exception:
        pass
    return None

# only for hard_pass core
core = [r for r in results if r["hard_pass"] and r["dist_ma20_pct"] <= 30]
core.sort(key=lambda x: (-(x["score"]), -x["rs20_pct"]))

# prefer MA20>MA60 rising etc already in score
# try industry for top 30
for r in core[:30]:
    ind = get_industry(r["code6"])
    if ind:
        r["industry"] = ind
        r["risks"] = [x for x in r["risks"] if x != "信息不足"]
        # without board RS, keep industry_score modest mid
        r["industry_score"] = 55
        r["industry_note"] = "有行业分类但行业RS未精确计算"
        # rebuild industry component lightly: keep score as-is (already used 50)
    time.sleep(0.05)

# Cap selection: prefer core <=10; S 0-5, A 5-10; NEVER pad; R1 cap 5
s_list = [r for r in core if r["grade"] == "S"][:5]
a_list = [r for r in core if r["grade"] == "A"]
# If no S/A but hard_pass B — do NOT push B/C per strategy
# For R1, still only S/A in watchlist; if none, empty
selected = []
selected.extend(s_list)
for r in a_list:
    if len(selected) >= min(10, cap):
        break
    if len([x for x in selected if x["grade"] == "A"]) >= 10:
        break
    selected.append(r)

# If no S/A, leave empty (don't pad with B/C)
print("S", len(s_list), "A_pool", len(a_list), "selected", len(selected))
for r in selected:
    print(r["grade"], r["code"], r["name"], r["score"], r["state"], r["vol_ratio"], r["breakout_strength_pct"])

out = {
    "t_date": T_DATE,
    "regime": regime,
    "market_score": ms,
    "cap": cap,
    "screened": len(results),
    "hard_pass": sum(1 for r in results if r["hard_pass"]),
    "selected": selected,
    "all_hard_pass": core[:30],
    "failures_n": len(failures),
    "data_gaps": data_gaps,
    "meta": meta,
}
(OUT / "candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
(OUT / "all_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
print("saved candidates.json")
