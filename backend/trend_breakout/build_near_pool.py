#!/usr/bin/env python3
"""Build near-breakout observation pool after close-side scan.

V2-aware: prefer phases SECOND_BREAKOUT / HEALTHY_PULLBACK / approaching second
(from candidates_v2 / all_results_v2 when present). Falls back to V1 states.

Does NOT loosen S/A hard gates. Writes:
  near_breakout_pool.json / near_breakout_pool.md
Optionally patches report.md with a short 待突破观察池 section.

放量硬门槛（权威）:
  无放量(量<均量)不得入池 — require vol_ratio = Volume/MA20Volume >= 1.0.
  Missing vol_ratio → exclude (don't guess).
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

# Near pool caps by regime (larger than S/A core, still bounded)
NEAR_CAPS = {"R0": 0, "R1": 15, "R2": 25, "R3": 40, "R4": 50}
NEAR_CAPS_V2 = {"RISK_OFF": 0, "WEAK": 15, "NORMAL": 30, "STRONG": 50}
V2_PREF_PHASES = {
    "SECOND_BREAKOUT", "HEALTHY_PULLBACK", "PLATFORM_HOLD", "MAIN_RISE_ACCEL",
    "二次突破", "健康分歧", "平台不破", "主升加速",
}
DEFAULT_DIST_MAX = 0.03  # within 3% below Prev20High
MIN_AVG20_AMT = 3e7  # 3000万


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _is_st(name: str) -> bool:
    n = str(name or "")
    return ("ST" in n) or ("退" in n) or n.startswith(("N", "C"))


def _code_prefix(code6: str, code_full: Optional[str] = None) -> str:
    if code_full and len(code_full) >= 8 and code_full[:2] in ("sh", "sz"):
        return code_full
    c = str(code6).zfill(6)
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    return "sz" + c



def classify_v2(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Prefer V2 phases: SECOND_BREAKOUT / HEALTHY_PULLBACK / approaching second.
    Still require vol_ratio >= 1.0.
    """
    name = r.get("name") or ""
    if _is_st(name):
        return None
    vol_ratio = r.get("vol_ratio")
    if vol_ratio is None and isinstance(r.get("metrics"), dict):
        vol_ratio = r["metrics"].get("vol_ratio")
    if vol_ratio is None:
        return None
    try:
        vr = float(vol_ratio)
    except (TypeError, ValueError):
        return None
    if vr < 1.0:
        return None

    phase = r.get("phase") or ""
    phase_cn = r.get("phase_cn") or r.get("state") or phase
    flags = r.get("flags") or {}
    approaching = bool(flags.get("approaching_second"))
    prefer = (
        phase in V2_PREF_PHASES
        or phase_cn in V2_PREF_PHASES
        or approaching
    )
    # also allow FIRST_BREAKOUT / PLATFORM as soft near
    soft_ok = phase in (
        "FIRST_BREAKOUT", "PLATFORM", "ACCUMULATION", "OBSERVE",
        "首次突破", "平台整理", "底部蓄势", "观察",
    )
    if not prefer and not soft_ok:
        return None
    if r.get("drop"):
        return None
    if r.get("grade") in ("S", "A") and r.get("pushable"):
        # core push handled via from_selected
        return None

    close = float(r.get("close") or (r.get("metrics") or {}).get("close") or 0)
    ma20 = float(r.get("ma20") or (r.get("metrics") or {}).get("ma20") or 0)
    prev = float(
        r.get("prev20high")
        or (r.get("metrics") or {}).get("breakout_high")
        or (r.get("metrics") or {}).get("platform_high")
        or 0
    )
    if close <= 0 or ma20 <= 0:
        return None
    dist_ma20 = float(r.get("dist_ma20_pct") or ((close / ma20 - 1) * 100))
    if dist_ma20 > 30:
        return None

    if prefer:
        tag = phase_cn or phase
        if approaching and phase not in ("SECOND_BREAKOUT", "二次突破"):
            tag = "接近二次突破"
        observe_note = f"V2阶段={phase_cn}，观察平台不破与二次放量（非买入建议）"
    else:
        tag = "接近突破" if soft_ok else phase_cn
        observe_note = f"V2阶段={phase_cn}，结构未完整，谨慎观察"

    code6 = str(r.get("code6") or "").zfill(6)
    dist_pct = 0.0
    if prev > 0:
        dist_pct = (prev - close) / prev * 100
        if close > prev:
            dist_pct = -((close / prev - 1) * 100)
    return {
        "code6": code6,
        "code": _code_prefix(code6, r.get("code")),
        "name": name,
        "prev20high": round(prev, 3) if prev else None,
        "close_T": round(close, 3),
        "dist_to_breakout_pct": round(dist_pct, 3),
        "ma20": round(ma20, 3),
        "rs20": round(float(r.get("rs20_pct") or 0), 3),
        "rs60": round(float(r.get("rs60_pct") or 0), 3),
        "vol_ratio": round(vr, 3),
        "industry": r.get("industry") or "未知",
        "state": tag,
        "phase": phase,
        "phase_cn": phase_cn,
        "risks": list(r.get("risks") or []),
        "observe_note": observe_note,
        "score": r.get("score"),
        "grade": r.get("grade"),
        "avg20_amt_yi": r.get("avg20_amt_yi") or (r.get("metrics") or {}).get("avg20_amt_yi"),
        "dist_ma20_pct": round(dist_ma20, 3),
        "source": "v2_results",
        "kjs_pattern": bool(r.get("kjs_pattern")),
    }



