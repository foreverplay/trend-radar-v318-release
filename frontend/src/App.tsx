import { useEffect, useRef, useState } from "react";
import {
  fetchBoardsByDate,
  fetchDividendBoardsByDate,
  fetchLatestBoards,
  fetchLatestDividendBoards,
  fetchMeta,
} from "./api";
import BoardTable from "./components/BoardTable";
import DividendBoardTable from "./components/DividendBoardTable";
import ResearchPanel from "./research/ResearchPanel";
import type { BoardsPayload, DividendBoardsPayload } from "./types";
import {
  BANNER_TEXT,
  DIVIDEND_BANNER_TEXT,
  zhRegime,
  zhSource,
} from "./zhLabels";

type ModeKey = "growth" | "dividend" | "research";
type TabKey = "mature_entry" | "trend_all" | "watch_pool" | "extended";
type DividendTabKey = "quality" | "opportunity" | "risk_watch";

const TABS: { key: TabKey; label: string }[] = [
  { key: "mature_entry", label: "成熟入场榜" },
  { key: "trend_all", label: "趋势全榜" },
  { key: "watch_pool", label: "观察池" },
  { key: "extended", label: "过热延伸" },
];

const DIVIDEND_TABS: { key: DividendTabKey; label: string }[] = [
  { key: "quality", label: "优质红利" },
  { key: "opportunity", label: "当前机会" },
  { key: "risk_watch", label: "风险观察" },
];

const BOARD_POLL_MS = 90_000;

function shanghaiNow(now = new Date()): { day: string; minutes: number; weekday: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hourCycle: "h23",
  }).formatToParts(now);
  const pick = (type: string) => parts.find((item) => item.type === type)?.value || "";
  const week: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return {
    day: `${pick("year")}-${pick("month")}-${pick("day")}`,
    minutes: Number(pick("hour")) * 60 + Number(pick("minute")),
    weekday: week[pick("weekday")] ?? 0,
  };
}

// 榜单文件要等整轮排行导出才换成当天。跟随最新榜时，走势从开盘起改要当天行情。
function liveQuoteDay(boardDay: string): string {
  if (!boardDay) return boardDay;
  const now = shanghaiNow();
  if (now.weekday === 0 || now.weekday === 6 || now.minutes < 9 * 60 + 15) return boardDay;
  return now.day > boardDay ? now.day : boardDay;
}

