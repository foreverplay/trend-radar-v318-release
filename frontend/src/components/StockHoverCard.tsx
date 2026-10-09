import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fetchFlow, fetchKlines, fetchRiseReason, fetchTrends } from "../api";
import type { BoardRow, FlowPayload, KlineBar, ReasonPayload } from "../types";
import { zhCell } from "../zhLabels";
import { IntradayChart, KlineChart } from "./QuoteCharts";

type Box = { left: number; top: number; width: number };

function place(anchor: HTMLElement): Box {
  const rect = anchor.getBoundingClientRect();
  const width = Math.min(980, window.innerWidth - 16);
  let left = Math.min(rect.left, window.innerWidth - width - 8);
  left = Math.max(8, left);
  let top = rect.bottom + 8;
  if (top + 560 > window.innerHeight) top = Math.max(8, rect.top - 560);
  return { left, top, width };
}

function scoreWidth(raw?: string): string {
  const n = Number(raw);
  if (!Number.isFinite(n)) return "0%";
  return `${Math.max(0, Math.min(100, n))}%`;
}

function evidence(row: BoardRow): string[] {
  return [
    `阶段 ${zhCell("stage", row.stage)}，成熟度 ${zhCell("maturity", row.maturity)}。`,
    `趋势等级 ${row.trend_grade || "—"}，入场状态 ${zhCell("entry_status", row.entry_status)}，路径 ${zhCell("path", row.path)}。`,
    `趋势涨幅 ${row.trend_gain_pct || "—"}%，保护价 ${row.protect || "—"}，破坏价 ${row.break_price || "—"}。`,
    `退出信号 ${zhCell("exit_signal", row.exit_signal)}。等级高只说明图形质量，不等于适合当天参与。`,
  ];
}

export default function StockHoverCard({
  row,
  asOf,
  anchor,
  onClose,
  onPointerEnter,
  onPointerLeave,
}: {
  row: BoardRow;
  asOf: string;
  anchor: HTMLElement;
  onClose: () => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
}) {
  const code = row.code || "";
  const [box, setBox] = useState<Box>(() => place(anchor));
  const [period, setPeriod] = useState<"day" | "month">("day");
  const [trend, setTrend] = useState<Awaited<ReturnType<typeof fetchTrends>> | null>(null);
  const [bars, setBars] = useState<KlineBar[]>([]);
  const [flow, setFlow] = useState<FlowPayload | null>(null);
  const [reason, setReason] = useState<ReasonPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const cardRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const update = () => setBox(place(anchor));
    update();
    const scroller = anchor.closest(".table-wrap");
    window.addEventListener("resize", update);
    scroller?.addEventListener("scroll", update);
    return () => {
      window.removeEventListener("resize", update);
      scroller?.removeEventListener("scroll", update);
    };
  }, [anchor]);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setTrend(null);
    setBars([]);
    Promise.allSettled([
      fetchTrends(code, asOf, ctrl.signal),
      fetchKlines(code, period, asOf, ctrl.signal),
      fetchFlow(code, asOf, ctrl.signal),
      fetchRiseReason(code, row.name || "", asOf, ctrl.signal),
    ]).then((items) => {
      if (ctrl.signal.aborted) return;
      if (items[0].status === "fulfilled") setTrend(items[0].value);
      if (items[1].status === "fulfilled") setBars(items[1].value.bars || []);
      if (items[2].status === "fulfilled") setFlow(items[2].value);
      if (items[3].status === "fulfilled") setReason(items[3].value);
      setLoading(false);
    });
    return () => ctrl.abort();
  }, [code, asOf, period, row.name]);

  return createPortal(
    <aside
      ref={cardRef}
      className="hover-card"
      style={{ left: box.left, top: box.top, width: box.width }}
      onMouseEnter={onPointerEnter}
      onMouseLeave={onPointerLeave}
      role="dialog"
      aria-label={`${row.name || code} 详情`}
    >
      <header className="hover-head">
        <div>
          <strong>{row.name || row.stock} {code}</strong>
          <p>观察日 {asOf} · 趋势等级 {row.trend_grade || "—"} · {zhCell("entry_status", row.entry_status)}</p>
        </div>
        <button type="button" onClick={onClose}>关闭</button>
      </header>
      <div className="hover-charts">
        <section>
          <p className="kicker">日内分时</p>
          {loading && !trend ? <p className="chart-empty">正在读取分时…</p> : <IntradayChart points={trend?.points || []} preClose={trend?.preClose || 0} />}
        </section>
        <section>
          <div className="kicker-row">
            <p className="kicker">{period === "day" ? "日K" : "月K"}</p>
            <div>
              <button type="button" className={period === "day" ? "on" : ""} onClick={() => setPeriod("day")}>日K</button>
              <button type="button" className={period === "month" ? "on" : ""} onClick={() => setPeriod("month")}>月K</button>
            </div>
          </div>
          {loading && !bars.length ? <p className="chart-empty">正在读取K线…</p> : <KlineChart bars={bars} period={period} />}
        </section>
      </div>
      <div className="score-list">
        {[
          ["技术分", row.tech],
          ["延续分", row.cont],
          ["板块分", row.sector],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label} {value || "—"}</span>
            <i><b style={{ width: scoreWidth(value) }} /></i>
          </div>
        ))}
      </div>
      <ul className="evidence">
        {evidence(row).map((line) => <li key={line}>{line}</li>)}
      </ul>
      {reason?.available && reason.reason ? <p className="extra">异动说明：{reason.reason}</p> : null}
      {flow?.available ? (
        <p className="extra">
          资金结构：大单占比 {Number(flow.largeSharePct || 0).toFixed(1)}%，主力净流入 {Number(flow.mainNetPct || 0).toFixed(2)}%
          {flow.date ? ` · ${flow.date}` : ""}
        </p>
      ) : null}
    </aside>,
    document.body,
  );
}
