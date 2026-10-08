"""
Exit state helper — Phase 8 ratchet persistence (in-memory + optional JSON dir).

API
---
- ``ExitStateStore.update_state(code6, result)`` — persist protection / peak_close / streak
- ``ExitStateStore.get(code6)`` → prior_state dict for ``ExitEngine.evaluate(prior_state=...)``
- Module-level ``update_state`` / ``get_state`` use a process-wide default store

Persistence path (optional): ``v3/exit_state/data/{code6}.json`` when ``persist=True``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from v3.engines.exit_engine import ExitEngineResult
from v3.types import ExitSignal, TrendBotResult

_DEFAULT_DIR = Path(__file__).resolve().parent / "data"


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _result_to_state(result: Union[ExitEngineResult, TrendBotResult, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(result, dict):
        d = result
        sig = d.get("exit_signal")
        return {
            "protection_price": d.get("protection_price"),
            "peak_close": d.get("peak_close"),
            "broken_streak": int(d.get("broken_streak") or 0),
            "warning_price": d.get("warning_price"),
            "break_trend_price": d.get("break_trend_price") or d.get("break_price"),
            "structure_support": d.get("structure_support"),
            "exit_signal": sig.value if isinstance(sig, ExitSignal) else sig,
            "consumption_score": d.get("consumption_score"),
            "t_date": d.get("t_date"),
        }
    if isinstance(result, ExitEngineResult):
        return {
            "protection_price": result.protection_price,
            "peak_close": result.peak_close,
            "broken_streak": int(result.broken_streak or 0),
            "warning_price": result.warning_price,
            "break_trend_price": result.break_trend_price,
            "structure_support": result.structure_support,
            "exit_signal": (
                result.exit_signal.value
                if isinstance(result.exit_signal, ExitSignal)
                else result.exit_signal
            ),
            "t_date": result.t_date,
        }
    # TrendBotResult — limited fields
    return {
        "protection_price": result.protection_price,
        "peak_close": getattr(result, "peak_close", None),
        "broken_streak": int(getattr(result, "broken_streak", 0) or 0),
        "warning_price": result.warning_price,
        "break_trend_price": result.break_price,
        "exit_signal": (
            result.exit_signal.value
            if isinstance(result.exit_signal, ExitSignal)
            else result.exit_signal
        ),
        "consumption_score": result.consumption_score,
        "t_date": result.t_date,
    }


class ExitStateStore:
    """In-memory ratchet state; optional JSON files under ``root``."""

    def __init__(self, root: Optional[Path] = None, *, persist: bool = False) -> None:
        self.root = Path(root) if root is not None else _DEFAULT_DIR
        self.persist = bool(persist)
        self._mem: Dict[str, Dict[str, Any]] = {}

    def get(self, code6: str) -> Dict[str, Any]:
        code = _norm_code(code6)
        if code in self._mem:
            return dict(self._mem[code])
        if self.persist:
            path = self.root / f"{code}.json"
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        self._mem[code] = dict(data)
                        return dict(data)
                except Exception:  # noqa: BLE001
                    pass
        return {}

    def update_state(
        self,
        code6: str,
        result: Union[ExitEngineResult, TrendBotResult, Dict[str, Any]],
    ) -> Dict[str, Any]:
        code = _norm_code(code6)
        state = _result_to_state(result)
        # ratchet: never lower protection / peak in stored state vs previous
        prev = self.get(code)
        if prev:
            pp = prev.get("protection_price")
            np_ = state.get("protection_price")
            if pp is not None and np_ is not None:
                state["protection_price"] = max(float(pp), float(np_))
            elif pp is not None and np_ is None:
                state["protection_price"] = pp
            pk = prev.get("peak_close")
            nk = state.get("peak_close")
            if pk is not None and nk is not None:
                state["peak_close"] = max(float(pk), float(nk))
            elif pk is not None and nk is None:
                state["peak_close"] = pk
        self._mem[code] = state
        if self.persist:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{code}.json"
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(state)

    def clear(self, code6: Optional[str] = None) -> None:
        if code6 is None:
            self._mem.clear()
        else:
            self._mem.pop(_norm_code(code6), None)


# Process-wide default (in-memory; persist off)
_DEFAULT_STORE = ExitStateStore(persist=False)


def get_state(code6: str) -> Dict[str, Any]:
    return _DEFAULT_STORE.get(code6)


def update_state(
    code6: str,
    result: Union[ExitEngineResult, TrendBotResult, Dict[str, Any]],
) -> Dict[str, Any]:
    """Dict-in-memory API: ``update_state(code6, result)``."""
    return _DEFAULT_STORE.update_state(code6, result)


def default_store() -> ExitStateStore:
    return _DEFAULT_STORE


__all__ = [
    "ExitStateStore",
    "get_state",
    "update_state",
    "default_store",
]
