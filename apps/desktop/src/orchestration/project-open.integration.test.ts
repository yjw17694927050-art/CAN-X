import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The cross-boundary integration test for V0.3-12.
 *
 * The unit suites prove each boundary on its own. This file proves they are wired
 * to each other, which is a different claim and the one a mock-only suite cannot
 * make: it uses the **real** desktop bridge, the **real** project-open
 * orchestration and the **real** Runtime project client, and stubs only the two
 * outermost adapters — the Tauri IPC call and `fetch`.
 *
 * ```text
 * user's choice                     invoke("select_project_directory")   ← Rust focused tests own this edge
 *   ↓ selectProjectDirectory()      src/desktop/project-directory-bridge.ts      REAL
 *   ↓ openProjectFromNativeDialog() src/orchestration/project-open.ts            REAL
 *   ↓ inspectProject(path)          src/runtime/project-client.ts                REAL
 *   ↓ GET /project/inspect?project_path=…                                        stubbed fetch, asserted URL
 * ```
 *
 * The native dialog is deliberately *not* automated here (§20): the OS selection
 * edge is proved by the Rust module's own focused tests, and what this file must
 * prove is that the exact string Rust would publish travels unchanged through the
 * renderer chain into the Runtime request.
 */

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

import {
  PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE,
  ProjectDirectoryBridgeError,
  SELECT_PROJECT_DIRECTORY_COMMAND,
  selectProjectDirectory,
} from "../desktop/project-directory-bridge";
import {
  RuntimeProjectApiError,
  RuntimeProjectTransportError,
  inspectProject,
} from "../runtime/project-client";
import type { ProjectReadModel } from "../runtime/project-client";
import { openProjectFromNativeDialog } from "./project-open";

/**
 * A hostile-but-legal project directory: mixed case, a `..` segment, spaces, and a
 * `?`, `&`, `#` and `/` that would each change the request's structure if the path
 * were concatenated into a URL instead of encoded.
 */
const HOSTILE_PROJECT_PATH = "C:\\canx\\Projects\\Mixed Case & more?\\#tag\\..\\Vehicle_Bus";

const RUNTIME_PROJECT_PAYLOAD = {
  project_id: "6f1c0f6e-0f1a-4c67-9d3f-2f0b1a7c5e10",
  display_name: "Vehicle Bus",
  schema_version: 3,
  created_at: "2026-09-19T10:00:00+00:00",
  updated_at: "2026-09-19T11:30:00+00:00",
};

