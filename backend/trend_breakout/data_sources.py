#!/usr/bin/env python3
"""
Multi-source fallback layer for A-share Trend Confirmation Breakout pipeline.

Every public helper returns:
  {"ok": bool, "value": Any, "source": str, "errors": [str, ...]}

Prefer working public sources; never invent numbers.
"""
from __future__ import annotations

import json
import time
import warnings
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

try:
    import akshare as ak
except Exception:  # pragma: no cover
    ak = None

OUT = _PKG_ROOT
CACHE = OUT / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
TURNOVER_CACHE = CACHE / "turnover_daily.json"
LOCAL_DAILY = _PKG_LOCAL_DAILY
TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2


# ---------- helpers ----------

def _ok(value: Any, source: str, errors: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"ok": True, "value": value, "source": source, "errors": list(errors or [])}


def _fail(errors: List[str], source: str = "") -> Dict[str, Any]:
    return {"ok": False, "value": None, "source": source, "errors": list(errors)}


def _retry(name: str, fn: Callable, *args, retries: int = MAX_RETRIES, sleep: float = 0.6, **kwargs):
    errs = []
    for i in range(retries + 1):
        try:
            return fn(*args, **kwargs), errs
        except Exception as e:
            errs.append(f"{name}[try{i}]: {type(e).__name__}: {e}")
            if i < retries:
                time.sleep(sleep * (i + 1))
    return None, errs


def _http_get(url: str, headers: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT) -> requests.Response:
    h = {"User-Agent": "Mozilla/5.0 (compatible; trend-breakout/1.0)"}
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=timeout)


def shanghai_now() -> datetime:
    return datetime.now(TZ)


def detect_t_date(prefer: Optional[str] = None) -> Dict[str, Any]:
    """
    Auto-detect latest closed trading day in Asia/Shanghai.
    Before 15:05 on a session day → previous closed day; after close → that day if trading.
    """
    errors: List[str] = []
    now = shanghai_now()
    today = now.strftime("%Y-%m-%d")

    trade_dates: List[str] = []
    if ak is not None:
        cal, errs = _retry("tool_trade_date_hist_sina", ak.tool_trade_date_hist_sina, retries=1)
        errors.extend(errs)
        if cal is not None and len(cal):
            trade_dates = [str(x)[:10] for x in cal["trade_date"].tolist()]

    if prefer:
        return _ok({"t_date": prefer, "t_compact": prefer.replace("-", ""), "now": now.isoformat()}, "prefer")

    if not trade_dates:
        # fallback: weekday heuristic
        d = now.date()
        if now.time() < dtime(15, 5):
            d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
        t = d.strftime("%Y-%m-%d")
        errors.append("trade calendar unavailable; weekday heuristic")
        return _ok({"t_date": t, "t_compact": t.replace("-", ""), "now": now.isoformat()}, "weekday_heuristic", errors)

    closed_cutoff = dtime(15, 5)
    if now.time() < closed_cutoff:
        eligible = [d for d in trade_dates if d < today]
    else:
        eligible = [d for d in trade_dates if d <= today]
    if not eligible:
        eligible = [d for d in trade_dates if d <= today] or trade_dates[-1:]
    t = eligible[-1]
    return _ok({"t_date": t, "t_compact": t.replace("-", ""), "now": now.isoformat()}, "trade_calendar", errors)


# ---------- A. Two-market turnover (亿元) ----------

def _parse_sse_amt(df: pd.DataFrame) -> Optional[float]:
    if df is None or not len(df):
        return None
    try:
        row = df[df["单日情况"].astype(str) == "成交金额"]
        if len(row):
            return float(row.iloc[0]["股票"])
    except Exception:
        return None
    return None


def _parse_szse_amt(df: pd.DataFrame) -> Optional[float]:
    if df is None or not len(df):
        return None
    try:
        if "成交金额" not in df.columns:
            return None
        col0 = df.iloc[:, 0].astype(str)
        # Prefer 股票 / A股 / 总计
        for key in ["股票", "A股", "总计"]:
            m = df[col0.str.contains(key, na=False)]
            if len(m):
                val = float(str(m.iloc[0]["成交金额"]).replace(",", ""))
                if val > 1e6:  # yuan
                    val = val / 1e8
                return val
        # else sum numeric?
        vals = pd.to_numeric(df["成交金额"].astype(str).str.replace(",", ""), errors="coerce")
        total = float(vals.sum())
        if total > 1e6:
            total = total / 1e8
        return total if total > 0 else None
    except Exception:
        return None


