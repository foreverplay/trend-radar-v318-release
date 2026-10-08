#!/usr/bin/env python3
"""Launcher for V3.1.8 close publish."""
from __future__ import annotations
import runpy, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.argv[0] = str(ROOT / "v3" / "publish_close_v318.py")
runpy.run_module("v3.publish_close_v318", run_name="__main__")
