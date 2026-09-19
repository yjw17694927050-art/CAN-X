import { describe, expect, it, vi } from "vitest";

import type { FrameViewportSnapshot } from "../workers/frame-worker-core";
import { RETRY_DELAYS_MS, RealtimeStreamStore } from "./realtime-stream";

class FakeWorker {
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  readonly posted: { readonly type: string; readonly payload: ArrayBuffer }[] = [];
  terminated = false;

  postMessage(message: { readonly type: string; readonly payload: ArrayBuffer }): void {
    this.posted.push(message);
  }

  terminate(): void {
    this.terminated = true;
  }
}

class FakeSocket {
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: ArrayBuffer }) => void) | null = null;
  closed = false;

  close(): void {
    this.closed = true;
  }
}

function harness() {
  const workers: FakeWorker[] = [];
  const sockets: FakeSocket[] = [];
  const store = new RealtimeStreamStore({
    url: "ws://test/stream/frames",
    createWorker: () => {
      const worker = new FakeWorker();
      workers.push(worker);
      return worker as unknown as Worker;
    },
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
  });
  return { sockets, store, workers };
}

function snapshot(over: Partial<FrameViewportSnapshot> = {}): FrameViewportSnapshot {
  return { decodeMs: 0, droppedViewFrames: 0, frames: [], sequenceGaps: 0, streamId: "stream-1", ...over };
}

function emitViewport(worker: FakeWorker, value: FrameViewportSnapshot): void {
  worker.onmessage?.({ data: { type: "viewport", snapshot: value } });
}

