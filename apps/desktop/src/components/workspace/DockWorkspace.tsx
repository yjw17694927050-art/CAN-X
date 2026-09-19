import { QueryClientProvider, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  createDockview,
  type CreateComponentOptions,
  type DockviewApi,
  type IContentRenderer,
} from "dockview";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useTranslation } from "react-i18next";

import { LiveTracePanel } from "../trace/LiveTracePanel";
import { startVirtualCapture, stopCapture } from "../../runtime/capture-client";
import { createCaptureLifecycle, type CapturePhase } from "../../runtime/capture-lifecycle";
import { decodeDbcFrameBatch } from "../../runtime/dbc-decode-client";
import { realtimeStream, useRealtimeStream } from "../../runtime/realtime-stream";
import { LivePlotPanel } from "../plot/LivePlotPanel";
import { DbcWorkspace } from "../dbc/DbcWorkspace";
import { ProjectControls } from "../project/ProjectControls";
import { createDecodedRealtimeStore, type DecodedRealtimeStore } from "../../workspace/decoded-realtime";
import { WorkspaceDecodeCoordinator } from "../../workspace/decode-coordinator";
import { createWorkspaceSession } from "../../workspace/session";
import type { WorkspaceSessionStore } from "../../workspace/session";
import { WorkspaceSessionProvider } from "../../workspace/WorkspaceSessionProvider";
import "dockview/dist/styles/dockview.css";

class WorkspacePanelRenderer implements IContentRenderer {
  readonly element = document.createElement("section");
  readonly #root: Root;

  constructor(
    options: CreateComponentOptions,
    session: WorkspaceSessionStore,
    queryClient: QueryClient,
    decoded: DecodedRealtimeStore,
  ) {
    this.element.className = "workspace-panel";
    this.element.dataset.panel = options.name;
    this.#root = createRoot(this.element);
    // Every Dockview panel is rendered into its own React root, so the provider that
    // wraps `<App />` does not reach it. Three app-wide singletons are therefore handed
    // across explicitly, and each is the *same instance* in every panel: the app's
    // QueryClient (one server-state cache), the workspace's one WorkspaceSessionStore
    // (one selection/binding authority) and the workspace's one DecodedRealtimeStore
    // (one set of decode outcomes). A panel that built its own session or its own decoded
    // store would be a second authority, which is exactly what sharing here prevents —
    // and Plot and Trace reading different outcomes is the bug this closes.
    this.#root.render(
      <QueryClientProvider client={queryClient}>
        <WorkspaceSessionProvider store={session}>
          {panelContent(options.name, decoded)}
        </WorkspaceSessionProvider>
      </QueryClientProvider>,
    );
  }

  init(): void {}

  dispose(): void {
    queueMicrotask(() => this.#root.unmount());
  }
}

function panelContent(name: string, decoded: DecodedRealtimeStore): ReactNode {
  if (name === "project") return <ProjectControls />;
  if (name === "trace") return <LiveTracePanel decoded={decoded} />;
  if (name === "plot") return <LivePlotPanel decoded={decoded} />;
  if (name === "dbc") return <DbcWorkspace />;
  return <div className="workspace-panel-placeholder" />;
}

export interface DockWorkspaceProps {
  /**
   * The workspace's one session, injected. Omitted in production, where the workspace
   * creates exactly one per mount; a test passes one to observe what the panels read.
   */
  readonly session?: WorkspaceSessionStore;
  /**
   * The workspace's one decoded store, injected. Omitted in production, where the workspace
   * creates exactly one per mount; a test passes one to observe what the panels read.
   */
  readonly decoded?: DecodedRealtimeStore;
}

