import { useSyncExternalStore } from "react";

import type { FrameViewportSnapshot } from "../workers/frame-worker-core";

/**
 * Connection lifecycle of the single shared realtime pipeline.
 *
 * ```text
 *   idle ──subscribe──▶ connecting ──onopen──▶ open
 *                            ▲                  │
 *                            │                  │ onclose · onerror · Worker failure
 *                            │                  ▼
 *                            └──── backoff ── retrying ── cadence exhausted ──▶ closed
 *
 *   unsupported  ◀── this environment has no Worker, or no WebSocket
 *   any state    ──last subscriber leaves (dispose)──▶ idle
 * ```
 *
 * `retrying` is a state rather than a synonym for `closed` because the two say different
 * things: one is a Runtime that is coming back, the other is a pipeline that has stopped
 * trying. `closed` is terminal until every subscriber leaves — a bounded cadence that
 * ended is a fact, not a pause.
 */
export type StreamConnection =
  | "idle"
  | "connecting"
  | "open"
  | "retrying"
  | "closed"
  | "unsupported";

/**
 * The bounded reconnect cadence, in milliseconds.
 *
 * Each entry is the wait *before* its attempt, so the first retry is never immediate and
 * no wait is shorter than the one before it. The list is finite by construction: after
 * {@link RETRY_DELAYS_MS}`.length` failures the pipeline reports `closed` and stops, which
 * is what makes "bounded" a property of this code rather than of how the failures happen
 * to arrive. There is deliberately no jitter — the cadence is a contract a test can
 * assert, and one local sidecar is not a herd to synchronise.
 */
export const RETRY_DELAYS_MS = [250, 500, 1_000, 2_000, 4_000] as const;

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

/** The state a store holds when it owns no pipeline at all. */
const IDLE_STATE: RealtimeStreamState = {
  connection: "idle",
  streamId: null,
  snapshot: null,
  droppedViewFrames: 0,
  sequenceGaps: 0,
  decodeMs: 0,
};

/**
 * One workspace-owned WebSocket + Worker + bounded store.
 *
 * Trace and Plot are projections of this single store; neither owns a socket or a worker.
 * Consumers are reference-counted, so the pipeline is opened on the first subscriber and
 * released exactly once when the last one leaves.
 *
 * The socket and the Worker are **one pipeline, not two resources**. They are created
 * together, released together, and a failure of either is a failure of the pair: a socket
 * delivering frames into a Worker that cannot decode them is not a working stream, and a
 * Worker that cannot run is not a working stream either. That is why `dispose`, a drop and
 * a Worker error all end in the same place — both handles released, nothing left
 * half-alive.
 *
 * Recovery is explicit and bounded rather than a bare "closed":
 *
 * ```text
 *   a failure                  → release the pair, emit `retrying`, schedule the next attempt
 *   the timer fires            → create a fresh pair, emit `connecting`
 *   the cadence is exhausted   → emit `closed`, and stop
 *   the last subscriber leaves → release everything, emit `idle`, cancel the timer
 * ```
 *
 * Every handler installed for a pair captures the generation it was installed under and
 * ignores anything once that generation has moved on, so a late `open` from a socket that
 * has already been replaced cannot claim the connection its replacement owns.
 */
export class RealtimeStreamStore {
  #state: RealtimeStreamState = { ...IDLE_STATE };
  readonly #listeners = new Set<() => void>();
  #worker: Worker | null = null;
  #socket: WebSocket | null = null;
  #retryTimer: ReturnType<typeof setTimeout> | null = null;
  /**
   * Which pipeline the installed handlers belong to.
   *
   * Advanced every time a pair is created *and* every time one is released, so a handler
   * from a superseded socket or Worker is recognisable as stale even though it is still
   * attached to a live object.
   */
  #generation = 0;
  /** Failures since the last successful open: drives both the cadence and the cap. */
  #attempts = 0;

  constructor(private readonly deps: RealtimeStreamDeps) {}

  getState = (): RealtimeStreamState => this.#state;

