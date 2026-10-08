#!/usr/bin/env python3
"""Launcher — run from repo root so stdlib `types` is not shadowed by v3/types.py."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# Execute module as __main__
sys.argv[0] = str(ROOT / "v3" / "publish_close_v3.py")
runpy.run_module("v3.publish_close_v3", run_name="__main__")