export function DockWorkspace({ decoded: injectedDecoded, session }: DockWorkspaceProps = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
  /**
   * The translator the layout effect is allowed to read without depending on it.
   *
   * Measured, not assumed: `useTranslation()` hands back a **new** `i18n` object when the
   * language changes — and that object is not the module instance either — so a dependency
   * on `i18n` re-runs the layout effect and rebuilds every panel, which is exactly the
   * defect the effect below exists to avoid. A ref keeps the *current* translator reachable
   * from an effect that has no business re-running for a translation.
   */
  const translate = useRef(t);
  /**
   * The layout this mount built.
   *
   * A ref because two effects need it: the one that builds the layout, and the one that
   * retitles the panels a language change leaves in place.
   */
  const layout = useRef<DockviewApi | null>(null);
  const queryClient = useQueryClient();
  const { connection, decodeMs } = useRealtimeStream();
  // One capture lifecycle per mounted workspace, created exactly once — the same rule as the
  // session and the decoded store below. It replaces the closure boolean this mount used to
  // own, and both of that boolean's failures are ordering failures: a boolean set inside a
  // fire-and-forget `.then` cannot tell a cleanup that already ran whether there is anything
  // left to stop, and a second StrictMode setup would POST a second start against an endpoint
  // that answers `capture.already_running`. See `createCaptureBinding` below.
  const [capture] = useState(createCaptureBinding);
  // What the header says, projected from the lifecycle's own phase. Reading through
  // `useSyncExternalStore` makes the shell a subscriber of the lifecycle rather than a copy
  // of it, so the phase that renders is the phase the store holds.
  const runtimeState = useSyncExternalStore(capture.subscribe, capture.readRuntimeState);
  // One session per mounted workspace, created once and never replaced. Its identity is
  // stable for the whole mount, which is what lets the Dockview layout below outlive a
  // project change instead of being torn down and rebuilt for one.
  const [created] = useState(createWorkspaceSession);
  const store = session ?? created;
  // One decoded store, same rule: created once per mount, never per project. A project
  // change is an epoch *inside* the coordinator (which clears the store), not a reason to
  // build a new pipeline — and rebuilding would tear down the realtime subscription the
  // Trace and Plot roots hold.
  const [ownDecoded] = useState(createDecodedRealtimeStore);
  const decodedStore = injectedDecoded ?? ownDecoded;

  /**
   * The one decode loop. It subscribes to the same realtime store the panels do, so it is a
   * subscriber of the pipeline rather than a second pipeline: no socket, no Worker, no store
   * is created here.
   */
  useEffect(() => {
    const coordinator = new WorkspaceDecodeCoordinator({
      decode: decodeDbcFrameBatch,
      decoded: decodedStore,
      realtime: realtimeStream,
      session: store,
    });
    return coordinator.start();
  }, [decodedStore, store]);

  /**
   * The capture this workspace wants live, for exactly as long as it is mounted.
   *
   * This effect only *records intent* — acquire on mount, release on unmount — and the
   * lifecycle reconciles that intent against its own phase, one control-plane call at a time.
   * That split is what makes the two orderings that matter come out right: an answer that
   * arrives after unmount is stopped by the continuation that learns the capture was owned,
   * and a StrictMode replay is one intent rather than two starts. Neither a failed start nor a
   * failed stop rejects into a promise nobody holds — the lifecycle records them as its
   * `failed` phase.
   */
  useEffect(() => {
    capture.acquire();
    return () => capture.release();
  }, [capture]);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return;
    }

    const api = createDockview(container, {
      createComponent: (options) =>
        new WorkspacePanelRenderer(options, store, queryClient, decodedStore),
    });

    // The titles, in whatever language `i18n` is in right now. Read from the instance rather
    // than from the render's `t`, because `i18n` is the stable identity and `t` is not: a
    // language change hands back a new `t` and the same `i18n`. That is the whole distinction
    // this effect's dependency array needs, and it is measured rather than assumed —
    // `DockWorkspace.test.tsx` observes one layout across a `changeLanguage`, and observed a
    // second one for as long as `t` was listed here.
    api.addPanel({ component: "project", id: "project", title: translate.current("workspace.project") });
    api.addPanel({
      component: "trace",
      id: "trace",
      position: { direction: "right", referencePanel: "project" },
      title: translate.current("workspace.trace"),
    });
    api.addPanel({
      component: "plot",
      id: "plot",
      position: { direction: "right", referencePanel: "trace" },
      title: translate.current("workspace.plot"),
    });
    api.addPanel({
      component: "agent",
      id: "agent",
      position: { direction: "below", referencePanel: "plot" },
      title: translate.current("workspace.agent"),
    });
    // DBC joins the trace group as a sibling tab: a permanent engineering workspace,
    // not a separate floating view.
    api.addPanel({
      component: "dbc",
      id: "dbc",
      position: { direction: "within", referencePanel: "trace" },
      title: translate.current("workspace.dbc"),
    });

    layout.current = api;

    return () => api.dispose();
    // `i18n`, `store`, `decodedStore` and `queryClient` are stable identities for the whole
    // mount, so this layout is built exactly once, in whatever language it was built in.
    // Project state deliberately does NOT appear here, and neither does the translation
    // function: both change for reasons that have nothing to do with the layout, and rebuilding
    // would dispose and recreate every panel — tearing down the Trace/Plot roots that hold the
    // realtime subscription and restarting the capture lifecycle. A project change reaches the
    // panels through the session (a subscription), so panels update in place; a language change
    // reaches the titles through the effect below.
  }, [queryClient, store, decodedStore]);

  /**
   * Retitle the panels when the language changes.
   *
   * The other half of not rebuilding the layout for a translation: the panels are the same
   * instances, so their labels have to be brought forward rather than left in whatever
   * language the layout happened to be built in. Depending on `t` — not on `i18n` — is
   * deliberate and is the whole reason this is a separate effect: `t` is the identity that
   * changes with the language, and this effect sets five strings and disposes nothing, so
   * running it once per language change costs nothing.
   */
  useEffect(() => {
    translate.current = t;
    const api = layout.current;
    if (api === null) return;
    api.getPanel("project")?.setTitle(t("workspace.project"));
    api.getPanel("trace")?.setTitle(t("workspace.trace"));
    api.getPanel("plot")?.setTitle(t("workspace.plot"));
    api.getPanel("agent")?.setTitle(t("workspace.agent"));
    api.getPanel("dbc")?.setTitle(t("workspace.dbc"));
  }, [t]);

  return (
    <main aria-label={t("workspace.label")} className="workspace-shell">
      <div className={`runtime-state runtime-state-${runtimeState}`} role="status">
        {t(`runtime.${runtimeState}`)}
      </div>
      <div className={`stream-state stream-state-${connection}`} role="status">
        {t(`stream.${connection}`)}
        {decodeMs > 0 ? ` · ${t("stream.decodeMs", { ms: decodeMs.toFixed(2) })}` : ""}
      </div>
      <div className="dockview-theme-dark" ref={containerRef} />
    </main>
  );
}

