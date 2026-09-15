import { describe, expect, it } from "vitest";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import { buildByteSeries } from "./series";

function frame(index: number, data: number[]): RuntimeFrame {
  return {
    sequence: BigInt(index),
    channelId: "can0",
    arbitrationId: 0x123,
    isExtended: false,
    isFd: false,
    bitrateSwitch: false,
    errorStateIndicator: false,
    dlc: data.length,
    data: new Uint8Array(data),
    direction: "rx",
    hardwareTimestamp: null,
    hostTimestamp: 10 + index,
    normalizedTimestamp: index / 10,
    clockDomain: "host.monotonic",
    timestampQuality: "host",
    flags: 0,
  };
}

describe("buildByteSeries", () => {
  it("extracts a deterministic byte value on normalized time", () => {
    const result = buildByteSeries([frame(0, [2, 7]), frame(1, [3, 8])], 1, 10);

    expect(result).toEqual([
      { time: 0, value: 7 },
      { time: 0.1, value: 8 },
    ]);
  });

  it("skips frames whose payload does not contain the byte", () => {
    expect(buildByteSeries([frame(0, []), frame(1, [4])], 0, 10)).toEqual([
      { time: 0.1, value: 4 },
    ]);
  });

  it("bounds output while retaining the first and last samples", () => {
    const source = Array.from({ length: 101 }, (_, index) => frame(index, [index % 256]));

    const result = buildByteSeries(source, 0, 10);

    expect(result.length).toBeLessThanOrEqual(10);
    expect(result[0]).toEqual({ time: 0, value: 0 });
    expect(result.at(-1)).toEqual({ time: 10, value: 100 });
  });
});
