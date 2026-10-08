"""
V3.1.5 / V3.1.6 / V3.1.7 EntrySignal emitter — realtime, as-of ≤ t only.

V3.1.5: CONFIRMED + tech/cont + EntryWindow PRIME|CONFIRMED (too early).
V3.1.6 current rule: state∈{MAIN_RISE,ACCELERATION,NEW_WAVE} + maturity∈{MATURE,ADVANCED}
  + EntryWindow PRIME|CONFIRMED + existing tech/cont/sector floors (NOT raised).
  FundRisk HIGH blocks; TrendFailure blocks; Exhaustion HIGH cannot be PRIME
  (handled in EntryWindow).

Decoupled from MajorGate / MajorPromotionDate / future peak labels.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from v3.v31.config import A_PLUS_PATH_CONFIG, get_v311_config, get_v315_label, get_v316_label, get_v317_label
from v3.v31.types import EntrySignalEvent, EntryWindow, TrendMaturityLevel, TrendState

# Feature names that must NEVER appear as live EntrySignal inputs
_LOOKAHEAD_FORBIDDEN = frozenset(
    {
        "major_id",
        "major_label",
        "future_peak_gain",
        "future_peak",
        "forward_return",
        "forward20dMaxReturn",
        "forward40dMaxReturn",
        "forward60dMaxReturn",
        "forward120dMaxReturn",
        "majorHit",
        "majorType",
        "peakToEndGiveback",
        "majorLabelDate",
        "leadDays",
    }
)

_STRENGTH_STATES = frozenset(
    {
        TrendState.CONFIRMED.value,
        TrendState.MAIN_RISE.value,
        TrendState.ACCELERATION.value,
        TrendState.NEW_WAVE.value,
    }
)

_V316_ENTRY_STATES = frozenset(
    {
        TrendState.MAIN_RISE.value,
        TrendState.ACCELERATION.value,
        TrendState.NEW_WAVE.value,
    }
)

_V316_MATURITY = frozenset(
    {
        TrendMaturityLevel.MATURE.value,
        TrendMaturityLevel.ADVANCED.value,
    }
)


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def check_lookahead_features(features: Optional[Dict[str, Any]]) -> List[str]:
    """Return forbidden keys present in a feature dict (LOOKAHEAD_ERROR evidence)."""
    if not features:
        return []
    bad = []
    for k in features.keys():
        lk = str(k)
        if lk in _LOOKAHEAD_FORBIDDEN or lk.startswith("future_"):
            bad.append(lk)
    return bad


class EntrySignalEmitter:
    """
    Emit EntrySignal on rising edge for a chosen rule.

    Rules:
      - current: V3.1.6 mature gate when maturity_level provided; else V3.1.5
      - classic: A_PLUS_PATH_CONFIG CLASSIC floors
      - strength: A_PLUS_PATH_CONFIG STRENGTH + state + HTF

    Major / wave_level / peak_gain_from_anchor never gate emission.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        version: str = "3.1.6",
    ) -> None:
        self.version = str(version)
        self.v311 = config or get_v311_config()
        self.paths = self.v311.get("a_plus_paths") or A_PLUS_PATH_CONFIG
        if self.version.startswith("3.1.7"):
            self.label_cfg = get_v317_label()
        elif self.version.startswith("3.1.6"):
            self.label_cfg = get_v316_label()
        else:
            self.label_cfg = get_v315_label()
        self._prev_ok: Dict[str, bool] = {}  # key = f"{symbol}:{rule}"

    def reset(self) -> None:
        self._prev_ok = {}

    def evaluate_day(
        self,
        *,
        symbol: str,
        t_date: str,
        entry_price: Optional[float],
        trend_state: str,
        entry_window: str,
        tech: Optional[float],
        cont: Optional[float],
        sector: Optional[float] = None,
        fund: Optional[float] = None,
        fund_risk_level: Optional[str] = None,
        risk_level: Optional[str] = None,
        htf_ok: bool = False,
        broken: bool = False,
        grade: Optional[str] = None,
        path: Optional[str] = None,
        trend_id: int = 0,
        wave_id: int = 0,
        wave_level: str = "",
        feature_audit: Optional[Dict[str, Any]] = None,
        rules: Optional[Tuple[str, ...]] = ("current", "classic", "strength"),
        # V3.1.6 / V3.1.7
        maturity_level: Optional[str] = None,
        maturity_score: Optional[float] = None,
        structural_completed: bool = False,
        exhaustion: Optional[str] = None,
        follow_through: Optional[str] = None,
        current_wave_return: Optional[float] = None,
        realized_trend_score: Optional[float] = None,
        trend_age_days: Optional[int] = None,
        low_realized_trend: bool = False,
    ) -> List[EntrySignalEvent]:
        """Return rising-edge EntrySignals for requested rules (as-of only)."""
        bad = check_lookahead_features(feature_audit)
        lookahead_error = bool(bad)
        out: List[EntrySignalEvent] = []
        if entry_price is None or entry_price <= 0:
            for rule in rules or ():
                self._prev_ok[f"{symbol}:{rule}"] = False
            return out

        state = str(trend_state or "").upper()
        ew = str(entry_window or "").upper()
        tech_v = _f(tech)
        cont_v = _f(cont)
        sec_v = _f(sector)
        mat = str(maturity_level or "").upper() if maturity_level else None

        for rule in rules or ("current",):
            ok, tags = self._rule_ok(
                rule=rule,
                state=state,
                ew=ew,
                tech=tech_v,
                cont=cont_v,
                sector=sec_v,
                fund=_f(fund),
                fund_risk_level=str(fund_risk_level or ""),
                risk_level=str(risk_level or "WATCH").upper(),
                htf_ok=bool(htf_ok),
                broken=bool(broken),
                maturity_level=mat,
                structural_completed=bool(structural_completed),
                exhaustion=str(exhaustion or "").upper(),
                follow_through=str(follow_through or "").upper(),
                current_wave_return=_f(current_wave_return),
                realized_trend_score=_f(realized_trend_score),
                trend_age_days=int(trend_age_days or 0),
                low_realized_trend=bool(low_realized_trend),
            )
            key = f"{symbol}:{rule}"
            prev = bool(self._prev_ok.get(key))
            self._prev_ok[key] = ok
            if not ok or prev:
                continue
            if lookahead_error:
                tags = list(tags) + ["LOOKAHEAD_ERROR"] + [f"bad:{b}" for b in bad]
            if low_realized_trend:
                tags = list(tags) + ["LOW_REALIZED_TREND"]
            ev = EntrySignalEvent(
                symbol=str(symbol).zfill(6),
                signalDate=t_date,
                entryPrice=float(entry_price),
                entryWindow=ew,
                rule=rule,
                trendState=state,
                tech=tech_v,
                cont=cont_v,
                sector=sec_v,
                htfOk=bool(htf_ok),
                grade=grade,
                path=path,
                trendId=int(trend_id or 0),
                waveId=int(wave_id or 0),
                waveLevel=str(wave_level or ""),
                tags=tags,
                lookaheadError=lookahead_error,
                trendMaturity=mat,
                maturityScore=_f(maturity_score),
                structuralCompleted=bool(structural_completed),
            )
            out.append(ev)
        return out

    def _rule_ok(
        self,
        *,
        rule: str,
        state: str,
        ew: str,
        tech: Optional[float],
        cont: Optional[float],
        sector: Optional[float],
        fund: Optional[float],
        fund_risk_level: str,
        risk_level: str,
        htf_ok: bool,
        broken: bool,
        maturity_level: Optional[str],
        structural_completed: bool,
        exhaustion: str,
        follow_through: str = "",
        current_wave_return: Optional[float] = None,
        realized_trend_score: Optional[float] = None,
        trend_age_days: int = 0,
        low_realized_trend: bool = False,
    ) -> Tuple[bool, List[str]]:
        tags: List[str] = []
        if broken or state == TrendState.TREND_FAILURE.value:
            return False, ["blocked_failure"]
        if tech is None or cont is None:
            return False, ["missing_tech_cont"]

        if rule == "current":
            es = (self.label_cfg.get("entry_signal") or {})
            # FundRisk HIGH blocks EntrySignal (V3.1.6+)
            if es.get("block_fund_risk_high", True) and fund_risk_level == "HIGH":
                return False, ["fund_HIGH"]
            windows = set(es.get("entry_windows") or ("PRIME", "CONFIRMED"))
            use_v317 = self.version.startswith("3.1.7")
            use_v316 = (not use_v317) and (
                self.version.startswith("3.1.6") or maturity_level is not None
            )
            if use_v317:
                states = set(es.get("states") or _V316_ENTRY_STATES)
                mat_ok = set(es.get("maturity_levels") or ("MATURE",))
                if state not in states:
                    return False, ["state_not_mature_entry"]
                if not maturity_level or maturity_level not in mat_ok:
                    # ADVANCED is not a fresh entry maturity for current rule
                    return False, [f"maturity={maturity_level or 'None'}"]
                if ew not in windows:
                    return False, [f"entry_window={ew}"]
                if exhaustion == "HIGH" and ew == EntryWindow.PRIME.value:
                    return False, ["exhaustion_HIGH_not_prime"]
                if ew == EntryWindow.PRIME.value:
                    if es.get("require_follow_through_pass_for_prime", True) and follow_through != "PASS":
                        return False, [f"follow_through={follow_through or 'None'}"]
                    wr_min = float(es.get("prime_wave_return_min", 0.08))
                    if current_wave_return is not None and current_wave_return < wr_min:
                        return False, [f"wave_return={current_wave_return:.3f}<{wr_min}"]
            elif use_v316:
                states = set(es.get("states") or _V316_ENTRY_STATES)
                mat_ok = set(es.get("maturity_levels") or _V316_MATURITY)
                if state not in states:
                    return False, ["state_not_mature_entry"]
                if not maturity_level or maturity_level not in mat_ok:
                    return False, [f"maturity={maturity_level or 'None'}"]
                if ew not in windows:
                    return False, [f"entry_window={ew}"]
                if exhaustion == "HIGH" and ew == EntryWindow.PRIME.value:
                    return False, ["exhaustion_HIGH_not_prime"]
            else:
                states = set(es.get("states") or _STRENGTH_STATES)
                if state not in states:
                    return False, ["state_not_confirmed"]
                if ew not in windows:
                    return False, [f"entry_window={ew}"]
            tech_min = float(es.get("tech_min", 75.0))
            cont_min = float(es.get("cont_min", 70.0))
            sec_min = float(es.get("sector_min", 60.0))
            if tech < tech_min or cont < cont_min:
                return False, [f"tech/cont {tech:.1f}/{cont:.1f} < {tech_min}/{cont_min}"]
            if sector is not None and sector < sec_min:
                return False, [f"sector={sector:.1f}<{sec_min}"]
            if use_v317:
                tags.append("ENTRY_CURRENT_V317")
            elif use_v316:
                tags.append("ENTRY_CURRENT_V316")
            else:
                tags.append("ENTRY_CURRENT")
            if structural_completed:
                tags.append("STRUCT_COMPLETED")
            return True, tags

        if rule == "classic":
            path = self.paths.get("CLASSIC") or A_PLUS_PATH_CONFIG["CLASSIC"]
            if state not in _STRENGTH_STATES and state != TrendState.COOLING.value:
                if state in (TrendState.NONE.value, TrendState.OBSERVE.value, TrendState.TREND_FAILURE.value):
                    return False, ["no_trend"]
            if tech < float(path["tech_min"]) or cont < float(path["cont_min"]):
                return False, ["classic_tech_cont"]
            if sector is not None and sector < float(path["sector_min"]):
                return False, ["classic_sector"]
            if fund_risk_level == "HIGH":
                return False, ["fund_HIGH"]
            allowed = set(path.get("allowed_risk") or ("SAFE", "WATCH"))
            if state in _STRENGTH_STATES:
                allowed = set(path.get("allowed_risk_relaxed") or (allowed | {"HIGH"}))
            if risk_level not in allowed and risk_level not in ("SAFE", "WATCH", "WARNING", "HIGH"):
                return False, [f"risk={risk_level}"]
            if self.version.startswith("3.1.7"):
                if not maturity_level or maturity_level != TrendMaturityLevel.MATURE.value:
                    return False, [f"classic_maturity={maturity_level or 'None'}"]
                tags.append("CLASSIC_MATURE_GATE")
            tags.append("ENTRY_CLASSIC")
            return True, tags

        if rule == "strength":
            path = self.paths.get("STRENGTH") or A_PLUS_PATH_CONFIG["STRENGTH"]
            states = set(path.get("states") or _STRENGTH_STATES)
            if state not in states:
                return False, ["strength_state"]
            if path.get("require_htf", True) and not htf_ok:
                return False, ["htf_not_ok"]
            if tech < float(path["tech_min"]) or cont < float(path["cont_min"]):
                return False, ["strength_tech_cont"]
            if sector is not None and sector < float(path["sector_min"]):
                return False, ["strength_sector"]
            if fund_risk_level == "HIGH":
                return False, ["fund_HIGH"]
            # V3.1.6: strength path also prefers mature when available
            if self.version.startswith("3.1.6") and maturity_level:
                if maturity_level not in _V316_MATURITY and state == TrendState.CONFIRMED.value:
                    return False, ["strength_immature_confirmed"]
            # V3.1.7: strength must be MATURE
            if self.version.startswith("3.1.7"):
                if not maturity_level or maturity_level != TrendMaturityLevel.MATURE.value:
                    return False, [f"strength_maturity={maturity_level or 'None'}"]
                tags.append("STRENGTH_MATURE_GATE")
            tags.append("ENTRY_STRENGTH")
            return True, tags

        return False, [f"unknown_rule={rule}"]


__all__ = ["EntrySignalEmitter", "check_lookahead_features"]
