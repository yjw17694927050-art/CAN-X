/**
 * Batch partitioning — turning one realtime viewport into decode requests the Runtime accepts.
 *
 * ```text
 * viewport frames (realtime order, mixed channels)
 *   ↓ assetForChannel(frame.channelId)
 * runs of (one asset) × (contiguous sequence) × (at most maxRunLength)
 *   ↓ one request per run
 * POST /dbc/assets/{assetId}/decode-batch
 * ```
 *
 * The constraint this module exists for is the Runtime's: `FrameBatch.create()` requires a
 * **contiguous** sequence range, because a batch is a slice of one stream's numbering, not a
 * set of samples. A viewport that alternates `can0`/`can1` therefore cannot be sent as "all
 * the `can0` frames" — `1 → 3` is not contiguous — so the viewport is cut into runs that are
 * each contiguous *within one asset*.
 *
 * Four properties are deliberate:
 *
 * * **Grouping by asset is the bug this prevents.** Two channels can be bound to two assets
 *   and interleave frame by frame; collecting `assetA = [1, 3, 4]` would hand the Runtime a
 *   batch whose sequence range has a hole in it, which is not a batch at all.
 * * **An unbound channel ends the run.** A frame whose channel has no binding is not decoded
 *   and is not part of any batch — and because it occupies a sequence number, the runs on
 *   either side of it are separated. Skipping it silently and joining its neighbours would
 *   manufacture the same hole.
 * * **The batch bound is applied here, not discovered from a rejection.** A run longer than
 *   `maxRunLength` is cut into several contiguous runs, each of which the Runtime accepts.
 * * **The input order is the output order.** A worker snapshot is already in realtime order;
 *   this function never sorts, so what is plotted stays the order the frames arrived in.
 */

import type { RuntimeFrame } from "../runtime/frame-schema";

/**
 * One decodable unit: frames of a single channel, contiguous in sequence, bounded in length.
 *
 * Every frame in `frames` is bound to `assetId` and their sequences are consecutive, so the
 * run is exactly what one `decode-batch` request may carry.
 */
export interface DecodeRun {
  readonly assetId: string;
  readonly frames: readonly RuntimeFrame[];
}

/** The Runtime's own per-request ceiling, so a run is never rejected for being too long. */
export const MAX_BATCH_FRAMES = 1000;

/**
 * Cut a viewport into contiguous, single-asset runs.
 *
 * Args:
 *   frames: The viewport's frames, in realtime order.
 *   assetForChannel: Which asset decodes a channel, or `null` when nothing is bound to it.
 *     This is the session's answer (`WorkspaceSessionStore.assetForChannel`), passed in so
 *     this function stays pure and knows nothing about sessions.
 *   maxRunLength: The largest number of frames one run may carry. Defaults to
 *     {@link MAX_BATCH_FRAMES}, the Runtime's own limit.
 *
 * Returns:
 *   The runs, in input order. Frames whose channel is unbound contribute no run.
 *
 * Throws:
 *   `RangeError` when `maxRunLength` is not a positive integer — a zero bound would make
 *   every run impossible and the caller would be asking for an infinite loop.
 */
export function partitionDecodeRuns(
  frames: readonly RuntimeFrame[],
  assetForChannel: (channelId: string) => string | null,
  maxRunLength: number = MAX_BATCH_FRAMES,
): readonly DecodeRun[] {
  if (!Number.isInteger(maxRunLength) || maxRunLength < 1) {
    throw new RangeError("maxRunLength must be a positive integer");
  }

  const runs: DecodeRun[] = [];
  // The run being built, kept as a mutable pair and pushed by reference: the frames array
  // is only ever appended to, so the pushed runs stay correct without a second pass.
  let current: { assetId: string; frames: RuntimeFrame[] } | null = null;
  let lastSequence: bigint | null = null;

  for (const frame of frames) {
    const assetId = assetForChannel(frame.channelId);
    if (assetId === null) {
      // Unbound: no batch carries it, and the sequence it occupies separates its neighbours.
      current = null;
      lastSequence = null;
      continue;
    }
    const continues =
      current !== null &&
      current.assetId === assetId &&
      lastSequence !== null &&
      lastSequence + 1n === frame.sequence &&
      current.frames.length < maxRunLength;
    if (continues && current !== null) {
      current.frames.push(frame);
    } else {
      current = { assetId, frames: [frame] };
      runs.push(current);
    }
    lastSequence = frame.sequence;
  }

  return runs;
}
