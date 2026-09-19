import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

import {
  PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE,
  ProjectDirectoryBridgeError,
  SELECT_PROJECT_DIRECTORY_COMMAND,
  selectProjectDirectory,
} from "./project-directory-bridge";

afterEach(() => {
  invokeMock.mockReset();
});

const CHOSEN_DIRECTORY = "C:\\canx\\projects\\Vehicle_Bus";

describe("the project directory bridge", () => {
  it("names the Tauri command select_project_directory", () => {
    expect(SELECT_PROJECT_DIRECTORY_COMMAND).toBe("select_project_directory");
  });

  it("resolves the chosen directory as a string", async () => {
    invokeMock.mockResolvedValue(CHOSEN_DIRECTORY);

    await expect(selectProjectDirectory()).resolves.toBe(CHOSEN_DIRECTORY);
  });

  it("forwards the chosen directory character for character", async () => {
    // Deliberately hostile: odd separators, mixed case, padding and a trailing
    // separator. Normalising any of it here would silently change which directory
    // the caller later inspects.
    const odd = "  C:\\canx\\Projects\\Mixed Case\\nested\\  ";

    invokeMock.mockResolvedValue(odd);

    await expect(selectProjectDirectory()).resolves.toBe(odd);
  });

  it("invokes the command with no argument a caller could turn into a path", async () => {
    invokeMock.mockResolvedValue(CHOSEN_DIRECTORY);

    await selectProjectDirectory();

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]).toEqual([SELECT_PROJECT_DIRECTORY_COMMAND]);
    expect(invokeMock.mock.calls[0]).toHaveLength(1);
  });

  it("resolves null when the user dismisses the dialog", async () => {
    invokeMock.mockResolvedValue(null);

    await expect(selectProjectDirectory()).resolves.toBeNull();
  });

  it("rejects with the typed bridge error the shell reported", async () => {
    invokeMock.mockRejectedValue({
      code: "desktop.project_directory_unreadable",
      message: "The selected project directory could not be read.",
      recoverable: true,
    });

    const cause: unknown = await selectProjectDirectory().catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(ProjectDirectoryBridgeError);
    const failure = cause as ProjectDirectoryBridgeError;
    expect(failure.code).toBe("desktop.project_directory_unreadable");
    expect(failure.recoverable).toBe(true);
    expect(failure.message).toBe(
      "desktop.project_directory_unreadable: The selected project directory could not be read.",
    );
  });

  it("carries a non-recoverable refusal through unchanged", async () => {
    invokeMock.mockRejectedValue({
      code: "desktop.project_directory_not_a_directory",
      message: "The selection is not a directory.",
      recoverable: false,
    });

    const cause = (await selectProjectDirectory().catch(
      (error: unknown) => error,
    )) as ProjectDirectoryBridgeError;

    expect(cause.code).toBe("desktop.project_directory_not_a_directory");
    expect(cause.recoverable).toBe(false);
  });

  it("diagnoses an unavailable bridge rather than inventing a directory failure", async () => {
    invokeMock.mockRejectedValue("window.__TAURI_INTERNALS__ is not defined");

    const cause = (await selectProjectDirectory().catch(
      (error: unknown) => error,
    )) as ProjectDirectoryBridgeError;

    expect(cause).toBeInstanceOf(ProjectDirectoryBridgeError);
    expect(cause.code).toBe(PROJECT_DIRECTORY_BRIDGE_UNAVAILABLE_CODE);
    expect(cause.recoverable).toBe(false);
    expect(cause.message).not.toContain("__TAURI_INTERNALS__");
  });

  it("reports a malformed successful payload as a contract error, not a bridge error", async () => {
    invokeMock.mockResolvedValue(42);

    const cause: unknown = await selectProjectDirectory().catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(Error);
    expect(cause).not.toBeInstanceOf(ProjectDirectoryBridgeError);
    expect((cause as Error).message).toMatch(/string/);
  });

  it("rejects a payload that describes a location instead of a directory string", async () => {
    invokeMock.mockResolvedValue({ directory: CHOSEN_DIRECTORY });

    const cause: unknown = await selectProjectDirectory().catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(Error);
    expect(cause).not.toBeInstanceOf(ProjectDirectoryBridgeError);
  });
});

describe("the boundary this bridge refuses to cross", () => {
  /** The whole file, comments included. */
  const source = (): string =>
    readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), "project-directory-bridge.ts"),
      "utf8",
    );

  /**
   * The executable text, with comments removed.
   *
   * A comment is allowed to *say* `readFile` while explaining that this module does
   * not call it; only the code is asserted against, so the check cannot be satisfied
   * or defeated by prose.
   */
  const code = (): string =>
    source()
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");

  it("does not read the project definition file", () => {
    expect(code()).not.toContain("project.json");
  });

  it("does not reach the filesystem itself", () => {
    const text = code();

    expect(text).not.toMatch(/from\s+"node:fs/);
    expect(text).not.toMatch(/from\s+"fs"/);
    expect(text).not.toMatch(/\breadFile(Sync)?\b/);
    expect(text).not.toMatch(/\bexistsSync\b/);
  });

  it("keeps no mutable module-level state", () => {
    expect(code()).not.toMatch(/^(let|var)\s/m);
    expect(code()).not.toMatch(/\bcurrentProject\b/);
    expect(code()).not.toMatch(/\bactiveProject\b/);
  });
});

describe("the Rust half of the contract", () => {
  const rustSource = (relative: string): string =>
    readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), relative), "utf8");

  it("registers the command this module invokes", () => {
    const libSource = rustSource("../../src-tauri/src/lib.rs");

    expect(libSource).toContain(`project_directory_bridge::${SELECT_PROJECT_DIRECTORY_COMMAND}`);
  });

  it("exports the same command name from the Rust bridge", () => {
    const bridgeSource = rustSource("../../src-tauri/src/project_directory_bridge.rs");

    // The name is spelled once on each side and asserted against the other, so the
    // two halves of the IPC bridge cannot drift apart silently.
    expect(bridgeSource).toContain(
      `pub const SELECT_PROJECT_DIRECTORY_COMMAND: &str = "${SELECT_PROJECT_DIRECTORY_COMMAND}"`,
    );
  });
});
