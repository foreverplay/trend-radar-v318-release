#!/usr/bin/env python3
"""Pre-warm V3.1.7 snapshot cache (speed only; no outputs, no strategy effect).

Replays candidates from candidates_v317.json (or candidates_v3.json) that are NOT
already in the V3.1.7 scope, so a later close run only computes the new day.

  python -m v3.v31.prewarm_v317 --t-date YYYY-MM-DD --grades WATCH,FILTER --workers 3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v3.publish_close_v317 import ROOT, run_v317_layer
from v3.v31.live_v317 import DEFAULT_WARMUP


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--t-date", required=True)
    ap.add_argument("--grades", default="WATCH")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    a = ap.parse_args()
    p = ROOT / "candidates_v317.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if str(d.get("t_date")) != a.t_date:
        raise SystemExit(f"candidates_v317 t_date={d.get('t_date')} != {a.t_date}")
    grades = {g.strip() for g in a.grades.split(",") if g.strip()}
    rows = [r for r in d["results"] if not r.get("evaluated") and r.get("plan_grade") in grades]
    rows.sort(key=lambda r: (-(r.get("continuity_score") or 0), -(r.get("tech_score") or 0)))
    items = [(r["code"], r.get("name") or "") for r in rows]
    print(f"prewarm {len(items)} symbols T={a.t_date} workers={a.workers}", flush=True)
    res = run_v317_layer(items, a.t_date, warmup=a.warmup, workers=a.workers)
    print(f"done ok={sum(1 for v in res.values() if v.get('ok'))}/{len(res)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
