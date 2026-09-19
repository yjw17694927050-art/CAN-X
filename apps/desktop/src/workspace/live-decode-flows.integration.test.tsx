import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import "../i18n/config";
import { LiveTracePanel } from "../components/trace/LiveTracePanel";
import type {
  DecodeBatchResult,
  DecodeFrameBatchInput,
  DecodedSignalValue,
} from "../runtime/dbc-decode-client";
import type { RuntimeFrame } from "../runtime/frame-schema";
import { RealtimeStreamStore } from "../runtime/realtime-stream";
import type { FrameViewportSnapshot } from "../workers/frame-worker-core";
import { createDecodedRealtimeStore, type DecodedRealtimeStore } from "./decoded-realtime";
import { WorkspaceDecodeCoordinator } from "./decode-coordinator";
import { createWorkspaceSession, type WorkspaceSessionStore } from "./session";
import { buildSignalSeries, DEFAULT_MAX_SAMPLES, type PlotSample } from "../components/plot/series";

/**
 * The V0.3-14 integration flows — the whole chain, with real stores and only the two edges
 * (the socket above, the Runtime below) stood in for.
 *
 * ```text
 * FakeSocket → RealtimeStreamStore → WorkspaceDecodeCoordinator → DecodedRealtimeStore
 *                (real, one pipeline)         (real)                   (real)
 *                                                  ↓ decode (deferred in the test)
 *                                          LiveTracePanel (real React) + buildSignalSeries
 * ```
 *
 * The fakes are exactly the two things a test cannot own: the WebSocket and the HTTP call.
 * Everything between them is the production object graph, because the defects this file
 * exists to catch — a second pipeline, a stale response published, a frozen row rewritten —
 * live in the *composition*, and a test that mocked the composition would not see them.
 */

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

function frame(sequence: number, channelId = "can0"): RuntimeFrame {
  return {
    sequence: BigInt(sequence),
    channelId,
    arbitrationId: 0x123,
    isExtended: false,
    isFd: false,
    bitrateSwitch: false,
    errorStateIndicator: false,
    dlc: 2,
    data: new Uint8Array([0x01, sequence % 256]),
    direction: "rx",
    hardwareTimestamp: null,
    hostTimestamp: 100 + sequence,
    normalizedTimestamp: sequence / 1000,
    clockDomain: "host.monotonic",
    timestampQuality: "host",
    flags: 0,
  };
}

function snapshot(frames: readonly RuntimeFrame[], streamId = "s1"): FrameViewportSnapshot {
  return { decodeMs: 0, droppedViewFrames: 0, frames, sequenceGaps: 0, streamId };
}

const RPM: readonly DecodedSignalValue[] = [
  { choiceLabel: null, name: "EngineSpeed", physicalValue: 1500, rawValue: 3000, unit: "rpm" },
];

function decoded(
  streamId: string,
  frames: readonly RuntimeFrame[],
  signals: readonly DecodedSignalValue[] = RPM,
): DecodeBatchResult {
  return {
    firstSequence: frames[0]?.sequence ?? 0n,
    frameCount: frames.length,
    lastSequence: frames[frames.length - 1]?.sequence ?? 0n,
    records: frames.map((f) => ({
      frame: f,
      outcome: { decoded: { messageName: "EngineData", signals }, failure: null },
    })),
    streamId,
  };
}

interface Deferred {
  readonly promise: Promise<DecodeBatchResult>;
  reject(cause: unknown): void;
  resolve(result: DecodeBatchResult): void;
}

function deferred(): Deferred {
  let resolve!: (result: DecodeBatchResult) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<DecodeBatchResult>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, reject, resolve };
}

interface Chain {
  readonly decoded: DecodedRealtimeStore;
  readonly realtime: RealtimeStreamStore;
  readonly requests: DecodeFrameBatchInput[];
  readonly session: WorkspaceSessionStore;
  readonly sockets: FakeSocket[];
  readonly workers: FakeWorker[];
  answer(index: number, result: DecodeBatchResult): void;
  fail(index: number, cause: unknown): void;
  /** Push a viewport as the Worker would, once the response has been set up. */
  push(frames: readonly RuntimeFrame[], streamId?: string): void;
  stop(): void;
}

/**
 * The production object graph, with only the socket and the Runtime call replaced.
 *
 * No React tree is installed here: the coordinator and the stores are exercised on their
 * own, and a test that wants to see what a panel renders mounts one against these stores.
 */
