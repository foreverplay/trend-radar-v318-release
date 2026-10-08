"""Pytest entry for Phase 14 regression smoke."""
from __future__ import annotations

from v3.tests.phase14_smoke import run_smoke


def test_phase14_smoke() -> None:
    run_smoke()