  get consumerCount(): number {
    return this.#listeners.size;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    this.#connect();
    return () => {
      this.#listeners.delete(listener);
      if (this.#listeners.size === 0) this.dispose();
    };
  };

  /**
   * Release the pipeline, cancel any pending retry and return to `idle`.
   *
   * Final for as long as nobody subscribes again: no attached handler, no pending timer
   * and no event already in flight can open a socket after this returns, which is what
   * makes "unmount stops the pipeline" a property rather than a hope.
   */
  dispose(): void {
    this.#release();
    this.#state = { ...IDLE_STATE };
    this.#emit();
  }

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

  /**
   * Open a pipeline if this store should have one and does not.
   *
   * `idle` is the only state a *new* pipeline may be opened from: `retrying` already owns
   * a pending timer, `connecting` and `open` already own a pair, `unsupported` is this
   * environment's verdict, and `closed` is the cadence having been spent. A subscriber
   * arriving in any of those states is joining what exists (or what was decided), not
   * resetting it.
   */
  #connect(): void {
    if (this.#listeners.size === 0) return;
    if (this.#state.connection !== "idle") return;
    this.#attempts = 0;
    this.#open();
  }

  /** Create one socket-and-Worker pair and install its generation-guarded handlers. */
  #open(): void {
    const generation = ++this.#generation;
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
      if (generation !== this.#generation) return;
      const snapshot = readViewportSnapshot(event.data);
      if (snapshot !== null) {
        this.#publish(snapshot);
        return;
      }
      // The frame Worker reports a decode failure as a message rather than by throwing,
      // so an unread `error` message would be the one failure this store never hears.
      if (isWorkerFailure(event.data)) this.#fail(generation);
    };
    worker.onerror = () => this.#fail(generation);
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      if (generation !== this.#generation) return;
      this.#attempts = 0;
      this.#patch({ connection: "open" });
    };
    socket.onclose = () => this.#fail(generation);
    socket.onerror = () => this.#fail(generation);
    socket.onmessage = (event: MessageEvent) => {
      if (generation !== this.#generation) return;
      const payload = event.data as ArrayBuffer;
      worker.postMessage({ type: "batch", payload }, [payload]);
    };
  }

  /**
   * The pipeline that owns `generation` failed: release it and decide what comes next.
   *
   * Ignoring a stale generation is what keeps a late event from a replaced socket out of
   * the current connection's story. Ignoring the zero-subscriber case is what keeps a
   * failure that arrives after unmount from opening anything.
   */
  #fail(generation: number): void {
    if (generation !== this.#generation) return;
    if (this.#listeners.size === 0) return;
    const worker = this.#worker;
    const socket = this.#socket;
    this.#worker = null;
    this.#socket = null;
    // The pair being released is not the current generation any more, so an event it
    // delivers from here on (a `close` after an `error`, say) is ignored rather than
    // counted as a second failure.
    this.#generation += 1;
    socket?.close();
    worker?.terminate();

    const delay = RETRY_DELAYS_MS[this.#attempts];
    if (delay === undefined) {
      this.#patch({ connection: "closed" });
      return;
    }
    this.#attempts += 1;
    this.#patch({ connection: "retrying" });
    this.#retryTimer = setTimeout(() => {
      this.#retryTimer = null;
      if (this.#listeners.size === 0) return;
      if (this.#state.connection !== "retrying") return;
      this.#open();
    }, delay);
  }

  #release(): void {
    if (this.#retryTimer !== null) {
      clearTimeout(this.#retryTimer);
      this.#retryTimer = null;
    }
    this.#generation += 1;
    const worker = this.#worker;
    const socket = this.#socket;
    this.#worker = null;
    this.#socket = null;
    this.#attempts = 0;
    socket?.close();
    worker?.terminate();
  }
}

function readViewportSnapshot(value: unknown): FrameViewportSnapshot | null {
  if (typeof value !== "object" || value === null || !("type" in value)) return null;
  const candidate = value as { type: unknown; snapshot?: FrameViewportSnapshot };
  if (candidate.type !== "viewport" || candidate.snapshot === undefined) return null;
  return candidate.snapshot;
}

/**
 * Whether a Worker message is the Worker's own failure report.
 *
 * `frame-worker.ts` catches a per-batch decode failure and posts `{type: "error"}` before
 * carrying on, so this is the shape the pipeline has to recognise to keep that failure
 * from being silently dropped.
 */
function isWorkerFailure(value: unknown): boolean {
  if (typeof value !== "object" || value === null || !("type" in value)) return false;
  return (value as { type: unknown }).type === "error";
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
