import { describe, expect, it } from "vitest";

import type { RuntimeFrame } from "../runtime/frame-schema";
import { partitionDecodeRuns } from "./decode-partition";

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

/** A binding map expressed as the lookup the coordinator actually has. */
function bindings(map: Record<string, string>): (channelId: string) => string | null {
  return (channelId) => map[channelId] ?? null;
}

/** One run, flattened to `assetId[seq, seq, …]` so the expectation reads as the spec. */
function shape(runs: readonly { assetId: string; frames: readonly RuntimeFrame[] }[]): string[] {
  return runs.map((run) => `${run.assetId}[${run.frames.map((f) => f.sequence).join(",")}]`);
}

describe("partitionDecodeRuns", () => {
  it("splits interleaved assets into contiguous runs, never grouping by asset", () => {
    // can0 -> assetA, can1 -> assetB; sequences 1..5 alternate.
    const frames = [
      frame(1, "can0"),
      frame(2, "can1"),
      frame(3, "can0"),
      frame(4, "can0"),
      frame(5, "can1"),
    ];

    const runs = partitionDecodeRuns(frames, bindings({ can0: "assetA", can1: "assetB" }));

    // The whole point: assetA is NOT [1,3,4] — 1 -> 3 is not contiguous.
    expect(shape(runs)).toEqual(["assetA[1]", "assetB[2]", "assetA[3,4]", "assetB[5]"]);
  });

  it("cuts a run when the sequence is not contiguous, even for one asset", () => {
    const frames = [frame(1, "can0"), frame(3, "can0")];

    const runs = partitionDecodeRuns(frames, bindings({ can0: "assetA" }));

    expect(shape(runs)).toEqual(["assetA[1]", "assetA[3]"]);
  });

  it("drops frames whose channel is not bound, and the gap ends the run", () => {
    const frames = [frame(1, "can0"), frame(2, "can2"), frame(3, "can0")];

    const runs = partitionDecodeRuns(frames, bindings({ can0: "assetA" }));

    expect(shape(runs)).toEqual(["assetA[1]", "assetA[3]"]);
  });

  it("returns nothing when no channel is bound", () => {
    const frames = [frame(1, "can0"), frame(2, "can1")];

    expect(partitionDecodeRuns(frames, bindings({}))).toEqual([]);
  });

  it("returns nothing for an empty viewport", () => {
    expect(partitionDecodeRuns([], bindings({ can0: "assetA" }))).toEqual([]);
  });

  it("splits a long contiguous run at the batch bound", () => {
    const frames = Array.from({ length: 2500 }, (_, index) => frame(index + 1, "can0"));

    const runs = partitionDecodeRuns(frames, bindings({ can0: "assetA" }), 1000);

    expect(runs.map((run) => run.frames.length)).toEqual([1000, 1000, 500]);
    expect(shape(runs)).toEqual([
      `assetA[${range(1, 1000).join(",")}]`,
      `assetA[${range(1001, 2000).join(",")}]`,
      `assetA[${range(2001, 2500).join(",")}]`,
    ]);
  });

  it("keeps the realtime order, never sorting by sequence", () => {
    // A worker snapshot is already ordered; the partition preserves whatever order it is given.
    const frames = [frame(7, "can0"), frame(8, "can0")];

    const runs = partitionDecodeRuns(frames, bindings({ can0: "assetA" }));

    expect(runs[0]?.frames.map((f) => f.sequence)).toEqual([7n, 8n]);
  });

  it("rejects a non-positive batch bound rather than looping forever", () => {
    expect(() => partitionDecodeRuns([frame(1, "can0")], bindings({ can0: "assetA" }), 0)).toThrow(
      RangeError,
    );
  });
});

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, index) => from + index);
}
