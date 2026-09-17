/**
 * The Runtime DBC client — the renderer's typed view of the DBC HTTP surface.
 *
 * This module belongs to the **Runtime HTTP boundary**, alongside
 * `capture-client.ts`. It is not the desktop bridge: `src/desktop/dbc-file-bridge.ts`
 * talks IPC to Rust and turns a user's native-dialog choice into
 * `SelectedDbcContent`, and this file talks HTTP to the Python Runtime and turns
 * submitted content into a project-owned asset and registered assets back into typed
 * read models. Neither knows how the other works, which is what keeps a filesystem
 * decision out of an HTTP request and a Runtime concern out of a dialog.
 *
 * Two directions live here, and only these two:
 *
 * ```text
 * WRITE (import)
 *   SelectedDbcContent { sourceName, contentBase64 }
 *     ↓ caller maps the two names (never the values)
 *   ImportDbcAssetInput { projectPath, sourceName, contentBase64 }
 *     ↓ POST http://127.0.0.1:8765/dbc/assets
 *   RuntimeDbcAsset
 *
 * READ (list / get / database)
 *   listDbcAssets(projectPath)                 ↓ GET /dbc/assets?project_path=…
 *   getDbcAsset(projectPath, assetId)          ↓ GET /dbc/assets/{assetId}?project_path=…
 *   getDbcDatabase(projectPath, assetId)       ↓ GET /dbc/assets/{assetId}/database?project_path=…
 *     ↓ validated against the Runtime's contract
 *   RuntimeDbcAsset[] / RuntimeDbcAsset / RuntimeDbcDatabase
 * ```
 *
 * Seven properties are deliberate:
 *
 * * **The content is opaque here.** This client never Base64-decodes, never
 *   re-encodes, never parses DBC, never hashes and never touches a filesystem. The
 *   Runtime is the authority for every one of those; a second implementation here
 *   would be a second answer to the same question.
 * * **`projectPath` is not a file location.** It is the CAN-X project the Runtime
 *   API already requires a caller to name, and it is safe to send to the localhost
 *   Runtime. The path of the *external DBC file the user picked* is a different
 *   thing entirely and is not a field of this contract — there is nowhere here to
 *   put one, and the read models below carry no path either.
 * * **Every response is validated, never asserted.** `response.json() as
 *   RuntimeDbcDatabase` would let a future Runtime field, a renamed key or a
 *   stringified number travel into every caller as though it were part of the
 *   contract. Every field — down through nested signals, choices and nodes — is
 *   checked and a fresh object is built from exactly the fields the contract
 *   declares, in the Desktop's own camelCase shape. A malformed nested element
 *   refuses the whole payload: there is no half-valid object.
 * * **The wire shape stops here.** `snake_case` Runtime JSON is mapped onto the
 *   camelCase Desktop model in this module and nowhere else, so a UI can consume a
 *   typed object without ever knowing the Runtime's field names.
 * * **`projectPath` is an argument, not a global.** None of these functions read a
 *   module-level path, a store or a "current project": the caller names the project
 *   on every call, which is what lets a future Project Workspace exist without this
 *   layer having to learn about it.
 * * **Runtime diagnoses survive.** A structured Runtime failure is re-raised with
 *   its status, code, message, details, recoverability and source intact, so
 *   `project.not_found`, `dbc.asset_not_found` and `dbc.asset_integrity_failed` stay
 *   three different facts instead of collapsing into "Failed to load DBC". No second
 *   taxonomy is invented for them.
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

/**
 * One declared `VAL_` entry on a signal: a raw value and the label naming it.
 *
 * `value` keeps its sign: a label is a statement about a raw value, and a signed
 * signal's raw values may be negative.
 */
export interface RuntimeDbcChoice {
  readonly value: number;
  readonly label: string;
}

/**
 * One canonical signal **definition**, as the Runtime describes it.
 *
 * This is the definition, not a decoded value: `factor`/`offset` describe the
 * `physical = raw * factor + offset` mapping without applying it, and `startBit` is
 * carried exactly as the document expresses it. Fields the Runtime reports as
 * `null` are carried as `null` here rather than dropped, so "the document did not
 * declare a minimum" stays distinguishable from "the field never arrived".
 */
