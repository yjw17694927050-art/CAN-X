/**
 * The Runtime DBC asset client — the renderer's typed view of `POST /dbc/assets`.
 *
 * This module belongs to the **Runtime HTTP boundary**, alongside
 * `capture-client.ts`. It is not the desktop bridge: `src/desktop/dbc-file-bridge.ts`
 * talks IPC to Rust and turns a user's native-dialog choice into
 * `SelectedDbcContent`, and this file talks HTTP to the Python Runtime and turns
 * submitted content into a project-owned asset. Neither knows how the other works,
 * which is what keeps a filesystem decision out of an HTTP request and a Runtime
 * concern out of a dialog.
 *
 * ```text
 * SelectedDbcContent { sourceName, contentBase64 }
 *   ↓ caller maps the two names (never the values)
 * ImportDbcAssetInput { projectPath, sourceName, contentBase64 }
 *   ↓ POST http://127.0.0.1:8765/dbc/assets
 *   ↓ { project_path, source_name, content_base64 }
 * Python Runtime — project DBC import authority
 *   ↓ 201 { asset_id, source_name, sha256, size_bytes, encoding, imported_at }
 * RuntimeDbcAsset { assetId, sourceName, sha256, sizeBytes, encoding, importedAt }
 * ```
 *
 * Five properties are deliberate:
 *
 * * **The content is opaque here.** This client never Base64-decodes, never
 *   re-encodes, never parses DBC, never hashes and never touches a filesystem. The
 *   Runtime is the authority for every one of those; a second implementation here
 *   would be a second answer to the same question.
 * * **`project_path` is not a file location.** It is the CAN-X project the Runtime
 *   API already requires a caller to name, and it is safe to send to the localhost
 *   Runtime. The path of the *external DBC file the user picked* is a different
 *   thing entirely and is not a field of this contract — there is nowhere here to
 *   put one.
 * * **The response is validated, never asserted.** `response.json() as
 *   RuntimeDbcAsset` would let a future Runtime field, a renamed key or a
 *   stringified number travel into every caller as though it were part of the
 *   contract. Every field is checked and a fresh object is built from exactly the
 *   six the contract declares.
 * * **Runtime diagnoses survive.** A structured Runtime failure is re-raised with
 *   its status, code, message, details, recoverability and source intact, so
 *   `dbc.parse_failed`, `dbc.asset_storage_failed`, `project.not_found` and
 *   `api.request_validation_failed` stay four different facts instead of collapsing
 *   into "DBC import failed". No second taxonomy is invented for them.
 * * **A failure never echoes the request.** Neither error type has a field into
 *   which a payload could leak, and no message is built from the submitted content,
 *   the project path or the response body.
 */

/** Where the packaged Runtime sidecar listens. Same origin as every other client. */
const RUNTIME_URL = "http://127.0.0.1:8765";

/** The Runtime DBC asset collection. */
const DBC_ASSETS_PATH = "/dbc/assets";

/** One project-owned DBC asset, as the Runtime describes it. */
export interface RuntimeDbcAsset {
  /** The asset's stable identifier inside the project. */
  readonly assetId: string;
  /** The name the file had when it was imported. Provenance, never a location. */
  readonly sourceName: string;
  /** SHA-256 of the stored bytes, computed by the Runtime. */
  readonly sha256: string;
  /** Size of the stored bytes, computed by the Runtime. */
  readonly sizeBytes: number;
  /** The encoding the Runtime resolved the document under. */
  readonly encoding: string;
  /** When the Runtime registered the asset, as the Runtime reported it. */
  readonly importedAt: string;
}

/** The three facts the import contract takes: where, what to call it, what it is. */
export interface ImportDbcAssetInput {
  /** The CAN-X project that will own the imported asset. */
  readonly projectPath: string;
  /** The selected file's basename — provenance for the asset. */
  readonly sourceName: string;
  /** The exact original bytes, standard Base64 with padding, unchanged. */
  readonly contentBase64: string;
}

