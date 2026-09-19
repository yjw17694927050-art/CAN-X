import { describe, expect, it, vi } from "vitest";

import type { RuntimeFrame } from "../runtime/frame-schema";
import {
  EMPTY_DECODED_REALTIME,
  createDecodedRealtimeStore,
  type DecodedFrameEntry,
  type DecodedSignal,
} from "./decoded-realtime";

function frame(sequence: bigint, channelId = "can0"): RuntimeFrame {
  return {
    arbitrationId: 0x123,
    bitrateSwitch: false,
    channelId,
    clockDomain: "host",
    data: Uint8Array.from([1, 2, 3, 4, 5, 6, 7, 8]),
    direction: "rx",
    dlc: 8,
    errorStateIndicator: false,
    flags: 0,
    hardwareTimestamp: null,
    hostTimestamp: 1,
    isExtended: false,
    isFd: false,
    normalizedTimestamp: Number(sequence) / 1000,
    sequence,
    timestampQuality: "host",
  };
}

const RPM: DecodedSignal = {
  choiceLabel: null,
  name: "EngineRpm",
  physicalValue: 1234.5,
  rawValue: 4938,
  unit: "rpm",
};

function decoded(sequence: bigint, channelId = "can0"): DecodedFrameEntry {
  return {
    assetId: "asset-a",
    frame: frame(sequence, channelId),
    outcome: { messageName: "EngineSpeed", signals: [RPM], status: "decoded" },
  };
}

function failed(sequence: bigint): DecodedFrameEntry {
  return {
    assetId: "asset-a",
    frame: frame(sequence),
    outcome: { code: "dbc.message_not_found", status: "failed" },
  };
}

function batch(entries: readonly DecodedFrameEntry[], streamId = "stream-1") {
  return { assetId: "asset-a", entries, streamId };
}

describe("DecodedRealtimeStore — construction", () => {
  it("starts empty and creates a fresh store every time", () => {
    const first = createDecodedRealtimeStore();
    const second = createDecodedRealtimeStore();

    expect(first).not.toBe(second);
    expect(first.getSnapshot()).toBe(EMPTY_DECODED_REALTIME);
    expect(first.getSnapshot().entries.size).toBe(0);
    expect(first.getSnapshot().streamId).toBeNull();
    expect(first.getSnapshot().status).toBe("idle");
  });

  it("refuses a capacity that cannot bound anything", () => {
    expect(() => createDecodedRealtimeStore(0)).toThrow(/positive integer capacity/);
    expect(() => createDecodedRealtimeStore(1.5)).toThrow(/positive integer capacity/);
  });
});

describe("DecodedRealtimeStore — applying a batch", () => {
  it("keys outcomes by the frame's own sequence", () => {
    const store = createDecodedRealtimeStore();

    store.applyBatch(batch([decoded(7n), decoded(8n)]));

    expect(store.entryFor(7n)?.outcome.status).toBe("decoded");
    expect(store.entryFor(8n)?.outcome.status).toBe("decoded");
    expect(store.entryFor(9n)).toBeNull();
  });

  it("keeps the ORIGINAL frame object as the identity, not a reconstructed one", () => {
    const store = createDecodedRealtimeStore();
    const original = frame(7n);

    store.applyBatch(
      batch([{ assetId: "asset-a", frame: original, outcome: { messageName: "M", signals: [], status: "decoded" } }]),
    );

    // Identity, not equality: the decoded entry is the same frame the realtime pipeline
    // produced. A JSON round trip must never become the frame's new identity.
    expect(store.entryFor(7n)?.frame).toBe(original);
    expect(store.entryFor(7n)?.frame.sequence).toBe(7n);
  });

  it("records a per-frame failure as data, alongside decoded frames", () => {
    const store = createDecodedRealtimeStore();

    store.applyBatch(batch([decoded(1n), failed(2n), decoded(3n)]));

    expect(store.entryFor(1n)?.outcome.status).toBe("decoded");
    expect(store.entryFor(2n)?.outcome).toStrictEqual({
      code: "dbc.message_not_found",
      status: "failed",
    });
    expect(store.entryFor(3n)?.outcome.status).toBe("decoded");
    // The batch as a whole is not a failure: one frame not matching a message is normal.
    expect(store.getSnapshot().lastError).toBeNull();
    expect(store.getSnapshot().status).toBe("ready");
  });

  it("carries the physical value and the choice label without inventing either", () => {
    const store = createDecodedRealtimeStore();

    store.applyBatch(batch([decoded(1n)]));

    const signal = store.entryFor(1n)?.outcome;
    expect(signal?.status).toBe("decoded");
    if (signal?.status !== "decoded") throw new Error("unreachable");
    expect(signal.messageName).toBe("EngineSpeed");
    expect(signal.signals[0]).toStrictEqual(RPM);
    expect(signal.signals[0]?.physicalValue).toBe(1234.5);
    expect(signal.signals[0]?.rawValue).toBe(4938);
  });

  it("merges a second batch into the same stream", () => {
    const store = createDecodedRealtimeStore();

    store.applyBatch(batch([decoded(1n)]));
    store.applyBatch(batch([decoded(2n)]));

    expect(store.getSnapshot().entries.size).toBe(2);
    expect(store.entryFor(1n)).not.toBeNull();
    expect(store.entryFor(2n)).not.toBeNull();
  });

  it("replaces the entries when the batch belongs to a different stream", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n), decoded(2n)]));

    store.applyBatch(batch([decoded(1n)], "stream-2"));

    // Sequences are only comparable inside one stream; keeping both would conflate two
    // numbering spaces.
    expect(store.getSnapshot().streamId).toBe("stream-2");
    expect(store.getSnapshot().entries.size).toBe(1);
    expect(store.entryFor(1n)?.frame.sequence).toBe(1n);
  });

  it("overwrites an outcome for a sequence it already holds", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n)]));

    store.applyBatch(batch([failed(1n)]));

    expect(store.getSnapshot().entries.size).toBe(1);
    expect(store.entryFor(1n)?.outcome.status).toBe("failed");
  });
});

