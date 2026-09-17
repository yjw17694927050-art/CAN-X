import { invoke } from "@tauri-apps/api/core";

/**
 * The typed desktop bridge for user-chosen DBC files.
 *
 * This module belongs to the **desktop system layer**, not to the Runtime client.
 * `src/runtime/` talks HTTP to the Python Runtime; this file talks IPC to Rust.
 * Nothing here parses DBC, decodes Base64, stores content, renders a dialog or posts
 * to `POST /dbc/assets` — it turns one Tauri command into one typed value, and that
 * is the whole of its job.
 *
 * ```text
 * renderer
 *   ↓ invoke(SELECT_DBC_CONTENT_COMMAND)   ← the only argument: none
 * Rust desktop system layer
 *   ↓ native dialog → validated bounded exact-byte read
 * { source_name, content_base64 }
 *   ↓ validated here, renamed to the interface this module promises
 * SelectedDbcContent
 * ```
 *
 * The renderer cannot name a file. There is no parameter to pass that would be
 * interpreted as a path, and the payload that comes back carries a **basename** and
 * encoded bytes with no directory in sight.
 */

/**
 * The Tauri command that opens the native file dialog.
 *
 * The name is spelled once here so that the two halves of the bridge cannot drift:
 * the Rust side exports the same string, and both are asserted against each other.
 */
export const SELECT_DBC_CONTENT_COMMAND = "select_dbc_file";

/**
 * The code reported when the bridge itself could not be reached.
 *
 * Distinct from every Rust-side code on purpose: those describe what the *user's
 * choice* was, and this one describes the absence of a bridge — which the user
 * cannot fix by picking a different file.
 */
export const DBC_BRIDGE_UNAVAILABLE_CODE = "desktop.dbc_bridge_unavailable";

/** The content of one DBC file the user explicitly chose in the native dialog. */
export interface SelectedDbcContent {
  /**
   * The file's basename — for example `vehicle.dbc`.
   *
   * Provenance, not a location: it is the name the file had for the user when they
   * picked it, and it is the name the Runtime records when this content is imported.
   */
  readonly sourceName: string;
  /** The exact original file bytes, standard Base64 with padding. */
  readonly contentBase64: string;
}

/**
 * A typed failure that crossed the Tauri IPC boundary.
 *
 * Mirrors the `code` / `message` / `recoverable` contract the rest of CAN-X uses, so
 * a caller branches on the same three facts here as it does for an HTTP failure.
 * Messages never contain a filesystem path.
 */
export class DbcFileBridgeError extends Error {
  /** Stable diagnostic code, for example `desktop.dbc_file_empty`. */
  readonly code: string;
  /** Whether retrying with a different file could reasonably succeed. */
  readonly recoverable: boolean;

  constructor(code: string, message: string, recoverable: boolean) {
    super(`${code}: ${message}`);
    this.name = "DbcFileBridgeError";
    this.code = code;
    this.recoverable = recoverable;
  }
}

/**
 * The fields `POST /dbc/assets` accepts, for the stage that will finally import what
 * this bridge selects.
 *
 * Declared here — and exercised by a contract test — so the mapping from this
 * module's shape onto the Runtime's wire contract is *proven* rather than assumed to
 * line up. This module does not send the request: importing is V0.3-08's job, and
 * duplicating the Runtime client here would put two HTTP clients in one renderer.
 */
export interface RuntimeDbcImportFields {
  readonly source_name: string;
  readonly content_base64: string;
}

/**
 * Open the native file dialog and resolve the DBC file the user chooses.
 *
 * Returns:
 * * the chosen content, as a {@link SelectedDbcContent}, when the user picked a
 *   `.dbc` file the bridge could read within its bound;
 * * `null` when the user dismissed the dialog. Cancel is control flow, never an
 *   error, and a caller checks one fact instead of guessing which rejection means
 *   "the user changed their mind".
 *
 * Throws:
 * * {@link DbcFileBridgeError} when the bridge refused the choice — wrong file type,
 *   empty, too large, unreadable, or a dialog that failed to open. The `code` says
 *   which, and the message never quotes the path.
 * * {@link DbcFileBridgeError} with {@link DBC_BRIDGE_UNAVAILABLE_CODE} when the
 *   desktop shell is not there to answer at all.
 * * a plain `Error` when the shell answered with something this contract does not
 *   describe. That is a defect in the bridge, not a user's choice, and it is
 *   reported as such rather than being quietly reshaped into a plausible value.
 */
export async function selectDbcContent(): Promise<SelectedDbcContent | null> {
  let raw: unknown;
  try {
    raw = await invoke<unknown>(SELECT_DBC_CONTENT_COMMAND);
  } catch (cause) {
    throw toBridgeError(cause);
  }
  return readSelectedDbcContent(raw);
}

/**
 * Map selected content onto the Runtime's import contract.
 *
 * The whole of the mapping is two names: `sourceName` → `source_name` and
 * `contentBase64` → `content_base64`. Nothing is transformed, re-encoded or
 * re-interpreted — the bytes the user chose are the bytes the Runtime will store.
 */
export function toRuntimeImportFields(content: SelectedDbcContent): RuntimeDbcImportFields {
  return {
    source_name: content.sourceName,
    content_base64: content.contentBase64,
  };
}

/**
 * Validate the shell's answer and project it onto {@link SelectedDbcContent}.
 *
 * A fresh object is built from exactly two named fields rather than by trusting the
 * payload's shape. That is the whole point: if the shell ever answered with an extra
 * field — a `path`, a `directory`, anything a future change might add — it would be
 * dropped here rather than leaking into every consumer as an apparently supported
 * property. The payload is checked, not believed.
 */
function readSelectedDbcContent(value: unknown): SelectedDbcContent | null {
  if (value === null) return null;
  const record = asRecord(value, "the DBC selection payload");
  return {
    sourceName: asString(record["source_name"], "source_name"),
    contentBase64: asString(record["content_base64"], "content_base64"),
  };
}

function asRecord(value: unknown, description: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${description} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new Error(`the DBC selection payload field ${field} must be a string`);
  }
  return value;
}

/**
 * Turn whatever `invoke` rejected with into a {@link DbcFileBridgeError}.
 *
 * A rejection that already carries the three structured fields came from the Rust
 * bridge and is passed through intact. Anything else — an unavailable shell, a
 * serialization failure on the Rust side, a rejection from a layer that has nothing
 * to do with DBC — becomes the single "bridge unavailable" diagnosis rather than
 * being decorated with a code it never claimed.
 */
function toBridgeError(cause: unknown): DbcFileBridgeError {
  if (cause instanceof DbcFileBridgeError) return cause;
  const shaped = readBridgeErrorShape(cause);
  if (shaped !== null) {
    return new DbcFileBridgeError(shaped.code, shaped.message, shaped.recoverable);
  }
  return new DbcFileBridgeError(
    DBC_BRIDGE_UNAVAILABLE_CODE,
    "The desktop DBC file bridge is unavailable.",
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
