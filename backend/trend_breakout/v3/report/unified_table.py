"""
Unified result table writer — Phase 12 / Plan §26.

Writes batch JSON + Markdown:
  trend_bot_results_{t_date}.json
  trend_bot_results_{t_date}.md

Columns (Plan §26): 股票 / 阶段 / 等级 / Tech / Fund / Sector / Cont / 消耗 /
风险 / 保护价 / 破坏价 / ExitSignal

Sort via existing ``v3.bot.sort_results``. Does NOT cut over live V2.3 push.
禁止荐买 / 预测必涨.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from v3.bot import (  # noqa: E402
    TrendConfirmationBot,
    results_to_markdown_table,
    sort_results,
)
from v3.config import TREND_BOT_CONFIG  # noqa: E402
from v3.report.detail import format_stock_detail  # noqa: E402
from v3.types import (  # noqa: E402
    DataStatus,
    ExitSignal,
    Grade,
    Lifecycle,
    MarketRegime,
    RiskLevel,
    TrendBotResult,
)

# Asia/Shanghai for report timestamps
_CST = timezone(timedelta(hours=8))

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "out"


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _parse_codes(raw: str) -> List[str]:
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [_norm_code(p) for p in parts if p]


def _enum_val(x: Any, default: str = "") -> str:
    if x is None:
        return default
    if hasattr(x, "value"):
        return str(x.value)
    return str(x)


def _result_row_dict(r: TrendBotResult) -> Dict[str, Any]:
    """Flat Plan §26 row + key extras for JSON consumers."""
    name = (r.name or "").strip()
    stock = f"{name}({r.code})" if name else r.code
    return {
        "t_date": r.t_date,
        "code": r.code,
        "name": name,
        "股票": stock,
        "阶段": _enum_val(r.lifecycle, Lifecycle.NONE.value),
        "等级": _enum_val(r.grade, Grade.FILTER.value),
        "Tech": r.tech_score,
        "Fund": r.fund_score,
        "Sector": r.sector_score,
        "Cont": r.continuity_score,
        "消耗": r.consumption_score,
        "风险": _enum_val(r.risk_level, RiskLevel.WATCH.value),
        "保护价": r.protection_price,
        "破坏价": r.break_price,
        "ExitSignal": _enum_val(r.exit_signal, ExitSignal.HOLD.value),
        "warning_price": r.warning_price,
        "close": r.close,
        "regime_v3": _enum_val(r.regime_v3, MarketRegime.NORMAL.value),
        "legacy_regime": r.legacy_regime,
        "risk_off": bool(r.risk_off),
        "data_status": _enum_val(r.data_status, DataStatus.VALID.value),
        "reasons": list(r.reasons or []),
        "risks": list(r.risks or []),
        "notes": list(r.notes or []),
    }


def results_to_unified_markdown(
    results: Sequence[TrendBotResult],
    t_date: str,
    *,
    include_details: bool = True,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Full Markdown report: header + Plan §26 table + optional Plan §27 details.
    """
    sorted_rows = sort_results(list(results))
    meta = meta or {}
    regime = legacy = ""
    risk_off = False
    if sorted_rows:
        r0 = sorted_rows[0]
        regime = _enum_val(r0.regime_v3, "")
        legacy = r0.legacy_regime or ""
        risk_off = bool(r0.risk_off)

    lines: List[str] = [
        f"# TrendConfirmationBot V3.0 统一结果 — as-of {t_date}",
        "",
        f"**生成时间（CST）：** {datetime.now(_CST).strftime('%Y-%m-%d %H:%M:%S')} CST",
        f"**phase：** {TREND_BOT_CONFIG.get('phase')}  "
        f"**version：** {TREND_BOT_CONFIG.get('version')}  "
        f"**DEFAULT_EXIT：** `{TREND_BOT_CONFIG.get('exit', {}).get('default_strategy', '')}`",
        f"**regime_v3：** {regime or '—'}  **legacy：** {legacy or '—'}  "
        f"**risk_off：** {risk_off}",
        f"**n：** {len(sorted_rows)}",
        "",
        "> 观察池 / 结构确认输出。**禁止荐买 / 预测必涨**。"
        " Live close-push 仍走 V2.3 RecommendationScore 观察榜，直至显式切流。",
        "",
        "## 统一表（Plan §26）",
        "",
        results_to_markdown_table(sorted_rows),
        "",
    ]
    if meta:
        lines.append("## Meta")
        lines.append("")
        for k, v in meta.items():
            lines.append(f"- **{k}：** {v}")
        lines.append("")

    if include_details and sorted_rows:
        lines.append("## 个股明细（Plan §27）")
        lines.append("")
        for r in sorted_rows:
            lines.append(format_stock_detail(r))

    lines.append("---")
    lines.append("")
    lines.append(
        "*JSON+Markdown only（Web deferred）。Sort: grade A+→FILTER, Cont↓ Tech↓ Sector↓.*"
    )
    lines.append("")
    return "\n".join(lines)


