import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { FrameViewportSnapshot } from "../../workers/frame-worker-core";
import { buildByteSeries } from "./series";

interface WorkerViewportMessage {
  readonly type: "viewport";
  readonly snapshot: FrameViewportSnapshot;
}

export function LivePlotPanel() {
  const { t } = useTranslation();
  const chartElement = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("echarts/core").ECharts | null>(null);
  const latestSeries = useRef<readonly (readonly [number, number])[]>([]);
  const [frames, setFrames] = useState<readonly RuntimeFrame[]>([]);
  const series = useMemo(() => buildByteSeries(frames, 0, 1_000), [frames]);
  latestSeries.current = series.map(({ time, value }) => [time, value] as const);

  useEffect(() => {
    if (typeof Worker === "undefined" || typeof WebSocket === "undefined") return;
    const worker = new Worker(new URL("../../workers/frame-worker.ts", import.meta.url), {
      type: "module",
    });
    const websocket = new WebSocket("ws://127.0.0.1:8765/stream/frames");
    websocket.binaryType = "arraybuffer";
    websocket.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      worker.postMessage({ type: "batch", payload: event.data }, [event.data]);
    };
    worker.onmessage = (event: MessageEvent<WorkerViewportMessage>) => {
      if (event.data.type === "viewport") setFrames(event.data.snapshot.frames);
    };
    return () => {
      websocket.close();
      worker.terminate();
    };
  }, []);

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
