import type { DecodedFrameEntry, DecodedSignal } from "../../workspace/decoded-realtime";
import type { SignalSelection } from "../../workspace/session";

/** One point of a plotted signal: when the frame arrived, what it said. */
export interface PlotSample {
  readonly time: number;
  readonly value: number;
}

/** How many points a Plot keeps before it starts reducing. */
export const DEFAULT_MAX_SAMPLES = 1000;

/**
 * Project decoded outcomes onto one signal's bounded series.
 *
 * ```text
 * DecodedRealtimeStore.entries        every frame's outcome, keyed by sequence
 *   ↓ (channelId, assetId, messageName, signalName)      the four facts, all required
 * PlotSample[] { time: frame.normalizedTimestamp, value: signal.physicalValue }
 *   ↓ deterministic reduction
 * at most maxSamples points, first and last kept
 * ```
 *
 * Five properties are deliberate:
 *
 * * **The channel is part of the match, not a filter applied afterwards.** One DBC
 *   asset may be bound to `can0` *and* `can1`, and both may carry a message with the
 *   same name — so `assetId` + `messageName` + `signalName` alone would silently plot
 *   "whichever channel arrived", mixing two physically different signals into one
 *   curve. A record is taken only when `frame.channelId` equals the selection's.
 * * **Four facts or nothing.** Channel, asset, message and signal must *all* agree. A
 *   frame decoded against another asset, a frame whose message is a different one, or
 *   a decoded message that does not declare this signal contributes no point — there is
 *   no "closest match".
 * * **The value is the physical one.** The Runtime already computed
 *   `raw * factor + offset`; that product is what an engineer plots. `rawValue` is the
 *   bus's own integer and is deliberately never plotted — a curve of raw counts is
 *   unreadable and hides the scaling the document declared.
 * * **Time is the frame's normalized timestamp.** Both are produced by the realtime
 *   pipeline, so every panel places a frame at the same instant. A decode completion
 *   time or `Date.now()` would smear the x-axis by network latency and make two
 *   frames from the same batch look simultaneous.
 * * **Bounded, deterministically.** A capture runs for hours; a series that grows with
 *   it is a leak with a friendly name. {@link DEFAULT_MAX_SAMPLES} points are kept,
 *   chosen by an even stride that always retains the first and the last sample, so the
 *   same input reduces to the same output on every run.
 *
 * Pure: it reads no store, opens no socket and holds no state between calls.
 *
 * Args:
 *   records: The decoded entries to project — a store's `entries.values()`, or any
 *     iterable of them, in the order they should be plotted.
 *   selection: The four-part identity of the signal to plot, plus its unit.
 *   maxSamples: The upper bound on the returned series. Defaults to
 *     {@link DEFAULT_MAX_SAMPLES}.
 *
 * Returns:
 *   The matching samples, in input order, reduced to at most `maxSamples` points.
 *
 * Throws:
 *   `RangeError` when `maxSamples` is not an integer of at least two — a series of one
 *   point is not a curve, and a caller asking for it has a bug rather than a preference.
 */
export function buildSignalSeries(
  records: Iterable<DecodedFrameEntry>,
  selection: SignalSelection,
  maxSamples: number = DEFAULT_MAX_SAMPLES,
): readonly PlotSample[] {
  if (!Number.isInteger(maxSamples) || maxSamples < 2) {
    throw new RangeError("maxSamples must be an integer of at least 2");
  }

  const samples: PlotSample[] = [];
  for (const record of records) {
    const signal = selectedSignalIn(record, selection);
    if (signal === null) continue;
    samples.push({ time: record.frame.normalizedTimestamp, value: signal.physicalValue });
  }
  return reduceToBound(samples, maxSamples);
}

/**
 * The selected signal inside one record, or `null` when the record is not about it.
 *
 * All four facts are checked here and nowhere else, so there is exactly one place that
 * decides what a record contributes to the series. A `failed` outcome has no signals at
 * all, so it is excluded by the same branch that excludes a different message — never by
 * a separate special case that could drift.
 */
function selectedSignalIn(
  record: DecodedFrameEntry,
  selection: SignalSelection,
): DecodedSignal | null {
  if (record.assetId !== selection.assetId) return null;
  if (record.frame.channelId !== selection.channelId) return null;
  const { outcome } = record;
  if (outcome.status !== "decoded" || outcome.messageName !== selection.messageName) return null;
  return outcome.signals.find((signal) => signal.name === selection.signalName) ?? null;
}

/**
 * Reduce an over-long series to `maxSamples` points by an even stride.
 *
 * The stride is computed from the last index, exactly as the byte series did before it:
 * an evenly spaced reduction keeps the shape of the curve, and anchoring on the last
 * sample means the newest value is never dropped — the one point a live plot cannot
 * afford to lose.
 */
function reduceToBound(samples: readonly PlotSample[], maxSamples: number): readonly PlotSample[] {
  if (samples.length <= maxSamples) return samples;

  const reduced: PlotSample[] = [];
  const last = samples.length - 1;
  for (let index = 0; index < maxSamples; index += 1) {
    const sample = samples[Math.round((index * last) / (maxSamples - 1))];
    if (sample !== undefined) reduced.push(sample);
  }
  return reduced;
}
