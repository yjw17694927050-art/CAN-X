import { describe, expect, it } from "vitest";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { DecodedFrameEntry, DecodedSignal } from "../../workspace/decoded-realtime";
import type { SignalSelection } from "../../workspace/session";
import { buildSignalSeries, DEFAULT_MAX_SAMPLES } from "./series";

const SELECTION: SignalSelection = {
  assetId: "asset-1",
  channelId: "can0",
  messageName: "EngineData",
  signalName: "EngineSpeed",
  unit: "rpm",
};

function frame(sequence: number, channelId: string): RuntimeFrame {
  return {
    arbitrationId: 0x123,
    bitrateSwitch: false,
    channelId,
    clockDomain: "host.monotonic",
    data: new Uint8Array([0x01, 0x02]),
    direction: "rx",
    dlc: 2,
    errorStateIndicator: false,
    flags: 0,
    hardwareTimestamp: null,
    hostTimestamp: 1_000 + sequence,
    isExtended: false,
    isFd: false,
    normalizedTimestamp: sequence / 10,
    sequence: BigInt(sequence),
    timestampQuality: "host",
  };
}

/** A decoded signal whose raw and physical values differ, so a mix-up cannot hide. */
function signal(name: string, rawValue: number, physicalValue: number): DecodedSignal {
  return { choiceLabel: null, name, physicalValue, rawValue, unit: "rpm" };
}

/** One decoded entry, matching the selection unless an override says otherwise. */
function decodedEntry(
  sequence: number,
  signals: readonly DecodedSignal[],
  overrides: {
    readonly assetId?: string;
    readonly channelId?: string;
    readonly messageName?: string;
  } = {},
): DecodedFrameEntry {
  return {
    assetId: overrides.assetId ?? SELECTION.assetId,
    frame: frame(sequence, overrides.channelId ?? SELECTION.channelId),
    outcome: {
      messageName: overrides.messageName ?? SELECTION.messageName,
      signals,
      status: "decoded",
    },
  };
}

/** A frame the Runtime answered with a failure: it carries no signal at all. */
function failedEntry(sequence: number): DecodedFrameEntry {
  return {
    assetId: SELECTION.assetId,
    frame: frame(sequence, SELECTION.channelId),
    outcome: { code: "dbc.decode_failed", status: "failed" },
  };
}

describe("buildSignalSeries", () => {
  it("is empty when there is nothing to plot", () => {
    expect(buildSignalSeries([], SELECTION, 10)).toEqual([]);
  });

  it("takes only the frames of the selected channel", () => {
    const records = [
      decodedEntry(0, [signal("EngineSpeed", 1, 1_000)]),
      decodedEntry(1, [signal("EngineSpeed", 2, 2_000)], { channelId: "can1" }),
    ];

    // can0 and can1 can both carry "EngineData.EngineSpeed": only can0 is the selection.
    expect(buildSignalSeries(records, SELECTION, 10)).toEqual([{ time: 0, value: 1_000 }]);
  });

  it("takes only the outcomes that were decoded against the selected asset", () => {
    const records = [
      decodedEntry(0, [signal("EngineSpeed", 1, 1_000)]),
      decodedEntry(1, [signal("EngineSpeed", 2, 2_000)], { assetId: "asset-2" }),
    ];

    expect(buildSignalSeries(records, SELECTION, 10)).toEqual([{ time: 0, value: 1_000 }]);
  });

  it("takes only the frames decoded as the selected message, and only successful ones", () => {
    const records = [
      decodedEntry(0, [signal("EngineSpeed", 1, 1_000)]),
      decodedEntry(1, [signal("EngineSpeed", 2, 2_000)], { messageName: "BodyData" }),
      failedEntry(2),
    ];

    expect(buildSignalSeries(records, SELECTION, 10)).toEqual([{ time: 0, value: 1_000 }]);
  });

  it("takes only the selected signal out of a decoded message", () => {
    const records = [
      decodedEntry(0, [signal("EngineTemp", 9, 9_000), signal("EngineSpeed", 1, 1_000)]),
      decodedEntry(1, [signal("EngineTemp", 8, 8_000)]),
    ];

    expect(buildSignalSeries(records, SELECTION, 10)).toEqual([{ time: 0, value: 1_000 }]);
  });

  it("plots the physical value, never the raw one", () => {
    const records = [decodedEntry(0, [signal("EngineSpeed", 617, 1_234)])];

    const result = buildSignalSeries(records, SELECTION, 10);

    expect(result).toEqual([{ time: 0, value: 1_234 }]);
    expect(result[0]?.value).not.toBe(617);
  });

  it("places every sample on the frame's normalized timestamp", () => {
    const records = [
      decodedEntry(0, [signal("EngineSpeed", 1, 1_000)]),
      decodedEntry(1, [signal("EngineSpeed", 2, 2_000)]),
      decodedEntry(7, [signal("EngineSpeed", 3, 3_000)]),
    ];

    // 0, 0.1, 0.7 — the pipeline's own instants, not a decode completion time or
    // `Date.now()`, either of which would make these values unrunnable.
    expect(buildSignalSeries(records, SELECTION, 10).map((sample) => sample.time)).toEqual([
      0, 0.1, 0.7,
    ]);
  });

  it("bounds the series to maxSamples while retaining the first and last sample", () => {
    const records = Array.from({ length: 101 }, (_, index) =>
      decodedEntry(index, [signal("EngineSpeed", index, index)]),
    );

    const result = buildSignalSeries(records, SELECTION, 10);

    expect(result.length).toBeLessThanOrEqual(10);
    expect(result[0]).toEqual({ time: 0, value: 0 });
    expect(result.at(-1)).toEqual({ time: 10, value: 100 });
  });

  it("bounds the series to the default sample count when no bound is given", () => {
    const records = Array.from({ length: DEFAULT_MAX_SAMPLES + 1 }, (_, index) =>
      decodedEntry(index, [signal("EngineSpeed", index, index)]),
    );

    const result = buildSignalSeries(records, SELECTION);

    expect(result).toHaveLength(DEFAULT_MAX_SAMPLES);
    expect(result.at(-1)).toEqual({ time: DEFAULT_MAX_SAMPLES / 10, value: DEFAULT_MAX_SAMPLES });
  });

  it("refuses a bound that could not describe a curve", () => {
    expect(() => buildSignalSeries([], SELECTION, 1)).toThrow(RangeError);
    expect(() => buildSignalSeries([], SELECTION, 2.5)).toThrow(RangeError);
  });
});
