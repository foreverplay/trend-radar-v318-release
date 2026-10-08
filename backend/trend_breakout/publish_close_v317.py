#!/usr/bin/env python3
"""Launcher for V3.1.7 close publish — run from anywhere; avoids v3/types.py shadowing stdlib."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.argv[0] = str(ROOT / "v3" / "publish_close_v317.py")
runpy.run_module("v3.publish_close_v317", run_name="__main__")
