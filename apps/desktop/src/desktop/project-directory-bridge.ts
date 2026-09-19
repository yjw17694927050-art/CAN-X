import { invoke } from "@tauri-apps/api/core";

/**
 * The typed desktop bridge for the project directory the user picks.
 *
 * This module belongs to the **desktop system layer**, not to the Runtime client and
 * not to the project read model. `src/runtime/` describes what a project *is*; this
 * file only learns *where the user pointed*, and it deliberately learns nothing else.
 *
 * ```text
 * renderer
 *   ↓ invoke(SELECT_PROJECT_DIRECTORY_COMMAND)   ← the only argument: none
 * Rust desktop system layer
 *   ↓ native directory picker
 * "C:\canx\projects\Vehicle_Bus"  |  null
 *   ↓ handed on verbatim, or reported as a typed failure
 * string | null
 * ```
 *
 * Four things this module refuses to do, each of them load-bearing:
 *
 * * **It does not name a directory.** There is no parameter to pass, so the renderer
 *   cannot pick a path the user never picked.
 * * **It does not decide what a project is.** Reading the project's definition file,
 *   validating its schema or checking that the directory holds a project at all
 *   belongs to the project read model — one authority, not two.
 * * **It does not touch the filesystem.** No `fs`, no `readFile`, no `existsSync`: a
 *   renderer that reads files directly would bypass the Runtime's project ownership.
 * * **It keeps no project state.** No `currentProject`, no module-level variable: the
 *   caller that knows which project it means passes the string along itself.
 */

/**
 * The Tauri command that opens the native directory picker.
 *
 * Spelled once here so the two halves of the bridge cannot drift apart.
 */
export const SELECT_PROJECT_DIRECTORY_COMMAND = "select_project_directory";

/**
 * The code reported when the bridge itself could not be reached.
 *
 * Distinct from every code the shell reports on purpose: those describe what the
 * *user's choice* was, and this one describes the absence of a bridge — which no
 * amount of choosing a different directory can fix.
 */
export const PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE =
  "desktop.project_directory_bridge_unavailable";

/**
 * A typed failure that crossed the Tauri IPC boundary.
 *
 * Mirrors the `code` / `message` / `recoverable` contract the rest of CAN-X uses, so
 * a caller branches on the same three facts here as it does for an HTTP failure.
 * Messages never contain the directory the user picked.
 */
export class ProjectDirectoryBridgeError extends Error {
  /** Stable diagnostic code, for example `desktop.project_directory_not_a_directory`. */
  readonly code: string;
  /** Whether retrying with a different directory could reasonably succeed. */
  readonly recoverable: boolean;

  constructor(code: string, message: string, recoverable: boolean) {
    super(`${code}: ${message}`);
    this.name = "ProjectDirectoryBridgeError";
    this.code = code;
    this.recoverable = recoverable;
  }
}

/**
 * Open the native directory picker and resolve the directory the user chooses.
 *
 * Returns:
 * * the chosen directory as the shell reported it, character for character — this
 *   module normalises, resolves, expands or truncates nothing, because the string it
 *   returns is the string the caller will hand to the project read model;
 * * `null` when the user dismissed the picker. Cancel is control flow, never an
 *   error, and a caller checks one fact instead of guessing which rejection means
 *   "the user changed their mind".
 *
 * Throws:
 * * {@link ProjectDirectoryBridgeError} when the shell refused the choice — not a
 *   directory, unreadable, or a picker that failed to open. The `code` says which.
 * * {@link ProjectDirectoryBridgeError} with
 *   {@link PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE} when the desktop shell is not
 *   there to answer at all — no shell, a serialization failure on the Rust side, or a
 *   rejection from a layer that has nothing to do with directory selection.
 * * a plain `Error` when the shell answered with something this contract does not
 *   describe. A payload that is neither a string nor `null` is a defect in the
 *   bridge, and it is reported as a contract error rather than being reshaped into a
 *   plausible path or mistaken for an unavailable shell.
 */
export async function selectProjectDirectory(): Promise<string | null> {
  let raw: unknown;
  try {
    raw = await invoke<string | null>(SELECT_PROJECT_DIRECTORY_COMMAND);
  } catch (cause) {
    throw toBridgeError(cause);
  }
  return readSelectedProjectDirectory(raw);
}

/**
 * Validate the shell's answer and project it onto `string | null`.
 *
 * The two legal shapes are checked positively: a string is returned as it arrived,
 * `null` means the user cancelled, and anything else is rejected. A payload that
 * carried an object — however plausibly named its fields — is not searched for a
 * directory to promote out of it.
 */
function readSelectedProjectDirectory(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string") {
    throw new Error(
      "the project directory selection payload must be a string or null",
    );
  }
  return value;
}

/**
 * Turn whatever `invoke` rejected with into a {@link ProjectDirectoryBridgeError}.
 *
 * A rejection that already carries the three structured fields came from the Rust
 * bridge and is passed through intact. Anything else — an unavailable shell, a
 * serialization failure, a rejection from a layer that has nothing to do with this
 * selection — becomes the single "bridge unavailable" diagnosis rather than being
 * decorated with a code it never claimed.
 */
function toBridgeError(cause: unknown): ProjectDirectoryBridgeError {
  if (cause instanceof ProjectDirectoryBridgeError) return cause;
  const shaped = readBridgeErrorShape(cause);
  if (shaped !== null) {
    return new ProjectDirectoryBridgeError(shaped.code, shaped.message, shaped.recoverable);
  }
  return new ProjectDirectoryBridgeError(
    PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE,
    "The desktop project directory bridge is unavailable.",
    false,
  );
}

function readBridgeErrorShape(
  value: unknown,
): { code: string; message: string; recoverable: boolean } | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const code = record["code"];
  const message = record["message"];
  const recoverable = record["recoverable"];
  if (typeof code !== "string" || typeof message !== "string" || typeof recoverable !== "boolean") {
    return null;
  }
  return { code, message, recoverable };
}
