"""Smoke tests for V3.1.7 close publish (no network; plain python or pytest).

Run: cd . && python -m v3.tests.test_publish_v317
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from v3 import publish_close_v317 as P
from v3.types import ExitSignal, Grade, Lifecycle, RiskLevel, TrendBotResult

ROOT = Path(__file__).resolve().parents[2]


def _r(code="000001", grade="A+", tech=86.0, cont=80.0, sector=75.0, fund=70.0) -> TrendBotResult:
    return TrendBotResult(
        t_date="2000-01-03", code=code, name=f"T{code}", grade=Grade(grade),
        tech_score=tech, continuity_score=cont, fund_score=fund, sector_score=sector,
        consumption_score=30.0, lifecycle=Lifecycle.SECOND_BREAKOUT, risk_level=RiskLevel.WATCH,
        exit_signal=ExitSignal.HOLD, protection_price=9.5, break_price=9.0, close=10.0,
    )


def _v(**kw):
    base = dict(ok=True, state="MAIN_RISE", maturity_level="MATURE", entry_window="PRIME",
                follow_through="PASS", wave_return=0.12, trend_return=0.22, realized_score=78.0,
                trend_age=22, exhaustion="NORMAL", fund_risk="LOW", htf_ok=True,
                struct_completed=True)
    base.update(kw)
    return base


def test_a_plus_gate() -> None:
    ok, _ = P._a_plus_gate(_r(), _v())
    assert ok
    ok, f = P._a_plus_gate(_r(), _v(trend_return=0.10))
    assert not ok and any("15%" in x for x in f)  # LOW_REALIZED_TREND can't be A+
    ok, _ = P._a_plus_gate(_r(), _v(maturity_level="BUILDING"))
    assert not ok
    ok, _ = P._a_plus_gate(_r(), _v(trend_age=10))
    assert not ok
    ok, _ = P._a_plus_gate(_r(), _v(state="CONFIRMED"))
    assert not ok
    ok, _ = P._a_plus_gate(_r(), _v(maturity_level="ADVANCED"))
    assert ok  # allowed, but lands in EXTENDED
    ok, _ = P._a_plus_gate(_r(), {"ok": False})
    assert not ok


def test_partitions() -> None:
    rs = [
        _r("000001"),                                   # MATURE PRIME → MAIN, A+
        _r("000002"),                                   # Plan A+ BUILDING → A, BUILDING
        _r("000003", grade="A"),                        # ADVANCED → EXTENDED
        _r("000004", grade="B"),                        # MATURE CONFIRMED + Exhaustion HIGH → EXTENDED
        _r("000005", grade="FILTER", tech=50, cont=40),  # FILTER
        _r("000006", grade="B"),                        # MATURE FOLLOW → REST
        _r("000007", grade="A", tech=70),               # PRIME but tech<75 → not MAIN
        _r("000008", grade="B"),                        # PRIME but FT WEAK → not MAIN
    ]
    v = {
        "000001": _v(),
        "000002": _v(maturity_level="BUILDING", entry_window="NO_ENTRY", trend_age=8),
        "000003": _v(maturity_level="ADVANCED", entry_window="EXTENDED"),
        "000004": _v(entry_window="CONFIRMED", exhaustion="HIGH"),
        "000006": _v(entry_window="FOLLOW"),
        "000007": _v(),
        "000008": _v(follow_through="WEAK"),
    }
    rows = {d["code"]: d for d in P.decorate(rs, v)}
    assert rows["000001"]["section"] == P.SEC_MAIN and rows["000001"]["grade_v317"] == "A+"
    assert rows["000001"]["entry_signal"]
    assert rows["000002"]["grade_v317"] == "A" and rows["000002"]["section"] == P.SEC_BUILDING
    assert rows["000003"]["section"] == P.SEC_EXTENDED
    assert rows["000004"]["section"] == P.SEC_EXTENDED
    assert rows["000005"]["section"] == P.SEC_FILTER
    assert rows["000006"]["section"] == P.SEC_REST
    assert rows["000007"]["section"] != P.SEC_MAIN
    assert rows["000008"]["section"] != P.SEC_MAIN
    # no A+ outside MATURE/ADVANCED
    for d in rows.values():
        if d["grade_v317"] == "A+":
            assert d["maturity"] in ("MATURE", "ADVANCED")

    cnt = P.counts_v317(list(rows.values()))
    md = P.build_push_md(list(rows.values()), t_date="2000-01-03", meta={"market_score": 50},
                         regime_v3="CHOP", legacy="WEAK", risk_off=False, cnt=cnt)
    assert md.startswith("# 📡 趋势分析雷达 V3.1.7 观察榜 2000-01-03")
    assert "禁止荐买 / 预测必涨" in md
    for bad in ("ResonanceTags", "RecommendationScore", "RecScore", "点名核对", "荐买：", "必涨！"):
        assert bad not in md, bad
    for h in P.HEADERS:
        assert h in md


def test_no_hardcoded_dates_or_symbols() -> None:
    for rel in ("v3/publish_close_v317.py", "v3/v31/live_v317.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"20[12]\d-\d\d-\d\d", src), rel
        assert not re.search(r"['\"](00|30|60|68)\d{4}['\"]", src), rel


def test_no_future_bars() -> None:
    """Same T-row whether or not bars after T are passed in (uses snap cache, small warmup)."""
    from v3.v31.live_v317 import _read_local, evaluate_v317_asof, make_builder

    files = sorted((ROOT / "v3" / "v31" / "snap_cache").glob("*.pkl"))
    if not files:
        print("skip test_no_future_bars (no snap cache)")
        return
    code = files[0].stem
    h = _read_local(code)
    if h is None or len(h) < 120:
        return
    t = str(h["date"].iloc[-6].date())
    b = make_builder()
    a = evaluate_v317_asof(code, code, t, hist=h, builder=b, warmup=25)
    c = evaluate_v317_asof(code, code, t, hist=h[h["date"] <= pd.Timestamp(t)], builder=b, warmup=25)
    keys = ("state", "maturity_level", "entry_window", "trend_return", "wave_return", "follow_through", "trend_age")
    assert a.get("ok") and c.get("ok")
    assert all(a.get(k) == c.get(k) for k in keys), (a, c)


def main() -> None:
    for fn in (test_a_plus_gate, test_partitions, test_no_hardcoded_dates_or_symbols, test_no_future_bars):
        fn()
        print(f"PASS {fn.__name__}")


if __name__ == "__main__":
    main()
