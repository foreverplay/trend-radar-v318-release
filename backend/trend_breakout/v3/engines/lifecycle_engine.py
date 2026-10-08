"""
LifecycleEngine — Phase 6 thin mapper from technical/structure phase → Lifecycle.

Does **not** invent new phase detection. Delegates to StructureResult /
TechnicalEngineResult (or an explicit ``phase=`` string) and maps via
``Lifecycle.map_from_v2_phase``.

``is_priority_candidate`` flags Plan A+ focus stages:
SECOND_BREAKOUT / MAIN_RISE / ACCELERATION.

Does NOT cut over live V2.3 push/scan.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
try:
    from paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
except ImportError:  # packaged / flat layout fallback
    try:
        from trend_breakout.paths import ROOT as _PKG_ROOT, LOCAL_DAILY as _PKG_LOCAL_DAILY, CONTINUITY_CACHE as _PKG_CONT_CACHE, CACHE_DIR as _PKG_CACHE
    except ImportError:
        from pathlib import Path as __P
        import os as __os
        _PKG_ROOT = __P(__os.environ.get("TREND_BREAKOUT_ROOT", __P(__file__).resolve().parent))
        _k = __P(__os.environ.get("ASHARE_KLINE_ROOT", str(_PKG_ROOT.parent.parent / "data" / "sample" / "kline")))
        _PKG_LOCAL_DAILY = _k / "raw" / "daily"
        _PKG_CACHE = _PKG_ROOT / "cache"
        _PKG_CONT_CACHE = _PKG_CACHE / "continuity_history"
from typing import Any, Dict, List, Optional, Sequence, Union

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import get_config  # noqa: E402
from v3.types import DataStatus, Lifecycle  # noqa: E402

# Plan A+ focus lifecycles (STRATEGY_V3_0 / candidate matrix)
PRIORITY_LIFECYCLES: tuple = (
    Lifecycle.SECOND_BREAKOUT,
    Lifecycle.MAIN_RISE,
    Lifecycle.ACCELERATION,
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class LifecycleEngineResult:
    """
    V3 lifecycle mapping result.

    ``phase`` / ``phase_cn`` are raw V2 strings when available.
    ``lifecycle`` is the V3 enum via ``Lifecycle.map_from_v2_phase``.
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    lifecycle: Lifecycle = Lifecycle.NONE
    phase: str = "NONE"
    phase_cn: str = ""
    is_priority_candidate: bool = False
    source: str = ""  # "phase" | "technical_result" | "structure_result" | "none"
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        d["lifecycle"] = (
            self.lifecycle.value if isinstance(self.lifecycle, Lifecycle) else self.lifecycle
        )
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_priority_lifecycle(life: Union[Lifecycle, str, None]) -> bool:
    """True when lifecycle is SECOND_BREAKOUT / MAIN_RISE / ACCELERATION."""
    if life is None:
        return False
    if isinstance(life, Lifecycle):
        return life in PRIORITY_LIFECYCLES
    try:
        return Lifecycle(str(life).strip().upper()) in PRIORITY_LIFECYCLES
    except ValueError:
        # MAIN_RISE_ACCEL maps to ACCELERATION
        mapped = Lifecycle.map_from_v2_phase(str(life))
        return mapped in PRIORITY_LIFECYCLES


