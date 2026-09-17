import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  getDbcAsset,
  getDbcDatabase,
  importDbcAsset,
  listDbcAssets,
} from "./dbc-client";
import type {
  ImportDbcAssetInput,
  RuntimeDbcAsset,
  RuntimeDbcDatabase,
} from "./dbc-client";

afterEach(() => vi.unstubAllGlobals());

/**
 * Base64 chosen so that "exactly these characters" is a claim with teeth: it has
 * non-ASCII-derived bytes, real padding, and characters whose re-encoding would
 * differ if anything decoded and re-encoded it.
 */
const CONTENT_BASE64 = "VkVSU0lPTiAiMS4wIg0K/yEAkAECAwQ=";

const INPUT: ImportDbcAssetInput = {
  projectPath: "C:\\customer\\secret-program",
  sourceName: "Vehicle_Bus.DBC",
  contentBase64: CONTENT_BASE64,
};

const RUNTIME_ASSET = {
  asset_id: "dbc-asset-0001",
  source_name: "Vehicle_Bus.DBC",
  sha256: "a".repeat(64),
  size_bytes: 21,
  encoding: "utf-8",
  imported_at: "2026-09-17T08:30:00+00:00",
};

const IMPORTED_AT = "2026-09-17T08:30:00+00:00";

interface FetchCall {
  readonly url: string;
  readonly init: RequestInit;
}

interface StubbedFetch {
  readonly calls: FetchCall[];
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Record every fetch call so method, URL, headers and body stay observable. */
function stubFetch(response: Response): StubbedFetch {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(response);
    }),
  );
  return { calls };
}

function stubRejectingFetch(cause: unknown): StubbedFetch {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Promise.reject(cause);
    }),
  );
  return { calls };
}

function bodyOf(call: FetchCall): Record<string, unknown> {
  const body = call.init.body;
  if (typeof body !== "string") throw new Error("the request body was not a string");
  return JSON.parse(body) as Record<string, unknown>;
}

function headerOf(call: FetchCall, name: string): string | undefined {
  const headers = call.init.headers;
  if (headers === undefined || headers instanceof Headers || Array.isArray(headers)) {
    throw new Error("the request headers are not a plain record");
  }
  return (headers as Record<string, string>)[name];
}

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => {
      throw new Error("expected the import to be rejected");
    },
    (cause: unknown) => cause,
  );
}

describe("the Runtime DBC asset import", () => {
  it("resolves the imported asset as a typed value", async () => {
    stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await expect(importDbcAsset(INPUT)).resolves.toEqual({
      assetId: "dbc-asset-0001",
      sourceName: "Vehicle_Bus.DBC",
      sha256: "a".repeat(64),
      sizeBytes: 21,
      encoding: "utf-8",
      importedAt: IMPORTED_AT,
    });
  });

  it("projects an all-camelCase object and promotes no unknown Runtime field", async () => {
    stubFetch(
      jsonResponse(
        {
          ...RUNTIME_ASSET,
          stored_path: "C:\\customer\\secret-program\\.canx\\dbc\\dbc-asset-0001.dbc",
        },
        201,
      ),
    );

    const asset = await importDbcAsset(INPUT);

    expect(Object.keys(asset).sort()).toEqual([
      "assetId",
      "encoding",
      "importedAt",
      "sha256",
      "sizeBytes",
      "sourceName",
    ]);
    expect(JSON.stringify(asset)).not.toContain("secret-program");
  });

  it("posts JSON to the Runtime DBC asset collection", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcAsset(INPUT);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("POST");
    expect(calls[0]?.url).toBe("http://127.0.0.1:8765/dbc/assets");
    expect(headerOf(calls[0] as FetchCall, "Content-Type")).toBe("application/json");
  });

  it("maps the camelCase input onto the wire contract", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcAsset(INPUT);

    expect(bodyOf(calls[0] as FetchCall)).toEqual({
      project_path: "C:\\customer\\secret-program",
      source_name: "Vehicle_Bus.DBC",
      content_base64: CONTENT_BASE64,
    });
  });

  it("transfers the content bytes exactly, with no decode and no re-encode", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcAsset({ ...INPUT, contentBase64: CONTENT_BASE64 });

    const body = bodyOf(calls[0] as FetchCall);
    expect(body["content_base64"]).toBe(CONTENT_BASE64);
    expect(body["content_base64"]).not.toContain(" ");
    expect(body["content_base64"]).not.toContain("\n");
  });

  it("transfers the source name exactly, including case", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcAsset({ ...INPUT, sourceName: "Vehicle_Bus.DBC" });

    expect(bodyOf(calls[0] as FetchCall)["source_name"]).toBe("Vehicle_Bus.DBC");
  });
});

