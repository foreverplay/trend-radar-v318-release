"""研究只读接口。产物来自合成闭环，不是本机覆盖或收益。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_API = Path(__file__).resolve().parent
_LAB = _API.parent / "trend_breakout" / "research_lab"
sys.path.insert(0, str(_API))
sys.path.insert(0, str(_LAB))
sys.path.insert(0, str(_LAB / "tests"))
sys.path.insert(0, str(_LAB.parent))

from fastapi.testclient import TestClient

import app
from execution_loop import run_execution
from precheck_local import run_precheck
from test_closed_loop import _config, build_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_RESULTS_ROOT", str(tmp_path))
    return TestClient(app.app)


def test_research_page_is_served_by_the_api(client):
    page = client.get("/research")
    assert page.status_code == 200
    assert "研究实验室" in page.text
    assert "/api/research/status" in page.text
    assert "15.31" not in page.text
    assert "运行回测" not in page.text or "没有运行回测按钮" in page.text


def test_empty_root_and_malicious_run_id(client, tmp_path):
    status = client.get("/api/research/status")
    assert status.status_code == 200
    body = status.json()
    assert body["precheck_state"] == "missing"
    assert body["run_button"] is None
    assert "5924" not in json.dumps(body, ensure_ascii=False)
    runs = client.get("/api/research/runs")
    assert runs.json()["runs"] == []
    for bad in ("../etc/passwd", "foo/bar", "..", "a b", "%2e%2e"):
        denied = client.get(f"/api/research/runs/{bad}")
        assert denied.status_code in {400, 404}
        assert str(tmp_path) not in denied.text
        assert "/etc/passwd" not in denied.text
    import research_reader
    for bad in (".", "..", "../etc/passwd", "foo/bar"):
        with pytest.raises(research_reader.ResearchReadError):
            research_reader._run_dir(bad)


def test_blocked_and_diagnostic_runs_are_labeled(client, tmp_path):
    db = tmp_path / "book.sqlite"
    build_db(db)
    out = tmp_path / "diag01"
    result = run_execution(_config(db, out), db, out, run_id="diag01")
    assert result.status == "completed"
    blocked_db = tmp_path / "named.sqlite"
    build_db(blocked_db)
    import sqlite3
    conn = sqlite3.connect(blocked_db)
    conn.execute("ALTER TABLE daily_bars_raw RENAME TO daily_bars")
    conn.commit()
    conn.close()
    blocked_out = tmp_path / "block01"
    blocked = run_execution(_config(blocked_db, blocked_out), blocked_db, blocked_out, run_id="block01")
    assert blocked.status == "blocked"

    listed = client.get("/api/research/runs").json()["runs"]
    by_id = {item["run_id"]: item for item in listed}
    assert by_id["block01"]["status"] == "blocked"
    assert "daily_bars" in by_id["block01"]["reason"]
    assert by_id["diag01"]["not_strategy_evidence"] is True

    detail = client.get("/api/research/runs/diag01")
    assert detail.status_code == 200
    assert str(db) not in detail.text
    assert "sqlite_path" not in detail.text
    orders = client.get("/api/research/runs/diag01/orders").json()
    assert orders["present"] is True
    assert any(row.get("reason") == "limit_up" for row in orders["rows"])
    grades = client.get("/api/research/runs/diag01/grades").json()
    assert grades["source"] == "signals_not_full_universe"
    assert grades["rows"]
    metrics = client.get("/api/research/runs/diag01/metrics").json()
    assert metrics["present"] is True
    assert metrics["metrics"]["final_equity"] == pytest.approx(104888.2)
    blocked_metrics = client.get("/api/research/runs/block01/metrics").json()
    assert blocked_metrics["present"] is False
    assert blocked_metrics["metrics"] is None


def test_precheck_reports_unproven_and_bfq_declaration(tmp_path):
    db = tmp_path / "longtou.sqlite3"
    build_db(db)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE daily_bars_raw RENAME TO daily_bars")
    conn.commit()
    conn.close()
    repo = tmp_path / "repo"
    bfq = repo / "backend" / "trend_breakout" / "dividend_v319" / "data" / "bfq_daily"
    bfq.mkdir(parents=True)
    (bfq / "manifest.json").write_text(json.dumps({
        "generated_at": "2026-11-04T10:51:27+08:00",
        "adjust": "",
        "source": "akshare.stock_zh_a_daily",
        "ok": [{"path": "/secret/002601.parquet", "rows": 3609}],
    }), encoding="utf-8")
    (bfq / "002601.parquet").write_bytes(b"not-read-as-prices")
    parquet = tmp_path / "daily"
    parquet.mkdir()
    payload = run_precheck(repo, db, parquet, tmp_path / "results")
    assert payload["status"] == "blocked"
    assert payload["tables"]["daily_bars"]["price_basis"] == "unproven"
    assert payload["bfq_daily"]["accepted_as_execution_prices"] is False
    assert payload["bfq_daily"]["file_count"] == 1
    text = json.dumps(payload, ensure_ascii=False)
    assert "/secret" not in text
    assert "3609" not in text
