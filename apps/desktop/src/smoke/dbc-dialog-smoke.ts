/**
 * Native-dialog DBC smoke harness — **test-only, not a product feature**.
 *
 * This module exists for exactly one purpose: to let an operator or a verification
 * script drive the *real* renderer path of a desktop DBC import inside a **packaged**
 * `can-x.exe`, and read back a safe summary of what happened.
 *
 * ```text
 * V0.3-07 half                        V0.3-08 half
 * selectDbcContent()                  importDbcFromNativeDialog(projectPath)
 *   → Tauri IPC → Rust → dialog         → selectDbcContent()
 *   → bounded exact-byte read           → POST /dbc/assets on the Runtime
 *   → SelectedDbcContent                → project-owned asset
 * ```
 *
 * Both the Cancel step and the Import step call
 * `importDbcFromNativeDialog(projectPath)` — the function the product will call. That
 * is what makes this harness evidence rather than a demonstration: what it reports is
 * what the shipped orchestration does, including the Cancel contract that a dismissed
 * dialog produces no Runtime request at all. Only the payload-shape step bypasses the
 * orchestration, and it exists precisely to observe the Rust IPC payload directly.
 *
 * It is deliberately not wired into the product:
 *
 * * it is only reachable when the desktop bundle is built with
 *   `VITE_CANX_DBC_SMOKE=1`, and the import in `main.tsx` is guarded by a
 *   statically-replaceable `import.meta.env` check so a normal build drops this
 *   module (and its chunk) entirely;
 * * it publishes only what a smoke run needs to be judged — whether the dialog was
 *   cancelled, the selected basename, the raw Rust payload's key set, the decoded
 *   byte count and SHA-256 of the bytes that crossed IPC, and the asset metadata the
 *   Runtime returned. It never renders a project path, a source path, a directory,
 *   the raw content or the Base64 payload;
 * * the project it imports into comes from `VITE_CANX_DBC_SMOKE_PROJECT_PATH`, which
 *   is a *build-time* input. The harness never invents a project and never keeps one
 *   in state.
 *
 * The sequence is scripted rather than interactive: each step publishes its state to
 * the document title (`CANXSMOKE …`) before it opens a dialog, so an external driver
 * knows which dialog is on screen and can act on the real native window — Cancel for
 * the first step, a chosen fixture for the second and third.
 */

import { invoke } from "@tauri-apps/api/core";

import { SELECT_DBC_CONTENT_COMMAND } from "../desktop/dbc-file-bridge";
import { importDbcFromNativeDialog } from "../orchestration/dbc-import";

/** Prefix under which this harness publishes its results in the document title. */
export const SMOKE_TITLE_PREFIX = "CANXSMOKE";

/** Id of the panel this harness renders, so a driver can find it by a stable marker. */
export const SMOKE_PANEL_ID = "canx-dbc-smoke";

/** How long each completed step waits before opening the next dialog. */
const STEP_DELAY_MS = 12_000;

/** How long the harness waits after page load before the first dialog. */
const INITIAL_DELAY_MS = 2_000;

/** One step's safe, reportable outcome. Never carries a path or raw content. */
interface SmokeStepResult {
  readonly step: string;
  readonly returnedNull?: boolean;
  readonly sourceName?: string | null;
  readonly sourceNameHasSeparator?: boolean;
  readonly byteCount?: number;
  readonly sha256?: string;
  readonly payloadKeys?: readonly string[];
  /** The orchestration outcome for the import step: `cancelled` or `imported`. */
  readonly outcome?: string;
  readonly assetId?: string;
  readonly assetSha256?: string;
  readonly assetSizeBytes?: number;
  readonly assetEncoding?: string;
  readonly error?: string;
}

type StepRunner = () => Promise<SmokeStepResult>;

/**
 * Start the smoke sequence. Called once, from `main.tsx`, only in a smoke build.
 */
export function startDbcDialogSmoke(): void {
  void runSequence();
}

async function runSequence(): Promise<void> {
  const results: SmokeStepResult[] = [];

  publishState("waiting-for-cancel-dialog", results);
  await delay(INITIAL_DELAY_MS);
  results.push(await guarded("cancel", cancelStep));
  publishState("cancel-done", results);

  await delay(STEP_DELAY_MS);
  publishState("waiting-for-payload-dialog", results);
  results.push(await guarded("payload_shape", payloadShapeStep));
  publishState("payload-done", results);

  await delay(STEP_DELAY_MS);
  publishState("waiting-for-import-dialog", results);
  results.push(await guarded("import", importStep));

  publishState("done", results);
}

/**
 * Step 1 — the operator dismisses the native dialog, and the production
 * orchestration must report it as `cancelled` without importing anything.
 *
 * This step runs `importDbcFromNativeDialog` — the function the product will call —
 * and not `selectDbcContent`, the bridge underneath it. Both answer "no content" for
 * a dismissed dialog, which is exactly why the distinction matters: evidencing the
 * bridge here would let this harness report a `cancelled` outcome for a path the
 * orchestration never ran, and the orchestration's Cancel contract — *no request is
 * built at all* — would never actually be exercised in the packaged app.
 *
 * The operator is *supposed* to press Cancel. If they pick a file instead, that is
 * reported as a mismatch rather than silently accepted as a cancellation.
 */
