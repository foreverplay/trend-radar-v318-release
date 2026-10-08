#!/usr/bin/env python3
"""
V2.1 FundamentalEngine — local A-share financials only.

Prefer /workspace/ashare-financials/:
  clean/yjbb_*.parquet, lrb_*.parquet, xjll_*.parquet, zcfz_*.parquet
  analysis/merged_latest/company_latest_year_snapshot.csv

Missing core financials → data_insufficient=True (never invent 0 to filter).
No buy advice. No ZhuangStock label.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

FIN_ROOT = Path("/workspace/ashare-financials")
CLEAN = FIN_ROOT / "clean"
SNAP_PATH = FIN_ROOT / "analysis" / "merged_latest" / "company_latest_year_snapshot.csv"

# EM DATE_TYPE_CODE: 001年报 002中报 003一季 004三季
_DT_ANNUAL = "001"
_DT_SEMI = "002"
_DT_Q1 = "003"
_DT_Q3 = "004"

_BANK_BROKER_INS = (
    "银行",
    "证券",
    "保险",
    "多元金融",
    "资本市场",
    "银行Ⅱ",
    "证券Ⅱ",
    "保险Ⅱ",
)


def _norm_code(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if "." in c:
        c = c.split(".")[0]
    return c.zfill(6)


def _is_fin_industry(name: Optional[str]) -> bool:
    n = str(name or "")
    return any(k in n for k in _BANK_BROKER_INS)


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None or (isinstance(x, float) and not np.isfinite(x)):
            return None
        if pd.isna(x):
            return None
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _growth_pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None:
        return None
    if prev == 0:
        return None
    # both negative: still computable but flag quality separately
    return (cur / prev - 1.0) * 100.0


@dataclass
class FundamentalResult:
    ok: bool = False
    data_insufficient: bool = True
    score: Optional[float] = None  # None when insufficient
    level: str = "DATA_INSUFFICIENT"  # 优秀/良好/一般/风险/DATA_INSUFFICIENT
    tags: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    checklist: Dict[str, str] = field(default_factory=dict)  # ✓/⚠/—
    notes: List[str] = field(default_factory=list)
    industry: Optional[str] = None
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FundamentalEngine:
    """Lazy-loads panels once; evaluate(code) returns FundamentalResult."""

    def __init__(self, fin_root: Path = FIN_ROOT):
        self.fin_root = Path(fin_root)
        self.clean = self.fin_root / "clean"
        self._loaded = False
        self._yjbb: Optional[pd.DataFrame] = None
        self._lrb: Optional[pd.DataFrame] = None
        self._xjll: Optional[pd.DataFrame] = None
        self._zcfz: Optional[pd.DataFrame] = None
        self._snap: Optional[pd.DataFrame] = None
        self._ind_pe_median: Dict[str, float] = {}

    def _pick_parquet(self, stem: str) -> Optional[Path]:
        # prefer universe panel
        for name in (f"{stem}_universe_panel.parquet", f"{stem}_raw_panel.parquet"):
            p = self.clean / name
            if p.exists():
                return p
        # glob fallback
        matches = sorted(self.clean.glob(f"{stem}_*.parquet"))
        return matches[0] if matches else None

    def load(self) -> None:
        if self._loaded:
            return
        yj = self._pick_parquet("yjbb")
        lr = self._pick_parquet("lrb")
        xj = self._pick_parquet("xjll")
        zc = self._pick_parquet("zcfz")
        if yj is not None:
            cols = [
                "SECURITY_CODE",
                "SECURITY_NAME_ABBR",
                "REPORTDATE",
                "QDATE",
                "TOTAL_OPERATE_INCOME",
                "PARENT_NETPROFIT",
                "WEIGHTAVG_ROE",
                "YSTZ",
                "SJLTZ",
                "MGJYXJJE",
                "PUBLISHNAME",
                "period",
                "DEDUCT_BASIC_EPS",
                "BASIC_EPS",
            ]
            raw = pd.read_parquet(yj)
            use = [c for c in cols if c in raw.columns]
            self._yjbb = raw[use].copy()
            self._yjbb["SECURITY_CODE"] = self._yjbb["SECURITY_CODE"].astype(str).str.zfill(6)
            if "REPORTDATE" in self._yjbb.columns:
                self._yjbb["REPORTDATE"] = pd.to_datetime(self._yjbb["REPORTDATE"], errors="coerce")
        if lr is not None:
            cols = [
                "SECURITY_CODE",
                "REPORT_DATE",
                "DATE_TYPE_CODE",
                "PARENT_NETPROFIT",
                "DEDUCT_PARENT_NETPROFIT",
                "TOTAL_OPERATE_INCOME",
                "INDUSTRY_NAME",
            ]
            raw = pd.read_parquet(lr)
            use = [c for c in cols if c in raw.columns]
            self._lrb = raw[use].copy()
            self._lrb["SECURITY_CODE"] = self._lrb["SECURITY_CODE"].astype(str).str.zfill(6)
            self._lrb["REPORT_DATE"] = pd.to_datetime(self._lrb["REPORT_DATE"], errors="coerce")
            self._lrb["DATE_TYPE_CODE"] = self._lrb["DATE_TYPE_CODE"].astype(str).str.zfill(3)
        if xj is not None:
            cols = ["SECURITY_CODE", "REPORT_DATE", "DATE_TYPE_CODE", "NETCASH_OPERATE"]
            raw = pd.read_parquet(xj)
            use = [c for c in cols if c in raw.columns]
            self._xjll = raw[use].copy()
            self._xjll["SECURITY_CODE"] = self._xjll["SECURITY_CODE"].astype(str).str.zfill(6)
            self._xjll["REPORT_DATE"] = pd.to_datetime(self._xjll["REPORT_DATE"], errors="coerce")
            self._xjll["DATE_TYPE_CODE"] = self._xjll["DATE_TYPE_CODE"].astype(str).str.zfill(3)
        if zc is not None:
            cols = ["SECURITY_CODE", "REPORT_DATE", "DATE_TYPE_CODE", "ACCOUNTS_RECE"]
            raw = pd.read_parquet(zc)
            use = [c for c in cols if c in raw.columns]
            self._zcfz = raw[use].copy()
            self._zcfz["SECURITY_CODE"] = self._zcfz["SECURITY_CODE"].astype(str).str.zfill(6)
            self._zcfz["REPORT_DATE"] = pd.to_datetime(self._zcfz["REPORT_DATE"], errors="coerce")
            self._zcfz["DATE_TYPE_CODE"] = self._zcfz["DATE_TYPE_CODE"].astype(str).str.zfill(3)

        snap_p = self.fin_root / "analysis" / "merged_latest" / "company_latest_year_snapshot.csv"
        if snap_p.exists():
            snap = pd.read_csv(snap_p, dtype={"代码": str})
            snap["代码"] = snap["代码"].astype(str).str.zfill(6)
            self._snap = snap

        self._build_industry_roe_proxy()
        self._loaded = True

    def _build_industry_roe_proxy(self) -> None:
        """No PE in local panels — keep industry ROE median as soft valuation context only.
        PE premium left unavailable unless caller injects PE later."""
        self._ind_roe_median: Dict[str, float] = {}
        if self._snap is None or "行业" not in self._snap.columns:
            return
        s = self._snap.copy()
        if "净资产收益率" not in s.columns:
            return
        s["净资产收益率"] = pd.to_numeric(s["净资产收益率"], errors="coerce")
        for ind, g in s.groupby("行业"):
            med = g["净资产收益率"].median()
            if pd.notna(med):
                self._ind_roe_median[str(ind)] = float(med)

    def industry_of(self, code6: str) -> Optional[str]:
        self.load()
        code6 = _norm_code(code6)
        if self._snap is not None:
            m = self._snap[self._snap["代码"] == code6]
            if len(m):
                ind = m.iloc[0].get("行业")
                if ind and str(ind) not in ("nan", "None", "--", ""):
                    return str(ind)
        if self._lrb is not None:
            m = self._lrb[self._lrb["SECURITY_CODE"] == code6]
            if len(m):
                ind = m.sort_values("REPORT_DATE").iloc[-1].get("INDUSTRY_NAME")
                if ind and str(ind) not in ("nan", "None", "--", ""):
                    return str(ind)
        if self._yjbb is not None:
            m = self._yjbb[self._yjbb["SECURITY_CODE"] == code6]
            if len(m) and "PUBLISHNAME" in m.columns:
                ind = m.sort_values("REPORTDATE").iloc[-1].get("PUBLISHNAME")
                if ind and str(ind) not in ("nan", "None", "--", ""):
                    return str(ind)
        return None

    def _lrb_rows(self, code6: str) -> pd.DataFrame:
        if self._lrb is None:
            return pd.DataFrame()
        return self._lrb[self._lrb["SECURITY_CODE"] == code6].sort_values("REPORT_DATE")

    def _single_quarter_profits(self, code6: str) -> List[Tuple[pd.Timestamp, Optional[float]]]:
        """Derive last single-quarter profits from cumulative LRB rows."""
        df = self._lrb_rows(code6)
        if df.empty:
            return []
        by_date = {pd.Timestamp(r.REPORT_DATE): r for r in df.itertuples(index=False)}
        dates = sorted(by_date.keys())
        out: List[Tuple[pd.Timestamp, Optional[float]]] = []
        for d in dates:
            r = by_date[d]
            dt = str(getattr(r, "DATE_TYPE_CODE", "")).zfill(3)
            profit = _safe_float(getattr(r, "PARENT_NETPROFIT", None))
            if dt == _DT_Q1 or dt == _DT_ANNUAL:
                # Q1 and annual: annual is full year — for continuity we want Q4 = annual - Q3
                if dt == _DT_Q1:
                    out.append((d, profit))
                else:
                    # annual → Q4 = annual - Q3 same year
                    q3 = pd.Timestamp(year=d.year, month=9, day=30)
                    if q3 in by_date:
                        q3p = _safe_float(getattr(by_date[q3], "PARENT_NETPROFIT", None))
                        if profit is not None and q3p is not None:
                            out.append((d, profit - q3p))
                        else:
                            out.append((d, None))
                    else:
                        out.append((d, None))
            elif dt == _DT_SEMI:
                q1 = pd.Timestamp(year=d.year, month=3, day=31)
                if q1 in by_date:
                    q1p = _safe_float(getattr(by_date[q1], "PARENT_NETPROFIT", None))
                    if profit is not None and q1p is not None:
                        out.append((d, profit - q1p))
                    else:
                        out.append((d, None))
                else:
                    out.append((d, None))
            elif dt == _DT_Q3:
                semi = pd.Timestamp(year=d.year, month=6, day=30)
                if semi in by_date:
                    sp = _safe_float(getattr(by_date[semi], "PARENT_NETPROFIT", None))
                    if profit is not None and sp is not None:
                        out.append((d, profit - sp))
                    else:
                        out.append((d, None))
                else:
                    out.append((d, None))
        return out

    def evaluate(self, code: str, pe: Optional[float] = None, industry_pe: Optional[float] = None) -> FundamentalResult:
        self.load()
        code6 = _norm_code(code)
        res = FundamentalResult()
        tags: List[str] = []
        notes: List[str] = []
        metrics: Dict[str, Any] = {"code6": code6}
        checklist: Dict[str, str] = {}

        industry = self.industry_of(code6)
        res.industry = industry
        metrics["industry"] = industry

        # --- latest annual / latest period from snapshot + lrb ---
        snap_row = None
        if self._snap is not None:
            m = self._snap[self._snap["代码"] == code6]
            if len(m):
                snap_row = m.iloc[0]

        lrb = self._lrb_rows(code6)
        if lrb.empty and snap_row is None:
            res.data_insufficient = True
            res.tags = ["DATA_INSUFFICIENT"]
            res.notes = ["无本地财务快照/利润表"]
            res.checklist = {"基本面": "⚠"}
            return res

        # Prefer latest annual for YoY; also keep latest any period
        annual = lrb[lrb["DATE_TYPE_CODE"] == _DT_ANNUAL] if not lrb.empty else pd.DataFrame()
        latest_any = lrb.iloc[-1] if not lrb.empty else None
        latest_annual = annual.iloc[-1] if len(annual) else None
        prev_annual = annual.iloc[-2] if len(annual) >= 2 else None

        # Same DATE_TYPE YoY for latest period (prefer matching type)
        yoy_cur = None
        yoy_prev = None
        if latest_any is not None:
            dt = str(latest_any["DATE_TYPE_CODE"]).zfill(3)
            same = lrb[lrb["DATE_TYPE_CODE"] == dt]
            if len(same) >= 1:
                yoy_cur = same.iloc[-1]
            if len(same) >= 2:
                yoy_prev = same.iloc[-2]

        # Profit / deducted / revenue
        profit = None
        deducted = None
        revenue = None
        if snap_row is not None:
            profit = _safe_float(snap_row.get("归母净利润"))
            deducted = _safe_float(snap_row.get("扣非归母净利润"))
            revenue = _safe_float(snap_row.get("营业收入"))
            roe_snap = _safe_float(snap_row.get("净资产收益率"))
            metrics["report_period_snap"] = str(snap_row.get("报告期") or "")
        else:
            roe_snap = None

        if latest_annual is not None:
            if profit is None:
                profit = _safe_float(latest_annual.get("PARENT_NETPROFIT"))
            if deducted is None:
                deducted = _safe_float(latest_annual.get("DEDUCT_PARENT_NETPROFIT"))
            if revenue is None:
                revenue = _safe_float(latest_annual.get("TOTAL_OPERATE_INCOME"))

        # Growth: prefer yjbb YSTZ/SJLTZ on latest row; else compute from LRB
        rev_growth = None
        ded_growth = None
        profit_growth = None
        src_bits = []
        if self._yjbb is not None:
            yj = self._yjbb[self._yjbb["SECURITY_CODE"] == code6].sort_values("REPORTDATE")
            if len(yj):
                last_yj = yj.iloc[-1]
                rev_growth = _safe_float(last_yj.get("YSTZ"))
                profit_growth = _safe_float(last_yj.get("SJLTZ"))
                metrics["yjbb_qdate"] = str(last_yj.get("QDATE") or "")
                src_bits.append("yjbb")

        if yoy_cur is not None and yoy_prev is not None:
            if rev_growth is None:
                rev_growth = _growth_pct(
                    _safe_float(yoy_cur.get("TOTAL_OPERATE_INCOME")),
                    _safe_float(yoy_prev.get("TOTAL_OPERATE_INCOME")),
                )
            cur_ded = _safe_float(yoy_cur.get("DEDUCT_PARENT_NETPROFIT"))
            prev_ded = _safe_float(yoy_prev.get("DEDUCT_PARENT_NETPROFIT"))
            ded_growth = _growth_pct(cur_ded, prev_ded)
            if profit_growth is None:
                profit_growth = _growth_pct(
                    _safe_float(yoy_cur.get("PARENT_NETPROFIT")),
                    _safe_float(yoy_prev.get("PARENT_NETPROFIT")),
                )
            src_bits.append("lrb_yoy")
            metrics["yoy_period"] = str(pd.Timestamp(yoy_cur["REPORT_DATE"]).date())

        # PROFIT_QUALITY_RISK: profit up but deducted down / gap large
        profit_quality_risk = False
        if profit_growth is not None and ded_growth is not None:
            if profit_growth > 5 and ded_growth < -5:
                profit_quality_risk = True
            if profit is not None and deducted is not None and profit > 0:
                if deducted < profit * 0.5:
                    profit_quality_risk = True
        if profit_quality_risk:
            tags.append("PROFIT_QUALITY_RISK")

        # CashProfitRatio
        cash_profit_ratio = None
        skip_cash = _is_fin_industry(industry)
        if not skip_cash and self._xjll is not None and latest_any is not None:
            rd = pd.Timestamp(latest_any["REPORT_DATE"])
            dt = str(latest_any["DATE_TYPE_CODE"]).zfill(3)
            xj = self._xjll[
                (self._xjll["SECURITY_CODE"] == code6)
                & (self._xjll["DATE_TYPE_CODE"] == dt)
                & (self._xjll["REPORT_DATE"] == rd)
            ]
            # fallback: match date only
            if xj.empty:
                xj = self._xjll[
                    (self._xjll["SECURITY_CODE"] == code6) & (self._xjll["REPORT_DATE"] == rd)
                ]
            netcash = _safe_float(xj.iloc[0]["NETCASH_OPERATE"]) if len(xj) else None
            per_profit = _safe_float(latest_any.get("PARENT_NETPROFIT"))
            if netcash is not None and per_profit is not None and per_profit != 0:
                cash_profit_ratio = netcash / per_profit
            src_bits.append("xjll")
        elif skip_cash:
            notes.append("金融行业跳过朴素现金流/利润比")
            tags.append("CASH_RATIO_SKIPPED_FIN")

        # ReceivableGap: ΔAR vs ΔRevenue (latest vs prior same type)
        receivable_gap = None
        if self._zcfz is not None and yoy_cur is not None and yoy_prev is not None:
            dt = str(yoy_cur["DATE_TYPE_CODE"]).zfill(3)
            zc = self._zcfz[
                (self._zcfz["SECURITY_CODE"] == code6) & (self._zcfz["DATE_TYPE_CODE"] == dt)
            ].sort_values("REPORT_DATE")
            if len(zc) >= 2:
                ar_cur = _safe_float(zc.iloc[-1]["ACCOUNTS_RECE"])
                ar_prev = _safe_float(zc.iloc[-2]["ACCOUNTS_RECE"])
                rev_cur = _safe_float(yoy_cur.get("TOTAL_OPERATE_INCOME"))
                rev_prev = _safe_float(yoy_prev.get("TOTAL_OPERATE_INCOME"))
                if None not in (ar_cur, ar_prev, rev_cur, rev_prev) and rev_prev != 0:
                    d_ar = ar_cur - ar_prev
                    d_rev = rev_cur - rev_prev
                    # gap: receivables grew faster than revenue (ratio)
                    if abs(d_rev) > 1:
                        receivable_gap = d_ar / d_rev
                    metrics["delta_ar"] = d_ar
                    metrics["delta_rev"] = d_rev
            src_bits.append("zcfz")

        # last 4Q profit count (>0)
        sq = self._single_quarter_profits(code6)
        last4 = [p for _, p in sq[-4:]] if sq else []
        profit_pos_4q = sum(1 for p in last4 if p is not None and p > 0)
        profit_known_4q = sum(1 for p in last4 if p is not None)
        metrics["last4q_profits"] = last4
        metrics["profit_pos_4q"] = profit_pos_4q
        metrics["profit_known_4q"] = profit_known_4q

        # ROE
        roe = roe_snap
        if roe is None and self._yjbb is not None:
            yj = self._yjbb[self._yjbb["SECURITY_CODE"] == code6].sort_values("REPORTDATE")
            if len(yj):
                roe = _safe_float(yj.iloc[-1].get("WEIGHTAVG_ROE"))

        # PE premium (only if provided)
        pe_premium = None
        if pe is not None and industry_pe is not None and industry_pe > 0:
            pe_premium = (pe / industry_pe - 1.0) * 100.0
        elif pe is not None and industry and industry in getattr(self, "_ind_pe_median", {}):
            ip = self._ind_pe_median[industry]
            if ip > 0:
                pe_premium = (pe / ip - 1.0) * 100.0

        metrics.update(
            {
                "profit": profit,
                "deducted_profit": deducted,
                "revenue": revenue,
                "revenue_growth_pct": rev_growth,
                "deducted_profit_growth_pct": ded_growth,
                "profit_growth_pct": profit_growth,
                "cash_profit_ratio": cash_profit_ratio,
                "receivable_gap": receivable_gap,
                "roe": roe,
                "pe": pe,
                "pe_premium_pct": pe_premium,
                "profit_quality_risk": profit_quality_risk,
            }
        )

        # Core sufficiency: need at least profit or revenue signal
        core_ok = profit is not None or revenue is not None
        if not core_ok:
            res.data_insufficient = True
            res.tags = ["DATA_INSUFFICIENT"]
            res.notes = ["核心盈利/营收字段缺失"]
            res.checklist = {"基本面": "⚠"}
            res.metrics = metrics
            return res

        # ---- score components (skip missing; never invent 0) ----
        weights = {
            "盈利": 20.0,
            "营收": 15.0,
            "扣非": 20.0,
            "现金流": 20.0,
            "连续性": 10.0,
            "ROE": 10.0,
            "估值": 5.0,
        }
        parts: Dict[str, Optional[float]] = {}

        # 盈利: profit>0
        if profit is None:
            parts["盈利"] = None
        elif profit > 0:
            parts["盈利"] = 20.0
            checklist["盈利"] = "✓"
        else:
            parts["盈利"] = 0.0
            checklist["盈利"] = "⚠"
            tags.append("LOSS_MAKING")

        # 营收 growth
        if rev_growth is None:
            parts["营收"] = None
            checklist["营收"] = "—"
        elif rev_growth >= 20:
            parts["营收"] = 15.0
            checklist["营收"] = "✓"
        elif rev_growth >= 5:
            parts["营收"] = 11.0
            checklist["营收"] = "✓"
        elif rev_growth >= 0:
            parts["营收"] = 7.0
            checklist["营收"] = "✓"
        elif rev_growth >= -10:
            parts["营收"] = 3.0
            checklist["营收"] = "⚠"
        else:
            parts["营收"] = 0.0
            checklist["营收"] = "⚠"

        # 扣非
        if deducted is None and ded_growth is None:
            parts["扣非"] = None
            checklist["扣非"] = "—"
        else:
            sc = 0.0
            if deducted is not None and deducted > 0:
                sc += 10.0
            if ded_growth is not None:
                if ded_growth >= 20:
                    sc += 10.0
                elif ded_growth >= 5:
                    sc += 7.0
                elif ded_growth >= 0:
                    sc += 4.0
                elif ded_growth >= -15:
                    sc += 1.0
            if profit_quality_risk:
                sc = max(0.0, sc - 8.0)
            parts["扣非"] = min(20.0, sc)
            checklist["扣非"] = "⚠" if profit_quality_risk or (deducted is not None and deducted <= 0) else "✓"

        # 现金流
        if skip_cash:
            parts["现金流"] = None  # skip weight
            checklist["现金流"] = "—"
        elif cash_profit_ratio is None:
            parts["现金流"] = None
            checklist["现金流"] = "—"
        elif cash_profit_ratio >= 1.0:
            parts["现金流"] = 20.0
            checklist["现金流"] = "✓"
        elif cash_profit_ratio >= 0.5:
            parts["现金流"] = 14.0
            checklist["现金流"] = "✓"
        elif cash_profit_ratio >= 0.0:
            parts["现金流"] = 7.0
            checklist["现金流"] = "⚠"
        else:
            parts["现金流"] = 0.0
            checklist["现金流"] = "⚠"
            tags.append("NEG_OPERATING_CASH")

        # receivable gap soft penalty inside cash if extreme
        if receivable_gap is not None and receivable_gap > 1.5 and parts.get("现金流") is not None:
            parts["现金流"] = max(0.0, float(parts["现金流"]) - 5.0)
            tags.append("RECEIVABLE_GAP_HIGH")
            notes.append(f"应收增速相对营收偏快 gap={receivable_gap:.2f}")

        # 连续性 last 4Q
        if profit_known_4q < 2:
            parts["连续性"] = None
            checklist["连续性"] = "—"
        else:
            ratio = profit_pos_4q / max(profit_known_4q, 1)
            if profit_pos_4q >= 4:
                parts["连续性"] = 10.0
                checklist["连续性"] = "✓"
            elif profit_pos_4q >= 3:
                parts["连续性"] = 7.0
                checklist["连续性"] = "✓"
            elif profit_pos_4q >= 2:
                parts["连续性"] = 4.0
                checklist["连续性"] = "⚠"
            else:
                parts["连续性"] = 0.0
                checklist["连续性"] = "⚠"

        # ROE bands
        if roe is None:
            parts["ROE"] = None
            checklist["ROE"] = "—"
        elif roe >= 15:
            parts["ROE"] = 10.0
            checklist["ROE"] = "✓"
            tags.append("ROE_STRONG")
        elif roe >= 10:
            parts["ROE"] = 7.0
            checklist["ROE"] = "✓"
        elif roe >= 5:
            parts["ROE"] = 4.0
            checklist["ROE"] = "⚠"
        elif roe >= 0:
            parts["ROE"] = 2.0
            checklist["ROE"] = "⚠"
        else:
            parts["ROE"] = 0.0
            checklist["ROE"] = "⚠"

        # 估值 PE premium — only if available
        if pe_premium is None:
            parts["估值"] = None
            checklist["估值"] = "—"
            notes.append("无PE/行业PE，估值子项跳过")
        elif pe_premium <= -20:
            parts["估值"] = 5.0
            checklist["估值"] = "✓"
        elif pe_premium <= 0:
            parts["估值"] = 4.0
            checklist["估值"] = "✓"
        elif pe_premium <= 30:
            parts["估值"] = 2.0
            checklist["估值"] = "⚠"
        else:
            parts["估值"] = 0.0
            checklist["估值"] = "⚠"
            tags.append("PE_PREMIUM_HIGH")

        # Normalize over available weights
        avail_w = 0.0
        earned = 0.0
        breakdown = {}
        for k, w in weights.items():
            v = parts.get(k)
            if v is None:
                breakdown[k] = None
                continue
            avail_w += w
            earned += float(v)
            breakdown[k] = float(v)

        if avail_w < 40:  # too few dimensions
            res.data_insufficient = True
            res.score = None
            res.level = "DATA_INSUFFICIENT"
            tags = ["DATA_INSUFFICIENT"] + [t for t in tags if t != "DATA_INSUFFICIENT"]
            checklist["基本面"] = "⚠"
            notes.append(f"可用维度权重仅 {avail_w:.0f}/100")
            res.ok = False
            res.tags = tags
            res.notes = notes
            res.metrics = {**metrics, "score_breakdown": breakdown}
            res.checklist = checklist
            res.source = "+".join(src_bits) or "partial"
            return res

        score = earned / avail_w * 100.0
        score = float(round(min(100.0, max(0.0, score)), 1))

        if score >= 80:
            level = "优秀"
        elif score >= 65:
            level = "良好"
        elif score >= 50:
            level = "一般"
        else:
            level = "风险"

        if level == "优秀":
            tags.append("FUND_EXCELLENT")
        elif level == "风险":
            tags.append("FUND_RISK")

        checklist["基本面"] = "✓" if score >= 60 else "⚠"

        res.ok = True
        res.data_insufficient = False
        res.score = score
        res.level = level
        res.tags = tags
        res.notes = notes
        res.metrics = {**metrics, "score_breakdown": breakdown, "available_weight": avail_w}
        res.checklist = checklist
        res.source = "+".join(dict.fromkeys(src_bits)) or "snap"
        return res


__all__ = ["FundamentalEngine", "FundamentalResult"]