def _turnover_official(t_compact: str) -> Tuple[Optional[float], str, List[str]]:
    errors = []
    if ak is None:
        return None, "", ["akshare missing"]
    sse_df, e1 = _retry("stock_sse_deal_daily", ak.stock_sse_deal_daily, date=t_compact, retries=1)
    errors.extend(e1)
    sse_amt = _parse_sse_amt(sse_df) if sse_df is not None else None
    if sse_df is not None and sse_amt is None:
        errors.append("sse parse: no 成交金额 row")

    szse_amt = None
    for dtry in [t_compact]:
        sz, e2 = _retry("stock_szse_summary", ak.stock_szse_summary, date=dtry, retries=1)
        errors.extend(e2)
        if sz is not None:
            szse_amt = _parse_szse_amt(sz)
            if szse_amt is None:
                errors.append("szse parse failed")
            break

    if sse_amt is not None and szse_amt is not None:
        return sse_amt + szse_amt, "sse_deal_daily+szse_summary", errors
    if sse_amt is not None and szse_amt is None:
        errors.append(f"sse_only={sse_amt:.2f} incomplete without szse")
    return None, "", errors


def _em_index_kline_amts(secid: str, end: str, lmt: int = 30) -> Tuple[Optional[List[dict]], List[str]]:
    """Eastmoney index daily kline amounts via HTTP push2his (HTTPS often blocked)."""
    errors = []
    hosts = [
        "http://push2his.eastmoney.com",
        "http://push2delay.eastmoney.com",
    ]
    path = (
        f"/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=0&end={end}&lmt={lmt}"
    )
    for host in hosts:
        for attempt in range(2):
            try:
                r = _http_get(host + path, headers={"Referer": "https://quote.eastmoney.com/"}, timeout=12)
                if not r.content or r.status_code >= 400:
                    raise ValueError(f"http {r.status_code} empty={not r.content}")
                j = r.json()
                klines = (j.get("data") or {}).get("klines") or []
                if not klines:
                    raise ValueError("empty klines")
                out = []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) < 7:
                        continue
                    out.append({"date": parts[0], "amt_yi": float(parts[6]) / 1e8, "close": float(parts[2]), "volume": float(parts[5])})
                if out:
                    return out, errors
            except Exception as e:
                errors.append(f"em_kline_{secid}@{host}[{attempt}]: {type(e).__name__}: {e}")
                time.sleep(0.3 * (attempt + 1))
    return None, errors


def _turnover_em_index(t_date: str) -> Tuple[Optional[float], str, List[str], Optional[List[dict]]]:
    """Sum SH000001 + SZ399001 eastmoney kline amounts for T and recent hist."""
    end = t_date.replace("-", "")
    sh, e1 = _em_index_kline_amts("1.000001", end, 30)
    sz, e2 = _em_index_kline_amts("0.399001", end, 30)
    errors = e1 + e2
    if not sh or not sz:
        return None, "", errors, None
    sh_map = {r["date"]: r["amt_yi"] for r in sh}
    sz_map = {r["date"]: r["amt_yi"] for r in sz}
    common = sorted(set(sh_map) & set(sz_map))
    series = [{"date": d.replace("-", ""), "amt": sh_map[d] + sz_map[d], "date_dash": d} for d in common]
    # T value
    t_amt = None
    for r in series:
        if r["date_dash"] == t_date or r["date"] == end:
            t_amt = r["amt"]
    if t_amt is None and series:
        # last available ≤ T
        for r in reversed(series):
            if r["date_dash"] <= t_date:
                t_amt = r["amt"]
                break
    if t_amt is None:
        return None, "", errors + ["em_index no T amount"], None
    return t_amt, "em_index_kline_sh000001+sz399001", errors, series


