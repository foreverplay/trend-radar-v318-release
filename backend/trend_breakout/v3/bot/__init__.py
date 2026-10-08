"""
V3.0 Bot package — Phase 7+8 TrendConfirmationBot orchestrator.

Wires Market → Sector → Technical → Fundamental → Risk → Continuity →
Lifecycle → grade_candidate → ExitEngine → TrendBotResult.

Does NOT cut over live V2.3 push/scan. No BacktestEngine yet.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "TrendConfirmationBot",
    "results_to_markdown_table",
    "sort_results",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import trend_confirmation_bot as _mod

        return getattr(_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
