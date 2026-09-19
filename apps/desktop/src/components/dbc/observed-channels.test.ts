import { describe, expect, it } from "vitest";

import type { RuntimeDbcAsset } from "../../runtime/dbc-client";
import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { FrameViewportSnapshot } from "../../workers/frame-worker-core";
import type { DecodeBindings } from "../../workspace/session";
import { observedChannelBindings, observedChannelIds } from "./observed-channels";

// The channel list is derived from real frames — the same `RuntimeFrame` the Worker
// decodes and the Trace renders — so the fixtures are real frames, not a stand-in shape.
function frame(channelId: string, sequence: number): RuntimeFrame {
  return {
    arbitrationId: 0x123,
    bitrateSwitch: false,
    channelId,
    clockDomain: "host.monotonic",
    data: new Uint8Array([0x01]),
    direction: "rx",
    dlc: 1,
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

function viewport(...channelIds: readonly string[]): FrameViewportSnapshot {
  return {
    decodeMs: 0,
    droppedViewFrames: 0,
    frames: channelIds.map((channelId, index) => frame(channelId, index)),
    sequenceGaps: 0,
    streamId: "stream-1",
  };
}

const ASSET_A: RuntimeDbcAsset = {
  assetId: "dbc-asset-0001",
  encoding: "utf-8",
  importedAt: "2026-09-17T08:30:00+00:00",
  sha256: "a".repeat(64),
  sizeBytes: 2048,
  sourceName: "powertrain.dbc",
};

const ASSET_B: RuntimeDbcAsset = {
  assetId: "dbc-asset-0002",
  encoding: "latin-1",
  importedAt: "2026-09-16T10:15:00+00:00",
  sha256: "b".repeat(64),
  sizeBytes: 512,
  sourceName: "body.dbc",
};

const ASSETS: readonly RuntimeDbcAsset[] = [ASSET_A, ASSET_B];

function bindings(entries: ReadonlyArray<readonly [string, string]>): DecodeBindings {
  return new Map(entries);
}

const NO_BINDINGS: DecodeBindings = new Map<string, string>();

describe("observedChannelIds", () => {
  it("lists nothing before any frame has been observed", () => {
    expect(observedChannelIds(null)).toEqual([]);
  });

  it("lists nothing when the observed viewport holds no frames", () => {
    expect(observedChannelIds(viewport())).toEqual([]);
  });

  it("de-duplicates and orders the channelIds the observed frames carry", () => {
    expect(observedChannelIds(viewport("can2", "can0", "can1", "can2"))).toEqual([
      "can0",
      "can1",
      "can2",
    ]);
  });

  it("leaves out a blank channelId, which the session refuses to bind", () => {
    expect(observedChannelIds(viewport("can0", ""))).toEqual(["can0"]);
  });
});

describe("observedChannelBindings", () => {
  it("reports every observed channel as unbound when nothing is bound", () => {
    expect(
      observedChannelBindings({ assets: ASSETS, bindings: NO_BINDINGS, snapshot: viewport("can0") }),
    ).toEqual([
      { assetId: null, channelId: "can0", sourceName: null, state: "unbound" },
    ]);
  });

  it("names the bound asset's sourceName when the channel is bound to a listed asset", () => {
    const rows = observedChannelBindings({
      assets: ASSETS,
      bindings: bindings([["can0", ASSET_A.assetId]]),
      snapshot: viewport("can0", "can1"),
    });

    expect(rows).toEqual([
      { assetId: ASSET_A.assetId, channelId: "can0", sourceName: "powertrain.dbc", state: "bound" },
      { assetId: null, channelId: "can1", sourceName: null, state: "unbound" },
    ]);
  });

  it("reports a binding whose asset is absent from the collection as unlisted, not dropped", () => {
    const rows = observedChannelBindings({
      assets: ASSETS,
      bindings: bindings([["can0", "dbc-asset-gone"]]),
      snapshot: viewport("can0"),
    });

    expect(rows).toEqual([
      { assetId: "dbc-asset-gone", channelId: "can0", sourceName: null, state: "unlisted" },
    ]);
  });

  it("never lists a channel it has not observed, even when the session holds a binding for it", () => {
    const rows = observedChannelBindings({
      assets: ASSETS,
      bindings: bindings([["can9", ASSET_B.assetId]]),
      snapshot: viewport("can0"),
    });

    expect(rows.map((row) => row.channelId)).toEqual(["can0"]);
  });

  it("produces one row per observed channel, in the observed-channel order", () => {
    const rows = observedChannelBindings({
      assets: ASSETS,
      bindings: bindings([["can1", ASSET_B.assetId]]),
      snapshot: viewport("can1", "can0"),
    });

    expect(rows.map((row) => row.channelId)).toEqual(["can0", "can1"]);
  });
});
