"""只读研究运行目录。请求不能指定路径、SQL 或命令。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PATH_KEYS = {"sqlite_path", "output_dir", "artifacts", "parquet_root", "code_path"}


class ResearchReadError(ValueError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def results_root() -> Path:
    env = os.environ.get("RESEARCH_RESULTS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "trend_breakout" / "research_lab" / "results").resolve()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items() if key not in _PATH_KEYS and not key.endswith("_path")}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and (value.startswith("/") or value.startswith("~") or ":\\" in value):
        return "[redacted]"
    return value


def _run_dir(run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id or ""):
        raise ResearchReadError(400, "run_id 不合法")
    root = results_root()
    candidate = (root / run_id).resolve()
    if root != candidate and root not in candidate.parents:
        raise ResearchReadError(400, "run_id 不合法")
    if not candidate.is_dir():
        raise ResearchReadError(404, "没有这个研究运行")
    return candidate


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchReadError(422, f"JSON 无法读取: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ResearchReadError(422, "JSON 不是对象")
    return data


def _one_file(run_dir: Path, stem: str) -> Optional[Path]:
    found = sorted(path for path in run_dir.iterdir() if path.is_file() and path.name.startswith(stem + "_") and path.suffix in {".parquet", ".csv", ".json"})
    if not found:
        return None
    if len(found) > 1:
        raise ResearchReadError(422, f"{stem} 文件不唯一，拒绝猜测")
    return found[0]


def _frame(path: Optional[Path]) -> Optional[pd.DataFrame]:
    if path is None:
        return None
    try:
        if path.suffix == ".parquet":
            return pd.read_parquet(path)
        if path.suffix == ".csv":
            return pd.read_csv(path)
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResearchReadError(422, f"结果文件无法读取: {type(exc).__name__}") from exc
    if isinstance(data, list):
        return pd.DataFrame(data)
    raise ResearchReadError(422, "结果文件不是表格")


def _records(frame: Optional[pd.DataFrame], page: int, page_size: int) -> Dict[str, Any]:
    if frame is None:
        return {"present": False, "rows": [], "total": None, "page": page, "page_size": page_size}
    total = int(len(frame))
    start = (page - 1) * page_size
    part = frame.iloc[start:start + page_size]
    rows = json.loads(part.to_json(orient="records", date_format="iso"))
    return {"present": True, "rows": rows, "total": total, "page": page, "page_size": page_size}


def _page_args(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size < 1 or page_size > 200:
        raise ResearchReadError(400, "分页参数不合法")
    return page, page_size


def _summary(run_dir: Path) -> Dict[str, Any]:
    manifest = _read_json(run_dir / "manifest.json")
    if manifest is None:
        raise ResearchReadError(422, "缺少 manifest.json")
    safe = _redact(manifest)
    files = sorted(path.name for path in run_dir.iterdir() if path.is_file())
    return {
        "run_id": run_dir.name,
        "status": safe.get("status") or "unknown",
        "mode": safe.get("mode"),
        "result_class": safe.get("result_class"),
        "evidence_class": safe.get("evidence_class"),
        "generated_at": safe.get("finished_at") or safe.get("started_at"),
        "config_hash": safe.get("config_hash"),
        "code_hash": safe.get("code_hash"),
        "score_mode": safe.get("score_mode"),
        "price_basis": safe.get("price_basis"),
        "reason": safe.get("reason"),
        "start_date": safe.get("start_date"),
        "end_date": safe.get("end_date"),
        "files": files,
        "not_strategy_evidence": safe.get("evidence_class") == "not_strategy_evidence" or safe.get("result_class") == "diagnostic_not_official_fills",
    }


def status_payload() -> Dict[str, Any]:
    root = results_root()
    precheck = None
    precheck_state = "missing"
    message = "还没有预检文件。在仓库根目录用 .venv/bin/python 运行 backend/trend_breakout/research_lab/precheck_local.py。页面不会填入股票数量。"
    if root.is_dir():
        raw = _read_json(root / "precheck.json")
        if raw is not None:
            precheck = _redact(raw)
            precheck_state = "present"
            message = "覆盖数字只来自预检文件。这不是回测结果。"
    runs = []
    if root.is_dir():
        runs = list_runs()["runs"]
    return {
        "root_ready": root.is_dir(),
        "precheck_state": precheck_state,
        "precheck_message": message,
        "precheck": precheck,
        "run_count": len(runs),
        "commands": [
            ".venv/bin/python backend/trend_breakout/research_lab/precheck_local.py",
            ".venv/bin/python backend/trend_breakout/research_lab/run_research_backtest.py backend/trend_breakout/research_lab/configs/local_formal.json",
            "RESEARCH_RESULTS_ROOT=<独立结果目录> RESEARCH_API_PORT=8791 backend/scripts/run_research_api.sh",
            "健康检查是 GET /api/health；代码里没有 /health，所以 /health 返回 404 是预期",
        ],
        "run_button": None,
    }


def list_runs() -> Dict[str, Any]:
    root = results_root()
    if not root.is_dir():
        return {"runs": [], "message": "研究目录还不存在。先在本机运行预检或回测命令，结果会写到独立研究目录。"}
    runs = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir() or not RUN_ID_RE.fullmatch(child.name):
            continue
        if not (child / "manifest.json").is_file():
            runs.append({"run_id": child.name, "status": "unreadable", "reason": "目录里没有 manifest.json"})
            continue
        try:
            runs.append(_summary(child))
        except ResearchReadError as exc:
            runs.append({"run_id": child.name, "status": "unreadable", "reason": exc.detail})
    message = None if runs else "研究目录是空的，还没有可显示的 run。空目录不会显示收益。"
    return {"runs": runs, "message": message}


def run_detail(run_id: str) -> Dict[str, Any]:
    summary = _summary(_run_dir(run_id))
    summary["sections"] = {
        "grades": _one_file(_run_dir(run_id), "grades") is not None or _one_file(_run_dir(run_id), "signals") is not None,
        "orders": _one_file(_run_dir(run_id), "orders") is not None,
        "trades": _one_file(_run_dir(run_id), "trades") is not None,
        "positions": _one_file(_run_dir(run_id), "daily_positions") is not None,
        "equity": _one_file(_run_dir(run_id), "equity") is not None,
        "metrics": _one_file(_run_dir(run_id), "metrics") is not None,
    }
    return summary


def _date_filter(frame: pd.DataFrame, date: Optional[str]) -> pd.DataFrame:
    if not date:
        return frame
    if not DATE_RE.fullmatch(date):
        raise ResearchReadError(400, "日期格式应为 YYYY-MM-DD")
    for column in ("trade_date", "date", "signal_date"):
        if column in frame.columns:
            values = frame[column].astype(str).str.slice(0, 10)
            return frame.loc[values == date].copy()
    return frame.iloc[0:0]


def grades(run_id: str, date: Optional[str], page: int, page_size: int) -> Dict[str, Any]:
    page, page_size = _page_args(page, page_size)
    run_dir = _run_dir(run_id)
    path = _one_file(run_dir, "grades")
    source = "grades"
    if path is None:
        path = _one_file(run_dir, "signals")
        source = "signals_not_full_universe"
    frame = _frame(path)
    if frame is None:
        return {"present": False, "source": None, "rows": [], "total": None, "message": "这个 run 没有评级或信号文件。"}
    grade_col = "grade" if "grade" in frame.columns else "grade_v318" if "grade_v318" in frame.columns else None
    if grade_col is None:
        return {"present": False, "source": source, "rows": [], "total": 0, "message": "文件里没有 grade 字段，不补评级。"}
    grades_only = frame[frame[grade_col].isin(["A+", "A"])].copy()
    grades_only = _date_filter(grades_only, date)
    payload = _records(grades_only, page, page_size)
    payload["source"] = source
    payload["message"] = "只显示文件中的 A/A+。" if source == "grades" else "这是信号文件里的 A/A+，不是全市场逐日评级。"
    return payload


def table_section(run_id: str, stem: str, date: Optional[str], page: int, page_size: int) -> Dict[str, Any]:
    page, page_size = _page_args(page, page_size)
    frame = _frame(_one_file(_run_dir(run_id), stem))
    if frame is None:
        return {"present": False, "rows": [], "total": None, "message": f"没有 {stem} 文件。"}
    payload = _records(_date_filter(frame, date), page, page_size)
    payload["message"] = None
    return payload


def metrics(run_id: str) -> Dict[str, Any]:
    summary = _summary(_run_dir(run_id))
    raw = _read_json(_one_file(_run_dir(run_id), "metrics")) if _one_file(_run_dir(run_id), "metrics") else None
    if summary["status"] != "completed" or not raw:
        return {
            "present": False,
            "metrics": None,
            "message": summary.get("reason") or "这个 run 没有完成，不显示收益数字。",
        }
    safe = _redact(raw)
    if "total_return" not in safe and "final_equity" not in safe:
        return {"present": False, "metrics": None, "message": "指标文件没有净值字段。"}
    safe["not_strategy_evidence"] = bool(summary["not_strategy_evidence"] or safe.get("evidence_class") == "not_strategy_evidence")
    return {"present": True, "metrics": safe, "message": "数字来自该 run 的 metrics 文件。"}
