import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { i18n } from "../../i18n/config";
import type { RuntimeDbcAsset } from "../../runtime/dbc-client";
import type { RuntimeFrame } from "../../runtime/frame-schema";
import { RealtimeStreamStore, realtimeStream } from "../../runtime/realtime-stream";
import type { ProjectReadModel } from "../../runtime/project-client";
import { WorkspaceSessionProvider } from "../../workspace/WorkspaceSessionProvider";
import { createWorkspaceSession, type WorkspaceSessionStore } from "../../workspace/session";
import { DbcBindingControl } from "./DbcBindingControl";

const PROJECT_PATH = "C:\\customer\\secret-program";

const PROJECT: ProjectReadModel = {
  createdAt: "2026-09-15T09:00:00+00:00",
  displayName: "Secret Program",
  projectId: "0f6b9a52-1d3e-4c7a-9b21-51f0c6f2a7d8",
  schemaVersion: 1,
  updatedAt: "2026-09-17T09:00:00+00:00",
};

const ASSET_A: RuntimeDbcAsset = {
  assetId: "dbc-asset-0001",
  encoding: "utf-8",
  importedAt: "2026-09-17T08:30:00+00:00",
  sha256: "a".repeat(64),
  sizeBytes: 2048,
  sourceName: "powertrain.dbc",
};

const ASSET_B: RuntimeDbcAsset = {
  assetId: "dbc-asset-0002",
  encoding: "latin-1",
  importedAt: "2026-09-16T10:15:00+00:00",
  sha256: "b".repeat(64),
  sizeBytes: 512,
  sourceName: "body.dbc",
};

const ASSETS: readonly RuntimeDbcAsset[] = [ASSET_A, ASSET_B];

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

/**
 * One injectable shared store plus the resources it opened.
 *
 * The deps are injected exactly the way the real pipeline injects `new Worker(...)` /
 * `new WebSocket(...)`, so the counts are the control's own evidence about how many
 * realtime pipelines exist: the store below opens one, on its first subscriber.
 */
function harness() {
  const workers: FakeWorker[] = [];
  const sockets: FakeSocket[] = [];
  const store = new RealtimeStreamStore({
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    createWorker: () => {
      const worker = new FakeWorker();
      workers.push(worker);
      return worker as unknown as Worker;
    },
    url: "ws://test/stream/frames",
  });

  const push = (...channelIds: readonly string[]): void => {
    act(() => {
      workers[0]?.onmessage?.({ data: { type: "viewport", snapshot: viewport(...channelIds) } });
    });
  };

  return { push, sockets, store, workers };
}

function frame(channelId: string, sequence: number): RuntimeFrame {
  return {
    arbitrationId: 0x123,
    bitrateSwitch: false,
    channelId,
    clockDomain: "host.monotonic",
    data: new Uint8Array([0x01]),
    direction: "rx",
    dlc: 1,
    errorStateIndicator: false,
    flags: 0,
    hardwareTimestamp: null,
    hostTimestamp: 100 + sequence,
    isExtended: false,
    isFd: false,
    normalizedTimestamp: sequence / 1000,
    sequence: BigInt(sequence),
    timestampQuality: "host",
  };
}

function viewport(...channelIds: readonly string[]) {
  return {
    decodeMs: 0,
    droppedViewFrames: 0,
    frames: channelIds.map((channelId, index) => frame(channelId, index)),
    sequenceGaps: 0,
    streamId: "stream-1",
  };
}

/** A session with the project open — and therefore a session that may hold bindings. */
function openedSession(): WorkspaceSessionStore {
  const session = createWorkspaceSession();
  session.openProject({ project: PROJECT, projectPath: PROJECT_PATH });
  return session;
}

/**
 * Render the control inside the workspace's one session, exactly as a panel would.
 *
 * The returned `session` is the same instance the provider was given, so a test can
 * assert on the bindings the control actually wrote rather than on the screen alone.
 * The no-session case is rendered directly by its own test, so this helper never has to
 * hand back a `null` session.
 */
function renderControl(options: {
  readonly assets?: readonly RuntimeDbcAsset[];
  readonly session?: WorkspaceSessionStore;
  readonly store: RealtimeStreamStore;
}) {
  const session = options.session ?? openedSession();
  const view = render(
    <WorkspaceSessionProvider store={session}>
      <DbcBindingControl assets={options.assets ?? ASSETS} store={options.store} />
    </WorkspaceSessionProvider>,
  );
  return { ...view, session };
}