function chain(): Chain {
  const sockets: FakeSocket[] = [];
  const workers: FakeWorker[] = [];
  const realtime = new RealtimeStreamStore({
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    createWorker: () => {
      const worker = new FakeWorker();
      workers.push(worker);
      return worker as unknown as Worker;
    },
    url: "ws://test/stream/frames",
  });
  const session = createWorkspaceSession();
  const decodedStore = createDecodedRealtimeStore(4096);
  const requests: DecodeFrameBatchInput[] = [];
  const pending: Deferred[] = [];
  const coordinator = new WorkspaceDecodeCoordinator({
    decode: (input) => {
      requests.push(input);
      const next = deferred();
      pending.push(next);
      return next.promise;
    },
    decoded: decodedStore,
    realtime,
    session,
  });
  const stop = coordinator.start();
  return {
    answer: (index, result) => {
      pending[index]?.resolve(result);
    },
    decoded: decodedStore,
    fail: (index, cause) => {
      pending[index]?.reject(cause);
    },
    push: (frames, streamId = "s1") => {
      act(() => {
        workers[0]?.onmessage?.({ data: { type: "viewport", snapshot: snapshot(frames, streamId) } });
      });
    },
    realtime,
    requests,
    session,
    sockets,
    stop,
    workers,
  };
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function openProject(session: WorkspaceSessionStore, path: string): void {
  session.openProject({
    project: {
      createdAt: "2026-09-19T00:00:00+00:00",
      displayName: "P",
      projectId: "p1",
      schemaVersion: 1,
      updatedAt: "2026-09-19T00:00:00+00:00",
    },
    projectPath: path,
  });
}

function samplesFor(decodedStore: DecodedRealtimeStore, channelId = "can0"): readonly PlotSample[] {
  return buildSignalSeries(
    decodedStore.getSnapshot().entries.values(),
    {
      assetId: "assetA",
      channelId,
      messageName: "EngineData",
      signalName: "EngineSpeed",
      unit: "rpm",
    },
    DEFAULT_MAX_SAMPLES,
  );
}

describe("V0.3-14 — flow 1: the whole chain, frame to plotted signal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("decodes a frame, shows it in Trace, and plots its physical value", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");
    render(<LiveTracePanel decoded={c.decoded} store={c.realtime} />);

    const one = frame(1);
    c.push([one]);
    await settle();
    c.answer(0, decoded("s1", [one]));
    await settle();

    // The store knows the frame.
    expect(c.decoded.entryFor(1n)?.outcome).toEqual({
      messageName: "EngineData",
      signals: RPM,
      status: "decoded",
    });

    // Trace shows the message and the engineering form of the value.
    expect(await screen.findByText("EngineData")).toBeInTheDocument();
    expect(screen.getByText("EngineSpeed=1500 rpm")).toBeInTheDocument();
    // The raw payload column is still there, unchanged.
    expect(screen.getByText("01 01")).toBeInTheDocument();

    // Plot's series is the decoded physical value on the frame's own timestamp.
    expect(samplesFor(c.decoded)).toEqual([{ time: 0.001, value: 1500 }]);
    c.stop();
  });

  it("plots the selected channel's signal only, never another channel's", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");
    c.session.bindChannel("can1", "assetA");

    const onCan0 = frame(1, "can0");
    const onCan1 = frame(2, "can1");
    c.push([onCan0, onCan1]);
    await settle();

    c.answer(0, decoded("s1", [onCan0], [{ choiceLabel: null, name: "EngineSpeed", physicalValue: 1500, rawValue: 3000, unit: "rpm" }]));
    await settle();
    c.answer(1, decoded("s1", [onCan1], [{ choiceLabel: null, name: "EngineSpeed", physicalValue: 2000, rawValue: 4000, unit: "rpm" }]));
    await settle();

    expect(samplesFor(c.decoded, "can0")).toEqual([{ time: 0.001, value: 1500 }]);
    expect(samplesFor(c.decoded, "can1")).toEqual([{ time: 0.002, value: 2000 }]);
    c.stop();
  });
});

describe("V0.3-14 — flow 2: interleaved assets are batched by contiguous run", () => {
  it("submits A[1] B[2] A[3,4] B[5], never A[1,3,4]", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");
    c.session.bindChannel("can1", "assetB");

    const frames = [
      frame(1, "can0"),
      frame(2, "can1"),
      frame(3, "can0"),
      frame(4, "can0"),
      frame(5, "can1"),
    ];
    c.push(frames);
    await settle();

    // Answer each run in turn; the coordinator serialises them.
    c.answer(0, decoded("s1", [frames[0] as RuntimeFrame]));
    await settle();
    c.answer(1, decoded("s1", [frames[1] as RuntimeFrame]));
    await settle();
    c.answer(2, decoded("s1", [frames[2] as RuntimeFrame, frames[3] as RuntimeFrame]));
    await settle();
    c.answer(3, decoded("s1", [frames[4] as RuntimeFrame]));
    await settle();

    expect(
      c.requests.map((request) => `${request.assetId}[${request.frames.map((f) => f.sequence).join(",")}]`),
    ).toEqual(["assetA[1]", "assetB[2]", "assetA[3,4]", "assetB[5]"]);
    c.stop();
  });
});

