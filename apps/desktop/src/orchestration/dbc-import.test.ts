import { afterEach, describe, expect, it, vi } from "vitest";

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

// Only the outermost layers are replaced: the Tauri IPC entry point the desktop
// bridge calls and the global `fetch` the Runtime client calls. Everything between
// them — the bridge's projection, the field mapping, the request body, the response
// validation — is the real wiring under test.
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

import { DbcFileBridgeError, SELECT_DBC_CONTENT_COMMAND } from "../desktop/dbc-file-bridge";
import { RuntimeDbcApiError } from "../runtime/dbc-client";
import { importDbcFromNativeDialog } from "./dbc-import";

afterEach(() => {
  invokeMock.mockReset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const PROJECT_PATH = "C:\\canx\\projects\\demo";

/** Content with padding and non-ASCII bytes: decode/re-encode would change it. */
const CONTENT_BASE64 = "VkVSU0lPTiAiMS4wIg0K/yEAkAECAwQ=";

const SELECTED = {
  source_name: "Vehicle_Bus.DBC",
  content_base64: CONTENT_BASE64,
};

const RUNTIME_ASSET = {
  asset_id: "dbc-asset-0001",
  source_name: "Vehicle_Bus.DBC",
  sha256: "a".repeat(64),
  size_bytes: 21,
  encoding: "utf-8",
  imported_at: "2026-09-17T08:30:00+00:00",
};

interface FetchCall {
  readonly url: string;
  readonly init: RequestInit;
}

function stubFetch(response: Response): readonly FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(response);
    }),
  );
  return calls;
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function bodyOf(call: FetchCall): Record<string, unknown> {
  const body = call.init.body;
  if (typeof body !== "string") throw new Error("the request body was not a string");
  return JSON.parse(body) as Record<string, unknown>;
}

describe("importing DBC content from the native dialog", () => {
  it("reports a dismissed dialog as a cancelled outcome", async () => {
    invokeMock.mockResolvedValue(null);
    stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await expect(importDbcFromNativeDialog(PROJECT_PATH)).resolves.toEqual({
      status: "cancelled",
    });
  });

  it("makes no Runtime request when the dialog was dismissed", async () => {
    invokeMock.mockResolvedValue(null);
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(calls).toHaveLength(0);
  });

  it("opens the native dialog exactly once for one import", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0]).toEqual([SELECT_DBC_CONTENT_COMMAND]);
  });

  it("imports exactly once for one import", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("POST");
    expect(calls[0]?.url).toBe("http://127.0.0.1:8765/dbc/assets");
  });

  it("returns the imported asset unchanged", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    const outcome = await importDbcFromNativeDialog(PROJECT_PATH);

    expect(outcome).toEqual({
      status: "imported",
      asset: {
        assetId: "dbc-asset-0001",
        sourceName: "Vehicle_Bus.DBC",
        sha256: "a".repeat(64),
        sizeBytes: 21,
        encoding: "utf-8",
        importedAt: "2026-09-17T08:30:00+00:00",
      },
    });
  });

  it("forwards the caller's project path explicitly", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(bodyOf(calls[0] as FetchCall)["project_path"]).toBe(PROJECT_PATH);
  });

  it("forwards the selected name exactly, case and all", async () => {
    invokeMock.mockResolvedValue({ ...SELECTED, source_name: "Vehicle_Bus.DBC" });
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(bodyOf(calls[0] as FetchCall)["source_name"]).toBe("Vehicle_Bus.DBC");
  });

  it("forwards the selected bytes exactly", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(bodyOf(calls[0] as FetchCall)["content_base64"]).toBe(CONTENT_BASE64);
  });

  it("never decodes, re-encodes or normalises the selected content", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));
    // `atob` is the only way this renderer can turn the selected Base64 back into
    // bytes, so a spy on it observes "no decode happened" directly. `TextDecoder` is
    // deliberately not asserted: `Response.json()` decodes the response body
    // internally, so it fires no matter what this layer does.
    const atob = vi.spyOn(globalThis, "atob");

    await importDbcFromNativeDialog(PROJECT_PATH);

    expect(atob).not.toHaveBeenCalled();
    expect(bodyOf(calls[0] as FetchCall)["content_base64"]).toBe(CONTENT_BASE64);
    expect(bodyOf(calls[0] as FetchCall)["content_base64"]).not.toMatch(/[\s]/);
  });

  it("keeps a source filesystem path out of the Runtime request", async () => {
    // The bridge projects onto two named fields, so even a payload that carried a
    // location cannot become a request field.
    invokeMock.mockResolvedValue({
      ...SELECTED,
      path: "C:\\customer\\secret-program\\Vehicle_Bus.DBC",
      absolute_path: "C:\\customer\\secret-program\\Vehicle_Bus.DBC",
      directory: "C:\\customer\\secret-program",
    });
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcFromNativeDialog(PROJECT_PATH);

    const body = bodyOf(calls[0] as FetchCall);
    expect(Object.keys(body).sort()).toEqual(["content_base64", "project_path", "source_name"]);
    expect(JSON.stringify(body)).not.toContain("secret-program");
  });

  it("does not swallow a typed failure from the desktop bridge", async () => {
    invokeMock.mockRejectedValue({
      code: "desktop.dbc_file_too_large",
      message: "The selected DBC file is larger than the 16777216 byte import bound.",
      recoverable: true,
    });
    const calls = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    const cause: unknown = await importDbcFromNativeDialog(PROJECT_PATH).catch(
      (error: unknown) => error,
    );

    expect(cause).toBeInstanceOf(DbcFileBridgeError);
    expect((cause as DbcFileBridgeError).code).toBe("desktop.dbc_file_too_large");
    expect((cause as DbcFileBridgeError).recoverable).toBe(true);
    // A refused selection must not become an import attempt.
    expect(calls).toHaveLength(0);
  });

  it("does not swallow a typed failure from the Runtime", async () => {
    invokeMock.mockResolvedValue(SELECTED);
    stubFetch(
      jsonResponse(
        {
          code: "dbc.parse_failed",
          message: "The submitted content is not a DBC document.",
          details: {},
          recoverable: false,
          source: "dbc",
        },
        422,
      ),
    );

    const cause: unknown = await importDbcFromNativeDialog(PROJECT_PATH).catch(
      (error: unknown) => error,
    );

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    expect((cause as RuntimeDbcApiError).code).toBe("dbc.parse_failed");
    expect((cause as RuntimeDbcApiError).status).toBe(422);
  });
});
