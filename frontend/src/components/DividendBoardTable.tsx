import { Fragment, useEffect, useRef, useState } from "react";
import type { BoardRow, DividendBoardRow } from "../types";
import { zhDividendCell, zhDividendColumnLabel } from "../zhLabels";
import { normalizeStockCode } from "./CopyCodeButton";
import { RowMiniCharts } from "./MiniCharts";
import StockHoverCard, { type HoverCardMeta } from "./StockHoverCard";
import StockIdentity from "./StockIdentity";

type ColKey =
  | "stock"
  | "dividend_grade"
  | "dividend_quality_score"
  | "dividend_sustainability_score"
  | "dividend_trend_score"
  | "dividend_opportunity_score"
  | "dividend_risk"
  | "dividend_entry_status"
  | "dividend_yield_ttm"
  | "industry_group"
  | "valuation_score"
  | "action_hint";

const COL_KEYS: ColKey[] = [
  "stock",
  "dividend_grade",
  "dividend_quality_score",
  "dividend_sustainability_score",
  "dividend_trend_score",
  "dividend_opportunity_score",
  "dividend_risk",
  "dividend_entry_status",
  "dividend_yield_ttm",
  "industry_group",
  "valuation_score",
  "action_hint",
];

const NUMERIC = new Set<ColKey>([
  "dividend_quality_score",
  "dividend_sustainability_score",
  "dividend_trend_score",
  "dividend_opportunity_score",
  "dividend_yield_ttm",
  "valuation_score",
]);

const GRADE_ORD: Record<string, number> = {
  "A+": 5,
  A: 4,
  B: 3,
  C: 2,
  WATCH: 1,
  FILTER: 0,
};

const RISK_ORD: Record<string, number> = {
  SAFE: 1,
  WATCH: 2,
  WARNING: 3,
  HIGH: 4,
};

type SortDir = "desc" | "asc";
type SortState = { key: ColKey; dir: SortDir };

const DEFAULT_SORT: SortState = { key: "dividend_opportunity_score", dir: "desc" };

function cycleSort(prev: SortState, key: ColKey): SortState {
  if (prev.key !== key) return { key, dir: "desc" };
  if (prev.dir === "desc") return { key, dir: "asc" };
  return DEFAULT_SORT;
}