/** The one row for a channel. Throws rather than returning null, so a missing row is loud. */
function channelRow(container: HTMLElement, channelId: string): HTMLElement {
  const row = container.querySelector(`tr[data-channel-id="${channelId}"]`);
  if (row === null) throw new Error(`No binding row was rendered for channel ${channelId}`);
  return row as HTMLElement;
}

function channelIds(container: HTMLElement): readonly (string | null)[] {
  return Array.from(container.querySelectorAll("tbody tr[data-channel-id]"), (row) =>
    row.getAttribute("data-channel-id"),
  );
}

describe("DbcBindingControl — observed channels", () => {
  it("shows a real empty state before any frame has been observed", () => {
    const { store } = harness();
    const { container } = renderControl({ store });

    expect(screen.getByText("No CAN channel has been observed yet.")).toBeInTheDocument();
    expect(channelIds(container)).toEqual([]);
  });

  it("lists every observed channel once, sorted, each with an explicit not-bound state", () => {
    const { push, store } = harness();
    const { container } = renderControl({ store });

    push("can1", "can0", "can1");

    expect(channelIds(container)).toEqual(["can0", "can1"]);
    for (const channelId of ["can0", "can1"]) {
      expect(within(channelRow(container, channelId)).getByText("Not bound")).toBeInTheDocument();
    }
  });
});

describe("DbcBindingControl — binding a channel", () => {
  it("shows the bound asset's sourceName on a bound channel row", () => {
    const { push, store } = harness();
    const session = openedSession();
    session.bindChannel("can0", ASSET_A.assetId);

    const { container } = renderControl({ session, store });
    push("can0", "can1");

    const bound = within(channelRow(container, "can0"));
    expect(bound.getByText("powertrain.dbc")).toBeInTheDocument();
    expect(bound.getByText("Bound")).toBeInTheDocument();
    expect(within(channelRow(container, "can1")).getByText("Not bound")).toBeInTheDocument();
  });

  it("binds the channel to the inspected asset when Bind is activated, then shows the row bound", () => {
    const { push, store } = harness();
    const { container, session } = renderControl({ store });
    push("can0");

    act(() => {
      session.browseAsset(ASSET_A.assetId);
    });
    fireEvent.click(within(channelRow(container, "can0")).getByRole("button", { name: "Bind" }));

    expect(session.assetForChannel("can0")).toBe(ASSET_A.assetId);
    const row = within(channelRow(container, "can0"));
    expect(row.getByText("powertrain.dbc")).toBeInTheDocument();
    expect(row.getByText("Bound")).toBeInTheDocument();
    expect(row.getByRole("button", { name: "Unbind" })).toBeInTheDocument();
  });

  it("unbinds the channel on Unbind and returns the row to its not-bound state", () => {
    const { push, store } = harness();
    const session = openedSession();
    session.bindChannel("can0", ASSET_A.assetId);

    const { container } = renderControl({ session, store });
    push("can0");

    fireEvent.click(within(channelRow(container, "can0")).getByRole("button", { name: "Unbind" }));

    expect(session.getSnapshot().dbcBindings.size).toBe(0);
    const row = within(channelRow(container, "can0"));
    expect(row.getByText("Not bound")).toBeInTheDocument();
    expect(row.getByRole("button", { name: "Bind" })).toBeInTheDocument();
  });

  it("offers no Bind while no asset is being inspected, and creates no binding", () => {
    const { push, store } = harness();
    const { container, session } = renderControl({ store });
    push("can0", "can1");

    expect(screen.getByText("Select a DBC asset to bind it to a channel.")).toBeInTheDocument();
    for (const channelId of ["can0", "can1"]) {
      const bind = within(channelRow(container, channelId)).getByRole("button", { name: "Bind" });
      expect(bind).toBeDisabled();
      fireEvent.click(bind);
    }

    expect(session.getSnapshot().dbcBindings.size).toBe(0);
    expect(session.assetForChannel("can0")).toBeNull();
  });

  it("offers no Bind when no project is open, because a binding needs a project behind it", () => {
    const { push, store } = harness();
    const session = createWorkspaceSession();
    const { container } = renderControl({ session, store });
    push("can0");

    act(() => {
      session.browseAsset(ASSET_A.assetId);
    });

    const bind = within(channelRow(container, "can0")).getByRole("button", { name: "Bind" });
    expect(bind).toBeDisabled();
    fireEvent.click(bind);

    expect(session.getSnapshot().dbcBindings.size).toBe(0);
  });

  it("offers no Bind when no workspace session is above it", () => {
    const { push, store } = harness();
    const { container } = render(<DbcBindingControl assets={ASSETS} store={store} />);
    push("can0");

    const bind = within(channelRow(container, "can0")).getByRole("button", { name: "Bind" });
    expect(bind).toBeDisabled();
    fireEvent.click(bind);

    expect(realtimeStream.consumerCount).toBe(0);
  });
});

