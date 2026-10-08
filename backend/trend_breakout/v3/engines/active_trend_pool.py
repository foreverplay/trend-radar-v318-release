"""
ActiveTrendPool — V3.1 persistence until TREND_FAILURE.

Discovery universe remains top600 (external). Pool keeps A+/A names
until trend failure / broken — OVERHEAT does not eject.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from v3.v31.config import V31_CONFIG
from v3.v31.types import PoolEntry, TrendState


@dataclass
class ActiveTrendPoolResult:
    code: str
    in_pool: bool = False
    entry: Optional[PoolEntry] = None
    action: str = "NONE"  # ENTER|STAY|EXIT|NONE
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.entry is not None:
            d["entry"] = self.entry.to_dict()
        return d


class ActiveTrendPool:
    """In-memory (+ optional JSON) pool store for regression / future bot wire."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        store_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.config = config or V31_CONFIG
        self.pool_cfg = dict(self.config.get("pool") or {})
        self.store_path = Path(store_path) if store_path else None
        self._entries: Dict[str, PoolEntry] = {}
        if self.store_path and self.store_path.is_file():
            self.load()

    def _norm(self, code: str) -> str:
        c = str(code).strip()
        if c.startswith(("sh", "sz", "bj")):
            c = c[2:]
        if "." in c:
            c = c.split(".")[0]
        return c.zfill(6)

    def get(self, code: str) -> Optional[PoolEntry]:
        return self._entries.get(self._norm(code))

    def is_active(self, code: str) -> bool:
        e = self.get(code)
        return bool(e and e.active)

    def update(
        self,
        *,
        code: str,
        t_date: str,
        grade: str,
        trend_state: Union[str, TrendState, None],
        broken: bool = False,
    ) -> ActiveTrendPoolResult:
        code6 = self._norm(code)
        g = str(grade or "").strip().upper()
        if g == "A_PLUS":
            g = "A+"
        state = (
            trend_state.value
            if isinstance(trend_state, TrendState)
            else str(trend_state or TrendState.NONE.value).upper()
        )
        entry_grades = set(self.pool_cfg.get("entry_grades") or ("A+", "A"))
        exit_states = set(self.pool_cfg.get("exit_states") or ("TREND_FAILURE",))
        notes: List[str] = []
        cur = self._entries.get(code6)

        # Exit conditions
        if broken or state in exit_states:
            if cur and cur.active:
                cur.active = False
                cur.exit_reason = "BROKEN" if broken else state
                cur.last_grade = g
                cur.last_state = state
                self._entries[code6] = cur
                notes.append(f"EXIT pool reason={cur.exit_reason}")
                return ActiveTrendPoolResult(
                    code=code6, in_pool=False, entry=cur, action="EXIT", notes=notes
                )
            return ActiveTrendPoolResult(
                code=code6, in_pool=False, entry=cur, action="NONE", notes=["not in pool"]
            )

        # Enter
        if cur is None or not cur.active:
            if g in entry_grades:
                peak = g
                ent = PoolEntry(
                    code=code6,
                    entered_date=t_date,
                    peak_grade=peak,
                    last_grade=g,
                    last_state=state,
                    active=True,
                )
                self._entries[code6] = ent
                notes.append(f"ENTER pool grade={g}")
                return ActiveTrendPoolResult(
                    code=code6, in_pool=True, entry=ent, action="ENTER", notes=notes
                )
            return ActiveTrendPoolResult(
                code=code6, in_pool=False, entry=cur, action="NONE", notes=["no entry"]
            )

        # Stay — update peak
        rank = {"A+": 2, "A": 1}
        if rank.get(g, 0) > rank.get(cur.peak_grade, 0):
            cur.peak_grade = g
        cur.last_grade = g
        cur.last_state = state
        self._entries[code6] = cur
        notes.append("STAY in pool")
        return ActiveTrendPoolResult(
            code=code6, in_pool=True, entry=cur, action="STAY", notes=notes
        )

    def save(self) -> None:
        if not self.store_path:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v.to_dict() for k, v in self._entries.items()}
        self.store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> None:
        if not self.store_path or not self.store_path.is_file():
            return
        raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        self._entries = {}
        for k, v in (raw or {}).items():
            self._entries[k] = PoolEntry(**v)

    def active_codes(self) -> List[str]:
        return [k for k, v in self._entries.items() if v.active]


__all__ = ["ActiveTrendPool", "ActiveTrendPoolResult"]
