/**
 * Batch partitioning — turning one realtime viewport into decode requests the Runtime accepts.
 *
 * ```text
 * viewport frames (realtime order, mixed channels)
 *   ↓ assetForChannel(frame.channelId)
 * one ordered work set per asset, in viewport order, cut at the frame bound
 *   ↓ one request per work set
 * POST /dbc/assets/{assetId}/decode-frames
 * ```
 *
 * **Grouping by asset is the point, and contiguity is not.** A DBC decode is a
 * frame-local, stateless transformation: a decoder asked about sequence 1, 3 and 5
 * answers exactly what it would answer for each of them alone, and never looks at
 * sequence 2. A live viewport that alternates `can0` and `can1` therefore leaves
 * every asset's own sequences full of the other channel's gaps — and those gaps are
 * not a defect to be cut around, they are simply what "this asset's frames in this
 * viewport" looks like. Partitioning on contiguity (which this module used to do,
 * against the Runtime's `FrameBatch` contract) turned that ordinary viewport into
 * one request per frame; partitioning on asset identity turns it into one request
 * per asset.
 *
 * Five properties are deliberate:
 *
 * * **One work set per asset per bound.** Frames of one asset are collected in
 *   viewport order and cut only when the set reaches `maxFramesPerSet`, so
 *   `#requests(asset) = ceil(framesFor(asset) / maxFramesPerSet)`.
 * * **An unbound channel contributes nothing, and cuts nothing.** A frame whose
 *   channel has no binding is not decoded and is not part of any work set. Unlike
 *   the sequence-based version of this module, it does not end a run either: it is
 *   not in the set, and it has no power to fragment one.
 * * **The input order is the output order.** Frames are appended in the order they
 *   arrived and never sorted, so the work sets are in viewport order per asset and
 *   the sets themselves come back in the order their asset first appeared. What is
 *   plotted stays the order the frames arrived in.
 * * **A set's sequences ascend and are unique, but need not be consecutive.** That
 *   is exactly the Runtime's `DecodeFrameSet` contract; the viewport's own realtime
 *   order is what provides it, and `partition` neither sorts nor de-duplicates to
 *   manufacture it. A viewport that repeated a sequence is a broken viewport, and
 *   the Runtime refuses it rather than this module hiding it.
 * * **Asset identity is passed in, never looked up.** `assetForChannel` is the
 *   session's answer, so this module stays pure and knows nothing about sessions,
 *   projects or React.
 */

import type { RuntimeFrame } from "../runtime/frame-schema";

/**
 * One decodable unit: the frames of a single asset, in viewport order, bounded in length.
 *
 * The frames are bound to `assetId` and their sequences ascend, so the set is exactly
 * what one `decode-frames` request may carry. They are *not* required to be
 * consecutive — see the module header.
 */
export interface DecodeWorkSet {
  readonly assetId: string;
  readonly frames: readonly RuntimeFrame[];
}

/** The Runtime's own per-request ceiling, so a work set is never rejected for being too long. */
export const MAX_WORK_SET_FRAMES = 1000;

/**
 * Group a viewport into one ordered work set per asset, cutting at the frame bound.
 *
 * Args:
 *   frames: The viewport's frames, in realtime order.
 *   assetForChannel: Which asset decodes a channel, or `null` when nothing is bound to
 *     it. This is the session's answer (`WorkspaceSessionStore.assetForChannel`),
 *     passed in so this function stays pure and knows nothing about sessions.
 *   maxFramesPerSet: The largest number of frames one work set may carry. Defaults to
 *     {@link MAX_WORK_SET_FRAMES}, the Runtime's own limit.
 *
 * Returns:
 *   The work sets: for each asset that appears in the viewport, its frames in viewport
 *   order, cut at the bound; the assets in the order they first appeared. Frames whose
 *   channel is unbound contribute no set.
 *
 * Throws:
 *   `RangeError` when `maxFramesPerSet` is not a positive integer — a zero bound would
 *   make every set impossible and the caller would be asking for an infinite loop.
 */
export function partitionDecodeWorkSets(
  frames: readonly RuntimeFrame[],
  assetForChannel: (channelId: string) => string | null,
  maxFramesPerSet: number = MAX_WORK_SET_FRAMES,
): readonly DecodeWorkSet[] {
  if (!Number.isInteger(maxFramesPerSet) || maxFramesPerSet < 1) {
    throw new RangeError("maxFramesPerSet must be a positive integer");
  }

  // `Map` preserves insertion order for string keys, which is what makes the output
  // order "the order each asset first appeared" rather than an accident of hashing.
  const setsByAsset = new Map<string, RuntimeFrame[][]>();

  for (const frame of frames) {
    const assetId = assetForChannel(frame.channelId);
    if (assetId === null) continue;

    let sets = setsByAsset.get(assetId);
    if (sets === undefined) {
      sets = [];
      setsByAsset.set(assetId, sets);
    }
    // The last set is the only one that can still grow: earlier ones are full by
    // construction, which is what keeps the output a partitioned view and not a
    // reshuffle.
    const open = sets[sets.length - 1];
    if (open !== undefined && open.length < maxFramesPerSet) open.push(frame);
    else sets.push([frame]);
  }

  const workSets: DecodeWorkSet[] = [];
  for (const [assetId, sets] of setsByAsset) {
    for (const set of sets) workSets.push({ assetId, frames: set });
  }
  return workSets;
}
