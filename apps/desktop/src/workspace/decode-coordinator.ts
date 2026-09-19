/**
 * The workspace decode coordinator — the one place a workspace may issue decode HTTP.
 *
 * ```text
 * RealtimeStreamStore    raw frames, as the Worker decoded them
 * WorkspaceSessionStore  which asset decodes which channel
 *   ↓ this coordinator (one per workspace, injected)
 * partitionDecodeWorkSets → one decode-frames request at a time
 *   ↓
 * DecodedRealtimeStore   the outcomes Trace and Plot both read
 * ```
 *
 * Trace and Plot are read-only consumers of the store this class fills: neither issues a
 * request, and neither owns decode state. That is the whole reason this is a coordinator
 * rather than logic inside a panel — a second decode path would be a second answer to
 * "what does this frame mean", and the two would drift.
 *
 * Seven properties are deliberate:
 *
 * * **One request in flight, globally.** Not one per asset and not one per viewport: the
 *   coordinator holds at most a single outstanding `decode-frames` call, so the load it
 *   can place on the Runtime is bounded by construction rather than by how fast snapshots
 *   arrive. A burst of snapshots does not become a burst of requests — the outstanding
 *   response's completion re-runs the pass against the newest viewport, so the loop
 *   advances at the pace of the Runtime rather than the pace of the bus. Serialising the
 *   requests costs nothing structural: the number of requests one viewport needs is
 *   bounded by the number of *bound assets*, not by the number of frames, so an
 *   alternating two-channel viewport is two requests and not one per frame.
 * * **The newest viewport wins.** When a response completes, the coordinator re-reads the
 *   *current* realtime snapshot and the *current* bindings rather than replaying what it
 *   skipped. There is no queue of stale viewports to grind through, so a slow Runtime
 *   cannot make the UI fall minutes behind the bus.
 * * **A binding change is an epoch.** Opening another project, binding, rebinding, unbinding
 *   or clearing bindings makes every recorded outcome an answer to a question nobody is
 *   asking any more, so the decoded store is cleared and the generation advances. A response
 *   that was in flight across the change is discarded instead of published — otherwise a
 *   rebind would briefly show values decoded against the *previous* document.
 * * **A stream change is an epoch too.** Sequence numbers are only comparable inside one
 *   stream, so a new stream id forgets the processed set and resets the store; frames of the
 *   old stream can never be joined to the new one's numbering.
 * * **Every answered frame is remembered, including failures.** A frame that decoded *and* a
 *   frame the Runtime refused are both "the Runtime has spoken about this sequence", so
 *   neither is resubmitted on the next snapshot. Without that, one undecodable frame would be
 *   re-requested on every viewport update for the rest of the capture.
 * * **A request failure is not a frame failure.** A transport error, a 500 or a contract
 *   mismatch is recorded as the request-level `lastError`; it clears nothing, stops nothing,
 *   and leaves the raw Trace and the stream untouched. The same viewport is not retried in a
 *   tight loop, because that would be a busy-wait against an unavailable Runtime.
 * * **Disposal is final.** The unsubscribe function returned by {@link
 *   WorkspaceDecodeCoordinator.start} detaches both subscriptions and advances the
 *   generation, so a response still in flight at unmount cannot publish into a store whose
 *   workspace is gone.
 */

import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  type DecodeFrameSetInput,
  type DecodedFrameOutcome,
  type DecodedFrameSetResult,
} from "../runtime/dbc-decode-client";
import type { RuntimeFrame } from "../runtime/frame-schema";
import type { RealtimeStreamState } from "../runtime/realtime-stream";
import { MAX_WORK_SET_FRAMES, partitionDecodeWorkSets } from "./decode-partition";
import type { DecodeOutcome, DecodedFrameEntry, DecodedRealtimeStore } from "./decoded-realtime";
import type { DecodeBindings, WorkspaceSessionSnapshot } from "./session";

/**
 * The realtime state the coordinator reads.
 *
 * Structural rather than a concrete class so the coordinator depends on *what it needs* —
 * the current viewport and a change signal — instead of on the socket-and-Worker store that
 * happens to provide them. `RealtimeStreamStore` satisfies this as it stands.
 */
export interface DecodeRealtimeSource {
  getState(): RealtimeStreamState;
  subscribe(listener: () => void): () => void;
}

/**
 * The session facts the coordinator reads: the open project, the bindings, and the
 * channel-to-asset lookup. `WorkspaceSessionStore` satisfies this as it stands.
 */
export interface DecodeSessionSource {
  getSnapshot(): WorkspaceSessionSnapshot;
  subscribe(listener: () => void): () => void;
  assetForChannel(channelId: string): string | null;
}