/** What the workspace header says about the Runtime, as this component renders it. */
type CaptureRuntimeState = "starting" | "capturing" | "failed";

/**
 * The header's word for each phase the capture lifecycle can be in.
 *
 * `owned` and `observed` both mean a capture is live, so both read as `capturing`: the
 * difference between them is an obligation this component holds, not something a user needs
 * told. `failed` is the one phase that reports the Runtime rather than the capture, which is
 * the same text the header already used for a start that did not answer.
 */
const RUNTIME_STATE_OF_PHASE: Readonly<Record<CapturePhase, CaptureRuntimeState>> = {
  failed: "failed",
  idle: "starting",
  observed: "capturing",
  owned: "capturing",
  starting: "starting",
  stopping: "capturing",
};

/** The one capture lifecycle a mounted workspace owns, and the header's view of it. */
interface CaptureBinding {
  /** Declare that this workspace wants a capture to be live. Idempotent. */
  acquire(): void;
  /** The header's word for the lifecycle's phase, as a primitive snapshot. */
  readRuntimeState(): CaptureRuntimeState;
  /** Declare that nothing wants the capture any more. Idempotent. */
  release(): void;
  subscribe(listener: () => void): () => void;
}

/**
 * Build the workspace's capture lifecycle, wired to the Runtime's capture control plane.
 *
 * The projection is the point: {@link CaptureBinding.readRuntimeState} answers with a
 * *string*, so React's identity check on the store is about the phase rather than about a
 * state object that every phase change would have replaced.
 */
function createCaptureBinding(): CaptureBinding {
  const lifecycle = createCaptureLifecycle({ start: startVirtualCapture, stop: stopCapture });
  return {
    acquire: () => lifecycle.acquire(),
    readRuntimeState: () => RUNTIME_STATE_OF_PHASE[lifecycle.getState().phase],
    release: () => lifecycle.release(),
    subscribe: lifecycle.subscribe,
  };
}
