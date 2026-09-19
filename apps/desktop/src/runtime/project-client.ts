/**
 * The Runtime project client — the renderer's typed view of the project HTTP
 * surface.
 *
 * This module belongs to the **Runtime HTTP boundary**, alongside `dbc-client.ts`
 * and `capture-client.ts`. It is not the desktop bridge and it is not a project
 * workspace: it talks HTTP to the Python Runtime and turns one answered question
 * into typed read models. Nothing here opens a directory, reads a manifest,
 * chooses a project or remembers one.
 *
 * One direction lives here, and only this one:
 *
 * ```text
 * READ (inspect)
 *   inspectProject(projectPath)                ↓ GET /project/inspect?project_path=…
 *     ↓ validated against the Runtime's contract
 *   ProjectReadModel { projectId, displayName, schemaVersion, createdAt, updatedAt }
 * ```
 *
 * Six properties are deliberate:
 *
 * * **A project is named, never remembered.** `projectPath` is an argument on
 *   every call. There is no module-level path, no store, no `setCurrentProject`
 *   and no cached handle: this client cannot describe a project the caller did
 *   not just name, which is what keeps the Runtime's statelessness stateless all
 *   the way to the renderer. It answers *"is this directory a CAN-X project, and
 *   what does it say it is?"* and nothing else.
 * * **The path is forwarded, not interpreted.** It is never normalised, resolved,
 *   lower-cased, expanded, trimmed, separator-rewritten or validated here. The
 *   Runtime's project domain is the only authority on which strings name a
 *   directory, and a second opinion in TypeScript would be a second definition of
 *   "valid project". `C:\Mixed\Case\..\Dir\` reaches the Runtime exactly as the
 *   caller wrote it.
 * * **The wire shape stops here.** `snake_case` Runtime JSON is mapped onto the
 *   camelCase Desktop model in this module and nowhere else, so a UI can consume a
 *   typed object without ever knowing the Runtime's field names.
 * * **Every response is validated, never asserted.** `response.json() as
 *   ProjectReadModel` would let a renamed key, a stringified number or a payload
 *   that answered a list where an object was declared travel into every caller as
 *   though it were part of the contract. Each of the five fields is checked and a
 *   fresh object is built from exactly those five — no `as`, no spread of the
 *   payload, no sixth field promoted because the Runtime happened to send it.
 * * **The answer carries no filesystem authority.** The read model has no path, no
 *   manifest name and no database name, because the Runtime's projection has none
 *   either. The caller already knows what it asked for; this layer invents no way
 *   to discover where the Runtime keeps things.
 * * **A failure carries the Runtime's diagnosis; this layer adds nothing to it.**
 *   Every message this layer authors is a static constant, and neither the transport
 *   nor the contract error has a field a path or a payload could enter. A structured
 *   Runtime API failure is the deliberate exception, and it is deliberate for a
 *   reason: its five-field envelope is preserved *intact*, so its `message` and
 *   `details` are the Runtime's own diagnostics — including a `path` the Runtime
 *   chose to report — and this client neither strips them down nor supplements them.
 *   What this layer never does is add the project path, the request URL or the raw
 *   response body on its own account.
 */

import {
  RUNTIME_URL,
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  readResponse,
  runtimeFetch,
} from "./runtime-http";

/**
 * The Runtime boundary's failure vocabulary, under this module's historic names.
 *
 * `RuntimeProject*` predates the shared floor in `./runtime-http`, and the project
 * surface is not the only consumer of those names: `components/project/ProjectControls`
 * and the project-open orchestration both classify failures by them. They are
 * re-exported rather than redeclared because a second, structurally identical class would
 * break `instanceof` at exactly the boundary this split exists to keep honest — a failure
 * raised by the floor has to be the failure the project UI recognises. The `Dbc` in the
 * canonical names is historic too: they are the Runtime boundary's types, and renaming
 * them would be renaming the boundary rather than adding one.
 */
export {
  RuntimeDbcApiError as RuntimeProjectApiError,
  RuntimeDbcContractError as RuntimeProjectContractError,
  RuntimeDbcTransportError as RuntimeProjectTransportError,
};

/** The Runtime project read-model endpoint. */
const PROJECT_INSPECT_PATH = "/project/inspect";

/**
 * One validated CAN-X project, as the Desktop consumes it.
 *
 * Identity and metadata only, in the Desktop's own camelCase shape. The Runtime's
 * projection is already path-free — this model stays path-free for the same reason
 * and is not allowed to grow a path back.
 *
 * `createdAt` / `updatedAt` are carried as the Runtime rendered them (ISO-8601
 * UTC) rather than parsed into a `Date`: parsing here would be a second
 * interpretation of an instant the Runtime already decided, and a locale- or
 * version-dependent one at that.
 */
