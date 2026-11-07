#!/usr/bin/env python3
"""CLI: build DividendUniverse latest snapshot (Phase1)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # trend-breakout
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dividend_v319.engines.dividendUniverseEngine import write_universe_latest


def main() -> None:
    path = write_universe_latest()
    print(path)


if __name__ == "__main__":
    main()
