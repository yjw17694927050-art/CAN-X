import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "../i18n/config";
import type { RuntimeFrame } from "../runtime/frame-schema";
import { RealtimeStreamStore } from "../runtime/realtime-stream";
import type { FrameViewportSnapshot } from "../workers/frame-worker-core";
import { LivePlotPanel } from "./plot/LivePlotPanel";
import { LiveTracePanel } from "./trace/LiveTracePanel";

class FakeWorker {
  onmessage: ((event: { data: unknown }) => void) | null = null;

  postMessage(): void {}

  terminate(): void {}
}

class FakeSocket {
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: ArrayBuffer }) => void) | null = null;

  close(): void {}
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

function frame(sequence: number): RuntimeFrame {
  return {
    arbitrationId: 0x123,
    bitrateSwitch: false,
    channelId: "can0",
    clockDomain: "host.monotonic",
    data: new Uint8Array([0x01, sequence % 256]),
    direction: "rx",
    dlc: 2,
    errorStateIndicator: false,
    flags: 0,
    hardwareTimestamp: null,
    hostTimestamp: 100 + sequence,
    isExtended: false,
    isFd: false,
    normalizedTimestamp: sequence / 1000,
    sequence: BigInt(sequence),
    timestampQuality: "host",
  };
}

function snapshot(frames: readonly RuntimeFrame[]): FrameViewportSnapshot {
  return { decodeMs: 0, droppedViewFrames: 0, frames, sequenceGaps: 0, streamId: "s1" };
}

describe("shared realtime providers", () => {
  it("serves Trace and Plot from a single shared socket and worker", () => {
    const { sockets, store, workers } = harness();

    render(<LiveTracePanel store={store} />);
    render(<LivePlotPanel store={store} />);

    expect(store.consumerCount).toBe(2);
    expect(sockets).toHaveLength(1);
    expect(workers).toHaveLength(1);
  });

  it("keeps a Trace-only freeze cursor while the shared store keeps updating", async () => {
    const { store, workers } = harness();
    render(<LiveTracePanel store={store} />);

    act(() => {
      workers[0]?.onmessage?.({ data: { type: "viewport", snapshot: snapshot([frame(0)]) } });
    });
    expect(await screen.findByText("01 00")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Freeze" }));

    act(() => {
      workers[0]?.onmessage?.({ data: { type: "viewport", snapshot: snapshot([frame(7)]) } });
    });

    // The shared store advanced past the freeze...
    expect(store.getState().snapshot?.frames[0]?.sequence).toBe(7n);
    // ...but the frozen Trace still shows the viewport it captured.
    expect(screen.getByText("01 00")).toBeInTheDocument();
    expect(screen.queryByText("01 07")).not.toBeInTheDocument();
  });
});