describe("RealtimeStreamStore", () => {
  it("opens exactly one socket and worker shared by every consumer", () => {
    const { sockets, store, workers } = harness();

    const first = store.subscribe(() => undefined);
    const second = store.subscribe(() => undefined);

    expect(workers).toHaveLength(1);
    expect(sockets).toHaveLength(1);
    expect(sockets[0]?.binaryType).toBe("arraybuffer");
    expect(store.consumerCount).toBe(2);

    first();
    expect(sockets[0]?.closed).toBe(false);

    second();
    expect(sockets[0]?.closed).toBe(true);
    expect(workers[0]?.terminated).toBe(true);
    expect(store.consumerCount).toBe(0);
  });

  it("forwards binary socket frames into the single worker", () => {
    const { sockets, store, workers } = harness();
    store.subscribe(() => undefined);
    const buffer = new ArrayBuffer(4);

    sockets[0]?.onmessage?.({ data: buffer });

    expect(workers[0]?.posted).toEqual([{ type: "batch", payload: buffer }]);
  });

  it("publishes viewport snapshots with stream identity and pressure metrics", () => {
    const { store, workers } = harness();
    const listener = vi.fn();
    store.subscribe(listener);

    emitViewport(
      workers[0] as FakeWorker,
      snapshot({ decodeMs: 2.5, droppedViewFrames: 3, sequenceGaps: 5, streamId: "capture-42" }),
    );

    const state = store.getState();
    expect(state.streamId).toBe("capture-42");
    expect(state.droppedViewFrames).toBe(3);
    expect(state.sequenceGaps).toBe(5);
    expect(state.decodeMs).toBe(2.5);
    expect(state.snapshot).not.toBeNull();
    expect(listener).toHaveBeenCalled();
  });

  it("tracks connection lifecycle transitions", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store } = harness();
      const unsubscribe = store.subscribe(() => undefined);
      expect(store.getState().connection).toBe("connecting");

      sockets[0]?.onopen?.();
      expect(store.getState().connection).toBe("open");

      sockets[0]?.onclose?.();
      expect(store.getState().connection).toBe("retrying");

      unsubscribe();
      expect(store.getState().connection).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports an unsupported connection when the browser lacks Worker or WebSocket", () => {
    const store = new RealtimeStreamStore({
      createSocket: () => null,
      createWorker: () => null,
      url: "ws://test/stream/frames",
    });

    const unsubscribe = store.subscribe(() => undefined);

    expect(store.getState().connection).toBe("unsupported");
    unsubscribe();
    expect(store.getState().connection).toBe("idle");
  });

  it("clears state on teardown and reopens exactly once for the next consumer", () => {
    const { sockets, store, workers } = harness();
    const unsubscribe = store.subscribe(() => undefined);
    emitViewport(workers[0] as FakeWorker, snapshot({ streamId: "first" }));

    unsubscribe();

    expect(store.getState().snapshot).toBeNull();
    expect(store.getState().connection).toBe("idle");

    const resubscribe = store.subscribe(() => undefined);
    expect(workers).toHaveLength(2);
    expect(sockets).toHaveLength(2);
    resubscribe();
  });

  it("terminates a created worker when the socket cannot be created", () => {
    const workers: FakeWorker[] = [];
    const store = new RealtimeStreamStore({
      createSocket: () => null,
      createWorker: () => {
        const worker = new FakeWorker();
        workers.push(worker);
        return worker as unknown as Worker;
      },
      url: "ws://test/stream/frames",
    });

    const unsubscribe = store.subscribe(() => undefined);

    expect(workers).toHaveLength(1);
    expect(workers[0]?.terminated).toBe(true);
    expect(store.getState().connection).toBe("unsupported");
    expect(store.getState().snapshot).toBeNull();
    unsubscribe();
  });

  it("closes a created socket when the worker cannot be created", () => {
    const sockets: FakeSocket[] = [];
    const store = new RealtimeStreamStore({
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      createWorker: () => null,
      url: "ws://test/stream/frames",
    });

    const unsubscribe = store.subscribe(() => undefined);

    expect(sockets).toHaveLength(1);
    expect(sockets[0]?.closed).toBe(true);
    expect(store.getState().connection).toBe("unsupported");
    expect(store.getState().snapshot).toBeNull();
    unsubscribe();
  });

  it("waits a strictly positive, non-decreasing, finite delay before each retry", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store } = harness();
      const unsubscribe = store.subscribe(() => undefined);

      for (const [attempt, delay] of RETRY_DELAYS_MS.entries()) {
        sockets[attempt]?.onclose?.();
        expect(store.getState().connection).toBe("retrying");

        // Not one millisecond before the cadence says: no immediate reconnect loop.
        vi.advanceTimersByTime(delay - 1);
        expect(sockets).toHaveLength(attempt + 1);

        vi.advanceTimersByTime(1);
        expect(sockets).toHaveLength(attempt + 2);
      }

      // The cadence is a property of the constant, not of the test: every wait is real,
      // and no wait is shorter than the one before it.
      expect(RETRY_DELAYS_MS.every((delay) => delay > 0)).toBe(true);
      expect([...RETRY_DELAYS_MS].sort((left, right) => left - right)).toStrictEqual([
        ...RETRY_DELAYS_MS,
      ]);
      unsubscribe();
    } finally {
      vi.useRealTimers();
    }
  });

  it("releases the pair on a drop and ends in an open connection when the Runtime returns", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store, workers } = harness();
      store.subscribe(() => undefined);
      sockets[0]?.onopen?.();
      expect(store.getState().connection).toBe("open");

      sockets[0]?.onclose?.();

      // The dead pair is released rather than left dangling, which is what let the old
      // `#start` gate block every later attempt forever.
      expect(store.getState().connection).toBe("retrying");
      expect(workers[0]?.terminated).toBe(true);
      expect(sockets[0]?.closed).toBe(true);
      expect(workers).toHaveLength(1);
      expect(sockets).toHaveLength(1);

      vi.advanceTimersByTime(RETRY_DELAYS_MS[0]);
      expect(store.getState().connection).toBe("connecting");
      // Paired lifetimes: one Worker per socket, never a new socket over an old Worker.
      expect(sockets).toHaveLength(2);
      expect(workers).toHaveLength(2);

      sockets[1]?.onopen?.();
      expect(store.getState().connection).toBe("open");
      // Recovery is a reconnection of the one pipeline, not a second one.
      expect(store.consumerCount).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores an event from a socket that has already been superseded", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store } = harness();
      store.subscribe(() => undefined);
      const superseded = sockets[0];

      superseded?.onclose?.();
      vi.advanceTimersByTime(RETRY_DELAYS_MS[0]);
      expect(sockets).toHaveLength(2);

      // The old socket's handlers are still installed, but the connection it belonged to
      // is gone: a late `open` from it must not claim the connection the new socket owns.
      superseded?.onopen?.();
      expect(store.getState().connection).toBe("connecting");
      superseded?.onclose?.();
      expect(store.getState().connection).toBe("connecting");
      expect(sockets).toHaveLength(2);

      sockets[1]?.onopen?.();
      expect(store.getState().connection).toBe("open");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops retrying once the cadence is exhausted instead of looping forever", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store } = harness();
      store.subscribe(() => undefined);

      for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
        sockets[attempt]?.onclose?.();
        if (store.getState().connection === "retrying") vi.runOnlyPendingTimers();
      }

      expect(store.getState().connection).toBe("closed");
      expect(sockets).toHaveLength(RETRY_DELAYS_MS.length + 1);

      // Terminal: no timer is left and no further attempt is made, however long the app
      // stays open. The bound is a count, so it cannot depend on machine speed.
      expect(vi.getTimerCount()).toBe(0);
      vi.advanceTimersByTime(600_000);
      expect(sockets).toHaveLength(RETRY_DELAYS_MS.length + 1);
      expect(store.getState().connection).toBe("closed");
    } finally {
      vi.useRealTimers();
    }
  });

  it("performs no reconnect after dispose, however long the app stays open", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store, workers } = harness();
      const unsubscribe = store.subscribe(() => undefined);
      sockets[0]?.onclose?.();
      expect(store.getState().connection).toBe("retrying");

      unsubscribe();

      expect(store.getState().connection).toBe("idle");
      // The pending retry is cancelled with the pipeline, not left to fire into a store
      // whose workspace is gone.
      expect(vi.getTimerCount()).toBe(0);
      expect(workers[0]?.terminated).toBe(true);
      expect(sockets[0]?.closed).toBe(true);

      vi.advanceTimersByTime(600_000);
      expect(sockets).toHaveLength(1);
      expect(workers).toHaveLength(1);
      expect(store.getState().connection).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
  });

  it("turns a Worker error into a defined state and a bounded retry", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store, workers } = harness();
      store.subscribe(() => undefined);
      sockets[0]?.onopen?.();

      workers[0]?.onerror?.();

      expect(store.getState().connection).toBe("retrying");
      expect(workers[0]?.terminated).toBe(true);
      expect(sockets[0]?.closed).toBe(true);
      expect(workers).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not swallow the failure the frame Worker reports in band", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store, workers } = harness();
      store.subscribe(() => undefined);
      sockets[0]?.onopen?.();

      // `frame-worker.ts` catches a decode failure per batch and posts it as a message
      // rather than throwing, so an unread `type: "error"` was the one failure the store
      // never heard about.
      workers[0]?.onmessage?.({ data: { message: "boom", type: "error" } });

      expect(store.getState().connection).toBe("retrying");
    } finally {
      vi.useRealTimers();
    }
  });

  it("never re-publishes a superseded Worker's viewport, and moves the stream on", () => {
    vi.useFakeTimers();
    try {
      const { sockets, store, workers } = harness();
      store.subscribe(() => undefined);
      sockets[0]?.onopen?.();
      emitViewport(workers[0] as FakeWorker, snapshot({ streamId: "epoch-1" }));
      expect(store.getState().streamId).toBe("epoch-1");

      sockets[0]?.onclose?.();
      vi.advanceTimersByTime(RETRY_DELAYS_MS[0]);
      sockets[1]?.onopen?.();

      // A late viewport from the released Worker cannot resurrect the old epoch.
      emitViewport(workers[0] as FakeWorker, snapshot({ streamId: "epoch-1-stale" }));
      expect(store.getState().streamId).toBe("epoch-1");

      // The next epoch's identity does arrive, which is the change the decode coordinator
      // keys its "a stream change is an epoch" invalidation on.
      emitViewport(workers[1] as FakeWorker, snapshot({ streamId: "epoch-2" }));
      expect(store.getState().streamId).toBe("epoch-2");
    } finally {
      vi.useRealTimers();
    }
  });
});
