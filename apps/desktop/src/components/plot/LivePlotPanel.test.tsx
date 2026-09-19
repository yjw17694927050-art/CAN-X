import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "../../i18n/config";
import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { ProjectReadModel } from "../../runtime/project-client";
import { RealtimeStreamStore } from "../../runtime/realtime-stream";
import {
  createDecodedRealtimeStore,
  type DecodedFrameEntry,
} from "../../workspace/decoded-realtime";
import { createWorkspaceSession, type WorkspaceSessionStore } from "../../workspace/session";
import { WorkspaceSessionProvider } from "../../workspace/WorkspaceSessionProvider";
import { LivePlotPanel } from "./LivePlotPanel";

/**
 * ECharts is replaced by a recorder, so the assertion can be about the *data the panel
 * hands the chart* rather than about pixels jsdom cannot paint. The panel still loads its
 * four modules dynamically and still calls `init`/`setOption`; only their bodies are fakes.
 */
const chartMock = vi.hoisted(() => {
  const options: Record<string, unknown>[] = [];
  const resizes: unknown[] = [];
  const counts = { inits: 0 };
  return {
    counts,
    options,
    resizes,
    init: () => {
      counts.inits += 1;
      return {
        dispose: () => undefined,
        resize: () => {
          resizes.push(undefined);
        },
        setOption: (option: Record<string, unknown>) => {
          options.push(option);
        },
      };
    },
  };
});

/**
 * A `ResizeObserver` the test drives, rather than one that fires on `observe`.
 *
 * The stub `src/test/setup.ts` installs notifies immediately and synchronously, which is
 * what a real observer never does. A resize test needs the notification to arrive on the
 * test's command, so this one records its targets and delivers nothing until `notify()`.
 */
class RecordingResizeObserver implements ResizeObserver {
  static readonly instances: RecordingResizeObserver[] = [];

  readonly #callback: ResizeObserverCallback;
  #targets: Element[] = [];
  disconnects = 0;

  constructor(callback: ResizeObserverCallback) {
    this.#callback = callback;
    RecordingResizeObserver.instances.push(this);
  }

  disconnect(): void {
    this.disconnects += 1;
    this.#targets = [];
  }

  observe(target: Element): void {
    this.#targets.push(target);
  }

  unobserve(): void {}

  /** Deliver one notification per still-observed target, as a layout change would. */
  notify(): void {
    for (const target of this.#targets) {
      const entry: ResizeObserverEntry = {
        borderBoxSize: [],
        contentBoxSize: [],
        contentRect: new DOMRect(0, 0, 800, 320),
        devicePixelContentBoxSize: [],
        target,
      };
      this.#callback([entry], this);
    }
  }
}

/** The one observer the plot attached, or a failure that says it attached none. */
function onlyObserver(): RecordingResizeObserver {
  const observer = RecordingResizeObserver.instances[0];
  if (observer === undefined) throw new Error("the plot attached no ResizeObserver");
  return observer;
}

vi.mock("echarts/core", () => ({ init: chartMock.init, use: () => undefined }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const PROJECT_PATH = "C:\\customer\\secret-program";

const PROJECT: ProjectReadModel = {
  createdAt: "2026-09-15T09:00:00+00:00",
  displayName: "Secret Program",
  projectId: "0f6b9a52-1d3e-4c7a-9b21-51f0c6f2a7d8",
  schemaVersion: 1,
  updatedAt: "2026-09-17T09:00:00+00:00",
};

const ASSET_A = "dbc-asset-0001";

class FakeWorker {
  onmessage: ((event: { data: unknown }) => void) | null = null;

  postMessage(): void {}

  terminate(): void {}
}

class FakeSocket {
  binaryType = "arraybuffer";
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: ArrayBuffer }) => void) | null = null;
  onopen: (() => void) | null = null;

  close(): void {}
}

/** The one shared realtime store, with its Worker and socket faked. */
function harness() {
  const store = new RealtimeStreamStore({
    createSocket: () => new FakeSocket() as unknown as WebSocket,
    createWorker: () => new FakeWorker() as unknown as Worker,
    url: "ws://test/stream/frames",
  });
  return { store };
}

