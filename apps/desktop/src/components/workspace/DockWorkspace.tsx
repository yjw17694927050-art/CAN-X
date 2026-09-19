import { QueryClientProvider, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createDockview, type CreateComponentOptions, type IContentRenderer } from "dockview";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useTranslation } from "react-i18next";

import { LiveTracePanel } from "../trace/LiveTracePanel";
import { startVirtualCapture, stopCapture } from "../../runtime/capture-client";
import { useRealtimeStream } from "../../runtime/realtime-stream";
import { LivePlotPanel } from "../plot/LivePlotPanel";
import { DbcWorkspace } from "../dbc/DbcWorkspace";
import { ProjectControls } from "../project/ProjectControls";
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
  ) {
    this.element.className = "workspace-panel";
    this.element.dataset.panel = options.name;
    this.#root = createRoot(this.element);
    // Every Dockview panel is rendered into its own React root, so the provider that
    // wraps `<App />` does not reach it. Two app-wide singletons are therefore handed
    // across explicitly, and both are the *same instance* in every panel: the app's
    // QueryClient (one server-state cache) and the workspace's one WorkspaceSessionStore
    // (one selection/binding authority). A panel that built its own session would be a
    // second authority, which is exactly what sharing here prevents.
    this.#root.render(
      <QueryClientProvider client={queryClient}>
        <WorkspaceSessionProvider store={session}>{panelContent(options.name)}</WorkspaceSessionProvider>
      </QueryClientProvider>,
    );
  }

  init(): void {}

  dispose(): void {
    queueMicrotask(() => this.#root.unmount());
  }
}

function panelContent(name: string): ReactNode {
  if (name === "project") return <ProjectControls />;
  if (name === "trace") return <LiveTracePanel />;
  if (name === "plot") return <LivePlotPanel />;
  if (name === "dbc") return <DbcWorkspace />;
  return <div className="workspace-panel-placeholder" />;
}

export interface DockWorkspaceProps {
  /**
   * The workspace's one session, injected. Omitted in production, where the workspace
   * creates exactly one per mount; a test passes one to observe what the panels read.
   */
  readonly session?: WorkspaceSessionStore;
}

export function DockWorkspace({ session }: DockWorkspaceProps = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { connection, decodeMs } = useRealtimeStream();
  const [runtimeState, setRuntimeState] = useState<"starting" | "capturing" | "failed">("starting");
  // One session per mounted workspace, created once and never replaced. Its identity is
  // stable for the whole mount, which is what lets the Dockview layout below outlive a
  // project change instead of being torn down and rebuilt for one.
  const [created] = useState(createWorkspaceSession);
  const store = session ?? created;

  useEffect(() => {
    let ownsCapture = false;
    void startVirtualCapture()
      .then((started) => {
        ownsCapture = started;
        setRuntimeState("capturing");
      })
      .catch(() => setRuntimeState("failed"));
    return () => {
      if (ownsCapture) void stopCapture();
    };
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return;
    }

    const api = createDockview(container, {
      createComponent: (options) => new WorkspacePanelRenderer(options, store, queryClient),
    });

    api.addPanel({ component: "project", id: "project", title: t("workspace.project") });
    api.addPanel({
      component: "trace",
      id: "trace",
      position: { direction: "right", referencePanel: "project" },
      title: t("workspace.trace"),
    });
    api.addPanel({
      component: "plot",
      id: "plot",
      position: { direction: "right", referencePanel: "trace" },
      title: t("workspace.plot"),
    });
    api.addPanel({
      component: "agent",
      id: "agent",
      position: { direction: "below", referencePanel: "plot" },
      title: t("workspace.agent"),
    });
    // DBC joins the trace group as a sibling tab: a permanent engineering workspace,
    // not a separate floating view.
    api.addPanel({
      component: "dbc",
      id: "dbc",
      position: { direction: "within", referencePanel: "trace" },
      title: t("workspace.dbc"),
    });

    return () => api.dispose();
    // `store` and `queryClient` are stable identities for the whole mount, so this
    // layout is built exactly once. Project state deliberately does NOT appear here: it
    // reaches the panels through the session (a subscription), so opening or switching a
    // project updates panels in place rather than disposing and rebuilding every panel —
    // and therefore never tears down the Trace/Plot roots that hold the realtime
    // subscription, nor restarts the capture lifecycle.
  }, [t, queryClient, store]);

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
