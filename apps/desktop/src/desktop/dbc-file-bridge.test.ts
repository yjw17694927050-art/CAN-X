import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

import {
  DBC_BRIDGE_UNAVAILABLE_CODE,
  DbcFileBridgeError,
  SELECT_DBC_CONTENT_COMMAND,
  selectDbcContent,
  toRuntimeImportFields,
} from "./dbc-file-bridge";

afterEach(() => {
  invokeMock.mockReset();
});

const CHOSEN = { source_name: "vehicle.dbc", content_base64: "VkVSU0lPTiAiMS4wIg0K" };

describe("the DBC file bridge", () => {
  it("resolves the chosen content as a typed value", async () => {
    invokeMock.mockResolvedValue(CHOSEN);

    await expect(selectDbcContent()).resolves.toEqual({
      sourceName: "vehicle.dbc",
      contentBase64: "VkVSU0lPTiAiMS4wIg0K",
    });
  });

  it("invokes the command with no argument a caller could turn into a path", async () => {
    invokeMock.mockResolvedValue(CHOSEN);

    await selectDbcContent();

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]).toEqual([SELECT_DBC_CONTENT_COMMAND]);
    expect(invokeMock.mock.calls[0]).toHaveLength(1);
  });

  it("resolves null when the user dismisses the dialog", async () => {
    invokeMock.mockResolvedValue(null);

    await expect(selectDbcContent()).resolves.toBeNull();
  });

  it("rejects with the typed bridge error the shell reported", async () => {
    invokeMock.mockRejectedValue({
      code: "desktop.dbc_file_empty",
      message: "The selected DBC file is empty.",
      recoverable: true,
    });

    const cause: unknown = await selectDbcContent().catch((error: unknown) => error);

    expect(cause).toBeInstanceOf(DbcFileBridgeError);
    const failure = cause as DbcFileBridgeError;
    expect(failure.code).toBe("desktop.dbc_file_empty");
    expect(failure.recoverable).toBe(true);
    expect(failure.message).toBe("desktop.dbc_file_empty: The selected DBC file is empty.");
  });

  it("carries a refusal's non-recoverability through unchanged", async () => {
    invokeMock.mockRejectedValue({
      code: "desktop.dbc_file_too_large",
      message: "The selected DBC file is larger than the 16777216 byte import bound.",
      recoverable: true,
    });

    const cause = (await selectDbcContent().catch((error: unknown) => error)) as DbcFileBridgeError;

    expect(cause.code).toBe("desktop.dbc_file_too_large");
    expect(cause.recoverable).toBe(true);
  });

  it("diagnoses an unavailable bridge rather than inventing a DBC failure", async () => {
    invokeMock.mockRejectedValue("window.__TAURI_INTERNALS__ is not defined");

    const cause = (await selectDbcContent().catch((error: unknown) => error)) as DbcFileBridgeError;

    expect(cause).toBeInstanceOf(DbcFileBridgeError);
    expect(cause.code).toBe(DBC_BRIDGE_UNAVAILABLE_CODE);
    expect(cause.recoverable).toBe(false);
    expect(cause.message).not.toContain("__TAURI_INTERNALS__");
  });
});

describe("payload validation", () => {
  it("rejects a payload missing a required field", async () => {
    invokeMock.mockResolvedValue({ source_name: "vehicle.dbc" });

    await expect(selectDbcContent()).rejects.toThrow(/content_base64/);
  });

  it("rejects a payload whose field has the wrong type", async () => {
    invokeMock.mockResolvedValue({ source_name: 42, content_base64: "VkVSU0lPTg==" });

    await expect(selectDbcContent()).rejects.toThrow(/source_name/);
  });

  it("rejects a payload that is not an object", async () => {
    invokeMock.mockResolvedValue("vehicle.dbc");

    await expect(selectDbcContent()).rejects.toThrow(/must be an object/);
  });

  it("rejects an array payload", async () => {
    invokeMock.mockResolvedValue([CHOSEN]);

    await expect(selectDbcContent()).rejects.toThrow(/must be an object/);
  });

  it("does not promote a path field out of an unexpected response", async () => {
    invokeMock.mockResolvedValue({
      ...CHOSEN,
      path: "C:\\customer\\secret-program\\vehicle.dbc",
      absolute_path: "C:\\customer\\secret-program\\vehicle.dbc",
    });

    const content = await selectDbcContent();

    // Exactly the two fields this module promises, projected onto a fresh object:
    // a location the shell should never have sent cannot become a property that
    // every consumer then reads as supported.
    expect(content).not.toBeNull();
    const keys = Object.keys(content as object).sort();
    expect(keys).toEqual(["contentBase64", "sourceName"]);
    expect(keys).not.toContain("path");
    expect(JSON.stringify(content)).not.toContain("secret-program");
  });
});

describe("the Runtime import contract", () => {
  it("maps the selection onto the fields POST /dbc/assets accepts", () => {
    const fields = toRuntimeImportFields({
      sourceName: "vehicle.dbc",
      contentBase64: "VkVSU0lPTiAiMS4wIg0K",
    });

    expect(Object.keys(fields).sort()).toEqual(["content_base64", "source_name"]);
    expect(fields.source_name).toBe("vehicle.dbc");
    expect(fields.content_base64).toBe("VkVSU0lPTiAiMS4wIg0K");
  });

  it("transfers the content unchanged", () => {
    const content = { sourceName: "BODY.DBC", contentBase64: "AAECAwQ=" };

    expect(toRuntimeImportFields(content).content_base64).toBe(content.contentBase64);
  });
});

describe("the Rust half of the contract", () => {
  const rustSource = (relative: string): string =>
    readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), relative), "utf8");

  it("registers the command this module invokes", () => {
    const libSource = rustSource("../../src-tauri/src/lib.rs");

    expect(libSource).toContain(`dbc_file_bridge::${SELECT_DBC_CONTENT_COMMAND}`);
  });

  it("exports the same command name from the Rust bridge", () => {
    const bridgeSource = rustSource("../../src-tauri/src/dbc_file_bridge.rs");

    expect(bridgeSource).toContain(
      `pub const SELECT_DBC_FILE_COMMAND: &str = "${SELECT_DBC_CONTENT_COMMAND}"`,
    );
  });

  it("keeps the desktop bridge out of the Runtime client", () => {
    const bridgeSource = rustSource("../../src-tauri/src/dbc_file_bridge.rs");

    expect(bridgeSource).toContain("MAX_DBC_IMPORT_BYTES");
    expect(bridgeSource).toContain("desktop.dbc_file_too_large");
  });
});
