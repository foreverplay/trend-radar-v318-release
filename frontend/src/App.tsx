import { useEffect, useState } from "react";
import { fetchLatestBoards } from "./api";
import BoardTable from "./components/BoardTable";
import type { BoardsPayload } from "./types";

type TabKey = "mature_entry" | "trend_all" | "watch_pool" | "extended";

const TABS: { key: TabKey; label: string }[] = [
  { key: "mature_entry", label: "成熟入场榜" },
  { key: "trend_all", label: "趋势全榜" },
  { key: "watch_pool", label: "观察池" },
  { key: "extended", label: "过热延伸" },
];

export default function App() {
  const [data, setData] = useState<BoardsPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("trend_all");
  const [source, setSource] = useState<string>("");

  useEffect(() => {
    fetchLatestBoards()
      .then((d) => {
        setData(d);
        setSource(d.source || "api/sample");
        if (d.boards.mature_entry?.length) setTab("mature_entry");
        else setTab("trend_all");
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const counts = data?.counts || {};
  const boardCounts = {
    mature_entry: data?.boards.mature_entry.length ?? 0,
    trend_all: (counts as any).trend_all ?? data?.boards.trend_all.length ?? 0,
    watch_pool: data?.boards.watch_pool.length ?? 0,
    extended: data?.boards.extended.length ?? 0,
  };

  return (
    <div className="app">
      <header className="hdr">
        <div>
          <h1>趋势分析雷达 V3.1.8</h1>
          <p className="sub">
            {data?.t_date || "—"} · regime={data?.regime || "—"}
            {data?.legacy_regime ? `（legacy ${data.legacy_regime}）` : ""} ·
            MarketScore={data?.market_score ?? "—"}
          </p>
        </div>
        <div className="counts">
          <span>成熟入场 {boardCounts.mature_entry}</span>
          <span>趋势全榜 {boardCounts.trend_all}</span>
          <span>观察池 {boardCounts.watch_pool}</span>
          <span>过热延伸 {boardCounts.extended}</span>
        </div>
      </header>

      <div className="banner">
        观察池；A+≠今天必买；PRIME/CONFIRMED 才是成熟入场窗口。禁止荐买 / 预测必涨。
        TrendGrade ⊥ EntryStatus（双轨）。数据源：{source || "…"}
      </div>

      {err && <p className="error">{err}</p>}
      {!data && !err && <p className="loading">加载中…</p>}

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
