/**
 * V0.3-07 native-dialog end-to-end smoke harness — **test-only, not a product feature**.
 *
 * This module exists for exactly one purpose: to let an operator or a verification
 * script drive the *real* renderer path of the desktop DBC bridge — `selectDbcContent()`
 * → Tauri IPC → Rust `select_dbc_file` → native Windows file dialog → bounded
 * exact-byte read — inside a **packaged** `can-x.exe`, and read back a safe summary
 * of what happened.
 *
 * It is deliberately not wired into the product:
 *
 * * it is only reachable when the desktop bundle is built with
 *   `VITE_CANX_DBC_SMOKE=1`, and the import in `main.tsx` is guarded by a
 *   statically-replaceable `import.meta.env` check so a normal build drops this
 *   module (and its chunk) entirely;
 * * it calls no Runtime HTTP endpoint, and in particular never `POST /dbc/assets`;
 * * it reports only what a smoke run needs — whether the dialog was cancelled, the
 *   selected basename, the decoded byte count and the SHA-256 of the decoded bytes.
 *   It never renders a filesystem path, a directory, the raw content or the full
 *   Base64 payload.
 *
 * The sequence is deliberately scripted rather than interactive: each step publishes
 * its state to the document title (`CANXSMOKE …`) before it opens a dialog, so an
 * external driver knows which dialog is on screen and can act on the real native
 * window — Cancel for the first step, a chosen fixture for the second and third.
 */

import { invoke } from "@tauri-apps/api/core";

import { SELECT_DBC_CONTENT_COMMAND, selectDbcContent } from "../desktop/dbc-file-bridge";

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
  publishState("waiting-for-select-dialog", results);
  results.push(await guarded("select", selectStep));
  publishState("select-done", results);

  await delay(STEP_DELAY_MS);
  publishState("waiting-for-payload-dialog", results);
  results.push(await guarded("payload_shape", payloadShapeStep));

  publishState("done", results);
}

/** Step 1 — the operator dismisses the native dialog; the bridge must answer `null`. */
async function cancelStep(): Promise<SmokeStepResult> {
  const selected = await selectDbcContent();
  return { step: "cancel", returnedNull: selected === null };
}

/**
 * Step 2 — the operator picks a real `.dbc` fixture.
 *
 * The SHA-256 is computed **here**, from the bytes that crossed IPC, so comparing it
 * with the fixture's own digest on disk proves the exact-byte guarantee at the far
 * end of the chain rather than merely asserting it.
 */
async function selectStep(): Promise<SmokeStepResult> {
  const selected = await selectDbcContent();
  if (selected === null) {
    return { step: "select", returnedNull: true };
  }
  const bytes = decodeBase64(selected.contentBase64);
  return {
    step: "select",
    returnedNull: false,
    sourceName: selected.sourceName,
    sourceNameHasSeparator: /[\\/]/.test(selected.sourceName),
    byteCount: bytes.length,
    sha256: await sha256Hex(bytes),
  };
}

/**
 * Step 3 — one raw `invoke`, bypassing this module's own projection, so the *shape*
 * of the payload the Rust layer actually publishes can be observed at runtime.
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

function decodeBase64(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function sha256Hex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  let hex = "";
  for (const byte of new Uint8Array(digest)) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
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