def _turnover_sina_tencent_hq() -> Tuple[Optional[float], str, List[str]]:
    errors = []
    # sina first
    try:
        r = _http_get(
            "https://hq.sinajs.cn/list=sh000001,sz399001",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=12,
        )
        r.encoding = "gbk"
        amts = {}
        for line in r.text.splitlines():
            if '="' not in line:
                continue
            left, right = line.split('="', 1)
            code = left.split("_")[-1]
            parts = right.rstrip('";').split(",")
            if len(parts) > 9 and parts[9]:
                amts[code] = float(parts[9]) / 1e8
        if "sh000001" in amts and "sz399001" in amts:
            return amts["sh000001"] + amts["sz399001"], "sina_hq_index_amount", errors
        errors.append(f"sina_hq incomplete {amts}")
    except Exception as e:
        errors.append(f"sina_hq: {type(e).__name__}: {e}")

    # tencent
    try:
        r = _http_get("https://qt.gtimg.cn/q=sh000001,sz399001", timeout=12)
        r.encoding = "gbk"
        total = 0.0
        got = 0
        for chunk in r.text.split(";"):
            if "~" not in chunk:
                continue
            # amount often in field 37 (万元) or embedded in field 35 as close/vol/amount
            parts = chunk.split("~")
            if len(parts) < 38:
                continue
            # field 37 is 成交额(万)
            try:
                yi = float(parts[37]) / 100.0  # 万 → 亿
                if yi > 0:
                    total += yi
                    got += 1
            except Exception:
                pass
        if got >= 2 and total > 0:
            return total, "tencent_qt_index_amount", errors
        errors.append(f"tencent incomplete got={got} total={total}")
    except Exception as e:
        errors.append(f"tencent: {type(e).__name__}: {e}")
    return None, "", errors


def _spot_sum_amt_yi(spot: Optional[pd.DataFrame]) -> Optional[float]:
    if spot is None or not len(spot) or "成交额" not in spot.columns:
        return None
    df = spot.copy()
    if "代码" in df.columns:
        code = df["代码"].astype(str)
        df = df[code.str.startswith(("sh", "sz", "SH", "SZ", "60", "00", "30", "68"))]
        # exclude BJ if coded as bj
        df = df[~code.str.startswith(("bj", "BJ", "92"))]
    s = pd.to_numeric(df["成交额"], errors="coerce").sum()
    if not np.isfinite(s) or s <= 0:
        return None
    # spot 成交额 usually 元
    return float(s) / 1e8 if s > 1e6 else float(s)