function frame(sequence: number): RuntimeFrame {
  return {
    arbitrationId: 0x123,
    bitrateSwitch: false,
    channelId: "can0",
    clockDomain: "host.monotonic",
    data: new Uint8Array([0x01, 0x02]),
    direction: "rx",
    dlc: 2,
    errorStateIndicator: false,
    flags: 0,
    hardwareTimestamp: null,
    hostTimestamp: 1_000 + sequence,
    isExtended: false,
    isFd: false,
    normalizedTimestamp: sequence / 1_000,
    sequence: BigInt(sequence),
    timestampQuality: "host",
  };
}

/** A decoded entry whose raw and physical values differ, so a mix-up cannot hide. */
function decodedEntry(sequence: number, rawValue: number, physicalValue: number): DecodedFrameEntry {
  return {
    assetId: ASSET_A,
    frame: frame(sequence),
    outcome: {
      messageName: "EngineData",
      signals: [
        { choiceLabel: null, name: "EngineSpeed", physicalValue, rawValue, unit: "rpm" },
      ],
      status: "decoded",
    },
  };
}

/** A session that has bound can0 to the asset and selected one of its signals. */
function selectionSession(unit: string | null = "rpm"): WorkspaceSessionStore {
  const session = createWorkspaceSession();
  session.openProject({ project: PROJECT, projectPath: PROJECT_PATH });
  session.bindChannel("can0", ASSET_A);
  session.selectSignal({
    assetId: ASSET_A,
    channelId: "can0",
    messageName: "EngineData",
    signalName: "EngineSpeed",
    unit,
  });
  return session;
}

function renderPlot(session: WorkspaceSessionStore, decoded = createDecodedRealtimeStore()) {
  const { store } = harness();
  const view = render(
    <WorkspaceSessionProvider store={session}>
      <LivePlotPanel decoded={decoded} store={store} />
    </WorkspaceSessionProvider>,
  );
  return { ...view, decoded };
}

/** The series data the panel last handed to ECharts. */
function plottedData(): unknown {
  const option = chartMock.options.at(-1);
  const series = option?.series as readonly { readonly data: unknown }[] | undefined;
  return series?.[0]?.data;
}

function yAxisName(): unknown {
  return (chartMock.options.at(-1)?.yAxis as { readonly name?: unknown } | undefined)?.name;
}