/**
 * A structured failure the Runtime itself diagnosed.
 *
 * The five fields are the Runtime's own error envelope, carried through rather than
 * translated: `code` stays branchable, `recoverable` keeps its retry semantics,
 * `source` keeps saying *which* boundary refused, and `details` arrives as the
 * Runtime rendered it. Nothing here is reconstructed from the request, so a caller
 * diagnosing a failure can never be shown the payload it sent.
 */
export class RuntimeDbcApiError extends Error {
  /** The HTTP status the Runtime answered with. */
  readonly status: number;
  /** The Runtime's stable diagnostic code, for example `dbc.parse_failed`. */
  readonly code: string;
  /** The Runtime's structured context, passed through unchanged. */
  readonly details: Readonly<Record<string, unknown>>;
  /** Whether the Runtime considers the operation worth retrying. */
  readonly recoverable: boolean;
  /** Which Runtime boundary produced the diagnosis. */
  readonly source: string;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Readonly<Record<string, unknown>>,
    recoverable: boolean,
    source: string,
  ) {
    super(`${code}: ${message}`);
    this.name = "RuntimeDbcApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.recoverable = recoverable;
    this.source = source;
  }
}

/**
 * The Runtime could not be reached, or did not answer with its own envelope.
 *
 * A transport failure is about the *exchange*, not about DBC: the sidecar is not
 * listening, the connection dropped, or something in front of the Runtime answered
 * with HTML. It carries a static message because the alternative — forwarding a
 * layer's text — is how a proxy's error page, a URL or an internal address ends up
 * in a user-facing diagnosis.
 */
export class RuntimeDbcTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RuntimeDbcTransportError";
  }
}

/**
 * The Runtime answered successfully with something this contract does not describe.
 *
 * Distinct from {@link RuntimeDbcTransportError} on purpose: the exchange worked and
 * the Runtime spoke, so the disagreement is between the Runtime's contract and this
 * client's — a defect to fix, not a condition to retry.
 */
export class RuntimeDbcContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RuntimeDbcContractError";
  }
}

/** The one transport message, so every unreachable-Runtime story reads alike. */
const TRANSPORT_MESSAGE = "The CAN-X Runtime could not be reached.";

/** The one envelope message for a non-2xx answer that is not the shared envelope. */
const UNREADABLE_FAILURE_MESSAGE =
  "The CAN-X Runtime answered with a failure this client cannot interpret.";

/** The one contract message for a 2xx answer this client cannot accept. */
const UNREADABLE_ASSET_MESSAGE =
  "The CAN-X Runtime answered with an asset payload that does not match the contract.";

/**
 * Import one already-selected DBC document as a project-owned asset.
 *
 * The request is built from three named fields and sent as JSON. Nothing is
 * transformed on the way: `contentBase64` is the caller's string, character for
 * character, so the digest of what the Runtime stores is the digest of what the
 * user chose. Whether the content is a valid DBC document is decided by the Rust
 * bridge that read it and by the Runtime domain that parses it — never here.
 *
 * Args:
 *   input: The project to import into, the provenance name, and the exact bytes.
 *
 * Returns:
 *   The imported asset, projected onto {@link RuntimeDbcAsset}.
 *
 * Throws:
 *   {@link RuntimeDbcApiError} when the Runtime diagnosed the failure itself.
 *   {@link RuntimeDbcTransportError} when the Runtime could not be reached, or
 *   answered with a failure that is not the shared envelope.
 *   {@link RuntimeDbcContractError} when the Runtime answered successfully with a
 *   payload this contract does not describe.
 */
export async function importDbcAsset(input: ImportDbcAssetInput): Promise<RuntimeDbcAsset> {
  const response = await post(input);
  const payload = await readJson(response);

  if (!response.ok) {
    const envelope = readErrorEnvelope(payload);
    if (envelope === null) {
      throw new RuntimeDbcTransportError(UNREADABLE_FAILURE_MESSAGE);
    }
    throw new RuntimeDbcApiError(
      response.status,
      envelope.code,
      envelope.message,
      envelope.details,
      envelope.recoverable,
      envelope.source,
    );
  }

  return readAsset(payload);
}

