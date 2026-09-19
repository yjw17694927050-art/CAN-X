/**
 * The decoded realtime store — what the Runtime said about the frames that arrived.
 *
 * ```text
 * RealtimeStreamStore            raw frames, as the Worker decoded them
 *   ↓ Workspace Decode Coordinator        the ONE place that may issue decode HTTP
 * Runtime decode-batch
 *   ↓ DecodedRealtimeStore        this file: decoded outcomes, bounded, by sequence
 * Trace + Plot                   both read this, neither fetches anything
 * ```
 *
 * **One store per workspace, injected — never a module-level instance.** It is created by
 * the workspace and handed to every panel root alongside the session, so the Trace panel
 * and the Plot panel in two React roots read the *same* decoded state instead of each
 * keeping a copy and each issuing its own requests.
 *
 * Five properties are deliberate:
 *
 * * **The raw frame stays the identity.** An entry is keyed by the frame's own `sequence`
 *   and carries the original `RuntimeFrame` object. The numeric sequence that crossed the
 *   HTTP boundary is *not* allowed to become the frame's identity: a decoded product is
 *   the same frame plus an interpretation, never a new frame that replaced it.
 * * **Bounded, always.** The store keeps at most `capacity` entries and evicts the oldest,
 *   counting what it dropped. A decode path that grows without limit is a leak with a
 *   friendly name, and an industrial capture runs for hours.
 * * **A batch is one entry per frame, or nothing.** {@link DecodedRealtimeStore.applyBatch}
 *   takes a whole validated batch; a caller cannot slip in a half-batch, and `decoded` and
 *   `failed` are mutually exclusive by construction so no consumer has to guess which of
 *   the two to trust.
 * * **Request failures are not frame failures.** A request-level failure is recorded on
 *   `lastError` and leaves every entry exactly as it was — the per-frame outcomes of an
 *   earlier batch are still true. It never clears the store and never stops the stream.
 * * **Epochs are explicit.** {@link DecodedRealtimeStore.reset} and
 *   {@link DecodedRealtimeStore.clear} are how a coordinator discards state that belongs
 *   to a stream or a binding that no longer exists; nothing here decides *when* that is,
 *   because only the coordinator knows which generation a response belongs to.
 */

import type { RuntimeFrame } from "../runtime/frame-schema";

/** One decoded signal value, as the Runtime reported it. */
export interface DecodedSignal {
  readonly name: string;
  /** The raw bus value, un-scaled. */
  readonly rawValue: number;
  /** `raw * factor + offset`, computed by the Runtime — never here. */
  readonly physicalValue: number;
  /** The `VAL_` label, when the document declared one. */
  readonly choiceLabel: string | null;
  readonly unit: string | null;
}

/**
 * What happened to one frame.
 *
 * A discriminated union rather than an optional pair, so `decoded` and `failure` cannot
 * both be present and a consumer branches on one fact.
 */
export type DecodeOutcome =
  | {
      readonly status: "decoded";
      readonly messageName: string;
      readonly signals: readonly DecodedSignal[];
    }
  | { readonly status: "failed"; readonly code: string };

/** One frame with its outcome, and the asset it was decoded against. */
export interface DecodedFrameEntry {
  /**
   * The **original** frame object.
   *
   * Kept whole, and kept identical: this is the canonical frame the realtime pipeline
   * produced, not a reconstruction from the decode response.
   */
  readonly frame: RuntimeFrame;
  /** The project-owned asset this outcome came from. */
  readonly assetId: string;
  readonly outcome: DecodeOutcome;
}

/** Request-level lifecycle, so a UI can say "waiting" without inventing a state. */
export type DecodeRequestStatus = "idle" | "pending" | "ready" | "failed";

export interface DecodedRealtimeState {
  /** The stream the current entries belong to, or `null` before anything arrived. */
  readonly streamId: string | null;
  /** Decoded entries by the frame's own sequence. */
  readonly entries: ReadonlyMap<bigint, DecodedFrameEntry>;
  /** How many entries were evicted by the bound. Diagnostics, not an error. */
  readonly droppedEntries: number;
  /** The last request-level failure's stable code, or `null`. Never a body, never a path. */
  readonly lastError: string | null;
  readonly status: DecodeRequestStatus;
}