describe("DbcBindingControl — browsing is not binding", () => {
  it("changes no binding while the inspected asset changes", () => {
    const { push, store } = harness();
    const { container, session } = renderControl({ store });
    push("can0", "can1");

    act(() => {
      session.browseAsset(ASSET_A.assetId);
    });
    expect(screen.getByText("Inspecting powertrain.dbc")).toBeInTheDocument();

    act(() => {
      session.browseAsset(ASSET_B.assetId);
    });
    expect(screen.getByText("Inspecting body.dbc")).toBeInTheDocument();

    expect(session.getSnapshot().dbcBindings.size).toBe(0);
    expect(session.assetForChannel("can0")).toBeNull();
    expect(session.assetForChannel("can1")).toBeNull();
    for (const channelId of ["can0", "can1"]) {
      expect(within(channelRow(container, channelId)).getByText("Not bound")).toBeInTheDocument();
    }
  });

  it("marks a binding whose asset is absent from the collection as unlisted rather than dropping the row", () => {
    const { push, store } = harness();
    const session = openedSession();
    session.bindChannel("can0", "dbc-asset-gone");

    const { container } = renderControl({ session, store });
    push("can0", "can1");

    const row = within(channelRow(container, "can0"));
    expect(row.getByText("an asset not in this project")).toBeInTheDocument();
    expect(row.getByText("Bound")).toBeInTheDocument();
    expect(within(channelRow(container, "can1")).getByText("Not bound")).toBeInTheDocument();
  });
});

describe("DbcBindingControl — one realtime pipeline", () => {
  it("subscribes to the injected store and creates no Worker or WebSocket of its own", () => {
    const globalWorker = globalThis.Worker;
    const globalSocket = globalThis.WebSocket;
    class ForbiddenWorker {
      constructor() {
        throw new Error("DbcBindingControl constructed its own Worker");
      }
    }
    class ForbiddenSocket {
      constructor() {
        throw new Error("DbcBindingControl constructed its own WebSocket");
      }
    }
    globalThis.Worker = ForbiddenWorker as unknown as typeof Worker;
    globalThis.WebSocket = ForbiddenSocket as unknown as typeof WebSocket;

    try {
      const { push, sockets, store, workers } = harness();
      const { container } = renderControl({ store });

      // The one shared store this component was handed is the only pipeline it uses.
      expect(store.consumerCount).toBe(1);
      expect(workers).toHaveLength(1);
      expect(sockets).toHaveLength(1);
      // The app-wide singleton stays untouched — no second subscriber, no second socket.
      expect(realtimeStream.consumerCount).toBe(0);

      push("can0");
      expect(channelIds(container)).toEqual(["can0"]);
      expect(store.consumerCount).toBe(1);
      expect(workers).toHaveLength(1);
      expect(sockets).toHaveLength(1);
    } finally {
      globalThis.Worker = globalWorker;
      globalThis.WebSocket = globalSocket;
    }
  });
});

describe("DbcBindingControl — i18n", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("localizes the binding control into zh-CN", async () => {
    await i18n.changeLanguage("zh-CN");
    const { push, store } = harness();
    const { container } = renderControl({ store });

    expect(screen.getByText("尚未观测到任何 CAN 通道。")).toBeInTheDocument();

    push("can0");
    expect(within(channelRow(container, "can0")).getByText("未绑定")).toBeInTheDocument();
    expect(within(channelRow(container, "can0")).getByRole("button", { name: "绑定" })).toBeInTheDocument();
  });
});
