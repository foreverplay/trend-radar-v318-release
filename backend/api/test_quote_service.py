"""Offline checks for quote clipping and strict local spot reconstruction."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "api"))
sys.path.insert(0, str(ROOT / "backend" / "trend_breakout"))

from quote_service import clip_bars, get_trends, parse_minute_klines, points_for_date  # noqa: E402


def test_hub_empty_history_is_kept() -> None:
    import quote_service as qs

    def fake(path: str, params=None):
        return {
            "available": False,
            "points": [],
            "tradeDate": (params or {}).get("date"),
            "preClose": 0,
            "source": "",
        }

    qs._hub_get = fake  # type: ignore[method-assign]

    class Cache:
        def get(self, key):
            return None

        def put(self, key, result, ttl):
            self.result = result

    out = get_trends("600519", "2020-01-02", cache=Cache())
    assert out["available"] is False
    assert out["points"] == []
    assert out["tradeDate"] == "2020-01-02"


def test_clip_bars_drops_future() -> None:
    bars = [
        {"time": "2026-09-18", "close": 10, "change_pct": 0},
        {"time": "2026-09-21", "close": 11, "change_pct": 9},
        {"time": "2026-09-22", "close": 99, "change_pct": 1},
    ]
    out = clip_bars(bars, "2026-09-21")
    assert [b["time"] for b in out] == ["2026-09-18", "2026-09-21"]
    assert out[-1]["close"] == 11
    assert abs(out[-1]["change_pct"] - 10) < 1e-9


def test_minute_points_keep_requested_date() -> None:
    lines = [
        "2026-09-18 09:31,1,10,10,10,100,1,1,1,1",
        "2026-09-21 09:31,1,12,12,12,100,1,1,20,1",
        "2026-09-21 09:32,1,13,13,13,80,1,1,30,1",
    ]
    _pre, points = parse_minute_klines(lines)
    kept = points_for_date(points, "2026-09-21")
    assert [p["time"] for p in kept] == ["09:31", "09:32"]
    assert points_for_date(points, "2026-09-22") == []


def test_historical_minute_uses_tencent_day_when_kline_is_latest_only() -> None:
    import quote_service as qs

    orig_hub, orig_get = qs._hub_get, qs._get_json

    def fake_get(url, params=None, headers=None, timeout=8):
        if url == qs.KLINE:
            return {"data": {"klines": ["2026-10-08 09:31,1,10,10,10,100,1,1,1,1"]}}
        if url == qs.TENCENT_DAY:
            assert (params or {}).get("code") == "sh600519"
            return {
                "data": {
                    "sh600519": {
                        "data": [
                            {
                                "date": "20260928",
                                "prec": "100",
                                "data": ["0931 110 10 1000", "0932 121 30 3000"],
                            },
                            {"date": "20261008", "prec": "125", "data": ["0931 130 10 1000"]},
                        ]
                    }
                }
            }
        raise AssertionError(url)

    class Cache:
        def get(self, key):
            return None

        def put(self, key, result, ttl):
            self.ttl = ttl

    qs._hub_get = lambda path, params=None: None
    qs._get_json = fake_get
    try:
        out = get_trends("600519", "2026-09-28", cache=Cache())
    finally:
        qs._hub_get, qs._get_json = orig_hub, orig_get
    assert out["available"] is True
    assert out["tradeDate"] == "2026-09-28"
    assert out["source"] == "tencent_day"
    assert abs(out["preClose"] - 100) < 1e-9
    assert [p["time"] for p in out["points"]] == ["09:31", "09:32"]
    assert abs(out["points"][1]["price"] - 121) < 1e-9
    assert abs(out["points"][1]["volume"] - 20) < 1e-9


def test_missing_day_is_not_replaced_by_latest_session() -> None:
    import quote_service as qs

    orig_hub, orig_get = qs._hub_get, qs._get_json

    def fake_get(url, params=None, headers=None, timeout=8):
        if url == qs.KLINE:
            return {"data": {"klines": ["2026-10-08 09:31,1,10,10,10,100,1,1,1,1"]}}
        if url == qs.TENCENT_DAY:
            return {"data": {"sh600519": {"data": [{"date": "20261008", "prec": "125", "data": ["0931 130 10 1000"]}]}}}
        raise AssertionError(url)

    class Cache:
        def get(self, key):
            return None

        def put(self, key, result, ttl):
            return None

    qs._hub_get = lambda path, params=None: None
    qs._get_json = fake_get
    try:
        out = get_trends("600519", "2020-01-02", cache=Cache())
    finally:
        qs._hub_get, qs._get_json = orig_hub, orig_get
    assert out["available"] is False
    assert out["points"] == []
    assert out["tradeDate"] == "2020-01-02"


def test_local_spot_has_no_future_bar() -> None:
    import os

    os.environ["ASHARE_KLINE_ROOT"] = str(ROOT / "data" / "sample" / "kline")
    import importlib
    import data_sources as ds

    importlib.reload(ds)
    spot = ds.spot_from_local_daily("2026-09-21")
    assert len(spot) >= 1
    empty = ds.spot_from_local_daily("2026-09-22")
    assert empty.empty


if __name__ == "__main__":
    test_clip_bars_drops_future()
    test_minute_points_keep_requested_date()
    test_hub_empty_history_is_kept()
    test_historical_minute_uses_tencent_day_when_kline_is_latest_only()
    test_missing_day_is_not_replaced_by_latest_session()
    test_local_spot_has_no_future_bar()
    print("OK quote/strict filters")
