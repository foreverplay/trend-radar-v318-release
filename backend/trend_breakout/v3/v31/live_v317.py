"""
V3.1.7 live as-of layer for the close publish (publish_close_v317).

For one symbol and one T, replay the *same* V3.1.7 per-day pipeline used by
`v3.v31.regression_v317.replay_symbol` over a warm-up window that ends at T,
and return the T-day row (TrendState / Maturity / RealizedTrend /
FollowThrough / EntryWindow / Exhaustion / FundRisk ...).

Hard rules:
  * hist is truncated to date <= T before anything runs (no future bars).
  * no symbol/date branches; thresholds come from v3.v31.config V317_*.
  * MajorMoveLabel results from replay_symbol are discarded (label-only).
  * continuity history is NOT persisted during replay (read-only), so the
    live V2.3 ContinuityDelta store is not polluted by warm-up days.

Caches (pure speed, invalidated automatically):
  * v3/v31/hist_cache/{code6}.parquet  local daily + remote bars up to T
  * v3/v31/snap_cache/{code6}.pkl      FeatureSnapshot per date, keyed by
    (date, close-at-date, bars-count-at-date); a qfq re-adjust changes close
    and drops the entry.

Observation only. 禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import contextlib
import io
import pickle
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
from typing import Any, Dict, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DAILY = _PKG_LOCAL_DAILY
HIST_CACHE = ROOT / "v3" / "v31" / "hist_cache"
SNAP_CACHE = ROOT / "v3" / "v31" / "snap_cache"
BASE_COLS = ["date", "open", "high", "low", "close", "volume", "amount"]

# Warm-up trading days replayed before T (state machines start cold).
DEFAULT_WARMUP = 160


def _full(code6: str) -> str:
    return ("sh" if code6.startswith(("5", "6", "9")) else "sz") + code6


def _read_local(code6: str) -> Optional[pd.DataFrame]:
    p = LOCAL_DAILY / f"{code6}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if "date" not in df.columns or df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_hist_to_t(code6: str, t_date: str, *, allow_remote: bool = True) -> Tuple[Optional[pd.DataFrame], str]:
    """Daily bars with date <= T. Local parquet first; stale → remote fill (cached).

    Returns (df, source). df is None when no bar on T can be obtained.
    """
    code6 = str(code6).zfill(6)
    T = pd.Timestamp(t_date)
    HIST_CACHE.mkdir(parents=True, exist_ok=True)

    local = _read_local(code6)
    if local is not None and local["date"].iloc[-1] >= T:
        return local[local["date"] <= T].reset_index(drop=True), "local"

    cp = HIST_CACHE / f"{code6}.parquet"
    if cp.exists():
        try:
            c = pd.read_parquet(cp)
            c["date"] = pd.to_datetime(c["date"])
            if c["date"].iloc[-1] >= T and (T in set(c["date"])):
                return c[c["date"] <= T].reset_index(drop=True), "hist_cache"
        except Exception:
            pass

    if not allow_remote:
        return (local[local["date"] <= T].reset_index(drop=True) if local is not None else None), "local_stale"

    try:
        from v2_staircase_scan import ensure_hist_to_t  # live scan's fill chain
    except Exception:
        return (local if local is not None else None), "local_stale"

    start_ts = (local["date"].iloc[-1] - pd.Timedelta(days=20)) if local is not None else (T - pd.Timedelta(days=1100))
    # NOTE: no redirect_stdout here — this runs in threads and redirect_stdout is process-global
    rdf, src, _errs = ensure_hist_to_t(_full(code6), t_date, start=start_ts.strftime("%Y%m%d"))
    if rdf is None or rdf.empty:
        return (local[local["date"] <= T].reset_index(drop=True) if local is not None else None), "local_stale"
    rdf = rdf.copy()
    rdf["date"] = pd.to_datetime(rdf["date"])
    rdf = rdf[rdf["date"] <= T].sort_values("date").reset_index(drop=True)

    merged: Optional[pd.DataFrame] = None
    if local is not None:
        ov = local.merge(rdf[["date", "close"]], on="date", suffixes=("", "_r"))
        same_adj = len(ov) > 0 and float(
            ((ov["close"] - ov["close_r"]).abs() / ov["close"].abs().clip(lower=1e-9)).max()
        ) < 0.005
        if same_adj:
            new = rdf[rdf["date"] > local["date"].iloc[-1]]
            merged = pd.concat([local, new], ignore_index=True, sort=False)
    if merged is None:
        # adjustment mismatch (qfq re-based) or no local: refetch a long remote window
        rdf2, src, _errs = ensure_hist_to_t(_full(code6), t_date, start=(T - pd.Timedelta(days=1100)).strftime("%Y%m%d"))
        if rdf2 is None or rdf2.empty:
            return None, "unavailable"
        rdf2 = rdf2.copy()
        rdf2["date"] = pd.to_datetime(rdf2["date"])
        merged = rdf2[[c for c in BASE_COLS if c in rdf2.columns]]
    merged = merged[merged["date"] <= T].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if merged.empty or merged["date"].iloc[-1] != T:
        return merged if not merged.empty else None, "no_bar_on_T"
    try:
        merged.to_parquet(cp, index=False)
    except Exception:
        pass
    return merged, f"remote_fill:{src}"


class _NoPersistContinuity:
    """Wrap ContinuityEngine so replay never writes the live delta history."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def evaluate(self, *a: Any, **kw: Any) -> Any:
        kw["persist"] = False
        return self._inner.evaluate(*a, **kw)

    def __getattr__(self, k: str) -> Any:
        return getattr(self._inner, k)