/** A response good enough for this client: it reads `ok`, `status` and `json()`. */
function jsonResponse(payload: unknown, status = 200): unknown {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  invokeMock.mockReset();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the project-open chain, end to end", () => {
  it("returns cancelled and never asks the Runtime when the user dismisses the picker", async () => {
    invokeMock.mockResolvedValue(null);

    const outcome = await openProjectFromNativeDialog(selectProjectDirectory, inspectProject);

    expect(outcome).toEqual({ status: "cancelled" });
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("carries the selected directory into GET /project/inspect and returns the read model", async () => {
    invokeMock.mockResolvedValue(HOSTILE_PROJECT_PATH);
    fetchMock.mockResolvedValue(jsonResponse(RUNTIME_PROJECT_PAYLOAD));

    const outcome = await openProjectFromNativeDialog(selectProjectDirectory, inspectProject);

    expect(outcome.status).toBe("opened");
    if (outcome.status !== "opened") throw new Error("expected an opened outcome");

    expect(outcome.projectPath).toBe(HOSTILE_PROJECT_PATH);
    expect(outcome.project).toEqual({
      projectId: RUNTIME_PROJECT_PAYLOAD.project_id,
      displayName: RUNTIME_PROJECT_PAYLOAD.display_name,
      schemaVersion: RUNTIME_PROJECT_PAYLOAD.schema_version,
      createdAt: RUNTIME_PROJECT_PAYLOAD.created_at,
      updatedAt: RUNTIME_PROJECT_PAYLOAD.updated_at,
    } satisfies ProjectReadModel);
  });

  it("forwards the selected path character for character, as one encoded query parameter", async () => {
    invokeMock.mockResolvedValue(HOSTILE_PROJECT_PATH);
    fetchMock.mockResolvedValue(jsonResponse(RUNTIME_PROJECT_PAYLOAD));

    await openProjectFromNativeDialog(selectProjectDirectory, inspectProject);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const requested = String(fetchMock.mock.calls[0]?.[0]);
    const url = new URL(requested);

    expect(url.origin).toBe("http://127.0.0.1:8765");
    expect(url.pathname).toBe("/project/inspect");
    // The whole path is one value: nothing in it became structure.
    expect(url.searchParams.get("project_path")).toBe(HOSTILE_PROJECT_PATH);
    expect(url.searchParams.getAll("project_path")).toHaveLength(1);
    expect(url.hash).toBe("");
  });

  it("asks the Runtime exactly once for exactly one selection", async () => {
    invokeMock.mockResolvedValue(HOSTILE_PROJECT_PATH);
    fetchMock.mockResolvedValue(jsonResponse(RUNTIME_PROJECT_PAYLOAD));

    await openProjectFromNativeDialog(selectProjectDirectory, inspectProject);

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("propagates a selection failure unchanged and never reaches the Runtime", async () => {
    invokeMock.mockRejectedValue({
      code: "desktop.project_directory_unavailable",
      message: "The selected location could not be resolved to a filesystem path.",
      recoverable: true,
    });

    const cause: unknown = await openProjectFromNativeDialog(
      selectProjectDirectory,
      inspectProject,
    ).catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(ProjectDirectoryBridgeError);
    expect((cause as ProjectDirectoryBridgeError).code).toBe(
      "desktop.project_directory_unavailable",
    );
    expect((cause as ProjectDirectoryBridgeError).recoverable).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("propagates a structured Runtime failure unchanged, keeping the shared envelope", async () => {
    invokeMock.mockResolvedValue(HOSTILE_PROJECT_PATH);
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          code: "project.not_found",
          message: "No CAN-X project was found at the given path.",
          details: { reason: "manifest_missing" },
          recoverable: false,
          source: "project",
        },
        404,
      ),
    );

    const cause: unknown = await openProjectFromNativeDialog(
      selectProjectDirectory,
      inspectProject,
    ).catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(RuntimeProjectApiError);
    const failure = cause as RuntimeProjectApiError;
    expect(failure.status).toBe(404);
    expect(failure.code).toBe("project.not_found");
    expect(failure.recoverable).toBe(false);
    expect(failure.source).toBe("project");
    expect(failure.details).toEqual({ reason: "manifest_missing" });
  });

  it("reports an unreachable Runtime as a transport failure, not a project verdict", async () => {
    invokeMock.mockResolvedValue(HOSTILE_PROJECT_PATH);
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const cause: unknown = await openProjectFromNativeDialog(
      selectProjectDirectory,
      inspectProject,
    ).catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(RuntimeProjectTransportError);
  });

  it("does not echo the selected path in any diagnosis this chain produces", async () => {
    invokeMock.mockRejectedValue("window.__TAURI_INTERNALS__ is not defined");

    const cause = (await openProjectFromNativeDialog(
      selectProjectDirectory,
      inspectProject,
    ).catch((error: unknown) => error)) as ProjectDirectoryBridgeError;

    expect(cause.code).toBe(PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE);
    expect(cause.message).not.toContain(HOSTILE_PROJECT_PATH);
    expect(cause.message).not.toContain("__TAURI_INTERNALS__");
  });
});

describe("the Rust and TypeScript halves name the same command", () => {
  // src/orchestration → src → desktop → apps → repository root.
  const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
  const read = (relative: string): string => readFileSync(resolve(repositoryRoot, relative), "utf8");

  it("registers the exact command the TypeScript bridge invokes", () => {
    const lib = read("apps/desktop/src-tauri/src/lib.rs");
    const rustBridge = read("apps/desktop/src-tauri/src/project_directory_bridge.rs");

    expect(lib).toContain(`project_directory_bridge::${SELECT_PROJECT_DIRECTORY_COMMAND}`);
    expect(rustBridge).toContain(
      `pub const SELECT_PROJECT_DIRECTORY_COMMAND: &str = "${SELECT_PROJECT_DIRECTORY_COMMAND}"`,
    );
    expect(SELECT_PROJECT_DIRECTORY_COMMAND).toBe("select_project_directory");
  });
});
