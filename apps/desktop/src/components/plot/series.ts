import type { RuntimeFrame } from "../../runtime/frame-schema";

export interface PlotSample {
  readonly time: number;
  readonly value: number;
}

/** Extract one payload byte and deterministically reduce it to a bounded series. */
export function buildByteSeries(
  frames: readonly RuntimeFrame[],
  byteIndex: number,
  maxSamples: number,
): readonly PlotSample[] {
  if (!Number.isInteger(byteIndex) || byteIndex < 0) {
    throw new RangeError("byteIndex must be a non-negative integer");
  }
  if (!Number.isInteger(maxSamples) || maxSamples < 2) {
    throw new RangeError("maxSamples must be an integer of at least 2");
  }

  const samples = frames.flatMap((frame) => {
    const value = frame.data[byteIndex];
    return value === undefined ? [] : [{ time: frame.normalizedTimestamp, value }];
  });
  if (samples.length <= maxSamples) return samples;

  const result: PlotSample[] = [];
  const last = samples.length - 1;
  for (let index = 0; index < maxSamples; index += 1) {
    const sample = samples[Math.round((index * last) / (maxSamples - 1))];
    if (sample !== undefined) result.push(sample);
  }
  return result;
}
