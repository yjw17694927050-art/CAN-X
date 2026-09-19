/**
 * The minimal, read-only channel list the decode-binding control renders — derived from
 * the frames the realtime stream has actually observed, and from nothing else.
 *
 * ```text
 * RealtimeStreamStore.snapshot.frames[].channelId     the only channel identity that exists
 *   ↓ de-duplicate + order
 * observedChannelIds()          ["can0", "can1"]      not a registry, a projection
 *   ↓ look up the session's binding
 * observedChannelBindings()     bound | unbound | unlisted
 * ```
 *
 * Four properties are deliberate:
 *
 * * **The channel list is observed, not declared.** There is no channel registry, no
 *   device manager and no configuration here: a channel appears because a frame carrying
 *   its `channelId` arrived, and a channel nobody has transmitted on does not appear at
 *   all. This is why the list can legitimately be empty — before the first frame there
 *   are no channels, and the control shows that as a real empty state rather than
 *   inventing a `can0`.
 * * **Order is deterministic.** The ids are sorted by code unit, so the same observed set
 *   always renders in the same order — a locale-dependent collation would make the rows
 *   move under the user's cursor for no reason.
 * * **A blank id is not a channel.** `WorkspaceSessionStore.bindChannel` refuses an empty
 *   `channelId`, so listing one would offer a Bind that always throws. The filter matches
 *   the session's own guard exactly (`=== ""`) rather than guessing at whitespace.
 * * **Unlisted is a state, not a disappearance.** A binding may name an asset the supplied
 *   collection no longer holds (the asset was deleted, or the collection is stale). The
 *   row stays, reported as `"unlisted"`, because silently dropping it would hide a
 *   binding the user cannot then remove.
 *
 * Pure: no store, no Worker, no React, no session instance is read here.
 */

import type { RuntimeDbcAsset } from "../../runtime/dbc-client";
import type { FrameViewportSnapshot } from "../../workers/frame-worker-core";
import type { DecodeBindings } from "../../workspace/session";

/** How one observed channel's decode binding reads on screen. */
export type ChannelBindingState = "bound" | "unbound" | "unlisted";

/** One row: the channel, its binding, and the name to show for it. */
export interface ObservedChannelBinding {
  /** The `channelId` the frames carried. */
  readonly channelId: string;
  /** `"bound"` (listed asset), `"unlisted"` (bound to an asset the collection lacks), `"unbound"`. */
  readonly state: ChannelBindingState;
  /** The bound asset's id, or `null` when the channel is unbound. */
  readonly assetId: string | null;
  /** The bound asset's `sourceName`, or `null` unless the state is `"bound"`. */
  readonly sourceName: string | null;
}

export interface ObservedChannelInput {
  /** The realtime store's latest viewport, or `null` before anything arrived. */
  readonly snapshot: FrameViewportSnapshot | null;
  /** The session's `channelId -> assetId` bindings. */
  readonly bindings: DecodeBindings;
  /** The project's asset collection, as the caller read it from the Runtime. */
  readonly assets: readonly RuntimeDbcAsset[];
}

/**
 * The channelIds the observed frames carry, de-duplicated and ordered.
 *
 * Empty — not `["can0"]` — when nothing has been observed yet or the viewport holds no
 * frames, so the caller can render an honest empty state.
 */
export function observedChannelIds(snapshot: FrameViewportSnapshot | null): readonly string[] {
  const channelIds = new Set<string>();
  for (const frame of snapshot?.frames ?? []) {
    if (frame.channelId !== "") channelIds.add(frame.channelId);
  }
  return [...channelIds].sort(compareCodeUnits);
}

/** One row per observed channel, each carrying its binding state. */
export function observedChannelBindings(
  input: ObservedChannelInput,
): readonly ObservedChannelBinding[] {
  const sourceNames = new Map<string, string>();
  for (const asset of input.assets) sourceNames.set(asset.assetId, asset.sourceName);

  return observedChannelIds(input.snapshot).map((channelId) => {
    const assetId = input.bindings.get(channelId) ?? null;
    if (assetId === null) {
      return { assetId: null, channelId, sourceName: null, state: "unbound" };
    }
    const sourceName = sourceNames.get(assetId) ?? null;
    return sourceName === null
      ? { assetId, channelId, sourceName: null, state: "unlisted" }
      : { assetId, channelId, sourceName, state: "bound" };
  });
}

/** Plain code-unit ordering: deterministic on every host, every locale, every run. */
function compareCodeUnits(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}
