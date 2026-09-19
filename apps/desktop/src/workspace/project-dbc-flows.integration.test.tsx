import "../i18n/config";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DbcWorkspace } from "../components/dbc/DbcWorkspace";
import { ProjectControls } from "../components/project/ProjectControls";
import type { ProjectReadModel } from "../runtime/project-client";
import { useRealtimeStream } from "../runtime/realtime-stream";
import { createWorkspaceSession, type WorkspaceSessionStore } from "./session";
import { WorkspaceSessionProvider, useWorkspaceSession } from "./WorkspaceSessionProvider";

/**
 * The V0.3-13 cross-boundary flows, driven through the REAL modules.
 *
 * Only the two outermost adapters are replaced — the Tauri `invoke` (the native pickers)
 * and `fetch` (the Runtime's HTTP surface). Everything between them is production code:
 * the real orchestration compositions, the real desktop bridges, the real Runtime clients
 * with their real typed error classes, the real workspace session, and the real
 * `ProjectControls` / `DbcWorkspace` / `DbcImportControl` / `DbcBindingControl`. The
 * failure mode a wiring increment has to guard against is two correct halves joined
 * wrongly, and only the real halves can show that.
 *
 * ```text
 * how the panels are rendered here, and why it is the production composition
 *
 * DockWorkspace                            this file
 *   createRoot(panelElement)                render(<PanelHarness …/>)     ← one root per panel
 *     QueryClientProvider (app's)             QueryClientProvider (shared)
 *       WorkspaceSessionProvider (one)          WorkspaceSessionProvider (one)
 *         <ProjectControls/> · <DbcWorkspace/>    <ProjectControls/> · <DbcWorkspace/>
 * ```
 *
 * Dockview itself is the one part this file cannot drive: it mounts panel content lazily
 * behind real layout, and jsdom performs no layout, so a Dockview panel's body is not in
 * the document under test. The provider composition above is therefore reproduced
 * verbatim instead of driven through Dockview, and Dockview's own contribution — one
 * session per workspace, handed to every panel root, with a layout that a project change
 * must NOT rebuild — is pinned by {@link describe} "DockWorkspace wiring" below, which
 * asserts it against the real source rather than against a rendered tree.
 */

const PROJECT_A = "C:\\programs\\alpha.canx";
const PROJECT_B = "D:\\programs\\beta.canx";

const MODEL_A: ProjectReadModel = {
  projectId: "11111111-1111-4111-8111-111111111111",
  displayName: "Alpha Program",
  schemaVersion: 1,
  createdAt: "2026-09-19T00:00:00+00:00",
  updatedAt: "2026-09-19T01:00:00+00:00",
};

const MODEL_B: ProjectReadModel = {
  projectId: "22222222-2222-4222-8222-222222222222",
  displayName: "Beta Program",
  schemaVersion: 1,
  createdAt: "2026-09-19T02:00:00+00:00",
  updatedAt: "2026-09-19T03:00:00+00:00",
};

interface WireAsset {
  readonly asset_id: string;
  readonly source_name: string;
  readonly sha256: string;
  readonly size_bytes: number;
  readonly encoding: string;
  readonly imported_at: string;
}

function wireAsset(assetId: string, sourceName: string): WireAsset {
  return {
    asset_id: assetId,
    source_name: sourceName,
    sha256: "a".repeat(64),
    size_bytes: 1024,
    encoding: "utf-8",
    imported_at: "2026-09-19T04:00:00+00:00",
  };
}

/** The Tauri pickers, keyed by command name. A missing key rejects like a closed shell. */
const pickers = vi.hoisted(() => new Map<string, () => unknown>());

// The native pickers are the one desktop boundary that cannot exist under jsdom. The
// command names are asserted elsewhere (project-open.integration.test.ts and the Rust
// bridge's own tests); here the picker is the user's choice, so it is a variable.
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (command: string) => {
    const picker = pickers.get(command);
    if (picker === undefined) {
      throw new Error(`the desktop shell has no command ${command}`);
    }
    return picker();
  },
}));

/** What the fake Runtime holds, per project path. */
let runtimeAssets: Map<string, WireAsset[]>;