describe("DecodedRealtimeStore — boundedness", () => {
  it("evicts oldest-first at capacity and counts what it dropped", () => {
    const store = createDecodedRealtimeStore(3);

    store.applyBatch(batch([decoded(1n), decoded(2n), decoded(3n), decoded(4n), decoded(5n)]));

    expect(store.getSnapshot().entries.size).toBe(3);
    expect(store.getSnapshot().droppedEntries).toBe(2);
    expect(store.entryFor(1n)).toBeNull();
    expect(store.entryFor(2n)).toBeNull();
    expect(store.entryFor(3n)).not.toBeNull();
    expect(store.entryFor(5n)).not.toBeNull();
  });

  it("stays bounded across many batches, never growing without limit", () => {
    const store = createDecodedRealtimeStore(10);
    for (let sequence = 0; sequence < 500; sequence += 1) {
      store.applyBatch(batch([decoded(BigInt(sequence))]));
    }

    expect(store.getSnapshot().entries.size).toBe(10);
    expect(store.getSnapshot().droppedEntries).toBe(490);
  });
});

describe("DecodedRealtimeStore — request-level failure", () => {
  it("records the failure without discarding what was already decoded", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n)]));

    store.failRequest("dbc.asset_integrity_failed");

    expect(store.getSnapshot().lastError).toBe("dbc.asset_integrity_failed");
    expect(store.getSnapshot().status).toBe("failed");
    // A request that failed says nothing about the frames an earlier request answered.
    expect(store.entryFor(1n)).not.toBeNull();
  });

  it("clears the recorded failure when the next batch succeeds", () => {
    const store = createDecodedRealtimeStore();
    store.failRequest("dbc.asset_not_found");

    store.applyBatch(batch([decoded(2n)]));

    expect(store.getSnapshot().lastError).toBeNull();
    expect(store.getSnapshot().status).toBe("ready");
  });

  it("does not touch the entries or the stream when a request is in flight", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n)]));
    const before = store.getSnapshot().entries;

    store.markPending();

    expect(store.getSnapshot().status).toBe("pending");
    expect(store.getSnapshot().entries).toBe(before);
  });
});

describe("DecodedRealtimeStore — epochs", () => {
  it("reset discards entries and remembers the new stream", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n)]));

    store.reset("stream-9");

    expect(store.getSnapshot().streamId).toBe("stream-9");
    expect(store.getSnapshot().entries.size).toBe(0);
    expect(store.getSnapshot().droppedEntries).toBe(0);
    expect(store.getSnapshot().status).toBe("idle");
  });

  it("clear discards everything, because the question changed", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n)]));

    store.clear();

    expect(store.getSnapshot()).toBe(EMPTY_DECODED_REALTIME);
  });
});

describe("DecodedRealtimeStore — subscription and identity", () => {
  it("serves several React roots from one store", () => {
    const store = createDecodedRealtimeStore();
    const traceRoot = vi.fn();
    const plotRoot = vi.fn();
    store.subscribe(traceRoot);
    store.subscribe(plotRoot);

    store.applyBatch(batch([decoded(1n)]));

    expect(store.listenerCount).toBe(2);
    expect(traceRoot).toHaveBeenCalledTimes(1);
    expect(plotRoot).toHaveBeenCalledTimes(1);
  });

  it("keeps the snapshot identity stable when nothing changes", () => {
    const store = createDecodedRealtimeStore();
    store.applyBatch(batch([decoded(1n)]));
    const before = store.getSnapshot();

    store.markPending();
    store.markPending();

    expect(store.getSnapshot().status).toBe("pending");
    expect(store.getSnapshot()).not.toBe(before);
  });

  it("publishes nothing for a no-op clear or reset", () => {
    const store = createDecodedRealtimeStore();
    const listener = vi.fn();
    store.subscribe(listener);

    store.clear();
    store.reset(null);

    expect(listener).not.toHaveBeenCalled();
  });
});
