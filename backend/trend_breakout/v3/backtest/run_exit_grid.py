"""
Phase 10+11 CLI — exit strategy grid + Walk-Forward (2026YTD_proxy) + ExitQualityScore.

Writes:
  v3/backtest/results/exit_grid_2026proxy/rankings.csv
  v3/backtest/results/exit_grid_2026proxy/summary.md
  v3/backtest/results/exit_grid_2026proxy/default_exit_choice.json

Then (unless --no-apply-config) updates TREND_BOT_CONFIG exit/backtest default
from the measured winner.

Does NOT touch v2_3_scan / v2_recommend / v2_staircase_scan / live routines.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.backtest.engine import BacktestEngine  # noqa: E402
from v3.backtest.exit_quality import (  # noqa: E402
    MAX_DD_ELIMINATE,
    WEIGHTS,
    combine_window_scores,
    exit_quality_score,
)
from v3.backtest.strategies import (  # noqa: E402
    BreakoutSupportExit,
    DynamicProtectionExit,
    DynamicProtectionMaxDD,
    FixedPct,
    MA20,
    MA20_ATR,
    StructureSupportATR,
    SwingLow,
    build_plan18_grid,
)
from v3.backtest.walk_forward import (  # noqa: E402
    DEFERRED_NOTE,
    WF_WINDOW_LABEL,
    default_wf_spec,
    filter_entries_for_split,
    sample_legacy_entries,
)
from v3.config import TREND_BOT_CONFIG  # noqa: E402

RESULTS_DIR = _ROOT / "v3" / "backtest" / "results" / "exit_grid_2026proxy"
LEGACY_A_PLUS = _ROOT / "history" / "a_plus_daily_2026.parquet"
CONFIG_PATH = _ROOT / "v3" / "config.py"


def _shanghai_now_iso() -> str:
    # box clock is Asia/Shanghai
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _strategy_base_and_params(strat: Any) -> Tuple[str, Dict[str, Any]]:
    name = getattr(strat, "name", type(strat).__name__)
    # strip _cN suffix for base
    base = re.sub(r"_c\d+$", "", name)
    params: Dict[str, Any] = {}
    if hasattr(strat, "pct"):
        params["pct"] = float(strat.pct)
    if hasattr(strat, "atr_mult"):
        params["atr_mult"] = float(strat.atr_mult)
    if hasattr(strat, "max_dd"):
        params["max_dd"] = float(strat.max_dd)
    if hasattr(strat, "lookback"):
        params["lookback"] = int(strat.lookback)
    if hasattr(strat, "confirm_days"):
        params["confirm_days"] = int(strat.confirm_days)
    return base, params


def _agg_row(prefix: str, agg: Dict[str, Any], eq: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        f"{prefix}_n_closed": agg.get("n_closed"),
        f"{prefix}_mean_return": agg.get("mean_return"),
        f"{prefix}_median_return": agg.get("median_return"),
        f"{prefix}_win_rate": agg.get("win_rate"),
        f"{prefix}_mean_capture": agg.get("mean_capture"),
        f"{prefix}_mean_giveback": agg.get("mean_giveback"),
        f"{prefix}_max_dd": agg.get("max_dd"),
        f"{prefix}_avg_hold": agg.get("avg_hold_days"),
        f"{prefix}_early_exit_rate": agg.get("early_exit_rate"),
        f"{prefix}_eq_score": eq.get("score"),
        f"{prefix}_eliminated": eq.get("eliminated"),
        f"{prefix}_elim_reason": eq.get("eliminate_reason"),
    }
    return out


def run_grid(
    *,
    top_n_per_day: int = 5,
    max_entries: int = 300,
    confirm_days_list: Optional[List[int]] = None,
    out_dir: Path = RESULTS_DIR,
    apply_config: bool = True,
) -> Dict[str, Any]:
    t0 = time.time()
    confirm_days_list = list(confirm_days_list or [0, 1])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wf = default_wf_spec()
    full_start = wf.splits["train"].start
    full_end = wf.splits["test"].end

    entries, sample_meta = sample_legacy_entries(
        str(LEGACY_A_PLUS),
        start=full_start,
        end=full_end,
        top_n_per_day=top_n_per_day,
        max_entries=max_entries,
    )
    sample_meta["wf_window"] = wf.label

    engine = BacktestEngine()
    rows: List[Dict[str, Any]] = []
    skipped: List[str] = []

    # Build strategy factories per confirm_days
    for cdays in confirm_days_list:
        grid = build_plan18_grid(confirm_days=cdays)
        for strat in grid:
            base_name, params = _strategy_base_and_params(strat)
            row: Dict[str, Any] = {
                "name": base_name,
                "strategy_id": getattr(strat, "name", base_name),
                "confirm_days": int(cdays),
                "params_json": json.dumps(params, ensure_ascii=False),
                "wf_window": wf.label,
            }
            window_eq: Dict[str, Dict[str, Any]] = {}
            window_agg: Dict[str, Dict[str, Any]] = {}
            try:
                for wname, split in wf.splits.items():
                    w_entries = filter_entries_for_split(entries, split)
                    # clone strategy fresh per window (stateful confirm / ExitEngine)
                    strat_w = _rehydrate(base_name, params, cdays)
                    result = engine.run(
                        entries=w_entries,
                        strategy=strat_w,
                        start=split.start,
                        end=split.end,
                        entry_source="legacy_rec_score",
                    )
                    agg = dict(result.aggregate or {})
                    rets = [
                        float(t.exit_gain)
                        for t in result.trades
                        if t.exit_gain is not None
                        and t.status in ("closed", "force_close")
                    ]
                    eq = exit_quality_score(agg, trade_returns=rets)
                    agg["equity_max_dd"] = eq.get("equity_max_dd")
                    window_agg[wname] = agg
                    window_eq[wname] = eq
                    row.update(_agg_row(wname, agg, eq))
                    row[f"{wname}_equity_max_dd"] = eq.get("equity_max_dd")
                    row[f"{wname}_n_entries"] = len(w_entries)
                    row[f"{wname}_empty_reason"] = result.empty_reason
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{base_name}_c{cdays}: {type(exc).__name__}: {exc}")
                row["error"] = f"{type(exc).__name__}: {exc}"
                rows.append(row)
                continue

            combo = combine_window_scores(window_eq.get("train"), window_eq.get("val"))
            row["selection_score"] = combo.get("selection_score")
            row["selection_eliminated"] = combo.get("eliminated")
            row["selection_elim_reason"] = combo.get("eliminate_reason")
            # test EQ already in row
            rows.append(row)
            print(
                f"[grid] {row['strategy_id']}: sel={row.get('selection_score')} "
                f"train_eq={row.get('train_eq_score')} val_eq={row.get('val_eq_score')} "
                f"test_eq={row.get('test_eq_score')} "
                f"maxdd_val={row.get('val_max_dd')}",
                flush=True,
            )

    df = pd.DataFrame(rows)
    if len(df) == 0:
        raise RuntimeError("exit grid produced zero rows")

    # Rank: selection_score desc; eliminated last
    df["_rank_key"] = df["selection_score"].fillna(-1e9)
    df.loc[df["selection_eliminated"] == True, "_rank_key"] = -1e9  # noqa: E712
    df = df.sort_values("_rank_key", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    df = df.drop(columns=["_rank_key"])

    rankings_path = out_dir / "rankings.csv"
    df.to_csv(rankings_path, index=False)

    # Winner = first non-eliminated with selection_score
    winner_row = None
    for _, r in df.iterrows():
        if not bool(r.get("selection_eliminated")) and r.get("selection_score") is not None:
            winner_row = r
            break
    if winner_row is None:
        # fallback: best train_eq among non-hard-error
        cand = df[df["train_eq_score"].notna()].sort_values("train_eq_score", ascending=False)
        if len(cand):
            winner_row = cand.iloc[0]
            fallback_note = "fallback: no Val-eligible winner; used best train_eq_score"
        else:
            fallback_note = "no winner"
    else:
        fallback_note = None

    generated_at = _shanghai_now_iso()
    runtime_s = time.time() - t0

    choice: Dict[str, Any]
    if winner_row is not None:
        params = json.loads(winner_row["params_json"]) if isinstance(winner_row["params_json"], str) else {}
        choice = {
            "name": str(winner_row["name"]),
            "strategy_id": str(winner_row["strategy_id"]),
            "params": params,
            "confirm_days": int(winner_row["confirm_days"]),
            "scores": {
                "selection_score": _num(winner_row.get("selection_score")),
                "train_eq_score": _num(winner_row.get("train_eq_score")),
                "val_eq_score": _num(winner_row.get("val_eq_score")),
                "test_eq_score": _num(winner_row.get("test_eq_score")),
                "train_mean_return": _num(winner_row.get("train_mean_return")),
                "val_mean_return": _num(winner_row.get("val_mean_return")),
                "test_mean_return": _num(winner_row.get("test_mean_return")),
                "train_max_dd": _num(winner_row.get("train_max_dd")),
                "val_max_dd": _num(winner_row.get("val_max_dd")),
                "test_max_dd": _num(winner_row.get("test_max_dd")),
            },
            "wf_window": wf.label,
            "entry_sample": sample_meta,
            "exit_quality_weights": WEIGHTS,
            "max_dd_eliminate": MAX_DD_ELIMINATE,
            "generated_at": generated_at,
            "runtime_seconds": round(runtime_s, 1),
            "fallback_note": fallback_note,
            "deferred_full_wf": DEFERRED_NOTE,
        }
    else:
        choice = {
            "name": None,
            "error": "no_winner",
            "generated_at": generated_at,
            "runtime_seconds": round(runtime_s, 1),
            "deferred_full_wf": DEFERRED_NOTE,
        }

    choice_path = out_dir / "default_exit_choice.json"
    choice_path.write_text(json.dumps(choice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = _render_summary(
        df=df,
        choice=choice,
        sample_meta=sample_meta,
        wf=wf,
        skipped=skipped,
        runtime_s=runtime_s,
        confirm_days_list=confirm_days_list,
    )
    summary_path = out_dir / "summary.md"
    summary_path.write_text(summary, encoding="utf-8")

    if apply_config and choice.get("name"):
        _apply_config_winner(choice)

    return {
        "rankings_path": str(rankings_path),
        "summary_path": str(summary_path),
        "choice_path": str(choice_path),
        "choice": choice,
        "n_strategies": len(df),
        "n_entries": sample_meta.get("n_entries"),
        "runtime_seconds": runtime_s,
        "skipped": skipped,
    }


def _num(x: Any) -> Optional[float]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _rehydrate(base_name: str, params: Dict[str, Any], confirm_days: int) -> Any:
    """Rebuild a fresh strategy instance from base name + params."""
    c = int(confirm_days)
    p = dict(params or {})
    p["confirm_days"] = c
    mapping = {
        "Fixed5%": lambda: FixedPct(0.05, confirm_days=c),
        "Fixed8%": lambda: FixedPct(0.08, confirm_days=c),
        "Fixed10%": lambda: FixedPct(0.10, confirm_days=c),
        "Fixed15%": lambda: FixedPct(0.15, confirm_days=c),
        "MA20": lambda: MA20(confirm_days=c),
        "MA20-0.5ATR": lambda: MA20_ATR(atr_mult=0.5, confirm_days=c),
        "MA20-1ATR": lambda: MA20_ATR(atr_mult=1.0, confirm_days=c),
        "SwingLow": lambda: SwingLow(atr_mult=0.0, confirm_days=c),
        "SwingLow-0.5ATR": lambda: SwingLow(atr_mult=0.5, confirm_days=c),
        "SwingLow-1ATR": lambda: SwingLow(atr_mult=1.0, confirm_days=c),
        "BreakoutSupport": lambda: BreakoutSupportExit(atr_mult=0.0, confirm_days=c),
        "BreakoutSupport-0.5ATR": lambda: BreakoutSupportExit(atr_mult=0.5, confirm_days=c),
        "BreakoutSupport-1ATR": lambda: BreakoutSupportExit(atr_mult=1.0, confirm_days=c),
        "StructureSupport-0.5ATR": lambda: StructureSupportATR(atr_mult=0.5, confirm_days=c),
        "DynamicProtection": lambda: DynamicProtectionExit(confirm_days=c),
        "DynamicProtection+MaxDrawdown": lambda: DynamicProtectionMaxDD(
            max_dd=p.get("max_dd"), confirm_days=c
        ),
    }
    # FixedPct names from instance
    if base_name.startswith("Fixed") and base_name.endswith("%"):
        if base_name in mapping:
            return mapping[base_name]()
    if base_name in mapping:
        return mapping[base_name]()
    # fuzzy
    for k, fn in mapping.items():
        if k.replace("+", "") == base_name.replace("+", ""):
            return fn()
    raise KeyError(f"cannot rehydrate strategy {base_name!r}")


def _render_summary(
    *,
    df: pd.DataFrame,
    choice: Dict[str, Any],
    sample_meta: Dict[str, Any],
    wf: Any,
    skipped: List[str],
    runtime_s: float,
    confirm_days_list: List[int],
) -> str:
    lines: List[str] = []
    lines.append("# Exit Grid + Walk-Forward — Phase 10/11 (2026YTD_proxy)")
    lines.append("")
    lines.append("> 观察 / 退出结构研究。**不进入 live 选股。禁止荐买 / 预测必涨。**")
    lines.append("")
    lines.append("## Data reality / 数据现实")
    lines.append("")
    lines.append(f"- **wf_window**: `{wf.label}` — **NOT** Plan 2019–2025 ideal")
    lines.append(f"- {DEFERRED_NOTE}")
    lines.append(
        f"- Entries: `{sample_meta.get('entry_source')}` — {sample_meta.get('sampling_rule')}"
    )
    lines.append(
        f"- n_entries={sample_meta.get('n_entries')} "
        f"(top_n_per_day={sample_meta.get('top_n_per_day')}, "
        f"max={sample_meta.get('max_entries')})"
    )
    lines.append("")
    lines.append("## WF splits (trading calendar)")
    lines.append("")
    for k, sp in wf.splits.items():
        lines.append(f"- **{k}**: {sp.start} → {sp.end}")
    lines.append("")
    lines.append("## ExitQualityScore (§24 proxy)")
    lines.append("")
    lines.append(f"- Weights: `{json.dumps(WEIGHTS)}`")
    lines.append(f"- Hard eliminate if MaxDD > {MAX_DD_ELIMINATE:.0%}")
    lines.append("- Selection = 0.4×Train + 0.6×Val EQ; Val MaxDD>20% hard eliminate; Train overage soft-penalize (×0.7)")
    lines.append("")
    lines.append(f"- confirm_days tested: {confirm_days_list}")
    lines.append(f"- runtime: **{runtime_s/60:.1f} min** ({runtime_s:.0f}s)")
    lines.append("")
    lines.append("## Winner → DEFAULT_EXIT_STRATEGY")
    lines.append("")
    if choice.get("name"):
        lines.append(f"- **name**: `{choice['name']}`")
        lines.append(f"- **strategy_id**: `{choice.get('strategy_id')}`")
        lines.append(f"- **params**: `{json.dumps(choice.get('params'), ensure_ascii=False)}`")
        lines.append(f"- **confirm_days**: {choice.get('confirm_days')}")
        sc = choice.get("scores") or {}
        lines.append(
            f"- scores: selection={sc.get('selection_score')}, "
            f"train={sc.get('train_eq_score')}, val={sc.get('val_eq_score')}, "
            f"test={sc.get('test_eq_score')}"
        )
        lines.append(
            f"- mean_return train/val/test: "
            f"{sc.get('train_mean_return')}, {sc.get('val_mean_return')}, {sc.get('test_mean_return')}"
        )
        lines.append(
            f"- max_dd train/val/test: "
            f"{sc.get('train_max_dd')}, {sc.get('val_max_dd')}, {sc.get('test_max_dd')}"
        )
        if choice.get("fallback_note"):
            lines.append(f"- ⚠ fallback: {choice['fallback_note']}")
    else:
        lines.append("- **no winner**")
    lines.append("")
    lines.append("## Top 10 rankings (by selection_score)")
    lines.append("")
    cols = [
        "rank",
        "name",
        "confirm_days",
        "selection_score",
        "train_eq_score",
        "val_eq_score",
        "test_eq_score",
        "train_mean_return",
        "val_mean_return",
        "test_mean_return",
        "val_max_dd",
        "test_max_dd",
        "selection_eliminated",
    ]
    show = df[[c for c in cols if c in df.columns]].head(10)

    def fmt(v: Any) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for _, r in show.iterrows():
        lines.append("| " + " | ".join(fmt(r.get(c)) for c in cols) + " |")
    lines.append("")
    if skipped:
        lines.append("## Skips / errors")
        lines.append("")
        for s in skipped:
            lines.append(f"- {s}")
        lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("1. 2026YTD proxy only — short sample, regime single-year bias.")
    lines.append("2. Entries = legacy RecScore A+, **not** Plan-grade A+ matrix.")
    lines.append("3. BreakoutSupport uses OHLCV proxy (no TechnicalEngine platform).")
    lines.append("4. V2.3 live path untouched; backtest does not feed selection.")
    lines.append("5. Full 2019–2025 Plan A+ WF deferred until archives exist.")
    lines.append("")
    lines.append(f"_generated_at: {choice.get('generated_at')}_")
    lines.append("")
    return "\n".join(lines)


def _apply_config_winner(choice: Dict[str, Any]) -> None:
    """Patch v3/config.py TREND_BOT_CONFIG phase + default_strategy from winner."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    name = str(choice["name"])
    cdays = int(choice.get("confirm_days") or 0)
    # phase → 11
    text2, n1 = re.subn(r'"phase":\s*\d+', '"phase": 11', text, count=1)
    if n1 == 0:
        text2 = text
    # ensure exit.default_strategy
    if '"default_strategy"' in text2:
        text2 = re.sub(
            r'("default_strategy":\s*)"[^"]*"',
            rf'\1"{name}"',
            text2,
            count=2,  # exit + backtest if both exist
        )
    # inject exit.default_strategy if missing inside exit block
    if "default_strategy" not in (
        re.search(r'"exit"\s*:\s*\{(.*?)\n    \},', text2, re.S) or [None, ""]
    )[1]:
        text2 = text2.replace(
            '    "exit": {\n',
            f'    "exit": {{\n        "default_strategy": "{name}",\n',
            1,
        )
    # backtest.default_strategy
    if re.search(r'"backtest"\s*:\s*\{[^}]*"default_strategy"', text2, re.S):
        pass  # already subbed via count=2
    else:
        text2 = text2.replace(
            '    "backtest": {\n',
            f'    "backtest": {{\n        "default_strategy": "{name}",\n',
            1,
        )
    # confirm_days in exit
    text2 = re.sub(
        r'("confirm_days":\s*)\d+',
        rf"\g<1>{cdays}",
        text2,
        count=1,
    )
    # refresh comment on backtest phase
    text2 = text2.replace(
        "# --- backtest (Phase 9; ExitEngine validation only — NOT live selection) ---",
        "# --- backtest (Phase 11; DEFAULT from exit grid — NOT live selection) ---",
    )
    text2 = text2.replace(
        "# Phase 10 will expand strategy grid + Walk-Forward/OOS",
        "# Phase 10/11: strategy grid + WF done; default_strategy from measured winner",
    )
    CONFIG_PATH.write_text(text2, encoding="utf-8")
    # also update in-memory
    TREND_BOT_CONFIG["phase"] = 11
    TREND_BOT_CONFIG.setdefault("exit", {})["default_strategy"] = name
    TREND_BOT_CONFIG["exit"]["confirm_days"] = cdays
    TREND_BOT_CONFIG.setdefault("backtest", {})["default_strategy"] = name


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Phase 10/11 exit strategy grid + WF")
    p.add_argument("--top-n-per-day", type=int, default=5)
    p.add_argument("--max-entries", type=int, default=300)
    p.add_argument(
        "--confirm-days",
        type=str,
        default="0,1,2",
        help="comma-separated confirm_days list",
    )
    p.add_argument("--out-dir", type=str, default=str(RESULTS_DIR))
    p.add_argument("--no-apply-config", action="store_true")
    p.add_argument(
        "--smoke",
        action="store_true",
        help="tiny run: max_entries=20, confirm_days=0 only",
    )
    args = p.parse_args(argv)
    if args.smoke:
        args.max_entries = 20
        args.confirm_days = "0"
        args.top_n_per_day = 2
    cdays = [int(x.strip()) for x in str(args.confirm_days).split(",") if x.strip() != ""]
    info = run_grid(
        top_n_per_day=args.top_n_per_day,
        max_entries=args.max_entries,
        confirm_days_list=cdays,
        out_dir=Path(args.out_dir),
        apply_config=not args.no_apply_config,
    )
    print(json.dumps({k: v for k, v in info.items() if k != "choice"}, ensure_ascii=False, indent=2))
    print("WINNER:", json.dumps(info.get("choice"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