export interface RuntimeDbcSignal {
  readonly name: string;
  readonly startBit: number;
  readonly length: number;
  /** `little_endian` (Intel) or `big_endian` (Motorola), in the Runtime's terms. */
  readonly byteOrder: string;
  readonly isSigned: boolean;
  readonly isFloat: boolean;
  readonly factor: number;
  readonly offset: number;
  readonly minimum: number | null;
  readonly maximum: number | null;
  readonly unit: string | null;
  readonly receivers: readonly string[];
  readonly choices: readonly RuntimeDbcChoice[];
  readonly isMultiplexer: boolean;
  readonly multiplexerSignal: string | null;
  readonly multiplexerIds: readonly number[] | null;
  readonly comment: string | null;
}

/**
 * One canonical message definition.
 *
 * `frameId` travels with `isExtended`: the pair, never the id alone, decides which
 * identifier space the message addresses.
 */
export interface RuntimeDbcMessage {
  readonly frameId: number;
  readonly name: string;
  readonly length: number;
  readonly isExtended: boolean;
  readonly isFd: boolean;
  readonly senders: readonly string[];
  readonly comment: string | null;
  readonly cycleTime: number | null;
  readonly signals: readonly RuntimeDbcSignal[];
}

/** One canonical network node (an ECU in the document's own vocabulary). */
export interface RuntimeDbcNode {
  readonly name: string;
  readonly comment: string | null;
}

/**
 * The canonical content of one imported DBC document, as a typed Desktop model.
 *
 * No path, no traceback and no parser internals: this is the document as CAN-X
 * understands it, in the order the source declared it. It carries no provenance —
 * an asset's metadata is {@link RuntimeDbcAsset}'s job, and the two are read
 * through different endpoints.
 */
export interface RuntimeDbcDatabase {
  readonly version: string | null;
  readonly messages: readonly RuntimeDbcMessage[];
  readonly nodes: readonly RuntimeDbcNode[];
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
  /** The Runtime's stable diagnostic code, for example `dbc.asset_not_found`. */
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

/** The one contract message for a 2xx answer whose asset shape is wrong. */
const UNREADABLE_ASSET_MESSAGE =
  "The CAN-X Runtime answered with an asset payload that does not match the contract.";

/** The one contract message for a 2xx answer whose asset-collection shape is wrong. */
const UNREADABLE_ASSET_LIST_MESSAGE =
  "The CAN-X Runtime answered with an asset list payload that does not match the contract.";

/** The one contract message for a 2xx answer whose DBC database shape is wrong. */
const UNREADABLE_DATABASE_MESSAGE =
  "The CAN-X Runtime answered with a DBC database payload that does not match the contract.";

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
  const payload = await readResponse(await post(input));
  return readAsset(payload);
}

/**
 * List the DBC assets one project owns.
 *
 * Args:
 *   projectPath: The CAN-X project whose assets are listed. Required and explicit —
 *     this function has no notion of a "current" project and never guesses one.
 *
 * Returns:
 *   The project's assets in the Runtime's own deterministic order, each projected
 *   onto {@link RuntimeDbcAsset}.
 *
 * Throws:
 *   {@link RuntimeDbcApiError} when the Runtime diagnosed the failure itself — for
 *   example `project.not_found` when the path is not a CAN-X project.
 *   {@link RuntimeDbcTransportError} when the Runtime could not be reached, or
 *   answered with a failure that is not the shared envelope.
 *   {@link RuntimeDbcContractError} when the Runtime answered successfully with a
 *   payload this contract does not describe.
 */
export async function listDbcAssets(projectPath: string): Promise<readonly RuntimeDbcAsset[]> {
  const payload = await readResponse(await get(assetCollectionUrl(projectPath)));
  return readAssetList(payload);
}

/**
 * Read one registered asset's metadata.
 *
 * Args:
 *   projectPath: The CAN-X project that owns the asset.
 *   assetId: The asset's identifier, taken from {@link RuntimeDbcAsset.assetId} or a
 *     listing. It is encoded as a single path segment, so an id containing a
 *     separator, a query character or a fragment cannot change the URL's structure.
 *
 * Returns:
 *   The asset, projected onto {@link RuntimeDbcAsset}.
 *
 * Throws:
 *   {@link RuntimeDbcApiError} when the Runtime diagnosed the failure itself — for
 *   example `dbc.asset_not_found` (404) for an unknown id in an existing project,
 *   or `project.not_found` for a path that is not a CAN-X project.
 *   {@link RuntimeDbcTransportError} / {@link RuntimeDbcContractError} as above.
 */
