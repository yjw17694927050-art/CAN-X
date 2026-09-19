import "../../i18n/config";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { i18n } from "../../i18n/config";
import { DockWorkspace } from "./DockWorkspace";

/**
 * What the workspace shell owns, driven through the real `DockWorkspace`.
 *
 * Two of this component's jobs have no other witness:
 *
 * * **Who stops the capture.** The mount starts a capture whose answer arrives
 *   asynchronously, and it is the *shell* that has to stop the one it started — no panel
 *   does, and nothing below this file can see the ordering. Both failure modes are
 *   orderings a test cannot reach by accident, so `startVirtualCapture` is a promise the
 *   test resolves by hand: the answer arrives exactly when the test says it does.
 * * **Whether a translation change rebuilds the layout.** Dockview's layout is built in an
 *   effect whose dependency array contains `t` from `useTranslation`, and only a driven
 *   language change can say what that costs. `createDockview` is recorded rather than
 *   rendered so the answer is a number of layouts, not an impression of one.
 *
 * Dockview itself is replaced by a recorder: this file is about the shell's effects, and
 * a real Dockview contributes panel bodies that jsdom cannot lay out anyway. The realtime
 * pipeline is left alone — it is driven here only through the globals `WebSocket` and
 * `Worker` that it already takes from the environment.
 */

interface Pending<T> {
  reject(cause: unknown): void;
  resolve(value: T): void;
}

/** The capture control plane, replaced by promises the test resolves by hand. */
const capture = vi.hoisted(() => ({
  startCalls: 0,
  starts: [] as Pending<boolean>[],
  stopCalls: 0,
  stops: [] as Pending<void>[],
}));

vi.mock("../../runtime/capture-client", () => ({
  startVirtualCapture: () => {
    capture.startCalls += 1;
    return new Promise<boolean>((resolve, reject) => {
      capture.starts.push({ reject, resolve });
    });
  },
  stopCapture: () => {
    capture.stopCalls += 1;
    return new Promise<void>((resolve, reject) => {
      capture.stops.push({ reject, resolve });
    });
  },
}));

interface RecordedPanel {
  readonly id: string;
  /** The latest title, as `addPanel` or a later `setTitle` left it. */
  title: string;
}

interface RecordedLayout {
  disposed: number;
  /** The panels in the order they were added. */
  readonly panels: RecordedPanel[];
}

/** Every layout `createDockview` builds, in order, so "rebuilt" is a countable fact. */
const dock = vi.hoisted(() => ({ layouts: [] as RecordedLayout[] }));

vi.mock("dockview", () => {
  /** The panel handle Dockview answers with — `setTitle` is the only part under test. */
  const handle = (panel: RecordedPanel) => {
    const setTitle = (title: string) => {
      panel.title = title;
    };
    return { api: { setTitle }, setTitle };
  };

  return {
    createDockview: () => {
      const layout: RecordedLayout = { disposed: 0, panels: [] };
      dock.layouts.push(layout);
      return {
        addPanel: (options: { id: string; title?: string }) => {
          const panel: RecordedPanel = { id: options.id, title: options.title ?? "" };
          layout.panels.push(panel);
          return handle(panel);
        },
        dispose: () => {
          layout.disposed += 1;
        },
        getPanel: (id: string) => {
          const panel = layout.panels.find((candidate) => candidate.id === id);
          return panel === undefined ? undefined : handle(panel);
        },
      };
    },
  };
});

/** The titles of the layout built at `index`, in panel order. */
function titlesOf(index: number): string[] {
  return dock.layouts[index]?.panels.map((panel) => panel.title) ?? [];
}

class StubWorker {
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  postMessage(): void {}

  terminate(): void {}
}

/**
 * A socket that fails as soon as the pipeline has finished wiring it up.
 *
 * The failure is delivered on a microtask rather than from the constructor, because the
 * store installs `onerror` only after the socket exists — a synchronous failure would be
 * a failure nobody was listening for, which is not the state under test.
 */
class FailingSocket {
  binaryType = "";
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: ArrayBuffer }) => void) | null = null;
  onopen: (() => void) | null = null;

  constructor(readonly url: string) {
    queueMicrotask(() => this.onerror?.());
  }

  close(): void {}
}

