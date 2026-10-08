#!/usr/bin/env python3
"""LEGACY V1 stage1 — prefer v2_staircase_scan.py as primary daily output.

Trend Confirmation Breakout — stage1 market + prefilter (multi-source fallbacks)."""
import json, time, warnings, traceback
from pathlib import Path
try:
    from paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
except ImportError:  # packaged / flat layout fallback
    try:
        from trend_breakout.paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
    except ImportError:
        from pathlib import Path as __P
        import os as __os
        _PKG_ROOT = __P(__os.environ.get("TREND_BREAKOUT_ROOT", __P(__file__).resolve().parent))
        _k = __P(__os.environ.get("ASHARE_KLINE_ROOT", str(_PKG_ROOT.parent.parent / "data" / "sample" / "kline")))
        _PKG_LOCAL_DAILY = _k / "raw" / "daily"
        _PKG_CACHE = _PKG_ROOT / "cache"
        _PKG_CONT_CACHE = _PKG_CACHE / "continuity_history"
import numpy as np
import pandas as pd
import akshare as ak

import data_sources as ds

warnings.filterwarnings("ignore")
OUT = _PKG_ROOT
OUT.mkdir(parents=True, exist_ok=True)
data_gaps = []

# Auto-detect T_DATE (do not hardcode forever)
_td = ds.detect_t_date()
T_DATE = _td["value"]["t_date"]
T_DATE_COMPACT = _td["value"]["t_compact"]
print(f"T_DATE={T_DATE} via {_td['source']}")
if _td.get("errors"):
    data_gaps.extend(_td["errors"])

def note_gap(msg):
    print("GAP", msg)
    data_gaps.append(msg)

# ========== 1. Spot first (used by turnover / zt-dt / prefilter) ==========
print("=== spot ===")
sp = ds.fetch_spot_universe()
spot = sp["value"] if sp["ok"] else None
if sp["ok"]:
    print(f"spot source={sp['source']} rows={len(spot)}")
    spot.to_pickle(OUT / "spot_sina.pkl")
else:
    note_gap("spot failed: " + "; ".join(sp.get("errors") or [])[:300])
if sp.get("errors"):
    for e in sp["errors"][:5]:
        note_gap(e)

# ========== 2. Market breadth + zt/dt ==========
print("=== market activity / zt-dt ===")
br = ds.fetch_breadth_zt_dt(T_DATE, spot=spot)
if not br["ok"]:
    note_gap("breadth/zt-dt failed: " + "; ".join(br.get("errors") or [])[:300])
    up_n = down_n = zt_n = dt_n = real_zt = real_dt = 0.0
else:
    bv = br["value"]
    up_n = float(bv["up"])
    down_n = float(bv["down"])
    zt_n = float(bv["zt"])
    dt_n = float(bv["dt"])
    real_zt = float(bv.get("real_zt", zt_n))
    real_dt = float(bv.get("real_dt", dt_n))
    print(f"breadth source={br['source']} up={up_n} down={down_n} zt={zt_n}({bv['zt_source']}) dt={dt_n}({bv['dt_source']})")
    print("sources detail", bv.get("sources"))
for e in (br.get("errors") or [])[:6]:
    note_gap(e)

# ========== 3. Index spot + history ==========
print("=== indices ===")
idx_map = {
    "上证指数": "sh000001",
    "沪深300": "sh000300",
    "中证1000": "sh000852",
    "创业板指": "sz399006",
}

idx_res = ds.fetch_sina_index_spot(list(idx_map.values()))
idx_spot = idx_res["value"] if idx_res["ok"] else None
if idx_res["ok"]:
    print("index spot", idx_res["source"])
else:
    note_gap("index spot failed")
for e in (idx_res.get("errors") or [])[:3]:
    note_gap(e)