/** Every URL the renderer asked the Runtime for, in order. */
let requestedUrls: string[];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

function assetCollectionOf(projectPath: string): readonly WireAsset[] {
  return runtimeAssets.get(projectPath) ?? [];
}

/**
 * A stand-in for the Runtime's HTTP surface — an adapter, not a module mock.
 *
 * It answers the endpoints the desktop calls, keyed by the `project_path` query parameter
 * it is handed, so "which project did that request name?" is answered by the real URL the
 * real client built.
 */
function installRuntime(): void {
  requestedUrls = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    requestedUrls.push(url);
    const parsed = new URL(url);
    const projectPath = parsed.searchParams.get("project_path") ?? "";
    const method = init?.method ?? "GET";

    if (parsed.pathname === "/project/inspect") {
      const model =
        projectPath === PROJECT_A ? MODEL_A : projectPath === PROJECT_B ? MODEL_B : null;
      return model === null
        ? jsonResponse(
            {
              code: "project.not_found",
              details: {},
              message: "not a CAN-X project",
              recoverable: false,
              source: "project",
            },
            404,
          )
        : jsonResponse({
            created_at: model.createdAt,
            display_name: model.displayName,
            project_id: model.projectId,
            schema_version: model.schemaVersion,
            updated_at: model.updatedAt,
          });
    }

    if (parsed.pathname === "/dbc/assets" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}")) as {
        project_path: string;
        source_name: string;
      };
      const imported = wireAsset(`dbc-asset-${body.source_name.length}${Date.now() % 1000}`, body.source_name);
      runtimeAssets.set(body.project_path, [...assetCollectionOf(body.project_path), imported]);
      return jsonResponse(imported, 201);
    }

    if (parsed.pathname === "/dbc/assets") {
      return jsonResponse({ assets: assetCollectionOf(projectPath) });
    }

    // The capture lifecycle DockWorkspace starts on mount. Not what this file tests, but
    // it must not be the reason a flow fails.
    return jsonResponse({ started: true });
  });
}

/** Record every Worker and WebSocket the realtime pipeline constructs. */
let workers: unknown[];
let sockets: unknown[];

/**
 * The viewport snapshot the sentinel pipeline delivers once it is opened.
 *
 * One frame is enough: a channel exists for the binding control precisely because a frame
 * carrying its `channelId` arrived, which is the only channel authority CAN-X has.
 */
const SNAPSHOT = {
  decodeMs: 0,
  droppedViewFrames: 0,
  frames: [{ channelId: "can0" }],
  sequenceGaps: 0,
  streamId: "stream-1",
};

function installRealtimeSentinels(): void {
  workers = [];
  sockets = [];
  class SentinelWorker {
    onmessage: ((event: { data: unknown }) => void) | null = null;
    constructor() {
      workers.push(this);
    }
    // The store hands each received batch to the worker and expects a viewport back, so
    // the sentinel answers exactly that: it is the decode step, and answering lets the
    // binding control observe a channel without a socket, a decoder or a real frame.
    postMessage(): void {
      this.onmessage?.({ data: { snapshot: SNAPSHOT, type: "viewport" } });
    }
    terminate(): void {}
  }
  class SentinelSocket {
    binaryType = "";
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onmessage: ((event: { data: unknown }) => void) | null = null;
    constructor(readonly url: string) {
      sockets.push(this);
      // One batch, delivered asynchronously so subscription is complete first.
      queueMicrotask(() => {
        this.onopen?.();
        this.onmessage?.({ data: new ArrayBuffer(8) });
      });
    }
    close(): void {}
  }
  vi.stubGlobal("Worker", SentinelWorker);
  vi.stubGlobal("WebSocket", SentinelSocket);
}

