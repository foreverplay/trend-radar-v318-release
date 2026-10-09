"""On-demand quotes: Tencent then Eastmoney, with SQLite cache.

Historical responses are clipped to the requested date. Missing history stays
empty instead of being replaced by today's bars.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Shanghai")
CODE_RE = re.compile(r"^\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UA = {"User-Agent": "Mozilla/5.0 (compatible; trend-radar/3.1.8)"}
KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TRENDS = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
TENCENT_MINUTE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query"
TENCENT_DAY = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/day/query"
TENCENT_KLINE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
SINA_FLOW = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_lscjfb"
ULIST = "https://push2.eastmoney.com/api/qt/ulist.np/get"
THS_ANOMALY = "https://dq.10jqka.com.cn/fuyao/transaction_history/service/v1/get/{market}_{code}.txt"
HIS_UT = "7eea3edcaed734bea9cbfc24409ed989"
UT = "bd1d9ddb04089700cf9c27f6f7426281"


def cache_path() -> Path:
    raw = os.environ.get("QUOTE_CACHE_PATH")
    if raw:
        return Path(raw).expanduser()
    root = Path(__file__).resolve().parents[2]
    return root / "data" / "cache" / "quotes.sqlite3"


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def normalize_code(code: str) -> str:
    raw = str(code or "").strip()
    if raw.lower().startswith(("sh", "sz", "bj")):
        raw = raw[2:]
    raw = raw.zfill(6) if raw.isdigit() else raw
    if not CODE_RE.match(raw):
        raise ValueError("股票代码需为 6 位数字")
    return raw


def normalize_date(value: Optional[str], default: Optional[str] = None) -> str:
    raw = (value or default or today_str()).strip()
    if not DATE_RE.match(raw):
        raise ValueError("日期需为 YYYY-MM-DD")
    return raw


def secid_for(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def tencent_code(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "-", "--"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clip_bars(bars: List[Dict[str, Any]], as_of: str) -> List[Dict[str, Any]]:
    """Drop bars after as_of and recompute change from the visible series."""
    kept = [bar for bar in bars if str(bar.get("time") or "")[:10] <= as_of]
    out: List[Dict[str, Any]] = []
    prev = 0.0
    for bar in kept:
        close = to_float(bar.get("close"))
        item = dict(bar)
        item["time"] = str(bar.get("time") or "")[:10]
        item["change_pct"] = ((close / prev) - 1) * 100 if prev else to_float(bar.get("change_pct"))
        out.append(item)
        prev = close or prev
    return out


def points_for_date(points: List[Dict[str, Any]], trade_date: str) -> List[Dict[str, Any]]:
    matched = [p for p in points if str(p.get("date") or trade_date)[:10] == trade_date]
    return matched


class QuoteCache:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS quote_cache (
                  cache_key TEXT PRIMARY KEY,
                  payload TEXT NOT NULL,
                  fetched_at REAL NOT NULL,
                  expires_at REAL NOT NULL
                )
                """
            )

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                "SELECT payload, expires_at FROM quote_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
        if not row:
            return None
        expires = float(row[1])
        if expires > 0 and expires < time.time():
            return None
        try:
            data = json.loads(row[0])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, payload: Dict[str, Any], ttl: Optional[float]) -> None:
        expires = 0.0 if ttl is None else time.time() + ttl
        with sqlite3.connect(self.path) as con:
            con.execute(
                """
                INSERT INTO quote_cache(cache_key, payload, fetched_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                  payload=excluded.payload,
                  fetched_at=excluded.fetched_at,
                  expires_at=excluded.expires_at
                """,
                (key, json.dumps(payload, ensure_ascii=False), time.time(), expires),
            )


def _get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: float = 8) -> Any:
    h = dict(UA)
    if headers:
        h.update(headers)
    response = requests.get(url, params=params, headers=h, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _clock(raw: str) -> str:
    text = raw.strip()
    if " " in text:
        text = text.split(" ")[-1]
    return text[:5]


def parse_tencent_minute(payload: Any, code: str) -> Tuple[float, List[Dict[str, Any]], str]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    node = data.get(code) if isinstance(data, dict) and isinstance(data.get(code), dict) else None
    if node is None:
        return 0.0, [], ""
    quote = ((node.get("qt") or {}).get(code) or [])
    pre_close = to_float(quote[4]) if len(quote) > 4 else 0.0
    date = ""
    if len(quote) > 30:
        raw_date = str(quote[30])
        if len(raw_date) >= 8 and raw_date[:8].isdigit():
            date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    inner = node.get("data") if isinstance(node.get("data"), dict) else {}
    if not date:
        date = str(inner.get("date") or "")[:10]
    points: List[Dict[str, Any]] = []
    prev_vol = 0.0
    for line in inner.get("data") or []:
        if not isinstance(line, str):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) < 3:
            continue
        raw_clock = parts[0]
        clock = f"{raw_clock[:2]}:{raw_clock[2:4]}" if len(raw_clock) >= 4 and ":" not in raw_clock else raw_clock[:5]
        price = to_float(parts[1])
        cum_vol = to_float(parts[2])
        volume = max(0.0, cum_vol - prev_vol)
        prev_vol = cum_vol
        points.append(
            {
                "date": date,
                "time": clock,
                "price": price,
                "avg": price,
                "volume": volume,
                "change_pct": ((price / pre_close) - 1) * 100 if pre_close else 0.0,
            }
        )
    return pre_close, points, date