def classify_near(r: Dict[str, Any], dist_max: float) -> Optional[Dict[str, Any]]:
    """Return pool item or None. Prefer RS20/RS60 > 0.

    HARD filter: 无放量(量<均量)不得入池 — vol_ratio must be present and >= 1.0.
    """
    name = r.get("name") or ""
    if _is_st(name):
        return None
    close = float(r.get("close") or 0)
    ma20 = float(r.get("ma20") or 0)
    prev20high = float(r.get("prev20high") or 0)
    if close <= 0 or ma20 <= 0 or prev20high <= 0:
        return None
    dist_ma20 = float(r.get("dist_ma20_pct") or ((close / ma20 - 1) * 100))
    if dist_ma20 > 30:
        return None
    avg20 = r.get("avg20_amt_yi")
    if avg20 is not None and float(avg20) * 1e8 < MIN_AVG20_AMT:
        return None
    # liquidity from avg20_amt_yi preferred; if missing allow if hard/near tagged
    rs20 = float(r.get("rs20_pct") or 0)
    rs60 = float(r.get("rs60_pct") or 0)
    vol_ratio = r.get("vol_ratio")
    # HARD: 无放量(量<均量)不得入池 — missing VR → exclude (don't guess)
    if vol_ratio is None:
        return None
    try:
        vr = float(vol_ratio)
    except (TypeError, ValueError):
        return None
    if not (vr >= 1.0):
        return None  # VolRatio < 1.0 = volume below recent avg → not 放量
    above = close > prev20high
    dist_to_bo = (prev20high - close) / prev20high  # positive when below high
    dist_pct = dist_to_bo * 100

    # Preferred trend: Close>MA20, RS20>0, RS60>0
    trend_ok = close > ma20 and rs20 > 0 and rs60 > 0
    soft_rs = close > ma20 and (rs20 > 0 or rs60 > 0)

    state = r.get("state") or ""
    grade = r.get("grade")
    hard = bool(r.get("hard_pass"))

    tag = None
    observe_note = ""
    if above and hard and grade in ("S", "A"):
        # handled by merge of selected; skip here to avoid dup
        return None
    if not above and 0 <= dist_to_bo <= dist_max and trend_ok:
        tag = "接近突破"
        observe_note = f"收盘距前20高约 {dist_pct:.2f}%，趋势条件满足，明日观察能否放量站上"
    elif above and (not hard or grade not in ("S", "A")):
        # already above Prev20High but VolRatio soft / not yet A-S
        if not trend_ok and not soft_rs:
            return None
        if vr is not None and vr < 1.3:
            tag = "趋势成立未突破"  # soft volume — treat as watch, not confirmed push
            observe_note = f"收盘已高于前20高但量比偏软(VR={vr:.2f}<1.3)，非S/A确认，观察量能能否跟上"
        elif grade in ("B", "C") or (hard and grade not in ("S", "A")):
            tag = "趋势成立未突破"
            observe_note = f"已过前20高但未入S/A核心推送(grade={grade})，仅作次日跟踪"
        elif state in ("接近突破", "趋势成立未突破"):
            tag = state
            observe_note = "已过前20高但硬门槛未全过，观察确认"
        else:
            # hard_pass without grade or soft
            if not trend_ok:
                return None
            tag = "趋势成立未突破"
            observe_note = "收盘高于前20高但未达核心推送标准"
    elif not above and 0 <= dist_to_bo <= dist_max and soft_rs:
        # RS preferred but allow soft if close to high
        tag = "接近突破"
        observe_note = f"距前20高 {dist_pct:.2f}%，RS部分满足，谨慎观察"
    else:
        return None

    risks = list(r.get("risks") or [])
    if dist_ma20 > 15 and "追高" not in risks:
        risks.append("追高")
    if dist_ma20 > 20 and "偏离MA20偏大" not in risks:
        risks.append("偏离MA20偏大")

    code6 = str(r.get("code6") or "").zfill(6)
    return {
        "code6": code6,
        "code": _code_prefix(code6, r.get("code")),
        "name": name,
        "prev20high": round(prev20high, 3),
        "close_T": round(close, 3),
        "dist_to_breakout_pct": round(dist_pct if not above else -((close / prev20high - 1) * 100), 3),
        "ma20": round(ma20, 3),
        "rs20": round(rs20, 3),
        "rs60": round(rs60, 3),
        "vol_ratio": round(vr, 3) if vr is not None else None,
        "industry": r.get("industry") or "未知",
        "state": tag,
        "risks": risks,
        "observe_note": observe_note,
        "score": r.get("score"),
        "grade": grade,
        "avg20_amt_yi": r.get("avg20_amt_yi"),
        "dist_ma20_pct": round(dist_ma20, 3),
        "source": "all_results",
    }


