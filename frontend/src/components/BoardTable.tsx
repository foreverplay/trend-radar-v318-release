import type { BoardRow } from "../types";

const COLS: { key: keyof BoardRow; label: string }[] = [
  { key: "stock", label: "股票" },
  { key: "stage", label: "阶段" },
  { key: "trend_grade", label: "TrendGrade" },
  { key: "maturity", label: "成熟度" },
  { key: "entry_status", label: "EntryStatus" },
  { key: "path", label: "路径" },
  { key: "trend_gain_pct", label: "趋势涨幅%" },
  { key: "tech", label: "Tech" },
  { key: "cont", label: "Cont" },
  { key: "sector", label: "Sector" },
  { key: "protect", label: "保护价" },
  { key: "break_price", label: "破坏价" },
  { key: "exit_signal", label: "ExitSignal" },
];

function gradeClass(g?: string): string {
  if (g === "A+") return "g-aplus";
  if (g === "A") return "g-a";
  if (g === "B") return "g-b";
  if (g === "C") return "g-c";
  return "";
}

export default function BoardTable({
  rows,
  emptyText,
}: {
  rows: BoardRow[];
  emptyText: string;
}) {
  if (!rows.length) {
    return <p className="empty">{emptyText}</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {COLS.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.code || r.stock}-${i}`}>
              {COLS.map((c) => (
                <td
                  key={c.key}
                  className={c.key === "trend_grade" ? gradeClass(r.trend_grade) : ""}
                >
                  {c.key === "stock"
                    ? r.stock || (r.name && r.code ? `${r.name}(${r.code})` : "—")
                    : r[c.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
