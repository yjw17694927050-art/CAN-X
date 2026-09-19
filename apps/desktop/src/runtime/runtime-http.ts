/**
 * The Runtime HTTP transport — the shared floor under every Desktop client that talks
 * to the local Runtime.
 *
 * Every client below (`dbc-client.ts`, the DBC decode client, and any later one) speaks
 * to the same `http://127.0.0.1:8765` application boundary, and that boundary has
 * exactly one failure vocabulary: a 2xx answer carries the payload, a non-2xx answer
 * carries the Runtime's five-field error envelope (`code` · `message` · `details` ·
 * `recoverable` · `source`). This module owns that vocabulary **once**, so a second
 * client cannot invent a second translation of it — the moment two clients disagree
 * about what a 500 means, one of them is wrong and a caller has no way to tell which.
 *
 * ```text
 *   request
 *     ↓ fetch
 *   2xx      → payload, validated by the endpoint's own reader
 *   4xx/5xx  → envelope → RuntimeDbcApiError(code, status, details, …)
 *   no envelope behind a failure → RuntimeDbcTransportError
 *   fetch threw / unreachable   → RuntimeDbcTransportError
 * ```
 *
 * Three properties are deliberate:
 *
 * * **The three failure types are defined here, once.** {@link RuntimeDbcApiError} is a
 *   diagnosis the *Runtime* made; {@link RuntimeDbcTransportError} is a failure of the
 *   *exchange*; {@link RuntimeDbcContractError} is a 2xx answer this Desktop does not
 *   understand — a defect to fix, not a condition to retry. They stay three different
 *   facts, and every HTTP client re-uses these three rather than declaring its own.
 *   The `Dbc` in the names is historic: they are the Runtime boundary's types, and
 *   renaming them would be renaming the boundary rather than adding one.
 * * **A failure never echoes the request.** Neither error type has a field into which a
 *   payload could leak, and no message is built from a project path, a request body or
 *   the body text of a response.
 * * **Reading is separated from interpreting.** {@link readResponse} splits success from
 *   failure and does nothing else. *How* a success payload is validated stays the
 *   endpoint's business, because a decode batch, an asset listing and a DBC database are
 *   three different contracts and a shared reader for them would have to be a union of
 *   all three.
 */

/** Where the packaged Runtime sidecar listens. Same origin as every other client. */
export const RUNTIME_URL = "http://127.0.0.1:8765";

/**
 * A structured failure the Runtime itself diagnosed.
 *
 * The five fields are the Runtime's own error envelope, carried through rather than
 * translated: `code` stays branchable, `recoverable` keeps its retry semantics,
 * `source` keeps saying *which* boundary refused, and `details` arrives as the Runtime
 * rendered it. Nothing here is reconstructed from the request, so a caller diagnosing a
 * failure can never be shown the payload it sent.
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
 * A transport failure is about the *exchange*, not about the request's subject: the
 * sidecar is not listening, the connection dropped, or something in front of the
 * Runtime answered with HTML. It carries a static message because the alternative —
 * forwarding a layer's text — is how a proxy's error page, a URL or an internal address
 * ends up in a user-facing diagnosis.
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
export const TRANSPORT_MESSAGE = "The CAN-X Runtime could not be reached.";

/** The one envelope message for a non-2xx answer that is not the shared envelope. */
export const UNREADABLE_FAILURE_MESSAGE =
  "The CAN-X Runtime answered with a failure this client cannot interpret.";

/**
 * Send one request, translating an unreachable Runtime into a transport failure.
 *
 * The only place a rejection from `fetch` becomes a diagnosis. The rejection's own text
 * is never forwarded: it can carry a URL, an address or a browser message about the
 * network, none of which tells a user anything they can act on and all of which is more
 * than they should be shown.
 */
export async function runtimeFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new RuntimeDbcTransportError(TRANSPORT_MESSAGE);
  }
}

/** The Runtime's error envelope, narrowed to the five fields it promises. */
export interface RuntimeErrorEnvelope {
  readonly code: string;
  readonly message: string;
  readonly details: Readonly<Record<string, unknown>>;
  readonly recoverable: boolean;
  readonly source: string;
}

/**
 * Validate a failure body against the shared envelope.
 *
 * `null` means "this is not the Runtime's own diagnosis" — the shape the application
 * boundary of `canx.api.errors` defines. Checked rather than believed for the same
 * reason a success payload is: a `{"detail": …}` from a framework, a gateway or a future
 * Runtime version must be reported as an uninterpretable failure, not decorated with a
 * code it never claimed.
 */
export function readErrorEnvelope(payload: unknown): RuntimeErrorEnvelope | null {
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
 * Read the response body as JSON, or report that it was not JSON.
 *
 * Returns `undefined` rather than throwing so that the *caller* can decide what a
 * non-JSON body means, which differs by status: in front of a failure response it is a
 * transport problem, behind a success response it is a contract problem.
 */
export async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/**
 * Read a response as JSON and translate the failure half of the exchange.
 *
 * Splitting success from failure here keeps the two intended meanings apart for every
 * endpoint rather than inlining them per call site: behind a non-2xx answer, a missing
 * envelope means the reply did not come *from* the Runtime's application boundary (a
 * proxy, a framework or a version that does not speak this contract), which is a
 * transport problem; behind a 2xx answer, a body that is not JSON is the caller's
 * contract problem, diagnosed when the payload is validated.
 */
export async function readResponse(response: Response): Promise<unknown> {
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
