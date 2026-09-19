/**
 * The capture control plane — the two calls that turn the virtual source on and off.
 *
 * ```text
 *   startVirtualCapture()  POST /capture/start  → true   we started it; somebody must stop it
 *                                              → false  the Runtime answered
 *                                                       `capture.already_running`: a capture
 *                                                       exists that we did not begin
 *   stopCapture()          POST /capture/stop   → void
 * ```
 *
 * Both calls go through the shared Runtime transport floor (`./runtime-http`), which owns
 * the base URL, `fetch`, the five-field error envelope and the transport-versus-contract
 * split. What stays here is only what is this endpoint's own: the URL paths, the request
 * body, and the one piece of capture-specific meaning — that
 * `capture.already_running` means "attach, do not own" rather than "failed".
 */

import {
  RUNTIME_URL,
  RuntimeDbcApiError,
  readResponse,
  runtimeFetch,
} from "./runtime-http";

const CAPTURE_START_PATH = "/capture/start";
const CAPTURE_STOP_PATH = "/capture/stop";

/**
 * The Runtime's own code for "a capture is already running".
 *
 * Branched on instead of the 409 status it happens to arrive with: the status is how the
 * contract is delivered, the code is what it means, and a client keyed on the status
 * would read every future 409 as "already running" too.
 */
const CAPTURE_ALREADY_RUNNING = "capture.already_running";

/**
 * Start the deterministic V0.1 source.
 *
 * Returns `true` when this caller started the capture and therefore owns stopping it, and
 * `false` when the Runtime reported one already running. Every other outcome — an
 * unreachable Runtime, a non-2xx answer, an unreadable failure — is raised as the shared
 * boundary's own failure type rather than reduced to a boolean, because "I could not ask"
 * and "somebody is already capturing" are not the same answer.
 */
export async function startVirtualCapture(): Promise<boolean> {
  try {
    await readResponse(
      await runtimeFetch(`${RUNTIME_URL}${CAPTURE_START_PATH}`, {
        body: JSON.stringify({
          batch_size: 250,
          channel_count: 1,
          is_fd: false,
          rate_hz: 1_000,
          seed: 1,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      }),
    );
    return true;
  } catch (cause: unknown) {
    if (cause instanceof RuntimeDbcApiError && cause.code === CAPTURE_ALREADY_RUNNING) {
      return false;
    }
    throw cause;
  }
}

/** Stop the capture. Idempotent on the Runtime side; a failure is raised, never swallowed. */
export async function stopCapture(): Promise<void> {
  await readResponse(await runtimeFetch(`${RUNTIME_URL}${CAPTURE_STOP_PATH}`, { method: "POST" }));
}