def write_unified_report(
    results: Sequence[TrendBotResult],
    out_dir: Union[str, Path],
    t_date: str,
    *,
    include_details: bool = True,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Path]:
    """
    Write ``trend_bot_results_{t_date}.json`` + ``.md`` under ``out_dir``.

    Returns ``{"json": Path, "md": Path}``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t = str(t_date).strip()
    sorted_rows = sort_results(list(results))

    payload: Dict[str, Any] = {
        "t_date": t,
        "generated_at": datetime.now(_CST).isoformat(timespec="seconds"),
        "version": TREND_BOT_CONFIG.get("version"),
        "phase": TREND_BOT_CONFIG.get("phase"),
        "default_exit": TREND_BOT_CONFIG.get("exit", {}).get("default_strategy"),
        "columns_plan_26": [
            "股票",
            "阶段",
            "等级",
            "Tech",
            "Fund",
            "Sector",
            "Cont",
            "消耗",
            "风险",
            "保护价",
            "破坏价",
            "ExitSignal",
        ],
        "n": len(sorted_rows),
        "meta": meta or {},
        "results": [_result_row_dict(r) for r in sorted_rows],
        # full dumps for tooling (optional consumers)
        "results_full": [r.to_dict() for r in sorted_rows],
        "disclaimer": "观察池/结构确认；禁止荐买/预测必涨；live 仍 V2.3 直至显式切流",
    }

    json_path = out / f"trend_bot_results_{t}.json"
    md_path = out / f"trend_bot_results_{t}.md"

    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    md_path.write_text(
        results_to_unified_markdown(
            sorted_rows, t, include_details=include_details, meta=meta
        ),
        encoding="utf-8",
    )
    return {"json": json_path, "md": md_path}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "TrendConfirmationBot V3 Phase 12 — unified JSON+Markdown report "
            "(Plan §26 table + §27 details)"
        )
    )
    parser.add_argument(
        "--codes",
        default="002768,301108,300964",
        help="Comma-separated code6 list",
    )
    parser.add_argument("--t-date", default="2026-09-21", help="As-of date YYYY-MM-DD")
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        default=False,
        help="Skip Plan §27 per-stock detail blocks in Markdown",
    )
    parser.add_argument(
        "--no-stale-meta",
        action="store_true",
        default=False,
        help="Disallow stale market_meta.json when t_date mismatches",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    codes = _parse_codes(args.codes)
    bot = TrendConfirmationBot()
    results = bot.evaluate_many(
        codes,
        t_date=args.t_date,
        persist_continuity=False,
        write_archive=False,
        allow_stale_meta=not args.no_stale_meta,
    )
    paths = write_unified_report(
        results,
        args.out_dir,
        args.t_date,
        include_details=not args.no_details,
        meta={"codes": ",".join(codes), "source": "v3.report.unified_table CLI"},
    )
    print(f"wrote {paths['json']}")
    print(f"wrote {paths['md']}")
    print()
    print(results_to_markdown_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = [
    "write_unified_report",
    "results_to_unified_markdown",
    "DEFAULT_OUT_DIR",
]
