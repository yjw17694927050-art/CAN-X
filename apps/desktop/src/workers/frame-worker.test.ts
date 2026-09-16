import { encode } from "@msgpack/msgpack";
import { describe, expect, it } from "vitest";

import { FrameWorkerCore } from "./frame-worker-core";

function batch(first: bigint, count: number, streamId = "stream-1"): Uint8Array {
  const frames = Array.from({ length: count }, (_, offset) => ({
    sequence: first + BigInt(offset),
    channel_id: "can0",
    arbitration_id: 0x123,
    is_extended: false,
    is_fd: false,
    bitrate_switch: false,
    error_state_indicator: false,
    dlc: 1,
    data: new Uint8Array([offset]),
    direction: "rx",
    hardware_timestamp: null,
    host_timestamp: 100 + offset,
    normalized_timestamp: offset,
    clock_domain: "host.monotonic",
    timestamp_quality: "host",
    flags: 0,
  }));
  return encode(
    {
      schema_version: 1,
      stream_id: streamId,
      first_sequence: first,
      last_sequence: first + BigInt(count - 1),
      frame_count: count,
      frames,
    },
    { useBigInt64: true },
  );
}

describe("FrameWorkerCore", () => {
  it("decodes uint64 sequences without losing precision", () => {
    const core = new FrameWorkerCore(4);
    const sequence = 9_007_199_254_740_993n;

    const snapshot = core.ingest(batch(sequence, 1));

    expect(snapshot?.frames[0]?.sequence).toBe(sequence);
  });

  it("keeps only the newest bounded viewport frames", () => {
    const core = new FrameWorkerCore(3);

    const snapshot = core.ingest(batch(0n, 5));

    expect(snapshot?.frames.map((frame) => frame.sequence)).toEqual([2n, 3n, 4n]);
    expect(snapshot?.droppedViewFrames).toBe(2);
  });

  it("freezes snapshots while ingestion and gap detection continue", () => {
    const core = new FrameWorkerCore(4);
    core.ingest(batch(0n, 2));
    core.freeze();

    expect(core.ingest(batch(4n, 2))).toBeNull();
    const resumed = core.resume();

    expect(resumed.sequenceGaps).toBe(2);
    expect(resumed.frames.map((frame) => frame.sequence)).toEqual([0n, 1n, 4n, 5n]);
  });

  it("governs snapshots without dropping ingestion", () => {
    let now = 0;
    const core = new FrameWorkerCore(10, 20, () => now);
    expect(core.ingest(batch(0n, 1))).not.toBeNull();
    now = 5;
    expect(core.ingest(batch(1n, 1))).toBeNull();
    now = 20;
    expect(core.flush()?.frames.map((frame) => frame.sequence)).toEqual([0n, 1n]);
  });

  it("records a monotonic decode duration for each batch", () => {
    let now = 0;
    const core = new FrameWorkerCore(4, 0, () => (now += 1));

    const snapshot = core.ingest(batch(0n, 1));

    expect(snapshot?.decodeMs).toBeGreaterThan(0);
  });

  it("resets bounded state when a new capture stream begins", () => {
    const core = new FrameWorkerCore(10);
    core.ingest(batch(0n, 2, "first"));
    const snapshot = core.ingest(batch(0n, 1, "second"));
    expect(snapshot?.frames.map((frame) => frame.sequence)).toEqual([0n]);
    expect(snapshot?.sequenceGaps).toBe(0);
  });
});
