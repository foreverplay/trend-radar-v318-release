#!/usr/bin/env python3
"""Low-frequency intraday near-breakout watcher.

ONE batch quote per run for ALL pool codes (compress pull).
  1) Sina HQ — prefer single list= for whole pool (≤80)
  2) Tencent QT — only if Sina insufficient
  3) Eastmoney push2 ulist last resort
Inter-chunk sleep from cache/rate_limit_notes.json (default 0.05–0.2s).

Session gate (Asia/Shanghai): weekdays 09:30–11:30 and 13:00–15:00.
Outside session → quiet exit unless --force.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

OUT = _PKG_ROOT
CACHE = OUT / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
POOL_PATH = OUT / "near_breakout_pool.json"
SNAP_PATH = OUT / "intraday_snapshot.json"
ALERT_PATH = OUT / "intraday_alert.md"
STATUS_PATH = OUT / "intraday_status.json"
LAST_QUOTES = CACHE / "intraday_last.json"
SMOKE_PATH = OUT / "intraday_smoke.json"

TZ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (compatible; trend-breakout-intraday/1.0)"
CHUNK = 80
# Default inter-chunk sleep; overridden by cache/rate_limit_notes.json when present
SLEEP_LO, SLEEP_HI = 0.05, 0.2
RATE_NOTES = CACHE / "rate_limit_notes.json"


def _load_inter_chunk_sleep() -> tuple:
    """Return (lo, hi) seconds between chunks from gentle probe notes."""
    lo, hi = SLEEP_LO, SLEEP_HI
    if RATE_NOTES.exists():
        try:
            notes = json.loads(RATE_NOTES.read_text(encoding="utf-8"))
            rec = notes.get("recommended") or {}
            m = rec.get("min_inter_chunk_s")
            if isinstance(m, (int, float)) and m >= 0:
                # small jitter above recommended floor
                lo = float(m)
                hi = max(lo, min(0.2, lo + 0.05))
            cs = rec.get("chunk_size")
            global CHUNK
            if isinstance(cs, int) and 10 <= cs <= 100:
                CHUNK = cs
        except Exception:
            pass
    return lo, hi

# Actionable tags
TAG_BREAKING = "突破确认中"
TAG_VOLUME = "已突破放量嫌疑"
TAG_FAIL = "跌破失效"
TAG_GAP = "高开追涨风险"
TAG_NEAR = "接近突破中"
TAG_HOLD = "守位观察"
TAG_WEAK = "弱势低开"


def shanghai_now() -> datetime:
    return datetime.now(TZ)


def is_trading_session(now: Optional[datetime] = None) -> Tuple[bool, str]:
    now = now or shanghai_now()
    if now.weekday() >= 5:
        return False, f"weekend {now.strftime('%Y-%m-%d %H:%M:%S%z')}"
    t = now.time()
    am = dtime(9, 30) <= t <= dtime(11, 30)
    pm = dtime(13, 0) <= t <= dtime(15, 0)
    if am or pm:
        return True, f"in_session {now.strftime('%Y-%m-%d %H:%M:%S%z')}"
    return False, f"outside_session {now.strftime('%Y-%m-%d %H:%M:%S%z')}"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def to_full_code(item: Dict[str, Any]) -> str:
    if item.get("code") and str(item["code"])[:2] in ("sh", "sz"):
        return str(item["code"])
    c = str(item.get("code6") or "").zfill(6)
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    return "sz" + c


def _chunks(xs: List[str], n: int) -> List[List[str]]:
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def _parse_sina_body(text: str, out: Dict[str, Dict]) -> None:
    for line in text.splitlines():
        if '="' not in line:
            continue
        left, right = line.split('="', 1)
        code = left.split("_")[-1]
        parts = right.rstrip('";').split(",")
        if len(parts) < 10 or not parts[3]:
            continue
        try:
            name = parts[0]
            open_ = float(parts[1]) if parts[1] else None
            preclose = float(parts[2]) if parts[2] else None
            price = float(parts[3]) if parts[3] else None
            high = float(parts[4]) if parts[4] else None
            low = float(parts[5]) if parts[5] else None
            vol = float(parts[8]) if parts[8] else None  # shares
            amount = float(parts[9]) if parts[9] else None  # yuan
        except ValueError:
            continue
        if price is None or price <= 0:
            continue
        pct = None
        if preclose and preclose > 0 and price:
            pct = (price / preclose - 1) * 100
        out[code] = {
            "code": code,
            "name": name,
            "price": price,
            "open": open_,
            "preclose": preclose,
            "high": high,
            "low": low,
            "volume": vol,
            "amount": amount,
            "pct": pct,
            "source": "sina_hq",
        }


def fetch_sina_batch(codes: List[str]) -> Tuple[Dict[str, Dict], List[str], float]:
    """Sina HQ: prefer ONE list= for whole pool; chunk only if needed."""
    out: Dict[str, Dict] = {}
    errors: List[str] = []
    t0 = time.time()
    sleep_lo, sleep_hi = _load_inter_chunk_sleep()
    # Prefer single request when pool fits (typical ≤80)
    chunk_size = max(CHUNK, len(codes)) if len(codes) <= 80 else CHUNK
    chunks = _chunks(codes, chunk_size)
    for i, chunk in enumerate(chunks):
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"},
                timeout=12,
            )
            if r.status_code in (403, 429):
                errors.append(f"sina_http_{r.status_code}")
                time.sleep(min(2.0, 0.5 * (2 ** i)))
                continue
            r.encoding = "gbk"
            if not (r.text or "").strip():
                errors.append("sina_empty")
                time.sleep(min(2.0, 0.5 * (2 ** i)))
                continue
            _parse_sina_body(r.text, out)
        except Exception as e:
            errors.append(f"sina_chunk: {type(e).__name__}: {e}")
            time.sleep(min(2.0, 0.5 * (2 ** i)))
            continue
        if i + 1 < len(chunks):
            time.sleep(random.uniform(sleep_lo, sleep_hi))
    return out, errors, time.time() - t0


def fetch_tencent_batch(codes: List[str]) -> Tuple[Dict[str, Dict], List[str], float]:
    """Tencent QT: only used when Sina fails / insufficient. Prefer single batch."""
    out: Dict[str, Dict] = {}
    errors: List[str] = []
    t0 = time.time()
    sleep_lo, sleep_hi = _load_inter_chunk_sleep()
    chunk_size = max(CHUNK, len(codes)) if len(codes) <= 80 else CHUNK
    chunks = _chunks(codes, chunk_size)
    for i, chunk in enumerate(chunks):
        url = "http://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            if r.status_code in (403, 429):
                errors.append(f"tencent_http_{r.status_code}")
                time.sleep(min(2.0, 0.5 * (2 ** i)))
                continue
            r.encoding = "gbk"
            if not (r.text or "").strip():
                errors.append("tencent_empty")
                time.sleep(min(2.0, 0.5 * (2 ** i)))
                continue
            for seg in r.text.split(";"):
                if "~" not in seg:
                    continue
                try:
                    body = seg.split("=", 1)[1].strip().strip('"')
                except IndexError:
                    continue
                parts = body.split("~")
                if len(parts) < 10:
                    continue
                try:
                    name = parts[1]
                    code6 = parts[2].zfill(6)
                    price = float(parts[3]) if parts[3] else None
                    preclose = float(parts[4]) if parts[4] else None
                    open_ = float(parts[5]) if parts[5] else None
                    left = seg.split("=")[0]
                    full = left.split("_")[-1] if "_" in left else None
                    if not full:
                        full = ("sh" if code6.startswith(("5", "6", "9")) else "sz") + code6
                    vol = float(parts[6]) if len(parts) > 6 and parts[6] else None
                    amount = None
                    if len(parts) > 37 and parts[37]:
                        amount = float(parts[37]) * 10000
                    pct = float(parts[32]) if len(parts) > 32 and parts[32] else None
                    if pct is None and preclose and price and preclose > 0:
                        pct = (price / preclose - 1) * 100
                    high = float(parts[33]) if len(parts) > 33 and parts[33] else None
                    low = float(parts[34]) if len(parts) > 34 and parts[34] else None
                except ValueError:
                    continue
                if not price or price <= 0:
                    continue
                out[full] = {
                    "code": full,
                    "name": name,
                    "price": price,
                    "open": open_,
                    "preclose": preclose,
                    "high": high,
                    "low": low,
                    "volume": vol,
                    "amount": amount,
                    "pct": pct,
                    "source": "tencent_qt",
                }
        except Exception as e:
            errors.append(f"tencent_chunk: {type(e).__name__}: {e}")
            time.sleep(min(2.0, 0.5 * (2 ** i)))
            continue
        if i + 1 < len(chunks):
            time.sleep(random.uniform(sleep_lo, sleep_hi))
    return out, errors, time.time() - t0


def fetch_em_clist_batch(codes: List[str]) -> Tuple[Dict[str, Dict], List[str], float]:
    """Eastmoney push2 clist — last resort, longer timeout, rarely."""
    out: Dict[str, Dict] = {}
    errors: List[str] = []
    t0 = time.time()
    # Map to secids
    # Use individual quotes in ONE request via uts if possible — clist needs fs filter.
    # Fallback: small batch via push2 getStockList or quote API
    # Use https://push2.eastmoney.com/api/qt/ulist.np/get?secids=0.301072,1.600000&fields=...
    for chunk in _chunks(codes, 40):  # smaller chunks for EM
        secids = []
        for c in chunk:
            code6 = c[2:]
            mkt = "1" if c.startswith("sh") else "0"
            secids.append(f"{mkt}.{code6}")
        url = (
            "https://push2.eastmoney.com/api/qt/ulist.np/get"
            f"?fltt=2&secids={','.join(secids)}"
            "&fields=f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18"
        )
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
                timeout=20,
            )
            data = r.json()
            diff = (data.get("data") or {}).get("diff") or []
            for row in diff:
                code6 = str(row.get("f12") or "").zfill(6)
                full = ("sh" if code6.startswith(("5", "6", "9")) else "sz") + code6
                price = row.get("f2")
                if price is None or price == "-":
                    continue
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                def _f(k):
                    v = row.get(k)
                    try:
                        return float(v) if v not in (None, "-") else None
                    except (TypeError, ValueError):
                        return None
                out[full] = {
                    "code": full,
                    "name": row.get("f14") or "",
                    "price": price,
                    "open": _f("f17"),
                    "preclose": _f("f18"),
                    "high": _f("f15"),
                    "low": _f("f16"),
                    "volume": _f("f5"),
                    "amount": _f("f6"),
                    "pct": _f("f3"),
                    "source": "eastmoney_ulist",
                }
        except Exception as e:
            errors.append(f"em_ulist: {type(e).__name__}: {e}")
        time.sleep(random.uniform(0.6, 1.2))
    return out, errors, time.time() - t0


def batch_quotes(codes: List[str], prefer: Optional[str] = None) -> Dict[str, Any]:
    """Prefer single Sina batch for whole pool; Tencent only if Sina fails."""
    order = ["sina", "tencent", "eastmoney"]
    if RATE_NOTES.exists():
        try:
            rec = (json.loads(RATE_NOTES.read_text(encoding="utf-8")).get("recommended") or {})
            so = rec.get("sources_order")
            if isinstance(so, list) and so:
                order = [x for x in so if x in ("sina", "tencent", "eastmoney")]
                for x in ("sina", "tencent", "eastmoney"):
                    if x not in order:
                        order.append(x)
        except Exception:
            pass
    if prefer and prefer in order:
        order = [prefer] + [x for x in order if x != prefer]
    errors: List[str] = []
    timing: Dict[str, float] = {}
    for src in order:
        if src == "sina":
            q, e, dt = fetch_sina_batch(codes)
        elif src == "tencent":
            q, e, dt = fetch_tencent_batch(codes)
        else:
            q, e, dt = fetch_em_clist_batch(codes)
        timing[src] = round(dt, 3)
        errors.extend(e)
        # accept if we got ≥50% of codes or at least 1 when pool tiny
        need = max(1, len(codes) // 2)
        if len(q) >= need:
            return {"ok": True, "quotes": q, "source": src, "errors": errors, "timing": timing, "n": len(q)}
        errors.append(f"{src}_insufficient n={len(q)}/{len(codes)}")
    return {"ok": False, "quotes": {}, "source": "", "errors": errors, "timing": timing, "n": 0}


def classify_action(item: Dict[str, Any], q: Dict[str, Any], prev_tag: Optional[str]) -> Tuple[str, str]:
    """Return (tag, note). Does not invent; uses live quote vs pool prev20high."""
    price = q.get("price")
    open_ = q.get("open")
    prev20 = float(item.get("prev20high") or 0)
    close_t = float(item.get("close_T") or 0)
    if not price or not prev20:
        return "数据不足", "缺最新价或前20高"
    pct = q.get("pct")
    amount = q.get("amount")

    gap_pct = None
    if open_ and close_t > 0:
        gap_pct = (open_ / close_t - 1) * 100

    # 高开追涨风险: open gap >7%
    if gap_pct is not None and gap_pct > 7:
        return TAG_GAP, f"开盘相对昨收高开 {gap_pct:.1f}%>7%，追涨风险"

    # 跌破失效: last < prev20high * 0.98 after having approached, or deep weak open
    if price < prev20 * 0.98:
        if open_ and close_t and open_ < close_t * 0.95:
            return TAG_FAIL, f"现价 {price:.2f} < 前20高×0.98 且低开偏弱"
        if item.get("state") in ("接近突破", "突破刚确认", "已确认突破") or (prev_tag and "突破" in prev_tag):
            return TAG_FAIL, f"现价 {price:.2f} 跌破前20高×0.98（{prev20 * 0.98:.2f}）"
        return TAG_WEAK, f"现价偏弱 {price:.2f}，距失效线不远"

    rising_vs_open = open_ is not None and price >= open_
    if price >= prev20 and rising_vs_open:
        # volume hint if amount elevated vs rough prior day amt
        avg_yi = item.get("avg20_amt_yi")
        elevated = False
        if amount and avg_yi:
            # rough: if already > 0.5 * avg20 full-day early, or any large
            elevated = amount >= float(avg_yi) * 1e8 * 0.4
        if elevated and price > prev20:
            return TAG_VOLUME, f"现价 {price:.2f} > 前20高 {prev20} 且成交额偏高嫌疑"
        return TAG_BREAKING, f"现价 {price:.2f} ≥ 前20高 {prev20} 且相对开盘走强"

    if price > prev20:
        return TAG_BREAKING, f"现价 {price:.2f} > 前20高 {prev20}"

    # approaching
    dist = (prev20 - price) / prev20 * 100
    if dist <= 3:
        return TAG_NEAR, f"距前20高约 {dist:.2f}%，观察中"
    return TAG_HOLD, f"距前20高 {dist:.2f}%，暂无突破迹象"


MATERIAL_TAGS = {TAG_BREAKING, TAG_VOLUME, TAG_FAIL, TAG_GAP, TAG_WEAK}


def diff_events(
    pool: List[Dict[str, Any]],
    quotes: Dict[str, Dict],
    prev_snap: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    prev_map = {}
    if prev_snap and prev_snap.get("items"):
        for it in prev_snap["items"]:
            prev_map[it.get("code") or it.get("code6")] = it

    items = []
    events = []
    for p in pool:
        full = to_full_code(p)
        q = quotes.get(full)
        if not q:
            # try code6 key variants
            q = quotes.get(p.get("code6")) or quotes.get("sz" + p["code6"]) or quotes.get("sh" + p["code6"])
        prev = prev_map.get(full) or prev_map.get(p.get("code6"))
        prev_tag = (prev or {}).get("tag")
        if not q:
            row = {
                "code": full,
                "code6": p.get("code6"),
                "name": p.get("name"),
                "prev20high": p.get("prev20high"),
                "close_T": p.get("close_T"),
                "tag": "无行情",
                "note": "本轮未取到报价",
                "price": None,
                "pool_state": p.get("state"),
            }
            items.append(row)
            continue
        tag, note = classify_action(p, q, prev_tag)
        row = {
            "code": full,
            "code6": p.get("code6"),
            "name": p.get("name") or q.get("name"),
            "prev20high": p.get("prev20high"),
            "close_T": p.get("close_T"),
            "price": q.get("price"),
            "open": q.get("open"),
            "pct": q.get("pct"),
            "amount": q.get("amount"),
            "tag": tag,
            "note": note,
            "pool_state": p.get("state"),
            "quote_source": q.get("source"),
            "external_hint": p.get("external_hint"),
        }
        items.append(row)
        changed = prev_tag != tag
        material = tag in MATERIAL_TAGS
        # first sighting of material tag also counts
        if material and (changed or prev is None):
            events.append({**row, "prev_tag": prev_tag, "change": True})
        elif material and changed:
            events.append({**row, "prev_tag": prev_tag, "change": True})
    return items, events


def write_alert(events: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    lines = [
        f"# 盘中观察提醒（{meta.get('as_of')}）",
        "",
        f"- 行情源：{meta.get('quote_source')}",
        f"- 池内：{meta.get('pool_n')} | 事件：{len(events)}",
        "",
        "> 仅跟踪观察池相对前20高的价格变化，**不构成买入建议，不预判必涨**。",
        "",
        "| 代码 | 名称 | 标签 | 现价 | 前20高 | 涨跌% | 说明 |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for e in events:
        pct = e.get("pct")
        pct_s = f"{pct:.2f}" if isinstance(pct, (int, float)) else "-"
        price = e.get("price")
        price_s = f"{price:.2f}" if isinstance(price, (int, float)) else "-"
        lines.append(
            f"| {e.get('code6')} | {e.get('name')} | {e.get('tag')} | {price_s} | {e.get('prev20high')} | {pct_s} | {e.get('note')} |"
        )
    lines.append("")
    ALERT_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_quiet(msg: str, extra: Optional[dict] = None) -> None:
    obj = {
        "status": msg,
        "as_of": shanghai_now().strftime("%Y-%m-%d %H:%M:%S%z"),
        **(extra or {}),
    }
    _save_json(STATUS_PATH, obj)
    print(msg)


def ensure_minimal_pool_for_test() -> bool:
    """If pool missing, generate from candidates / report_payload."""
    if POOL_PATH.exists():
        data = _load_json(POOL_PATH)
        if data and data.get("pool"):
            return False
    # import builder
    sys.path.insert(0, str(OUT))
    from build_near_pool import build_pool
    build_pool(patch_report=True)
    return True


def run(force: bool = False, smoke: bool = False, prefer: Optional[str] = None) -> int:
    now = shanghai_now()
    in_sess, sess_msg = is_trading_session(now)

    if not POOL_PATH.exists() or not (_load_json(POOL_PATH) or {}).get("pool"):
        print("pool missing/empty → building minimal from last candidates")
        ensure_minimal_pool_for_test()

    pdata = _load_json(POOL_PATH) or {}
    pool = pdata.get("pool") or []
    if not pool:
        write_quiet("EMPTY_POOL", {"session": sess_msg})
        if smoke:
            _save_json(SMOKE_PATH, {"ok": False, "reason": "EMPTY_POOL", "session": sess_msg})
        return 0

    if not in_sess and not force:
        write_quiet("OUTSIDE_SESSION", {"session": sess_msg, "pool_n": len(pool), "hint": "use --force for dry-run"})
        if smoke:
            _save_json(
                SMOKE_PATH,
                {
                    "ok": True,
                    "gate": "OUTSIDE_SESSION",
                    "session": sess_msg,
                    "pool_n": len(pool),
                    "as_of": now.strftime("%Y-%m-%d %H:%M:%S%z"),
                },
            )
        return 0

    codes = [to_full_code(p) for p in pool]
    # dedupe preserve order
    seen = set()
    codes_u = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            codes_u.append(c)

    t0 = time.time()
    bq = batch_quotes(codes_u, prefer=prefer)
    elapsed = time.time() - t0

    # cache last quotes
    _save_json(
        LAST_QUOTES,
        {
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S%z"),
            "source": bq.get("source"),
            "n": bq.get("n"),
            "quotes": bq.get("quotes") or {},
            "errors": bq.get("errors") or [],
        },
    )

    if not bq.get("ok"):
        write_quiet("QUOTE_FAIL", {"errors": bq.get("errors"), "timing": bq.get("timing")})
        if smoke:
            _save_json(
                SMOKE_PATH,
                {
                    "ok": False,
                    "reason": "QUOTE_FAIL",
                    "errors": bq.get("errors"),
                    "timing": bq.get("timing"),
                    "elapsed_s": round(elapsed, 3),
                    "sources_tried": list((bq.get("timing") or {}).keys()),
                },
            )
        return 1

    prev_snap = _load_json(SNAP_PATH)
    items, events = diff_events(pool, bq["quotes"], prev_snap)

    snap = {
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S%z"),
        "t_date_pool": pdata.get("t_date"),
        "forced": bool(force and not in_sess),
        "session": sess_msg,
        "quote_source": bq.get("source"),
        "pool_n": len(pool),
        "quoted_n": bq.get("n"),
        "events_n": len(events),
        "timing": bq.get("timing"),
        "elapsed_s": round(elapsed, 3),
        "items": items,
        "events": events,
        "errors": bq.get("errors") or [],
    }
    _save_json(SNAP_PATH, snap)

    if smoke:
        _save_json(
            SMOKE_PATH,
            {
                "ok": True,
                "as_of": snap["as_of"],
                "session": sess_msg,
                "forced": snap["forced"],
                "quote_source": bq.get("source"),
                "sources_timing": bq.get("timing"),
                "elapsed_s": round(elapsed, 3),
                "pool_n": len(pool),
                "quoted_n": bq.get("n"),
                "events_n": len(events),
                "sample": items[:3],
            },
        )

    if not events:
        write_quiet(
            "NO_CHANGE",
            {
                "quote_source": bq.get("source"),
                "pool_n": len(pool),
                "quoted_n": bq.get("n"),
                "elapsed_s": round(elapsed, 3),
            },
        )
        # still keep a minimal alert stub noting quiet
        ALERT_PATH.write_text(
            f"# 盘中观察（安静）\n\n{now.strftime('%Y-%m-%d %H:%M:%S%z')} NO_CHANGE — 无相对上次快照的实质状态变化。源={bq.get('source')}\n",
            encoding="utf-8",
        )
        return 0

    write_alert(events, snap)
    write_quiet(
        "EVENTS",
        {
            "n": len(events),
            "tags": [e.get("tag") for e in events],
            "quote_source": bq.get("source"),
            "alert": str(ALERT_PATH),
        },
    )
    print(f"EVENTS {len(events)} → {ALERT_PATH}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Intraday near-breakout watcher (low frequency)")
    ap.add_argument("--force", action="store_true", help="Run quote even outside trading session")
    ap.add_argument("--smoke", action="store_true", help="Write intraday_smoke.json")
    ap.add_argument("--prefer", choices=["sina", "tencent", "eastmoney"], default=None)
    args = ap.parse_args()
    sys.exit(run(force=args.force, smoke=args.smoke, prefer=args.prefer))


if __name__ == "__main__":
    main()