export interface ProjectReadModel {
  /** The UUID the manifest and the database agree on. Never inferred from a path. */
  readonly projectId: string;
  /** The name the project database owns. Deliberately not derived from the directory. */
  readonly displayName: string;
  /** The canonical manifest schema version. */
  readonly schemaVersion: number;
  /** When the project was created, as the Runtime reported it. */
  readonly createdAt: string;
  /** When the project was last updated, as the Runtime reported it. */
  readonly updatedAt: string;
}

/** The one contract message for a 2xx answer whose project shape is wrong. */
const UNREADABLE_PROJECT_MESSAGE =
  "The CAN-X Runtime answered with a project payload that does not match the contract.";

/**
 * Inspect one CAN-X project and project its identity onto the Desktop read model.
 *
 * The question this asks is exactly the Runtime's: *is this directory a CAN-X
 * project, and what does it say it is?* Whether the answer is yes is not decided
 * here — a directory that is not a project comes back as the Runtime's own
 * `project.not_found` (or another `project.*` code), raised as
 * {@link RuntimeProjectApiError} with its status, code, message, details,
 * recoverability and source intact.
 *
 * Args:
 *   projectPath: The directory to inspect, exactly as the caller holds it. Required
 *     and explicit — this function has no notion of a "current" project and never
 *     guesses one. It is not normalised in any way: it is encoded as the
 *     `project_path` query parameter and forwarded byte for byte.
 *
 * Returns:
 *   The validated project, projected onto {@link ProjectReadModel}.
 *
 * Throws:
 *   {@link RuntimeProjectApiError} when the Runtime diagnosed the failure itself —
 *   for example `project.not_found` for a directory that is not a CAN-X project, or
 *   `api.request_validation_failed` for an empty path.
 *   {@link RuntimeProjectTransportError} when the Runtime could not be reached, or
 *   answered with a failure that is not the shared envelope.
 *   {@link RuntimeProjectContractError} when the Runtime answered successfully with
 *   a payload this contract does not describe.
 */
export async function inspectProject(projectPath: string): Promise<ProjectReadModel> {
  const payload = await readResponse(await runtimeFetch(inspectUrl(projectPath), { method: "GET" }));
  return readProject(payload);
}

/**
 * One project's inspect URL: `GET /project/inspect?project_path=…`.
 *
 * The origin comes from the shared floor (`./runtime-http`), and the path is never
 * assembled from caller input by concatenation: `URLSearchParams` applies the query-string
 * encoding rules, so the value is percent-encoded as a whole and cannot terminate the
 * query, start a second parameter or introduce a fragment. The Runtime decodes it back to
 * exactly the string the caller passed.
 */
function inspectUrl(projectPath: string): string {
  const query = new URLSearchParams({ project_path: projectPath }).toString();
  return `${RUNTIME_URL}${PROJECT_INSPECT_PATH}?${query}`;
}

/**
 * Validate one project payload and project it onto {@link ProjectReadModel}.
 *
 * The five declared fields and nothing else: the returned object is built here,
 * field by field, from values that were each checked. An unknown key the Runtime
 * sends is not promoted, and a declared field that is missing or of the wrong type
 * refuses the *whole* payload — there is no half-valid read model.
 */
function readProject(value: unknown): ProjectReadModel {
  const record = readObject(value, UNREADABLE_PROJECT_MESSAGE);
  return {
    projectId: readString(record["project_id"], UNREADABLE_PROJECT_MESSAGE),
    displayName: readString(record["display_name"], UNREADABLE_PROJECT_MESSAGE),
    schemaVersion: readInteger(record["schema_version"], UNREADABLE_PROJECT_MESSAGE),
    createdAt: readString(record["created_at"], UNREADABLE_PROJECT_MESSAGE),
    updatedAt: readString(record["updated_at"], UNREADABLE_PROJECT_MESSAGE),
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

/** Read one declared string field, or refuse the payload. */
function readString(value: unknown, message: string): string {
  if (typeof value !== "string") throw new RuntimeDbcContractError(message);
  return value;
}

/**
 * Read a safe integer, or refuse the payload.
 *
 * `1.5`, `NaN`, `2 ** 53` and `"1"` all refuse: a schema version is a whole number
 * the Runtime declared, and `"1"` is not `1`, so accepting it would be this client
 * inventing an interpretation the contract does not describe.
 */
function readInteger(value: unknown, message: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value;
}
