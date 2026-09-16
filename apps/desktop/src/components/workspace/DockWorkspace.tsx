import { createDockview, type CreateComponentOptions, type IContentRenderer } from "dockview";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useTranslation } from "react-i18next";

import { LiveTracePanel } from "../trace/LiveTracePanel";
import { startVirtualCapture, stopCapture } from "../../runtime/capture-client";
import { useRealtimeStream } from "../../runtime/realtime-stream";
import { LivePlotPanel } from "../plot/LivePlotPanel";
import "dockview/dist/styles/dockview.css";

class WorkspacePanelRenderer implements IContentRenderer {
  readonly element = document.createElement("section");
  readonly #root: Root;

  constructor(options: CreateComponentOptions) {
    this.element.className = "workspace-panel";
    this.element.dataset.panel = options.name;
    this.#root = createRoot(this.element);
    this.#root.render(panelContent(options.name));
  }

  init(): void {}

  dispose(): void {
    queueMicrotask(() => this.#root.unmount());
  }
}

function panelContent(name: string): ReactNode {
  if (name === "trace") return <LiveTracePanel />;
  if (name === "plot") return <LivePlotPanel />;
  return <div className="workspace-panel-placeholder" />;
}

export function DockWorkspace() {
  const containerRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
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
      createComponent: (options) => new WorkspacePanelRenderer(options),
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

    return () => api.dispose();
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
