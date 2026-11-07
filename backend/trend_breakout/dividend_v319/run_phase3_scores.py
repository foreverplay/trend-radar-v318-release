#!/usr/bin/env python3
"""
Phase3–5 batch: Quality + Sustainability (+ Return sample) for Dividend V3.1.9.

Usage:
  cd <TREND_BREAKOUT_ROOT>
  python -m dividend_v319.run_phase3_scores
  python -m dividend_v319.run_phase3_scores --as-of 2025-12-31
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dividend_v319.config.dividendConfig import DIV_ROOT, BFQ_DIR, DIVIDEND_CONFIG, OUT_DIR
from dividend_v319.engines.dividendQualityEngine import DividendQualityEngine
from dividend_v319.engines.dividendReturnEngine import batch_multi_horizon
from dividend_v319.engines.dividendSustainabilityEngine import DividendSustainabilityEngine
from dividend_v319.engines.dividendUniverseEngine import DividendUniverseEngine

CST = ZoneInfo("Asia/Shanghai")

# Prefer sector anchors for Return sample + audit
SEED_CODES = [
    "601398",  # 工商银行
    "601288",  # 农业银行
    "601988",  # 中国银行
    "601328",  # 交通银行
    "600036",  # 招商银行
    "601088",  # 中国神华
    "601225",  # 陕西煤业
    "601666",  # 平煤股份
    "000983",  # 山西焦煤
    "600900",  # 长江电力
    "600886",  # 国投电力
    "600025",  # 华能水电
    "600377",  # 宁沪高速
    "600350",  # 山东高速
    "601006",  # 大秦铁路
    "600000",  # 浦发银行
    "000001",  # 平安银行
    "601728",  # 中国电信
    "600941",  # 中国移动
    "600050",  # 中国联通
]


def _z6(c: str) -> str:
    return str(c).strip().zfill(6)


def pick_return_sample(active_codes: List[str], n_random: int = 200) -> List[str]:
    bfq_ok = {p.stem for p in BFQ_DIR.glob("*.parquet")}
    seeds = [_z6(c) for c in SEED_CODES if _z6(c) in set(active_codes)]
    # all available bfq in universe
    bfq_in = sorted(bfq_ok.intersection(active_codes))
    pool = [c for c in active_codes if c not in seeds]
    random.Random(319).shuffle(pool)
    picked: List[str] = []
    seen: Set[str] = set()
    for c in seeds + bfq_in + pool:
        if c in seen:
            continue
        seen.add(c)
        picked.append(c)
        if len(picked) >= max(n_random, len(seeds)):
            break
    # ensure >=200 if universe allows
    return picked[: max(200, len(seeds))] if len(active_codes) >= 200 else picked


def summarize_scores(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"n": 0}
    a = np.array(vals, dtype=float)
    return {
        "n": int(len(a)),
        "mean": float(a.mean()),
        "std": float(a.std()),
        "p10": float(np.percentile(a, 10)),
        "p25": float(np.percentile(a, 25)),
        "p50": float(np.percentile(a, 50)),
        "p75": float(np.percentile(a, 75)),
        "p90": float(np.percentile(a, 90)),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def audit_three(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pick 3 names with non-null continuity for manual check."""
    cands = [
        r
        for r in rows
        if r.get("continuous_cash_years")
        and r.get("quality_continuity") is not None
        and r.get("dividend_quality_score") is not None
    ]
    prefer = ["601398", "600900", "601088", "600036", "601225"]
    out = []
    used = set()
    for p in prefer:
        for r in cands:
            if r["symbol"] == p and p not in used:
                out.append(r)
                used.add(p)
                break
        if len(out) >= 3:
            break
    for r in cands:
        if r["symbol"] in used:
            continue
        out.append(r)
        used.add(r["symbol"])
        if len(out) >= 3:
            break
    slim = []
    for r in out[:3]:
        slim.append(
            {
                "symbol": r["symbol"],
                "name": r["name"],
                "continuous_cash_years": r.get("continuous_cash_years"),
                "cash_years_tail": (r.get("cash_years") or [])[-8:],
                "annual_dps_tail": dict(list((r.get("annual_dps") or {}).items())[-5:]),
                "quality_continuity": r.get("quality_continuity"),
                "quality_stability_growth": r.get("quality_stability_growth"),
                "quality_payout": r.get("quality_payout"),
                "quality_cash_ability": r.get("quality_cash_ability"),
                "quality_record": r.get("quality_record"),
                "dividend_quality_score": r.get("dividend_quality_score"),
                "dividend_sustainability_score": r.get("dividend_sustainability_score"),
                "reasons_head": (r.get("reasons") or [])[:6],
            }
        )
    return slim