export async function getDbcAsset(
  projectPath: string,
  assetId: string,
): Promise<RuntimeDbcAsset> {
  const payload = await readResponse(await get(assetUrl(projectPath, assetId)));
  return readAsset(payload);
}

/**
 * Read the canonical DBC definition of one registered asset.
 *
 * This is the read model a future DBC workspace renders: the messages, signals,
 * choices and nodes of one project-owned document, with no path and no parser
 * internals. Nothing is decoded here — decoding a *frame* is a different endpoint
 * and a different increment.
 *
 * Args:
 *   projectPath: The CAN-X project that owns the asset.
 *   assetId: The asset's identifier.
 *
 * Returns:
 *   The document's canonical content, projected onto {@link RuntimeDbcDatabase}.
 *
 * Throws:
 *   {@link RuntimeDbcApiError} when the Runtime diagnosed the failure itself — for
 *   example `dbc.asset_not_found` (404), `dbc.asset_integrity_failed` when the
 *   stored copy no longer matches its digest, or `project.not_found`.
 *   {@link RuntimeDbcTransportError} / {@link RuntimeDbcContractError} as above.
 */
export async function getDbcDatabase(
  projectPath: string,
  assetId: string,
): Promise<RuntimeDbcDatabase> {
  const payload = await readResponse(await get(databaseUrl(projectPath, assetId)));
  return readDatabase(payload);
}

/**
 * Send the import request.
 *
 * The only place a write request is built, and the only place a rejection from
 * `fetch` is turned into a diagnosis. The rejection's own text is never forwarded:
 * it can carry a URL, an address or a browser message about the network, none of
 * which tells a user anything they can act on and all of which is more than they
 * should be shown.
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
 * Send a read request to a URL this module built itself.
 *
 * The URL is never assembled from caller input by concatenation: every builder
 * below encodes the project path as a query parameter and the asset id as a path
 * segment, so a `?`, `&`, `#`, `/`, `%` or whitespace in either value is data and
 * never structure. `fetch` carries no body and no caller headers — a GET the
 * Runtime defined.
 */
async function get(url: string): Promise<Response> {
  try {
    return await fetch(url, { method: "GET" });
  } catch {
    throw new RuntimeDbcTransportError(TRANSPORT_MESSAGE);
  }
}

/**
 * Encode the project path as the `project_path` query parameter.
 *
 * `URLSearchParams` applies the query-string encoding rules, so the value is
 * percent-encoded as a whole: it cannot terminate the query, start a second
 * parameter or introduce a fragment. The Runtime decodes it back to exactly the
 * string the caller passed.
 */
function projectQuery(projectPath: string): string {
  return new URLSearchParams({ project_path: projectPath }).toString();
}

/** The asset collection URL for one project: `GET /dbc/assets?project_path=…`. */
function assetCollectionUrl(projectPath: string): string {
  return `${RUNTIME_URL}${DBC_ASSETS_PATH}?${projectQuery(projectPath)}`;
}

/**
 * One asset's URL: `GET /dbc/assets/{assetId}?project_path=…`.
 *
 * The id is encoded with `encodeURIComponent`, which escapes `/`, `?`, `#`, `%`,
 * `&` and whitespace, so the id stays a single path segment. It is a path segment
 * and not a query value because the Runtime's route is `/assets/{asset_id}`.
 */
function assetUrl(projectPath: string, assetId: string): string {
  return `${RUNTIME_URL}${DBC_ASSETS_PATH}/${encodeURIComponent(assetId)}?${projectQuery(projectPath)}`;
}

/** One asset's database URL: `GET /dbc/assets/{assetId}/database?project_path=…`. */
function databaseUrl(projectPath: string, assetId: string): string {
  const base = `${RUNTIME_URL}${DBC_ASSETS_PATH}/${encodeURIComponent(assetId)}/database`;
  return `${base}?${projectQuery(projectPath)}`;
}

