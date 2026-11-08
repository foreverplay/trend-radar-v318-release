"""闭环测试。行情和特征都是合成数据，不是策略收益证据。"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

_LAB = Path(__file__).resolve().parents[1]
_BACKEND = _LAB.parent
sys.path.insert(0, str(_LAB))
sys.path.insert(0, str(_BACKEND))

from execution_loop import run_execution
from scoring_engine import score_feature_row
from trading_rules_provider import RuleError, stamp_rule, stamp_tax
from v3.engines.higher_timeframe import HigherTimeframeEngine
from v3.v31.live_v318 import evaluate_v318_asof


DATES = ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06", "2024-06-07", "2024-06-11"]
CODES = ("600001", "600008", "600009")


def _assumptions():
    return {
        "commission_rate": 0.0003,
        "commission_min": 5.0,
        "slippage_bps": 0,
        "price_limit_pct_by_board": {"SH_MAIN": 10},
        "min_buy_lot_by_board": {"SH_MAIN": 100},
        "accept_no_corporate_action_table": True,
    }


def _config(db_path: Path, output: Path):
    return {
        "mode": "diagnostic",
        "score_mode": "v318_feature_row",
        "sqlite_path": str(db_path),
        "output_dir": str(output),
        "start_date": DATES[0],
        "end_date": DATES[-1],
        "volume_unit": "share",
        "codes": list(CODES),
        "assumptions": _assumptions(),
        "entry": {"grades": ["A+", "A"], "entry_statuses": ["PRIME", "CONFIRMED"]},
        "exit": {
            "exit_signals": ["EXIT"],
            "exit_on_filter": False,
            "max_holding_trading_days": 4,
        },
        "portfolio": {
            "initial_capital": 100000,
            "max_positions": 5,
            "single_position_limit": 0.5,
            "reserve_cash_rate": 0,
        },
    }


def _px(code: str, date: str):
    if code == "600001":
        table = {
            "2024-06-03": (10.0, 10.0),
            "2024-06-04": (10.0, 10.5),
            "2024-06-05": (10.5, 11.0),
            "2024-06-06": (11.0, 11.0),
            "2024-06-07": (11.0, 11.0),
            "2024-06-11": (11.0, 11.0),
        }
    elif code == "600008":
        table = {date: (20.0, 20.0) for date in DATES}
    else:
        table = {
            "2024-06-03": (10.0, 10.0),
            "2024-06-04": (11.0, 11.0),
            "2024-06-05": (11.0, 11.0),
            "2024-06-06": (11.0, 11.0),
            "2024-06-07": (11.0, 11.0),
            "2024-06-11": (11.0, 11.0),
        }
    return table[date]


def _feature(code: str, date: str):
    entry_day = date == "2024-06-03"
    score = {"600001": 90.0, "600008": 80.0, "600009": 95.0}[code]
    if not entry_day and code == "600009":
        score = 10.0
    if date == "2024-06-11":
        score = 10.0
    return {
        "symbol": code,
        "trade_date": date,
        "trend_structure_score": score,
        "continuity_score": 82.0 if score >= 80 else 40.0,
        "sector_score": None,
        "tech_score": None,
        "fund_risk_level": None,
        "trend_state": "MAIN_RISE",
        "maturity_level": "MATURE",
        "follow_through": "PASS",
        "exhaustion": "NORMAL",
        "rapid_main_rise": 0,
        "htf_ok": 1,
        "structural_completed": 1,
        "just_completed_structure": 0,
        "current_wave_return": None,
        "exit_signal": "HOLD",
        "broken": 0,
    }


def build_db(path: Path, *, qfq_only=False, gap_code=None, duplicate=False, adj_change=False, drop_status=False):
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    bar_table = "daily_bars_qfq" if qfq_only else "daily_bars_raw"
    cols = "symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, amount REAL"
    if adj_change:
        cols += ", adj_factor REAL"
    conn.execute(f"CREATE TABLE {bar_table} ({cols})")
    conn.execute("CREATE TABLE index_bars (symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER, amount REAL)")
    conn.execute("CREATE TABLE security_status_asof (symbol TEXT, trade_date TEXT, board TEXT, st_status TEXT, days_listed INTEGER)")
    conn.execute(
        """CREATE TABLE v318_feature_inputs (
            symbol TEXT, trade_date TEXT, trend_structure_score REAL, continuity_score REAL,
            sector_score REAL, tech_score REAL, fund_risk_level TEXT, trend_state TEXT,
            maturity_level TEXT, follow_through TEXT, exhaustion TEXT, rapid_main_rise INTEGER,
            htf_ok INTEGER, structural_completed INTEGER, just_completed_structure INTEGER,
            current_wave_return REAL, exit_signal TEXT, broken INTEGER
        )"""
    )
    for date in DATES:
        conn.execute("INSERT INTO index_bars VALUES ('000001', ?, 1, 1, 1, 1, 1, 1)", (date,))
        for code in CODES:
            if gap_code == code and date == "2024-06-05":
                continue
            open_px, close_px = _px(code, date)
            volume = 10000
            factor = 1.0 if date < "2024-06-06" else 1.2
            values = [code, date, open_px, max(open_px, close_px), min(open_px, close_px), close_px, volume, close_px * volume]
            if adj_change:
                values.append(factor)
            conn.execute(f"INSERT INTO {bar_table} VALUES ({','.join('?' for _ in values)})", values)
            if duplicate and code == "600001" and date == DATES[0]:
                conn.execute(f"INSERT INTO {bar_table} VALUES ({','.join('?' for _ in values)})", values)
            if not drop_status:
                conn.execute(
                    "INSERT INTO security_status_asof VALUES (?, ?, 'SH_MAIN', 'NORMAL', 1000)",
                    (code, date),
                )
            feat = _feature(code, date)
            conn.execute(
                """INSERT INTO v318_feature_inputs VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    feat["symbol"], feat["trade_date"], feat["trend_structure_score"], feat["continuity_score"],
                    feat["sector_score"], feat["tech_score"], feat["fund_risk_level"], feat["trend_state"],
                    feat["maturity_level"], feat["follow_through"], feat["exhaustion"], feat["rapid_main_rise"],
                    feat["htf_ok"], feat["structural_completed"], feat["just_completed_structure"],
                    feat["current_wave_return"], feat["exit_signal"], feat["broken"],
                ),
            )
    conn.commit()
    conn.close()