def to_markdown(payload: Dict[str, Any], top: List[Dict[str, Any]]) -> str:
    as_of = payload["as_of"]
    qs = payload["summary"]["quality"]
    ss = payload["summary"]["sustainability"]
    lines = [
        f"# 红利趋势榜 Phase3–5 批跑摘要",
        "",
        f"- as_of: **{as_of}**（Asia/Shanghai）",
        f"- generated_at: {payload['generated_at']}",
        f"- universe_active: {payload['universe_counts'].get('active')}",
        f"- scored: {payload['n_scored']}（Quality+Sustainability）",
        f"- weights_note: 待回测校准",
        "",
        "## 分数分布",
        "",
        f"- Quality: n={qs.get('n')} mean={qs.get('mean', float('nan')):.2f} p50={qs.get('p50', float('nan')):.2f} "
        f"p10={qs.get('p10', float('nan')):.2f} p90={qs.get('p90', float('nan')):.2f}",
        f"- Sustainability: n={ss.get('n')} mean={ss.get('mean', float('nan')):.2f} p50={ss.get('p50', float('nan')):.2f} "
        f"p10={ss.get('p10', float('nan')):.2f} p90={ss.get('p90', float('nan')):.2f}",
        "",
        "## Top50 by Quality（并列时看 Sustainability）",
        "",
        "| 排名 | 代码 | 名称 | 质量分 | 可持续分 | 连续年 | risk_hint | 关键理由 |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for i, r in enumerate(top, 1):
        reason = "；".join((r.get("reasons") or [])[:2]).replace("|", "/")
        lines.append(
            f"| {i} | {r['symbol']} | {r.get('name','')} | {r.get('dividend_quality_score')} | "
            f"{r.get('dividend_sustainability_score')} | {r.get('continuous_cash_years')} | "
            f"{r.get('risk_hint','')} | {reason[:80]} |"
        )
    lines.extend(
        [
            "",
            "## Return 抽样",
            "",
            f"- 样本数: {payload['return_batch']['n_requested']}",
            f"- 有bfq并算出PR/TR: {payload['return_batch']['n_with_bfq_ok']}",
            f"- 缺bfq标记 DATA_INSUFFICIENT: {payload['return_batch']['n_bfq_missing']}",
            f"- 口径: bfq + ex-date cash；主口径不复投；禁止 qfq+现金",
            "",
            "## 人工抽查（3只）",
            "",
            "```json",
            json.dumps(payload.get("manual_audit", []), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 已知偏差",
            "",
        ]
    )
    for g in payload.get("known_gaps", []):
        lines.append(f"- {g}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2025-12-31", help="PIT as-of date")
    ap.add_argument("--min-years", type=int, default=3)
    ap.add_argument("--return-sample", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0, help="debug limit; 0=all")
    args = ap.parse_args()
    as_of = args.as_of

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    phase_dir = OUT_DIR / "phase3"
    phase_dir.mkdir(parents=True, exist_ok=True)

    uni = DividendUniverseEngine(DIVIDEND_CONFIG).asof(as_of, min_years=args.min_years)
    members = uni["members"]
    codes = [m["symbol"] for m in members]
    names = {m["symbol"]: m.get("name") or "" for m in members}
    if args.limit and args.limit > 0:
        codes = codes[: args.limit]

    q_eng = DividendQualityEngine(DIVIDEND_CONFIG)
    s_eng = DividendSustainabilityEngine(DIVIDEND_CONFIG)

    rows: List[Dict[str, Any]] = []
    for i, code in enumerate(codes):
        q = q_eng.score(code, as_of, name=names.get(code, ""))
        s = s_eng.score(code, as_of, name=names.get(code, "") or q.name)
        reasons = []
        # de-dupe preserve order
        for r in (q.reasons or []) + (s.reasons or []):
            if r not in reasons:
                reasons.append(r)
        rows.append(
            {
                "symbol": code,
                "name": q.name or s.name or names.get(code, ""),
                "as_of": as_of,
                "dividend_quality_score": q.dividend_quality_score,
                "quality_continuity": q.continuity_score,
                "quality_stability_growth": q.stability_growth_score,
                "quality_payout": q.payout_score,
                "quality_cash_ability": q.cash_ability_score,
                "quality_record": q.record_quality_score,
                "continuous_cash_years": q.continuous_cash_years,
                "cash_years": q.cash_years,
                "annual_dps": q.annual_dps,
                "special_heuristic_years": q.special_heuristic_years,
                "dividend_sustainability_score": s.dividend_sustainability_score,
                "sustainability_components": s.component_scores,
                "risk_hint": s.risk_hint,
                "unhealthy_flags": s.unhealthy_flags,
                "quality_data_status": q.data_status,
                "sustainability_data_status": s.data_status,
                "missing_quality": q.missing_components,
                "missing_sustainability": s.missing_components,
                "reasons": reasons,
            }
        )
        if (i + 1) % 400 == 0:
            print(f"scored {i+1}/{len(codes)}", flush=True)

    q_vals = [r["dividend_quality_score"] for r in rows if r["dividend_quality_score"] is not None]
    s_vals = [
        r["dividend_sustainability_score"]
        for r in rows
        if r["dividend_sustainability_score"] is not None
    ]

    ranked = sorted(
        [r for r in rows if r["dividend_quality_score"] is not None],
        key=lambda r: (
            -(r["dividend_quality_score"] or -1),
            -(r["dividend_sustainability_score"] or -1),
        ),
    )
    top50 = ranked[:50]

    # Return batch
    ret_codes = pick_return_sample(codes, n_random=args.return_sample)
    # Prefer computing for those with bfq; still call all to mark missing
    # Limit actual compute set: all with bfq in sample + seeds; for non-bfq just mark
    ret_results = []
    n_ok = 0
    n_miss = 0
    for c in ret_codes:
        if (BFQ_DIR / f"{c}.parquet").exists():
            ret_results.append(batch_multi_horizon([c], as_of)[0])
            if ret_results[-1].get("data_status") == "VALID":
                n_ok += 1
            else:
                n_miss += 1
        else:
            ret_results.append(
                {
                    "code": c,
                    "as_of": as_of,
                    "data_status": "DATA_INSUFFICIENT",
                    "horizons": {},
                    "notes": ["bfq_missing_skip_pr_tr_never_use_qfq_plus_cash"],
                }
            )
            n_miss += 1

    # attach returns onto matching score rows for output join sample
    ret_by = {r["code"]: r for r in ret_results}
    for r in rows:
        if r["symbol"] in ret_by:
            r["returns"] = ret_by[r["symbol"]]

    manual = audit_three(rows)

    known_gaps = [
        "全市场 bfq 仅样本级（当前 data/bfq_daily ≈33）；Return 全池需 Phase6 扩覆盖",
        "红利指数历史成分仍为 proxy_no_hist_index_cons",
        "特别分红强标签弱（is_special_flag 多为 null/False），依赖 DPS 启发式降权",
        "FCF≈经营现金流-购建固定资产，未扣全部维持性资本开支",
        "支付率用 DPS/EPS 代理，未用公司章程口径的合并报表分红总额",
        "Quality/Sustainability 权重待回测校准",
        "未实现 Valuation/Technical/Risk/Opportunity 综合分（Phase6+）",
    ]

    payload = {
        "strategy": "DIVIDEND_TREND",
        "phase": "3-5",
        "version": "V3.1.9-phase3",
        "as_of": as_of,
        "generated_at": datetime.now(CST).isoformat(),
        "timezone": "Asia/Shanghai",
        "universe_mode": uni.get("universe_mode"),
        "universe_counts": uni.get("counts"),
        "n_scored": len(rows),
        "n_quality_scored": len(q_vals),
        "n_sustainability_scored": len(s_vals),
        "summary": {
            "quality": summarize_scores(q_vals),
            "sustainability": summarize_scores(s_vals),
            "risk_hint_counts": pd.Series([r.get("risk_hint") for r in rows]).value_counts().to_dict(),
            "quality_data_status_counts": pd.Series(
                [r.get("quality_data_status") for r in rows]
            )
            .value_counts()
            .to_dict(),
        },
        "config_weights": {
            "quality_component_weights": DIVIDEND_CONFIG.quality_component_weights,
            "sustainability_component_weights": DIVIDEND_CONFIG.sustainability_component_weights,
            "note": "待回测校准",
        },
        "top50": top50,
        "manual_audit": manual,
        "return_batch": {
            "n_requested": len(ret_codes),
            "n_with_bfq_ok": n_ok,
            "n_bfq_missing": n_miss,
            "sample_codes_head": ret_codes[:40],
            "results_with_bfq": [r for r in ret_results if r.get("horizons")],
            "spec": "PHASE2_TR_SPEC.md — primary total_return = no reinvest",
        },
        "known_gaps": known_gaps,
        "publish_close_v318_untouched": True,
    }

    # full rows separate (large)
    full_path = phase_dir / f"scores_{as_of.replace('-', '')}.json"
    with full_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "as_of": as_of,
                "generated_at": payload["generated_at"],
                "n": len(rows),
                "rows": rows,
            },
            f,
            ensure_ascii=False,
        )

    summary_path = phase_dir / f"summary_{as_of.replace('-', '')}.json"
    # shrink return results in summary if huge
    summary_payload = dict(payload)
    summary_payload["return_batch"] = dict(payload["return_batch"])
    summary_payload["return_batch"]["results_with_bfq"] = payload["return_batch"][
        "results_with_bfq"
    ][:40]
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_path = phase_dir / f"report_{as_of.replace('-', '')}.md"
    md_path.write_text(to_markdown(payload, top50), encoding="utf-8")

    # also convenience latest pointers
    (OUT_DIR / "phase3_scores_latest.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "phase3_report_latest.md").write_text(
        to_markdown(payload, top50), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "as_of": as_of,
                "n_scored": len(rows),
                "n_quality": len(q_vals),
                "n_sustainability": len(s_vals),
                "quality_summary": payload["summary"]["quality"],
                "sustainability_summary": payload["summary"]["sustainability"],
                "top5": [
                    {
                        "symbol": r["symbol"],
                        "name": r["name"],
                        "Q": r["dividend_quality_score"],
                        "S": r["dividend_sustainability_score"],
                    }
                    for r in top50[:5]
                ],
                "return_ok": n_ok,
                "return_miss": n_miss,
                "paths": {
                    "full": str(full_path),
                    "summary": str(summary_path),
                    "md": str(md_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
