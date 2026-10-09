import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { buildIntradayOption, buildKlineOption, parseMaPeriods } from "../../../../market-data-hub/web/quoteCharts.ts";
import type { IntradayPoint, KlineBar } from "../types";

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

export function IntradayChart({ points, preClose }: { points: IntradayPoint[]; preClose: number }) {
  const option = useMemo(
    () => (points.length ? buildIntradayOption(points, preClose, "compact") : null),
    [points, preClose],
  );
  const ref = useChart(option);
  if (!option) return <p className="chart-empty">暂无该日分时</p>;
  return <div className="quote-chart" ref={ref} />;
}

export function KlineChart({ bars, period, maKey = "" }: { bars: KlineBar[]; period: "day" | "month"; maKey?: string }) {
  const option = useMemo(
    () => (bars.length ? buildKlineOption(bars, period, parseMaPeriods(maKey), "compact") : null),
    [bars, period, maKey],
  );
  const ref = useChart(option);
  if (!option) return <p className="chart-empty">暂无该日K线</p>;
  return <div className="quote-chart" ref={ref} />;
}