def test_feature_row_uses_production_grade_and_not_a_fallback():
    scored = score_feature_row(_feature("600001", "2024-06-03"))
    assert scored["ok"] is True
    assert scored["grade_v318"] == "A+"
    assert scored["entry_window_v318"] == "CONFIRMED"
    assert scored["scoring_mode"] == "v318_feature_row"
    missing = dict(_feature("600001", "2024-06-03"))
    missing["continuity_score"] = None
    failed = score_feature_row(missing)
    assert failed["ok"] is False
    assert failed["grade_v318"] is None


def test_cli_fixture_matches_hand_accounting(tmp_path: Path):
    db_path = tmp_path / "market.sqlite"
    build_db(db_path)
    cfg = _config(db_path, tmp_path / "out")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_LAB / "run_research_backtest.py"), str(cfg_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    out = Path(payload["output_dir"])
    run_id = payload["run_id"]
    trades = pd.read_parquet(out / f"trades_{run_id}.parquet")
    orders = pd.read_parquet(out / f"orders_{run_id}.parquet")
    equity = pd.read_parquet(out / f"equity_{run_id}.parquet")
    positions = pd.read_parquet(out / f"daily_positions_{run_id}.parquet")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["evidence_class"] == "not_strategy_evidence"
    assert manifest["result_class"] == "diagnostic_not_official_fills"
    assert manifest["assumptions"]["commission_rate"] == 0.0003
    assert "2024-06-08" not in set(equity["date"])
    assert "2024-06-10" not in set(equity["date"])

    buys = trades[trades.side == "BUY"].sort_values("code")
    sells = trades[trades.side == "SELL"].sort_values("code")
    assert list(buys["date"]) == ["2024-06-04", "2024-06-04"]
    assert list(buys["code"]) == ["600001", "600008"]
    assert list(buys["quantity"]) == [5000, 2400]
    assert list(buys["price"]) == [10.0, 20.0]
    assert buys.loc[buys.code == "600001", "commission"].iloc[0] == pytest.approx(15.0)
    assert buys.loc[buys.code == "600008", "commission"].iloc[0] == pytest.approx(14.4)
    assert list(sells["date"]) == ["2024-06-11", "2024-06-11"]
    assert sells.loc[sells.code == "600001", "stamp_tax"].iloc[0] == pytest.approx(27.5)
    assert sells.loc[sells.code == "600001", "commission"].iloc[0] == pytest.approx(16.5)
    assert sells.loc[sells.code == "600008", "stamp_tax"].iloc[0] == pytest.approx(24.0)
    rejected = orders[(orders.status == "REJECTED") & (orders.code == "600009")]
    assert list(rejected["reason"]) == ["limit_up"]
    row = equity.set_index("date")
    assert row.loc["2024-06-03", "total_equity"] == pytest.approx(100000)
    assert row.loc["2024-06-04", "cash"] == pytest.approx(1970.6)
    assert row.loc["2024-06-04", "market_value"] == pytest.approx(100500)
    assert row.loc["2024-06-04", "total_equity"] == pytest.approx(102470.6)
    assert row.loc["2024-06-07", "positions_count"] == 2
    assert row.loc["2024-06-11", "total_equity"] == pytest.approx(104888.2)
    assert row.loc["2024-06-11", "market_value"] == pytest.approx(0)
    held = positions[positions.date == "2024-06-04"]
    assert set(held["code"]) == {"600001", "600008"}
    assert int(held.loc[held.code == "600001", "quantity"].iloc[0]) == 5000


def test_empty_signals_keep_cash(tmp_path: Path):
    db_path = tmp_path / "empty.sqlite"
    build_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE v318_feature_inputs SET trend_structure_score=10, continuity_score=10")
    conn.commit()
    conn.close()
    result = run_execution(_config(db_path, tmp_path / "out"), db_path, tmp_path / "out", run_id="empty")
    assert result.status == "completed"
    assert result.trades == []
    assert {row["total_equity"] for row in result.equity} == {100000}


def test_cash_competition_tie_breaks_by_code(tmp_path: Path):
    db_path = tmp_path / "tie.sqlite"
    build_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM daily_bars_raw WHERE symbol = '600009'")
    conn.execute("DELETE FROM security_status_asof WHERE symbol = '600009'")
    conn.execute("DELETE FROM v318_feature_inputs WHERE symbol = '600009'")
    conn.execute("UPDATE daily_bars_raw SET open=10, high=10, low=10, close=10, amount=100000 WHERE symbol='600008'")
    conn.execute("UPDATE v318_feature_inputs SET trend_structure_score=90, continuity_score=82 WHERE trade_date='2024-06-03'")
    conn.execute("UPDATE v318_feature_inputs SET trend_structure_score=10, continuity_score=10 WHERE trade_date!='2024-06-03'")
    conn.commit()
    conn.close()
    cfg = _config(db_path, tmp_path / "out")
    cfg["codes"] = ["600001", "600008"]
    cfg["portfolio"]["initial_capital"] = 1010
    cfg["portfolio"]["single_position_limit"] = 1
    result = run_execution(cfg, db_path, tmp_path / "tie-out", run_id="tie")
    buys = [row for row in result.trades if row["side"] == "BUY"]
    assert [row["code"] for row in buys] == ["600001"]
    assert buys[0]["quantity"] == 100
    assert buys[0]["commission"] == pytest.approx(5)
    rejected = [row for row in result.orders if row["code"] == "600008" and row["status"] == "REJECTED"]
    assert rejected and rejected[0]["reason"] == "below_min_lot_or_cash"


def test_blocks(tmp_path: Path):
    qfq = tmp_path / "qfq.sqlite"
    build_db(qfq, qfq_only=True)
    blocked = run_execution(_config(qfq, tmp_path / "q"), qfq, tmp_path / "q", run_id="qfq")
    assert blocked.status == "blocked"
    assert "前复权" in blocked.manifest["reason"]

    formal = _config(tmp_path / "unused.sqlite", tmp_path / "f")
    formal["mode"] = "formal"
    formal["assumptions"] = {}
    blocked = run_execution(formal, tmp_path / "unused.sqlite", tmp_path / "f", run_id="formal")
    assert blocked.status == "blocked"
    assert "正式模式" in blocked.manifest["reason"]

    gap = tmp_path / "gap.sqlite"
    build_db(gap, gap_code="600001")
    blocked = run_execution(_config(gap, tmp_path / "g"), gap, tmp_path / "g", run_id="gap")
    assert blocked.status == "blocked"
    assert "缺少原始行情" in blocked.manifest["reason"]

    dup = tmp_path / "dup.sqlite"
    build_db(dup, duplicate=True)
    blocked = run_execution(_config(dup, tmp_path / "d"), dup, tmp_path / "d", run_id="dup")
    assert blocked.status == "blocked"
    assert "重复" in blocked.manifest["reason"]

    adj = tmp_path / "adj.sqlite"
    build_db(adj, adj_change=True)
    blocked = run_execution(_config(adj, tmp_path / "a"), adj, tmp_path / "a", run_id="adj")
    assert blocked.status == "blocked"
    assert "复权因子" in blocked.manifest["reason"]

    unknown = tmp_path / "st.sqlite"
    build_db(unknown)
    conn = sqlite3.connect(unknown)
    conn.execute("UPDATE security_status_asof SET st_status='UNKNOWN' WHERE symbol='600001'")
    conn.commit()
    conn.close()
    result = run_execution(_config(unknown, tmp_path / "st"), unknown, tmp_path / "st-out", run_id="st")
    assert any(row["code"] == "600001" and str(row["reason"]).startswith("rule:") for row in result.orders)


def test_limit_down_volume_zero_and_sell_not_on_buy_day(tmp_path: Path):
    db_path = tmp_path / "risk.sqlite"
    build_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE daily_bars_raw SET volume=0, amount=0 WHERE symbol='600008' AND trade_date='2024-06-04'")
    conn.execute("UPDATE daily_bars_raw SET open=9.9, low=9.9 WHERE symbol='600001' AND trade_date='2024-06-11'")
    conn.commit()
    conn.close()
    result = run_execution(_config(db_path, tmp_path / "risk-out"), db_path, tmp_path / "risk-out", run_id="risk")
    reasons = {(row["code"], row["date"], row["reason"]) for row in result.orders if row["status"] == "REJECTED"}
    assert ("600008", "2024-06-04", "no_liquidity_volume_zero") in reasons
    assert ("600001", "2024-06-11", "limit_down") in reasons
    assert all(not (row["side"] == "SELL" and row["date"] == "2024-06-04") for row in result.trades)
    still_held = [row for row in result.positions if row["date"] == "2024-06-11" and row["code"] == "600001"]
    assert still_held and still_held[0]["quantity"] == 5000


def test_stamp_tax_dates_are_not_the_old_wrong_split():
    with pytest.raises(RuleError):
        stamp_rule("2008-04-24")
    with pytest.raises(RuleError):
        stamp_rule("2008-09-18")
    first = stamp_rule("2008-09-19")
    assert first.rate == 0.001 and first.seller_only is True
    before = stamp_rule("2023-08-27")
    after = stamp_rule("2023-08-28")
    assert before.rate == 0.001 and after.rate == 0.0005
    sell = stamp_tax("2024-06-11", 55000, True, "diagnostic", {})
    buy = stamp_tax("2024-06-04", 50000, False, "diagnostic", {})
    assert sell["stamp_tax"] == pytest.approx(27.5)
    assert buy["stamp_tax"] == 0


def test_checkpoint_requires_account_and_same_config(tmp_path: Path):
    db_path = tmp_path / "market.sqlite"
    build_db(db_path)
    cfg = _config(db_path, tmp_path / "out")
    first = run_execution(cfg, db_path, tmp_path / "part", run_id="part", stop_after="2024-06-04")
    assert first.status == "checkpoint"
    state = json.loads((tmp_path / "part" / "checkpoint.json").read_text(encoding="utf-8"))
    assert "cash" in state and "positions" in state and "pending_orders" in state
    full = run_execution(cfg, db_path, tmp_path / "full", run_id="full")
    resumed = run_execution(cfg, db_path, tmp_path / "resume", run_id="resume", resume_state=state)
    assert [row["cash_after"] for row in resumed.trades] == [row["cash_after"] for row in full.trades]
    broken = dict(state)
    del broken["cash"]
    with pytest.raises(Exception):
        run_execution(cfg, db_path, tmp_path / "bad", run_id="bad", resume_state=broken)
    changed = json.loads(json.dumps(cfg))
    changed["portfolio"]["initial_capital"] = 1
    with pytest.raises(Exception):
        run_execution(changed, db_path, tmp_path / "mismatch", run_id="mismatch", resume_state=state)


def test_v318_entry_truncates_future_bars(monkeypatch):
    import scoring_engine

    hist = pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=30),
        "open": [10 + i * 0.1 for i in range(30)],
        "high": [10.2 + i * 0.1 for i in range(30)],
        "low": [9.8 + i * 0.1 for i in range(30)],
        "close": [10.1 + i * 0.1 for i in range(30)],
        "volume": [1000] * 30,
        "amount": [10000] * 30,
    })
    seen = {}

    def _fake(code, name, t_date, **kwargs):
        seen["allow_remote"] = kwargs.get("allow_remote")
        seen["max_date"] = str(pd.to_datetime(kwargs["hist"]["date"]).max().date())
        return {"ok": False, "error": "synthetic_uncomputable", "grade_v318": None}

    monkeypatch.setattr(scoring_engine, "evaluate_v318_asof", _fake)

    class _Provider:
        def get_stock_daily_bars(self, code, end_date=None, start_date=None):
            frame = hist.copy()
            if end_date:
                frame = frame[frame["date"] <= pd.Timestamp(end_date)]
            return frame

    engine = scoring_engine.V318ScoringEngine(_Provider())
    scored = engine.score_stock_asof("600001", "2024-01-15")
    assert seen["allow_remote"] is False
    assert seen["max_date"] <= "2024-01-15"
    assert scored["ok"] is False
    assert scored["grade_v318"] is None

    t_date = "2024-02-09"
    base = hist[hist["date"] <= t_date].copy()
    extra = hist.copy()
    extra.loc[extra.index[-1], "close"] = 1.0
    first = evaluate_v318_asof("600001", "600001", t_date, hist=base, allow_remote=False)
    second = evaluate_v318_asof("600001", "600001", t_date, hist=extra, allow_remote=False)
    assert first.get("error") == second.get("error")
    assert first.get("grade_v318") == second.get("grade_v318")

    htf = HigherTimeframeEngine()
    long_hist = pd.DataFrame({
        "date": pd.bdate_range("2023-01-02", periods=120),
        "close": [100 + i * 0.4 for i in range(120)],
    })
    cut = str(long_hist["date"].iloc[79].date())
    before = htf.evaluate(long_hist, t_date=cut)
    crashed = long_hist.copy()
    crashed.loc[80:, "close"] = 1.0
    after = htf.evaluate(crashed, t_date=cut)
    assert before.monthly_label == after.monthly_label
    assert before.htf_ok == after.htf_ok