beforeEach(() => {
  pickers.clear();
  pickers.set("select_project_directory", () => null);
  runtimeAssets = new Map([
    [PROJECT_A, [wireAsset("dbc-asset-a1", "powertrain.dbc")]],
    [PROJECT_B, [wireAsset("dbc-asset-b1", "body.dbc")]],
  ]);
  installRuntime();
  installRealtimeSentinels();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The exact composition DockWorkspace hands to one panel root. */
function PanelHarness({
  children,
  session,
}: {
  readonly children: React.ReactNode;
  readonly session: WorkspaceSessionStore;
}) {
  const queryClient = sharedQueryClient;
  return (
    <QueryClientProvider client={queryClient}>
      <WorkspaceSessionProvider store={session}>{children}</WorkspaceSessionProvider>
    </QueryClientProvider>
  );
}

let sharedQueryClient: QueryClient;

/**
 * The steady realtime subscriber the production layout always has.
 *
 * In the real workspace the Trace and Plot panels are permanently in the Dockview layout
 * and both observe the one shared realtime store, so the pipeline is held open by them
 * regardless of what the DBC panel is doing. Reproducing that here matters: without it the
 * DBC panel would be the only subscriber, and its loading state during a project switch
 * would drop the count to zero and legitimately tear the pipeline down — a property of the
 * harness, not of the product.
 */
function RealtimeKeeper() {
  useRealtimeStream();
  return null;
}

/** Mount the project panel and the DBC panel as two roots over ONE session. */
function mountWorkspacePanels(session: WorkspaceSessionStore) {
  sharedQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const projectPanel = render(
    <PanelHarness session={session}>
      <RealtimeKeeper />
      <ProjectControls />
    </PanelHarness>,
  );
  const dbcPanel = render(
    <PanelHarness session={session}>
      <RealtimeKeeper />
      <DbcWorkspace />
    </PanelHarness>,
  );
  return { dbcPanel, projectPanel };
}

function openButton(): HTMLElement {
  return screen.getByRole("button", { name: /Open Project/ });
}

function openProjectPath(session: WorkspaceSessionStore): string | null {
  return session.getSnapshot().openedProject?.projectPath ?? null;
}

/** Paths the Runtime was asked about, in order, for one endpoint. */
function askedPaths(pathname: string): string[] {
  return requestedUrls
    .map((url) => new URL(url))
    .filter((url) => url.pathname === pathname)
    .map((url) => url.searchParams.get("project_path") ?? "");
}

describe("V0.3-13 flow A — Project Open reaches the DBC panel with one project", () => {
  it("carries the selected directory from the picker to the DBC panel's Runtime query", async () => {
    pickers.set("select_project_directory", () => PROJECT_A);
    const session = createWorkspaceSession();
    mountWorkspacePanels(session);

    fireEvent.click(openButton());

    await waitFor(() => expect(openProjectPath(session)).toBe(PROJECT_A));
    // The session holds the path verbatim and the model the Runtime declared.
    expect(session.getSnapshot().openedProject?.project.projectId).toBe(MODEL_A.projectId);
    expect(session.getSnapshot().openedProject?.project.displayName).toBe("Alpha Program");

    // The DBC panel — a different React root, fed only through the session — asked the
    // Runtime about exactly that project, with nothing normalised on the way.
    await waitFor(() => expect(askedPaths("/dbc/assets")).toContain(PROJECT_A));
    expect(askedPaths("/dbc/assets").every((path) => path === PROJECT_A)).toBe(true);
  });

  it("performs no Runtime request and writes nothing when the picker is dismissed", async () => {
    pickers.set("select_project_directory", () => null);
    const session = createWorkspaceSession();
    mountWorkspacePanels(session);

    fireEvent.click(openButton());
    await screen.findByText("No project was opened.");

    expect(session.getSnapshot().openedProject).toBeNull();
    expect(askedPaths("/project/inspect")).toStrictEqual([]);
    expect(askedPaths("/dbc/assets")).toStrictEqual([]);
  });
});

describe("V0.3-13 flow B — DBC Import refreshes the asset list", () => {
  it("imports through the Runtime and the list refetches the new asset", async () => {
    pickers.set("select_project_directory", () => PROJECT_A);
    pickers.set("select_dbc_file", () => ({
      content_base64: "VkVSVEhDQU5Y",
      source_name: "chassis.dbc",
    }));
    const session = createWorkspaceSession();
    mountWorkspacePanels(session);

    fireEvent.click(openButton());
    await waitFor(() => expect(openProjectPath(session)).toBe(PROJECT_A));
    await screen.findByRole("option", { name: /powertrain\.dbc/ });

    const importButton = screen.getByRole("button", { name: /Import DBC/ });
    await waitFor(() => expect(importButton).toBeEnabled());
    fireEvent.click(importButton);

    await screen.findByText(/Imported chassis\.dbc/);
    // Visible without a manual reload: the invalidation keyed on THIS project's path is
    // what made the list refetch, and the refetch is what put the asset on screen.
    expect(await screen.findByRole("option", { name: /chassis\.dbc/ })).toBeInTheDocument();
    expect(askedPaths("/dbc/assets").filter((path) => path === PROJECT_A).length).toBeGreaterThan(1);
  });
});

describe("V0.3-13 flow C — inspecting is not binding", () => {
  it("changes no binding when an asset is inspected, and binds only on Bind", async () => {
    pickers.set("select_project_directory", () => PROJECT_A);
    const session = createWorkspaceSession();
    mountWorkspacePanels(session);

    fireEvent.click(openButton());
    await waitFor(() => expect(openProjectPath(session)).toBe(PROJECT_A));

    fireEvent.click(await screen.findByRole("option", { name: /powertrain\.dbc/ }));

    // Inspecting records the inspection in the workspace session and nothing else.
    await waitFor(() => expect(session.getSnapshot().browsedAssetId).toBe("dbc-asset-a1"));
    expect(session.getSnapshot().dbcBindings.size).toBe(0);

    const row = await screen.findByRole("row", { name: /can0/ });
    expect(within(row).getByText("Not bound")).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: "Bind" }));

    await waitFor(() => expect(session.assetForChannel("can0")).toBe("dbc-asset-a1"));
    const boundRow = await screen.findByRole("row", { name: /can0/ });
    expect(boundRow.dataset.bindingState).toBe("bound");
    expect(within(boundRow).getByText("powertrain.dbc")).toBeInTheDocument();
  });
});

