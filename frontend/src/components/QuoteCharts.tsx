import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { buildIntradayOption, buildKlineOption, parseMaPeriods } from "../../../../market-data-hub/web/quoteCharts.ts";
import type { BarDetail } from "../../../../market-data-hub/web/barDetail.ts";
import type { IntradayPoint, KlineBar } from "../types";

function useChart(option: echarts.EChartsOption | null, onPick?: (index: number) => void) {
  const ref = useRef<HTMLDivElement>(null);
  const pick = useRef(onPick);
  useEffect(() => {
    pick.current = onPick;
  });
  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option, true);
    // 点在三块图任意位置都按横轴落到那一根，不必正好点中细蜡烛
    chart.getZr().on("click", (event) => {
      if (!pick.current) return;
      if (!chart.containPixel({ gridIndex: [0, 1, 2] }, [event.offsetX, event.offsetY])) return;
      const value = chart.convertFromPixel({ xAxisIndex: 0 }, event.offsetX);
      const index = Math.round(Array.isArray(value) ? value[0] : value);
      if (Number.isFinite(index)) pick.current(index);
    });
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

export function KlineChart({
  bars,
  period,
  maKey = "",
  selectedTime = "",
  onPick,
}: {
  bars: KlineBar[];
  period: "day" | "month";
  maKey?: string;
  selectedTime?: string;
  onPick?: (time: string) => void;
}) {
  const selectedIndex = selectedTime ? bars.findIndex((bar) => bar.time === selectedTime) : -1;
  const option = useMemo(
    () => (bars.length ? buildKlineOption(bars, period, parseMaPeriods(maKey), "compact", selectedIndex) : null),
    [bars, period, maKey, selectedIndex],
  );
  const ref = useChart(option, (index) => {
    const bar = bars[index];
    if (bar && onPick) onPick(bar.time);
  });
  if (!option) return <p className="chart-empty">暂无该日K线</p>;
  return <div className="quote-chart" ref={ref} />;
}

export function BarDetailPanel({ detail, onClose }: { detail: BarDetail; onClose: () => void }) {
  return (
    <div className="bar-detail">
      <div className="bar-detail-head">
        <strong>{detail.title}</strong>
        <button type="button" onClick={onClose}>收起</button>
      </div>
      {detail.sections.map((section) => (
        <section key={section.name}>
          <p className="kicker">{section.name}</p>
          <dl>
            {section.items.map((item) => (
              <div key={item.label} className={item.wide ? "wide" : undefined}>
                <dt>{item.label}</dt>
                <dd className={item.tone}>{item.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
