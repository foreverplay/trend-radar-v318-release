#!/usr/bin/env python3
"""Session loop for intraday near-breakout watcher.

Cadence: AFTER each query completes, wait ~3 minutes with jitter
  wait = clamp(180 + random(0,120), 150, 600) seconds
NOT wall-clock cron every 3 minutes (platform @every floor is 5m).

Trading segments Asia/Shanghai weekdays: 09:30–11:30, 13:00–15:00.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

OUT = _PKG_ROOT
CACHE = OUT / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
STATE_PATH = CACHE / "session_loop_state.json"
PID_PATH = CACHE / "session_loop.pid"
LOG_PATH = CACHE / "session_loop.log"
NOTES_PATH = CACHE / "rate_limit_notes.json"

TZ = ZoneInfo("Asia/Shanghai")
AM_START, AM_END = dtime(9, 30), dtime(11, 30)
PM_START, PM_END = dtime(13, 0), dtime(15, 0)

# Default post-query pacing (overridable from rate_limit_notes.json)
BASE_SLEEP_S = 180
JITTER_MAX_S = 120
CLAMP_LO, CLAMP_HI = 150, 600


def shanghai_now() -> datetime:
    return datetime.now(TZ)


def _log(msg: str) -> None:
    line = f"{shanghai_now().strftime('%Y-%m-%d %H:%M:%S%z')} {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def _save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_pacing() -> Tuple[int, int, int, int]:
    """Return base, jitter_max, clamp_lo, clamp_hi from notes if present."""
    base, jitter, lo, hi = BASE_SLEEP_S, JITTER_MAX_S, CLAMP_LO, CLAMP_HI
    notes = _load_json(NOTES_PATH) or {}
    rec = notes.get("recommended") or {}
    if isinstance(rec.get("min_post_query_sleep_s"), (int, float)):
        base = int(rec["min_post_query_sleep_s"])
    j = rec.get("post_query_jitter_s")
    if isinstance(j, (list, tuple)) and len(j) == 2:
        jitter = int(j[1])
    c = rec.get("post_query_clamp_s")
    if isinstance(c, (list, tuple)) and len(c) == 2:
        lo, hi = int(c[0]), int(c[1])
    return base, jitter, lo, hi


def compute_wait_s() -> int:
    base, jitter, lo, hi = load_pacing()
    wait = base + random.randint(0, max(0, jitter))
    return max(lo, min(hi, wait))


def session_window(now: Optional[datetime] = None) -> Tuple[str, bool]:
    """Return (segment, in_auction). segment in morning|afternoon|lunch|pre|post|weekend."""
    now = now or shanghai_now()
    if now.weekday() >= 5:
        return "weekend", False
    t = now.time()
    if AM_START <= t <= AM_END:
        return "morning", True
    if PM_START <= t <= PM_END:
        return "afternoon", True
    if AM_END < t < PM_START:
        return "lunch", False
    if t < AM_START:
        return "pre", False
    return "post", False


def seconds_until(target: dtime, now: Optional[datetime] = None) -> float:
    now = now or shanghai_now()
    tgt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if tgt <= now:
        return 0.0
    return (tgt - now).total_seconds()


def segment_end(segment: str, now: Optional[datetime] = None) -> Optional[datetime]:
    now = now or shanghai_now()
    if segment == "morning":
        return now.replace(hour=11, minute=30, second=0, microsecond=0)
    if segment == "afternoon":
        return now.replace(hour=15, minute=0, second=0, microsecond=0)
    return None


def pid_alive(pid: int) -> bool:
    """True if process exists. EPERM means it exists but we cannot signal it."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as e:
        # errno 3 = ESRCH (no such process); 1 = EPERM
        if getattr(e, "errno", None) == 3:
            return False
        if getattr(e, "errno", None) == 1:
            return True
        return False


def acquire_pid(force: bool = False) -> bool:
    if PID_PATH.exists():
        try:
            old = int(PID_PATH.read_text().strip().split()[0])
        except Exception:
            old = None
        if old and pid_alive(old) and old != os.getpid():
            if not force:
                _log(f"REFUSE second instance pid={old} (use --force)")
                return False
            _log(f"force: superseding stale/other pid={old}")
        try:
            PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
    PID_PATH.write_text(f"{os.getpid()}\n", encoding="utf-8")
    return True