index_info = {}
for name, code in idx_map.items():
    info = {"code": code, "name": name}
    if idx_spot is not None:
        m = idx_spot[idx_spot["代码"] == code]
        if len(m):
            info["close_T"] = float(m.iloc[0]["最新价"])
            info["pct_T"] = float(m.iloc[0]["涨跌幅"])
            info["preclose"] = float(m.iloc[0]["昨收"])
            if "今开" in m.columns:
                info["open_T"] = float(m.iloc[0]["今开"])
            if "最高" in m.columns:
                info["high_T"] = float(m.iloc[0]["最高"])
            if "最低" in m.columns:
                info["low_T"] = float(m.iloc[0]["最低"])
    ih = ds.fetch_index_hist(code)
    hist = ih["value"] if ih["ok"] else None
    if not ih["ok"]:
        note_gap(f"index_hist_{code}: " + "; ".join((ih.get("errors") or [])[:2]))
    if hist is not None and len(hist):
        hist = hist.copy()
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist.sort_values("date").reset_index(drop=True)
        last_d = hist["date"].iloc[-1].strftime("%Y-%m-%d")
        if last_d < T_DATE and "close_T" in info:
            new = {
                "date": pd.Timestamp(T_DATE),
                "open": info.get("open_T", np.nan),
                "high": info.get("high_T", np.nan),
                "low": info.get("low_T", np.nan),
                "close": info["close_T"],
                "volume": np.nan,
            }
            hist = pd.concat([hist, pd.DataFrame([new])], ignore_index=True)
        info["hist"] = hist
        info["close"] = float(hist["close"].iloc[-1])
        if "pct_T" not in info and len(hist) >= 2:
            info["pct_T"] = (hist["close"].iloc[-1] / hist["close"].iloc[-2] - 1) * 100
        info["ma20"] = float(hist["close"].tail(20).mean())
        info["ma60"] = float(hist["close"].tail(60).mean()) if len(hist) >= 60 else None
        info["above_ma20"] = info["close"] > info["ma20"]
        if len(hist) >= 21:
            info["ret20"] = float(hist["close"].iloc[-1] / hist["close"].iloc[-21] - 1)
        if len(hist) >= 61:
            info["ret60"] = float(hist["close"].iloc[-1] / hist["close"].iloc[-61] - 1)
        print(name, "pct", round(info.get("pct_T", 0), 3), "close", info["close"], "MA20", round(info["ma20"], 2), ">", info["above_ma20"], "hist", ih.get("source"))
    index_info[name] = info

hs300_ret20 = index_info.get("沪深300", {}).get("ret20", 0.0) or 0.0
hs300_ret60 = index_info.get("沪深300", {}).get("ret60", 0.0) or 0.0

# ========== 4. Turnover ==========
print("=== turnover ===")
to = ds.fetch_turnover_t(T_DATE, spot=spot)
hist_series = None
if to["ok"]:
    two_market_amt = float(to["value"]["amt_yi"])
    hist_series = to["value"].get("hist_series")
    print(f"turnover_T={two_market_amt:.2f}亿 source={to['source']}")
else:
    two_market_amt = None
    note_gap("turnover_T failed: " + "; ".join((to.get("errors") or [])[:4]))
for e in (to.get("errors") or [])[:8]:
    # keep primary failures visible but avoid huge dump
    if any(k in e for k in ["sse", "szse", "ValueError", "BadZip", "SSL", "Connection"]):
        note_gap(e)

ma = ds.fetch_turnover_ma(T_DATE, amt_t=two_market_amt, hist_series=hist_series)
if ma["ok"]:
    mv = ma["value"]
    amt_T = float(mv["amt_yi"])
    ma5 = float(mv["ma5"])
    ma20 = float(mv["ma20"])
    vs5 = float(mv["vs_ma5"] or 1)
    vs20 = float(mv["vs_ma20"] or 1)
    print(f"Turnover T={amt_T:.1f} MA5={ma5:.1f} MA20={ma20:.1f} vsMA5={vs5:.2f} vsMA20={vs20:.2f} hist_len={mv['hist_len']} src={ma['source']}")
    for g in mv.get("gap_flags") or []:
        note_gap(g)
else:
    amt_T = two_market_amt or 0.0
    ma5 = ma20 = amt_T
    vs5 = vs20 = 1.0
    note_gap("turnover MA failed: " + "; ".join((ma.get("errors") or [])[:3]))

# ========== 5. Regime classification (UNCHANGED thresholds) ==========
idx_pcts = [index_info[n].get("pct_T", 0) or 0 for n in ["上证指数", "沪深300", "中证1000", "创业板指"] if n in index_info]
avg_idx_pct = float(np.mean(idx_pcts)) if idx_pcts else 0
n_idx_up = sum(1 for p in idx_pcts if p > 0)
n_above_ma20 = sum(1 for n in ["上证指数", "沪深300", "中证1000", "创业板指"] if index_info.get(n, {}).get("above_ma20"))

breadth_ratio = up_n / max(down_n, 1)
up_share = up_n / max(up_n + down_n, 1)

ms = 50.0
ms += np.clip(avg_idx_pct * 8, -15, 15)
ms += (n_idx_up - 2) * 4
ms += (n_above_ma20 - 2) * 5
if up_share >= 0.65:
    ms += 12
elif up_share >= 0.55:
    ms += 6
elif up_share >= 0.45:
    ms += 0
elif up_share >= 0.35:
    ms -= 8
else:
    ms -= 15
zt_dt = real_zt / max(real_dt, 1)
if real_zt >= 80 and zt_dt >= 5:
    ms += 10
elif real_zt >= 50 and zt_dt >= 3:
    ms += 5