export interface WorkspaceDecodeDeps {
  /** The workspace's one realtime store. The coordinator is a subscriber, not an owner. */
  readonly realtime: DecodeRealtimeSource;
  /** The workspace's one session — the only source of `channelId -> assetId`. */
  readonly session: DecodeSessionSource;
  /** The workspace's one decoded store, and the only thing this coordinator writes. */
  readonly decoded: DecodedRealtimeStore;
  /** The decode call itself, injected so a test can hold a response open. */
  readonly decode: (input: DecodeFrameSetInput) => Promise<DecodedFrameSetResult>;
  /** The largest work set to submit. Defaults to the Runtime's own limit. */
  readonly maxWorkSetFrames?: number;
}

/** The stable request-level codes the coordinator records, one per failure family. */
const TRANSPORT_FAILURE_CODE = "dbc.decode_transport_failed";
const CONTRACT_FAILURE_CODE = "dbc.decode_contract_failed";
const UNKNOWN_FAILURE_CODE = "dbc.decode_failed";

/**
 * One workspace's decode loop.
 *
 * Constructed by the workspace and started once; the store it writes and the subscription
 * it holds both live exactly as long as the workspace mount does, never as long as a
 * project does — a project switch is an epoch inside the loop, not a new loop.
 */
export class WorkspaceDecodeCoordinator {
  readonly #deps: WorkspaceDecodeDeps;
  readonly #maxWorkSetFrames: number;
  #inFlight = false;
  /** Advanced by every change that invalidates an in-flight answer. */
  #generation = 0;
  #streamId: string | null = null;
  /** The sequences the Runtime has already answered for, in the current epoch. */
  #processed = new Set<bigint>();
  /** The viewport signature that just failed, so it is not resubmitted unchanged. */
  #blockedSignature: string | null = null;
  #openProjectPath: string | null = null;
  #bindings: DecodeBindings | null = null;
  #unsubscribe: readonly (() => void)[] = [];

  constructor(deps: WorkspaceDecodeDeps) {
    this.#deps = deps;
    this.#maxWorkSetFrames = deps.maxWorkSetFrames ?? MAX_WORK_SET_FRAMES;
  }

  /**
   * How many sequences are currently suppressed from being decoded again.
   *
   * Diagnostics, not policy: no behaviour reads it and no UI shows it. It exists so the *bound* on
   * duplicate-suppression state can be asserted directly instead of inferred from timing.
   */
  get processedCount(): number {
    return this.#processed.size;
  }

  /**
   * Subscribe to the two stores and begin decoding.
   *
   * Returns the unsubscribe function. Calling it detaches both subscriptions and advances
   * the generation, so a response still in flight cannot publish into a store whose
   * workspace has been torn down.
   */
  start(): () => void {
    const session = this.#deps.session.getSnapshot();
    this.#openProjectPath = session.openedProject?.projectPath ?? null;
    this.#bindings = session.dbcBindings;
    this.#unsubscribe = [
      this.#deps.realtime.subscribe(() => this.#onRealtime()),
      this.#deps.session.subscribe(() => this.#onSession()),
    ];
    this.#onRealtime();
    return () => {
      for (const unsubscribe of this.#unsubscribe) unsubscribe();
      this.#unsubscribe = [];
      this.#generation += 1;
      this.#inFlight = false;
    };
  }

