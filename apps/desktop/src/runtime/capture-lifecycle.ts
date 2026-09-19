/**
 * The workspace capture lifecycle — who started the capture, and who has to stop it.
 *
 * ```text
 *   WorkspaceSessionProvider / DockWorkspace mount
 *     ↓ acquire()                       "this workspace wants a capture to be live"
 *   CaptureLifecycle
 *     ↓ start()  → true   phase `owned`      we started it: we will stop it
 *     ↓ start()  → false  phase `observed`   someone else's capture: we touch nothing
 *   mount unmounts
 *     ↓ release()                       "nothing wants it any more"
 *     ↓ stop()   (only when `owned`)
 * ```
 *
 * The defect this replaces was a closure boolean set inside a fire-and-forget async
 * effect. Three things could go wrong with it, and they are the three things this type
 * exists to make impossible:
 *
 * * **The start resolved after the effect had already been cleaned up.** The cleanup read
 *   `ownsCapture` while it was still `false`, so nothing ever stopped the capture that the
 *   start was about to begin. Here the cleanup only *records intent*; the handler that
 *   learns the capture is owned is the one that notices nobody wants it and stops it.
 * * **The effect ran twice** (React StrictMode replays setup → cleanup → setup in
 *   development). A second `start` would be a second POST to an endpoint that answers
 *   `capture.already_running`, and the second answer (`false`) would look like "somebody
 *   else's capture" while the first one's ownership had already been thrown away. Here
 *   `acquire` is idempotent: intent is a boolean, not a count, and the in-flight start is
 *   never abandoned by a replay.
 * * **The stop rejected.** `void stopCapture()` inside a cleanup is an unobserved
 *   rejection, which in a renderer is a console error at best and a crash at worst. Here
 *   a failed stop is a recorded `failureCode` on a `failed` phase — observed, actionable,
 *   and never rethrown into a promise nobody holds.
 *
 * Two properties are deliberate:
 *
 * * **Attaching is not owning.** `capture.already_running` means a capture exists that
 *   this workspace did not begin, so `start()` resolves `false` and this lifecycle holds
 *   no stop obligation. Stopping a capture somebody else owns would be a worse bug than
 *   leaking one.
 * * **No retries.** A failed start or stop is terminal until the workspace remounts.
 *   Retrying a capture control-plane call would be an unbounded loop against a Runtime
 *   that is not answering, which is the same defect as an unbounded reconnect.
 */

/**
 * Where the lifecycle is, in its own terms.
 *
 * `owned` and `observed` are deliberately different states even though both mean "a
 * capture is running": only `owned` carries the obligation to stop it, and a single state
 * would have to answer "did I start this?" with a side channel.
 */
export type CapturePhase = "idle" | "starting" | "owned" | "observed" | "stopping" | "failed";

export interface CaptureLifecycleState {
  readonly phase: CapturePhase;
  /** True between {@link CaptureLifecycle.acquire} and {@link CaptureLifecycle.release}. */
  readonly wanted: boolean;
  /** The Runtime's stable code for the last failed start or stop, or `null`. */
  readonly failureCode: string | null;
}

export interface CaptureLifecycleDeps {
  /**
   * Start the capture.
   *
   * `true` means this lifecycle now **owns** it and must stop it; `false` means the
   * Runtime reported the capture as already running, so this lifecycle is only observing
   * it and must leave it alone.
   */
  readonly start: () => Promise<boolean>;
  /** Stop the capture this lifecycle owns. */
  readonly stop: () => Promise<void>;
}

/**
 * The code recorded when a capture call failed without a stable code of its own.
 *
 * One constant rather than an exception's text: a message can carry an address or a
 * payload, and a UI has no business showing either.
 */
export const CAPTURE_FAILURE_CODE = "capture.lifecycle_failed";

const IDLE_STATE: CaptureLifecycleState = { failureCode: null, phase: "idle", wanted: false };

