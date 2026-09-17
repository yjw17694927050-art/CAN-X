import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Architecture checks on the packaged smoke harness.
 *
 * The harness is not a product module, but its *truthfulness* is what the packaged
 * evidence rests on: whatever path it exercises is the path the report may claim to
 * have exercised. A harness that quietly measures the bridge's Cancel instead of the
 * orchestration's Cancel produces evidence for a claim nobody made.
 *
 * Reading the source is the right instrument here. The defect this guards against is
 * structural — *which function a step calls* — and a runtime test could not see it,
 * because either call answers `null` when the dialog is dismissed. The distinction
 * only exists in the code, so the code is what is checked.
 */

// Normalised to LF: the repository checks the file out with CRLF on Windows, and a
// brace-search that only understands one line ending would report "no closing brace"
// instead of the real answer.
const HARNESS_SOURCE = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "dbc-dialog-smoke.ts"),
  "utf8",
).replace(/\r\n/g, "\n");

/**
 * The body of one top-level function in the harness.
 *
 * Throws when the function is gone rather than returning an empty string: a check
 * against a fragment of nothing passes vacuously, and a vacuous pass on this file is
 * exactly the failure mode being guarded against.
 */
function functionBody(name: string): string {
  const start = HARNESS_SOURCE.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`the smoke harness no longer declares ${name}()`);
  const end = HARNESS_SOURCE.indexOf("\n}\n", start);
  if (end < 0) throw new Error(`the smoke harness declares ${name}() without a closing brace`);
  return HARNESS_SOURCE.slice(start, end + 2);
}

describe("the packaged DBC smoke harness", () => {
  it("runs the Cancel step through the production orchestrator", () => {
    const body = functionBody("cancelStep");

    expect(body).toContain("importDbcFromNativeDialog(");
    // The bare bridge answers `null` for a dismissed dialog too, so calling it here
    // would produce evidence for the bridge while the report claims the
    // orchestration's Cancel path was exercised.
    expect(body).not.toContain("selectDbcContent(");
  });

  it("reports the orchestrator's own cancelled outcome for the Cancel step", () => {
    const body = functionBody("cancelStep");

    expect(body).toContain('"cancelled"');
  });

  it("runs the Import step through the production orchestrator", () => {
    const body = functionBody("importStep");

    expect(body).toContain("importDbcFromNativeDialog(");
    expect(body).toContain('"imported"');
  });

  it("keeps the raw IPC payload-shape step, observing the Rust payload directly", () => {
    const body = functionBody("payloadShapeStep");

    expect(body).toContain("invoke(SELECT_DBC_CONTENT_COMMAND)");
    expect(body).toContain("payloadKeys");
  });

  it("never publishes the project path or raw content in a step result", () => {
    // Everything the document title carries is a `SmokeStepResult`, so the interface
    // is the whole publication surface: a field that is not declared here cannot
    // reach the panel or the title.
    const start = HARNESS_SOURCE.indexOf("interface SmokeStepResult");
    expect(start).toBeGreaterThan(-1);
    const end = HARNESS_SOURCE.indexOf("\n}", start);
    const shape = HARNESS_SOURCE.slice(start, end);

    expect(shape).not.toMatch(/projectPath|contentBase64|directory|absolute_path|sourcePath/);
  });
});