  /** A new viewport, or a new stream epoch. */
  #onRealtime(): void {
    const state = this.#deps.realtime.getState();
    if (state.streamId !== this.#streamId) {
      // A different stream is a different numbering: nothing recorded so far is comparable.
      this.#streamId = state.streamId;
      this.#processed.clear();
      this.#blockedSignature = null;
      this.#generation += 1;
      this.#deps.decoded.reset(state.streamId);
    }
    this.#pump();
  }

  /** A selection change — most of which are not decode-relevant and must not reset anything. */
  #onSession(): void {
    const snapshot = this.#deps.session.getSnapshot();
    const projectPath = snapshot.openedProject?.projectPath ?? null;
    if (projectPath !== this.#openProjectPath || snapshot.dbcBindings !== this.#bindings) {
      // Project or binding changed: every recorded outcome answered the previous question.
      this.#openProjectPath = projectPath;
      this.#bindings = snapshot.dbcBindings;
      this.#generation += 1;
      this.#processed.clear();
      this.#blockedSignature = null;
      this.#deps.decoded.clear();
    }
    this.#pump();
  }

  /**
   * Submit the next work set, or note that there is more to do.
   *
   * The store is read *now*, not from a cached snapshot: a pass that starts after a slow
   * response must work on the viewport that exists, not the one that was current when the
   * response was requested.
   */
  #pump(): void {
    if (this.#inFlight) {
      // A response is already outstanding. Its `finally` re-runs this pass against the newest
      // viewport, so nothing is queued here and a burst of snapshots collapses into one more
      // pass rather than a backlog of stale viewports.
      return;
    }
    const projectPath = this.#openProjectPath;
    if (projectPath === null) return;

    const state = this.#deps.realtime.getState();
    const viewport = state.snapshot;
    if (viewport === null || viewport.streamId === null) return;

    // Duplicate suppression is bounded by construction. A marker only ever means "this sequence
    // has already been answered", and that is worth remembering only while the sequence can still
    // be handed to the Runtime again — so markers whose frames have fallen out of the viewport are
    // dropped before the set is consulted. Keeping them would make this state grow with the length
    // of the capture, which is the same defect as an unbounded frame buffer, one layer up.
    this.#processed = retainVisibleSequences(this.#processed, viewport.frames);

    const pending = viewport.frames.filter((frame) => !this.#processed.has(frame.sequence));
    if (pending.length === 0) return;

    const signature = `${viewport.streamId}|${pending.map((frame) => frame.sequence).join(",")}`;
    if (signature === this.#blockedSignature) return;

    const work = partitionDecodeWorkSets(
      pending,
      (channelId) => this.#deps.session.assetForChannel(channelId),
      this.#maxWorkSetFrames,
    )[0];
    if (work === undefined) return;

    this.#inFlight = true;
    this.#deps.decoded.markPending();
    const requestGeneration = this.#generation;

    void this.#deps
      .decode({
        assetId: work.assetId,
        frames: work.frames,
        projectPath,
        streamId: viewport.streamId,
      })
      .then((result) => {
        // An answer to a question that has been superseded is not an answer.
        if (requestGeneration !== this.#generation) return;
        this.#deps.decoded.applyBatch({
          assetId: work.assetId,
          entries: result.records.map(
            (record): DecodedFrameEntry => ({
              assetId: work.assetId,
              frame: record.frame,
              outcome: toDecodeOutcome(record.outcome),
            }),
          ),
          streamId: result.streamId,
        });
        for (const record of result.records) this.#processed.add(record.frame.sequence);
        this.#blockedSignature = null;
      })
      .catch((cause: unknown) => {
        if (requestGeneration !== this.#generation) return;
        // Remember *this* viewport as answered-badly: the frames stay unprocessed (so a
        // later, different viewport may retry them) but this exact one is not resubmitted.
        this.#blockedSignature = signature;
        this.#deps.decoded.failRequest(failureCode(cause));
      })
      .finally(() => {
        this.#inFlight = false;
        // Unconditional: a completed response may have left more runs in the viewport, and
        // the loop continues until every decodable frame has been answered. One request at a
        // time is enforced by `#inFlight`, not by this call.
        this.#pump();
      });
  }
}

/**
 * Project the decode client's outcome union onto the decoded store's.
 *
 * The two unions say the same thing in different shapes — one is what the wire produced,
 * the other is what a UI branches on — and this is the single place they are joined, so the
 * store never has to know about `decoded`/`failure` pairs and the client never has to know
 * about `status`.
 */
function toDecodeOutcome(outcome: DecodedFrameOutcome): DecodeOutcome {
  if (outcome.decoded === null) {
    return { code: outcome.failure.code, status: "failed" };
  }
  return {
    messageName: outcome.decoded.messageName,
    signals: outcome.decoded.signals.map((signal) => ({
      choiceLabel: signal.choiceLabel,
      name: signal.name,
      physicalValue: signal.physicalValue,
      rawValue: signal.rawValue,
      unit: signal.unit,
    })),
    status: "decoded",
  };
}

/**
 * The stable code a request-level failure is recorded under.
 *
 * The Runtime's own diagnostic code is preferred when there is one, because it is what a
 * user can act on; the three fallbacks say which *family* failed without inventing a code
 * the Runtime never used. Nothing here renders the error's message: a transport error's text
 * can carry an address, and the UI has no business showing one.
 */
function failureCode(cause: unknown): string {
  if (cause instanceof RuntimeDbcApiError) return cause.code;
  if (cause instanceof RuntimeDbcTransportError) return TRANSPORT_FAILURE_CODE;
  if (cause instanceof RuntimeDbcContractError) return CONTRACT_FAILURE_CODE;
  return UNKNOWN_FAILURE_CODE;
}

/**
 * The processed markers that survive one viewport.
 *
 * A marker is a promise that a sequence has already been answered, and it is only meaningful while
 * that sequence can still be submitted again. Once a frame has fallen out of the bounded viewport it
 * can no longer be requested, so its marker answers a question nobody can ask — dropping it is what
 * keeps this state bounded by the viewport rather than by the number of frames the capture has ever
 * carried.
 *
 * ```text
 *   viewport A            1 .. 20000      processed ⊇ 1..20000
 *   viewport B        10001 .. 30000      processed becomes 10001..20000, never 1..30000
 * ```
 *
 * Pure and exported for that reason: the bound is an invariant worth asserting on its own, without
 * a coordinator, a socket or a decode call in the way.
 */
export function retainVisibleSequences(
  processed: ReadonlySet<bigint>,
  frames: readonly RuntimeFrame[],
): Set<bigint> {
  if (processed.size === 0) return new Set<bigint>();

  const visible = new Set<bigint>();
  for (const frame of frames) visible.add(frame.sequence);

  const retained = new Set<bigint>();
  for (const sequence of processed) {
    if (visible.has(sequence)) retained.add(sequence);
  }
  return retained;
}