elif real_zt < 20 or zt_dt < 1:
    ms -= 8
vs5 = amt_T / ma5 if ma5 else 1
vs20 = amt_T / ma20 if ma20 else 1
if vs5 >= 1.15 and vs20 >= 1.0:
    ms += 8
elif vs5 >= 0.95:
    ms += 2
elif vs5 < 0.8:
    ms -= 8
else:
    ms -= 3

ms = float(np.clip(ms, 0, 100))

if ms < 30 or (up_share < 0.30 and avg_idx_pct < 0) or (real_dt > real_zt and avg_idx_pct < -1):
    regime = "R0"
elif ms < 45 or up_share < 0.40:
    regime = "R1"
elif ms < 60 or up_share < 0.52:
    regime = "R2"
elif ms < 75:
    regime = "R3"
else:
    regime = "R4"

caps = {"R0": 0, "R1": 5, "R2": 10, "R3": 15, "R4": 20}
cap = caps[regime]

market_conclusion = []
market_conclusion.append(f"四指数涨跌幅: " + ", ".join(f"{n} {index_info[n].get('pct_T', float('nan')):+.2f}%" for n in idx_map if n in index_info))
market_conclusion.append(f"涨跌家数 {int(up_n)}/{int(down_n)} (上涨占比 {up_share*100:.1f}%)")
market_conclusion.append(f"涨停/跌停 {int(real_zt)}/{int(real_dt)}")
market_conclusion.append(f"两市成交额约 {amt_T:.0f}亿, vsMA5={vs5:.2f}, vsMA20={vs20:.2f}")
market_conclusion.append(f"指数站上MA20家数 {n_above_ma20}/4")

print("REGIME", regime, "MarketScore", round(ms, 1), "cap", cap)
for line in market_conclusion:
    print(line)

meta = {
    "t_date": T_DATE,
    "regime": regime,
    "market_score": round(ms, 1),
    "cap": cap,
    "breadth": {"up": int(up_n), "down": int(down_n), "zt": int(real_zt), "dt": int(real_dt), "up_share": round(up_share, 4)},
    "turnover": {
        "amt_yi": round(amt_T, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "vs_ma5": round(vs5, 3),
        "vs_ma20": round(vs20, 3),
        "source": to.get("source"),
        "ma_source": ma.get("source"),
        "hist_len": (ma.get("value") or {}).get("hist_len"),
    },
    "zt_dt_source": br.get("source") if br.get("ok") else None,
    "spot_source": sp.get("source") if sp.get("ok") else None,
    "indices": {n: {"pct": index_info[n].get("pct_T"), "close": index_info[n].get("close"), "ma20": index_info[n].get("ma20"), "above_ma20": index_info[n].get("above_ma20")} for n in idx_map if n in index_info},
    "hs300_ret20": hs300_ret20,
    "hs300_ret60": hs300_ret60,
    "data_gaps": data_gaps,
    "market_conclusion": market_conclusion,
}
with open(OUT / "market_meta.json", "w") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
print("saved market_meta.json")

if regime == "R0":
    print("R0 - skip stock screening")
    with open(OUT / "candidates_raw.json", "w") as f:
        json.dump([], f)
    raise SystemExit(0)

# ========== 6. Candidate prefilter from spot ==========
if spot is None:
    raise SystemExit("No spot data")

df = spot.copy()
df = df[~df["名称"].str.contains("ST|退", na=False)]
df = df[df["最新价"].notna() & (df["最新价"] > 0)]

def is_limit_up(row):
    code = row["代码"]
    pct = row["涨跌幅"]
    if pd.isna(pct):
        return False
    if code.startswith(("sz300", "sz301", "sh688")):
        return pct >= 19.5
    return pct >= 9.7

df["limit_up"] = df.apply(is_limit_up, axis=1)
df = df[df["涨跌幅"] > 0]
df = df[df["成交额"] >= 3e7]
df["near_high"] = (df["最新价"] / df["最高"]).fillna(0)
df["pre_score"] = df["涨跌幅"].clip(0, 15) * 2 + np.log10(df["成交额"].clip(1e4)) * 3 + df["near_high"] * 10
non_lu = df[~df["limit_up"]].nlargest(80, "pre_score")
lu = df[df["limit_up"]].nlargest(20, "成交额")
cand = pd.concat([non_lu, lu]).drop_duplicates("代码")
print("prefilter candidates", len(cand))
cand.to_pickle(OUT / "prefilter.pkl")
cand[["代码", "名称", "最新价", "涨跌幅", "成交额", "limit_up"]].to_csv(OUT / "prefilter.csv", index=False)
print(cand.nlargest(15, "pre_score")[["代码", "名称", "最新价", "涨跌幅", "成交额"]].to_string())
print("DONE stage1")