def from_selected(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge yesterday S/A core into pool for next-day tracking.

    Still requires 放量达标: vol_ratio >= 1.0 (missing → exclude).
    """
    vol_ratio = r.get("vol_ratio")
    if vol_ratio is None:
        return None
    try:
        vr0 = float(vol_ratio)
    except (TypeError, ValueError):
        return None
    if vr0 < 1.0:
        return None  # 无放量不得入池
    close = float(r.get("close") or 0)
    prev20high = float(r.get("prev20high") or 0)
    above = close > prev20high if prev20high else False
    dist_pct = ((prev20high - close) / prev20high * 100) if prev20high else 0.0
    if above:
        dist_pct = -((close / prev20high - 1) * 100)
    st = r.get("state") or "已确认突破"
    if st not in ("已确认突破", "突破刚确认", "高位加速", "疑似假突破"):
        st = "已确认突破" if r.get("grade") in ("S", "A") else st
    # normalize for pool tag
    if st in ("突破刚确认",):
        pool_state = "突破刚确认"
    else:
        pool_state = "已确认突破"
    code6 = str(r.get("code6") or "").zfill(6)
    return {
        "code6": code6,
        "code": _code_prefix(code6, r.get("code")),
        "name": r.get("name") or "",
        "prev20high": round(prev20high, 3),
        "close_T": round(close, 3),
        "dist_to_breakout_pct": round(dist_pct, 3),
        "ma20": round(float(r.get("ma20") or 0), 3),
        "rs20": round(float(r.get("rs20_pct") or 0), 3),
        "rs60": round(float(r.get("rs60_pct") or 0), 3),
        "vol_ratio": r.get("vol_ratio"),
        "industry": r.get("industry") or "未知",
        "state": pool_state,
        "risks": list(r.get("risks") or []),
        "observe_note": "昨收核心S/A推送，次日跟踪能否守住突破位（非买入建议）",
        "score": r.get("score"),
        "grade": r.get("grade"),
        "avg20_amt_yi": r.get("avg20_amt_yi"),
        "dist_ma20_pct": r.get("dist_ma20_pct"),
        "source": "selected_core",
    }


def rank_key(item: Dict[str, Any]):
    # V2 phases preferred, then V1 states
    state_rank = {
        "二次突破": 0,
        "SECOND_BREAKOUT": 0,
        "健康分歧": 1,
        "HEALTHY_PULLBACK": 1,
        "平台不破": 2,
        "PLATFORM_HOLD": 2,
        "主升加速": 3,
        "MAIN_RISE_ACCEL": 3,
        "首次突破": 4,
        "FIRST_BREAKOUT": 4,
        "突破刚确认": 5,
        "已确认突破": 6,
        "接近突破": 7,
        "趋势成立未突破": 8,
        "平台整理": 9,
        "PLATFORM": 9,
    }.get(item.get("state"), 9)
    dist = item.get("dist_to_breakout_pct")
    # for near: smaller positive dist first; for above: smaller |overshoot|
    if dist is None:
        d = 99.0
    elif dist >= 0:
        d = dist
    else:
        d = 50 + abs(dist)
    score = -(float(item.get("score") or 0))
    return (state_rank, d, score)


def build_pool(
    dist_max: float = DEFAULT_DIST_MAX,
    patch_report: bool = True,
) -> Dict[str, Any]:
    meta = _load_json(OUT / "market_meta.json") or {}
    cand = _load_json(OUT / "candidates_v2.json") or _load_json(OUT / "candidates.json") or {}
    results = _load_json(OUT / "all_results_v2.json") or _load_json(OUT / "all_results.json") or []
    payload = _load_json(OUT / "report_payload.json") or {}
    use_v2 = bool(_load_json(OUT / "all_results_v2.json")) or cand.get("model") == "V2_staircase"

    t_date = cand.get("t_date") or meta.get("t_date") or payload.get("t_date")
    regime = cand.get("regime") or meta.get("regime") or payload.get("regime") or "R0"
    regime_v2 = cand.get("regime_v2") or meta.get("regime_v2")
    if regime_v2 and regime_v2 in NEAR_CAPS_V2:
        near_cap = NEAR_CAPS_V2.get(regime_v2, 15)
    else:
        near_cap = NEAR_CAPS.get(regime, 15)

    selected = cand.get("selected") or []
    if not selected and payload:
        selected = list(payload.get("s_list") or []) + list(payload.get("a_list") or [])

    pool: List[Dict[str, Any]] = []
    seen = set()

    # 1) merge S/A core first (仍要求放量 VR>=1)
    dropped_no_vol = 0
    for r in selected:
        item = from_selected(r)
        if item is None:
            dropped_no_vol += 1
            continue
        if item["code6"] in seen:
            continue
        seen.add(item["code6"])
        pool.append(item)

    # 2) near / soft from all_results（V2 phases preferred；VR>=1 硬过滤）
    near_items: List[Dict[str, Any]] = []
    for r in results:
        vr_raw = r.get("vol_ratio")
        if vr_raw is None and isinstance(r.get("metrics"), dict):
            vr_raw = r["metrics"].get("vol_ratio")
        if vr_raw is None:
            dropped_no_vol += 1
        else:
            try:
                if float(vr_raw) < 1.0:
                    dropped_no_vol += 1
            except (TypeError, ValueError):
                dropped_no_vol += 1
        if use_v2:
            item = classify_v2(r)
            if item is None:
                item = classify_near(r, dist_max)
        else:
            item = classify_near(r, dist_max)
        if item is None:
            continue
        if item["code6"] in seen:
            continue
        near_items.append(item)

    near_items.sort(key=rank_key)
    # remaining slots after selected (selected always kept even if > cap? — keep selected, fill rest up to cap)
    remain = max(0, near_cap - len(pool)) if near_cap > 0 else 0
    if regime == "R0" or regime_v2 == "RISK_OFF":
        # RISK_OFF / R0: empty near
        pool = []
        seen = set()
        remain = 0
    for item in near_items:
        if len(pool) >= near_cap:
            break
        if item["code6"] in seen:
            continue
        seen.add(item["code6"])
        pool.append(item)

    pool.sort(key=rank_key)

    out = {
        "t_date": t_date,
        "built_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S%z"),
        "regime": regime,
        "near_cap": near_cap,
        "dist_max": dist_max,
        "count": len(pool),
        "counts_by_state": {},
        "dropped_no_vol": dropped_no_vol,
        "vol_ratio_min": 1.0,
        "pool": pool,
        "notes": [
            "待突破观察池，非S/A核心推送；不构成买入建议",
            "硬门槛确认突破标准未放宽",
            "无放量(量<均量)不得入池：要求 vol_ratio=Volume/MA20Volume >= 1.0；缺失则剔除",
            f"regime={regime} regime_v2={regime_v2} near_cap≤{near_cap}",
            "V2优先阶段: SECOND_BREAKOUT / HEALTHY_PULLBACK / PLATFORM_HOLD / approaching second" if use_v2 else "V1 state tags",
        ],
        "use_v2": use_v2,
        "regime_v2": regime_v2,
    }
    for it in pool:
        st = it.get("state") or "?"
        out["counts_by_state"][st] = out["counts_by_state"].get(st, 0) + 1

    (OUT / "near_breakout_pool.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "near_breakout_pool.md").write_text(_md_table(out), encoding="utf-8")

    if patch_report:
        _patch_report_md(out)

    print(
        f"near pool {len(pool)} regime={regime} cap={near_cap} "
        f"by_state={out['counts_by_state']} dropped_no_vol≈{dropped_no_vol}"
    )
    return out


def _md_table(out: Dict[str, Any]) -> str:
    lines = [
        f"# 待突破/接近突破观察池（{out.get('t_date')}）",
        "",
        f"- 市场状态：**{out.get('regime')}**，观察池上限 ≤{out.get('near_cap')}",
        f"- 池内数量：**{out.get('count')}**（{out.get('counts_by_state')}）",
        f"- 生成时间：{out.get('built_at')}（Asia/Shanghai）",
        "",
        "> 本池用于次日开盘低频跟踪，**不是** S/A 核心推送，不构成买入建议。",
        "> **放量硬门槛**：无放量(量&lt;均量 / VolRatio&lt;1.0)不得入池。",
        "",
        "| 代码 | 名称 | 状态 | 收盘 | 前20高 | 距突破% | MA20 | RS20% | RS60% | VR | 行业 | 观察备注 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for it in out.get("pool") or []:
        lines.append(
            "| {code6} | {name} | {state} | {close_T} | {prev20high} | {dist_to_breakout_pct} | {ma20} | {rs20} | {rs60} | {vr} | {industry} | {note} |".format(
                code6=it.get("code6"),
                name=it.get("name"),
                state=it.get("state"),
                close_T=it.get("close_T"),
                prev20high=it.get("prev20high"),
                dist_to_breakout_pct=it.get("dist_to_breakout_pct"),
                ma20=it.get("ma20"),
                rs20=it.get("rs20"),
                rs60=it.get("rs60"),
                vr=it.get("vol_ratio") if it.get("vol_ratio") is not None else "-",
                industry=it.get("industry") or "未知",
                note=(it.get("observe_note") or "")[:40],
            )
        )
    lines.append("")
    lines.append("## 风险提示")
    lines.append("数据来自收盘扫描；次日走势不确定。排除 ST、距MA20>30%、流动性不足、无放量(VR<1)标的。")
    lines.append("")
    return "\n".join(lines)


def _patch_report_md(out: Dict[str, Any]) -> None:
    path = OUT / "report.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    marker = "## 👀 待突破观察池"
    # remove old section if present
    if marker in text:
        before = text.split(marker)[0].rstrip()
        # drop until next ## or end, keep disclaimer if after
        rest = text.split(marker, 1)[1]
        # find next top-level ## that is not our subsection
        idx = None
        for i, line in enumerate(rest.splitlines()):
            if i == 0:
                continue
            if line.startswith("## ") and not line.startswith("## 👀"):
                idx = i
                break
        if idx is not None:
            after = "\n".join(rest.splitlines()[idx:])
            text = before + "\n\n" + after
        else:
            text = before + "\n"

    top = (out.get("pool") or [])[:8]
    names = "、".join(f"{x['name']}({x['code6']})" for x in top) if top else "（空）"
    section = [
        "",
        marker,
        "",
        f"- 数量：**{out.get('count')}**（上限 ≤{out.get('near_cap')}，regime={out.get('regime')}）",
        f"- 状态分布：{out.get('counts_by_state')}",
        f"- 示例：{names}",
        f"- 明细见 `near_breakout_pool.md` / `near_breakout_pool.json`",
        "",
        "> 待突破池与 S/A 核心推送分离，**不垫高核心名单**。仅供次日开盘低频观察。",
        "",
    ]
    # insert before 最终结论 or 免责声明
    insert_at = None
    for key in ("## 最终结论", "## 免责声明", "## 数据缺口"):
        if key in text:
            insert_at = text.index(key)
            break
    block = "\n".join(section)
    if insert_at is not None:
        text = text[:insert_at].rstrip() + "\n" + block + "\n" + text[insert_at:]
    else:
        text = text.rstrip() + "\n" + block
    path.write_text(text, encoding="utf-8")
    print("patched report.md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist-max", type=float, default=DEFAULT_DIST_MAX)
    ap.add_argument("--no-patch-report", action="store_true")
    args = ap.parse_args()
    build_pool(dist_max=args.dist_max, patch_report=not args.no_patch_report)


if __name__ == "__main__":
    main()