def parse_tencent_day_minutes(payload: Any, code: str, trade_date: str) -> Tuple[float, List[Dict[str, Any]]]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    node = data.get(code) if isinstance(data, dict) and isinstance(data.get(code), dict) else None
    rows = node.get("data") if isinstance(node, dict) else None
    if not isinstance(rows, list):
        return 0.0, []
    want = trade_date.replace("-", "")
    chosen = None
    for row in rows:
        raw = str(row.get("date") or "") if isinstance(row, dict) else ""
        if raw == want or raw[:10] == trade_date:
            chosen = row
            break
    if not isinstance(chosen, dict):
        return 0.0, []
    qt = [""] * 31
    qt[4] = chosen.get("prec")
    qt[30] = want
    wrapped = {
        "data": {
            code: {
                "qt": {code: qt},
                "data": {"date": trade_date, "data": chosen.get("data") or []},
            }
        }
    }
    pre_close, points, got = parse_tencent_minute(wrapped, code)
    if got and got != trade_date:
        return 0.0, []
    return pre_close, points_for_date(points, trade_date)


def parse_eastmoney_trends(payload: Any) -> Tuple[float, List[Dict[str, Any]], str]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return 0.0, [], ""
    pre_close = to_float(data.get("preClose") or data.get("prePrice"))
    points: List[Dict[str, Any]] = []
    date = ""
    for line in data.get("trends") or []:
        if not isinstance(line, str):
            continue
        parts = line.split(",")
        if len(parts) < 4:
            continue
        stamp = parts[0]
        if not date and len(stamp) >= 10:
            date = stamp[:10]
        if len(parts) >= 8:
            price = to_float(parts[2])
            avg = to_float(parts[7], price)
            volume = to_float(parts[5])
        else:
            price = to_float(parts[1])
            avg = to_float(parts[2], price)
            volume = to_float(parts[3])
        points.append(
            {
                "date": stamp[:10] if len(stamp) >= 10 else date,
                "time": _clock(stamp),
                "price": price,
                "avg": avg,
                "volume": volume,
                "change_pct": ((price / pre_close) - 1) * 100 if pre_close else 0.0,
            }
        )
    return pre_close, points, date


def parse_minute_klines(lines: List[str]) -> Tuple[float, List[Dict[str, Any]]]:
    rows: List[Tuple[str, str, float, float, float]] = []
    for line in lines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        stamp = parts[0]
        day = stamp[:10]
        close = to_float(parts[2])
        volume = to_float(parts[5])
        listed = to_float(parts[8]) if len(parts) > 8 else 0.0
        rows.append((day, stamp, close, volume, listed))
    if not rows:
        return 0.0, []
    last_close, last_change = rows[-1][2], rows[-1][4]
    pre_close = last_close / (1 + last_change / 100) if last_change > -99.999 and last_close else last_close
    points = []
    for day, stamp, close, volume, listed in rows:
        points.append(
            {
                "date": day,
                "time": _clock(stamp),
                "price": close,
                "avg": close,
                "volume": volume,
                "change_pct": ((close / pre_close) - 1) * 100 if pre_close else listed,
            }
        )
    return pre_close, points


def parse_ohlc_klines(lines: List[str]) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    prev = 0.0
    for line in lines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        close = to_float(parts[2])
        listed = to_float(parts[8]) if len(parts) > 8 else 0.0
        bars.append(
            {
                "time": parts[0][:10],
                "open": to_float(parts[1]),
                "close": close,
                "high": to_float(parts[3]),
                "low": to_float(parts[4]),
                "volume": to_float(parts[5]),
                "amount": to_float(parts[6]) if len(parts) > 6 else 0.0,
                "change_pct": listed if listed else (((close / prev) - 1) * 100 if prev else 0.0),
            }
        )
        prev = close or prev
    return bars


