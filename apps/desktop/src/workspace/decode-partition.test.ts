import { describe, expect, it } from "vitest";

import type { RuntimeFrame } from "../runtime/frame-schema";
import { MAX_WORK_SET_FRAMES, partitionDecodeWorkSets } from "./decode-partition";

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

/**
 * One work set, flattened to `assetId[seq, seq, …]` so an expectation reads as the spec.
 *
 * The gaps are visible in this rendering on purpose: a work set of `assetA[1,3,5]` is the
 * *expected* output for an alternating viewport, not a tolerated one.
 */
function shape(
  sets: readonly { assetId: string; frames: readonly RuntimeFrame[] }[],
): string[] {
  return sets.map((set) => `${set.assetId}[${set.frames.map((f) => f.sequence).join(",")}]`);
}

describe("partitionDecodeWorkSets", () => {
  it("groups interleaved assets by asset, keeping every gap", () => {
    // can0 -> assetA, can1 -> assetB; sequences 1..5 alternate.
    const frames = [
      frame(1, "can0"),
      frame(2, "can1"),
      frame(3, "can0"),
      frame(4, "can0"),
      frame(5, "can1"),
    ];

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA", can1: "assetB" }));

    // The whole point: two requests, not one per frame. `1 -> 3` is a legal work set.
    expect(shape(sets)).toEqual(["assetA[1,3,4]", "assetB[2,5]"]);
  });

  it("keeps one asset's gapped frames in a single set", () => {
    const frames = [frame(1, "can0"), frame(3, "can0")];

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA" }));

    expect(shape(sets)).toEqual(["assetA[1,3]"]);
  });

  it("groups a six-frame alternation into exactly two sets", () => {
    const frames = [
      frame(1, "can0"),
      frame(2, "can1"),
      frame(3, "can0"),
      frame(4, "can1"),
      frame(5, "can0"),
      frame(6, "can1"),
    ];

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA", can1: "assetB" }));

    expect(sets).toHaveLength(2);
    expect(shape(sets)).toEqual(["assetA[1,3,5]", "assetB[2,4,6]"]);
  });

  it("does not grow the request count with the frame count", () => {
    // 1000 alternating frames, a 1000-frame bound: two requests, not one thousand.
    const frames = Array.from({ length: 1000 }, (_, index) =>
      frame(index + 1, index % 2 === 0 ? "can0" : "can1"),
    );

    const sets = partitionDecodeWorkSets(
      frames,
      bindings({ can0: "assetA", can1: "assetB" }),
      MAX_WORK_SET_FRAMES,
    );

    expect(sets.length).toBeLessThanOrEqual(2);
    expect(sets.map((set) => set.assetId)).toEqual(["assetA", "assetB"]);
    expect(sets.map((set) => set.frames.length)).toEqual([500, 500]);
    // Every frame is decoded exactly once, gaps included.
    expect(new Set(sets.flatMap((set) => set.frames.map((f) => f.sequence))).size).toBe(1000);
  });

  it("drops frames whose channel is not bound, without fragmenting the bound asset", () => {
    // can1 is unbound and sits between two assetA frames; it must not cut them apart.
    const frames = [frame(1, "can0"), frame(2, "can1"), frame(3, "can0")];

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA" }));

    expect(shape(sets)).toEqual(["assetA[1,3]"]);
  });

  it("never lets an unbound channel turn an asset's work into single-frame sets", () => {
    // Half the viewport is unbound; the bound half is still one request per bound.
    const frames = Array.from({ length: 400 }, (_, index) =>
      frame(index + 1, index % 2 === 0 ? "can0" : "can9"),
    );

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA" }));

    expect(sets).toHaveLength(1);
    expect(sets[0]?.frames.length).toBe(200);
    expect(sets[0]?.frames.map((f) => f.sequence)).toEqual(
      Array.from({ length: 200 }, (_, index) => BigInt(index * 2 + 1)),
    );
  });

  it("returns nothing when no channel is bound", () => {
    const frames = [frame(1, "can0"), frame(2, "can1")];

    expect(partitionDecodeWorkSets(frames, bindings({}))).toEqual([]);
  });

  it("returns nothing for an empty viewport", () => {
    expect(partitionDecodeWorkSets([], bindings({ can0: "assetA" }))).toEqual([]);
  });

  it("splits one asset past the bound, and only there", () => {
    const frames = Array.from({ length: 2500 }, (_, index) => frame(index + 1, "can0"));

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA" }), 1000);

    expect(sets.map((set) => set.frames.length)).toEqual([1000, 1000, 500]);
    expect(shape(sets)).toEqual([
      `assetA[${range(1, 1000).join(",")}]`,
      `assetA[${range(1001, 2000).join(",")}]`,
      `assetA[${range(2001, 2500).join(",")}]`,
    ]);
  });

  it("keeps the realtime order, never sorting by sequence", () => {
    // A worker snapshot is already ordered; the partition preserves whatever order it is given.
    const frames = [frame(7, "can0"), frame(8, "can0")];

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA" }));

    expect(sets[0]?.frames.map((f) => f.sequence)).toEqual([7n, 8n]);
  });

  it("returns the assets in the order they first appeared", () => {
    const frames = [frame(1, "can1"), frame(2, "can0"), frame(3, "can1")];

    const sets = partitionDecodeWorkSets(frames, bindings({ can0: "assetA", can1: "assetB" }));

    expect(sets.map((set) => set.assetId)).toEqual(["assetB", "assetA"]);
  });

  it("rejects a non-positive frame bound rather than looping forever", () => {
    expect(() =>
      partitionDecodeWorkSets([frame(1, "can0")], bindings({ can0: "assetA" }), 0),
    ).toThrow(RangeError);
  });
});

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, index) => from + index);
}