/**
 * One workspace's capture ownership.
 *
 * Intent is set with {@link acquire} / {@link release}; everything else follows from
 * reconciling that intent against the phase, one start or stop at a time.
 */
export class CaptureLifecycle {
  readonly #deps: CaptureLifecycleDeps;
  #state: CaptureLifecycleState = { ...IDLE_STATE };
  readonly #listeners = new Set<() => void>();
  #wanted = false;
  /** True while a start or stop is in flight, so two reconciliations never overlap. */
  #pumping = false;
  #settled: Promise<void> = Promise.resolve();

  constructor(deps: CaptureLifecycleDeps) {
    this.#deps = deps;
  }

  getState = (): CaptureLifecycleState => this.#state;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  /** Declare that this workspace wants a capture to be live. Idempotent. */
  acquire(): void {
    this.#wanted = true;
    this.#pump();
  }

  /** Declare that nothing wants the capture any more. Idempotent. */
  release(): void {
    this.#wanted = false;
    this.#pump();
  }

  /**
   * Resolve once no start or stop is in flight.
   *
   * A seam, not a production path: production sets intent and renders whatever the state
   * says. It never rejects — a failed start or stop is a phase, not a rejection.
   */
  settled(): Promise<void> {
    return this.#settled;
  }

  #pump(): void {
    if (this.#pumping) return;
    this.#pumping = true;
    this.#settled = this.#run();
  }

  /**
   * Reconcile intent against the phase until they agree.
   *
   * The loop re-reads `#wanted` after every `await`, which is what makes
   * "unmount before the start resolves" work: the decision to stop is taken by the
   * continuation that learns the capture is owned, not by the cleanup that ran earlier.
   */
  async #run(): Promise<void> {
    try {
      for (;;) {
        if (this.#wanted && this.#state.phase === "idle") {
          this.#set({ failureCode: null, phase: "starting" });
          try {
            const owned = await this.#deps.start();
            this.#set({ failureCode: null, phase: owned ? "owned" : "observed" });
          } catch (cause: unknown) {
            this.#set({ failureCode: failureCodeOf(cause), phase: "failed" });
          }
          continue;
        }
        if (!this.#wanted && this.#state.phase === "owned") {
          this.#set({ failureCode: null, phase: "stopping" });
          try {
            await this.#deps.stop();
            this.#set({ failureCode: null, phase: "idle" });
          } catch (cause: unknown) {
            this.#set({ failureCode: failureCodeOf(cause), phase: "failed" });
          }
          continue;
        }
        if (!this.#wanted && this.#state.phase === "observed") {
          // Somebody else's capture, and we are done watching it. There is nothing to
          // stop and nothing to remember.
          this.#set({ failureCode: null, phase: "idle" });
          continue;
        }
        return;
      }
    } finally {
      this.#pumping = false;
    }
  }

  #set(patch: Partial<CaptureLifecycleState>): void {
    const next: CaptureLifecycleState = { ...this.#state, ...patch, wanted: this.#wanted };
    const changed =
      next.phase !== this.#state.phase ||
      next.failureCode !== this.#state.failureCode ||
      next.wanted !== this.#state.wanted;
    this.#state = next;
    if (!changed) return;
    for (const listener of this.#listeners) listener();
  }
}

/**
 * The stable code a capture failure is recorded under.
 *
 * The Runtime's own code is preferred when the failure carries one, because it is what a
 * user can act on; the fallback says only which family failed without inventing a code the
 * Runtime never used. The message is deliberately never read.
 */
function failureCodeOf(cause: unknown): string {
  if (typeof cause === "object" && cause !== null && "code" in cause) {
    const code = (cause as { code: unknown }).code;
    if (typeof code === "string" && code !== "") return code;
  }
  return CAPTURE_FAILURE_CODE;
}

/** The workspace's capture lifecycle, wired to the Runtime's capture control plane. */
export function createCaptureLifecycle(deps: CaptureLifecycleDeps): CaptureLifecycle {
  return new CaptureLifecycle(deps);
}
