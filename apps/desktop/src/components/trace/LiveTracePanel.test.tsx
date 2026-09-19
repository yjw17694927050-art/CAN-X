import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "../../i18n/config";
import type { RuntimeFrame } from "../../runtime/frame-schema";
import { RealtimeStreamStore } from "../../runtime/realtime-stream";
import type { FrameViewportSnapshot } from "../../workers/frame-worker-core";
import {
  createDecodedRealtimeStore,
  type DecodeOutcome,
  type DecodedRealtimeStore,
} from "../../workspace/decoded-realtime";
import { LiveTracePanel } from "./LiveTracePanel";

const STREAM_ID = "s1";

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
  return { decodeMs: 0, droppedViewFrames: 0, frames, sequenceGaps: 0, streamId: STREAM_ID };
}

function publish(workers: FakeWorker[], frames: readonly RuntimeFrame[]): void {
  act(() => {
    workers[0]?.onmessage?.({ data: { type: "viewport", snapshot: snapshot(frames) } });
  });
}

function messageOutcome(messageName: string): DecodeOutcome {
  return {
    messageName,
    signals: [
      { choiceLabel: null, name: "EngineSpeed", physicalValue: 1234, rawValue: 617, unit: "rpm" },
    ],
    status: "decoded",
  };
}

/**
 * Answer one sequence.
 *
 * The entry carries a *reconstructed* frame object — a fresh instance with the
 * same `sequence`, exactly as a decode response would — so these tests only pass
 * if the panel joins outcomes to frames by sequence and not by object identity.
 */
function answer(decoded: DecodedRealtimeStore, sequence: number, outcome: DecodeOutcome): void {
  act(() => {
    decoded.applyBatch({
      assetId: "asset-1",
      entries: [{ assetId: "asset-1", frame: frame(sequence), outcome }],
      streamId: STREAM_ID,
    });
  });
}

/** Every rendered data row, as its cell texts: [timestamp … direction, message, signals]. */
function dataRows(): string[][] {
  return screen
    .getAllByRole("row")
    .filter((row) => row.getAttribute("data-trace-row") !== null)
    .map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent ?? ""),
    );
}

const MESSAGE = 6;
const SIGNALS = 7;

/** frame 0 decoded as "First", then frozen, then the world moves on. */
function frozenScript() {
  const { store, workers } = harness();
  const decoded = createDecodedRealtimeStore();
  render(<LiveTracePanel decoded={decoded} store={store} />);

  publish(workers, [frame(0)]);
  answer(decoded, 0, messageOutcome("First"));
  fireEvent.click(screen.getByRole("button", { name: "Freeze" }));
  publish(workers, [frame(0), frame(7)]);
  answer(decoded, 0, messageOutcome("Second"));
  return { decoded, store, workers };
}

describe("LiveTracePanel decoded integration", () => {
  it("renders raw rows with empty decoded cells when no decoded store is injected", async () => {
    const { store, workers } = harness();
    render(<LiveTracePanel store={store} />);

    publish(workers, [frame(0)]);

    expect(await screen.findByText("01 00")).toBeInTheDocument();
    expect(dataRows()).toHaveLength(1);
    expect(dataRows()[0]?.[MESSAGE]).toBe("—");
    expect(dataRows()[0]?.[SIGNALS]).toBe("—");
  });

  it("fills the decoded cells from the shared decoded store as it updates", async () => {
    const { store, workers } = harness();
    const decoded = createDecodedRealtimeStore();
    render(<LiveTracePanel decoded={decoded} store={store} />);

    publish(workers, [frame(0)]);
    expect(await screen.findByText("01 00")).toBeInTheDocument();
    expect(dataRows()[0]?.[MESSAGE]).toBe("—");

    answer(decoded, 0, messageOutcome("EngineData"));

    expect(await screen.findByText("EngineData")).toBeInTheDocument();
    expect(dataRows()[0]?.[MESSAGE]).toBe("EngineData");
    expect(dataRows()[0]?.[SIGNALS]).toBe("EngineSpeed=1234 rpm");
  });

  it("joins an outcome to its frame by sequence, not by position", async () => {
    const { store, workers } = harness();
    const decoded = createDecodedRealtimeStore();
    render(<LiveTracePanel decoded={decoded} store={store} />);

    publish(workers, [frame(0), frame(1)]);
    expect(await screen.findByText("01 00")).toBeInTheDocument();

    // Only sequence 1 was answered: the first row must stay unresolved.
    answer(decoded, 1, messageOutcome("SecondMessage"));

    expect(await screen.findByText("SecondMessage")).toBeInTheDocument();
    const rows = dataRows();
    expect(rows).toHaveLength(2);
    expect(rows[0]?.[0]).toBe("0.000000");
    expect(rows[0]?.[MESSAGE]).toBe("—");
    expect(rows[1]?.[0]).toBe("0.001000");
    expect(rows[1]?.[MESSAGE]).toBe("SecondMessage");
  });

  it("freezes the raw frames and their decoded projection together", async () => {
    const { store, workers } = harness();
    const decoded = createDecodedRealtimeStore();
    render(<LiveTracePanel decoded={decoded} store={store} />);

    publish(workers, [frame(0)]);
    answer(decoded, 0, messageOutcome("First"));
    expect(await screen.findByText("First")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Freeze" }));
    publish(workers, [frame(0), frame(7)]);

    // The shared store advanced past the freeze...
    expect(store.getState().snapshot?.frames).toHaveLength(2);
    // ...but the frozen Trace still shows exactly the one row it captured, with
    // the decoded projection that was current at that moment.
    expect(dataRows()).toHaveLength(1);
    expect(dataRows()[0]?.[0]).toBe("0.000000");
    expect(dataRows()[0]?.[MESSAGE]).toBe("First");
  });

  it("leaves a frozen row untouched when a late decode response arrives", async () => {
    frozenScript();

    // The late answer belongs to the shared store — the shared store really did
    // move on...
    expect(await screen.findByText("First")).toBeInTheDocument();

    // ...but the frozen line is not rewritten by it.
    expect(dataRows()[0]?.[MESSAGE]).toBe("First");
    expect(screen.queryByText("Second")).not.toBeInTheDocument();
  });

  it("shows the latest decoded state again after returning to follow", async () => {
    const { store } = frozenScript();
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(screen.queryByText("Second")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Follow" }));

    expect(await screen.findByText("Second")).toBeInTheDocument();
    expect(dataRows()).toHaveLength(2);
    expect(dataRows()[0]?.[MESSAGE]).toBe("Second");
    expect(screen.queryByText("First")).not.toBeInTheDocument();
    // Freeze never touched the shared capture path.
    expect(store.getState().snapshot?.frames).toHaveLength(2);
  });
});