describe("V0.3-14 — flow 3: a response from a project the user has left", () => {
  it("is discarded, and the new project starts clean", async () => {
    const c = chain();
    openProject(c.session, "/p1");
    c.session.bindChannel("can0", "assetA");

    const one = frame(1);
    c.push([one]);
    await settle();
    expect(c.requests).toHaveLength(1);

    openProject(c.session, "/p2");
    await settle();
    c.answer(0, decoded("s1", [one]));
    await settle();

    expect(c.decoded.entryFor(1n)).toBeNull();
    expect(c.decoded.getSnapshot().entries.size).toBe(0);
    c.stop();
  });
});

describe("V0.3-14 — flow 4: a response superseded by a rebind", () => {
  it("is ignored, and the next binding's decode is accepted", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");

    const one = frame(1);
    c.push([one]);
    await settle();

    c.session.bindChannel("can0", "assetB");
    await settle();
    c.answer(0, decoded("s1", [one]));
    await settle();
    expect(c.decoded.entryFor(1n)).toBeNull();

    // The new binding decodes the same sequence against the new asset.
    c.push([one]);
    await settle();
    expect(c.requests.at(-1)?.assetId).toBe("assetB");
    c.answer(c.requests.length - 1, decoded("s1", [one]));
    await settle();

    expect(c.decoded.entryFor(1n)?.assetId).toBe("assetB");
    c.stop();
  });
});

describe("V0.3-14 — flow 5: backpressure", () => {
  it("never has more than one decode request in flight", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");

    c.push([frame(1)]);
    await settle();
    c.push([frame(1), frame(2)]);
    await settle();
    c.push([frame(1), frame(2), frame(3)]);
    await settle();
    c.push([frame(1), frame(2), frame(3), frame(4)]);
    await settle();

    expect(c.requests).toHaveLength(1);
    c.answer(0, decoded("s1", [frame(1)]));
    await settle();
    expect(c.requests).toHaveLength(2);
    c.stop();
  });
});

describe("V0.3-14 — flow 6: no duplicate decode", () => {
  it("submits each sequence once per binding generation", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");
    const frames = [frame(1), frame(2), frame(3)];

    c.push(frames);
    await settle();
    c.answer(0, decoded("s1", frames));
    await settle();

    c.push(frames);
    await settle();
    c.push(frames);
    await settle();

    expect(c.requests).toHaveLength(1);
    c.stop();
  });
});

describe("V0.3-14 — flow 7: one pipeline, however much the workspace is driven", () => {
  it("does not grow the socket or Worker count across project and binding changes", async () => {
    const c = chain();
    openProject(c.session, "/p1");
    c.session.bindChannel("can0", "assetA");
    render(<LiveTracePanel decoded={c.decoded} store={c.realtime} />);

    c.push([frame(1)]);
    await settle();
    c.answer(0, decoded("s1", [frame(1)]));
    await settle();

    // Every one of these could plausibly rebuild something; none of them may.
    openProject(c.session, "/p2");
    c.session.bindChannel("can0", "assetB");
    c.session.browseAsset("assetB");
    c.session.selectSignal({
      assetId: "assetB",
      channelId: "can0",
      messageName: "EngineData",
      signalName: "EngineSpeed",
      unit: "rpm",
    });
    c.push([frame(2)]);
    await settle();
    c.answer(1, decoded("s1", [frame(2)]));
    await settle();

    expect(c.workers).toHaveLength(1);
    expect(c.sockets).toHaveLength(1);
    c.stop();
  });
});

describe("V0.3-14 — flow 8: Trace freeze captures raw and decoded together", () => {
  it("does not rewrite a frozen row when a late decode response lands", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");
    render(<LiveTracePanel decoded={c.decoded} store={c.realtime} />);

    const one = frame(1);
    c.push([one]);
    await settle();

    // Freeze before the Runtime has answered.
    screen.getByRole("button", { name: "Freeze" }).click();
    await settle();

    c.answer(0, decoded("s1", [one]));
    await settle();

    // The frozen line still shows no decode result for that frame…
    expect(screen.queryByText("EngineData")).not.toBeInTheDocument();
    // …and the encoded payload is still visible, so the row itself is there.
    expect(screen.getByText("01 01")).toBeInTheDocument();
    c.stop();
  });

  it("shows the latest decoded state again after Follow", async () => {
    const c = chain();
    openProject(c.session, "/p");
    c.session.bindChannel("can0", "assetA");
    render(<LiveTracePanel decoded={c.decoded} store={c.realtime} />);

    const one = frame(1);
    c.push([one]);
    await settle();
    screen.getByRole("button", { name: "Freeze" }).click();
    await settle();
    c.answer(0, decoded("s1", [one]));
    await settle();
    expect(screen.queryByText("EngineData")).not.toBeInTheDocument();

    screen.getByRole("button", { name: "Follow" }).click();
    await settle();

    expect(await screen.findByText("EngineData")).toBeInTheDocument();
    c.stop();
  });
});