function renderWorkspace(options: { readonly strict?: boolean } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const workspace = (
    <QueryClientProvider client={queryClient}>
      <DockWorkspace />
    </QueryClientProvider>
  );
  const tree: ReactNode = options.strict === true ? <StrictMode>{workspace}</StrictMode> : workspace;
  return render(tree);
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  capture.startCalls = 0;
  capture.starts.length = 0;
  capture.stopCalls = 0;
  capture.stops.length = 0;
  dock.layouts.length = 0;
  // No pipeline in the capture-ordering tests: an absent Worker and WebSocket are the
  // one environment this store already answers without a socket or a timer.
  vi.stubGlobal("WebSocket", undefined);
  vi.stubGlobal("Worker", undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DockWorkspace — the capture it starts, it stops", () => {
  it("stops the capture whose start answered only after the workspace was unmounted", async () => {
    const view = renderWorkspace();

    // The POST is in flight and nobody has claimed the capture yet.
    expect(capture.startCalls).toBe(1);
    expect(capture.stopCalls).toBe(0);

    view.unmount();
    // The cleanup that ran above has nothing to stop: whether this workspace owns the
    // capture is not known until the answer arrives.
    expect(capture.stopCalls).toBe(0);

    await act(async () => {
      capture.starts[0]?.resolve(true);
    });

    // Whoever learns the capture was owned is the one that stops it. If the decision is
    // taken by the cleanup instead — a closure boolean read before the answer exists —
    // nothing stops it and this is 0.
    expect(capture.stopCalls).toBe(1);

    await act(async () => {
      capture.stops[0]?.resolve();
    });
  });

  it("starts exactly one capture when StrictMode replays the mount", async () => {
    const view = renderWorkspace({ strict: true });

    // StrictMode in development runs setup → cleanup → setup. A second `start` would be a
    // second POST against an endpoint that answers `capture.already_running`, and the
    // second answer would look like somebody else's capture.
    expect(capture.startCalls).toBe(1);

    await act(async () => {
      capture.starts[0]?.resolve(true);
    });

    expect(capture.startCalls).toBe(1);
    expect(capture.stopCalls).toBe(0);

    // The real unmount still stops exactly the capture that was started here.
    view.unmount();
    expect(capture.stopCalls).toBe(1);

    await act(async () => {
      capture.stops[0]?.resolve();
    });
  });

  it("stops nothing when the capture it found was already running", async () => {
    const view = renderWorkspace();

    // `false` is the Runtime saying "somebody else is capturing": attaching is not
    // owning, and stopping another owner's capture is worse than leaking one.
    await act(async () => {
      capture.starts[0]?.resolve(false);
    });

    view.unmount();
    expect(capture.stopCalls).toBe(0);
  });

  it("leaves no unobserved rejection when the stop fails", async () => {
    const dropped: unknown[] = [];
    const collect = (reason: unknown) => dropped.push(reason);
    process.on("unhandledRejection", collect);

    const view = renderWorkspace();
    await act(async () => {
      capture.starts[0]?.resolve(true);
    });

    view.unmount();
    await act(async () => {
      capture.stops[0]?.reject(new Error("the Runtime is not listening"));
    });
    // A rejection is only observed once the microtask queue has drained.
    await act(async () => {
      await Promise.resolve();
    });
    process.off("unhandledRejection", collect);

    expect(dropped).toEqual([]);
  });
});

describe("DockWorkspace — a translation change and the Dockview layout", () => {
  /**
   * The measurement, taken before the fix and kept as its regression test.
   *
   * With `t` in the layout's dependency array this file observed a *second* `createDockview`
   * and a `dispose` on the first: language change ⟹ layout rebuild ⟹ every panel recreated
   * ⟹ the Trace/Plot roots holding the realtime subscription torn down. The numbers below are
   * that observation, not a hope — the first two assertions fail on the rebuild, the third
   * fails on any fix that decouples the layout by dropping the titles instead of retitling.
   */
  it("does not rebuild the layout, and retitles the panels it keeps", async () => {
    renderWorkspace();

    expect(dock.layouts).toHaveLength(1);
    expect(dock.layouts[0]?.panels.map((panel) => panel.id)).toStrictEqual([
      "project",
      "trace",
      "plot",
      "agent",
      "dbc",
    ]);
    expect(titlesOf(0)).toStrictEqual(["Project", "Trace", "Plot", "Agent", "DBC"]);

    await act(async () => {
      await i18n.changeLanguage("zh-CN");
    });

    // One layout, never disposed: a translation is not a reason to rebuild the workspace.
    expect(dock.layouts).toHaveLength(1);
    expect(dock.layouts[0]?.disposed).toBe(0);
    // …and the panels that were kept say the right thing in the new language, so decoupling
    // the layout from `t` did not cost the user their translated labels.
    expect(titlesOf(0)).toStrictEqual(["项目", "报文追踪", "绘图", "Agent", "DBC"]);
  });
});

describe("DockWorkspace — the retrying stream state", () => {
  it("says the stream is reconnecting instead of printing the key", async () => {
    vi.stubGlobal("Worker", StubWorker);
    vi.stubGlobal("WebSocket", FailingSocket);

    renderWorkspace();

    await waitFor(() => {
      expect(screen.getByText("Stream reconnecting")).toBeInTheDocument();
    });
    expect(screen.queryByText("stream.retrying")).toBeNull();
  });
});
