#!/usr/bin/env python3
"""Smoke: load sample boards JSON / markdown without network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "api"))

from boards_loader import load_latest, parse_push_markdown  # noqa: E402


def main() -> int:
    sample = ROOT / "data" / "sample"
    data = load_latest(sample)
    assert data.get("version") == "V3.1.8"
    assert "boards" in data
    boards = data["boards"]
    print("OK latest boards", data.get("t_date"), {k: len(v) for k, v in boards.items()})
    md = (sample / "push_close_v318_20261008.md").read_text(encoding="utf-8")
    parsed = parse_push_markdown(md)
    print("OK markdown parse", {k: len(v) for k, v in parsed["boards"].items()})
    meta = json.loads((sample / "market_meta.json").read_text(encoding="utf-8"))
    print("OK market_meta score", meta.get("market_score"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