def release_pid() -> None:
    try:
        if PID_PATH.exists():
            cur = PID_PATH.read_text().strip().split()[0]
            if cur == str(os.getpid()):
                PID_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def write_state(**kwargs: Any) -> None:
    prev = _load_json(STATE_PATH) or {}
    prev.update(kwargs)
    prev["pid"] = os.getpid()
    prev["updated_at"] = shanghai_now().strftime("%Y-%m-%d %H:%M:%S%z")
    _save_json(STATE_PATH, prev)


def run_one_watch(force_quote: bool = False) -> Dict[str, Any]:
    """Import and call intraday_watch.run; capture status from status file / stdout semantics."""
    sys.path.insert(0, str(OUT))
    import intraday_watch as iw  # noqa: WPS433

    t0 = time.time()
    # Outside session: session loop only calls us when in session, but --once may need --force
    rc = iw.run(force=force_quote, smoke=False, prefer=None)
    elapsed_ms = int((time.time() - t0) * 1000)
    status_obj = _load_json(OUT / "intraday_status.json") or {}
    snap = _load_json(OUT / "intraday_snapshot.json") or {}
    status = status_obj.get("status") or ("EVENTS" if snap.get("events_n") else "UNKNOWN")
    quote_source = status_obj.get("quote_source") or snap.get("quote_source") or ""
    return {
        "rc": rc,
        "elapsed_ms": elapsed_ms,
        "status": status,
        "quote_source": quote_source,
        "events_n": snap.get("events_n") if status == "EVENTS" else status_obj.get("n"),
        "as_of": status_obj.get("as_of") or snap.get("as_of"),
    }


def sleep_interruptible(seconds: float, stop_at: Optional[datetime] = None) -> str:
    """Sleep in small slices; return reason: done|session_end|lunch_end."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        now = shanghai_now()
        seg, in_sess = session_window(now)
        if stop_at and now >= stop_at:
            return "session_end"
        if not in_sess and seg != "lunch":
            # left trading day
            if seg in ("post", "weekend", "pre"):
                return "session_end"
        remaining = end - time.time()
        if stop_at:
            remaining = min(remaining, (stop_at - now).total_seconds())
        if remaining <= 0:
            break
        time.sleep(min(5.0, remaining))
    return "done"


def allowed_now(mode: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Whether loop should run a query now given mode flags."""
    now = now or shanghai_now()
    seg, in_sess = session_window(now)
    if mode == "once":
        return True, seg  # caller decides force
    if mode == "morning":
        return seg == "morning", seg
    if mode == "afternoon":
        return seg == "afternoon", seg
    if mode == "full-day":
        if in_sess:
            return True, seg
        if seg == "lunch":
            return False, seg  # sleep through
        return False, seg
    # default: any in-session segment
    return in_sess, seg


