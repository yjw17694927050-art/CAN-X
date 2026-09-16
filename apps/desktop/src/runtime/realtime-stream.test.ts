import { describe, expect, it, vi } from "vitest";

import type { FrameViewportSnapshot } from "../workers/frame-worker-core";
import { RealtimeStreamStore } from "./realtime-stream";

class FakeWorker {
  onmessage: ((event: { data: unknown }) => void) | null = null;
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
    const { sockets, store } = harness();
    store.subscribe(() => undefined);
    expect(store.getState().connection).toBe("connecting");

    sockets[0]?.onopen?.();
    expect(store.getState().connection).toBe("open");

    sockets[0]?.onclose?.();
    expect(store.getState().connection).toBe("closed");
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
});
