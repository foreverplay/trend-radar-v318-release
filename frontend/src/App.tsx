import { useEffect, useRef, useState } from "react";
import { fetchBoardsByDate, fetchLatestBoards, fetchMeta } from "./api";
import BoardTable from "./components/BoardTable";
import type { BoardsPayload } from "./types";
import { BANNER_TEXT, zhRegime, zhSource } from "./zhLabels";

type TabKey = "mature_entry" | "trend_all" | "watch_pool" | "extended";

const TABS: { key: TabKey; label: string }[] = [
  { key: "mature_entry", label: "成熟入场榜" },
  { key: "trend_all", label: "趋势全榜" },
  { key: "watch_pool", label: "观察池" },
  { key: "extended", label: "过热延伸" },
];

export default function App() {
  const [data, setData] = useState<BoardsPayload | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabKey>("trend_all");
  const [source, setSource] = useState<string>("");
  const seq = useRef(0);

  useEffect(() => {
    fetchMeta()
      .then((meta) => setDates(meta.dates || []))
      .catch(() => setDates([]));
  }, []);

  function load(date?: string) {
    const id = ++seq.current;
    setLoading(true);
    setErr(null);
    const request = date ? fetchBoardsByDate(date) : fetchLatestBoards();
    request
      .then((d) => {
        if (id !== seq.current) return;
        setData(d);
        setSource(d.source || (date ? "api" : "api"));
        if (d.t_date) setSelected(d.t_date);
      })
      .catch((e) => {
        if (id !== seq.current) return;
        setErr(String(e));
      })
      .finally(() => {
        if (id === seq.current) setLoading(false);
      });
  }

  useEffect(() => {
    load();
  }, []);

  const counts = data?.counts || {};
  const boardCounts = {
    mature_entry: data?.boards.mature_entry.length ?? 0,
    trend_all: (counts as { trend_all?: number }).trend_all ?? data?.boards.trend_all.length ?? 0,
    watch_pool: data?.boards.watch_pool.length ?? 0,
    extended: data?.boards.extended.length ?? 0,
  };
  const legacyNote =
    data?.legacy_regime && data.legacy_regime !== data?.regime
      ? `（兼容标签：${zhRegime(data.legacy_regime)}）`
      : "";
  const dateOptions = dates.length ? dates : selected ? [selected] : [];

  return (
    <div className="app">
      <header className="hdr">
        <div>
          <h1>趋势分析雷达 V3.1.8</h1>
          <p className="sub">
            交易日 {data?.t_date || "—"} · 市场状态 {zhRegime(data?.regime)}
            {legacyNote} · 市场评分 {data?.market_score ?? "—"}
          </p>
        </div>
        <div className="hdr-tools">
          <label className="date-pick">
            历史日期
            <select
              value={selected}
              disabled={!dateOptions.length}
              onChange={(e) => {
                setSelected(e.target.value);
                load(e.target.value);
              }}
            >
              {dateOptions.map((day) => (
                <option key={day} value={day}>{day}</option>
              ))}
            </select>
          </label>
          <div className="counts">
            <span>成熟入场 {boardCounts.mature_entry}</span>
            <span>趋势全榜 {boardCounts.trend_all}</span>
            <span>观察池 {boardCounts.watch_pool}</span>
            <span>过热延伸 {boardCounts.extended}</span>
          </div>
        </div>
      </header>

      <div className="banner">
        {BANNER_TEXT} 数据来源：{zhSource(source || "…")}
        {data?.asof_note ? ` ${data.asof_note}` : ""}
      </div>

      {err && <p className="error">{err}</p>}
      {loading && !data && !err && <p className="loading">加载中…</p>}
      {loading && data ? <p className="loading">正在切换日期…</p> : null}

      {data && (
        <>
          <nav className="tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={tab === t.key ? "active" : ""}
                onClick={() => setTab(t.key)}
              >
                {t.label}（{data.boards[t.key].length}）
              </button>
            ))}
          </nav>
          <BoardTable
            rows={data.boards[tab]}
            asOf={data.t_date || selected}
            emptyText={
              tab === "mature_entry"
                ? "今日无满足成熟入场榜的标的。"
                : "本榜暂无展示行。"
            }
          />
        </>
      )}

      <footer className="ftr">
        V3.1.8 · 仅供趋势观察与研究 · 非投资建议
      </footer>
    </div>
  );
}
