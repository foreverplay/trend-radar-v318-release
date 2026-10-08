"""Load V3.1.8 board payloads from JSON or push markdown."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

SECTION_MAP = {
    "成熟入场榜": "mature_entry",
    "趋势全榜": "trend_all",
    "观察池": "watch_pool",
    "过热延伸": "extended",
}

HEADER_MAP = {
    "股票": "stock",
    "阶段": "stage",
    "TrendGrade": "trend_grade",
    "成熟度": "maturity",
    "EntryStatus": "entry_status",
    "路径": "path",
    "趋势涨幅%": "trend_gain_pct",
    "Tech": "tech",
    "Cont": "cont",
    "Sector": "sector",
    "保护价": "protect",
    "破坏价": "break_price",
    "ExitSignal": "exit_signal",
}


def parse_push_markdown(text: str, t_date: Optional[str] = None) -> Dict[str, Any]:
    boards: Dict[str, List[Dict[str, Any]]] = {
        "mature_entry": [],
        "trend_all": [],
        "watch_pool": [],
        "extended": [],
    }
    current = None
    header = None
    regime = None
    market_score = None
    disclaimer = None
    for line in text.splitlines():
        if line.startswith("regime=") or "MarketScore=" in line:
            m = re.search(r"regime=([^\s（(]+)", line)
            if m:
                regime = m.group(1)
            m2 = re.search(r"MarketScore=([0-9.]+)", line)
            if m2:
                market_score = float(m2.group(1))
        if line.startswith(">") and "禁止荐买" in line:
            disclaimer = line.lstrip("> ").strip()
        m = re.match(r"^##\s+(\S+)", line)
        if m:
            title = m.group(1)
            current = None
            header = None
            for k, v in SECTION_MAP.items():
                if title.startswith(k):
                    current = v
                    break
            continue
        if current is None:
            continue
        if line.startswith("|") and "股票" in line:
            header = [c.strip() for c in line.strip("|").split("|")]
            continue
        if line.startswith("|") and re.match(r"^\|\s*-+", line):
            continue
        if line.startswith("|") and header:
            cols = [c.strip() for c in line.strip("|").split("|")]
            row: Dict[str, Any] = {}
            for h, c in zip(header, cols):
                row[HEADER_MAP.get(h, h)] = c
            sm = re.match(r"(.+?)\((\d{6})\)", row.get("stock", ""))
            if sm:
                row["name"] = sm.group(1).strip()
                row["code"] = sm.group(2)
            boards[current].append(row)

    if not t_date:
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
        t_date = m.group(1) if m else None

    return {
        "version": "V3.1.8",
        "t_date": t_date,
        "title": f"趋势分析雷达 V3.1.8 观察榜 {t_date or ''}".strip(),
        "regime": regime,
        "market_score": market_score,
        "disclaimer": disclaimer
        or "观察池；A+≠今天必买；禁止荐买 / 预测必涨。",
        "counts": {k: len(v) for k, v in boards.items()},
        "boards": boards,
        "source": "markdown",
    }


def load_boards_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_sample_root() -> Path:
    here = Path(__file__).resolve()
    # backend/api -> backend -> release/data/sample
    cand = here.parents[2] / "data" / "sample"
    if cand.is_dir():
        return cand
    return here.parents[1] / "trend_breakout"


def list_board_dates(sample_root: Optional[Path] = None) -> List[str]:
    root = sample_root or find_sample_root()
    dates = set()
    for p in root.glob("boards_v318_*.json"):
        m = re.search(r"boards_v318_(\d{8})\.json", p.name)
        if m:
            ymd = m.group(1)
            dates.add(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
    for p in root.glob("push_close_v318_*.md"):
        m = re.search(r"push_close_v318_(\d{8})\.md", p.name)
        if m:
            ymd = m.group(1)
            dates.add(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
    return sorted(dates, reverse=True)


def load_latest(sample_root: Optional[Path] = None) -> Dict[str, Any]:
    root = sample_root or find_sample_root()
    jsons = sorted(root.glob("boards_v318_*.json"), reverse=True)
    if jsons:
        return load_boards_json(jsons[0])
    mds = sorted(root.glob("push_close_v318_*.md"), reverse=True)
    if mds:
        return parse_push_markdown(mds[0].read_text(encoding="utf-8"))
    raise FileNotFoundError(f"No boards sample under {root}")


def load_by_date(date: str, sample_root: Optional[Path] = None) -> Dict[str, Any]:
    root = sample_root or find_sample_root()
    ymd = date.replace("-", "")
    jp = root / f"boards_v318_{ymd}.json"
    if jp.is_file():
        return load_boards_json(jp)
    mp = root / f"push_close_v318_{ymd}.md"
    if mp.is_file():
        return parse_push_markdown(mp.read_text(encoding="utf-8"), t_date=date)
    raise FileNotFoundError(f"No boards for date {date}")
