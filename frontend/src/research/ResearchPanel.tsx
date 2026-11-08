import { useEffect, useState } from "react";
import { cellText, evidenceText, showReturn, type ResearchRun } from "./model";

export const RESEARCH_API_BASE = import.meta.env.VITE_RESEARCH_API_BASE || "http://127.0.0.1:8791";

type StatusPayload = {
  precheck_state: string;
  precheck_message: string;
  precheck: Record<string, unknown> | null;
  run_count: number;
  commands: string[];
};

type TablePayload = {
  present: boolean;
  rows: Record<string, unknown>[];
  total: number | null;
  message?: string | null;
  source?: string;
};

async function readJson(path: string): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(`${RESEARCH_API_BASE}${path}`, { cache: "no-store" });
  } catch {
    throw new Error(
      `没有连上 ${RESEARCH_API_BASE}。已经在听的 8765/5173 不会因为刷新多出研究功能。健康检查是 /api/health，不是 /health。`,
    );
  }
  const body = (await res.json().catch(() => null)) as { detail?: string } | null;
  if (!res.ok) throw new Error(body?.detail || `研究接口返回 ${res.status}`);
  return body;
}

function TableBlock({ title, data }: { title: string; data: TablePayload | null }) {
  if (!data) return <p className="loading">正在读取{title}…</p>;
  if (!data.present) return <p className="empty">{data.message || `没有${title}文件，不补 0。`}</p>;
  if (!data.rows.length) return <p className="empty">{data.message || `${title}文件里没有行。`}</p>;
  const columns = Object.keys(data.rows[0]);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {data.rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => <td key={column}>{cellText(row[column])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ResearchPanel() {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<ResearchRun | null>(null);
  const [section, setSection] = useState<"grades" | "orders" | "trades" | "positions" | "equity">("orders");
  const [table, setTable] = useState<TablePayload | null>(null);
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [metricsNote, setMetricsNote] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [gradeDate, setGradeDate] = useState("");

  useEffect(() => {
    Promise.all([
      readJson("/api/research/status") as Promise<StatusPayload>,
      readJson("/api/research/runs") as Promise<{ runs: ResearchRun[]; message?: string | null }>,
    ])
      .then(([nextStatus, nextRuns]) => {
        setStatus(nextStatus);
        setRuns(nextRuns.runs || []);
        setEmptyMessage(nextRuns.message || null);
        setErr(null);
      })
      .catch((error: Error) => setErr(error.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setTable(null);
    setMetrics(null);
    readJson(`/api/research/runs/${encodeURIComponent(selected)}`)
      .then((value) => setDetail(value as ResearchRun))
      .catch((error: Error) => setErr(error.message));
    readJson(`/api/research/runs/${encodeURIComponent(selected)}/metrics`)
      .then((value) => {
        const body = value as { present: boolean; metrics: Record<string, unknown> | null; message?: string };
        setMetrics(body.present ? body.metrics : null);
        setMetricsNote(body.message || "");
      })
      .catch((error: Error) => setErr(error.message));
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    const query = section === "grades" && gradeDate ? `?date=${encodeURIComponent(gradeDate)}` : "";
    setTable(null);
    readJson(`/api/research/runs/${encodeURIComponent(selected)}/${section}${query}`)
      .then((value) => setTable(value as TablePayload))
      .catch((error: Error) => setErr(error.message));
  }, [selected, section, gradeDate]);

  const current = runs.find((run) => run.run_id === selected) || detail;
  const precheck = status?.precheck as {
    tables?: Record<string, { present?: boolean; rows?: number | null; symbols?: number | null; max_date?: string | null; price_basis?: string; detail?: string }>;
    execution_block?: string | null;
    bfq_daily?: { present?: boolean; file_count?: number; source?: string; adjust?: string; generated_at?: string; detail?: string; accepted_as_execution_prices?: boolean };
    kline_sample?: { present?: boolean; rows?: number | null; detail?: string };
    parquet_daily_files?: number | null;
  } | null;

  return (
    <section className="research-panel">
      <p className="empty">
        研究接口 {RESEARCH_API_BASE}。榜单仍走原来的接口。这里没有运行回测按钮。
      </p>
      {err && <p className="error">{err}</p>}
      {status && (
        <>
          <h2>数据覆盖预检</h2>
          <p className="empty">{status.precheck_message}</p>
          {status.precheck_state !== "present" && (
            <pre className="research-commands">{status.commands.join("\n")}</pre>
          )}
          {precheck?.execution_block && <p className="error">成交阻断：{precheck.execution_block}</p>}
          {precheck?.tables && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>表</th><th>存在</th><th>口径</th><th>行数</th><th>代码数</th><th>最大日期</th><th>说明</th></tr>
                </thead>
                <tbody>
                  {Object.entries(precheck.tables).map(([name, row]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{row.present ? "是" : "否"}</td>
                      <td>{cellText(row.price_basis)}</td>
                      <td>{cellText(row.rows)}</td>
                      <td>{cellText(row.symbols)}</td>
                      <td>{cellText(row.max_date)}</td>
                      <td>{cellText(row.detail)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {precheck?.bfq_daily?.present && (
            <p className="empty">
              bfq_daily {precheck.bfq_daily.file_count ?? "—"} 个文件，source={cellText(precheck.bfq_daily.source)}，adjust={JSON.stringify(precheck.bfq_daily.adjust ?? "")}，时间 {cellText(precheck.bfq_daily.generated_at)}。
              {precheck.bfq_daily.detail} 不作为成交价。
            </p>
          )}
          {precheck?.kline_sample?.present && (
            <p className="empty">
              kline 抽样 {precheck.kline_sample.rows ?? "—"} 行。{precheck.kline_sample.detail} parquet 文件数 {cellText(precheck.parquet_daily_files)}。
            </p>
          )}
        </>
      )}

      <h2>运行历史</h2>
      {!runs.length && <p className="empty">{emptyMessage || "没有运行记录。"}</p>}
      {!!runs.length && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>run_id</th><th>状态</th><th>模式</th><th>代码版本</th><th>说明</th></tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id} className={run.run_id === selected ? "row-open" : ""}>
                  <td><button type="button" className="code-tip" onClick={() => setSelected(run.run_id)}>{run.run_id}</button></td>
                  <td>{cellText(run.status)}</td>
                  <td>{cellText(run.mode || run.result_class)}</td>
                  <td>{cellText(run.code_hash)}</td>
                  <td>{evidenceText(run)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {current && (
        <>
          <h2>{current.run_id}</h2>
          <p className={current.not_strategy_evidence || current.status !== "completed" ? "error" : "empty"}>{evidenceText(current)}</p>
          <p className="empty">
            {showReturn(current, Boolean(metrics))
              ? `期末净值 ${cellText(metrics?.final_equity)}，总收益 ${cellText(metrics?.total_return)}。${metricsNote}`
              : metricsNote || "没有完成的指标文件，不显示收益。"}
          </p>
          <nav className="tabs">
            {(["grades", "orders", "trades", "positions", "equity"] as const).map((item) => (
              <button key={item} type="button" className={section === item ? "active" : ""} onClick={() => setSection(item)}>
                {item === "grades" ? "A/A+ 评级" : item === "orders" ? "订单" : item === "trades" ? "成交" : item === "positions" ? "持仓" : "净值"}
              </button>
            ))}
          </nav>
          {section === "grades" && (
            <label className="date-pick">
              日期
              <input value={gradeDate} placeholder="YYYY-MM-DD" onChange={(event) => setGradeDate(event.target.value)} />
            </label>
          )}
          {table?.source && <p className="empty">{table.message}</p>}
          <TableBlock title={section} data={table} />
        </>
      )}
    </section>
  );
}