describe("the Runtime request surface", () => {
  it("sends exactly the three fields the contract declares", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcAsset(INPUT);

    expect(Object.keys(bodyOf(calls[0] as FetchCall)).sort()).toEqual([
      "content_base64",
      "project_path",
      "source_name",
    ]);
  });

  it("never sends a field that would name a filesystem location", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 201));

    await importDbcAsset(INPUT);

    const body = bodyOf(calls[0] as FetchCall);
    for (const forbidden of [
      "source_path",
      "path",
      "absolute_path",
      "directory",
      "file_path",
      "selected_path",
    ]) {
      expect(body).not.toHaveProperty(forbidden);
    }
    const sourceName = body["source_name"];
    expect(typeof sourceName).toBe("string");
    expect(String(sourceName)).not.toMatch(/[\\/]/);
  });
});

describe("Runtime failures", () => {
  it("preserves a structured 4xx diagnosis", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "dbc.parse_failed",
          message: "The submitted content is not a DBC document.",
          details: { source_name: "Vehicle_Bus.DBC" },
          recoverable: false,
          source: "dbc",
        },
        422,
      ),
    );

    const cause = await rejectionOf(importDbcAsset(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    const failure = cause as RuntimeDbcApiError;
    expect(failure.status).toBe(422);
    expect(failure.code).toBe("dbc.parse_failed");
    expect(failure.message).toBe(
      "dbc.parse_failed: The submitted content is not a DBC document.",
    );
    expect(failure.details).toEqual({ source_name: "Vehicle_Bus.DBC" });
    expect(failure.recoverable).toBe(false);
    expect(failure.source).toBe("dbc");
  });

  it("preserves a structured 5xx diagnosis", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "dbc.asset_storage_failed",
          message: "The project-owned DBC copy could not be written.",
          details: {},
          recoverable: true,
          source: "dbc",
        },
        503,
      ),
    );

    const cause = await rejectionOf(importDbcAsset(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    const failure = cause as RuntimeDbcApiError;
    expect(failure.status).toBe(503);
    expect(failure.code).toBe("dbc.asset_storage_failed");
    expect(failure.recoverable).toBe(true);
    expect(failure.source).toBe("dbc");
  });

  it("keeps a project failure distinguishable from a DBC failure", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "project.not_found",
          message: "The project could not be opened.",
          details: {},
          recoverable: false,
          source: "project",
        },
        400,
      ),
    );

    const cause = (await rejectionOf(importDbcAsset(INPUT))) as RuntimeDbcApiError;

    expect(cause.code).toBe("project.not_found");
    expect(cause.source).toBe("project");
    expect(cause.status).toBe(400);
  });

  it("keeps a request-validation failure distinguishable from a DBC failure", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "api.request_validation_failed",
          message: "The request payload does not match the API contract.",
          details: { errors: [{ location: ["body", "content_base64"], type: "value_error" }] },
          recoverable: false,
          source: "api",
        },
        422,
      ),
    );

    const cause = (await rejectionOf(importDbcAsset(INPUT))) as RuntimeDbcApiError;

    expect(cause.code).toBe("api.request_validation_failed");
    expect(cause.details["errors"]).toEqual([
      { location: ["body", "content_base64"], type: "value_error" },
    ]);
  });

  it("keeps the details the Runtime reported while echoing no part of the request", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "dbc.parse_failed",
          message: "The submitted content is not a DBC document.",
          details: { line: 12, source_name: "Vehicle_Bus.DBC" },
          recoverable: false,
          source: "dbc",
        },
        422,
      ),
    );

    const cause = (await rejectionOf(importDbcAsset(INPUT))) as RuntimeDbcApiError;

    const rendered = `${cause.message} ${JSON.stringify(cause.details)}`;
    expect(rendered).toContain("line");
    expect(rendered).not.toContain(CONTENT_BASE64);
    expect(rendered).not.toContain("C:\\customer");
  });
});

