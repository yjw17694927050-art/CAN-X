import { useSyncExternalStore } from "react";

import type { FrameViewportSnapshot } from "../workers/frame-worker-core";

/** Connection lifecycle of the single shared realtime pipeline. */
export type StreamConnection = "idle" | "connecting" | "open" | "closed" | "unsupported";

export interface RealtimeStreamState {
  readonly connection: StreamConnection;
  readonly streamId: string | null;
  readonly snapshot: FrameViewportSnapshot | null;
  readonly droppedViewFrames: number;
  readonly sequenceGaps: number;
  /** Monotonic duration of the most recent Worker batch decode, in milliseconds. */
  readonly decodeMs: number;
}

export interface RealtimeStreamDeps {
  readonly createWorker: () => Worker | null;
  readonly createSocket: (url: string) => WebSocket | null;
  readonly url: string;
}

/**
 * One workspace-owned WebSocket + Worker + bounded store.
 *
 * Trace and Plot are projections of this single store; neither owns a socket or
 * a worker. Consumers are reference-counted, so the socket and worker are opened
 * on the first subscriber and torn down exactly once when the last one leaves.
 */
export class RealtimeStreamStore {
  #state: RealtimeStreamState = {
    connection: "idle",
    streamId: null,
    snapshot: null,
    droppedViewFrames: 0,
    sequenceGaps: 0,
    decodeMs: 0,
  };
  readonly #listeners = new Set<() => void>();
  #worker: Worker | null = null;
  #socket: WebSocket | null = null;

  constructor(private readonly deps: RealtimeStreamDeps) {}

  getState = (): RealtimeStreamState => this.#state;

  get consumerCount(): number {
    return this.#listeners.size;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    this.#start();
    return () => {
      this.#listeners.delete(listener);
      if (this.#listeners.size === 0) this.#stop();
    };
  };

  #emit(): void {
    for (const listener of this.#listeners) listener();
  }

  #patch(patch: Partial<RealtimeStreamState>): void {
    this.#state = { ...this.#state, ...patch };
    this.#emit();
  }

  #publish(snapshot: FrameViewportSnapshot): void {
    this.#patch({
      streamId: snapshot.streamId,
      snapshot,
      droppedViewFrames: snapshot.droppedViewFrames,
      sequenceGaps: snapshot.sequenceGaps,
      decodeMs: snapshot.decodeMs,
    });
  }

  #start(): void {
    if (this.#worker !== null || this.#socket !== null) return;
    const worker = this.deps.createWorker();
    const socket = this.deps.createSocket(this.deps.url);
    if (worker === null || socket === null) {
      // Atomic initialization: if either resource is unavailable, release the
      // one that was created so no orphan Worker or WebSocket is left behind.
      worker?.terminate();
      socket?.close();
      this.#patch({ connection: "unsupported" });
      return;
    }
    this.#worker = worker;
    this.#socket = socket;
    this.#patch({ connection: "connecting" });
    worker.onmessage = (event: MessageEvent) => {
      const snapshot = readViewportSnapshot(event.data);
      if (snapshot !== null) this.#publish(snapshot);
    };
    socket.binaryType = "arraybuffer";
    socket.onopen = () => this.#patch({ connection: "open" });
    socket.onclose = () => this.#patch({ connection: "closed" });
    socket.onerror = () => this.#patch({ connection: "closed" });
    socket.onmessage = (event: MessageEvent) => {
      const payload = event.data as ArrayBuffer;
      worker.postMessage({ type: "batch", payload }, [payload]);
    };
  }

  #stop(): void {
    const worker = this.#worker;
    const socket = this.#socket;
    this.#worker = null;
    this.#socket = null;
    socket?.close();
    worker?.terminate();
    this.#state = {
      connection: "idle",
      streamId: null,
      snapshot: null,
      droppedViewFrames: 0,
      sequenceGaps: 0,
      decodeMs: 0,
    };
    this.#emit();
  }
}

function readViewportSnapshot(value: unknown): FrameViewportSnapshot | null {
  if (typeof value !== "object" || value === null || !("type" in value)) return null;
  const candidate = value as { type: unknown; snapshot?: FrameViewportSnapshot };
  if (candidate.type !== "viewport" || candidate.snapshot === undefined) return null;
  return candidate.snapshot;
}

export const REALTIME_STREAM_URL = "ws://127.0.0.1:8765/stream/frames";

function createBrowserWorker(): Worker | null {
  if (typeof Worker === "undefined") return null;
  return new Worker(new URL("../workers/frame-worker.ts", import.meta.url), { type: "module" });
}

function createBrowserSocket(url: string): WebSocket | null {
  if (typeof WebSocket === "undefined") return null;
  return new WebSocket(url);
}

export const realtimeStream = new RealtimeStreamStore({
  createWorker: createBrowserWorker,
  createSocket: createBrowserSocket,
  url: REALTIME_STREAM_URL,
});

export function useRealtimeStream(store: RealtimeStreamStore = realtimeStream): RealtimeStreamState {
  return useSyncExternalStore(store.subscribe, store.getState, store.getState);
}