def fetch_turnover_t(t_date: str, spot: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Two-market turnover in 亿元 for T-day. Try-chain; never invent."""
    t_compact = t_date.replace("-", "")
    errors: List[str] = []

    amt, src, e = _turnover_official(t_compact)
    errors.extend(e)
    if amt is not None and amt > 0:
        return _ok({"amt_yi": float(amt), "unit": "亿元"}, src, errors)

    amt, src, e, series = _turnover_em_index(t_date)
    errors.extend(e)
    if amt is not None and amt > 0:
        return _ok({"amt_yi": float(amt), "unit": "亿元", "hist_series": series}, src, errors)

    amt, src, e = _turnover_sina_tencent_hq()
    errors.extend(e)
    if amt is not None and amt > 0:
        return _ok({"amt_yi": float(amt), "unit": "亿元"}, src, errors)

    # spot sums
    if spot is None:
        sp = fetch_spot_universe()
        errors.extend(sp.get("errors") or [])
        spot = sp.get("value") if sp.get("ok") else None
        spot_src = sp.get("source")
    else:
        spot_src = "provided_spot"

    amt = _spot_sum_amt_yi(spot)
    if amt is not None and amt > 0:
        return _ok({"amt_yi": float(amt), "unit": "亿元"}, f"spot_sum:{spot_src}", errors)

    return _fail(errors or ["all turnover sources failed"])


# ---------- B. Turnover history MA5/MA20 ----------

def _load_turnover_cache() -> Dict[str, float]:
    if not TURNOVER_CACHE.exists():
        return {}
    try:
        raw = json.loads(TURNOVER_CACHE.read_text())
        return {str(k): float(v) for k, v in (raw.get("daily") or raw).items()}
    except Exception:
        return {}


def _save_turnover_cache(daily: Dict[str, float]) -> None:
    payload = {
        "updated_at": shanghai_now().isoformat(),
        "unit": "亿元",
        "daily": {k: round(v, 4) for k, v in sorted(daily.items())},
    }
    TURNOVER_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def fetch_turnover_ma(t_date: str, amt_t: Optional[float] = None, hist_series: Optional[List[dict]] = None) -> Dict[str, Any]:
    """
    Build MA5/MA20 from persisted cache + today's value + EM index hist when available.
    Does NOT rely on broken SSE daily * scale 2.2.
    """
    errors: List[str] = []
    t_compact = t_date.replace("-", "")
    daily = _load_turnover_cache()
    source_parts = ["cache"]

    # merge EM series if provided or fetch
    series = hist_series
    if series is None:
        _, _, e, series = _turnover_em_index(t_date)
        errors.extend(e)
    if series:
        for r in series:
            d = r.get("date") or r.get("date_dash", "").replace("-", "")
            if d and r.get("amt"):
                daily[d] = float(r["amt"])
        source_parts.append("em_index_kline")

    if amt_t is not None and amt_t > 0:
        daily[t_compact] = float(amt_t)

    if not daily:
        return _fail(errors + ["no turnover history points"])

    _save_turnover_cache(daily)

    # series ≤ T
    points = sorted((d, a) for d, a in daily.items() if d <= t_compact)
    if not points:
        return _fail(errors + ["no points ≤ T"])

    amts = [a for _, a in points]
    # ensure T present
    if points[-1][0] != t_compact and amt_t:
        points.append((t_compact, float(amt_t)))
        amts = [a for _, a in points]

    amt_T = float(points[-1][1])
    n = len(points)
    ma5 = float(np.mean(amts[-5:])) if n >= 1 else amt_T
    ma20 = float(np.mean(amts[-20:])) if n >= 1 else amt_T
    gap = []
    if n < 5:
        gap.append(f"MA5 thin n={n}")
    if n < 20:
        gap.append(f"MA20 thin n={n}")

    value = {
        "amt_yi": amt_T,
        "ma5": ma5,
        "ma20": ma20,
        "vs_ma5": (amt_T / ma5) if ma5 else None,
        "vs_ma20": (amt_T / ma20) if ma20 else None,
        "hist_len": n,
        "gap_flags": gap,
        "points_tail": [{"date": d, "amt": round(a, 2)} for d, a in points[-5:]],
    }
    return _ok(value, "+".join(source_parts), errors)


# ---------- C. Limit-up / limit-down ----------

def _count_limit_from_spot(spot: pd.DataFrame) -> Tuple[int, int]:
    """Approx limit-up / limit-down from spot 涨跌幅."""
    if spot is None or not len(spot):
        return 0, 0
    df = spot.copy()
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    code = df["代码"].astype(str)
    # normalize to 6-digit-ish
    codes = df["代码"].astype(str)
    names = df["名称"].astype(str) if "名称" in df.columns else pd.Series([""] * len(df), index=df.index)
    zt = 0
    dt = 0
    for idx in df.index:
        pct = df.at[idx, "涨跌幅"]
        if not np.isfinite(pct):
            continue
        c = codes.at[idx].lower()
        name = names.at[idx] if idx in names.index else ""
        if c.startswith(("sz300", "sz301", "sh688")) or c.startswith(("300", "301", "688")):
            thr = 19.8
        elif "ST" in name.upper():
            thr = 4.8
        else:
            thr = 9.8
        if pct >= thr:
            zt += 1
        elif pct <= -thr:
            dt += 1
    return zt, dt


def fetch_breadth_zt_dt(t_date: str, spot: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """
    Breadth + zt/dt counts with multi-source max.
    Prefer max(legu, pool, spot-estimate) with source tags.
    """
    t_compact = t_date.replace("-", "")
    errors: List[str] = []
    sources = {}

    up = down = zt = dt = real_zt = real_dt = None

    # legu
    if ak is not None:
        act, e = _retry("stock_market_activity_legu", ak.stock_market_activity_legu, retries=1)
        errors.extend(e)
        if act is not None and len(act):
            breadth = {str(r["item"]): r["value"] for _, r in act.iterrows()}
            up = float(breadth.get("上涨", 0) or 0)
            down = float(breadth.get("下跌", 0) or 0)
            zt = float(breadth.get("涨停", breadth.get("真实涨停", 0)) or 0)
            dt = float(breadth.get("跌停", breadth.get("真实跌停", 0)) or 0)
            real_zt = float(breadth.get("真实涨停", zt) or 0)
            real_dt = float(breadth.get("真实跌停", dt) or 0)
            sources["legu"] = {"up": up, "down": down, "zt": real_zt, "dt": real_dt, "zt_raw": zt, "dt_raw": dt}

    # zt pool
    zt_pool_n = None
    if ak is not None:
        zdf, e = _retry("stock_zt_pool_em", ak.stock_zt_pool_em, date=t_compact, retries=1)
        errors.extend(e)
        if isinstance(zdf, pd.DataFrame) and len(zdf):
            zt_pool_n = len(zdf)
            sources["zt_pool_em"] = zt_pool_n

    # dt pool — try ak then direct HTTP
    dt_pool_n = None
    if ak is not None:
        ddf, e = _retry("stock_zt_pool_dtgc_em", ak.stock_zt_pool_dtgc_em, date=t_compact, retries=1, sleep=0.4)
        errors.extend(e)
        if isinstance(ddf, pd.DataFrame):
            # empty df with no cols may mean 0
            if len(ddf) == 0 and (len(ddf.columns) == 0 or "代码" in ddf.columns or True):
                dt_pool_n = 0 if (len(ddf.columns) == 0 or len(ddf) == 0) else len(ddf)
            else:
                dt_pool_n = len(ddf)
            sources["dt_pool_em"] = dt_pool_n

    if dt_pool_n is None:
        try:
            url = (
                "https://push2ex.eastmoney.com/getTopicDTPool"
                "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
                f"&Pageindex=0&pagesize=10000&sort=fund%3Aasc&date={t_compact}"
            )
            r = _http_get(url, headers={"Referer": "https://quote.eastmoney.com/"}, timeout=12)
            j = r.json()
            data = j.get("data") or {}
            if "tc" in data:
                dt_pool_n = int(data["tc"])
                sources["dt_pool_http"] = dt_pool_n
            elif data.get("pool") is not None:
                dt_pool_n = len(data["pool"])
                sources["dt_pool_http"] = dt_pool_n
        except Exception as e:
            errors.append(f"dt_pool_http: {type(e).__name__}: {e}")

    # spot estimate
    spot_zt = spot_dt = None
    if spot is None:
        sp = fetch_spot_universe()
        errors.extend(sp.get("errors") or [])
        if sp.get("ok"):
            spot = sp["value"]
    if spot is not None:
        spot_zt, spot_dt = _count_limit_from_spot(spot)
        sources["spot_estimate"] = {"zt": spot_zt, "dt": spot_dt}

    # combine
    zt_cands = []
    dt_cands = []
    if real_zt is not None:
        zt_cands.append(("legu_real_zt", int(real_zt)))
    if zt_pool_n is not None:
        zt_cands.append(("zt_pool", int(zt_pool_n)))
    if spot_zt is not None:
        zt_cands.append(("spot_est", int(spot_zt)))

    if real_dt is not None:
        dt_cands.append(("legu_real_dt", int(real_dt)))
    if dt_pool_n is not None:
        dt_cands.append(("dt_pool", int(dt_pool_n)))
    if spot_dt is not None:
        dt_cands.append(("spot_est", int(spot_dt)))

    # Prefer max for zt; for dt also prefer max so SSL fail doesn't silently leave 0 without trying spot
    if zt_cands:
        zt_src, zt_final = max(zt_cands, key=lambda x: x[1])
    else:
        zt_src, zt_final = "none", 0
        errors.append("no zt source")

    if dt_cands:
        dt_src, dt_final = max(dt_cands, key=lambda x: x[1])
    else:
        dt_src, dt_final = "none", 0
        errors.append("no dt source")

    # breadth from legu or spot
    if up is None and spot is not None and "涨跌幅" in spot.columns:
        pct = pd.to_numeric(spot["涨跌幅"], errors="coerce")
        up = float((pct > 0).sum())
        down = float((pct < 0).sum())
        sources["breadth_from_spot"] = True

    value = {
        "up": int(up or 0),
        "down": int(down or 0),
        "zt": int(zt_final),
        "dt": int(dt_final),
        "zt_source": zt_src,
        "dt_source": dt_src,
        "sources": sources,
        "real_zt": int(real_zt) if real_zt is not None else int(zt_final),
        "real_dt": int(real_dt) if real_dt is not None else int(dt_final),
    }
    # up/down required for ok if we have something
    ok = (up is not None) or bool(zt_cands) or bool(dt_cands)
    if not ok:
        return _fail(errors)
    return _ok(value, f"zt:{zt_src}|dt:{dt_src}", errors)


# ---------- D. Spot universe ----------

SPOT_SCHEMA = ["代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "最高", "最低", "今开", "昨收"]


def _normalize_spot(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df.copy()
    # eastmoney uses 代码 as 6-digit; sina as sh/sz
    if source.endswith("_em") or "eastmoney" in source:
        # map columns
        rename = {}
        for a, b in [("代码", "代码"), ("名称", "名称"), ("最新价", "最新价"), ("涨跌幅", "涨跌幅"),
                     ("成交量", "成交量"), ("成交额", "成交额"), ("最高", "最高"), ("最低", "最低"),
                     ("今开", "今开"), ("昨收", "昨收")]:
            if a in df.columns:
                rename[a] = b
        df = df.rename(columns=rename)
        if "代码" in df.columns:
            def to_full(c):
                c = str(c).zfill(6)
                if c.startswith(("5", "6", "9")):
                    return "sh" + c
                return "sz" + c
            df["代码"] = df["代码"].map(to_full)
    # keep A-share sh/sz
    if "代码" in df.columns:
        df = df[df["代码"].astype(str).str.startswith(("sh", "sz"))].copy()
        df = df[~df["代码"].astype(str).str.startswith(("sh900", "sz200"))]  # B shares rough
    for c in ["最新价", "涨跌幅", "成交量", "成交额", "最高", "最低", "今开", "昨收"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    cols = [c for c in SPOT_SCHEMA if c in df.columns]
    extra = [c for c in df.columns if c not in cols]
    return df[cols + extra]


def fetch_spot_universe() -> Dict[str, Any]:
    errors: List[str] = []
    if ak is None:
        return _fail(["akshare missing"])

    # eastmoney first
    df, e = _retry("stock_zh_a_spot_em", ak.stock_zh_a_spot_em, retries=1, sleep=1.0)
    errors.extend(e)
    if df is not None and len(df):
        out = _normalize_spot(df, "stock_zh_a_spot_em")
        return _ok(out, "stock_zh_a_spot_em", errors)

    df, e = _retry("stock_zh_a_spot", ak.stock_zh_a_spot, retries=1, sleep=1.0)
    errors.extend(e)
    if df is not None and len(df):
        out = _normalize_spot(df, "stock_zh_a_spot")
        return _ok(out, "stock_zh_a_spot", errors)

    return _fail(errors or ["spot failed"])


# ---------- E. Stock / index daily history ----------

def _normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # unify columns
    colmap = {
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
    }
    df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
    if "turnover" in df.columns and "amount" not in df.columns:
        # tencent has both turnover(换手) and amount
        pass
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    cols = [c for c in ["date", "open", "high", "low", "close", "volume", "amount"] if c in df.columns]
    return df[cols].sort_values("date").reset_index(drop=True)


def fetch_stock_hist(symbol: str, start: str = "20250101", end: Optional[str] = None) -> Dict[str, Any]:
    """
    symbol: sh600000 / sz000001 / 6-digit.
    Chain: local parquet → eastmoney hist → sina daily → tencent.
    """
    errors: List[str] = []
    full = symbol
    if len(symbol) == 6:
        full = ("sh" if symbol.startswith(("5", "6", "9")) else "sz") + symbol
    code6 = full[2:]
    end = end or shanghai_now().strftime("%Y%m%d")
    end_dash = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

    # local parquet
    try:
        lp = LOCAL_DAILY / f"{code6}.parquet"
        if lp.exists():
            df = pd.read_parquet(lp)
            if df is not None and len(df):
                df = _normalize_hist(df)
                df = df[(df["date"] >= "2025-01-01") & (df["date"] <= end_dash)]
                if len(df) >= 70:
                    return _ok(df, "local_parquet", errors)
                errors.append(f"local_parquet thin n={len(df)}")
    except Exception as e:
        errors.append(f"local_parquet: {type(e).__name__}: {e}")

    if ak is None:
        return _fail(errors + ["akshare missing"])

    # eastmoney
    try:
        df = ak.stock_zh_a_hist(symbol=code6, period="daily", start_date=start, end_date=end, adjust="qfq")
        if df is not None and len(df):
            return _ok(_normalize_hist(df), "stock_zh_a_hist", errors)
    except Exception as e:
        errors.append(f"stock_zh_a_hist: {type(e).__name__}: {e}")

    # sina
    df, e = _retry(
        "stock_zh_a_daily",
        ak.stock_zh_a_daily,
        symbol=full,
        start_date=start,
        end_date=end,
        adjust="qfq",
        retries=2,
        sleep=0.3,
    )
    errors.extend(e)
    if df is not None and len(df):
        return _ok(_normalize_hist(df), "stock_zh_a_daily", errors)

    # tencent
    try:
        df = ak.stock_zh_a_hist_tx(symbol=full, start_date=start, end_date=end, adjust="qfq", timeout=15)
        if df is not None and len(df):
            return _ok(_normalize_hist(df), "stock_zh_a_hist_tx", errors)
    except Exception as e:
        errors.append(f"stock_zh_a_hist_tx: {type(e).__name__}: {e}")

    return _fail(errors or ["hist failed"])


def append_t_bar_from_spot(hist: pd.DataFrame, t_date: str, spot_row) -> pd.DataFrame:
    """Append T OHLC/Vol/Amt from spot when hist lags."""
    hist = hist.copy()
    hist["date"] = pd.to_datetime(hist["date"])
    last = hist["date"].iloc[-1].strftime("%Y-%m-%d")
    if last >= t_date:
        return hist
    try:
        t_open = float(spot_row["今开"] if isinstance(spot_row, dict) else spot_row.今开)
        t_high = float(spot_row["最高"] if isinstance(spot_row, dict) else spot_row.最高)
        t_low = float(spot_row["最低"] if isinstance(spot_row, dict) else spot_row.最低)
        t_close = float(spot_row["最新价"] if isinstance(spot_row, dict) else spot_row.最新价)
        t_vol = float(spot_row["成交量"] if isinstance(spot_row, dict) else spot_row.成交量)
        t_amt = float(spot_row["成交额"] if isinstance(spot_row, dict) else spot_row.成交额)
        if all(np.isfinite(x) and x > 0 for x in [t_close, t_high, t_vol, t_amt]):
            hist = pd.concat([
                hist,
                pd.DataFrame([{
                    "date": pd.Timestamp(t_date),
                    "open": t_open, "high": t_high, "low": t_low, "close": t_close,
                    "volume": t_vol, "amount": t_amt,
                }]),
            ], ignore_index=True)
    except Exception:
        pass
    return hist


def fetch_index_hist(symbol: str = "sh000300") -> Dict[str, Any]:
    errors: List[str] = []
    if ak is None:
        return _fail(["akshare missing"])
    df, e = _retry("stock_zh_index_daily", ak.stock_zh_index_daily, symbol=symbol, retries=1)
    errors.extend(e)
    if df is not None and len(df):
        return _ok(_normalize_hist(df), "stock_zh_index_daily", errors)

    # EM kline backup for common indices
    secids = {"sh000001": "1.000001", "sh000300": "1.000300", "sh000852": "1.000852", "sz399006": "0.399006"}
    secid = secids.get(symbol)
    if secid:
        rows, e2 = _em_index_kline_amts(secid, shanghai_now().strftime("%Y%m%d"), 120)
        errors.extend(e2)
        if rows:
            df = pd.DataFrame([{"date": r["date"], "close": r["close"], "volume": r["volume"],
                                "open": np.nan, "high": np.nan, "low": np.nan, "amount": r["amt_yi"] * 1e8} for r in rows])
            return _ok(_normalize_hist(df), "em_index_kline", errors)
    return _fail(errors)


def fetch_sina_index_spot(codes: List[str]) -> Dict[str, Any]:
    errors: List[str] = []
    try:
        url = "https://hq.sinajs.cn/list=" + ",".join(codes)
        r = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=20)
        r.encoding = "gbk"
        rows = []
        for line in r.text.splitlines():
            if '="' not in line:
                continue
            left, right = line.split('="', 1)
            code = left.split("_")[-1]
            parts = right.rstrip('";').split(",")
            if len(parts) < 4:
                continue
            name = parts[0]
            open_ = float(parts[1]) if parts[1] else np.nan
            preclose = float(parts[2]) if parts[2] else np.nan
            price = float(parts[3]) if parts[3] else np.nan
            high = float(parts[4]) if parts[4] else np.nan
            low = float(parts[5]) if parts[5] else np.nan
            pct = (price / preclose - 1) * 100 if preclose else np.nan
            rows.append({"代码": code, "名称": name, "最新价": price, "涨跌幅": pct,
                         "昨收": preclose, "今开": open_, "最高": high, "最低": low})
        if rows:
            return _ok(pd.DataFrame(rows), "sina_hq_index_spot", errors)
        errors.append("sina_hq empty")
    except Exception as e:
        errors.append(f"sina_hq_index_spot: {type(e).__name__}: {e}")
    if ak is not None:
        df, e = _retry("stock_zh_index_spot_sina", ak.stock_zh_index_spot_sina, retries=0)
        errors.extend(e)
        if df is not None and len(df):
            return _ok(df, "stock_zh_index_spot_sina", errors)
    return _fail(errors)


# ---------- F. Industry ----------

def fetch_industry(code6: str) -> Dict[str, Any]:
    """Try EM individual info → CompanySurveyAjax → optional boards; else 未知."""
    errors: List[str] = []
    code6 = str(code6).zfill(6)

    if ak is not None:
        try:
            info = ak.stock_individual_info_em(symbol=code6)
            if info is not None and len(info):
                m = info[info["item"].astype(str).str.contains("行业")]
                if len(m):
                    return _ok(str(m.iloc[0]["value"]), "stock_individual_info_em", errors)
            errors.append("stock_individual_info_em: no 行业 field")
        except Exception as e:
            errors.append(f"stock_individual_info_em: {type(e).__name__}: {e}")

    # CompanySurveyAjax
    for mkt in ("SZ", "SH"):
        if code6.startswith(("5", "6", "9")) and mkt != "SH":
            continue
        if code6.startswith(("0", "3")) and mkt != "SZ":
            continue
        try:
            url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?code={mkt}{code6}"
            r = _http_get(url, headers={"Referer": "https://emweb.securities.eastmoney.com/"}, timeout=12)
            j = r.json()
            jb = j.get("jbzl") or {}
            ind = jb.get("sshy") or jb.get("sszjhhy")
            if ind and str(ind) not in ("--", "None", ""):
                return _ok(str(ind), "em_company_survey_ajax", errors)
        except Exception as e:
            errors.append(f"company_survey_{mkt}: {type(e).__name__}: {e}")

    # try both markets if prefix ambiguous
    for mkt in ("SZ", "SH"):
        try:
            url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?code={mkt}{code6}"
            r = _http_get(url, headers={"Referer": "https://emweb.securities.eastmoney.com/"}, timeout=10)
            j = r.json()
            jb = j.get("jbzl") or {}
            ind = jb.get("sshy") or jb.get("sszjhhy")
            if ind and str(ind) not in ("--", "None", ""):
                return _ok(str(ind), f"em_company_survey_ajax:{mkt}", errors)
        except Exception as e:
            errors.append(f"company_survey_any_{mkt}: {type(e).__name__}: {e}")

    return _ok("未知", "fallback_unknown", errors + ["行业信息不足"])


__all__ = [
    "detect_t_date",
    "fetch_turnover_t",
    "fetch_turnover_ma",
    "fetch_breadth_zt_dt",
    "fetch_spot_universe",
    "fetch_stock_hist",
    "fetch_index_hist",
    "fetch_sina_index_spot",
    "fetch_industry",
    "append_t_bar_from_spot",
    "OUT",
    "CACHE",
]
