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
import "dockview/dist/styles/dockview.css";

class WorkspacePanelRenderer implements IContentRenderer {
  readonly element = document.createElement("section");
  readonly #root: Root;

  constructor(options: CreateComponentOptions, projectPath: string | null, queryClient: QueryClient) {
    this.element.className = "workspace-panel";
    this.element.dataset.panel = options.name;
    this.#root = createRoot(this.element);
    // Every Dockview panel is rendered into its own React root, so the provider that
    // wraps `<App />` does not reach it. The app's QueryClient is handed across
    // explicitly, which keeps one server-state cache for the whole desktop while the
    // DBC panel lives in a root the app root cannot see.
    this.#root.render(
      <QueryClientProvider client={queryClient}>{panelContent(options.name, projectPath)}</QueryClientProvider>,
    );
  }

  init(): void {}

  dispose(): void {
    queueMicrotask(() => this.#root.unmount());
  }
}

function panelContent(name: string, projectPath: string | null): ReactNode {
  if (name === "trace") return <LiveTracePanel />;
  if (name === "plot") return <LivePlotPanel />;
  if (name === "dbc") return <DbcWorkspace projectPath={projectPath} />;
  return <div className="workspace-panel-placeholder" />;
}

export interface DockWorkspaceProps {
  /**
   * The CAN-X project the DBC workspace inspects, or `null` when none is open.
   *
   * It is a plain input, not a store: no global current project is created, and until
   * a Project Workspace exists to supply a path the DBC panel renders its no-project
   * state rather than inventing one.
   */
  readonly projectPath?: string | null;
}

export function DockWorkspace({ projectPath = null }: DockWorkspaceProps = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { connection, decodeMs } = useRealtimeStream();
  const [runtimeState, setRuntimeState] = useState<"starting" | "capturing" | "failed">("starting");

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
      createComponent: (options) => new WorkspacePanelRenderer(options, projectPath, queryClient),
    });

    api.addPanel({ component: "trace", id: "trace", title: t("workspace.trace") });
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
  }, [t, projectPath, queryClient]);

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
