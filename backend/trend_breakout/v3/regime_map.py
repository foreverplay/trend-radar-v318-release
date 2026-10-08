"""
Bidirectional market-regime map: V3 Plan labels ↔ V2 legacy.

Chosen mapping (documented):
  STRONG   ↔ BULL
  NORMAL   ↔ NORMAL
  WEAK     ↔ CHOP     # weak breadth / soft tape, not full risk-off
  RISK_OFF ↔ BEAR + risk_off=True

RISK_OFF is never deleted: always carry `legacy_regime` and `risk_off` bool.
A soft BEAR (legacy WEAK→ not used; only RISK_OFF sets risk_off) keeps RISK_OFF
semantics. If callers pass BEAR without risk_off, legacy defaults to WEAK-ish
BEAR representation via legacy_regime='WEAK' only when explicitly requested;
preferred path for RISK_OFF input is BEAR+risk_off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from .types import LegacyRegime, MarketRegime, MarketScore

# Forward: legacy → (regime_v3, risk_off)
_LEGACY_TO_V3 = {
    "STRONG": (MarketRegime.BULL, False),
    "NORMAL": (MarketRegime.NORMAL, False),
    "WEAK": (MarketRegime.CHOP, False),
    "RISK_OFF": (MarketRegime.BEAR, True),
}

# Reverse primary: regime_v3 → legacy (when risk_off False)
_V3_TO_LEGACY = {
    MarketRegime.BULL: "STRONG",
    MarketRegime.NORMAL: "NORMAL",
    MarketRegime.CHOP: "WEAK",
    MarketRegime.BEAR: "WEAK",  # soft bear; overridden to RISK_OFF when risk_off
}


@dataclass(frozen=True)
class RegimePair:
    regime_v3: MarketRegime
    legacy_regime: str
    risk_off: bool

    def to_dict(self) -> dict:
        return {
            "regime_v3": self.regime_v3.value,
            "legacy_regime": self.legacy_regime,
            "risk_off": self.risk_off,
        }


def legacy_to_v3(legacy: Union[str, LegacyRegime, None]) -> RegimePair:
    """Map V2 STRONG/NORMAL/WEAK/RISK_OFF → V3 + risk_off."""
    key = (legacy.value if isinstance(legacy, LegacyRegime) else (legacy or "NORMAL")).strip().upper()
    if key in _LEGACY_TO_V3:
        v3, risk_off = _LEGACY_TO_V3[key]
        return RegimePair(regime_v3=v3, legacy_regime=key, risk_off=risk_off)
    # unknown → NORMAL
    return RegimePair(regime_v3=MarketRegime.NORMAL, legacy_regime="NORMAL", risk_off=False)


def v3_to_legacy(
    regime_v3: Union[str, MarketRegime, None],
    *,
    risk_off: bool = False,
) -> RegimePair:
    """
    Map V3 BULL/NORMAL/BEAR/CHOP → legacy.
    If risk_off=True → legacy RISK_OFF and regime_v3 forced/kept as BEAR.
    """
    if isinstance(regime_v3, MarketRegime):
        v3 = regime_v3
    else:
        try:
            v3 = MarketRegime((regime_v3 or "NORMAL").strip().upper())
        except ValueError:
            v3 = MarketRegime.NORMAL
    if risk_off:
        return RegimePair(regime_v3=MarketRegime.BEAR, legacy_regime="RISK_OFF", risk_off=True)
    legacy = _V3_TO_LEGACY.get(v3, "NORMAL")
    # Pure BEAR without risk_off flag → still legacy WEAK (not RISK_OFF),
    # so RISK_OFF is only present when risk_off=True.
    return RegimePair(regime_v3=v3, legacy_regime=legacy, risk_off=False)


# Aliases matching task naming
from_legacy_regime = legacy_to_v3
to_legacy_regime = v3_to_legacy


def pair_from_market_meta(meta: dict) -> RegimePair:
    """
    Build RegimePair from existing market_meta.json shape.
    Prefers regime_v2; falls back to regime (R0..R4) heuristics.
    """
    legacy = meta.get("regime_v2") or meta.get("legacy_regime")
    if not legacy:
        r = str(meta.get("regime") or "")
        # R0 → RISK_OFF, R1 → WEAK, else NORMAL
        if r == "R0":
            legacy = "RISK_OFF"
        elif r == "R1":
            legacy = "WEAK"
        elif r in ("R3", "R4"):
            legacy = "STRONG" if r == "R4" else "NORMAL"
        else:
            legacy = "NORMAL"
    return legacy_to_v3(str(legacy))


def market_score_from_meta(meta: dict) -> MarketScore:
    """Helper: MarketScore dataclass from market_meta.json dict."""
    pair = pair_from_market_meta(meta)
    notes = []
    if isinstance(meta.get("market_conclusion"), list):
        notes = [str(x) for x in meta["market_conclusion"]]
    elif meta.get("notes"):
        notes = list(meta.get("notes") or [])
    return MarketScore(
        t_date=str(meta.get("t_date") or ""),
        market_score=float(meta.get("market_score") or 0.0),
        regime_v3=pair.regime_v3,
        legacy_regime=pair.legacy_regime,
        risk_off=pair.risk_off,
        cap=int(meta.get("cap") or 0),
        notes=notes,
        breadth=dict(meta.get("breadth") or {}),
        turnover=dict(meta.get("turnover") or {}),
        indices=dict(meta.get("indices") or {}),
    )


# Human-readable table for docs / STRATEGY_V3_0.md
REGIME_MAP_TABLE = """
| legacy_regime (V2) | regime_v3 (Plan) | risk_off | notes |
|--------------------|------------------|----------|-------|
| STRONG             | BULL             | false    | 强趋势 |
| NORMAL             | NORMAL           | false    | 中性 |
| WEAK               | CHOP             | false    | 弱势/震荡偏弱（非 RISK_OFF） |
| RISK_OFF           | BEAR             | true     | 极弱/防守；RISK_OFF 永不丢 |
| (BEAR, risk_off=0) | BEAR             | false    | 软熊；legacy=WEAK |
""".strip()


__all__ = [
    "RegimePair",
    "legacy_to_v3",
    "v3_to_legacy",
    "from_legacy_regime",
    "to_legacy_regime",
    "pair_from_market_meta",
    "market_score_from_meta",
    "REGIME_MAP_TABLE",
]