def run_loop(mode: str, force: bool = False) -> int:
    if not acquire_pid(force=force):
        return 2

    try:
        write_state(
            mode=mode,
            status="STARTING",
            last_finish=None,
            next_wake=None,
            segment=session_window()[0],
        )
        _log(f"START mode={mode} pid={os.getpid()}")

        # --once: single iteration, no loop sleep
        if mode == "once":
            seg, in_sess = session_window()
            force_q = force or (not in_sess)
            if not in_sess and not force:
                _log(f"OUTSIDE_SESSION seg={seg} (pass --force to quote anyway)")
                write_state(status="OUTSIDE_SESSION", segment=seg, last_finish=shanghai_now().strftime("%Y-%m-%d %H:%M:%S%z"))
                print("OUTSIDE_SESSION")
                return 0
            try:
                result = run_one_watch(force_quote=force_q)
            except Exception as e:
                _log(f"ERROR {type(e).__name__}: {e}")
                traceback.print_exc()
                write_state(status="ERROR", error=str(e))
                print("QUOTE_FAIL")
                return 1
            st = result["status"]
            _log(
                f"ONCE status={st} source={result.get('quote_source')} "
                f"elapsed_ms={result['elapsed_ms']}"
            )
            print(st)
            if st == "EVENTS":
                print(f"EVENTS alert=intraday_alert.md")
            write_state(
                status=st,
                last_finish=shanghai_now().strftime("%Y-%m-%d %H:%M:%S%z"),
                next_wake=None,
                elapsed_ms=result["elapsed_ms"],
                quote_source=result.get("quote_source"),
                segment=seg,
            )
            return 0 if st != "QUOTE_FAIL" else 1

        # Continuous loop
        while True:
            now = shanghai_now()
            ok, seg = allowed_now(mode, now)

            if mode == "full-day" and seg == "lunch":
                wait_lunch = seconds_until(PM_START, now)
                wake = now + timedelta(seconds=wait_lunch)
                write_state(
                    status="LUNCH_WAIT",
                    segment="lunch",
                    next_wake=wake.strftime("%Y-%m-%d %H:%M:%S%z"),
                )
                _log(f"LUNCH_WAIT {wait_lunch:.0f}s until afternoon")
                reason = sleep_interruptible(wait_lunch, stop_at=now.replace(hour=13, minute=0, second=0, microsecond=0))
                continue

            if mode == "morning" and seg != "morning":
                if seg in ("lunch", "afternoon", "post"):
                    _log("morning segment ended → exit")
                    write_state(status="ENDED", segment=seg, reason="morning_done")
                    print("SESSION_END")
                    return 0
                # pre-market: wait until open (query immediately when session starts — sleep until then)
                if seg == "pre":
                    w = seconds_until(AM_START, now)
                    wake = now + timedelta(seconds=w)
                    write_state(status="PRE_WAIT", next_wake=wake.strftime("%Y-%m-%d %H:%M:%S%z"), segment=seg)
                    _log(f"PRE_WAIT {w:.0f}s until morning open")
                    sleep_interruptible(w)
                    continue
                _log(f"not morning (seg={seg}) → exit")
                write_state(status="ENDED", segment=seg)
                return 0

            if mode == "afternoon" and seg != "afternoon":
                if seg == "post" or seg == "weekend":
                    _log("afternoon ended → exit")
                    write_state(status="ENDED", segment=seg, reason="afternoon_done")
                    print("SESSION_END")
                    return 0
                if seg in ("pre", "morning", "lunch"):
                    w = seconds_until(PM_START, now)
                    if w <= 0 and seg != "afternoon":
                        # somehow past? wait tiny
                        w = 30
                    wake = now + timedelta(seconds=w)
                    write_state(status="WAIT_AFTERNOON", next_wake=wake.strftime("%Y-%m-%d %H:%M:%S%z"), segment=seg)
                    _log(f"WAIT_AFTERNOON {w:.0f}s")
                    sleep_interruptible(w)
                    continue

            if mode == "full-day" and not ok:
                if seg in ("post", "weekend"):
                    _log("full-day ended → exit")
                    write_state(status="ENDED", segment=seg, reason="day_done")
                    print("SESSION_END")
                    return 0
                if seg == "pre":
                    w = seconds_until(AM_START, now)
                    wake = now + timedelta(seconds=w)
                    write_state(status="PRE_WAIT", next_wake=wake.strftime("%Y-%m-%d %H:%M:%S%z"), segment=seg)
                    _log(f"PRE_WAIT {w:.0f}s")
                    sleep_interruptible(w)
                    continue
                # other
                time.sleep(5)
                continue

            if not ok:
                _log(f"not in allowed session mode={mode} seg={seg} → exit")
                write_state(status="ENDED", segment=seg)
                print("SESSION_END")
                return 0

            # In session: query IMMEDIATELY (no sleep before first query)
            stop_at = segment_end(seg, now)
            try:
                result = run_one_watch(force_quote=False)
            except Exception as e:
                _log(f"ERROR {type(e).__name__}: {e}")
                traceback.print_exc()
                result = {
                    "status": "QUOTE_FAIL",
                    "elapsed_ms": 0,
                    "quote_source": "",
                }

            st = result["status"]
            finish = shanghai_now()
            _log(
                f"QUERY status={st} source={result.get('quote_source')} "
                f"elapsed_ms={result.get('elapsed_ms')} seg={seg}"
            )
            print(st)
            if st == "EVENTS":
                print("EVENTS alert=intraday_alert.md")

            # If session already ended during long pull, exit without sleep
            seg2, in2 = session_window(finish)
            if mode == "morning" and seg2 != "morning":
                write_state(status=st, last_finish=finish.strftime("%Y-%m-%d %H:%M:%S%z"), next_wake=None, segment=seg2)
                _log("morning ended after query → exit")
                print("SESSION_END")
                return 0
            if mode == "afternoon" and seg2 != "afternoon":
                write_state(status=st, last_finish=finish.strftime("%Y-%m-%d %H:%M:%S%z"), next_wake=None, segment=seg2)
                _log("afternoon ended after query → exit")
                print("SESSION_END")
                return 0
            if mode == "full-day" and seg2 in ("post", "weekend"):
                write_state(status=st, last_finish=finish.strftime("%Y-%m-%d %H:%M:%S%z"), next_wake=None, segment=seg2)
                _log("day ended after query → exit")
                print("SESSION_END")
                return 0

            wait = compute_wait_s()
            # Don't sleep past segment end meaningfully — wake at end to exit cleanly
            if stop_at:
                max_wait = (stop_at - shanghai_now()).total_seconds()
                if max_wait <= 1:
                    write_state(
                        status=st,
                        last_finish=finish.strftime("%Y-%m-%d %H:%M:%S%z"),
                        next_wake=None,
                        elapsed_ms=result.get("elapsed_ms"),
                        quote_source=result.get("quote_source"),
                        segment=seg2,
                    )
                    _log("segment end reached → exit")
                    print("SESSION_END")
                    return 0
                # For full-day morning→lunch: after morning end, next loop handles lunch wait
                wait = min(wait, max_wait + (3600 if mode == "full-day" and seg == "morning" else 0))
                # Actually: sleep only within remaining session OR allow crossing to lunch handler
                if mode == "full-day" and seg == "morning":
                    # may sleep into lunch; next iteration will LUNCH_WAIT
                    pass
                else:
                    wait = min(wait, max(1.0, max_wait))

            wake = shanghai_now() + timedelta(seconds=wait)
            write_state(
                status=st,
                last_finish=finish.strftime("%Y-%m-%d %H:%M:%S%z"),
                next_wake=wake.strftime("%Y-%m-%d %H:%M:%S%z"),
                wait_s=wait,
                elapsed_ms=result.get("elapsed_ms"),
                quote_source=result.get("quote_source"),
                segment=seg2,
            )
            _log(f"SLEEP {wait}s until {wake.strftime('%H:%M:%S')}")
            sleep_interruptible(wait, stop_at=None if mode == "full-day" else stop_at)

    finally:
        release_pid()
        _log("EXIT release pid")

    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Intraday session loop (post-completion ~3m + jitter)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--full-day", action="store_true", help="Morning + quiet lunch + afternoon")
    g.add_argument("--morning", action="store_true", help="09:30–11:30 only")
    g.add_argument("--afternoon", action="store_true", help="13:00–15:00 only")
    g.add_argument("--once", action="store_true", help="Single query, no loop")
    ap.add_argument("--force", action="store_true", help="Allow second PID / outside-session --once quote")
    args = ap.parse_args()

    if args.full_day:
        mode = "full-day"
    elif args.morning:
        mode = "morning"
    elif args.afternoon:
        mode = "afternoon"
    elif args.once:
        mode = "once"
    else:
        # default: whichever session is active, else refuse
        seg, in_sess = session_window()
        if in_sess:
            mode = "morning" if seg == "morning" else "afternoon"
        else:
            mode = "once"
            args.force = args.force  # keep
            _log(f"no flag + outside session → treat as --once (seg={seg})")

    sys.exit(run_loop(mode, force=args.force))


if __name__ == "__main__":
    main()
