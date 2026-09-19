import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

import { useRealtimeStream, type RealtimeStreamStore } from "../../runtime/realtime-stream";
import {
  EMPTY_DECODED_REALTIME,
  type DecodedRealtimeState,
  type DecodedRealtimeStore,
} from "../../workspace/decoded-realtime";
import { useWorkspaceSession } from "../../workspace/WorkspaceSessionProvider";
import { buildSignalSeries, DEFAULT_MAX_SAMPLES, type PlotSample } from "./series";

/**
 * The live signal plot: one decoded signal's **physical value** against time.
 *
 * ```text
 * WorkspaceSessionStore.selectedSignal    (channelId, assetId, messageName, signalName, unit)
 *   + DecodedRealtimeStore.entries        the outcomes the decode coordinator recorded
 *   ↓ buildSignalSeries(entries, selection, 1000)
 * PlotSample[]  { time: normalizedTimestamp, value: physicalValue }
 *   ↓
 * ECharts line: x = frame time, y = physical value, axis named "<Signal> (<unit>)"
 * ```
 *
 * Five properties are deliberate:
 *
 * * **A curve is a decoded signal, never a payload byte.** The panel that used to draw
 *   `byte0` of every frame is gone: a byte index is not a signal, it has no unit, no
 *   scaling and no name an engineer recognises, and it silently plotted unrelated
 *   frames side by side. What is drawn now is one named signal of one message, decoded
 *   against one asset, on one channel — the selection the user made.
 * * **The selection comes from the session, and only from there.** The panel reads
 *   `selectedSignal` through the workspace provider, so the DBC workspace can be in a
 *   different React root (Dockview renders every panel into its own) and still be the
 *   same authority. Nothing here picks a signal on its own: no default, no "first
 *   message", no fallback to the browsed asset.
 * * **The decoded store is injected, never read from context.** The shell hands the one
 *   `DecodedRealtimeStore` to every panel that projects it, so Plot and Trace read the
 *   same outcomes and neither issues a decode request. Absent, the panel reads the
 *   shared empty state and shows the waiting state — it never constructs a store.
 * * **Three real states, each said out loud.** No selection: the empty state that points
 *   at the DBC workspace. A selection with no samples yet: the waiting state — not an
 *   empty chart, which would look like a signal that is flat at zero. Samples: the
 *   curve. There is no fourth "unknown" state, because there is nothing else the panel
 *   could be waiting for.
 * * **It still shares the one realtime pipeline.** The panel subscribes to the same
 *   `RealtimeStreamStore` Trace uses, so both panels remain projections of one socket
 *   and one Worker; the stream identity is what keys the chart, because two epochs'
 *   timestamps are not comparable and a curve must not outlive the stream it came from.
 */

const NO_SAMPLES: readonly PlotSample[] = [];

/** A store that was not injected reads the shared empty state and never notifies. */
const subscribeToNothing = (): (() => void) => () => undefined;
const readEmptyDecoded = (): DecodedRealtimeState => EMPTY_DECODED_REALTIME;

export interface LivePlotPanelProps {
  /**
   * The shared realtime store to observe. Defaults to the app's one `realtimeStream`:
   * this panel is a second *subscriber*, never a second pipeline.
   */
  readonly store?: RealtimeStreamStore;
  /**
   * The workspace's decoded outcomes, injected by the shell.
   *
   * Plot never fetches and never owns decode state: it reads the same store the Trace
   * panel reads, so both project one set of outcomes.
   */
  readonly decoded?: DecodedRealtimeStore;
}

export function LivePlotPanel({ store, decoded }: LivePlotPanelProps = {}) {
  const { t } = useTranslation();
  const { streamId } = useRealtimeStream(store);
  const decodedState = useSyncExternalStore(
    decoded?.subscribe ?? subscribeToNothing,
    decoded?.getSnapshot ?? readEmptyDecoded,
    decoded?.getSnapshot ?? readEmptyDecoded,
  );
  const { selectedSignal } = useWorkspaceSession();

  const samples = useMemo(
    () =>
      selectedSignal === null
        ? NO_SAMPLES
        : buildSignalSeries(decodedState.entries.values(), selectedSignal, DEFAULT_MAX_SAMPLES),
    [decodedState.entries, selectedSignal],
  );

  if (selectedSignal === null) {
    return (
      <section aria-label={t("plot.title")} className="plot-notice">
        <p className="plot-notice-title">{t("plot.noSelection.title")}</p>
        <p className="plot-notice-hint">{t("plot.noSelection.hint")}</p>
      </section>
    );
  }

  if (samples.length === 0) {
    return (
      <section aria-label={t("plot.title")} className="plot-notice">
        <p className="plot-notice-waiting">{t("plot.waiting")}</p>
      </section>
    );
  }

  return (
    <SignalPlot
      axisName={axisName(selectedSignal.signalName, selectedSignal.unit)}
      // A new stream epoch makes every previously plotted timestamp incomparable, so
      // the chart is rebuilt rather than asked to carry a curve across the boundary.
      key={streamId ?? "no-stream"}
      samples={samples}
    />
  );
}

/**
 * The y-axis name: `<Signal> (<unit>)`, or the bare signal name when the document
 * declared no unit.
 *
 * Presentation metadata, joined here rather than stored: the unit is part of a
 * selection's *payload*, not its identity, and the axis label is the only place it is
 * needed.
 */
function axisName(signalName: string, unit: string | null): string {
  return unit === null || unit === "" ? signalName : `${signalName} (${unit})`;
}

/**
 * One curve, owned by ECharts and nothing else.
 *
 * The chart is mounted only while there are samples to show, so its lifecycle — the
 * dynamic import, the init, the dispose — is tied to the curve actually existing. That
 * is why this is a component rather than an effect in the panel: with the chart element
 * rendered conditionally, an effect keyed on translation alone would never see the
 * element appear.
 */
function SignalPlot({
  axisName: yAxisName,
  samples,
}: {
  readonly axisName: string;
  readonly samples: readonly PlotSample[];
}) {
  const { t } = useTranslation();
  const chartElement = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("echarts/core").ECharts | null>(null);
  const latestSeries = useRef<readonly (readonly [number, number])[]>([]);
  const latestAxisName = useRef(yAxisName);
  latestSeries.current = samples.map(({ time, value }) => [time, value] as const);
  latestAxisName.current = yAxisName;

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
        // No `min`/`max`: a byte's 0..255 domain meant nothing for a physical value, and
        // a fixed range would clip a scaled signal into a flat line at the edge.
        yAxis: { name: latestAxisName.current, type: "value" },
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
    chartRef.current?.setOption({
      series: [{ data: latestSeries.current }],
      yAxis: { name: latestAxisName.current },
    });
  }, [samples, yAxisName]);

  return <div aria-label={t("plot.title")} className="plot-canvas" ref={chartElement} />;
}
