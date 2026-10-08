#!/usr/bin/env python3
"""Gentle rate-limit probe for intraday batch quote sources.

Max ~20 requests. Exponential backoff on 403/429/empty.
Writes cache/rate_limit_notes.json with recommended pacing.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

OUT = _PKG_ROOT
CACHE = OUT / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
NOTES_PATH = CACHE / "rate_limit_notes.json"
POOL_PATH = OUT / "near_breakout_pool.json"

TZ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (compatible; trend-breakout-probe/1.0)"
MAX_REQUESTS = 20

# Sample codes (prefer from pool; pad with liquid names if needed)
FALLBACK_CODES = [
    "sh600000", "sh600519", "sh601318", "sz000001", "sz000002",
    "sz300750", "sh688981", "sz002415", "sh600036", "sz000858",
    "sh601166", "sz300059", "sh600276", "sz002594", "sh603259",
    "sz000725", "sh600030", "sz002475", "sh601012", "sz300014",
    "sh600887", "sz000063", "sh601888", "sz002352", "sh600031",
    "sz300015", "sh601899", "sz002241", "sh600104", "sz000568",
]


def shanghai_now() -> datetime:
    return datetime.now(TZ)


def load_sample_codes(n: int = 30) -> List[str]:
    codes: List[str] = []
    if POOL_PATH.exists():
        try:
            pdata = json.loads(POOL_PATH.read_text(encoding="utf-8"))
            for p in pdata.get("pool") or []:
                c = p.get("code")
                if c and str(c)[:2] in ("sh", "sz"):
                    codes.append(str(c))
                else:
                    c6 = str(p.get("code6") or "").zfill(6)
                    codes.append(("sh" if c6.startswith(("5", "6", "9")) else "sz") + c6)
        except Exception:
            pass
    for c in FALLBACK_CODES:
        if c not in codes:
            codes.append(c)
    return codes[:n]


def _count_sina(text: str) -> int:
    n = 0
    for line in text.splitlines():
        if '="' not in line:
            continue
        right = line.split('="', 1)[1]
        parts = right.rstrip('";').split(",")
        if len(parts) >= 4 and parts[3]:
            try:
                if float(parts[3]) > 0:
                    n += 1
            except ValueError:
                pass
    return n


def _count_tencent(text: str) -> int:
    n = 0
    for seg in text.split(";"):
        if "~" not in seg:
            continue
        try:
            body = seg.split("=", 1)[1].strip().strip('"')
            parts = body.split("~")
            if len(parts) > 3 and parts[3]:
                if float(parts[3]) > 0:
                    n += 1
        except (IndexError, ValueError):
            pass
    return n


def probe_one(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    kind: str,
    req_budget: List[int],
) -> Dict[str, Any]:
    """Single request with gentle backoff. Mutates req_budget[0]."""
    result: Dict[str, Any] = {
        "kind": kind,
        "url_host": url.split("/")[2],
        "ok": False,
        "status": None,
        "latency_ms": None,
        "n_quotes": 0,
        "error": None,
        "backoff_s": 0,
    }
    backoff = 0.5
    for attempt in range(3):
        if req_budget[0] <= 0:
            result["error"] = "budget_exhausted"
            return result
        req_budget[0] -= 1
        t0 = time.time()
        try:
            r = session.get(url, headers=headers, timeout=12)
            lat = (time.time() - t0) * 1000
            result["status"] = r.status_code
            result["latency_ms"] = round(lat, 1)
            if r.status_code in (403, 429):
                result["error"] = f"http_{r.status_code}"
                result["backoff_s"] = backoff
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            r.encoding = "gbk"
            text = r.text or ""
            if not text.strip():
                result["error"] = "empty_body"
                result["backoff_s"] = backoff
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            if kind == "sina":
                n = _count_sina(text)
            else:
                n = _count_tencent(text)
            result["n_quotes"] = n
            if n == 0:
                result["error"] = "zero_quotes"
                result["backoff_s"] = backoff
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            result["ok"] = True
            result["error"] = None
            return result
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            result["latency_ms"] = round((time.time() - t0) * 1000, 1)
            result["backoff_s"] = backoff
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
    return result


def probe_inter_chunk(
    session: requests.Session,
    codes: List[str],
    sleep_s: float,
    req_budget: List[int],
) -> Dict[str, Any]:
    """Two small sina chunks with given inter-chunk sleep."""
    half = max(1, len(codes) // 2)
    c1, c2 = codes[:half], codes[half:]
    if not c2:
        c2 = codes[:1]
    headers = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}
    out: Dict[str, Any] = {"sleep_s": sleep_s, "ok": False, "latencies_ms": [], "n_total": 0, "errors": []}
    for chunk in (c1, c2):
        if req_budget[0] <= 0:
            out["errors"].append("budget_exhausted")
            return out
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        r1 = probe_one(session, url, headers, "sina", req_budget)
        out["latencies_ms"].append(r1.get("latency_ms"))
        if not r1.get("ok"):
            out["errors"].append(r1.get("error") or "fail")
            return out
        out["n_total"] += int(r1.get("n_quotes") or 0)
        time.sleep(sleep_s)
    out["ok"] = True
    return out


def main() -> int:
    codes30 = load_sample_codes(30)
    codes_small = codes30[:8]  # for inter-chunk tests
    budget = [MAX_REQUESTS]
    session = requests.Session()
    trials: List[Dict[str, Any]] = []

    # 1) Sina batch 30
    sina_url = "https://hq.sinajs.cn/list=" + ",".join(codes30)
    sina_h = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}
    sina_r = probe_one(session, sina_url, sina_h, "sina", budget)
    trials.append({"name": "sina_batch_30", **sina_r})
    time.sleep(0.3)

    # 2) Tencent batch 30
    tx_url = "http://qt.gtimg.cn/q=" + ",".join(codes30)
    tx_r = probe_one(session, tx_url, {"User-Agent": UA}, "tencent", budget)
    trials.append({"name": "tencent_batch_30", **tx_r})
    time.sleep(0.3)

    # 3) Inter-chunk sleep ladder: 0.2 → 0.1 → 0.05 (stop early if fail)
    inter_results: List[Dict[str, Any]] = []
    shortest_ok: Optional[float] = None
    for sleep_try in (0.2, 0.1, 0.05):
        if budget[0] < 2:
            break
        ir = probe_inter_chunk(session, codes_small, sleep_try, budget)
        inter_results.append(ir)
        trials.append({"name": f"sina_inter_chunk_{sleep_try}", **ir})
        if ir.get("ok"):
            shortest_ok = sleep_try
        else:
            break  # don't go shorter if this failed
        time.sleep(0.4)

    # Optional: one more sina batch to confirm after short sleeps
    confirm = None
    if budget[0] > 0 and shortest_ok is not None:
        time.sleep(0.5)
        confirm = probe_one(session, sina_url, sina_h, "sina", budget)
        trials.append({"name": "sina_confirm_after_short", **confirm})

    min_inter = shortest_ok if shortest_ok is not None else 0.3
    # Slight safety margin
    min_inter_rec = round(max(0.05, min_inter), 3)
    if shortest_ok is None:
        min_inter_rec = 0.3

    sources_order = []
    if sina_r.get("ok"):
        sources_order.append("sina")
    if tx_r.get("ok"):
        sources_order.append("tencent")
    if "eastmoney" not in sources_order:
        sources_order.append("eastmoney")  # last resort, not probed heavily

    notes = {
        "probed_at": shanghai_now().strftime("%Y-%m-%d %H:%M:%S%z"),
        "max_requests_budget": MAX_REQUESTS,
        "requests_used": MAX_REQUESTS - budget[0],
        "sample_n": len(codes30),
        "trials": trials,
        "sina_batch_30": {
            "ok": sina_r.get("ok"),
            "latency_ms": sina_r.get("latency_ms"),
            "n_quotes": sina_r.get("n_quotes"),
            "status": sina_r.get("status"),
        },
        "tencent_batch_30": {
            "ok": tx_r.get("ok"),
            "latency_ms": tx_r.get("latency_ms"),
            "n_quotes": tx_r.get("n_quotes"),
            "status": tx_r.get("status"),
        },
        "inter_chunk_ladder": inter_results,
        "shortest_ok_inter_chunk_s": shortest_ok,
        "recommended": {
            "min_inter_chunk_s": min_inter_rec,
            "min_post_query_sleep_s": 180,
            "post_query_jitter_s": [0, 120],
            "post_query_clamp_s": [150, 600],
            "chunk_size": 80,
            "sources_order": sources_order or ["sina", "tencent", "eastmoney"],
            "prefer_single_sina_batch": True,
            "notes": (
                "Gentle probe only. Prefer one Sina HQ list= for whole pool (≤80). "
                "Fall back to Tencent only if Sina insufficient. "
                "Session loop: wait AFTER each query completes, base~180s + jitter 0–120s."
            ),
        },
    }
    NOTES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PROBE_OK requests_used={notes['requests_used']} → {NOTES_PATH}")
    print(
        f"sina_ms={sina_r.get('latency_ms')} n={sina_r.get('n_quotes')} | "
        f"tencent_ms={tx_r.get('latency_ms')} n={tx_r.get('n_quotes')} | "
        f"min_inter_chunk_s={min_inter_rec}"
    )
    return 0 if (sina_r.get("ok") or tx_r.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
