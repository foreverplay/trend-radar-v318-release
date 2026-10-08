#!/usr/bin/env python3
"""Display current near-breakout pool (约每30分钟).

- Re-apply 放量 filter before display (drop VR<1 / missing).
- Print Chinese markdown and write near_pool_display.md for parent to send.
- Empty pool → 当前无放量待突破标的
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

OUT = _PKG_ROOT
TZ = ZoneInfo("Asia/Shanghai")
DISPLAY_PATH = OUT / "near_pool_display.md"
POOL_PATH = OUT / "near_breakout_pool.json"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _vr(item: Dict[str, Any]) -> Optional[float]:
    for k in ("live_vol_ratio", "vol_ratio"):
        v = item.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def filter_fangliang(pool: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Drop 无放量: VR missing or < 1.0."""
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for it in pool:
        vr = _vr(it)
        if vr is None or vr < 1.0:
            dropped.append({
                "code6": it.get("code6"),
                "name": it.get("name"),
                "vol_ratio": vr,
            })
            continue
        kept.append(it)
    return kept, dropped


def format_display(data: Dict[str, Any], pool: List[Dict[str, Any]], dropped_n: int) -> str:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S%z")
    t_date = data.get("t_date") or "-"
    regime = data.get("regime") or "-"
    refreshed = data.get("refreshed_at") or data.get("built_at") or "-"
    lines = [
        f"# 待突破观察池（放量达标）",
        "",
        f"- 数据日：**{t_date}**　市场状态：**{regime}**",
        f"- 展示时间：{now}（Asia/Shanghai）",
        f"- 池更新时间：{refreshed}",
        f"- 当前数量：**{len(pool)}**",
    ]
    if dropped_n:
        lines.append(f"- 本次展示前因无放量剔除：{dropped_n}")
    lines += [
        "",
        "> 仅列出量比≥1.0（成交量≥近均量）的待突破/跟踪标的；**不是买入建议**。",
        "> 规则：无放量(量<均量)不得入池。",
        "",
    ]
    if not pool:
        lines += [
            "**当前无放量待突破标的**",
            "",
            "池为空或全部因量比不足被剔除。可等待下一小时刷新，或收盘后重建观察池。",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "| # | 代码 | 名称 | 状态 | 价格 | 距突破% | 量比 | 行业 |",
        "|---:|---|---|---|---:|---:|---:|---|",
    ]
    for i, it in enumerate(pool, 1):
        price = it.get("live_price")
        if price is None:
            price = it.get("close_T")
        vr = _vr(it)
        vr_s = f"{vr:.3f}" if vr is not None else "-"
        dist = it.get("dist_to_breakout_pct")
        dist_s = f"{dist:.3f}" if dist is not None else "-"
        lines.append(
            "| {i} | {code6} | {name} | {state} | {price} | {dist} | {vr} | {ind} |".format(
                i=i,
                code6=it.get("code6") or "",
                name=it.get("name") or "",
                state=it.get("state") or "",
                price=price if price is not None else "-",
                dist=dist_s,
                vr=vr_s,
                ind=it.get("industry") or "未知",
            )
        )
    lines += [
        "",
        "## 简表（便于推送）",
        "",
    ]
    for it in pool:
        vr = _vr(it)
        vr_s = f"{vr:.2f}" if vr is not None else "-"
        dist = it.get("dist_to_breakout_pct")
        dist_s = f"{dist:.2f}%" if dist is not None else "-"
        px = it.get("live_price")
        if px is None:
            px = it.get("close_T")
        lines.append(
            f"- {it.get('name')}（{it.get('code6')}）｜{it.get('state')}｜价 {px}｜距突破 {dist_s}｜量比 {vr_s}"
        )
    lines.append("")
    return "\n".join(lines)


def show(write: bool = True) -> Dict[str, Any]:
    data = _load_json(POOL_PATH) or {}
    raw_pool = list(data.get("pool") or [])
    pool, dropped = filter_fangliang(raw_pool)
    md = format_display(data, pool, len(dropped))
    if write:
        DISPLAY_PATH.write_text(md, encoding="utf-8")
    print(md)
    return {
        "count": len(pool),
        "dropped_no_vol": len(dropped),
        "display_path": str(DISPLAY_PATH),
        "raw_count": len(raw_pool),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Show near-breakout pool (30-min cadence)")
    ap.add_argument("--no-write", action="store_true", help="Print only, do not write near_pool_display.md")
    args = ap.parse_args()
    meta = show(write=not args.no_write)
    print(
        f"\n<!-- show_near_pool: raw={meta['raw_count']} shown={meta['count']} "
        f"dropped_no_vol={meta['dropped_no_vol']} path={meta['display_path']} -->"
    )


if __name__ == "__main__":
    main()
