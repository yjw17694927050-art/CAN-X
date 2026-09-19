import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  DecodeBatchResult,
  DecodeFrameBatchInput,
  DecodedSignalValue,
} from "../runtime/dbc-decode-client";
import type { RuntimeFrame } from "../runtime/frame-schema";
import type { RealtimeStreamState } from "../runtime/realtime-stream";
import type { FrameViewportSnapshot } from "../workers/frame-worker-core";
import { createDecodedRealtimeStore, type DecodedRealtimeStore } from "./decoded-realtime";
import {
  WorkspaceDecodeCoordinator,
  type DecodeRealtimeSource,
  type DecodeSessionSource,
} from "./decode-coordinator";
import { createWorkspaceSession } from "./session";

/** A realtime store that publishes only what a test tells it to. */
class FakeRealtime implements DecodeRealtimeSource {
  #state: RealtimeStreamState = {
    connection: "open",
    decodeMs: 0,
    droppedViewFrames: 0,
    sequenceGaps: 0,
    snapshot: null,
    streamId: null,
  };
  readonly #listeners = new Set<() => void>();

  getState = (): RealtimeStreamState => this.#state;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  publish(snapshot: FrameViewportSnapshot): void {
    this.#state = { ...this.#state, snapshot, streamId: snapshot.streamId };
    for (const listener of this.#listeners) listener();
  }
}

