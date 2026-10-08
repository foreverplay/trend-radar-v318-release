"""
TrendConfirmationBot V3.1 / V3.1.1 additive package.

Does NOT alter V3.0 ``grade_candidate`` or live publish paths.
Import grading via ``v3.grading_v31`` / ``v3.grading_v311``.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from .types import (
    ExhaustionLevel,
    TrendState,
    WaveInfo,
    WaveLevel,
    WaveEventKind,
    WaveEventLogEntry,
    PoolEntry,
    V31GradeContext,
    EntryWindow,
    FundRiskLevel,
    MajorMoveType,
    MoveBucket,
    SignalEvaluation,
    EntrySignalEvent,
)
from .config import (
    V31_CONFIG,
    V310_CONFIG,
    V311_CONFIG,
    A_PLUS_PATH_CONFIG,
    get_v31_config,
    get_v311_config,
    get_a_plus_path_config,
    V312_LIFECYCLE,
    V313_WAVE,
    get_v312_lifecycle,
    get_v313_lifecycle,
    get_v313_wave,
    V314_MAJOR_GATE,
    get_v314_major_gate,
    get_v314_lifecycle,
    V315_MAJOR_GATE_LABEL_ONLY,
    V315_LABEL,
    get_v315_major_gate_label_only,
    get_v315_label,
    get_v315_lifecycle,
)

__all__ = [
    "ExhaustionLevel",
    "TrendState",
    "WaveInfo",
    "WaveLevel",
    "WaveEventKind",
    "WaveEventLogEntry",
    "PoolEntry",
    "V31GradeContext",
    "EntryWindow",
    "FundRiskLevel",
    "MajorMoveType",
    "MoveBucket",
    "SignalEvaluation",
    "EntrySignalEvent",
    "V31_CONFIG",
    "V310_CONFIG",
    "V311_CONFIG",
    "A_PLUS_PATH_CONFIG",
    "get_v31_config",
    "get_v311_config",
    "get_a_plus_path_config",
    "V312_LIFECYCLE",
    "V313_WAVE",
    "get_v312_lifecycle",
    "get_v313_lifecycle",
    "get_v313_wave",
    "V314_MAJOR_GATE",
    "get_v314_major_gate",
    "get_v314_lifecycle",
    "V315_MAJOR_GATE_LABEL_ONLY",
    "V315_LABEL",
    "get_v315_major_gate_label_only",
    "get_v315_label",
    "get_v315_lifecycle",
]

__version__ = "3.1.5"