/**
 * Send the import request.
 *
 * The only place a request is built, and the only place a rejection from `fetch`
 * is turned into a diagnosis. The rejection's own text is never forwarded: it can
 * carry a URL, an address or a browser message about the network, none of which
 * tells a user anything they can act on and all of which is more than they should
 * be shown.
 */
async function post(input: ImportDbcAssetInput): Promise<Response> {
  try {
    return await fetch(`${RUNTIME_URL}${DBC_ASSETS_PATH}`, {
      body: JSON.stringify({
        project_path: input.projectPath,
        source_name: input.sourceName,
        content_base64: input.contentBase64,
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    });
  } catch {
    throw new RuntimeDbcTransportError(TRANSPORT_MESSAGE);
  }
}

/**
 * Read the response body as JSON, or report that it was not JSON.
 *
 * Returns `undefined` rather than throwing so that the *caller* can decide what a
 * non-JSON body means, which differs by status: in front of a failure response it is
 * a transport problem, behind a success response it is a contract problem.
 */
async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/** The Runtime's error envelope, narrowed to the five fields it promises. */
interface RuntimeErrorEnvelope {
  readonly code: string;
  readonly message: string;
  readonly details: Readonly<Record<string, unknown>>;
  readonly recoverable: boolean;
  readonly source: string;
}

/**
 * Validate a failure body against the shared envelope.
 *
 * `null` means "this is not the Runtime's own diagnosis" — the shape the
 * application boundary of `canx.api.errors` defines. Checked rather than believed
 * for the same reason the success payload is: a `{"detail": …}` from a framework, a
 * gateway or a future Runtime version must be reported as an uninterpretable
 * failure, not decorated with a code it never claimed.
 */
function readErrorEnvelope(payload: unknown): RuntimeErrorEnvelope | null {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return null;
  const record = payload as Record<string, unknown>;
  const code = record["code"];
  const message = record["message"];
  const details = record["details"];
  const recoverable = record["recoverable"];
  const source = record["source"];
  if (
    typeof code !== "string" ||
    typeof message !== "string" ||
    typeof recoverable !== "boolean" ||
    typeof source !== "string" ||
    typeof details !== "object" ||
    details === null ||
    Array.isArray(details)
  ) {
    return null;
  }
  // Copied, not aliased: the returned envelope must not be a live view of a body
  // that some other layer still holds.
  return { code, message, details: { ...(details as Record<string, unknown>) }, recoverable, source };
}

/**
 * Validate a success body and project it onto {@link RuntimeDbcAsset}.
 *
 * A fresh object is built from exactly the six declared fields. Checking is what
 * makes "the Runtime changed" a loud contract failure instead of a wrong number
 * silently becoming `NaN` at some later consumer, and building fresh is what keeps
 * an unannounced field from spreading to every caller as though it were supported.
 */
function readAsset(payload: unknown): RuntimeDbcAsset {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new RuntimeDbcContractError(UNREADABLE_ASSET_MESSAGE);
  }
  const record = payload as Record<string, unknown>;
  return {
    assetId: readString(record["asset_id"]),
    sourceName: readString(record["source_name"]),
    sha256: readString(record["sha256"]),
    sizeBytes: readSize(record["size_bytes"]),
    encoding: readString(record["encoding"]),
    importedAt: readString(record["imported_at"]),
  };
}

/** Read one declared string field, or refuse the payload. */
function readString(value: unknown): string {
  if (typeof value !== "string") throw new RuntimeDbcContractError(UNREADABLE_ASSET_MESSAGE);
  return value;
}

/**
 * Read the byte count, or refuse the payload.
 *
 * A count that is not a finite non-negative integer cannot describe stored bytes, so
 * it is not accepted as one — `"21"`, `null` and `NaN` are three different malformed
 * payloads and one refusal.
 */
function readSize(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new RuntimeDbcContractError(UNREADABLE_ASSET_MESSAGE);
  }
  return value;
}