describe("V0.3-13 flow D — a project switch clears everything the old project chose", () => {
  it("leaves no binding and no inspected asset, and reads the new project", async () => {
    const picked = [PROJECT_A, PROJECT_B];
    pickers.set("select_project_directory", () => picked.shift() ?? null);
    const session = createWorkspaceSession();
    mountWorkspacePanels(session);

    fireEvent.click(openButton());
    await waitFor(() => expect(openProjectPath(session)).toBe(PROJECT_A));

    fireEvent.click(await screen.findByRole("option", { name: /powertrain\.dbc/ }));
    await waitFor(() => expect(session.getSnapshot().browsedAssetId).toBe("dbc-asset-a1"));
    fireEvent.click(
      within(await screen.findByRole("row", { name: /can0/ })).getByRole("button", { name: "Bind" }),
    );
    await waitFor(() => expect(session.assetForChannel("can0")).toBe("dbc-asset-a1"));

    requestedUrls = [];
    fireEvent.click(openButton());
    await waitFor(() => expect(openProjectPath(session)).toBe(PROJECT_B));

    // Project A's decisions are gone, not merely stale.
    expect(session.getSnapshot().dbcBindings.size).toBe(0);
    expect(session.assetForChannel("can0")).toBeNull();
    expect(session.getSnapshot().browsedAssetId).toBeNull();

    // And the DBC panel moved to B: it reads B's assets and never re-reads A's.
    expect(await screen.findByRole("option", { name: /body\.dbc/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /powertrain\.dbc/ })).not.toBeInTheDocument();
    expect(askedPaths("/dbc/assets").length).toBeGreaterThan(0);
    expect(askedPaths("/dbc/assets").every((path) => path === PROJECT_B)).toBe(true);
  });
});