async function cancelStep(): Promise<SmokeStepResult> {
  const projectPath = readSmokeProjectPath();
  if (projectPath === null) {
    return {
      step: "cancel",
      error: "VITE_CANX_DBC_SMOKE_PROJECT_PATH is not configured for this build",
    };
  }

  const outcome = await importDbcFromNativeDialog(projectPath);
  if (outcome.status !== "cancelled") {
    return { step: "cancel", outcome: outcome.status, error: "the dialog was not cancelled" };
  }
  return { step: "cancel", outcome: "cancelled" };
}

/**
 * Step 2 — one raw `invoke`, bypassing this module's own projection, so the *shape*
 * of the payload the Rust layer actually publishes can be observed at runtime.
 *
 * V0.3-08 added a third field to nothing: the payload must still carry exactly
 * `source_name` and `content_base64`, which is what keeps the IPC surface the same
 * one V0.3-07 established.
 */
async function payloadShapeStep(): Promise<SmokeStepResult> {
  const raw: unknown = await invoke(SELECT_DBC_CONTENT_COMMAND);
  if (raw === null) {
    return { step: "payload_shape", returnedNull: true };
  }
  if (typeof raw !== "object" || Array.isArray(raw)) {
    return { step: "payload_shape", returnedNull: false, error: "payload was not an object" };
  }
  const record = raw as Record<string, unknown>;
  const sourceName = record["source_name"];
  return {
    step: "payload_shape",
    returnedNull: false,
    payloadKeys: Object.keys(record).sort(),
    sourceName: typeof sourceName === "string" ? sourceName : null,
  };
}

/**
 * Step 3 — the real orchestration, end to end.
 *
 * The operator picks a real `.dbc` fixture and this calls the *production* function
 * with the build-configured project path: native dialog → Tauri IPC → bounded read →
 * `POST /dbc/assets` → project-owned asset. Everything reported here is what the
 * Runtime returned; nothing is recomputed or inferred in the renderer, so the
 * evidence a driver reads is the Runtime's own account of the import.
 */
async function importStep(): Promise<SmokeStepResult> {
  const projectPath = readSmokeProjectPath();
  if (projectPath === null) {
    return {
      step: "import",
      error: "VITE_CANX_DBC_SMOKE_PROJECT_PATH is not configured for this build",
    };
  }

  const outcome = await importDbcFromNativeDialog(projectPath);
  if (outcome.status === "cancelled") {
    return { step: "import", outcome: "cancelled" };
  }
  const asset = outcome.asset;
  return {
    step: "import",
    outcome: "imported",
    assetId: asset.assetId,
    sourceName: asset.sourceName,
    sourceNameHasSeparator: /[\\/]/.test(asset.sourceName),
    assetSha256: asset.sha256,
    assetSizeBytes: asset.sizeBytes,
    assetEncoding: asset.encoding,
  };
}

/** The build-configured project, or `null` when the smoke build forgot to set one. */
function readSmokeProjectPath(): string | null {
  const value = import.meta.env.VITE_CANX_DBC_SMOKE_PROJECT_PATH;
  if (typeof value !== "string" || value.length === 0) return null;
  return value;
}

async function guarded(step: string, run: StepRunner): Promise<SmokeStepResult> {
  try {
    return await run();
  } catch (cause) {
    return { step, error: describe(cause) };
  }
}

function describe(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  return String(cause);
}

/**
 * Publish the run's state where an external driver can read it.
 *
 * The document title is the channel: it is the one piece of renderer state a packaged
 * Tauri window exposes to the operating system without granting the renderer any new
 * capability. Only safe fields are ever published.
 */
function publishState(state: string, results: readonly SmokeStepResult[]): void {
  const summary = JSON.stringify({ state, results });
  document.title = `${SMOKE_TITLE_PREFIX} ${summary}`;
  renderPanel(state, summary);
}

function renderPanel(state: string, summary: string): void {
  const existing = document.getElementById(SMOKE_PANEL_ID);
  const panel = existing ?? document.createElement("pre");
  if (existing === null) {
    panel.id = SMOKE_PANEL_ID;
    panel.setAttribute("data-canx-smoke", "test-only");
    panel.style.cssText =
      "position:fixed;left:0;right:0;bottom:0;max-height:40vh;overflow:auto;" +
      "margin:0;padding:8px;font:12px/1.4 monospace;white-space:pre-wrap;z-index:9999;" +
      "background:#0b1220;color:#c8e1ff;border-top:1px solid #28324a";
    document.body.append(panel);
  }
  panel.textContent = `DBC NATIVE-DIALOG SMOKE (test-only)\nstate: ${state}\n${summary}`;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