function frame(sequence: number, channelId: string): RuntimeFrame {
  return {
    sequence: BigInt(sequence),
    channelId,
    arbitrationId: 0x123,
    isExtended: false,
    isFd: false,
    bitrateSwitch: false,
    errorStateIndicator: false,
    dlc: 2,
    data: new Uint8Array([sequence % 256, 0]),
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

const SIGNALS: readonly DecodedSignalValue[] = [
  { choiceLabel: null, name: "EngineSpeed", physicalValue: 1500, rawValue: 3000, unit: "rpm" },
];

/** One batch result for the given frames, all decoded (or all failed when `signals` is null). */
function decodedResult(
  streamId: string,
  frames: readonly RuntimeFrame[],
  messageName = "EngineData",
  signals: readonly DecodedSignalValue[] | null = SIGNALS,
): DecodeBatchResult {
  return {
    firstSequence: frames[0]?.sequence ?? 0n,
    frameCount: frames.length,
    lastSequence: frames[frames.length - 1]?.sequence ?? 0n,
    records: frames.map((f) => ({
      frame: f,
      outcome:
        signals === null
          ? { decoded: null, failure: { code: "dbc.message_not_found" } }
          : { decoded: { messageName, signals }, failure: null },
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

interface Harness {
  readonly coordinator: WorkspaceDecodeCoordinator;
  readonly decoded: DecodedRealtimeStore;
  readonly realtime: FakeRealtime;
  readonly requests: DecodeFrameBatchInput[];
  /** Answer the oldest unanswered request. */
  answer(index: number, result: DecodeBatchResult): void;
  /** Reject the request at `index`. */
  fail(index: number, cause: unknown): void;
  readonly session: ReturnType<typeof createWorkspaceSession>;
  stop(): void;
}

/**
 * A coordinator over real session/decoded stores and a decoding function the test drives.
 *
 * `deferred` responses rather than resolved ones: every race this coordinator exists to
 * survive (a project switch, a rebind, a second snapshot) only exists while a request is in
 * flight, so a test that cannot hold a response open cannot observe the behaviour at all.
 */
function harness(): Harness {
  const realtime = new FakeRealtime();
  const session = createWorkspaceSession();
  const decoded = createDecodedRealtimeStore(64);
  const requests: DecodeFrameBatchInput[] = [];
  const pending: Deferred[] = [];
  const coordinator = new WorkspaceDecodeCoordinator({
    decode: (input) => {
      requests.push(input);
      const next = deferred();
      pending.push(next);
      return next.promise;
    },
    decoded,
    realtime,
    session,
  });
  const stop = coordinator.start();
  return {
    answer: (index, result) => {
      pending[index]?.resolve(result);
    },
    coordinator,
    decoded,
    fail: (index, cause) => {
      pending[index]?.reject(cause);
    },
    realtime,
    requests,
    session,
    stop,
  };
}

/** Let every already-resolved promise run its callbacks. */
async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function openProject(session: DecodeSessionSource, path: string): void {
  (session as ReturnType<typeof createWorkspaceSession>).openProject({
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

describe("WorkspaceDecodeCoordinator", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("issues no decode request while no project is open", async () => {
    const h = harness();

    // With no project open no binding can exist, so no channel resolves to an asset and the
    // viewport is not decodable however many frames arrive.
    h.realtime.publish(snapshot([frame(1, "can0")]));
    await settle();

    expect(h.requests).toEqual([]);
    expect(h.session.getSnapshot().openedProject).toBeNull();
    h.stop();
  });

  it("issues no decode request for a channel with no binding", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");

    h.realtime.publish(snapshot([frame(1, "can1")]));
    await settle();

    expect(h.requests).toEqual([]);
    h.stop();
  });

  it("partitions interleaved assets into one request per contiguous run, one at a time", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");
    h.session.bindChannel("can1", "assetB");
    const frames = [
      frame(1, "can0"),
      frame(2, "can1"),
      frame(3, "can0"),
      frame(4, "can0"),
      frame(5, "can1"),
    ];

    h.realtime.publish(snapshot(frames));
    await settle();

    // Exactly one request is in flight; the coordinator never fans out.
    expect(h.requests).toHaveLength(1);
    expect(h.requests[0]?.assetId).toBe("assetA");
    expect(h.requests[0]?.frames.map((f) => f.sequence)).toEqual([1n]);

    h.answer(0, decodedResult("s1", [frame(1, "can0")]));
    await settle();

    expect(h.requests).toHaveLength(2);
    expect(h.requests[1]?.assetId).toBe("assetB");
    expect(h.requests[1]?.frames.map((f) => f.sequence)).toEqual([2n]);

    h.answer(1, decodedResult("s1", [frame(2, "can1")]));
    await settle();

    expect(h.requests).toHaveLength(3);
    expect(h.requests[2]?.assetId).toBe("assetA");
    // The pair 3,4 is contiguous within assetA, so it travels as one batch.
    expect(h.requests[2]?.frames.map((f) => f.sequence)).toEqual([3n, 4n]);

    h.answer(2, decodedResult("s1", [frame(3, "can0"), frame(4, "can0")]));
    await settle();

    expect(h.requests).toHaveLength(4);
    expect(h.requests[3]?.assetId).toBe("assetB");
    expect(h.requests[3]?.frames.map((f) => f.sequence)).toEqual([5n]);

    h.answer(3, decodedResult("s1", [frame(5, "can1")]));
    await settle();
    h.stop();
  });

  it("records decoded outcomes in the store, keyed by the submitted frame's sequence", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");
    const one = frame(1, "can0");

    h.realtime.publish(snapshot([one]));
    await settle();
    h.answer(0, decodedResult("s1", [one]));
    await settle();

    const entry = h.decoded.entryFor(1n);
    expect(entry).not.toBeNull();
    expect(entry?.assetId).toBe("assetA");
    // The submitted frame object itself, not a reconstruction from the response.
    expect(entry?.frame).toBe(one);
    expect(entry?.outcome).toEqual({
      messageName: "EngineData",
      signals: [
        { choiceLabel: null, name: "EngineSpeed", physicalValue: 1500, rawValue: 3000, unit: "rpm" },
      ],
      status: "decoded",
    });
    h.stop();
  });

  it("keeps at most one request in flight when snapshots arrive faster than answers", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");

    h.realtime.publish(snapshot([frame(1, "can0")]));
    await settle();
    h.realtime.publish(snapshot([frame(1, "can0"), frame(2, "can0")]));
    await settle();
    h.realtime.publish(snapshot([frame(1, "can0"), frame(2, "can0"), frame(3, "can0")]));
    await settle();

    expect(h.requests).toHaveLength(1);

    h.answer(0, decodedResult("s1", [frame(1, "can0")]));
    await settle();

    // The next request carries the newest viewport's still-unprocessed frames, not a queue of
    // the snapshots it skipped: 1 is processed, so 2 and 3 remain.
    expect(h.requests).toHaveLength(2);
    expect(h.requests[1]?.frames.map((f) => f.sequence)).toEqual([2n, 3n]);
    h.stop();
  });

  it("decodes each sequence once, however often the same viewport is published", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");
    const frames = [frame(1, "can0"), frame(2, "can0"), frame(3, "can0")];

    h.realtime.publish(snapshot(frames));
    await settle();
    h.answer(0, decodedResult("s1", frames));
    await settle();

    h.realtime.publish(snapshot(frames));
    await settle();
    h.realtime.publish(snapshot(frames));
    await settle();

    expect(h.requests).toHaveLength(1);
    h.stop();
  });

  it("marks a per-frame failure processed, so it is never requested again", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");
    const frames = [frame(1, "can0")];

    h.realtime.publish(snapshot(frames));
    await settle();
    h.answer(0, decodedResult("s1", frames, "EngineData", null));
    await settle();

    expect(h.decoded.entryFor(1n)?.outcome).toEqual({
      code: "dbc.message_not_found",
      status: "failed",
    });

    h.realtime.publish(snapshot(frames));
    await settle();

    expect(h.requests).toHaveLength(1);
    h.stop();
  });

  it("drops a response that belongs to a project the user has left", async () => {
    const h = harness();
    openProject(h.session, "/p1");
    h.session.bindChannel("can0", "assetA");

    h.realtime.publish(snapshot([frame(1, "can0")]));
    await settle();
    expect(h.requests).toHaveLength(1);

    openProject(h.session, "/p2");
    await settle();
    h.answer(0, decodedResult("s1", [frame(1, "can0")]));
    await settle();

    // The outcome describes a project that is no longer open.
    expect(h.decoded.entryFor(1n)).toBeNull();
    expect(h.decoded.getSnapshot().entries.size).toBe(0);
    h.stop();
  });

  it("drops a response whose channel was rebound while it was in flight", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");

    h.realtime.publish(snapshot([frame(1, "can0")]));
    await settle();

    h.session.bindChannel("can0", "assetB");
    await settle();
    h.answer(0, decodedResult("s1", [frame(1, "can0")]));
    await settle();

    expect(h.decoded.entryFor(1n)).toBeNull();
    h.stop();
  });

  it("records a request-level failure without clearing what was already decoded", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");
    const first = [frame(1, "can0")];

    h.realtime.publish(snapshot(first));
    await settle();
    h.answer(0, decodedResult("s1", first));
    await settle();
    expect(h.decoded.entryFor(1n)).not.toBeNull();

    h.realtime.publish(snapshot([frame(1, "can0"), frame(2, "can0")]));
    await settle();
    h.fail(1, new Error("Runtime unreachable"));
    await settle();

    expect(h.decoded.getSnapshot().lastError).not.toBeNull();
    // The earlier per-frame outcome survives a later request-level failure.
    expect(h.decoded.entryFor(1n)).not.toBeNull();
    h.stop();
  });

  it("does not retry the same failed viewport forever", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");
    const frames = [frame(1, "can0")];

    h.realtime.publish(snapshot(frames));
    await settle();
    h.fail(0, new Error("Runtime unreachable"));
    await settle();

    h.realtime.publish(snapshot(frames));
    await settle();
    h.realtime.publish(snapshot(frames));
    await settle();

    expect(h.requests).toHaveLength(1);
    h.stop();
  });

  it("starts a new epoch when the stream changes, forgetting the old numbering", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");

    h.realtime.publish(snapshot([frame(1, "can0")], "s1"));
    await settle();
    h.answer(0, decodedResult("s1", [frame(1, "can0")]));
    await settle();
    expect(h.decoded.entryFor(1n)).not.toBeNull();

    h.realtime.publish(snapshot([frame(1, "can0")], "s2"));
    await settle();

    // A sequence from another stream says nothing about this one.
    expect(h.decoded.entryFor(1n)).toBeNull();
    expect(h.requests).toHaveLength(2);
    expect(h.requests[1]?.streamId).toBe("s2");
    h.stop();
  });

  it("stops issuing requests once disposed", async () => {
    const h = harness();
    openProject(h.session, "/p");
    h.session.bindChannel("can0", "assetA");

    h.stop();
    h.realtime.publish(snapshot([frame(1, "can0")]));
    await settle();

    expect(h.requests).toEqual([]);
  });
});