describe("transport and contract failures", () => {
  it("reports a non-JSON failure response as a transport failure", async () => {
    stubFetch(new Response("<html>502 Bad Gateway</html>", { status: 502 }));

    const cause = await rejectionOf(importDbcAsset(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect(cause).not.toBeInstanceOf(RuntimeDbcApiError);
    expect((cause as Error).message).not.toContain("502 Bad Gateway");
  });

  it("reports a failure whose JSON is not the shared envelope as a transport failure", async () => {
    stubFetch(jsonResponse({ detail: "gateway" }, 500));

    const cause = await rejectionOf(importDbcAsset(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect((cause as Error).message).not.toContain("gateway");
  });

  it("reports a network rejection as a transport failure", async () => {
    stubRejectingFetch(new TypeError("Failed to fetch"));

    const cause = await rejectionOf(importDbcAsset(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect((cause as Error).message).not.toContain("Failed to fetch");
  });

  it("reports a malformed 2xx body as a contract failure", async () => {
    stubFetch(new Response("not json at all", { status: 201 }));

    const cause = await rejectionOf(importDbcAsset(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcContractError);
    expect(cause).not.toBeInstanceOf(RuntimeDbcApiError);
  });

  it("rejects a 2xx body that is not an object", async () => {
    stubFetch(jsonResponse([RUNTIME_ASSET], 201));

    await expect(importDbcAsset(INPUT)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("rejects a 2xx body missing a declared field", async () => {
    const incomplete: Record<string, unknown> = { ...RUNTIME_ASSET };
    delete incomplete["size_bytes"];
    stubFetch(jsonResponse(incomplete, 201));

    await expect(importDbcAsset(INPUT)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("rejects a 2xx body whose field has the wrong type", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_ASSET, size_bytes: "21" }, 201));

    await expect(importDbcAsset(INPUT)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("rejects a 2xx body whose numeric field is not finite", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_ASSET, size_bytes: null }, 201));

    await expect(importDbcAsset(INPUT)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });
});

// ---------------------------------------------------------------------------
// V0.3-09 — the read surface: list / get / database
// ---------------------------------------------------------------------------

/** A second asset, so a listing is provably not "the one asset we happened to have". */
const RUNTIME_ASSET_TWO = {
  asset_id: "dbc-asset-0002",
  source_name: "Powertrain.dbc",
  sha256: "b".repeat(64),
  size_bytes: 4096,
  encoding: "utf-8-sig",
  imported_at: "2026-09-17T09:15:00+00:00",
};

/** The same asset projected onto the Desktop model, for an exact comparison. */
const EXPECTED_ASSET: RuntimeDbcAsset = {
  assetId: "dbc-asset-0001",
  sourceName: "Vehicle_Bus.DBC",
  sha256: "a".repeat(64),
  sizeBytes: 21,
  encoding: "utf-8",
  importedAt: IMPORTED_AT,
};

const EXPECTED_ASSET_TWO: RuntimeDbcAsset = {
  assetId: "dbc-asset-0002",
  sourceName: "Powertrain.dbc",
  sha256: "b".repeat(64),
  sizeBytes: 4096,
  encoding: "utf-8-sig",
  importedAt: "2026-09-17T09:15:00+00:00",
};

const PROJECT_PATH = "C:\\customer\\secret-program";

/**
 * Values chosen so that "the URL was built safely" has teeth: each character here
 * would change the URL's structure if it were concatenated rather than encoded.
 */
const HOSTILE_PROJECT_PATH = "C:\\a&b=c?d#e%f+g /sub";
const HOSTILE_ASSET_ID = "a/b?c=d#e%f&g h+i";

/**
 * A response whose `json()` hands the payload back unchanged.
 *
 * `Response` can only be built from a body, and `JSON.stringify` turns `NaN` and
 * `Infinity` into `null` — which would make "the Runtime sent a non-finite factor"
 * indistinguishable from "it sent null", and would let the `typeof` check stand in
 * for the finiteness check. These payloads are therefore handed over as objects,
 * carrying the exact values a JSON encoder could produce.
 */
function rawJsonResponse(body: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** One search parameter's values, read back through a real URL parse. */
function queryValues(url: string, name: string): readonly string[] {
  return new URL(url).searchParams.getAll(name);
}

/**
 * One wire signal with the full field set, so a malformed case differs from a valid
 * one in exactly the field under test.
 */
function wireSignal(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "Signal",
    start_bit: 0,
    length: 8,
    byte_order: "little_endian",
    is_signed: false,
    is_float: false,
    factor: 1,
    offset: 0,
    minimum: null,
    maximum: null,
    unit: null,
    receivers: ["Node"],
    choices: [],
    is_multiplexer: false,
    multiplexer_signal: null,
    multiplexer_ids: null,
    comment: null,
    ...overrides,
  };
}

/** One wire message with the full field set. */
function wireMessage(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    frame_id: 0x123,
    name: "Message",
    length: 8,
    is_extended: false,
    is_fd: false,
    senders: ["Node"],
    comment: null,
    cycle_time: null,
    signals: [wireSignal()],
    ...overrides,
  };
}

/** One wire database with the full field set. */
function wireDatabase(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: "1.0",
    messages: [wireMessage()],
    nodes: [{ name: "Node", comment: null }],
    ...overrides,
  };
}

/**
 * A complete `/database` payload exercising every declared feature.
 *
 * Standard and extended messages; CAN FD; Intel and Motorola byte order; signed and
 * unsigned; float metadata; factor/offset; minimum/maximum; unit; receivers;
 * choices; a multiplexer switch and a multiplexed signal with ids; comments; a
 * cycle time; nodes; and every nullable field exercised — both as a value and as
 * `null`.
 */
const RUNTIME_DATABASE = {
  version: "1.0",
  messages: [
    {
      frame_id: 0x123,
      name: "EngineData",
      length: 8,
      is_extended: false,
      is_fd: false,
      senders: ["ECU1"],
      comment: "engine status",
      cycle_time: 100,
      signals: [
        {
          name: "EngineSpeed",
          start_bit: 0,
          length: 16,
          byte_order: "little_endian",
          is_signed: false,
          is_float: false,
          factor: 0.25,
          offset: 0,
          minimum: 0,
          maximum: 8000,
          unit: "rpm",
          receivers: ["Gateway"],
          choices: [],
          is_multiplexer: false,
          multiplexer_signal: null,
          multiplexer_ids: null,
          comment: "crankshaft speed",
        },
        {
          name: "EngineTemp",
          start_bit: 7,
          length: 8,
          byte_order: "big_endian",
          is_signed: true,
          is_float: false,
          factor: 1,
          offset: -40,
          minimum: -40,
          maximum: 215,
          unit: "degC",
          receivers: ["Gateway", "Logger"],
          choices: [
            { value: -1, label: "NotAvailable" },
            { value: 0, label: "Idle" },
          ],
          is_multiplexer: false,
          multiplexer_signal: null,
          multiplexer_ids: null,
          comment: null,
        },
        {
          name: "MuxSwitch",
          start_bit: 23,
          length: 4,
          byte_order: "little_endian",
          is_signed: false,
          is_float: false,
          factor: 1,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: null,
          receivers: [],
          choices: [],
          is_multiplexer: true,
          multiplexer_signal: null,
          multiplexer_ids: null,
          comment: null,
        },
        {
          name: "MuxValueA",
          start_bit: 27,
          length: 12,
          byte_order: "little_endian",
          is_signed: false,
          is_float: true,
          factor: 0.5,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: "V",
          receivers: ["Gateway"],
          choices: [],
          is_multiplexer: false,
          multiplexer_signal: "MuxSwitch",
          multiplexer_ids: [0, 1],
          comment: "when the switch selects 0 or 1",
        },
      ],
    },
    {
      frame_id: 0x18ff50e5,
      name: "ExtendedFdFrame",
      length: 64,
      is_extended: true,
      is_fd: true,
      senders: ["Transmitter"],
      comment: null,
      cycle_time: null,
      signals: [
        {
          name: "WideCounter",
          start_bit: 0,
          length: 32,
          byte_order: "big_endian",
          is_signed: true,
          is_float: false,
          factor: 1,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: null,
          receivers: [],
          choices: [],
          is_multiplexer: false,
          multiplexer_signal: null,
          multiplexer_ids: null,
          comment: null,
        },
      ],
    },
  ],
  nodes: [
    { name: "ECU1", comment: "engine controller" },
    { name: "Gateway", comment: null },
  ],
};

/** The exact Desktop model the payload above must become — camelCase, no path. */
const DATABASE: RuntimeDbcDatabase = {
  version: "1.0",
  messages: [
    {
      frameId: 0x123,
      name: "EngineData",
      length: 8,
      isExtended: false,
      isFd: false,
      senders: ["ECU1"],
      comment: "engine status",
      cycleTime: 100,
      signals: [
        {
          name: "EngineSpeed",
          startBit: 0,
          length: 16,
          byteOrder: "little_endian",
          isSigned: false,
          isFloat: false,
          factor: 0.25,
          offset: 0,
          minimum: 0,
          maximum: 8000,
          unit: "rpm",
          receivers: ["Gateway"],
          choices: [],
          isMultiplexer: false,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: "crankshaft speed",
        },
        {
          name: "EngineTemp",
          startBit: 7,
          length: 8,
          byteOrder: "big_endian",
          isSigned: true,
          isFloat: false,
          factor: 1,
          offset: -40,
          minimum: -40,
          maximum: 215,
          unit: "degC",
          receivers: ["Gateway", "Logger"],
          choices: [
            { value: -1, label: "NotAvailable" },
            { value: 0, label: "Idle" },
          ],
          isMultiplexer: false,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: null,
        },
        {
          name: "MuxSwitch",
          startBit: 23,
          length: 4,
          byteOrder: "little_endian",
          isSigned: false,
          isFloat: false,
          factor: 1,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: null,
          receivers: [],
          choices: [],
          isMultiplexer: true,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: null,
        },
        {
          name: "MuxValueA",
          startBit: 27,
          length: 12,
          byteOrder: "little_endian",
          isSigned: false,
          isFloat: true,
          factor: 0.5,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: "V",
          receivers: ["Gateway"],
          choices: [],
          isMultiplexer: false,
          multiplexerSignal: "MuxSwitch",
          multiplexerIds: [0, 1],
          comment: "when the switch selects 0 or 1",
        },
      ],
    },
    {
      frameId: 0x18ff50e5,
      name: "ExtendedFdFrame",
      length: 64,
      isExtended: true,
      isFd: true,
      senders: ["Transmitter"],
      comment: null,
      cycleTime: null,
      signals: [
        {
          name: "WideCounter",
          startBit: 0,
          length: 32,
          byteOrder: "big_endian",
          isSigned: true,
          isFloat: false,
          factor: 1,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: null,
          receivers: [],
          choices: [],
          isMultiplexer: false,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: null,
        },
      ],
    },
  ],
  nodes: [
    { name: "ECU1", comment: "engine controller" },
    { name: "Gateway", comment: null },
  ],
};

describe("listing a project's DBC assets", () => {
  it("resolves the collection as typed assets", async () => {
    stubFetch(jsonResponse({ assets: [RUNTIME_ASSET, RUNTIME_ASSET_TWO] }, 200));

    await expect(listDbcAssets(PROJECT_PATH)).resolves.toEqual([
      EXPECTED_ASSET,
      EXPECTED_ASSET_TWO,
    ]);
  });

  it("maps every declared field of an asset, and invents none", async () => {
    stubFetch(jsonResponse({ assets: [RUNTIME_ASSET] }, 200));

    const assets = await listDbcAssets(PROJECT_PATH);

    expect(Object.keys(assets[0] ?? {}).sort()).toEqual([
      "assetId",
      "encoding",
      "importedAt",
      "sha256",
      "sizeBytes",
      "sourceName",
    ]);
  });

  it("reads the collection with GET and the project path as a query parameter", async () => {
    const { calls } = stubFetch(jsonResponse({ assets: [] }, 200));

    await listDbcAssets(PROJECT_PATH);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("GET");
    expect(calls[0]?.url).toBe(
      "http://127.0.0.1:8765/dbc/assets?project_path=C%3A%5Ccustomer%5Csecret-program",
    );
  });

  it("resolves an empty collection as an empty array, not as an error", async () => {
    stubFetch(jsonResponse({ assets: [] }, 200));

    await expect(listDbcAssets(PROJECT_PATH)).resolves.toEqual([]);
  });

  it("encodes a project path that would otherwise change the URL's structure", async () => {
    const { calls } = stubFetch(jsonResponse({ assets: [] }, 200));

    await listDbcAssets(HOSTILE_PROJECT_PATH);

    const url = calls[0]?.url ?? "";
    const parsed = new URL(url);
    // One parameter, and its decoded value is the caller's string character for
    // character: no `&` split it, no `=` re-keyed it, no `#` became a fragment.
    expect([...parsed.searchParams.keys()]).toEqual(["project_path"]);
    expect(queryValues(url, "project_path")).toEqual([HOSTILE_PROJECT_PATH]);
    expect(parsed.hash).toBe("");
  });

  it("drops an unannounced field instead of promoting it", async () => {
    stubFetch(
      jsonResponse(
        {
          assets: [
            {
              ...RUNTIME_ASSET,
              stored_path: "C:\\customer\\secret-program\\.canx\\dbc\\dbc-asset-0001.dbc",
            },
          ],
        },
        200,
      ),
    );

    const assets = await listDbcAssets(PROJECT_PATH);

    expect(assets).toEqual([EXPECTED_ASSET]);
    expect(JSON.stringify(assets)).not.toContain("secret-program");
  });
});

describe("reading one project-owned DBC asset", () => {
  it("reads one asset with GET, the id as a path segment and the project as a query", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 200));

    await expect(getDbcAsset(PROJECT_PATH, "dbc-asset-0001")).resolves.toEqual(EXPECTED_ASSET);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("GET");
    expect(calls[0]?.url).toBe(
      "http://127.0.0.1:8765/dbc/assets/dbc-asset-0001?project_path=C%3A%5Ccustomer%5Csecret-program",
    );
  });

  it("encodes an asset id that would otherwise change the URL's structure", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_ASSET, 200));

    await getDbcAsset(PROJECT_PATH, HOSTILE_ASSET_ID);

    const url = calls[0]?.url ?? "";
    const parsed = new URL(url);
    // The id stays one path segment: no `/` split it into two, no `?` started the
    // query early, no `#` started a fragment.
    expect(parsed.pathname).toBe(`/dbc/assets/${encodeURIComponent(HOSTILE_ASSET_ID)}`);
    expect(parsed.searchParams.getAll("project_path")).toEqual([PROJECT_PATH]);
    expect(parsed.hash).toBe("");
  });

  it("preserves a 404 asset-not-found diagnosis unchanged", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "dbc.asset_not_found",
          message: "The project does not own a DBC asset with that identifier.",
          details: { asset_id: "missing" },
          recoverable: false,
          source: "dbc",
        },
        404,
      ),
    );

    const cause = (await rejectionOf(getDbcAsset(PROJECT_PATH, "missing"))) as RuntimeDbcApiError;

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    expect(cause.status).toBe(404);
    expect(cause.code).toBe("dbc.asset_not_found");
    expect(cause.details).toEqual({ asset_id: "missing" });
    expect(cause.recoverable).toBe(false);
    expect(cause.source).toBe("dbc");
  });

  it("keeps a project failure distinguishable from an asset failure", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "project.not_found",
          message: "The project could not be opened.",
          details: {},
          recoverable: false,
          source: "project",
        },
        400,
      ),
    );

    const cause = (await rejectionOf(getDbcAsset(PROJECT_PATH, "dbc-asset-0001"))) as RuntimeDbcApiError;

    expect(cause.code).toBe("project.not_found");
    expect(cause.source).toBe("project");
    expect(cause.status).toBe(400);
  });
});