export default function App() {
  const [mode, setMode] = useState<ModeKey>("growth");
  const [data, setData] = useState<BoardsPayload | null>(null);
  const [divData, setDivData] = useState<DividendBoardsPayload | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [divDates, setDivDates] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [divSelected, setDivSelected] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabKey>("trend_all");
  const [divTab, setDivTab] = useState<DividendTabKey>("quality");
  const [source, setSource] = useState<string>("");
  const seq = useRef(0);
  const followLatest = useRef(true);
  const divFollowLatest = useRef(true);
  const dataRef = useRef<BoardsPayload | null>(null);
  const datesRef = useRef<string[]>([]);
  const divDatesRef = useRef<string[]>([]);
  const modeRef = useRef<ModeKey>("growth");
  datesRef.current = dates;
  divDatesRef.current = divDates;
  modeRef.current = mode;

  useEffect(() => {
    fetchMeta()
      .then((meta) => {
        setDates(meta.dates || []);
        setDivDates(meta.dividend_dates || []);
      })
      .catch(() => {
        setDates([]);
        setDivDates([]);
      });
  }, []);

  function loadGrowth(date?: string) {
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

  function loadDividend(date?: string) {
    const id = ++seq.current;
    setLoading(true);
    setErr(null);
    const request = date ? fetchDividendBoardsByDate(date) : fetchLatestDividendBoards();
    request
      .then((d) => {
        if (id !== seq.current) return;
        setDivData(d);
        setSource(d.source || "api");
        if (d.t_date) setDivSelected(d.t_date);
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
    loadGrowth();
  }, []);

  useEffect(() => {
    if (mode === "dividend" && !divData) {
      loadDividend(divSelected || undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  // 轻量跟最新榜：约 90s；切到历史日期暂停；页签隐藏暂停；失败退避
  useEffect(() => {
    let timer: number | undefined;
    let failStreak = 0;

    const softRefreshLatest = () => {
      if (document.hidden) return;
      if (modeRef.current === "growth") {
        if (!followLatest.current) return;
        const id = ++seq.current;
        fetchLatestBoards()
          .then((d) => {
            if (id !== seq.current) return;
            failStreak = 0;
            setData(d);
            setSource(d.source || "api");
            if (d.t_date) setSelected(d.t_date);
            setErr(null);
            fetchMeta()
              .then((meta) => {
                setDates(meta.dates || []);
                setDivDates(meta.dividend_dates || []);
              })
              .catch(() => undefined);
          })
          .catch(() => {
            failStreak += 1;
          });
        return;
      }
      if (!divFollowLatest.current) return;
      const id = ++seq.current;
      fetchLatestDividendBoards()
        .then((d) => {
          if (id !== seq.current) return;
          failStreak = 0;
          setDivData(d);
          setSource(d.source || "api");
          if (d.t_date) setDivSelected(d.t_date);
          setErr(null);
          fetchMeta()
            .then((meta) => {
              setDates(meta.dates || []);
              setDivDates(meta.dividend_dates || []);
            })
            .catch(() => undefined);
        })
        .catch(() => {
          failStreak += 1;
        });
    };

    const schedule = () => {
      window.clearTimeout(timer);
      const refreshing = dataRef.current?.refresh?.state === "running";
      const delay =
        failStreak > 0
          ? Math.min(BOARD_POLL_MS * (1 + failStreak), 300_000)
          : refreshing
            ? 10_000
            : BOARD_POLL_MS;
      timer = window.setTimeout(() => {
        softRefreshLatest();
        schedule();
      }, delay);
    };

    const onVis = () => {
      if (!document.hidden) {
        softRefreshLatest();
        schedule();
      }
    };

    document.addEventListener("visibilitychange", onVis);
    schedule();
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  const counts = data?.counts || {};
  const boardCounts = {
    mature_entry: data?.boards.mature_entry.length ?? 0,
    trend_all: (counts as { trend_all?: number }).trend_all ?? data?.boards.trend_all.length ?? 0,
    watch_pool: data?.boards.watch_pool.length ?? 0,
    extended: data?.boards.extended.length ?? 0,
  };
  const divCounts = {
    quality: divData?.boards.quality?.rows?.length ?? divData?.counts?.quality ?? 0,
    opportunity: divData?.boards.opportunity?.rows?.length ?? divData?.counts?.opportunity ?? 0,
    risk_watch: divData?.boards.risk_watch?.rows?.length ?? divData?.counts?.risk_watch ?? 0,
  };
  const legacyNote =
    data?.legacy_regime && data.legacy_regime !== data?.regime
      ? `（兼容标签：${zhRegime(data.legacy_regime)}）`
      : "";
  const dateOptions = dates.length ? dates : selected ? [selected] : [];
  const divDateOptions = divDates.length ? divDates : divSelected ? [divSelected] : [];
  const newest = dates[0] || data?.t_date || "";
  const following = !selected || selected === newest;
  const chartAsOf = following ? liveQuoteDay(data?.t_date || selected) : selected || data?.t_date || "";
  dataRef.current = data;
  const quoteNote =
    chartAsOf && data?.t_date && chartAsOf !== data.t_date
      ? data?.refresh?.message && (data.refresh.state === "running" || data.refresh.state === "failed")
        ? ` · ${data.refresh.message}`
        : ` · 走势 ${chartAsOf}（榜单仍是 ${data.t_date}，本轮排行导出后替换）`
      : "";
  const refreshNote =
    data?.refresh?.state === "running" && data.refresh.day && data.refresh.day !== data.t_date
      ? data.refresh.message || `正在重算 ${data.refresh.day}`
      : data?.refresh?.state === "failed"
        ? data.refresh.message || "最新交易日重算失败"
        : "";

  const divNewest = divDates[0] || divData?.t_date || "";
  const divFollowing = !divSelected || divSelected === divNewest;
  const divChartAsOf = divFollowing
    ? liveQuoteDay(divData?.t_date || divSelected || "")
    : divSelected || divData?.t_date || "";
  const divQuoteNote =
    divChartAsOf && divData?.t_date && divChartAsOf !== divData.t_date
      ? ` · 走势 ${divChartAsOf}（榜单仍是 ${divData.t_date}，本轮排行导出后替换）`
      : "";
  const growthActive = mode === "growth";
  const dividendActive = mode === "dividend";
  const researchActive = mode === "research";
  const headerTitle = researchActive ? "研究实验室" : growthActive ? "趋势分析雷达 V3.1.8" : "红利趋势榜 V3.1.9";
  const headerSub = growthActive ? (
    <>
      交易日 {data?.t_date || "—"} · 市场状态 {zhRegime(data?.regime)}
      {legacyNote} · 市场评分 {data?.market_score ?? "—"}
      {quoteNote}
    </>
  ) : researchActive ? (
    <>只读显示独立研究目录里的运行产物。当前这个已经打开的页面如果是旧进程，刷新不会出现研究功能。</>
  ) : (
    <>
      交易日 {divData?.t_date || divData?.as_of || "—"} · 评分样本 {divData?.n_scored ?? "—"} ·
      陷阱 {divData?.n_trap ?? "—"}
      {divQuoteNote}
    </>
  );

  return (
    <div className="app">
      <header className="hdr">
        <div>
          <h1>{headerTitle}</h1>
          <p className="sub">{headerSub}</p>
        </div>
        <div className="hdr-tools">
          {researchActive ? null : growthActive ? (
            <label className="date-pick">
              历史日期
              <select
                value={selected}
                disabled={!dateOptions.length}
                onChange={(e) => {
                  const day = e.target.value;
                  const newestDay = datesRef.current[0] || dateOptions[0];
                  followLatest.current = Boolean(newestDay) && day === newestDay;
                  setSelected(day);
                  loadGrowth(day);
                }}
              >
                {dateOptions.map((day) => (
                  <option key={day} value={day}>{day}</option>
                ))}
              </select>
            </label>
          ) : (
            <label className="date-pick">
              历史日期
              <select
                value={divSelected}
                disabled={!divDateOptions.length}
                onChange={(e) => {
                  const day = e.target.value;
                  const newestDay = divDatesRef.current[0] || divDateOptions[0];
                  divFollowLatest.current = Boolean(newestDay) && day === newestDay;
                  setDivSelected(day);
                  loadDividend(day);
                }}
              >
                {divDateOptions.map((day) => (
                  <option key={day} value={day}>{day}</option>
                ))}
              </select>
            </label>
          )}
          <div className="counts">
            {researchActive ? null : growthActive ? (
              <>
                <span>成熟入场 {boardCounts.mature_entry}</span>
                <span>趋势全榜 {boardCounts.trend_all}</span>
                <span>观察池 {boardCounts.watch_pool}</span>
                <span>过热延伸 {boardCounts.extended}</span>
              </>
            ) : (
              <>
                <span>优质红利 {divCounts.quality}</span>
                <span>当前机会 {divCounts.opportunity}</span>
                <span>风险观察 {divCounts.risk_watch}</span>
              </>
            )}
          </div>
        </div>
      </header>

      <nav className="mode-nav" aria-label="模块切换">
        <button
          type="button"
          className={growthActive ? "active" : ""}
          onClick={() => setMode("growth")}
        >
          趋势观察
        </button>
        <button
          type="button"
          className={dividendActive ? "active" : ""}
          onClick={() => setMode("dividend")}
        >
          红利趋势
        </button>
        <button
          type="button"
          className={researchActive ? "active" : ""}
          onClick={() => setMode("research")}
        >
          研究实验室
        </button>
      </nav>

      <div className="banner">
        {researchActive
          ? "研究页不改榜单计算。没有结果文件就不显示收益。旧静态样例页里的数字不是这次验证。"
          : growthActive
            ? BANNER_TEXT
            : DIVIDEND_BANNER_TEXT}{" "}
        {researchActive ? "" : `数据来源：${zhSource(source || "…")}`}
        {growthActive && data?.asof_note ? ` ${data.asof_note}` : ""}
        {growthActive && refreshNote ? ` ${refreshNote}` : ""}
        {dividendActive && divData?.disclaimer ? ` ${divData.disclaimer}` : ""}
      </div>

      {err && <p className="error">{err}</p>}
      {loading && ((growthActive && !data) || (dividendActive && !divData)) && !err && (
        <p className="loading">加载中…</p>
      )}
      {loading && ((growthActive && data) || (dividendActive && divData)) ? (
        <p className="loading">正在切换日期…</p>
      ) : null}

      {growthActive && data && (
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
            asOf={chartAsOf || data.t_date || selected}
            emptyText={
              tab === "mature_entry"
                ? "今日无满足成熟入场榜的标的。"
                : "本榜暂无展示行。"
            }
          />
        </>
      )}

      {researchActive && <ResearchPanel />}

      {dividendActive && divData && (
        <>
          <nav className="tabs">
            {DIVIDEND_TABS.map((t) => (
              <button
                key={t.key}
                className={divTab === t.key ? "active" : ""}
                onClick={() => setDivTab(t.key)}
              >
                {divData.boards[t.key]?.title || t.label}（{divData.boards[t.key]?.rows?.length ?? 0}）
              </button>
            ))}
          </nav>
          <DividendBoardTable
            rows={divData.boards[divTab]?.rows || []}
            asOf={divChartAsOf || divData.t_date || divData.as_of || divSelected}
            emptyText="本区暂无展示行。"
          />
        </>
      )}

      <footer className="ftr">
        V3.1.8 趋势观察 + 红利模块 V3.1.9 细分 · 仅供观察与研究 · 禁止荐买 / 预测必涨 · 非投资建议
      </footer>
    </div>
  );
}
