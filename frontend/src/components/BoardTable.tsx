import { useRef, useState } from "react";
import type { BoardRow } from "../types";
import { zhCell, zhColumnLabel } from "../zhLabels";
import StockHoverCard from "./StockHoverCard";

const COL_KEYS: (keyof BoardRow)[] = [
  "stock",
  "stage",
  "trend_grade",
  "maturity",
  "entry_status",
  "path",
  "trend_gain_pct",
  "tech",
  "cont",
  "sector",
  "protect",
  "break_price",
  "exit_signal",
];

const NUMERIC_KEYS = new Set<keyof BoardRow>([
  "trend_gain_pct",
  "tech",
  "cont",
  "sector",
  "protect",
  "break_price",
]);

// 这些列有文档里的强弱顺序，按字母比会把 A+ 排到 A 后面。
const ORDINAL: Partial<Record<keyof BoardRow, Record<string, number>>> = {
  trend_grade: { "A+": 4, A: 3, B: 2, C: 1, WATCH: 0, FILTER: -1 },
  maturity: { EARLY: 1, BUILDING: 2, ADVANCED: 3, MATURE: 4 },
  entry_status: { NO_ENTRY: 1, EXTENDED: 2, FOLLOW: 3, CONFIRMED: 4, PRIME: 5 },
};

type SortDir = "desc" | "asc";
type SortState = { key: keyof BoardRow; dir: SortDir };

// 进页面时榜单已按趋势等级降序（A+ > A > B > C）。取消排序回到这里，不回到接口原序。
const DEFAULT_SORT: SortState = { key: "trend_grade", dir: "desc" };

function cycleSort(prev: SortState, key: keyof BoardRow): SortState {
  if (prev.key !== key) return { key, dir: "desc" };
  if (prev.dir === "desc") return { key, dir: "asc" };
  return DEFAULT_SORT;
}

function parseNumber(raw?: string): number | null {
  if (raw == null || raw === "" || raw === "—") return null;
  const n = Number(String(raw).replace(/%/g, "").replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

function ordinalRank(key: keyof BoardRow, raw?: string): number | null {
  const table = ORDINAL[key];
  if (!table || raw == null || raw === "" || raw === "—") return null;
  if (raw in table) return table[raw];
  const upper = raw.toUpperCase();
  return upper in table ? table[upper] : null;
}

function gradeClass(g?: string): string {
  if (g === "A+") return "g-aplus";
  if (g === "A") return "g-a";
  if (g === "B") return "g-b";
  if (g === "C") return "g-c";
  return "";
}

function cellText(r: BoardRow, key: keyof BoardRow): string {
  if (key === "stock") {
    return r.stock || (r.name && r.code ? `${r.name}(${r.code})` : "—");
  }
  const raw = r[key];
  if (raw == null || raw === "") return "—";
  return zhCell(key, String(raw));
}

function isMissing(row: BoardRow, key: keyof BoardRow): boolean {
  if (NUMERIC_KEYS.has(key)) return parseNumber(row[key]) == null;
  if (ORDINAL[key]) {
    const raw = row[key];
    return raw == null || raw === "" || raw === "—";
  }
  const text = cellText(row, key);
  return !text || text === "—";
}

function comparePresent(a: BoardRow, b: BoardRow, key: keyof BoardRow): number {
  if (NUMERIC_KEYS.has(key)) {
    return (parseNumber(a[key]) ?? 0) - (parseNumber(b[key]) ?? 0);
  }
  const ar = ordinalRank(key, a[key]);
  const br = ordinalRank(key, b[key]);
  if (ar != null && br != null) return ar - br;
  return cellText(a, key).localeCompare(cellText(b, key), "zh-CN");
}

function sortRows(rows: BoardRow[], sort: SortState): BoardRow[] {
  const indexed = rows.map((row, index) => ({ row, index }));
  indexed.sort((a, b) => {
    const am = isMissing(a.row, sort.key);
    const bm = isMissing(b.row, sort.key);
    // 缺值固定沉底，避免升降序把「—」翻到表头。
    if (am || bm) {
      if (am && bm) return a.index - b.index;
      return am ? 1 : -1;
    }
    const cmp = comparePresent(a.row, b.row, sort.key);
    if (cmp === 0) return a.index - b.index;
    return sort.dir === "asc" ? cmp : -cmp;
  });
  return indexed.map((item) => item.row);
}

export default function BoardTable({
  rows,
  emptyText,
  asOf,
}: {
  rows: BoardRow[];
  emptyText: string;
  asOf: string;
}) {
  const [open, setOpen] = useState<{ row: BoardRow; anchor: HTMLElement } | null>(null);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const openTimer = useRef<number>();
  const closeTimer = useRef<number>();
  const viewRows = sortRows(rows, sort);

  function cancelClose() {
    window.clearTimeout(closeTimer.current);
  }
  function armClose() {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(null), 280);
  }
  function schedule(row: BoardRow, anchor: HTMLElement) {
    cancelClose();
    window.clearTimeout(openTimer.current);
    openTimer.current = window.setTimeout(() => setOpen({ row, anchor }), 180);
  }

  if (!rows.length) {
    return <p className="empty">{emptyText}</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {COL_KEYS.map((c) => {
              const active = sort?.key === c;
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
                    {zhColumnLabel(c)}
                    <span className="th-sort-mark" aria-hidden="true">
                      {active ? (sort.dir === "desc" ? "↓" : "↑") : ""}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {viewRows.map((r, i) => (
            <tr key={`${r.code || r.stock}-${i}`}>
              {COL_KEYS.map((c) => (
                <td
                  key={c}
                  className={c === "trend_grade" ? gradeClass(r.trend_grade) : ""}
                >
                  {c === "stock" && r.code ? (
                    <button
                      type="button"
                      className="code-tip"
                      onMouseEnter={(e) => {
                        if (window.matchMedia("(hover: hover)").matches) schedule(r, e.currentTarget);
                      }}
                      onMouseLeave={() => {
                        window.clearTimeout(openTimer.current);
                        armClose();
                      }}
                      onFocus={(e) => schedule(r, e.currentTarget)}
                      onBlur={armClose}
                      onClick={(e) => {
                        window.clearTimeout(openTimer.current);
                        setOpen({ row: r, anchor: e.currentTarget });
                      }}
                    >
                      {cellText(r, c)}
                    </button>
                  ) : (
                    cellText(r, c)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {open ? (
        <StockHoverCard
          row={open.row}
          asOf={asOf}
          anchor={open.anchor}
          onClose={() => setOpen(null)}
          onPointerEnter={cancelClose}
          onPointerLeave={armClose}
        />
      ) : null}
    </div>
  );
}
