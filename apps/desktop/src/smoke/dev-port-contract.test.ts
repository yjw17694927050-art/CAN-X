import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import viteConfig from "../../vite.config";

/**
 * The dev-port contract between the Tauri shell and the Vite dev server.
 *
 * `apps/desktop/src-tauri/tauri.conf.json` declares `devUrl`
 * (`http://localhost:1420`) and starts Vite through its `beforeDevCommand`.
 * The Tauri dev shell polls exactly that URL, so if Vite ever binds a different
 * port — or silently falls back to another one when 1420 is taken — `pnpm tauri
 * dev` never connects. That drift was the V0.3 P2 accepted non-blocking defect.
 *
 * This guard reads the *real* configs — the Vite config as the module Vite
 * itself loads, and the Tauri config as parsed JSON — rather than grepping
 * source text, so it tracks the effective values and cannot be satisfied by a
 * comment that merely mentions 1420.
 *
 * The check deliberately re-derives the Tauri URL's port with the WHATWG URL
 * parser instead of asserting a literal string: a devUrl that drops its port,
 * or moves to another one, fails here for the right reason.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const TAURI_CONFIG_PATH = resolve(HERE, "../../src-tauri/tauri.conf.json");

const EXPECTED_DEV_PORT = 1420;

interface TauriConfigShape {
  build?: { devUrl?: unknown };
}

function readTauriDevUrl(): string {
  const parsed = JSON.parse(readFileSync(TAURI_CONFIG_PATH, "utf8")) as TauriConfigShape;
  const devUrl = parsed.build?.devUrl;
  if (typeof devUrl !== "string" || devUrl.length === 0) {
    throw new Error(`${TAURI_CONFIG_PATH} no longer declares build.devUrl as a non-empty string`);
  }
  return devUrl;
}

function tauriDevPort(): number {
  const devUrl = readTauriDevUrl();
  const parsed = new URL(devUrl);
  if (parsed.port === "") {
    throw new Error(`the Tauri devUrl ${devUrl} declares no explicit port`);
  }
  return Number(parsed.port);
}

describe("the dev-port contract between Tauri and Vite", () => {
  it("waits for the Vite dev server on port 1420", () => {
    expect(tauriDevPort()).toBe(EXPECTED_DEV_PORT);
  });

  it("binds the Vite dev server to the exact port the Tauri shell waits on", () => {
    // Reading the module Vite loads means a change in the comment above the
    // setting cannot make this pass on its own.
    expect(viteConfig.server?.port).toBe(tauriDevPort());
  });

  it("fails closed when the port is already in use instead of moving to another", () => {
    // Without strictPort Vite would silently start on 1421 while Tauri keeps
    // polling 1420 — the shell would hang on a server it cannot reach.
    expect(viteConfig.server?.strictPort).toBe(true);
  });
});
