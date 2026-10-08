"""
FundamentalEngine — Phase 5 wrapper around V2 FundamentalEngine.

Reuses ``v2_fundamental.FundamentalEngine.evaluate`` unchanged.
Never invents fund_score=0 when V2 reports DATA_INSUFFICIENT.

Does NOT cut over live V2.3 push/scan.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.config import get_config  # noqa: E402
from v3.types import DataStatus  # noqa: E402
from v2_fundamental import FundamentalEngine as V2FundamentalEngine  # noqa: E402
from v2_fundamental import FundamentalResult  # noqa: E402


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class FundamentalEngineResult:
    """
    V3 fundamental evaluation of one stock.

    ``fund_score`` is None when data is insufficient (never fake 0).
    ``level`` mirrors V2: 优秀/良好/一般/风险/DATA_INSUFFICIENT.
    ``data_status`` is DATA_INSUFFICIENT when V2 says so.
    """

    ok: bool = False
    data_status: DataStatus = DataStatus.VALID
    code: Optional[str] = None
    fund_score: Optional[float] = None
    level: str = "DATA_INSUFFICIENT"
    industry: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    checklist: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    # raw V2 payload
    fundamental: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_status"] = (
            self.data_status.value
            if isinstance(self.data_status, DataStatus)
            else self.data_status
        )
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class FundamentalEngine:
    """
    V3 FundamentalEngine — thin wrap of ``v2_fundamental.FundamentalEngine``.

    Signature matches V2: ``evaluate(code, pe=None, industry_pe=None)``.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        v2: Optional[V2FundamentalEngine] = None,
    ) -> None:
        self.config = config if config is not None else get_config()
        self._v2 = v2 or V2FundamentalEngine()

    def load(self) -> None:
        self._v2.load()

    @property
    def v2(self) -> V2FundamentalEngine:
        return self._v2

    def evaluate(
        self,
        code: str,
        pe: Optional[float] = None,
        industry_pe: Optional[float] = None,
    ) -> FundamentalEngineResult:
        code6 = _norm_code(code)
        try:
            fr: FundamentalResult = self._v2.evaluate(
                code6, pe=pe, industry_pe=industry_pe
            )
            fund_d = fr.to_dict() if hasattr(fr, "to_dict") else {}

            # Never fake 0: preserve None when insufficient
            if fr.data_insufficient or fr.level == "DATA_INSUFFICIENT":
                status = DataStatus.DATA_INSUFFICIENT
                fund_score: Optional[float] = None
                ok = False
                level = "DATA_INSUFFICIENT"
            elif not fr.ok and fr.score is None:
                status = DataStatus.DATA_INSUFFICIENT
                fund_score = None
                ok = False
                level = fr.level or "DATA_INSUFFICIENT"
            else:
                status = DataStatus.VALID
                fund_score = None if fr.score is None else float(fr.score)
                ok = bool(fr.ok)
                level = fr.level or "一般"

            tags = list(fr.tags or [])
            notes = list(fr.notes or [])
            # Ensure DATA_INSUFFICIENT tag when status says so
            if status == DataStatus.DATA_INSUFFICIENT and "DATA_INSUFFICIENT" not in tags:
                tags = ["DATA_INSUFFICIENT"] + tags

            return FundamentalEngineResult(
                ok=ok,
                data_status=status,
                code=code6,
                fund_score=fund_score,
                level=level,
                industry=fr.industry,
                tags=tags,
                notes=notes,
                checklist=dict(fr.checklist or {}),
                metrics=dict(fr.metrics or {}),
                source=fr.source or "",
                fundamental=fund_d,
            )
        except Exception as exc:  # noqa: BLE001
            return FundamentalEngineResult(
                ok=False,
                data_status=DataStatus.ERROR,
                code=code6,
                fund_score=None,
                level="DATA_INSUFFICIENT",
                tags=["ERROR", "DATA_INSUFFICIENT"],
                notes=[f"fund_err:{type(exc).__name__}"],
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Smoke (__main__)
# ---------------------------------------------------------------------------


def _smoke() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="FundamentalEngine smoke")
    parser.add_argument("--code", default="002768")
    args = parser.parse_args()

    eng = FundamentalEngine()
    r = eng.evaluate(args.code)

    print("=== FundamentalEngine smoke ===")
    print(f"code        : {r.code}")
    print(f"ok / status : {r.ok} / {r.data_status.value}")
    print(f"fund_score  : {r.fund_score}")
    print(f"level       : {r.level}")
    print(f"industry    : {r.industry}")
    print(f"tags        : {r.tags}")
    print(f"notes       : {r.notes}")
    print(f"source      : {r.source}")
    if r.error:
        print(f"error       : {r.error}")

    assert r.data_status != DataStatus.ERROR, f"FundamentalEngine ERROR: {r.error}"
    if r.data_status == DataStatus.DATA_INSUFFICIENT:
        assert r.fund_score is None, "must not fake fund_score=0 when insufficient"
        assert r.level == "DATA_INSUFFICIENT"
    else:
        assert r.fund_score is not None or not r.ok
    print("PASS: FundamentalEngine fund_score/data_status")


if __name__ == "__main__":
    _smoke()