def parse_tencent_kline(payload: Any, code: str, period: str) -> List[Dict[str, Any]]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    node = data.get(code) if isinstance(data, dict) and isinstance(data.get(code), dict) else None
    if node is None:
        return []
    key = "qfqday" if period == "day" else "qfqmonth"
    rows = node.get(key) or node.get("day" if period == "day" else "month") or []
    bars: List[Dict[str, Any]] = []
    prev = 0.0
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        close = to_float(row[2])
        bars.append(
            {
                "time": str(row[0])[:10],
                "open": to_float(row[1]),
                "close": close,
                "high": to_float(row[3]),
                "low": to_float(row[4]),
                "volume": to_float(row[5]),
                "amount": to_float(row[6]) if len(row) > 6 else 0.0,
                "change_pct": ((close / prev) - 1) * 100 if prev else 0.0,
            }
        )
        prev = close or prev
    return bars


def resample_monthly(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for bar in bars:
        month = str(bar.get("time") or "")[:7]
        if len(month) < 7:
            continue
        groups.setdefault(month, []).append(bar)
    out: List[Dict[str, Any]] = []
    prev = 0.0
    for month in sorted(groups):
        rows = groups[month]
        close = to_float(rows[-1].get("close"))
        out.append(
            {
                "time": f"{month}-01",
                "open": to_float(rows[0].get("open")),
                "close": close,
                "high": max(to_float(row.get("high")) for row in rows),
                "low": min(to_float(row.get("low")) for row in rows),
                "volume": sum(to_float(row.get("volume")) for row in rows),
                "amount": sum(to_float(row.get("amount")) for row in rows),
                "change_pct": ((close / prev) - 1) * 100 if prev else 0.0,
            }
        )
        prev = close or prev
    return out


def _ttl(trade_date: str, available: bool) -> Optional[float]:
    if trade_date < today_str() and available:
        return None
    if available:
        return 60.0
    return 900.0


def _cached(cache: QuoteCache, key: str) -> Optional[Dict[str, Any]]:
    hit = cache.get(key)
    if hit is None:
        return None
    hit = dict(hit)
    hit["source"] = f"cache:{hit.get('source') or 'quote'}"
    return hit


def _hub_base() -> str:
    return os.environ.get("MARKET_HUB_URL", "").strip().rstrip("/")


def _hub_get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    base = _hub_base()
    if not base:
        return None
    try:
        response = requests.get(f"{base}{path}", params=params or {}, timeout=8)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def get_trends(code: str, trade_date: Optional[str] = None, cache: Optional[QuoteCache] = None) -> Dict[str, Any]:
    symbol = normalize_code(code)
    day = normalize_date(trade_date)
    store = cache or QuoteCache()
    key = f"trends:{symbol}:{day}"
    hit = _cached(store, key)
    if hit is not None:
        return hit
    remote = _hub_get(f"/api/quotes/{symbol}/trends", {"date": day})
    if remote is not None:
        remote.setdefault("symbol", symbol)
        remote.setdefault("tradeDate", day)
        store.put(key, remote, _ttl(day, bool(remote.get("available"))))
        return remote
    pre_close = 0.0
    points: List[Dict[str, Any]] = []
    source = ""
    tx = tencent_code(symbol)
    if day == today_str():
        try:
            pre_close, points, got = parse_tencent_minute(_get_json(TENCENT_MINUTE, {"code": tx}, {"Referer": "https://gu.qq.com/"}), tx)
            if got and got != day:
                points = []
            if points:
                source = "tencent"
        except Exception:
            points = []
        if not points:
            try:
                pre_close, points, got = parse_eastmoney_trends(
                    _get_json(
                        TRENDS,
                        {
                            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                            "ut": HIS_UT,
                            "ndays": "1",
                            "iscr": "0",
                            "secid": secid_for(symbol),
                        },
                    )
                )
                points = points_for_date(points, day)
                if got and got != day:
                    points = []
                if points:
                    source = "eastmoney"
            except Exception:
                points = []
    if not points:
        try:
            payload = _get_json(
                KLINE,
                {
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "ut": HIS_UT,
                    "klt": "1",
                    "fqt": "1",
                    "secid": secid_for(symbol),
                    "end": day.replace("-", ""),
                    "lmt": "241",
                },
            )
            lines = [item for item in ((payload.get("data") or {}).get("klines") or []) if isinstance(item, str)]
            pre_close, points = parse_minute_klines(lines)
            points = points_for_date(points, day)
            if points:
                source = "eastmoney_minute"
        except Exception:
            points = []
    # 东财 1 分钟 K 线忽略 end，lmt 只覆盖最新交易日，更早的近期交易日会被按日滤空。
    if not points and day < today_str():
        try:
            pre_close, points = parse_tencent_day_minutes(
                _get_json(TENCENT_DAY, {"code": tx}, {"Referer": "https://gu.qq.com/"}),
                tx,
                day,
            )
            if points:
                source = "tencent_day"
        except Exception:
            points = []
    result = {
        "symbol": symbol,
        "preClose": pre_close,
        "points": [{k: v for k, v in p.items() if k != "date"} | {"time": p["time"]} for p in points],
        "tradeDate": day,
        "available": bool(points),
        "source": source,
    }
    store.put(key, result, _ttl(day, bool(points)))
    return result


def _fetch_daily_bars(symbol: str) -> Tuple[List[Dict[str, Any]], str]:
    tx = tencent_code(symbol)
    try:
        payload = _get_json(
            TENCENT_KLINE,
            {"param": f"{tx},day,,,320,qfq"},
            {"Referer": "https://gu.qq.com/"},
        )
        bars = parse_tencent_kline(payload, tx, "day")
        if bars:
            return bars, "tencent"
    except Exception:
        pass
    payload = _get_json(
        KLINE,
        {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": HIS_UT,
            "klt": "101",
            "fqt": "1",
            "secid": secid_for(symbol),
            "end": "20500101",
            "lmt": "320",
        },
    )
    lines = [item for item in ((payload.get("data") or {}).get("klines") or []) if isinstance(item, str)]
    return parse_ohlc_klines(lines), "eastmoney"


def get_klines(
    code: str,
    period: str = "day",
    as_of: Optional[str] = None,
    cache: Optional[QuoteCache] = None,
) -> Dict[str, Any]:
    symbol = normalize_code(code)
    kind = "month" if period == "month" else "day"
    day = normalize_date(as_of)
    store = cache or QuoteCache()
    key = f"klines:{symbol}:{kind}:{day}"
    hit = _cached(store, key)
    if hit is not None:
        return hit
    remote = _hub_get(f"/api/quotes/{symbol}/klines", {"period": kind, "adjust": "qfq", "as_of": day})
    if remote is not None:
        remote.setdefault("symbol", symbol)
        remote.setdefault("period", kind)
        remote.setdefault("asOf", day)
        store.put(key, remote, _ttl(day, bool(remote.get("available"))))
        return remote
    bars, source = _fetch_daily_bars(symbol)
    bars = clip_bars(bars, day)
    if kind == "month":
        bars = resample_monthly(bars)
    result = {
        "symbol": symbol,
        "period": kind,
        "asOf": day,
        "bars": bars,
        "available": bool(bars),
        "source": source,
    }
    store.put(key, result, _ttl(day, bool(bars)))
    return result


def parse_sina_flow(payload: Any) -> Dict[str, Any]:
    rows = payload if isinstance(payload, list) else []
    if not rows or not isinstance(rows[0], dict):
        return {}
    row = rows[0]
    huge = to_float(row.get("r0"))
    large = to_float(row.get("r1"))
    mid = to_float(row.get("r2"))
    small = to_float(row.get("r3"))
    total = huge + large + mid + small
    if total <= 0:
        return {}
    return {
        "date": str(row.get("opendate") or "")[:10],
        "largeSharePct": round((huge + large) / total * 100, 2),
        "mainNetPct": round(to_float(row.get("ratioamount")) * 100, 2),
    }


def get_flow(code: str, trade_date: Optional[str] = None, cache: Optional[QuoteCache] = None) -> Dict[str, Any]:
    symbol = normalize_code(code)
    day = normalize_date(trade_date)
    store = cache or QuoteCache()
    key = f"flow:{symbol}:{day}"
    hit = _cached(store, key)
    if hit is not None:
        return hit
    remote = _hub_get(f"/api/quotes/{symbol}/flow", {"date": day})
    if remote is not None:
        remote.setdefault("symbol", symbol)
        store.put(key, remote, _ttl(day, bool(remote.get("available"))))
        return remote
    parsed: Dict[str, Any] = {}
    source = ""
    try:
        payload = _get_json(SINA_FLOW, {"page": "1", "num": "20", "sort": "opendate", "asc": "0", "daima": tencent_code(symbol)})
        rows = payload if isinstance(payload, list) else []
        for row in rows:
            parsed = parse_sina_flow([row] if isinstance(row, dict) else [])
            if parsed.get("date") == day:
                source = "sina"
                break
            parsed = {}
    except Exception:
        parsed = {}
    if not parsed and day == today_str():
        try:
            payload = _get_json(
                ULIST,
                {
                    "secids": secid_for(symbol),
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f12,f14,f62,f184,f66,f69,f72,f75",
                    "ut": UT,
                },
            )
            diff = ((payload.get("data") or {}).get("diff") or [])
            row = diff[0] if isinstance(diff, list) and diff else None
            if isinstance(row, dict):
                parsed = {"date": day, "largeSharePct": 0.0, "mainNetPct": to_float(row.get("f184"))}
                source = "eastmoney"
        except Exception:
            parsed = {}
    result = {
        "symbol": symbol,
        "available": bool(parsed),
        "date": parsed.get("date") or "",
        "largeSharePct": parsed.get("largeSharePct") or 0.0,
        "mainNetPct": parsed.get("mainNetPct") or 0.0,
        "source": source,
        "note": "" if parsed else "该日资金流无法还原",
    }
    store.put(key, result, _ttl(day, bool(parsed)))
    return result


def _compact_reason(name: str, items: List[str], limit: int = 80) -> str:
    preferred = [item for item in items if name and name in item] or items
    if not preferred:
        return ""
    text = preferred[0].replace(name, "").strip(" ，。:：")
    return text[:limit]


def _news_titles(name: str) -> List[str]:
    param = json.dumps(
        {
            "uid": "",
            "keyword": f"{name} 上涨",
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "timedown",
                    "pageIndex": 1,
                    "pageSize": 5,
                    "preTag": "",
                    "postTag": "",
                }
            },
        },
        ensure_ascii=False,
    )
    response = requests.get(
        "https://search-api-web.eastmoney.com/search/jsonp",
        params={"cb": "cb", "param": param},
        headers=UA,
        timeout=8,
    )
    response.raise_for_status()
    raw = response.text.strip()
    start, end = raw.find("("), raw.rfind(")")
    payload = json.loads(raw[start + 1 : end]) if start >= 0 and end > start else {}
    rows = (((payload.get("result") or {}).get("cmsArticleWebOld")) or [])
    return [str(row.get("title") or "").strip() for row in rows if isinstance(row, dict) and row.get("title")]