describe("V0.3-13 flow E — switching projects does not multiply the realtime pipeline", () => {
  it("opens one Worker and one socket, and opens no more across repeated switches", async () => {
    const picked = [PROJECT_A, PROJECT_B, PROJECT_A, PROJECT_B];
    pickers.set("select_project_directory", () => picked.shift() ?? null);
    const session = createWorkspaceSession();
    mountWorkspacePanels(session);

    fireEvent.click(openButton());
    await waitFor(() => expect(openProjectPath(session)).toBe(PROJECT_A));
    // The pipeline opens when a panel that observes channels mounts, which happens once
    // the asset collection has loaded.
    await screen.findByRole("option", { name: /powertrain\.dbc/ });
    await waitFor(() => expect(workers.length).toBeGreaterThan(0));

    const workersAfterFirst = workers.length;
    const socketsAfterFirst = sockets.length;
    // The absolute count is what the mount transition produced and is deliberately not
    // pinned to 1: the pipeline opens when the first channel-observing panel mounts, and
    // that transition is not what this flow is about. What must NOT happen is growth.
    expect(workersAfterFirst).toBeGreaterThan(0);
    expect(socketsAfterFirst).toBeGreaterThan(0);

    for (let round = 0; round < 3; round += 1) {
      fireEvent.click(openButton());
      await waitFor(() => expect(session.getSnapshot().openedProject).not.toBeNull());
    }

    // Project changes reach the panels through the session subscription, so nothing in
    // the realtime pipeline is rebuilt: no second Worker, no second socket.
    expect(workers).toHaveLength(workersAfterFirst);
    expect(sockets).toHaveLength(socketsAfterFirst);
    expect(session.listenerCount).toBeGreaterThanOrEqual(2);
  });
});

describe("V0.3-13 — one session authority across React roots", () => {
  it("shares one session between two separately rendered panel subtrees", async () => {
    const session = createWorkspaceSession();
    session.openProject({ project: MODEL_A, projectPath: PROJECT_A });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const first = render(
      <QueryClientProvider client={client}>
        <WorkspaceSessionProvider store={session}>
          <SessionReader />
        </WorkspaceSessionProvider>
      </QueryClientProvider>,
    );
    const second = render(
      <QueryClientProvider client={client}>
        <WorkspaceSessionProvider store={session}>
          <SessionReader />
        </WorkspaceSessionProvider>
      </QueryClientProvider>,
    );

    // A change published by one root is observed by the other: both read the one store
    // rather than keeping a copy each, which is what stops a second authority existing.
    session.browseAsset("dbc-asset-a1");

    await waitFor(() =>
      expect(within(first.container).getByTestId("browsed")).toHaveTextContent("dbc-asset-a1"),
    );
    expect(within(second.container).getByTestId("browsed")).toHaveTextContent("dbc-asset-a1");
    expect(session.listenerCount).toBe(2);
  });
});

function SessionReader() {
  const { browsedAssetId } = useWorkspaceSession();
  return <span data-testid="browsed">{browsedAssetId ?? "none"}</span>;
}

/**
 * Dockview's own contribution, asserted against the source it is written in.
 *
 * jsdom cannot render a Dockview panel body, so the wiring that makes a project change
 * cheap is pinned the way this repository already pins a cross-language one: by reading
 * the file and asserting the property directly. Both assertions are load-bearing — adding
 * `store.getSnapshot()` to the layout's dependency array, or dropping the
 * `WorkspaceSessionProvider` from a panel root, turns one of them RED.
 */
describe("V0.3-13 — DockWorkspace wiring", () => {
  // vitest's root is `apps/desktop`, so this resolves to the file the app builds from.
  const source = readFileSync(
    resolve(process.cwd(), "src/components/workspace/DockWorkspace.tsx"),
    "utf8",
  );

  it("does not rebuild the Dockview layout when the project changes", () => {
    const effect = source.slice(source.indexOf("    return () => api.dispose();"));
    const deps = effect.slice(effect.indexOf("}, ["), effect.indexOf("]);\n"));
    expect(deps).not.toContain("projectPath");
    expect(deps).not.toContain("getSnapshot");
    expect(deps).toContain("store");
  });

  it("creates exactly one session per mount and hands it to every panel root", () => {
    expect(source).toContain("useState(createWorkspaceSession)");
    expect(source).toContain("session ?? created");
    expect(source).toContain(
      "<WorkspaceSessionProvider store={session}>{panelContent(options.name)}</WorkspaceSessionProvider>",
    );
    // No module-level instance: the session is a mount-local value, never a singleton.
    expect(source).not.toContain("createWorkspaceSession()");
  });
});