/** The empty state, shared so an untouched store allocates nothing per read. */
export const EMPTY_DECODED_REALTIME: DecodedRealtimeState = {
  droppedEntries: 0,
  entries: new Map<bigint, DecodedFrameEntry>(),
  lastError: null,
  status: "idle",
  streamId: null,
};

/** One validated batch, ready to merge. */
export interface DecodedBatch {
  readonly streamId: string;
  readonly assetId: string;
  readonly entries: readonly DecodedFrameEntry[];
}

/**
 * One workspace's decoded outcomes.
 *
 * Bounded by construction: `capacity` entries, oldest evicted first, with the eviction
 * counted. Construct it through {@link createDecodedRealtimeStore} and inject it; this
 * module exports no instance.
 */
export class DecodedRealtimeStore {
  readonly #capacity: number;
  readonly #listeners = new Set<() => void>();
  #state: DecodedRealtimeState = EMPTY_DECODED_REALTIME;

  constructor(capacity = 4096) {
    if (!Number.isInteger(capacity) || capacity < 1) {
      throw new Error("A decoded realtime store needs a positive integer capacity.");
    }
    this.#capacity = capacity;
  }

  getSnapshot = (): DecodedRealtimeState => this.#state;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  /** How many React roots currently observe this store. Diagnostics, not policy. */
  get listenerCount(): number {
    return this.#listeners.size;
  }

  /** How many entries this store will hold before it starts evicting. */
  get capacity(): number {
    return this.#capacity;
  }

  /** The outcome recorded for one sequence, or `null`. The lookup Plot and Trace share. */
  entryFor(sequence: bigint): DecodedFrameEntry | null {
    return this.#state.entries.get(sequence) ?? null;
  }

  /** Mark a decode request in flight. Does not touch any recorded outcome. */
  markPending(): void {
    if (this.#state.status === "pending") return;
    this.#publish({ ...this.#state, status: "pending" });
  }

  /**
   * Merge one validated batch.
   *
   * A batch for a *different* stream replaces the entries rather than merging into them:
   * sequences are only comparable inside one stream, so keeping both would silently
   * conflate two numbering spaces. The entries are then bounded by eviction.
   */
  applyBatch(batch: DecodedBatch): void {
    const sameStream = this.#state.streamId === batch.streamId;
    const entries = sameStream
      ? new Map(this.#state.entries)
      : new Map<bigint, DecodedFrameEntry>();
    let dropped = sameStream ? this.#state.droppedEntries : 0;

    for (const entry of batch.entries) entries.set(entry.frame.sequence, entry);
    while (entries.size > this.#capacity) {
      const oldest = entries.keys().next();
      if (oldest.done === true) break;
      entries.delete(oldest.value);
      dropped += 1;
    }

    this.#publish({
      droppedEntries: dropped,
      entries,
      lastError: null,
      status: "ready",
      streamId: batch.streamId,
    });
  }

  /**
   * Record a request-level failure.
   *
   * Every previously recorded outcome survives: a request that failed says nothing about
   * the frames an earlier request already answered, and discarding them would make a
   * transient network problem erase the user's view.
   */
  failRequest(code: string): void {
    this.#publish({ ...this.#state, lastError: code, status: "failed" });
  }

  /**
   * Begin a new stream epoch, or return to idle.
   *
   * Called when the stream identity changes: sequence numbers from a previous stream say
   * nothing about this one.
   */
  reset(streamId: string | null = null): void {
    if (this.#state.streamId === streamId && this.#state.entries.size === 0) return;
    this.#publish({ ...EMPTY_DECODED_REALTIME, streamId });
  }

  /**
   * Discard everything decoded.
   *
   * Called when what the decode *means* has changed — a project switch, a rebind, a
   * cleared binding set. The outcomes are not stale, they are about a different question.
   */
  clear(): void {
    if (this.#state === EMPTY_DECODED_REALTIME) return;
    this.#publish(EMPTY_DECODED_REALTIME);
  }

  #publish(state: DecodedRealtimeState): void {
    this.#state = state;
    for (const listener of this.#listeners) listener();
  }
}

/** One fresh store. A factory, never a module-level instance — see the header. */
export function createDecodedRealtimeStore(capacity?: number): DecodedRealtimeStore {
  return new DecodedRealtimeStore(capacity);
}
