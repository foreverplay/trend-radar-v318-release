import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { macd } from "../lib/macd";
import { extendIntradayTimes } from "../lib/session";
import type { IntradayPoint, KlineBar } from "../types";

const UP = "#ff5a6a";
const DOWN = "#22c55e";
const GOLD = "#d4b06a";
const MUTED = "#8b93a7";
const LINE = "#2a3140";
const TEXT = "#e8edf5";

function axis(show = true) {
  return {
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { show, lineStyle: { color: LINE, opacity: 0.45 } },
    axisLabel: { color: MUTED, fontSize: 10 },
  };
}

function useChart(option: echarts.EChartsOption | null) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option, true);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);
  return ref;
}

export function buildIntradayOption(points: IntradayPoint[], preClose: number): echarts.EChartsOption {
  const times = extendIntradayTimes(points.map((item) => item.time));
  const blanks = Array.from({ length: Math.max(0, times.length - points.length) }, () => null);
  const prices = [...points.map((item) => item.price), ...blanks];
  const avgs = [...points.map((item) => item.avg), ...blanks];
  const volumes = [...points.map((item) => item.volume), ...blanks];
  const indicator = macd(points.map((item) => item.price));
  const sessionLabel = (value: string) => value === "09:30" || value === "11:30" || value === "15:00";
  return {
    animation: false,
    backgroundColor: "transparent",
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 42, right: 8, top: 12, height: "46%" },
      { left: 42, right: 8, top: "58%", height: "14%" },
      { left: 42, right: 8, top: "76%", height: "16%" },
    ],
    xAxis: [0, 1, 2].map((index) => ({
      type: "category" as const,
      data: times,
      gridIndex: index,
      axisLabel: {
        show: index === 2,
        color: MUTED,
        fontSize: 10,
        interval: (_idx: number, value: string) => sessionLabel(value),
      },
      axisTick: { show: false },
      axisLine: { show: false },
    })),
    yAxis: [
      { ...axis(), scale: true, gridIndex: 0, splitNumber: 3 },
      { ...axis(false), gridIndex: 1, splitNumber: 2 },
      { ...axis(false), gridIndex: 2, splitNumber: 2 },
    ],
    series: [
      {
        name: "昨收",
        type: "line",
        data: times.map(() => preClose || null),
        showSymbol: false,
        lineStyle: { color: MUTED, width: 1, type: "dashed" },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: "价格",
        type: "line",
        data: prices,
        showSymbol: false,
        lineStyle: { color: TEXT, width: 1.4 },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: "均价",
        type: "line",
        data: avgs,
        showSymbol: false,
        lineStyle: { color: GOLD, width: 1 },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: "成交量",
        type: "bar",
        data: volumes.map((value, index) => ({
          value,
          itemStyle: { color: (prices[index] ?? preClose) >= (index > 0 ? prices[index - 1] ?? preClose : preClose) ? UP : DOWN },
        })),
        xAxisIndex: 1,
        yAxisIndex: 1,
      },
      {
        name: "DIF",
        type: "line",
        data: [...indicator.dif.map((value) => Number(value.toFixed(4))), ...blanks],
        showSymbol: false,
        lineStyle: { color: "#7dd3fc", width: 1 },
        xAxisIndex: 2,
        yAxisIndex: 2,
      },
      {
        name: "DEA",
        type: "line",
        data: [...indicator.dea.map((value) => Number(value.toFixed(4))), ...blanks],
        showSymbol: false,
        lineStyle: { color: GOLD, width: 1 },
        xAxisIndex: 2,
        yAxisIndex: 2,
      },
      {
        name: "MACD",
        type: "bar",
        data: [...indicator.hist.map((value) => ({ value: Number(value.toFixed(4)), itemStyle: { color: value >= 0 ? UP : DOWN } })), ...blanks],
        xAxisIndex: 2,
        yAxisIndex: 2,
      },
    ],
  };
}

export function buildKlineOption(bars: KlineBar[], period: "day" | "month"): echarts.EChartsOption {
  const indicator = macd(bars.map((item) => item.close));
  const window = period === "day" ? 80 : 36;
  const start = bars.length > window ? ((bars.length - window) / bars.length) * 100 : 0;
  return {
    animation: false,
    backgroundColor: "transparent",
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1, 2], start, end: 100 }],
    grid: [
      { left: 42, right: 8, top: 12, height: "46%" },
      { left: 42, right: 8, top: "58%", height: "14%" },
      { left: 42, right: 8, top: "76%", height: "16%" },
    ],
    xAxis: [0, 1, 2].map((index) => ({
      type: "category" as const,
      data: bars.map((item) => item.time),
      gridIndex: index,
      axisLabel: {
        show: index === 2,
        color: MUTED,
        fontSize: 10,
        formatter: (value: string) => (period === "month" ? value.slice(0, 7) : value.slice(5)),
      },
      axisTick: { show: false },
      axisLine: { show: false },
    })),
    yAxis: [
      { ...axis(), scale: true, gridIndex: 0, splitNumber: 3 },
      { ...axis(false), gridIndex: 1, splitNumber: 2 },
      { ...axis(false), gridIndex: 2, splitNumber: 2 },
    ],
    series: [
      {
        type: "candlestick",
        data: bars.map((item) => [item.open, item.close, item.low, item.high]),
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: "成交量",
        type: "bar",
        data: bars.map((item) => ({ value: item.volume, itemStyle: { color: item.close >= item.open ? UP : DOWN } })),
        xAxisIndex: 1,
        yAxisIndex: 1,
      },
      {
        name: "DIF",
        type: "line",
        data: indicator.dif.map((value) => Number(value.toFixed(4))),
        showSymbol: false,
        lineStyle: { color: "#7dd3fc", width: 1 },
        xAxisIndex: 2,
        yAxisIndex: 2,
      },
      {
        name: "DEA",
        type: "line",
        data: indicator.dea.map((value) => Number(value.toFixed(4))),
        showSymbol: false,
        lineStyle: { color: GOLD, width: 1 },
        xAxisIndex: 2,
        yAxisIndex: 2,
      },
      {
        name: "MACD",
        type: "bar",
        data: indicator.hist.map((value) => ({ value: Number(value.toFixed(4)), itemStyle: { color: value >= 0 ? UP : DOWN } })),
        xAxisIndex: 2,
        yAxisIndex: 2,
      },
    ],
  };
}

export function IntradayChart({ points, preClose }: { points: IntradayPoint[]; preClose: number }) {
  const option = useMemo(
    () => (points.length ? buildIntradayOption(points, preClose) : null),
    [points, preClose],
  );
  const ref = useChart(option);
  if (!option) return <p className="chart-empty">暂无该日分时</p>;
  return <div className="quote-chart" ref={ref} />;
}

export function KlineChart({ bars, period }: { bars: KlineBar[]; period: "day" | "month" }) {
  const option = useMemo(() => (bars.length ? buildKlineOption(bars, period) : null), [bars, period]);
  const ref = useChart(option);
  if (!option) return <p className="chart-empty">暂无该日K线</p>;
  return <div className="quote-chart" ref={ref} />;
}