def _ths_reason(symbol: str) -> str:
    markets = ["17", "33"] if symbol.startswith(("6", "9")) else ["33", "17"]
    if symbol.startswith(("6", "9")):
        markets = ["17", "33"]
    headers = {"User-Agent": UA["User-Agent"], "Referer": "https://eq.10jqka.com.cn/"}
    for market in markets:
        try:
            payload = _get_json(THS_ANOMALY.format(market=market, code=symbol), headers=headers)
        except Exception:
            continue
        rows = [row for row in (((payload.get("data") or {}).get("anomalyAnalysisList")) or []) if isinstance(row, dict)]
        if not rows:
            continue
        latest = max(rows, key=lambda row: str(row.get("date") or ""))
        keywords = [str(item).strip() for item in (latest.get("keywordList") or []) if str(item).strip()]
        body = str(latest.get("reason") or "").strip()
        text = " ".join(part for part in ("+".join(keywords), body) if part)
        if text:
            return text[:120]
    return ""


def get_rise_reason(
    code: str,
    name: str = "",
    trade_date: Optional[str] = None,
    cache: Optional[QuoteCache] = None,
) -> Dict[str, Any]:
    symbol = normalize_code(code)
    day = normalize_date(trade_date)
    store = cache or QuoteCache()
    key = f"reason:{symbol}:{day}"
    hit = _cached(store, key)
    if hit is not None:
        return hit
    remote = _hub_get(f"/api/quotes/{symbol}/rise-reason", {"name": name, "date": day})
    if remote is not None:
        remote.setdefault("symbol", symbol)
        store.put(key, remote, _ttl(day, bool(remote.get("available"))))
        return remote
    if day != today_str():
        result = {
            "symbol": symbol,
            "available": False,
            "reason": "",
            "source": "",
            "note": "异动原因无法按历史日期还原",
        }
        store.put(key, result, None)
        return result
    reason = ""
    source = ""
    try:
        reason = _ths_reason(symbol)
        source = "ths" if reason else ""
    except Exception:
        reason = ""
    if not reason and name:
        try:
            reason = _compact_reason(name, _news_titles(name))
            source = "news" if reason else ""
        except Exception:
            reason = ""
    result = {
        "symbol": symbol,
        "available": bool(reason),
        "reason": reason,
        "source": source,
        "note": "" if reason else "暂无公开异动说明",
    }
    store.put(key, result, _ttl(day, bool(reason)))
    return result
