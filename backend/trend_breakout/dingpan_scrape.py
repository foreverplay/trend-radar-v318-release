#!/usr/bin/env python3
"""Best-effort 盯盘助手 / public breakout hint scrape.

Polite: 1 request, short timeout, User-Agent.
If blocked/login-walled → record skip and exit 0 (do not hang).
Overlaps with near_breakout_pool get external_hint tags.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

import requests

OUT = _PKG_ROOT
CACHE = OUT / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo("Asia/Shanghai")
UA = "Mozilla/5.0 (compatible; trend-breakout-dingpan/1.0; +research)"
TIMEOUT = 8

# Candidate public endpoints (may be blocked / change often)
CANDIDATE_URLS = [
    # Eastmoney quote center — limit-up / strong stocks board (public JSON-ish pages)
    (
        "em_zt_pool_html",
        "https://quote.eastmoney.com/ztb/detail#type=ztgc",
        "html",
    ),
    (
        "em_push2ex_zt",
        "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
        "&dpt=wz.ztzt&Pageindex=0&pagesize=50&sort=fbt%3Aasc&date=",
        "json_date",
    ),
]


def _save(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _today_compact() -> str:
    return datetime.now(TZ).strftime("%Y%m%d")


def try_em_zt_json() -> Dict[str, Any]:
    """One polite request to EM limit-up pool JSON."""
    url = (
        "https://push2ex.eastmoney.com/getTopicZTPool"
        "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
        f"&Pageindex=0&pagesize=50&sort=fbt%3Aasc&date={_today_compact()}"
    )
    t0 = time.time()
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=TIMEOUT,
        )
        elapsed = time.time() - t0
        if r.status_code in (401, 403):
            return {
                "ok": False,
                "source": "em_zt_pool",
                "skip_reason": f"http_{r.status_code}_blocked",
                "elapsed_s": round(elapsed, 3),
            }
        if r.status_code != 200:
            return {
                "ok": False,
                "source": "em_zt_pool",
                "skip_reason": f"http_{r.status_code}",
                "elapsed_s": round(elapsed, 3),
            }
        # may be JSON or empty
        try:
            data = r.json()
        except Exception:
            text = (r.text or "")[:200]
            if "login" in text.lower() or "登录" in text:
                return {
                    "ok": False,
                    "source": "em_zt_pool",
                    "skip_reason": "login_walled",
                    "elapsed_s": round(elapsed, 3),
                }
            return {
                "ok": False,
                "source": "em_zt_pool",
                "skip_reason": "non_json",
                "elapsed_s": round(elapsed, 3),
                "snip": text,
            }
        pool = (data.get("data") or {}).get("pool") or []
        names = []
        codes = []
        for row in pool[:50]:
            c = str(row.get("c") or row.get("code") or "").zfill(6)
            n = row.get("n") or row.get("name") or ""
            if c and c != "000000":
                codes.append(c)
                names.append({"code6": c, "name": n})
        return {
            "ok": bool(codes),
            "source": "em_zt_pool",
            "elapsed_s": round(elapsed, 3),
            "hints": names,
            "n": len(codes),
            "skip_reason": None if codes else "empty_pool",
        }
    except requests.Timeout:
        return {"ok": False, "source": "em_zt_pool", "skip_reason": "timeout", "elapsed_s": TIMEOUT}
    except Exception as e:
        return {
            "ok": False,
            "source": "em_zt_pool",
            "skip_reason": f"{type(e).__name__}: {e}",
            "elapsed_s": round(time.time() - t0, 3),
        }


def merge_hints_into_pool(hints: List[Dict[str, str]]) -> Dict[str, Any]:
    path = OUT / "near_breakout_pool.json"
    if not path.exists():
        return {"merged": 0, "reason": "no_pool"}
    data = json.loads(path.read_text(encoding="utf-8"))
    pool = data.get("pool") or []
    hint_map = {h["code6"]: h.get("name") for h in hints}
    merged = 0
    for it in pool:
        c = it.get("code6")
        if c in hint_map:
            it["external_hint"] = "涨停/异动池重叠"
            note = it.get("observe_note") or ""
            if "external_hint" not in note:
                it["observe_note"] = (note + " | 外部异动提示").strip(" |")
            merged += 1
    if merged:
        data["external_hint_n"] = merged
        data["external_hint_source"] = "em_zt_pool"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"merged": merged, "pool_n": len(pool)}


def run() -> int:
    # Only ONE primary attempt (polite). Document other 盯盘助手 as skip if no stable public URL.
    result = try_em_zt_json()
    result["as_of"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S%z")
    result["dingpan_assistant"] = {
        "usable": False,
        "note": (
            "未发现稳定免登录的「盯盘助手」公开API；"
            "本脚本仅尝试东方财富涨停池公共接口作异动提示。"
            "若被墙/SSL失败则跳过，不阻塞主流程。"
        ),
    }

    status_path = CACHE / "dingpan_scrape_status.json"
    if result.get("ok"):
        merge = merge_hints_into_pool(result.get("hints") or [])
        result["merge"] = merge
        result["dingpan_assistant"]["usable"] = False  # still not 盯盘助手 proper
        result["dingpan_assistant"]["em_zt_ok"] = True
        _save(status_path, result)
        print(f"OK em_zt n={result.get('n')} merged={merge.get('merged')}")
        return 0

    result["merge"] = {"merged": 0}
    _save(status_path, result)
    # Append skip note to DATA_SOURCES if not already
    _append_skip_note(result.get("skip_reason") or "unknown")
    print(f"SKIP {result.get('skip_reason')}")
    return 0


def _append_skip_note(reason: str) -> None:
    md = OUT / "DATA_SOURCES.md"
    if not md.exists():
        return
    text = md.read_text(encoding="utf-8")
    marker = "## 盯盘助手 / external scrape"
    note = (
        f"{marker}\n\n"
        f"- Status: **skipped / best-effort only** (as of {datetime.now(TZ).strftime('%Y-%m-%d %H:%M CST')})\n"
        f"- No stable public 盯盘助手 URL without login; do not hang pipeline.\n"
        f"- Tried: Eastmoney `push2ex.getTopicZTPool` (1 req, timeout {TIMEOUT}s).\n"
        f"- Last skip/result: `{reason}`\n"
        f"- Helper: `dingpan_scrape.py` → `cache/dingpan_scrape_status.json`\n"
    )
    if marker in text:
        # replace section lightly
        before = text.split(marker)[0].rstrip()
        rest = text.split(marker, 1)[1]
        # next ##
        lines = rest.splitlines()
        cut = len(lines)
        for i, ln in enumerate(lines):
            if i > 0 and ln.startswith("## "):
                cut = i
                break
        after = "\n".join(lines[cut:])
        text = before + "\n\n" + note + ("\n" + after if after.strip() else "\n")
    else:
        text = text.rstrip() + "\n\n" + note + "\n"
    md.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(run())
