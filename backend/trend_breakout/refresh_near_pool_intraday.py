#!/usr/bin/env python3
"""Hourly intraday refresh of the near-breakout observation pool.

Cadence (parent): about every 1 hour during session.
- Load current pool and/or rebuild from last-close candidates via build_near_pool.
- Refresh LIVE quotes (sina/tencent batch via intraday_watch helpers).
- Drop names whose today volume/amount is still below recent average *pace* (无放量).
- Write near_breakout_pool.json/.md and near_pool_refresh_status.json.
- Outside session: quiet exit 0 with OUTSIDE_SESSION (unless --force).

放量规则（权威）:
  无放量(量<均量)不得入池.
  Prefer: today_vol >= ma20_vol (full-day) OR today_vol >= ma20_vol * session_frac (on pace).
  Else amount: today_amount >= ma20_amount OR on-pace vs avg20_amt_yi.
  Missing live vol/amt for a name → keep (don't falsely drop on quote gap).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

OUT = _PKG_ROOT
TZ = ZoneInfo("Asia/Shanghai")
STATUS_PATH = OUT / "near_pool_refresh_status.json"

sys.path.insert(0, str(OUT))
from build_near_pool import build_pool, _md_table  # noqa: E402
from intraday_watch import (  # noqa: E402
    batch_quotes,
    is_trading_session,
    shanghai_now,
    to_full_code,
)


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _ma20_amount_yuan(item: Dict[str, Any]) -> Optional[float]:
    avg = item.get("avg20_amt_yi")
    if avg is None:
        return None
    try:
        v = float(avg) * 1e8
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _estimate_ma20_vol(item: Dict[str, Any], quote: Dict[str, Any]) -> Optional[float]:
    """Best-effort MA20 volume (shares). Usually unavailable; return None."""
    # If pool stored ma20_vol explicitly
    for k in ("ma20_vol", "avg20_vol", "ma20_volume"):
        if item.get(k) is not None:
            try:
                v = float(item[k])
                return v if v > 0 else None
            except (TypeError, ValueError):
                pass
    return None


def session_elapsed_fraction(now: Optional[datetime] = None) -> float:
    """Fraction of continuous-auction minutes elapsed (240 min/day). Clamp to [0.05, 1]."""
    from datetime import time as dtime

    now = now or shanghai_now()
    t = now.time()
    # Morning 09:30-11:30 = 120m; Afternoon 13:00-15:00 = 120m
    elapsed = 0.0
    if t < dtime(9, 30):
        elapsed = 0.0
    elif t <= dtime(11, 30):
        elapsed = (now.hour * 60 + now.minute + now.second / 60) - (9 * 60 + 30)
    elif t < dtime(13, 0):
        elapsed = 120.0
    elif t <= dtime(15, 0):
        elapsed = 120.0 + ((now.hour * 60 + now.minute + now.second / 60) - (13 * 60))
    else:
        elapsed = 240.0
    frac = elapsed / 240.0
    return max(0.05, min(1.0, frac))


def live_fangliang_ok(item: Dict[str, Any], quote: Optional[Dict[str, Any]]) -> Tuple[bool, str, Optional[float]]:
    """Return (keep, reason, vol_ratio_live).

    无放量 = today still below recent-avg *pace*.
    - Prefer volume: keep if today_vol >= ma20_vol (full-day already) OR
      today_vol >= ma20_vol * session_frac (on pace). Reported vol_ratio_live = today/ma20.
    - Else amount vs avg20 amount with same pace rule.
    - Quote gap: keep if close VR>=1; missing metrics → exclude.
    """
    close_vr = item.get("vol_ratio")
    try:
        close_vr_f = float(close_vr) if close_vr is not None else None
    except (TypeError, ValueError):
        close_vr_f = None

    if not quote:
        if close_vr_f is not None and close_vr_f < 1.0:
            return False, "no_quote_and_close_vr_lt_1", close_vr_f
        return True, "no_quote_keep", close_vr_f

    today_vol = quote.get("volume")
    today_amt = quote.get("amount")
    try:
        today_vol_f = float(today_vol) if today_vol is not None else None
    except (TypeError, ValueError):
        today_vol_f = None
    try:
        today_amt_f = float(today_amt) if today_amt is not None else None
    except (TypeError, ValueError):
        today_amt_f = None

    frac = session_elapsed_fraction()
    # Midday conservative floor: by half-session require at least 0.5x full avg
    # (frac itself is ~0.5 at midday). Early session uses actual frac (min 0.05).

    ma20_vol = _estimate_ma20_vol(item, quote)
    if today_vol_f is not None and ma20_vol is not None and ma20_vol > 0:
        vr_live = today_vol_f / ma20_vol
        if vr_live >= 1.0 or today_vol_f >= ma20_vol * frac:
            return True, "vol_pace_ok", round(vr_live, 3)
        return False, "vol_below_pace", round(vr_live, 3)

    ma20_amt = _ma20_amount_yuan(item)
    if today_amt_f is not None and ma20_amt is not None and ma20_amt > 0:
        vr_live = today_amt_f / ma20_amt
        if vr_live >= 1.0 or today_amt_f >= ma20_amt * frac:
            return True, "amt_pace_ok", round(vr_live, 3)
        return False, "amt_below_pace", round(vr_live, 3)

    # Live quote present but no comparable avg → fall back to close VR floor
    if close_vr_f is not None:
        if close_vr_f >= 1.0:
            return True, "close_vr_ok_no_live_avg", close_vr_f
        return False, "close_vr_lt_1_no_live_avg", close_vr_f

    # Missing both live comparable and close VR → exclude (don't guess)
    return False, "missing_vol_metrics", None


def refresh_pool(
    *,
    force: bool = False,
    rebuild: bool = True,
    prefer: Optional[str] = None,
) -> Dict[str, Any]:
    now = shanghai_now()
    in_sess, sess_msg = is_trading_session(now)
    if not in_sess and not force:
        status = {
            "status": "OUTSIDE_SESSION",
            "session": sess_msg,
            "at": now.strftime("%Y-%m-%d %H:%M:%S%z"),
        }
        _save_json(STATUS_PATH, status)
        print("OUTSIDE_SESSION", sess_msg)
        return status

    before_count = 0
    prev = _load_json(OUT / "near_breakout_pool.json") or {}
    if isinstance(prev, dict):
        before_count = int(prev.get("count") or len(prev.get("pool") or []))

    if rebuild:
        # Rebuild from last-close candidates/all_results with VR>=1 hard filter
        built = build_pool(patch_report=False)
    else:
        built = prev if prev.get("pool") is not None else build_pool(patch_report=False)

    pool: List[Dict[str, Any]] = list(built.get("pool") or [])
    after_rebuild = len(pool)

    codes = [to_full_code(it) for it in pool]
    quotes: Dict[str, Dict] = {}
    quote_meta: Dict[str, Any] = {}
    if codes:
        bq = batch_quotes(codes, prefer=prefer)
        quotes = bq.get("quotes") or {}
        quote_meta = {
            "source": bq.get("source"),
            "errors": bq.get("errors") or [],
            "timing": bq.get("timing") or {},
            "n_codes": len(codes),
            "n_quotes": len(quotes),
        }

    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for it in pool:
        full = to_full_code(it)
        q = quotes.get(full)
        ok, reason, vr_live = live_fangliang_ok(it, q)
        # Annotate live fields when available
        if q:
            it = dict(it)
            it["live_price"] = q.get("price")
            it["live_pct"] = q.get("pct")
            it["live_volume"] = q.get("volume")
            it["live_amount"] = q.get("amount")
            it["live_vol_ratio"] = vr_live
            it["live_source"] = q.get("source")
            # Update dist_to_breakout with live price when possible
            prev20 = float(it.get("prev20high") or 0)
            px = q.get("price")
            if prev20 > 0 and px:
                try:
                    pxf = float(px)
                    it["dist_to_breakout_pct"] = round((prev20 - pxf) / prev20 * 100, 3)
                    it["close_T"] = round(pxf, 3)  # display as last seen
                except (TypeError, ValueError):
                    pass
            if it.get("vol_ratio") is None and vr_live is not None:
                it["vol_ratio"] = vr_live
        if ok:
            # live_fangliang_ok already applied pace / full-day 放量 rules.
            # Do NOT re-require live VR>=1 early session (today/ma20 often <1 while still on pace).
            # Close-side VR>=1 was enforced at rebuild.
            kept.append(it)
        else:
            dropped.append({
                "code6": it.get("code6"),
                "name": it.get("name"),
                "reason": reason,
                "vol_ratio": vr_live if vr_live is not None else it.get("vol_ratio"),
            })

    counts_by_state: Dict[str, int] = {}
    for it in kept:
        st = it.get("state") or "?"
        counts_by_state[st] = counts_by_state.get(st, 0) + 1

    out = {
        "t_date": built.get("t_date"),
        "built_at": built.get("built_at"),
        "refreshed_at": now.strftime("%Y-%m-%d %H:%M:%S%z"),
        "regime": built.get("regime"),
        "near_cap": built.get("near_cap"),
        "dist_max": built.get("dist_max"),
        "count": len(kept),
        "counts_by_state": counts_by_state,
        "vol_ratio_min": 1.0,
        "dropped_no_vol": len(dropped),
        "pool": kept,
        "notes": list(built.get("notes") or []) + [
            "盘中小时刷新：剔除当日量/额仍低于近20日均量的无放量标的",
        ],
        "refresh": {
            "session": sess_msg,
            "forced": bool(force),
            "before_count": before_count,
            "after_rebuild": after_rebuild,
            "after_live_filter": len(kept),
            "dropped": dropped,
            "quote": quote_meta,
        },
    }
    (OUT / "near_breakout_pool.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "near_breakout_pool.md").write_text(_md_table(out), encoding="utf-8")

    status = {
        "status": "REFRESHED",
        "session": sess_msg,
        "at": out["refreshed_at"],
        "before_count": before_count,
        "after_rebuild": after_rebuild,
        "after_count": len(kept),
        "dropped_no_vol": len(dropped),
        "counts_by_state": counts_by_state,
        "quote_source": quote_meta.get("source"),
        "quote_n": quote_meta.get("n_quotes"),
    }
    _save_json(STATUS_PATH, status)
    print(
        f"REFRESHED before={before_count} rebuild={after_rebuild} "
        f"after={len(kept)} dropped_no_vol={len(dropped)} src={quote_meta.get('source')}"
    )
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description="Hourly near-pool intraday refresh")
    ap.add_argument("--force", action="store_true", help="Run even outside trading session")
    ap.add_argument("--no-rebuild", action="store_true", help="Skip close-side rebuild; filter current pool only")
    ap.add_argument("--prefer", choices=["sina", "tencent", "eastmoney"], default=None)
    args = ap.parse_args()
    refresh_pool(force=args.force, rebuild=not args.no_rebuild, prefer=args.prefer)


if __name__ == "__main__":
    main()
