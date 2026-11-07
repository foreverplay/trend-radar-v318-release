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


def attach_refresh(payload: Dict[str, Any], sample_root: Path) -> Dict[str, Any]:
    # 刷榜进程写入；页面据此知道当前文件是否还是上一轮导出。
    path = sample_root.parent / "cache" / "board_refresh.json"
    if not path.is_file():
        return payload
    try:
        payload["refresh"] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    return payload


def load_latest(sample_root: Optional[Path] = None) -> Dict[str, Any]:
    root = sample_root or find_sample_root()
    jsons = sorted(root.glob("boards_v318_*.json"), reverse=True)
    if jsons:
        return attach_refresh(load_boards_json(jsons[0]), root)
    mds = sorted(root.glob("push_close_v318_*.md"), reverse=True)
    if mds:
        return attach_refresh(parse_push_markdown(mds[0].read_text(encoding="utf-8")), root)
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

# ---- Dividend Trend V3.1.9 (additive; Growth boards unchanged) ----

DIVIDEND_SECTION_MAP = {
    "优质红利": "quality",
    "当前机会": "opportunity",
    "风险观察": "risk_watch",
}

DIVIDEND_SECTION_TITLES = {
    "quality": "优质红利",
    "opportunity": "当前机会",
    "risk_watch": "风险观察",
}


def _empty_dividend_boards() -> Dict[str, Dict[str, Any]]:
    return {
        key: {"id": key, "title": title, "rows": []}
        for key, title in DIVIDEND_SECTION_TITLES.items()
    }


def normalize_dividend_payload(raw: Dict[str, Any], source: str = "json") -> Dict[str, Any]:
    """Map Chinese board keys → stable API ids; keep Chinese title on each section."""
    boards = _empty_dividend_boards()
    src_boards = raw.get("boards") or {}
    if isinstance(src_boards, dict):
        for cn_title, rows in src_boards.items():
            api_id = DIVIDEND_SECTION_MAP.get(cn_title)
            if not api_id:
                # already-normalized id
                if cn_title in DIVIDEND_SECTION_TITLES and isinstance(rows, dict):
                    boards[cn_title] = {
                        "id": cn_title,
                        "title": rows.get("title") or DIVIDEND_SECTION_TITLES[cn_title],
                        "rows": list(rows.get("rows") or []),
                    }
                elif cn_title in DIVIDEND_SECTION_TITLES and isinstance(rows, list):
                    boards[cn_title]["rows"] = list(rows)
                continue
            if isinstance(rows, dict):
                boards[api_id] = {
                    "id": api_id,
                    "title": rows.get("title") or cn_title,
                    "rows": list(rows.get("rows") or []),
                }
            else:
                boards[api_id]["rows"] = list(rows or [])

    as_of = raw.get("as_of") or raw.get("t_date")
    t_date = None
    if isinstance(as_of, str) and as_of:
        t_date = as_of[:10] if len(as_of) >= 10 else as_of

    counts = {k: len(v.get("rows") or []) for k, v in boards.items()}
    return {
        "strategy": raw.get("strategy") or "DIVIDEND_TREND",
        "version": raw.get("version") or "V3.1.9",
        "phase": raw.get("phase"),
        "t_date": t_date,
        "as_of": as_of,
        "generated_at": raw.get("generated_at"),
        "timezone": raw.get("timezone") or "Asia/Shanghai",
        "title": f"红利趋势榜 V3.1.9 {t_date or ''}".strip(),
        "disclaimer": raw.get("disclaimer")
        or "观察池；红利三区须当日趋势观察评级 A+/A，当前机会红利等级也须 A+/A；禁止荐买 / 预测必涨。",
        "counts": counts,
        "boards": boards,
        "grade_counts": raw.get("grade_counts") or {},
        "risk_counts": raw.get("risk_counts") or {},
        "entry_counts": raw.get("entry_counts") or {},
        "n_scored": raw.get("n_scored"),
        "n_trap": raw.get("n_trap"),
        "known_gaps": raw.get("known_gaps") or [],
        "publish_close_v318_untouched": raw.get("publish_close_v318_untouched", True),
        "source": source,
    }


def list_dividend_dates(sample_root: Optional[Path] = None) -> List[str]:
    root = sample_root or find_sample_root()
    dates = set()
    for p in root.glob("dividend_board_*.json"):
        if p.name.endswith("_summary.json"):
            continue
        m = re.search(r"dividend_board_(\d{8})\.json", p.name)
        if m:
            ymd = m.group(1)
            dates.add(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
    return sorted(dates, reverse=True)


def load_dividend_latest(sample_root: Optional[Path] = None) -> Dict[str, Any]:
    root = sample_root or find_sample_root()
    dated = sorted(
        (p for p in root.glob("dividend_board_*.json") if not p.name.endswith("_summary.json")),
        reverse=True,
    )
    if dated:
        return normalize_dividend_payload(load_boards_json(dated[0]), source="json")
    latest = root / "dividend_board_latest_summary.json"
    if latest.is_file():
        return normalize_dividend_payload(load_boards_json(latest), source="json-summary")
    raise FileNotFoundError(f"No dividend boards sample under {root}")


def load_dividend_by_date(date: str, sample_root: Optional[Path] = None) -> Dict[str, Any]:
    root = sample_root or find_sample_root()
    ymd = date.replace("-", "")
    jp = root / f"dividend_board_{ymd}.json"
    if jp.is_file():
        return normalize_dividend_payload(load_boards_json(jp), source="json")
    sp = root / f"dividend_board_{ymd}_summary.json"
    if sp.is_file():
        return normalize_dividend_payload(load_boards_json(sp), source="json-summary")
    raise FileNotFoundError(f"No dividend boards for date {date}")