/**
 * Read a response as JSON and translate the failure half of the exchange.
 *
 * Splitting success from failure here keeps the two intended meanings apart for
 * every endpoint, not just the one that used to inline it: behind a non-2xx answer,
 * a missing envelope means the reply did not come *from* the Runtime's application
 * boundary (a proxy, a framework or a version that does not speak this contract),
 * which is a transport problem; behind a 2xx answer, a body that is not JSON is the
 * caller's contract problem, diagnosed when the payload is validated.
 */
async function readResponse(response: Response): Promise<unknown> {
  const payload = await readJson(response);
  if (response.ok) return payload;
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
  const record = readObject(payload, UNREADABLE_ASSET_MESSAGE);
  return {
    assetId: readString(record["asset_id"], UNREADABLE_ASSET_MESSAGE),
    sourceName: readString(record["source_name"], UNREADABLE_ASSET_MESSAGE),
    sha256: readString(record["sha256"], UNREADABLE_ASSET_MESSAGE),
    sizeBytes: readCount(record["size_bytes"], UNREADABLE_ASSET_MESSAGE),
    encoding: readString(record["encoding"], UNREADABLE_ASSET_MESSAGE),
    importedAt: readString(record["imported_at"], UNREADABLE_ASSET_MESSAGE),
  };
}

/**
 * Validate the asset-collection body and project it onto an array of assets.
 *
 * Each element is validated by the same {@link readAsset} the single-asset endpoint
 * uses, so a listing and a lookup agree field for field; one malformed element
 * refuses the whole response rather than yielding a list with a hole in it.
 */
function readAssetList(payload: unknown): RuntimeDbcAsset[] {
  const record = readObject(payload, UNREADABLE_ASSET_LIST_MESSAGE);
  return readArray(record["assets"], UNREADABLE_ASSET_LIST_MESSAGE, (item) => readAsset(item));
}

/**
 * Validate a DBC database body and project it onto {@link RuntimeDbcDatabase}.
 *
 * Everything below a database — messages, signals, choices, nodes — is checked by
 * the readers named for it, so a `NaN` factor, a stringified frame id or a missing
 * node name refuses the payload at the point it occurs and never reaches a caller as
 * a plausible-looking object.
 */
function readDatabase(payload: unknown): RuntimeDbcDatabase {
  const message = UNREADABLE_DATABASE_MESSAGE;
  const record = readObject(payload, message);
  return {
    version: readNullableString(record["version"], message),
    messages: readArray(record["messages"], message, (item) => readMessage(item, message)),
    nodes: readArray(record["nodes"], message, (item) => readNode(item, message)),
  };
}

/** Validate one message and project it onto {@link RuntimeDbcMessage}. */
function readMessage(value: unknown, message: string): RuntimeDbcMessage {
  const record = readObject(value, message);
  return {
    frameId: readCount(record["frame_id"], message),
    name: readString(record["name"], message),
    length: readCount(record["length"], message),
    isExtended: readBoolean(record["is_extended"], message),
    isFd: readBoolean(record["is_fd"], message),
    senders: readStringArray(record["senders"], message),
    comment: readNullableString(record["comment"], message),
    cycleTime: readNullableCount(record["cycle_time"], message),
    signals: readArray(record["signals"], message, (item) => readSignal(item, message)),
  };
}

/** Validate one signal and project it onto {@link RuntimeDbcSignal}. */
function readSignal(value: unknown, message: string): RuntimeDbcSignal {
  const record = readObject(value, message);
  return {
    name: readString(record["name"], message),
    startBit: readCount(record["start_bit"], message),
    length: readCount(record["length"], message),
    byteOrder: readString(record["byte_order"], message),
    isSigned: readBoolean(record["is_signed"], message),
    isFloat: readBoolean(record["is_float"], message),
    factor: readFiniteNumber(record["factor"], message),
    offset: readFiniteNumber(record["offset"], message),
    minimum: readNullableFiniteNumber(record["minimum"], message),
    maximum: readNullableFiniteNumber(record["maximum"], message),
    unit: readNullableString(record["unit"], message),
    receivers: readStringArray(record["receivers"], message),
    choices: readArray(record["choices"], message, (item) => readChoice(item, message)),
    isMultiplexer: readBoolean(record["is_multiplexer"], message),
    multiplexerSignal: readNullableString(record["multiplexer_signal"], message),
    multiplexerIds: readNullableCountArray(record["multiplexer_ids"], message),
    comment: readNullableString(record["comment"], message),
  };
}