describe("reading one asset's canonical DBC database", () => {
  it("reads the database with GET and the project path encoded as a query", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_DATABASE, 200));

    await getDbcDatabase(PROJECT_PATH, "dbc-asset-0001");

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("GET");
    expect(calls[0]?.url).toBe(
      "http://127.0.0.1:8765/dbc/assets/dbc-asset-0001/database?project_path=C%3A%5Ccustomer%5Csecret-program",
    );
  });

  it("maps every declared field of the canonical database, and invents none", async () => {
    stubFetch(jsonResponse(RUNTIME_DATABASE, 200));

    const database = await getDbcDatabase(PROJECT_PATH, "dbc-asset-0001");

    expect(database).toEqual(DATABASE);
    // Field-level spot checks on the parts a whole-object comparison would hide:
    expect(database.messages[0]?.frameId).toBe(0x123);
    expect(database.messages[1]?.isExtended).toBe(true);
    expect(database.messages[1]?.isFd).toBe(true);
    expect(database.messages[0]?.signals[0]?.byteOrder).toBe("little_endian");
    expect(database.messages[0]?.signals[1]?.byteOrder).toBe("big_endian");
    expect(database.messages[0]?.signals[1]?.isSigned).toBe(true);
    expect(database.messages[0]?.signals[3]?.isFloat).toBe(true);
    expect(database.messages[0]?.signals[3]?.multiplexerIds).toEqual([0, 1]);
    expect(database.messages[0]?.signals[2]?.isMultiplexer).toBe(true);
    expect(database.nodes).toEqual([
      { name: "ECU1", comment: "engine controller" },
      { name: "Gateway", comment: null },
    ]);
  });

  it("carries an absent version as null rather than dropping it", async () => {
    stubFetch(jsonResponse(wireDatabase({ version: null }), 200));

    const database = await getDbcDatabase(PROJECT_PATH, "dbc-asset-0001");

    expect(database.version).toBeNull();
    expect(Object.keys(database).sort()).toEqual(["messages", "nodes", "version"]);
  });

  it("never lets a path reach the database read model", async () => {
    stubFetch(
      jsonResponse(
        {
          ...RUNTIME_DATABASE,
          stored_path: "C:\\customer\\secret-program\\.canx\\dbc\\dbc-asset-0001.dbc",
          source_path: "C:\\customer\\secret-program\\Vehicle_Bus.DBC",
        },
        200,
      ),
    );

    const database = await getDbcDatabase(PROJECT_PATH, "dbc-asset-0001");

    expect(Object.keys(database).sort()).toEqual(["messages", "nodes", "version"]);
    expect(JSON.stringify(database)).not.toContain("secret-program");
  });

  it("preserves an asset-integrity diagnosis unchanged", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "dbc.asset_integrity_failed",
          message: "The stored DBC copy does not match its recorded digest.",
          details: {},
          recoverable: false,
          source: "dbc",
        },
        409,
      ),
    );

    const cause = (await rejectionOf(
      getDbcDatabase(PROJECT_PATH, "dbc-asset-0001"),
    )) as RuntimeDbcApiError;

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    expect(cause.status).toBe(409);
    expect(cause.code).toBe("dbc.asset_integrity_failed");
    expect(cause.source).toBe("dbc");
  });
});

