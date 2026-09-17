import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  importDbcAsset,
} from "./dbc-client";
import type { ImportDbcAssetInput } from "./dbc-client";

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
