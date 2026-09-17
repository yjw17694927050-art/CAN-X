/**
 * The desktop DBC import orchestration — the seam between two boundaries.
 *
 * Two boundaries already exist and neither may absorb the other:
 *
 * ```text
 * src/desktop/dbc-file-bridge.ts   OS / Tauri IPC     user's explicit choice → content
 * src/runtime/dbc-client.ts        Python Runtime HTTP content → project-owned asset
 * ```
 *
 * This module is the third thing: the flow that runs one after the other. It holds
 * no state, owns no dialog, builds no request body and parses no response — it
 * decides *when* each boundary runs and *what* the caller gets back.
 *
 * ```text
 * importDbcFromNativeDialog(projectPath)
 *   ↓ selectDbcContent()                     ← the one OS interaction
 *   ↓ null?  → { status: "cancelled" }       ← and nothing else happens
 *   ↓ toRuntimeImportFields(selected)        ← two names, no values transformed
 *   ↓ importDbcAsset({ projectPath, … })     ← the one Runtime request
 *   ↓ { status: "imported", asset }
 * ```
 *
 * Three properties are deliberate:
 *
 * * **Cancel is control flow.** A dismissed dialog ends the function before any
 *   request is built. It never becomes an error, an empty import or a Runtime call:
 *   the user changed their mind, and the only honest report of that is `cancelled`.
 * * **The project is an argument, not a global.** There is no `currentProject`, no
 *   `activeProject`, no module-level path. The caller that knows which project an
 *   import belongs to passes it in — which is what lets a future Project Workspace
 *   supply its own path without this module having to learn about workspaces.
 * * **The two paths are kept apart.** `projectPath` is the CAN-X project the Runtime
 *   API requires and is meant to be sent to the localhost Runtime; the external DBC
 *   file the user picked is represented only by its basename and its bytes, and
 *   neither this module nor the request it builds has a place for its location.
 *
 * Failures are not caught. A typed {@link DbcFileBridgeError} from the bridge and a
 * typed {@link RuntimeDbcApiError} from the Runtime both mean different things to a
 * caller, and re-wrapping them here would destroy exactly the distinction that makes
 * them diagnosable.
 */

import { selectDbcContent, toRuntimeImportFields } from "../desktop/dbc-file-bridge";
import { importDbcAsset } from "../runtime/dbc-client";
import type { RuntimeDbcAsset } from "../runtime/dbc-client";

/**
 * What one native-dialog import attempt produced.
 *
 * A discriminated union rather than `asset | null`, so a caller must acknowledge the
 * cancelled case and cannot mistake "the user changed their mind" for a failure or
 * for an empty asset.
 */
export type DbcImportOutcome =
  | { readonly status: "cancelled" }
  | { readonly status: "imported"; readonly asset: RuntimeDbcAsset };

/**
 * Let the user choose a DBC file, then import it into a project through the Runtime.
 *
 * Args:
 *   projectPath: The CAN-X project that will own the imported asset. Required and
 *     explicit — this function has no notion of a "current" project and never
 *     guesses one.
 *
 * Returns:
 *   `{ status: "cancelled" }` when the user dismissed the dialog, or
 *   `{ status: "imported", asset }` with the Runtime's metadata for the asset the
 *   project now owns.
 *
 * Throws:
 *   Whatever the boundary that failed threw — the bridge's typed error, the
 *   Runtime's typed error, or a desktop transport/contract error from the Runtime
 *   client. Nothing is translated here, because the caller can act on the
 *   distinction and this module cannot.
 */
export async function importDbcFromNativeDialog(
  projectPath: string,
): Promise<DbcImportOutcome> {
  const selected = await selectDbcContent();
  if (selected === null) {
    return { status: "cancelled" };
  }

  const fields = toRuntimeImportFields(selected);
  const asset = await importDbcAsset({
    projectPath,
    sourceName: fields.source_name,
    contentBase64: fields.content_base64,
  });
  return { status: "imported", asset };
}
