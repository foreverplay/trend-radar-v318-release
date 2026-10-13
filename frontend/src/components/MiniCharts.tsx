import { useEffect, useMemo, useRef, useState } from "react";
import { fetchKlines, fetchTrends } from "../api";
import type { IntradayPoint, KlineBar } from "../types";
import { clockToMinute, extendIntradayTimes } from "../../../../market-data-hub/web/session.ts";

const UP = "#ff5a6a";
const DOWN = "#22c55e";

let inflight = 0;
const waiters: Array<() => void> = [];

function acquireSlot() {
  if (inflight < 3) {
    inflight += 1;
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => {
    waiters.push(resolve);
  });
}

function releaseSlot() {
  const next = waiters.shift();
  if (next) {
    next();
    return;
  }
  inflight = Math.max(0, inflight - 1);
}

function pointIndex(times: string[], clock: string): number {
  const exact = times.indexOf(clock);
  if (exact >= 0) return exact;
  const target = clockToMinute(clock);
  if (target == null) return 0;
  let best = 0;
  let bestGap = Number.POSITIVE_INFINITY;
  times.forEach((item, index) => {
    const minute = clockToMinute(item);
    if (minute == null) return;
    const gap = Math.abs(minute - target);
    if (gap < bestGap) {
      best = index;
      bestGap = gap;
    }
  });
  return best;
}

function barDate(value: string) {
  const match = value.match(/(\d{4}-)?(\d{2}-\d{2})/);
  return match?.[2] || value;
}

function useCanvas(draw: (ctx: CanvasRenderingContext2D, width: number, height: number) => void, key: unknown) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const paint = () => {
      const width = canvas.clientWidth || wrap.clientWidth || 180;
      const height = canvas.clientHeight || 88;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      drawRef.current(ctx, width, height);
    };
    paint();
    const observer = new ResizeObserver(paint);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [key]);
  return { wrapRef, canvasRef };
}

function TrendSpark({ points }: { points: IntradayPoint[] }) {
  const { wrapRef, canvasRef } = useCanvas((ctx, width, height) => {
    const pad = { top: 6, right: 4, bottom: 6, left: 4 };
    const innerW = Math.max(1, width - pad.left - pad.right);
    const innerH = Math.max(1, height - pad.top - pad.bottom);
    const times = extendIntradayTimes(points.map((item) => item.time));
    const spanX = Math.max(1, times.length - 1);
    const xAt = (index: number) => pad.left + (index / spanX) * innerW;
    const prices = points.map((item) => item.price);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const span = max - min || 1;
    const last = points[points.length - 1];
    const change = last?.change_pct;
    ctx.beginPath();
    points.forEach((item, index) => {
      const x = xAt(pointIndex(times, item.time));
      const y = pad.top + innerH - ((item.price - min) / span) * innerH;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = change == null || change >= 0 ? UP : DOWN;
    ctx.lineWidth = 1.4;
    ctx.lineJoin = "round";
    ctx.stroke();
  }, points);

  return (
    <div className="board-spark-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="board-spark" />
      <div className="board-spark-axis" aria-hidden="true">
        <span>09:30</span>
        <span>11:30/13:00</span>
        <span>15:00</span>
      </div>
    </div>
  );
}

function MiniCandles({ bars }: { bars: KlineBar[] }) {
  const windowBars = useMemo(() => bars.slice(-48), [bars]);
  const { wrapRef, canvasRef } = useCanvas((ctx, width, height) => {
    const pad = { top: 6, right: 4, bottom: 6, left: 4 };
    const innerW = Math.max(1, width - pad.left - pad.right);
    const innerH = Math.max(1, height - pad.top - pad.bottom);
    const min = Math.min(...windowBars.map((item) => item.low));
    const max = Math.max(...windowBars.map((item) => item.high));
    const span = max - min || 1;
    const slot = innerW / windowBars.length;
    const bodyW = Math.max(1.2, Math.min(7, slot * 0.62));
    const yAt = (price: number) => pad.top + innerH - ((price - min) / span) * innerH;
    windowBars.forEach((item, index) => {
      const x = pad.left + (index + 0.5) * slot;
      const color = item.close >= item.open ? UP : DOWN;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yAt(item.high));
      ctx.lineTo(x, yAt(item.low));
      ctx.stroke();
      const top = Math.min(yAt(item.open), yAt(item.close));
      const bodyH = Math.max(1, Math.abs(yAt(item.close) - yAt(item.open)));
      ctx.fillRect(x - bodyW / 2, top, bodyW, bodyH);
    });
  }, windowBars);
  const first = windowBars[0];
  const mid = windowBars[Math.floor((windowBars.length - 1) / 2)];
  const last = windowBars[windowBars.length - 1];
  if (!first || !mid || !last) return <Slot text="暂无日K" />;
  return (
    <div className="board-spark-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="board-spark" />
      <div className="board-spark-axis" aria-hidden="true">
        <span>{barDate(first.time)}</span>
        <span>{barDate(mid.time)}</span>
        <span>{barDate(last.time)}</span>
      </div>
    </div>
  );
}

function Slot({ text }: { text?: string }) {
  return (
    <div className="board-spark-wrap">
      <div className="board-spark board-spark-slot">{text || ""}</div>
      <div className="board-spark-axis" aria-hidden="true">
        <span>&nbsp;</span>
      </div>
    </div>
  );
}

export function RowMiniCharts({
  code,
  asOf,
  live,
}: {
  code: string;
  asOf: string;
  live: boolean;
}) {
  const [points, setPoints] = useState<IntradayPoint[] | null>(null);
  const [bars, setBars] = useState<KlineBar[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setPoints(null);
    setBars(null);
  }, [code, asOf]);

  useEffect(() => {
    if (!live || !code) return;
    let cancelled = false;
    let held = false;
    const ctrl = new AbortController();
    setLoading(true);
    (async () => {
      try {
        await acquireSlot();
        held = true;
        if (cancelled) return;
        const [trend, kline] = await Promise.allSettled([
          fetchTrends(code, asOf, ctrl.signal),
          fetchKlines(code, "day", asOf, ctrl.signal),
        ]);
        if (cancelled) return;
        setPoints(trend.status === "fulfilled" ? trend.value.points || [] : []);
        setBars(kline.status === "fulfilled" ? kline.value.bars || [] : []);
      } finally {
        if (held) releaseSlot();
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [live, code, asOf]);

  const trendNode = points?.length
    ? <TrendSpark points={points} />
    : <Slot text={points ? "暂无分时" : live && loading ? "正在读取分时…" : ""} />;
  const klineNode = bars?.length
    ? <MiniCandles bars={bars} />
    : <Slot text={bars ? "暂无日K" : live && loading ? "正在读取日K…" : ""} />;

  return (
    <div className="board-mini-charts">
      <div>
        <p className="kicker">分时</p>
        {trendNode}
      </div>
      <div>
        <p className="kicker">日K</p>
        {klineNode}
      </div>
    </div>
  );
}