describe("the read surface's transport and contract boundaries", () => {
  it("reports a network rejection as a transport failure", async () => {
    stubRejectingFetch(new TypeError("Failed to fetch"));

    const cause = await rejectionOf(listDbcAssets(PROJECT_PATH));

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect((cause as Error).message).not.toContain("Failed to fetch");
  });

  it("reports a failure whose JSON is not the shared envelope as a transport failure", async () => {
    stubFetch(jsonResponse({ detail: "gateway" }, 502));

    const cause = await rejectionOf(getDbcDatabase(PROJECT_PATH, "dbc-asset-0001"));

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect(cause).not.toBeInstanceOf(RuntimeDbcApiError);
    expect((cause as Error).message).not.toContain("gateway");
  });

  it("reports a non-JSON 2xx body as a contract failure", async () => {
    stubFetch(new Response("<html>ok</html>", { status: 200 }));

    await expect(listDbcAssets(PROJECT_PATH)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("reports a collection body that is not an object as a contract failure", async () => {
    stubFetch(jsonResponse([RUNTIME_ASSET], 200));

    await expect(listDbcAssets(PROJECT_PATH)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("reports a collection whose assets field is not an array as a contract failure", async () => {
    stubFetch(jsonResponse({ assets: {} }, 200));

    await expect(listDbcAssets(PROJECT_PATH)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("reports a collection containing a malformed asset as a contract failure", async () => {
    stubFetch(
      jsonResponse({ assets: [RUNTIME_ASSET, { ...RUNTIME_ASSET_TWO, size_bytes: "4096" }] }, 200),
    );

    await expect(listDbcAssets(PROJECT_PATH)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });
});

describe("malformed database payloads", () => {
  /** The only assertion every malformed case shares: refuse, do not half-accept. */
  async function expectDatabaseRefusal(body: unknown): Promise<void> {
    stubFetch(rawJsonResponse(body, 200));
    await expect(getDbcDatabase(PROJECT_PATH, "dbc-asset-0001")).rejects.toBeInstanceOf(
      RuntimeDbcContractError,
    );
  }

  it("refuses a payload that is not an object", async () => {
    await expectDatabaseRefusal([RUNTIME_DATABASE]);
  });

  it("refuses a payload whose messages is not an array", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: {} }));
  });

  it("refuses a payload whose nodes is not an array", async () => {
    await expectDatabaseRefusal(wireDatabase({ nodes: "ECU1" }));
  });

  it("refuses a version that is neither a string nor null", async () => {
    await expectDatabaseRefusal(wireDatabase({ version: 7 }));
  });

  it("refuses a message that is an array where an object was declared", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ signals: [wireSignal()] }), []] }));
  });

  it("refuses a message whose frame_id is a string", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ frame_id: "0x123" })] }));
  });

  it("refuses a message whose frame_id is negative", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ frame_id: -1 })] }));
  });

  it("refuses a message whose length is not a safe integer", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ length: 2 ** 53 })] }));
  });

  it("refuses a message whose is_extended is a stringified boolean", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ is_extended: "true" })] }));
  });

  it("refuses a message whose senders contains a non-string", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ senders: ["ECU1", 42] })] }));
  });

  it("refuses a message whose comment is not a string", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ comment: 5 })] }));
  });

  it("refuses a message whose cycle_time is not an integer", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ cycle_time: 10.5 })] }));
  });

  it("refuses a message whose signals is not an array", async () => {
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ signals: {} })] }));
  });

  it("refuses a signal whose name is missing", async () => {
    const signal = wireSignal();
    delete signal["name"];
    await expectDatabaseRefusal(wireDatabase({ messages: [wireMessage({ signals: [signal] })] }));
  });

  it("refuses a signal whose factor is NaN", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ factor: Number.NaN })] })] }),
    );
  });

  it("refuses a signal whose factor is Infinity", async () => {
    await expectDatabaseRefusal(
      wireDatabase({
        messages: [wireMessage({ signals: [wireSignal({ factor: Number.POSITIVE_INFINITY })] })],
      }),
    );
  });

  it("refuses a signal whose offset is a string", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ offset: "0" })] })] }),
    );
  });

  it("refuses a signal whose nullable minimum holds a string", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ minimum: "0" })] })] }),
    );
  });

  it("refuses a signal whose start_bit is negative", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ start_bit: -1 })] })] }),
    );
  });

  it("refuses a signal whose receivers contains a non-string", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ receivers: [null] })] })] }),
    );
  });

  it("refuses a signal whose choices is not an array", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ choices: {} })] })] }),
    );
  });

  it("refuses a choice whose value is a string", async () => {
    await expectDatabaseRefusal(
      wireDatabase({
        messages: [
          wireMessage({ signals: [wireSignal({ choices: [{ value: "1", label: "One" }] })] }),
        ],
      }),
    );
  });

  it("refuses a choice whose label is missing", async () => {
    await expectDatabaseRefusal(
      wireDatabase({
        messages: [wireMessage({ signals: [wireSignal({ choices: [{ value: 1 }] })] })],
      }),
    );
  });

  it("refuses a multiplexer_ids array containing a non-integer", async () => {
    await expectDatabaseRefusal(
      wireDatabase({
        messages: [
          wireMessage({
            signals: [
              wireSignal({ multiplexer_signal: "MuxSwitch", multiplexer_ids: [0, 1.5] }),
            ],
          }),
        ],
      }),
    );
  });

  it("refuses a multiplexer_ids array containing a string", async () => {
    await expectDatabaseRefusal(
      wireDatabase({
        messages: [
          wireMessage({
            signals: [wireSignal({ multiplexer_signal: "MuxSwitch", multiplexer_ids: ["0"] })],
          }),
        ],
      }),
    );
  });

  it("refuses a multiplexer_ids array containing a negative id", async () => {
    await expectDatabaseRefusal(
      wireDatabase({
        messages: [
          wireMessage({
            signals: [wireSignal({ multiplexer_signal: "MuxSwitch", multiplexer_ids: [-1] })],
          }),
        ],
      }),
    );
  });

  it("refuses a signal whose boolean flag is a number", async () => {
    await expectDatabaseRefusal(
      wireDatabase({ messages: [wireMessage({ signals: [wireSignal({ is_multiplexer: 1 })] })] }),
    );
  });

  it("refuses a nodes array containing a non-object element", async () => {
    await expectDatabaseRefusal(wireDatabase({ nodes: ["ECU1"] }));
  });

  it("refuses a node whose name is missing", async () => {
    await expectDatabaseRefusal(wireDatabase({ nodes: [{ comment: null }] }));
  });

  it("refuses a node whose comment is not a string", async () => {
    await expectDatabaseRefusal(wireDatabase({ nodes: [{ name: "ECU1", comment: 5 }] }));
  });

  it("leaves no half-valid object behind: one bad nested signal refuses the whole payload", async () => {
    stubFetch(
      rawJsonResponse(
        wireDatabase({
          messages: [
            wireMessage(),
            wireMessage({ name: "Second", signals: [wireSignal({ factor: Number.NaN })] }),
          ],
        }),
        200,
      ),
    );

    await expect(getDbcDatabase(PROJECT_PATH, "dbc-asset-0001")).rejects.toBeInstanceOf(
      RuntimeDbcContractError,
    );
  });
});