function parseNumber(raw: unknown): number | null {
  if (raw == null || raw === "" || raw === "—") return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  const n = Number(String(raw).replace(/%/g, "").replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

function fmtScore(raw: unknown): string {
  const n = parseNumber(raw);
  if (n == null) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function rowCode(r: DividendBoardRow): string {
  return normalizeStockCode(r.symbol || r.code);
}

function stockText(r: DividendBoardRow): string {
  const code = rowCode(r);
  if (r.name && code) return `${r.name}(${code})`;
  return r.name || code || "—";
}

function joinList(raw: unknown, prefix = ""): string {
  if (Array.isArray(raw)) {
    const text = raw.filter(Boolean).join("；");
    return text ? `${prefix}${text}` : "";
  }
  if (raw == null || raw === "") return "";
  return `${prefix}${String(raw)}`;
}

function cellText(r: DividendBoardRow, key: ColKey): string {
  if (key === "stock") return stockText(r);
  if (key === "dividend_yield_ttm") {
    const n = parseNumber(r.dividend_yield_ttm);
    if (n == null) return "—";
    const pct = n <= 1 ? n * 100 : n;
    return `${pct.toFixed(2)}%`;
  }
  const raw = r[key as keyof DividendBoardRow];
  if (Array.isArray(raw)) return raw.length ? raw.join("；") : "—";
  if (typeof raw === "boolean") return raw ? "是" : "否";
  if (raw == null || raw === "") return "—";
  if (NUMERIC.has(key)) return fmtScore(raw);
  return zhDividendCell(key, String(raw));
}

function gradeClass(g?: string): string {
  if (g === "A+") return "g-aplus";
  if (g === "A") return "g-a";
  if (g === "B") return "g-b";
  if (g === "C") return "g-c";
  return "";
}

function riskClass(r?: string): string {
  if (!r) return "";
  const u = r.toUpperCase();
  if (u === "SAFE") return "risk-safe";
  if (u === "WATCH") return "risk-watch";
  if (u === "WARNING") return "risk-warn";
  if (u === "HIGH") return "risk-high";
  return "";
}

function compare(a: DividendBoardRow, b: DividendBoardRow, key: ColKey): number {
  if (key === "stock") return stockText(a).localeCompare(stockText(b), "zh-CN");
  if (key === "dividend_grade") {
    return (GRADE_ORD[String(a.dividend_grade || "").toUpperCase()] ?? -1) -
      (GRADE_ORD[String(b.dividend_grade || "").toUpperCase()] ?? -1);
  }
  if (key === "dividend_risk") {
    return (RISK_ORD[String(a.dividend_risk || "").toUpperCase()] ?? 0) -
      (RISK_ORD[String(b.dividend_risk || "").toUpperCase()] ?? 0);
  }
  if (NUMERIC.has(key)) {
    return (parseNumber(a[key as keyof DividendBoardRow]) ?? 0) -
      (parseNumber(b[key as keyof DividendBoardRow]) ?? 0);
  }
  return cellText(a, key).localeCompare(cellText(b, key), "zh-CN");
}

function isMissing(row: DividendBoardRow, key: ColKey): boolean {
  if (key === "stock") return stockText(row) === "—";
  if (NUMERIC.has(key)) return parseNumber(row[key as keyof DividendBoardRow]) == null;
  const t = cellText(row, key);
  return !t || t === "—";
}

function sortRows(rows: DividendBoardRow[], sort: SortState): DividendBoardRow[] {
  const indexed = rows.map((row, index) => ({ row, index }));
  indexed.sort((a, b) => {
    const am = isMissing(a.row, sort.key);
    const bm = isMissing(b.row, sort.key);
    if (am || bm) {
      if (am && bm) return a.index - b.index;
      return am ? 1 : -1;
    }
    const cmp = compare(a.row, b.row, sort.key);
    if (cmp === 0) return a.index - b.index;
    return sort.dir === "asc" ? cmp : -cmp;
  });
  return indexed.map((item) => item.row);
}

function toHoverRow(r: DividendBoardRow): BoardRow {
  const code = rowCode(r);
  return {
    code,
    name: r.name,
    stock: stockText(r),
    trend_grade: r.dividend_grade,
    tech: fmtScore(r.dividend_quality_score),
    cont: fmtScore(r.dividend_sustainability_score),
    sector: fmtScore(r.dividend_trend_score),
    protect: r.protect != null ? String(r.protect) : r.protection_price != null ? String(r.protection_price) : undefined,
    break_price: r.break_price != null ? String(r.break_price) : r.trend_break_price != null ? String(r.trend_break_price) : undefined,
  };
}

function hoverMeta(r: DividendBoardRow, asOf: string): HoverCardMeta {
  const yieldText = cellText(r, "dividend_yield_ttm");
  const lines = [
    `红利等级 ${r.dividend_grade || "—"}，入场 ${zhDividendCell("dividend_entry_status", r.dividend_entry_status)}，风险 ${zhDividendCell("dividend_risk", r.dividend_risk)}。`,
    `股息率(TTM) ${yieldText}，行业 ${r.industry_group || "—"}，估值分 ${fmtScore(r.valuation_score)}。`,
    joinList(r.why_watch, "关注：") || joinList(r.reasons, "理由：") || "等级与分数只描述红利质量与条件，不构成买卖建议。",
    joinList(r.why_not_now, "暂缓："),
    joinList(r.wait_for, "等待："),
    r.action_hint ? `动作提示：${zhDividendCell("action_hint", r.action_hint)}` : "",
  ].filter(Boolean);
  return {
    subtitle: `观察日 ${asOf} · 红利等级 ${r.dividend_grade || "—"} · ${zhDividendCell("dividend_entry_status", r.dividend_entry_status)}`,
    scores: [
      ["质量分", fmtScore(r.dividend_quality_score)],
      ["可持续", fmtScore(r.dividend_sustainability_score)],
      ["趋势分", fmtScore(r.dividend_trend_score)],
      ["机会分", fmtScore(r.dividend_opportunity_score)],
    ],
    evidenceLines: lines,
  };
}

export default function DividendBoardTable({
  rows,
  emptyText,
  asOf,
}: {
  rows: DividendBoardRow[];
  emptyText: string;
  asOf: string;
}) {
  const [open, setOpen] = useState<{ row: DividendBoardRow; anchor: HTMLElement } | null>(null);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [liveRows, setLiveRows] = useState<Set<number>>(() => new Set());
  const openTimer = useRef<number>();
  const closeTimer = useRef<number>();
  const chartNodes = useRef(new Map<number, HTMLTableRowElement>());
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewRows = sortRows(rows, sort);
  const rowKey = viewRows.map((row, index) => `${index}:${rowCode(row) || row.name || ""}`).join("|");

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => el.style.setProperty("--board-view", `${el.clientWidth}px`);
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [rowKey]);

  useEffect(() => {
    const nodes = [...chartNodes.current.values()].filter((node) => node.isConnected);
    if (!nodes.length) return;
    const hits = new Map<Element, number>();
    const count = rowKey ? rowKey.split("|").length : 0;
    const publish = () => {
      const seen = new Set<number>();
      for (const index of hits.values()) seen.add(index);
      let max = -1;
      for (const index of seen) if (index > max) max = index;
      if (max >= 0) {
        for (let step = 1; step <= 3 && max + step < count; step += 1) seen.add(max + step);
      }
      setLiveRows((prev) => {
        if (prev.size === seen.size && [...seen].every((index) => prev.has(index))) return prev;
        return seen;
      });
    };
    const observer = new IntersectionObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const index = Number((entry.target as HTMLElement).dataset.rowIndex);
        if (!Number.isFinite(index)) continue;
        if (entry.isIntersecting) {
          if (hits.get(entry.target) !== index) {
            hits.set(entry.target, index);
            changed = true;
          }
        } else if (hits.delete(entry.target)) {
          changed = true;
        }
      }
      if (changed) publish();
    }, { rootMargin: "160px" });
    nodes.forEach((node) => observer.observe(node));
    return () => {
      observer.disconnect();
    };
  }, [rowKey]);

  function cancelClose() {
    window.clearTimeout(closeTimer.current);
  }
  function armClose() {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(null), 420);
  }
  function schedule(row: DividendBoardRow, anchor: HTMLElement) {
    cancelClose();
    window.clearTimeout(openTimer.current);
    openTimer.current = window.setTimeout(() => setOpen({ row, anchor }), 180);
  }

  function sortHead() {
    return (
      <thead>
        <tr>
          {COL_KEYS.map((c) => {
            const active = sort.key === c;
            return (
              <th
                key={c}
                aria-sort={!active ? "none" : sort.dir === "asc" ? "ascending" : "descending"}
              >
                <button
                  type="button"
                  className={active ? "th-sort on" : "th-sort"}
                  onClick={() => {
                    setSort((prev) => cycleSort(prev, c));
                    setOpen(null);
                  }}
                >
                  {zhDividendColumnLabel(c)}
                  <span className="th-sort-mark" aria-hidden="true">
                    {active ? (sort.dir === "desc" ? "↓" : "↑") : ""}
                  </span>
                </button>
              </th>
            );
          })}
        </tr>
      </thead>
    );
  }

  function dataRow(r: DividendBoardRow, i: number) {
    const code = rowCode(r);
    const opened = open && rowCode(open.row) === code && open.row.name === r.name;
    return (
      <Fragment key={`${code || r.name}-${i}`}>
        <tr className={opened ? "board-info row-open" : "board-info"}>
          {COL_KEYS.map((c) => (
            <td
              key={c}
              className={
                c === "dividend_grade"
                  ? gradeClass(r.dividend_grade)
                  : c === "dividend_risk"
                    ? riskClass(r.dividend_risk)
                    : ""
              }
              title={
                c === "action_hint"
                  ? [
                      joinList(r.why_watch),
                      joinList(r.why_not_now),
                      joinList(r.wait_for, "等待："),
                    ]
                      .filter(Boolean)
                      .join(" | ") || undefined
                  : undefined
              }
            >
              {c === "stock" && code ? (
                <StockIdentity
                  label={stockText(r)}
                  code={code}
                  tipProps={{
                    onMouseEnter: (e) => {
                      if (!window.matchMedia("(hover: hover)").matches) return;
                      cancelClose();
                      if (open) {
                        window.clearTimeout(openTimer.current);
                        setOpen({ row: r, anchor: e.currentTarget });
                        return;
                      }
                      schedule(r, e.currentTarget);
                    },
                    onMouseLeave: () => {
                      window.clearTimeout(openTimer.current);
                    },
                    onFocus: (e) => schedule(r, e.currentTarget),
                    onBlur: armClose,
                    onClick: (e) => {
                      window.clearTimeout(openTimer.current);
                      cancelClose();
                      setOpen({ row: r, anchor: e.currentTarget });
                    },
                  }}
                />
              ) : (
                cellText(r, c)
              )}
            </td>
          ))}
        </tr>
        <tr
          className={opened ? "board-charts row-open" : "board-charts"}
          data-row-index={i}
          ref={(el) => {
            if (el) chartNodes.current.set(i, el);
            else chartNodes.current.delete(i);
          }}
        >
          <td colSpan={COL_KEYS.length}>
            <RowMiniCharts code={code} asOf={asOf} live={liveRows.has(i)} />
          </td>
        </tr>
      </Fragment>
    );
  }

  if (!rows.length) {
    return <p className="empty">{emptyText}</p>;
  }

  return (
    <div className="table-wrap" ref={wrapRef} onMouseEnter={cancelClose} onMouseLeave={armClose}>
      <table>
        {sortHead()}
        <tbody>{viewRows.map((r, i) => dataRow(r, i))}</tbody>
      </table>
      {open ? (
        <StockHoverCard
          row={toHoverRow(open.row)}
          asOf={asOf}
          anchor={open.anchor}
          onClose={() => setOpen(null)}
          onPointerEnter={cancelClose}
          onPointerLeave={armClose}
          meta={hoverMeta(open.row, asOf)}
        />
      ) : null}
    </div>
  );
}
