/// <reference lib="webworker" />

import { FrameWorkerCore } from "./frame-worker-core";

type WorkerCommand =
  | { readonly type: "configure"; readonly capacity: number }
  | { readonly type: "freeze" }
  | { readonly type: "resume" }
  | { readonly type: "batch"; readonly payload: ArrayBuffer };

const SNAPSHOT_INTERVAL_MS = 33;
let core = new FrameWorkerCore(20_000, SNAPSHOT_INTERVAL_MS);
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush(): void {
  if (flushTimer !== null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    const snapshot = core.flush();
    if (snapshot !== null) self.postMessage({ type: "viewport", snapshot });
  }, SNAPSHOT_INTERVAL_MS);
}

self.onmessage = (event: MessageEvent<WorkerCommand>) => {
  try {
    const command = event.data;
    if (command.type === "configure") {
      core = new FrameWorkerCore(command.capacity, SNAPSHOT_INTERVAL_MS);
    }
    if (command.type === "freeze") core.freeze();
    if (command.type === "resume") self.postMessage({ type: "viewport", snapshot: core.resume() });
    if (command.type === "batch") {
      const snapshot = core.ingest(command.payload);
      if (snapshot !== null) self.postMessage({ type: "viewport", snapshot });
      else scheduleFlush();
    }
  } catch (error: unknown) {
    self.postMessage({
      type: "error",
      message: error instanceof Error ? error.message : "Unknown worker failure",
    });
  }
};
