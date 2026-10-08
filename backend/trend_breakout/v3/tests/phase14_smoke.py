"""
Phase 14 regression smoke — import path + bot on 3 codes.

Asserts:
  - exit prices filled (protection + break) when data is present
  - grade ∈ Grade enum
  - DEFAULT_EXIT / exit.default_strategy present in config

Fails hard on regression. Does NOT touch live V2.3 push routines.
禁止荐买 / 预测必涨.

Run:
  cd .
  python -m v3.tests.phase14_smoke
  # or: python -m pytest v3/tests/test_phase14_smoke.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SMOKE_CODES = ("002768", "301108", "300964")
SMOKE_T_DATE = "2026-09-21"
GRADE_VALUES = {"A+", "A", "B", "WATCH", "FILTER"}


def run_smoke() -> None:
    # --- import path ---
    from v3.config import TREND_BOT_CONFIG, get_config
    from v3.types import (
        DataStatus,
        ExitSignal,
        Grade,
        Lifecycle,
        TrendBotResult,
    )
    from v3.bot import TrendConfirmationBot, results_to_markdown_table, sort_results
    from v3.report import format_stock_detail, write_unified_report
    from v3.report.unified_table import results_to_unified_markdown
    from v3.report.detail import lifecycle_node_markers

    # --- DEFAULT_EXIT present in config ---
    exit_cfg = TREND_BOT_CONFIG.get("exit") or {}
    default_exit = exit_cfg.get("default_strategy")
    assert default_exit, "DEFAULT_EXIT / exit.default_strategy missing in TREND_BOT_CONFIG"
    assert isinstance(default_exit, str) and default_exit.strip(), (
        "exit.default_strategy must be non-empty string"
    )
    # synonym key used by Phase 11 artifacts
    cfg2 = get_config()
    assert cfg2["exit"]["default_strategy"] == default_exit
    assert cfg2.get("backtest", {}).get("default_strategy"), (
        "backtest.default_strategy (DEFAULT_EXIT mirror) missing"
    )
    print(f"OK DEFAULT_EXIT={default_exit!r} phase={TREND_BOT_CONFIG.get('phase')}")

    # --- bot on 3 codes ---
    bot = TrendConfirmationBot()
    results: List[TrendBotResult] = bot.evaluate_many(
        list(SMOKE_CODES),
        t_date=SMOKE_T_DATE,
        persist_continuity=False,
        write_archive=False,
        allow_stale_meta=True,
    )
    assert len(results) == len(SMOKE_CODES), (
        f"expected {len(SMOKE_CODES)} results, got {len(results)}"
    )
    # sort_results stable / used by report
    sorted_r = sort_results(results)
    assert len(sorted_r) == len(results)

    for r in results:
        assert isinstance(r, TrendBotResult), type(r)
        # grade in enum
        assert isinstance(r.grade, Grade), f"{r.code}: grade not Grade enum: {r.grade!r}"
        assert r.grade.value in GRADE_VALUES, f"{r.code}: grade {r.grade.value} not in enum set"
        assert isinstance(r.lifecycle, Lifecycle)
        assert isinstance(r.exit_signal, ExitSignal)

        # exit prices filled when hist / data present
        if r.data_status != DataStatus.DATA_INSUFFICIENT:
            assert r.protection_price is not None, (
                f"{r.code}: protection_price missing (status={r.data_status})"
            )
            assert r.break_price is not None, (
                f"{r.code}: break_price missing (status={r.data_status})"
            )
            assert float(r.protection_price) > 0
            assert float(r.break_price) > 0

        # detail formatter must not raise
        detail = format_stock_detail(r)
        assert "ExitSignal" in detail and "生命周期节点" in detail
        markers = lifecycle_node_markers(r.lifecycle)
        assert "[●" in markers or "特例" in markers

    table = results_to_markdown_table(results)
    for col in ("股票", "阶段", "等级", "Tech", "保护价", "破坏价", "ExitSignal"):
        assert col in table, f"Plan §26 column missing: {col}"

    md = results_to_unified_markdown(results, SMOKE_T_DATE, include_details=True)
    assert "Plan §26" in md and "Plan §27" in md

    # write to temp under report/out (smoke artifact; ok to overwrite)
    out_dir = _ROOT / "v3" / "report" / "out"
    paths = write_unified_report(results, out_dir, SMOKE_T_DATE, include_details=True)
    assert paths["json"].is_file() and paths["md"].is_file()
    assert paths["json"].stat().st_size > 50
    assert paths["md"].stat().st_size > 50

    print(table)
    print()
    print(
        "exit sample:",
        {
            r.code: {
                "grade": r.grade.value,
                "prot": r.protection_price,
                "brk": r.break_price,
                "sig": r.exit_signal.value,
            }
            for r in results
        },
    )
    print(f"wrote {paths['json']}")
    print(f"wrote {paths['md']}")
    print("PASS: Phase 14 smoke")


def main() -> int:
    run_smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
