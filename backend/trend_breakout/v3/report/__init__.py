"""
V3.0 report package — Phase 12–13 unified table + per-stock detail.

JSON + Markdown only (Web frontend deferred). Does NOT cut over live V2.3 push.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "write_unified_report",
    "results_to_unified_markdown",
    "format_stock_detail",
    "lifecycle_node_markers",
]


def __getattr__(name: str) -> Any:
    if name in ("write_unified_report", "results_to_unified_markdown"):
        from .unified_table import results_to_unified_markdown, write_unified_report

        return {
            "write_unified_report": write_unified_report,
            "results_to_unified_markdown": results_to_unified_markdown,
        }[name]
    if name in ("format_stock_detail", "lifecycle_node_markers"):
        from .detail import format_stock_detail, lifecycle_node_markers

        return {
            "format_stock_detail": format_stock_detail,
            "lifecycle_node_markers": lifecycle_node_markers,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