beforeEach(() => {
  chartMock.counts.inits = 0;
  chartMock.options.length = 0;
  chartMock.resizes.length = 0;
  RecordingResizeObserver.instances.length = 0;
  // The plot's own observer, notified on the test's command rather than on `observe`.
  vi.stubGlobal("ResizeObserver", RecordingResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LivePlotPanel — no signal selected", () => {
  it("shows the empty state and draws no curve at all", () => {
    const session = createWorkspaceSession();
    session.openProject({ project: PROJECT, projectPath: PROJECT_PATH });

    const { container } = renderPlot(session);

    expect(screen.getByText("No signal selected")).toBeInTheDocument();
    expect(
      screen.getByText("Choose a channel and a signal in the DBC workspace."),
    ).toBeInTheDocument();
    // No chart element, and therefore no byte of any frame rendered as a curve.
    expect(container.querySelector(".plot-canvas")).toBeNull();
    expect(chartMock.options).toEqual([]);
  });
});

describe("LivePlotPanel — a selection with nothing decoded yet", () => {
  it("says it is waiting rather than showing an empty chart", () => {
    const { container } = renderPlot(selectionSession());

    expect(screen.getByText("Waiting for decoded samples")).toBeInTheDocument();
    expect(container.querySelector(".plot-canvas")).toBeNull();
    expect(chartMock.options).toEqual([]);
  });
});

describe("LivePlotPanel — a selection with samples", () => {
  it("plots the decoded physical value against the frame's normalized timestamp", async () => {
    const { decoded } = renderPlot(selectionSession());

    act(() => {
      decoded.applyBatch({
        assetId: ASSET_A,
        entries: [decodedEntry(0, 617, 1_234), decodedEntry(1, 700, 1_500.5)],
        streamId: "s1",
      });
    });

    await waitFor(() => {
      expect(chartMock.options.length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("Waiting for decoded samples")).not.toBeInTheDocument();
    // 1234 is the physical value; 617 (the raw one) must never reach the chart, and the
    // x values are the frames' own instants (0, 0.001) — not `Date.now()` at decode time.
    expect(plottedData()).toEqual([
      [0, 1_234],
      [0.001, 1_500.5],
    ]);
  });

  it("names the y axis after the signal, with its unit when the document declares one", async () => {
    const { decoded } = renderPlot(selectionSession("rpm"));

    act(() => {
      decoded.applyBatch({
        assetId: ASSET_A,
        entries: [decodedEntry(0, 617, 1_234)],
        streamId: "s1",
      });
    });

    await waitFor(() => {
      expect(yAxisName()).toBe("EngineSpeed (rpm)");
    });
  });

  it("names the y axis with the bare signal name when there is no unit", async () => {
    const { decoded } = renderPlot(selectionSession(null));

    act(() => {
      decoded.applyBatch({
        assetId: ASSET_A,
        entries: [decodedEntry(0, 617, 1_234)],
        streamId: "s1",
      });
    });

    await waitFor(() => {
      expect(yAxisName()).toBe("EngineSpeed");
    });
  });
});

describe("LivePlotPanel — a panel that changes size", () => {
  /** A selection with one decoded sample, which is the only state that builds a chart. */
  function renderChart(): ReturnType<typeof renderPlot> {
    const view = renderPlot(selectionSession());
    act(() => {
      view.decoded.applyBatch({
        assetId: ASSET_A,
        entries: [decodedEntry(0, 617, 1_234)],
        streamId: "s1",
      });
    });
    return view;
  }

  it("attaches exactly one observer to the chart element and resizes on every notification", async () => {
    renderChart();

    // Nothing has been initialised until the four echarts modules have been fetched.
    await waitFor(() => {
      expect(RecordingResizeObserver.instances).toHaveLength(1);
    });
    expect(chartMock.counts.inits).toBe(1);
    expect(chartMock.resizes).toHaveLength(0);

    const observer = onlyObserver();

    act(() => {
      observer.notify();
    });
    expect(chartMock.resizes).toHaveLength(1);

    // Repeated notifications are the normal case — a drag delivers one per frame — and each
    // one is a `resize()`, never another observer.
    act(() => {
      observer.notify();
      observer.notify();
    });
    expect(chartMock.resizes).toHaveLength(3);
    expect(RecordingResizeObserver.instances).toHaveLength(1);
  });

  it("disconnects the observer and disposes the chart on unmount, leaving no leak", async () => {
    const { unmount } = renderChart();

    await waitFor(() => {
      expect(RecordingResizeObserver.instances).toHaveLength(1);
    });
    const observer = onlyObserver();
    expect(observer.disconnects).toBe(0);

    unmount();

    expect(observer.disconnects).toBe(1);
    // A notification already queued when the chart was disposed must not reach it, and must
    // certainly not bring it back.
    expect(() => observer.notify()).not.toThrow();
    expect(chartMock.resizes).toHaveLength(0);
  });

  it("attaches no observer and builds no chart when it unmounts before the modules load", async () => {
    const session = selectionSession();
    const { decoded, unmount } = renderPlot(session);
    act(() => {
      decoded.applyBatch({
        assetId: ASSET_A,
        entries: [decodedEntry(0, 617, 1_234)],
        streamId: "s1",
      });
    });

    // The four echarts modules are still being fetched: there is no chart yet, and therefore
    // nothing for an observer to observe and nothing to leak. Without the race guard the
    // import continuation would `init` a chart — and attach an observer — after unmount.
    unmount();
    await act(async () => {
      await Promise.resolve();
    });

    expect(chartMock.counts.inits).toBe(0);
    expect(chartMock.resizes).toHaveLength(0);
    expect(RecordingResizeObserver.instances).toHaveLength(0);
  });
});
