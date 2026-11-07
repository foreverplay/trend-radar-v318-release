#!/usr/bin/env python3
"""
Phase12 DividendTrend backtest (+ Phase13 simple walk-forward).

Usage:
  cd backend/trend_breakout
  python -m dividend_v319.run_phase12_backtest
  python -m dividend_v319.run_phase12_backtest --freq quarterly
  python -m dividend_v319.run_phase12_backtest --skip-score  # reuse snapshots
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dividend_v319.backtest.batch_score import preload_prices, save_snapshot, score_asof
from dividend_v319.backtest.dividendBacktestEngine import (
    StrategySpec,
    excess_vs,
    load_index_price,
    metrics_from_equity,
    next_open_date,
    select_portfolio,
    simulate_equal_weight,
    simulate_index_bh,
    trading_days_between,
)
from dividend_v319.config.dividendConfig import BFQ_DIR, DIVIDEND_CONFIG, EVENTS_PATH, KLINE_DAILY, OUT_DIR
from dividend_v319.engines.dividendReturnEngine import compute_returns

CST = ZoneInfo("Asia/Shanghai")
BT_OUT = OUT_DIR / "backtest"
SNAP_DIR = BT_OUT / "snapshots"


def quarter_ends(start: str, end: str) -> List[str]:
    """Calendar quarter-ends clipped to [start, end]."""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    dates = pd.date_range(s, e, freq="QE")
    out = [d.strftime("%Y-%m-%d") for d in dates if s <= d <= e]
    # ensure start signal if not quarter end: use first QE on/after start already from range
    return out


def semiannual_ends(start: str, end: str) -> List[str]:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    dates = []
    y0, y1 = s.year, e.year
    for y in range(y0, y1 + 1):
        for md in ("06-30", "12-31"):
            d = pd.Timestamp(f"{y}-{md}")
            if s <= d <= e:
                dates.append(d.strftime("%Y-%m-%d"))
    return dates


def load_snapshot(as_of: str) -> Optional[Dict[str, Any]]:
    p = SNAP_DIR / f"snapshot_{as_of.replace('-', '')}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_snapshots(dates: List[str], *, limit: int = 0, reuse: bool = True) -> Dict[str, Dict[str, Any]]:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    px_cache: Dict[str, Any] = {}
    # shared engines warmed once
    from dividend_v319.engines.dividendQualityEngine import DividendQualityEngine
    from dividend_v319.engines.dividendSustainabilityEngine import DividendSustainabilityEngine
    from dividend_v319.engines.dividendTechnicalEngine import DividendTechnicalEngine
    from dividend_v319.engines.dividendValuationEngine import DividendValuationEngine
    from dividend_v319.engines.dividendRiskEngine import DividendRiskEngine
    from dividend_v319.engines.dividendEntryEngine import DividendEntryEngine
    from dividend_v319.backtest.batch_score import _patch_valuation_speed

    qe = DividendQualityEngine(DIVIDEND_CONFIG)
    se = DividendSustainabilityEngine(DIVIDEND_CONFIG)
    te = DividendTechnicalEngine(DIVIDEND_CONFIG)
    ve = DividendValuationEngine(DIVIDEND_CONFIG)
    _patch_valuation_speed(ve)
    engines = {
        "q": qe,
        "s": se,
        "t": te,
        "v": ve,
        "risk": DividendRiskEngine(),
        "entry": DividendEntryEngine(),
    }

    for d in dates:
        if reuse:
            old = load_snapshot(d)
            if old and old.get("rows"):
                print(f"[reuse] {d} n={old['n_scored']} elapsed_was={old.get('elapsed_sec')}", flush=True)
                out[d] = old
                continue
        print(f"[score] {d} …", flush=True)
        # expand price cache for new codes in this asof universe
        from dividend_v319.engines.dividendUniverseEngine import DividendUniverseEngine
        uni = DividendUniverseEngine(DIVIDEND_CONFIG).asof(d, min_years=3)
        codes = [m["symbol"] for m in uni["members"]]
        if limit and limit > 0:
            codes = codes[:limit]
        missing = [c for c in codes if c not in px_cache]
        if missing:
            print(f"  preload +{len(missing)} prices (cache={len(px_cache)})", flush=True)
            more = preload_prices(missing)
            px_cache.update(more)
        payload = score_asof(d, limit=limit, px_cache=px_cache, engines=engines)
        save_snapshot(payload, SNAP_DIR)
        print(
            f"  done {d} n={payload['n_scored']} grades={payload['grade_counts']} "
            f"sec={payload['elapsed_sec']}",
            flush=True,
        )
        out[d] = payload
    return out


def gather_codes(snaps: Dict[str, Dict[str, Any]]) -> List[str]:
    s = set()
    for p in snaps.values():
        for r in p.get("rows") or []:
            s.add(r["symbol"])
    return sorted(s)


def count_div_cuts(
    held_by_date: Dict[str, List[str]],
    events_path: Path = EVENTS_PATH,
) -> Dict[str, Any]:
    """Rough PIT cut detector: trailing 1y cash vs prior 1y cash drop >30% at signal dates."""
    if not events_path.exists():
        return {"available": False, "reason": "events_missing"}
    ev = pd.read_parquet(events_path)
    ev["code"] = ev["code"].map(lambda x: str(x).zfill(6))
    ev["ex_dividend_date"] = pd.to_datetime(ev["ex_dividend_date"], errors="coerce")
    ev["disclose_date"] = pd.to_datetime(ev["disclose_date"], errors="coerce")
    ev["cash_per_share"] = pd.to_numeric(ev["cash_per_share"], errors="coerce")
    ok = set(DIVIDEND_CONFIG.assign_progress_ok)
    cuts = []
    for as_of, codes in held_by_date.items():
        t = pd.Timestamp(as_of).normalize()
        for code in codes:
            g = ev[ev["code"] == code]
            if g.empty:
                continue
            def cash_sum(start, end):
                sub = g[
                    (g["assign_progress"].isin(ok))
                    & (g["cash_per_share"].fillna(0) > 0)
                    & (g["ex_dividend_date"] > start)
                    & (g["ex_dividend_date"] <= end)
                    & (g["disclose_date"].isna() | (g["disclose_date"] <= t))
                ]
                return float(sub["cash_per_share"].sum()) if len(sub) else 0.0
            c1 = cash_sum(t - pd.DateOffset(years=1), t)
            c0 = cash_sum(t - pd.DateOffset(years=2), t - pd.DateOffset(years=1))
            if c0 > 0 and c1 < c0 * 0.7:
                cuts.append({"as_of": as_of, "symbol": code, "cash_1y": c1, "cash_prev_1y": c0})
    return {
        "available": True,
        "n_cut_events": len(cuts),
        "n_signal_dates": len(held_by_date),
        "threshold": "trailing_1y_cash < 70% of prior_1y",
        "sample": cuts[:30],
    }


def bfq_sensitivity(symbols: List[str], start: str, end: str) -> Dict[str, Any]:
    """Small-sample TR vs price-only among symbols that have bfq bars.

    Uses compute_returns(bfq + cash events). Never qfq+cash.
    """
    bfq_ok = {p.stem for p in BFQ_DIR.glob("*.parquet")}
    inter = sorted(set(symbols) & bfq_ok)
    rows = []
    for c in inter:
        try:
            rb = compute_returns(c, start, end)
            pr = rb.price_return
            tr = rb.total_return_no_reinvest
            rows.append(
                {
                    "symbol": c,
                    "price_return": pr,
                    "total_return_no_reinvest": tr,
                    "dividend_cash_return": rb.dividend_cash_return,
                    "n_dividends": rb.n_dividends,
                    "cash_boost": (None if tr is None or pr is None else float(tr) - float(pr)),
                    "notes": rb.notes,
                }
            )
        except Exception as ex:
            rows.append({"symbol": c, "error": str(ex)[:160]})
    ok = [r for r in rows if r.get("cash_boost") is not None]
    summary: Dict[str, Any] = {}
    if ok:
        summary = {
            "n": len(ok),
            "mean_cash_boost": float(np.mean([r["cash_boost"] for r in ok])),
            "median_cash_boost": float(np.median([r["cash_boost"] for r in ok])),
            "mean_price_return": float(np.mean([r["price_return"] for r in ok])),
            "mean_total_return": float(np.mean([r["total_return_no_reinvest"] for r in ok])),
        }
    return {
        "window": {"start": start, "end": end},
        "n_overlap_bfq": len(inter),
        "summary": summary,
        "rows": rows,
        "note": "bfq+cash vs price-only on overlapping names; portfolio NAV remains price_only_qfq",
    }


def slice_equity(equity: List[Dict[str, Any]], start: str, end: str) -> List[Dict[str, Any]]:
    s = pd.Timestamp(start).normalize()
    e = pd.Timestamp(end).normalize()
    out = [x for x in equity if s <= pd.Timestamp(x["date"]) <= e]
    if not out:
        return out
    # rebase
    base = out[0]["nav"]
    reb = []
    for x in out:
        y = dict(x)
        y["nav"] = x["nav"] / base * 1_000_000.0
        reb.append(y)
    return reb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-12-31")
    ap.add_argument("--end", default="2025-09-30")
    ap.add_argument("--freq", choices=["quarterly", "semiannual"], default="quarterly")
    ap.add_argument("--limit", type=int, default=0, help="debug: score only first N names")
    ap.add_argument("--skip-score", action="store_true")
    ap.add_argument("--force-score", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    BT_OUT.mkdir(parents=True, exist_ok=True)
    dates = quarter_ends(args.start, args.end) if args.freq == "quarterly" else semiannual_ends(args.start, args.end)
    # drop last date if no forward period to trade (keep as terminal mark only if >=2)
    if not dates:
        raise SystemExit("no rebalance dates")

    print(f"rebalance signal dates ({args.freq}): {dates}", flush=True)
    snaps = ensure_snapshots(dates, limit=args.limit, reuse=(not args.force_score) or args.skip_score)
    if args.skip_score:
        # still load
        snaps = {d: load_snapshot(d) for d in dates}
        snaps = {d: v for d, v in snaps.items() if v}

    index_df = load_index_price("000922")
    # backtest calendar: from first exec to end
    first_exec = next_open_date(index_df, dates[0])
    if first_exec is None:
        raise SystemExit("cannot find first exec date")
    end_ts = pd.Timestamp(args.end).normalize()
    # clamp end to last index bar
    last_bar = pd.Timestamp(index_df["date"].iloc[-1])
    end_ts = min(end_ts, last_bar)
    calendar = trading_days_between(index_df, str(first_exec.date()), str(end_ts.date()))

    # strategies
    specs = [
        StrategySpec("QualityCore", "quality_core", k=None, notes="Grade A+/A & Risk SAFE/WATCH & !Trap & !TREND_BROKEN; all equal-weight"),
        StrategySpec("EntryTilt_K10", "entry_tilt", k=10, notes="QualityCore ∩ Entry tilt; top10 by Opp"),
        StrategySpec("EntryTilt_K20", "entry_tilt", k=20, notes="QualityCore ∩ Entry tilt; top20 by Opp"),
        StrategySpec("HighYield_Top20", "high_yield", k=20, notes="Naive top20 TTM yield in asof universe"),
        StrategySpec("HighYield_Top50", "high_yield", k=50, notes="Naive top50 TTM yield"),
    ]

    # build targets
    rebalance_targets: Dict[str, Dict[pd.Timestamp, List[str]]] = {sp.name: {} for sp in specs}
    held_for_cuts: Dict[str, List[str]] = {}
    rankings_rows = []

    for d in dates:
        payload = snaps[d]
        rows = payload["rows"]
        sig = pd.Timestamp(d).normalize()
        for sp in specs:
            names = select_portfolio(rows, sp)
            rebalance_targets[sp.name][sig] = names
            rankings_rows.append(
                {
                    "as_of": d,
                    "strategy": sp.name,
                    "n": len(names),
                    "symbols_head": ",".join(names[:15]),
                    "grade_counts": json.dumps(payload.get("grade_counts"), ensure_ascii=False),
                }
            )
            if sp.name == "QualityCore":
                held_for_cuts[d] = names

    # preload prices for all selected codes + any in snaps
    all_codes = set(gather_codes(snaps))
    for m in rebalance_targets.values():
        for names in m.values():
            all_codes.update(names)
    print(f"preload qfq prices n_codes={len(all_codes)} …", flush=True)
    px = preload_prices(sorted(all_codes))

    # exec map: only use signals that have a next open within calendar
    results = {}
    for sp in specs:
        exec_dates = {}
        targets = {}
        for sig, names in rebalance_targets[sp.name].items():
            ex = next_open_date(index_df, str(sig.date()))
            if ex is None or ex > end_ts:
                continue
            if ex < calendar[0] or ex > calendar[-1]:
                continue
            exec_dates[sig] = ex
            targets[sig] = names
        print(f"simulate {sp.name} signals={len(targets)} …", flush=True)
        sim = simulate_equal_weight(
            rebalance_targets=targets,
            exec_dates=exec_dates,
            px=px,
            calendar=calendar,
        )
        met = metrics_from_equity(sim.get("equity") or [])
        results[sp.name] = {"spec": sp.__dict__, "sim": sim, "metrics": met}

    # index BH
    bh = simulate_index_bh(index_df, str(calendar[0].date()), str(calendar[-1].date()))
    bh_met = metrics_from_equity(bh.get("equity") or [])
    results["Index_000922_BH"] = {
        "spec": {"name": "Index_000922_BH", "kind": "index_bh", "notes": "中证红利价格序列买入持有；非总回报指数"},
        "sim": bh,
        "metrics": bh_met,
    }

    # excess
    for name, pack in results.items():
        pack["excess_vs_index"] = excess_vs(pack["metrics"], bh_met)

    # walk-forward segments
    wf_segments = [
        ("train_2020_2021", "2020-01-01", "2021-12-31"),
        ("test_2022_2023", "2022-01-01", "2023-12-31"),
        ("test_2024_2025", "2024-01-01", str(calendar[-1].date())),
    ]
    # also rolling-ish: 2019-2021 train already; OOS 2022-2025 combined
    wf_segments.append(("oos_2022_2025", "2022-01-01", str(calendar[-1].date())))
    wf_table = []
    for seg_name, s, e in wf_segments:
        for strat_name, pack in results.items():
            eq = slice_equity(pack["sim"].get("equity") or [], s, e)
            met = metrics_from_equity(eq)
            wf_table.append(
                {
                    "segment": seg_name,
                    "start": s,
                    "end": e,
                    "strategy": strat_name,
                    **{k: met.get(k) for k in ("n_days", "n_years", "total_return", "ann_return", "max_dd", "vol_ann", "calmar")},
                }
            )

    cuts = count_div_cuts(held_for_cuts)
    # bfq sensitivity on QualityCore union
    qc_union = sorted({c for names in rebalance_targets["QualityCore"].values() for c in names})
    sens = bfq_sensitivity(qc_union, str(calendar[0].date()), str(calendar[-1].date()))

    # rankings csv
    rank_df = pd.DataFrame(rankings_rows)
    rank_path = BT_OUT / "rankings.csv"
    rank_df.to_csv(rank_path, index=False)

    # equity summary csv
    eq_rows = []
    for name, pack in results.items():
        m = pack["metrics"]
        eq_rows.append(
            {
                "strategy": name,
                "return_label": pack["sim"].get("return_label"),
                **m,
                "ann_excess_vs_000922": (pack.get("excess_vs_index") or {}).get("ann_excess"),
                "total_excess_vs_000922": (pack.get("excess_vs_index") or {}).get("total_excess"),
                "n_trades": len(pack["sim"].get("trades") or []),
            }
        )
    eq_df = pd.DataFrame(eq_rows)
    eq_path = BT_OUT / "equity_summary.csv"
    eq_df.to_csv(eq_path, index=False)

    # save equity curves
    for name, pack in results.items():
        pd.DataFrame(pack["sim"].get("equity") or []).to_csv(
            BT_OUT / f"equity_{name}.csv", index=False
        )

    wf_df = pd.DataFrame(wf_table)
    wf_path = BT_OUT / "walkforward_segments.csv"
    wf_df.to_csv(wf_path, index=False)

    meta = {
        "phase": "12-13",
        "version": "V3.1.9-phase12",
        "generated_at": datetime.now(CST).isoformat(),
        "timezone": "Asia/Shanghai",
        "window": {"start": args.start, "end": str(end_ts.date()), "calendar_start": str(calendar[0].date()), "calendar_end": str(calendar[-1].date())},
        "rebalance_freq": args.freq,
        "signal_dates": dates,
        "return_label": "price_only_qfq — 未计入现金分红再投资，低估红利策略真实总回报",
        "benchmark": {
            "code": "000922",
            "name": "中证红利",
            "return_label": "price_only",
            "note": "本地仅有价格日线；非官方总回报指数。000015 本地 parquet 缺失",
        },
        "costs": {
            "commission": DIVIDEND_CONFIG.commission_rate,
            "stamp_sell": DIVIDEND_CONFIG.stamp_tax_rate,
            "slippage": DIVIDEND_CONFIG.slippage,
        },
        "universe_mode": "proxy_no_hist_index_cons",
        "strategies": [sp.__dict__ for sp in specs],
        "equity_summary": eq_rows,
        "walkforward": wf_table,
        "dividend_cuts": cuts,
        "bfq_sensitivity": {
            "summary": sens.get("summary"),
            "n_overlap_bfq": sens.get("n_overlap_bfq"),
            "note": sens.get("note"),
        },
        "elapsed_sec": round(time.time() - t0, 2),
        "publish_close_v318_untouched": True,
        "known_gaps": [
            "组合净值 price_only_qfq，现金分红贡献未计入",
            "全市场 bfq 仅~33只；敏感性样本有限",
            "红利指数历史成分缺失",
            "000015 上证红利日线本地缺失，仅用 000922",
            "权重/阈值未做网格寻优（避免好看改参）",
        ],
    }
    (BT_OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (BT_OUT / "bfq_sensitivity.json").write_text(json.dumps(sens, ensure_ascii=False, indent=2), encoding="utf-8")

    # markdown report
    md = render_report(meta, eq_df, wf_df, cuts, sens)
    (BT_OUT / "PHASE12_BACKTEST.md").write_text(md, encoding="utf-8")
    print("wrote", BT_OUT / "PHASE12_BACKTEST.md", "elapsed", meta["elapsed_sec"], flush=True)


def render_report(meta, eq_df, wf_df, cuts, sens) -> str:
    lines = [
        "# 红利趋势策略 Phase12 回测报告（含 Phase13 简易 walk-forward）",
        "",
        f"- generated_at: {meta['generated_at']}（Asia/Shanghai）",
        f"- 窗口: {meta['window']['calendar_start']} → {meta['window']['calendar_end']}",
        f"- 调仓: **{meta['rebalance_freq']}**（信号日=季末/半年末收盘评分 → 次日开盘调仓）",
        f"- **收益口径: {meta['return_label']}**",
        f"- 基准: 中证红利 000922 **价格**买入持有（非总回报指数；000015 本地缺失）",
        f"- 成本: 佣金万三双边 + 印花税卖千一 + 滑点0.1%/边",
        f"- universe_mode: {meta['universe_mode']}（无历史指数成分）",
        f"- 非荐买；禁止编造；禁止 qfq+现金双重计算",
        "",
        "## ⚠️ 现金分红贡献不可用（组合层）",
        "",
        "全市场未复权/bfq 覆盖不足，**组合净值仅用前复权价格路径**。",
        "这会**低估**真正「股价+分红再投资」的红利策略总回报。",
        "下方「bfq 小样本敏感性」用已有 ~33 只 bfq 估现金抬升量级，**不能**外推为全组合精确 TR。",
        "",
        "## 策略对照（全样本）",
        "",
        "| 策略 | 年化 | 总收益 | MaxDD | 波动 | Calmar | 相对000922年化超额 | 口径 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, r in eq_df.iterrows():
        lines.append(
            f"| {r['strategy']} | {_pct(r.get('ann_return'))} | {_pct(r.get('total_return'))} | "
            f"{_pct(r.get('max_dd'))} | {_pct(r.get('vol_ann'))} | {_num(r.get('calmar'))} | "
            f"{_pct(r.get('ann_excess_vs_000922'))} | price_only |"
        )

    lines += [
        "",
        "## Walk-Forward / 分段稳定性",
        "",
        "| 分段 | 策略 | 年化 | 总收益 | MaxDD |",
        "|---|---|---:|---:|---:|",
    ]
    for _, r in wf_df.iterrows():
        lines.append(
            f"| {r['segment']} | {r['strategy']} | {_pct(r.get('ann_return'))} | "
            f"{_pct(r.get('total_return'))} | {_pct(r.get('max_dd'))} |"
        )

    lines += ["", "## 分红削减统计（QualityCore 持仓，粗糙 PIT）", ""]
    if cuts.get("available"):
        lines.append(
            f"- 事件数: **{cuts.get('n_cut_events')}**（阈值: {cuts.get('threshold')}）"
        )
        lines.append(f"- 信号日数: {cuts.get('n_signal_dates')}")
    else:
        lines.append(f"- 不可用: {cuts.get('reason')}")

    lines += ["", "## bfq 小样本敏感性（真 TR vs 价格）", ""]
    sm = sens.get("summary") or {}
    if sm:
        lines.append(f"- 重叠有 bfq 的 QualityCore 曾持股: **{sens.get('n_overlap_bfq')}**")
        lines.append(f"- 样本 n={sm.get('n')}；均值价格回报={_pct(sm.get('mean_price_return'))}；均值不复投TR={_pct(sm.get('mean_total_return'))}")
        lines.append(f"- 均值现金抬升（TR−Price）≈ **{_pct(sm.get('mean_cash_boost'))}**（中位 {_pct(sm.get('median_cash_boost'))}）")
    else:
        lines.append("- 样本不足或窗口内算不出")
    lines.append(f"- 说明: {sens.get('note')}")

    lines += [
        "",
        "## 大白话结论",
        "",
        _plain_conclusion(eq_df, wf_df, meta),
        "",
        "## 权重是否建议调整（仅数据依据）",
        "",
        _weight_advice(eq_df, wf_df),
        "",
        "## Phase15 Live 前缺口",
        "",
    ]
    for g in meta.get("known_gaps") or []:
        lines.append(f"- {g}")
    lines += [
        "- Walk-forward 仅分段稳定性表，**未**做参数网格重训",
        "- Entry/Grade 阈值仍为 Plan 初始值",
        "- Live 独立 publish（candidates_dividend.json）尚未接雷达编排",
        "- 特别分红强标签仍弱",
        "",
        "## 产物",
        "",
        "- `out/backtest/rankings.csv`",
        "- `out/backtest/equity_summary.csv`",
        "- `out/backtest/equity_*.csv`",
        "- `out/backtest/walkforward_segments.csv`",
        "- `out/backtest/meta.json`",
        "- `out/backtest/PHASE12_BACKTEST.md`",
        "",
    ]
    return "\n".join(lines)


def _pct(x) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return ""
    return f"{100*float(x):.2f}%"


def _num(x) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return ""
    return f"{float(x):.2f}"


def _plain_conclusion(eq_df, wf_df, meta) -> str:
    def row(name):
        m = eq_df[eq_df["strategy"] == name]
        return None if m.empty else m.iloc[0]

    qc = row("QualityCore")
    hy = row("HighYield_Top20")
    idx = row("Index_000922_BH")
    et = row("EntryTilt_K20")
    parts = []
    if qc is not None and hy is not None:
        if float(qc["ann_return"]) > float(hy["ann_return"]):
            parts.append(
                f"全窗 **QualityCore 年化 {_pct(qc['ann_return'])} 高于纯高股息 Top20 {_pct(hy['ann_return'])}**："
                f"说明在本套价格口径下，质量/风险过滤相对「只追股息率」有优势（仍是 price_only）。"
            )
        else:
            parts.append(
                f"全窗 QualityCore 年化 {_pct(qc['ann_return'])} **并未**稳定打赢高股息 Top20 {_pct(hy['ann_return'])}；"
                f"不能宣称质量分已证伪高股息——需结合回撤与分段，且现金未计入。"
            )
    if qc is not None and idx is not None:
        ex = qc.get("ann_excess_vs_000922")
        parts.append(
            f"相对中证红利价格基准，QualityCore 年化超额约 {_pct(ex)}，MaxDD {_pct(qc['max_dd'])} vs 基准 {_pct(idx['max_dd'])}。"
        )
    # failure env from wf
    if not wf_df.empty and qc is not None:
        sub = wf_df[wf_df["strategy"] == "QualityCore"]
        if not sub.empty:
            worst = sub.sort_values("ann_return").iloc[0]
            parts.append(
                f"QualityCore 最弱分段约是 **{worst['segment']}**（年化 {_pct(worst['ann_return'])}，回撤 {_pct(worst['max_dd'])}）。"
            )
    parts.append(
        "主要失效/失真环境：① 组合未计现金，高分红横盘股被系统性低估；② 无历史红利成分→幸存者偏差；"
        "③ 熊市里 Grade A 仍可能大面积回撤（价格口径）。"
    )
    if et is not None and qc is not None:
        parts.append(
            f"EntryTilt_K20 年化 {_pct(et['ann_return'])} vs QualityCore {_pct(qc['ann_return'])}："
            f"{'更集中的介入倾斜' if float(et['ann_return'])>float(qc['ann_return']) else '未显示稳定优于全量优质核心'}。"
        )
    return " ".join(parts)


def _weight_advice(eq_df, wf_df) -> str:
    """Do NOT change weights for looks; only state evidence."""
    lines = [
        "当前 Opp 权重仍是 Plan 初始：Q0.20 / S0.25 / Trend0.25 / TR0.15 / Val0.10 / Risk0.05。",
        "**本次回测没有做权重网格**，因此：**不建议仅为曲线更好而改权重**。",
    ]
    # evidence about high-yield underperformance or not
    try:
        qc = eq_df.set_index("strategy").loc["QualityCore"]
        hy = eq_df.set_index("strategy").loc["HighYield_Top20"]
        if float(hy["max_dd"]) < float(qc["max_dd"]) and float(hy["ann_return"]) > float(qc["ann_return"]):
            lines.append(
                "数据现象：高股息组全窗收益/回撤并不差——若未来要调权，应先补真 TR 与历史成分，再考虑是否降低 Trend 权或提高 Sustainability，而不是现在拍脑袋。"
            )
        elif float(qc["ann_return"]) > float(hy["ann_return"]) and float(qc["max_dd"]) > float(hy["max_dd"]) - 0.05:
            lines.append(
                "数据现象：QualityCore 相对高股息有收益优势但回撤未必更好——支持「继续保留可持续/风险过滤」，尚不足以支持上调某一维权重。"
            )
        else:
            lines.append("分段与全窗结论尚未指向单一权重方向；维持初始权重，等真 TR 覆盖扩大后再校准。")
    except Exception:
        lines.append("对照表不完整，维持初始权重。")
    # WF stability hint
    try:
        sub = wf_df[wf_df["strategy"] == "QualityCore"]
        anns = sub["ann_return"].astype(float)
        if anns.max() - anns.min() > 0.25:
            lines.append(
                f"Walk-forward 显示 QualityCore 年化跨段波动大（max−min≈{100*(anns.max()-anns.min()):.1f}pct），"
                "说明当前阈值/权重对样本外稳定性不足——优先修数据口径，再谈调权。"
            )
    except Exception:
        pass
    return "\n".join(f"- {x}" for x in lines)


if __name__ == "__main__":
    main()