def make_builder():
    from v3.v31.feature_snapshot import FeatureSnapshotBuilder

    class _Builder(FeatureSnapshotBuilder):
        def __init__(self) -> None:
            super().__init__()
            self.cont_eng = _NoPersistContinuity(self.cont_eng)
            self._memo: Dict[str, Dict[str, Any]] = {}
            self._dirty: set = set()

        def _load_memo(self, code6: str) -> Dict[str, Any]:
            if code6 not in self._memo:
                p = SNAP_CACHE / f"{code6}.pkl"
                d: Dict[str, Any] = {}
                if p.exists():
                    try:
                        d = pickle.loads(p.read_bytes())
                    except Exception:
                        d = {}
                self._memo[code6] = d
            return self._memo[code6]

        def build(self, *, code6, name, hist, t_date):  # type: ignore[override]
            code6 = str(code6).zfill(6)
            td = str(t_date)[:10]
            sub = hist[hist["date"] <= pd.Timestamp(td)]
            key = None
            if len(sub):
                key = (round(float(sub["close"].iloc[-1]), 4), int(len(sub)), str(sub["date"].iloc[-1].date()))
            memo = self._load_memo(code6)
            hit = memo.get(td)
            if key is not None and hit is not None and hit[0] == key:
                return hit[1]
            snap = super().build(code6=code6, name=name, hist=hist, t_date=td)
            if key is not None and snap.ok:
                memo[td] = (key, snap)
                self._dirty.add(code6)
            return snap

        def flush(self) -> None:
            SNAP_CACHE.mkdir(parents=True, exist_ok=True)
            for c in list(self._dirty):
                try:
                    (SNAP_CACHE / f"{c}.pkl").write_bytes(pickle.dumps(self._memo[c]))
                except Exception:
                    pass
            self._dirty.clear()

    return _Builder()


def evaluate_v317_asof(
    code6: str,
    name: str,
    t_date: str,
    *,
    hist: Optional[pd.DataFrame] = None,
    builder: Any = None,
    warmup: int = DEFAULT_WARMUP,
    allow_remote: bool = True,
) -> Dict[str, Any]:
    """Return the V3.1.7 row for T (keys as in regression_v317 rows) + meta."""
    from v3.v31.regression_v317 import replay_symbol

    code6 = str(code6).zfill(6)
    src = "given"
    if hist is None:
        hist, src = load_hist_to_t(code6, t_date, allow_remote=allow_remote)
    out: Dict[str, Any] = {"code6": code6, "t_date": t_date, "hist_source": src, "ok": False}
    if hist is None or hist.empty:
        out["error"] = "no_hist"
        return out
    h = hist.copy()
    h["date"] = pd.to_datetime(h["date"])
    h = h[h["date"] <= pd.Timestamp(t_date)].sort_values("date").reset_index(drop=True)  # no future bars
    if h.empty or str(h["date"].iloc[-1].date()) != str(t_date)[:10]:
        out["error"] = f"no_bar_on_T(last={str(h['date'].iloc[-1].date()) if len(h) else None})"
        return out
    start_idx = max(0, len(h) - int(warmup))
    start = str(h["date"].iloc[start_idx].date())
    own_builder = builder is None
    if own_builder:
        builder = make_builder()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = replay_symbol(code6, name, h, start, str(t_date)[:10], builder)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"replay:{type(e).__name__}:{e}"
        return out
    finally:
        if hasattr(builder, "flush"):
            builder.flush()
    rows = res.get("rows") or []
    last = rows[-1] if rows else None
    if not last or last.get("t_date") != str(t_date)[:10]:
        out["error"] = "no_row_on_T"
        return out
    out.update({k: v for k, v in last.items()})
    out["ok"] = True
    out["warmup_start"] = start
    out["warmup_days"] = len(rows)
    # replay rising-edge EntrySignal on T (snapshot inputs); publish recomputes level with scan scores
    out["entry_signal_replay_edge"] = bool(last.get("entry_signal_current"))
    return out


__all__ = ["evaluate_v317_asof", "load_hist_to_t", "make_builder", "DEFAULT_WARMUP"]