/** Validate one choice and project it onto {@link RuntimeDbcChoice}. */
function readChoice(value: unknown, message: string): RuntimeDbcChoice {
  const record = readObject(value, message);
  return {
    value: readInteger(record["value"], message),
    label: readString(record["label"], message),
  };
}

/** Validate one node and project it onto {@link RuntimeDbcNode}. */
function readNode(value: unknown, message: string): RuntimeDbcNode {
  const record = readObject(value, message);
  return {
    name: readString(record["name"], message),
    comment: readNullableString(record["comment"], message),
  };
}

/**
 * Read one value as a JSON object, or refuse the payload.
 *
 * Arrays are rejected explicitly: in JavaScript `typeof [] === "object"`, so a
 * payload that answered a list where an object was declared would otherwise pass a
 * naive check and be destructured into `undefined` fields.
 */
function readObject(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value as Record<string, unknown>;
}

/** Read one value as an array, validating each element with `readItem`. */
function readArray<T>(
  value: unknown,
  message: string,
  readItem: (item: unknown) => T,
): T[] {
  if (!Array.isArray(value)) throw new RuntimeDbcContractError(message);
  return value.map((item) => readItem(item));
}

/** Read one declared string field, or refuse the payload. */
function readString(value: unknown, message: string): string {
  if (typeof value !== "string") throw new RuntimeDbcContractError(message);
  return value;
}

/** Read one declared boolean field, or refuse the payload. `"true"` is not `true`. */
function readBoolean(value: unknown, message: string): boolean {
  if (typeof value !== "boolean") throw new RuntimeDbcContractError(message);
  return value;
}

/**
 * Read the byte count, or refuse the payload.
 *
 * A count that is not a finite non-negative integer cannot describe stored bytes, so
 * it is not accepted as one — `"21"`, `null` and `NaN` are three different malformed
 * payloads and one refusal.
 */
function readCount(value: unknown, message: string): number {
  const count = readInteger(value, message);
  if (count < 0) throw new RuntimeDbcContractError(message);
  return count;
}

/**
 * Read a signed safe integer, or refuse the payload.
 *
 * Any sign is accepted here — a multiplexer id or a choice value on a signed signal
 * may legitimately be negative — but `1.5`, `NaN`, `2 ** 53` and `"0"` all refuse:
 * they are a value the contract does not describe.
 */
function readInteger(value: unknown, message: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value;
}

/**
 * Read a finite number, or refuse the payload.
 *
 * `NaN` and `Infinity` are numbers to `typeof` but cannot describe a factor, an
 * offset or a physical bound: they are accepted only as a contract failure, not
 * carried forward to become a wrong value at some later consumer.
 */
function readFiniteNumber(value: unknown, message: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value;
}

/** Read one nullable string field: `null` stays `null`, anything else must be a string. */
function readNullableString(value: unknown, message: string): string | null {
  if (value === null) return null;
  return readString(value, message);
}

/** Read one nullable count: `null` stays `null`, anything else must be a non-negative integer. */
function readNullableCount(value: unknown, message: string): number | null {
  if (value === null) return null;
  return readCount(value, message);
}

/** Read one nullable finite number: `null` stays `null`, anything else must be finite. */
function readNullableFiniteNumber(value: unknown, message: string): number | null {
  if (value === null) return null;
  return readFiniteNumber(value, message);
}

/** Read an array of strings, or refuse the payload. */
function readStringArray(value: unknown, message: string): string[] {
  return readArray(value, message, (item) => readString(item, message));
}

/** Read an array of non-negative integers, or refuse the payload. */
function readCountArray(value: unknown, message: string): number[] {
  return readArray(value, message, (item) => readCount(item, message));
}

/** Read one nullable array of non-negative integers: `null` stays `null`. */
function readNullableCountArray(value: unknown, message: string): number[] | null {
  if (value === null) return null;
  return readCountArray(value, message);
}
