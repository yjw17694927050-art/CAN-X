import { useCallback, useEffect, useRef, useState } from "react";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { FrameViewportSnapshot } from "../../workers/frame-worker-core";
import { TracePanel, type TraceMode } from "./TracePanel";

interface WorkerViewportMessage {
  readonly type: "viewport";
  readonly snapshot: FrameViewportSnapshot;
}

export function LiveTracePanel() {
  const [frames, setFrames] = useState<readonly RuntimeFrame[]>([]);
  const [mode, setMode] = useState<TraceMode>("follow");
  const workerRef = useRef<Worker | null>(null);

  useEffect(() => {
    if (typeof Worker === "undefined" || typeof WebSocket === "undefined") return;
    const worker = new Worker(new URL("../../workers/frame-worker.ts", import.meta.url), {
      type: "module",
    });
    workerRef.current = worker;
    const websocket = new WebSocket("ws://127.0.0.1:8765/stream/frames");
    websocket.binaryType = "arraybuffer";
    websocket.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      worker.postMessage({ type: "batch", payload: event.data }, [event.data]);
    };
    worker.onmessage = (event: MessageEvent<WorkerViewportMessage>) => {
      if (event.data.type === "viewport") setFrames(event.data.snapshot.frames);
    };
    return () => {
      workerRef.current = null;
      websocket.close();
      worker.terminate();
    };
  }, []);

  const changeMode = useCallback((nextMode: TraceMode) => {
    workerRef.current?.postMessage({ type: nextMode === "freeze" ? "freeze" : "resume" });
    setMode(nextMode);
  }, []);

  return <TracePanel frames={frames} mode={mode} onModeChange={changeMode} />;
}
