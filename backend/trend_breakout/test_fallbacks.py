#!/usr/bin/env python3
"""Smoke test: which backup source succeeded for key market fields."""
import json
from pathlib import Path
import data_sources as ds

OUT = _PKG_ROOT
status = {"t_date": None, "results": {}, "notes": []}

td = ds.detect_t_date()
T_DATE = td["value"]["t_date"]
status["t_date"] = T_DATE
status["t_date_source"] = td["source"]
print(f"=== T_DATE={T_DATE} ({td['source']}) ===")

# Spot
print("\n--- spot ---")
sp = ds.fetch_spot_universe()
status["results"]["spot_rows"] = {
    "ok": sp["ok"],
    "source": sp.get("source"),
    "rows": int(len(sp["value"])) if sp["ok"] else 0,
    "errors": (sp.get("errors") or [])[:3],
}
print(status["results"]["spot_rows"])
spot = sp["value"] if sp["ok"] else None

# Turnover T
print("\n--- turnover_T ---")
to = ds.fetch_turnover_t(T_DATE, spot=spot)
hist_series = (to.get("value") or {}).get("hist_series") if to["ok"] else None
status["results"]["turnover_T"] = {
    "ok": to["ok"],
    "source": to.get("source"),
    "amt_yi": (to.get("value") or {}).get("amt_yi") if to["ok"] else None,
    "errors": (to.get("errors") or [])[:6],
}
print(status["results"]["turnover_T"])

# Turnover hist / MA
print("\n--- turnover_hist ---")
ma = ds.fetch_turnover_ma(T_DATE, amt_t=(to.get("value") or {}).get("amt_yi") if to["ok"] else None, hist_series=hist_series)
status["results"]["turnover_hist_len"] = {
    "ok": ma["ok"],
    "source": ma.get("source"),
    "hist_len": (ma.get("value") or {}).get("hist_len") if ma["ok"] else 0,
    "ma5": (ma.get("value") or {}).get("ma5") if ma["ok"] else None,
    "ma20": (ma.get("value") or {}).get("ma20") if ma["ok"] else None,
    "amt_yi": (ma.get("value") or {}).get("amt_yi") if ma["ok"] else None,
    "gap_flags": (ma.get("value") or {}).get("gap_flags") if ma["ok"] else [],
    "errors": (ma.get("errors") or [])[:4],
}
print(status["results"]["turnover_hist_len"])

# zt / dt
print("\n--- zt/dt ---")
br = ds.fetch_breadth_zt_dt(T_DATE, spot=spot)
status["results"]["zt"] = {
    "ok": br["ok"],
    "source": (br.get("value") or {}).get("zt_source") if br["ok"] else None,
    "value": (br.get("value") or {}).get("zt") if br["ok"] else None,
}
status["results"]["dt"] = {
    "ok": br["ok"],
    "source": (br.get("value") or {}).get("dt_source") if br["ok"] else None,
    "value": (br.get("value") or {}).get("dt") if br["ok"] else None,
    "all_sources": (br.get("value") or {}).get("sources") if br["ok"] else {},
}
status["results"]["breadth"] = {
    "up": (br.get("value") or {}).get("up"),
    "down": (br.get("value") or {}).get("down"),
    "combo_source": br.get("source"),
    "errors": (br.get("errors") or [])[:5],
}
print("zt", status["results"]["zt"])
print("dt", status["results"]["dt"])
print("breadth", status["results"]["breadth"])

# index hist
print("\n--- index hist sh000300 ---")
ih = ds.fetch_index_hist("sh000300")
status["results"]["index_hist"] = {
    "ok": ih["ok"],
    "source": ih.get("source"),
    "rows": int(len(ih["value"])) if ih["ok"] else 0,
    "errors": (ih.get("errors") or [])[:3],
}
print(status["results"]["index_hist"])

# stock hist 301072
print("\n--- stock hist 301072 ---")
sh = ds.fetch_stock_hist("sz301072", start="20250101", end=T_DATE.replace("-", ""))
status["results"]["stock_hist"] = {
    "ok": sh["ok"],
    "source": sh.get("source"),
    "rows": int(len(sh["value"])) if sh["ok"] else 0,
    "errors": (sh.get("errors") or [])[:4],
}
print(status["results"]["stock_hist"])

# industry 301072
print("\n--- industry 301072 ---")
ind = ds.fetch_industry("301072")
status["results"]["industry_301072"] = {
    "ok": ind["ok"],
    "source": ind.get("source"),
    "value": ind.get("value"),
    "errors": (ind.get("errors") or [])[:4],
}
print(status["results"]["industry_301072"])

# Compare vs old meta if present
old_path = OUT / "market_meta.json"
if old_path.exists():
    try:
        old = json.loads(old_path.read_text())
        status["old_meta_snapshot"] = {
            "t_date": old.get("t_date"),
            "turnover_amt_yi": (old.get("turnover") or {}).get("amt_yi"),
            "turnover_ma5": (old.get("turnover") or {}).get("ma5"),
            "turnover_ma20": (old.get("turnover") or {}).get("ma20"),
            "dt": (old.get("breadth") or {}).get("dt"),
            "zt": (old.get("breadth") or {}).get("zt"),
            "data_gaps_sample": (old.get("data_gaps") or [])[:5],
        }
    except Exception as e:
        status["notes"].append(f"old meta read: {e}")

# Success criteria helpers
turnover_backup_ok = bool(to.get("ok")) and to.get("source") not in (None, "")
# specifically: if official failed, backup still ok
official_failed = any("sse" in e.lower() or "szse" in e.lower() or "ValueError" in e or "BadZip" in e for e in (to.get("errors") or []))
status["proof"] = {
    "turnover_any_ok": turnover_backup_ok,
    "turnover_used_backup": turnover_backup_ok and "sse_deal_daily+szse_summary" not in (to.get("source") or ""),
    "official_had_errors": official_failed,
    "dt_has_source_beyond_silent_zero": bool((br.get("value") or {}).get("dt_source") not in (None, "none")),
    "dt_sources_tried": list(((br.get("value") or {}).get("sources") or {}).keys()),
    "hist_len_ge_5": ((ma.get("value") or {}).get("hist_len") or 0) >= 5,
}

out_path = OUT / "fallback_status.json"
out_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, default=str))
print("\n=== wrote", out_path, "===")
print(json.dumps(status["proof"], ensure_ascii=False, indent=2))
