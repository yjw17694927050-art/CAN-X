import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import { useRealtimeStream, type RealtimeStreamStore } from "../../runtime/realtime-stream";
import { buildByteSeries } from "./series";

const NO_FRAMES: readonly RuntimeFrame[] = [];

export interface LivePlotPanelProps {
  readonly store?: RealtimeStreamStore;
}

/**
 * Plot is a projection of the same shared realtime store as Trace. It never
 * owns a socket or Worker and is unaffected by a Trace-only freeze.
 */
export function LivePlotPanel({ store }: LivePlotPanelProps = {}) {
  const { t } = useTranslation();
  const chartElement = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("echarts/core").ECharts | null>(null);
  const latestSeries = useRef<readonly (readonly [number, number])[]>([]);
  const { snapshot } = useRealtimeStream(store);
  const frames = snapshot?.frames ?? NO_FRAMES;
  const series = useMemo(() => buildByteSeries(frames, 0, 1_000), [frames]);
  latestSeries.current = series.map(({ time, value }) => [time, value] as const);

  useEffect(() => {
    const element = chartElement.current;
    if (element === null) return;
    let disposed = false;
    void Promise.all([
      import("echarts/core"),
      import("echarts/charts"),
      import("echarts/components"),
      import("echarts/renderers"),
    ]).then(([echarts, charts, components, renderers]) => {
      if (disposed) return;
      echarts.use([
        charts.LineChart,
        components.GridComponent,
        components.TooltipComponent,
        renderers.CanvasRenderer,
      ]);
      const chart = echarts.init(element, undefined, { renderer: "canvas" });
      chartRef.current = chart;
      chart.setOption({
        animation: false,
        grid: { left: 48, right: 16, top: 20, bottom: 38 },
        tooltip: { trigger: "axis" },
        xAxis: { name: t("plot.time"), type: "value" },
        yAxis: { max: 255, min: 0, name: t("plot.byte0"), type: "value" },
        series: [{ data: latestSeries.current, showSymbol: false, type: "line" }],
      });
    });
    return () => {
      disposed = true;
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [t]);

  useEffect(() => {
    chartRef.current?.setOption({ series: [{ data: latestSeries.current }] });
  }, [series]);

  return <div aria-label={t("plot.title")} className="plot-canvas" ref={chartElement} />;
}