def _phase_from_obj(obj: Any) -> tuple:
    """
    Extract (phase, phase_cn, source_hint) from StructureResult /
    TechnicalEngineResult / dict. Returns (None, "", "") if absent.
    """
    if obj is None:
        return None, "", ""
    if isinstance(obj, dict):
        phase = obj.get("phase")
        phase_cn = obj.get("phase_cn") or ""
        return phase, str(phase_cn), "dict"
    phase = getattr(obj, "phase", None)
    phase_cn = getattr(obj, "phase_cn", None) or ""
    return phase, str(phase_cn), type(obj).__name__


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class LifecycleEngine:
    """
    V3 LifecycleEngine — map technical phase → Lifecycle enum.

    ``evaluate(phase=..., structure_result=..., technical_result=...)``
    precedence: explicit ``phase`` > ``technical_result`` > ``structure_result``.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config if config is not None else get_config()
        # allow config override of priority set (optional)
        cand = (self.config or {}).get("candidate") or {}
        a_plus = cand.get("A+") or {}
        allowed = a_plus.get("allowed_lifecycle")
        if allowed:
            mapped: List[Lifecycle] = []
            for name in allowed:
                try:
                    mapped.append(Lifecycle(str(name).strip().upper()))
                except ValueError:
                    mapped.append(Lifecycle.map_from_v2_phase(str(name)))
            self._priority: Sequence[Lifecycle] = tuple(mapped)
        else:
            self._priority = PRIORITY_LIFECYCLES

    @property
    def priority_lifecycles(self) -> Sequence[Lifecycle]:
        return self._priority

    def is_priority_candidate(self, life: Union[Lifecycle, str, None]) -> bool:
        if life is None:
            return False
        if isinstance(life, Lifecycle):
            return life in self._priority
        mapped = Lifecycle.map_from_v2_phase(str(life))
        return mapped in self._priority

    def evaluate(
        self,
        phase: Optional[str] = None,
        structure_result: Any = None,
        technical_result: Any = None,
    ) -> LifecycleEngineResult:
        try:
            resolved_phase: Optional[str] = None
            phase_cn = ""
            source = "none"

            if phase is not None and str(phase).strip() != "":
                resolved_phase = str(phase).strip()
                source = "phase"
                # try enrich cn from technical/structure if same phase
                for obj, label in (
                    (technical_result, "technical_result"),
                    (structure_result, "structure_result"),
                ):
                    p, cn, _ = _phase_from_obj(obj)
                    if p is not None and str(p).strip().upper() == resolved_phase.upper():
                        phase_cn = cn
                        break
            else:
                p, cn, hint = _phase_from_obj(technical_result)
                if p is not None and str(p).strip() != "":
                    resolved_phase = str(p).strip()
                    phase_cn = cn
                    source = "technical_result"
                else:
                    p2, cn2, hint2 = _phase_from_obj(structure_result)
                    if p2 is not None and str(p2).strip() != "":
                        resolved_phase = str(p2).strip()
                        phase_cn = cn2
                        source = "structure_result"

            if resolved_phase is None:
                return LifecycleEngineResult(
                    ok=False,
                    data_status=DataStatus.DATA_INSUFFICIENT,
                    lifecycle=Lifecycle.NONE,
                    phase="NONE",
                    phase_cn="",
                    is_priority_candidate=False,
                    source=source,
                    tags=["NO_PHASE"],
                    notes=["no phase from technical/structure; lifecycle=NONE"],
                )

            life = Lifecycle.map_from_v2_phase(resolved_phase)
            priority = life in self._priority
            tags: List[str] = []
            if priority:
                tags.append("PRIORITY_CANDIDATE")
            if resolved_phase.upper() == "MAIN_RISE_ACCEL":
                tags.append("MAPPED_MAIN_RISE_ACCEL")

            return LifecycleEngineResult(
                ok=True,
                data_status=DataStatus.VALID,
                lifecycle=life,
                phase=resolved_phase,
                phase_cn=phase_cn,
                is_priority_candidate=priority,
                source=source,
                tags=tags,
                notes=[],
            )
        except Exception as exc:  # noqa: BLE001
            return LifecycleEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                lifecycle=Lifecycle.NONE,
                phase="NONE",
                tags=["LIFECYCLE_EVAL_ERR"],
                notes=[f"lifecycle_err:{type(exc).__name__}"],
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Smoke (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    import pandas as pd

    from v3.engines.technical_engine import TechnicalEngine

    parser = argparse.ArgumentParser(description="LifecycleEngine smoke")
    parser.add_argument("--code", default="002768")
    parser.add_argument(
        "--parquet",
        default=str(_PKG_LOCAL_DAILY / "002768.parquet"),
    )
    parser.add_argument("--t-date", default="2026-09-21")
    args = parser.parse_args()

    hist = pd.read_parquet(args.parquet)
    tech = TechnicalEngine().evaluate(hist, t_date=args.t_date)
    eng = LifecycleEngine()
    r = eng.evaluate(technical_result=tech)

    print("=== LifecycleEngine smoke ===")
    print(f"code                 : {args.code}")
    print(f"t_date               : {args.t_date}")
    print(f"ok / status          : {r.ok} / {r.data_status.value}")
    print(f"phase (raw V2)       : {r.phase} ({r.phase_cn})")
    print(f"lifecycle            : {r.lifecycle.value}")
    print(f"is_priority_candidate: {r.is_priority_candidate}")
    print(f"source               : {r.source}")
    print(f"tags                 : {r.tags}")
    # consistency with TechnicalEngine's own map
    assert r.lifecycle == tech.lifecycle, (
        f"map mismatch engine={r.lifecycle} tech={tech.lifecycle}"
    )
    assert r.phase == tech.phase
    expected_priority = tech.lifecycle in PRIORITY_LIFECYCLES
    assert r.is_priority_candidate == expected_priority
    # map_from_v2_phase documented split
    assert Lifecycle.map_from_v2_phase("MAIN_RISE_ACCEL") == Lifecycle.ACCELERATION
    print("PASS: LifecycleEngine maps technical phase; priority flag OK")


if __name__ == "__main__":
    _smoke()
