import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RUNTIME_URL,
  RuntimeDbcApiError,
  RuntimeDbcTransportError,
  TRANSPORT_MESSAGE,
  readErrorEnvelope,
  readJson,
  readResponse,
  runtimeFetch,
} from "./runtime-http";

afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The Runtime's five-field envelope, as `canx.api.errors` renders it. */
const ENVELOPE = {
  code: "dbc.asset_not_found",
  message: "no such asset",
  details: { asset_id: "dbc-asset-0001" },
  recoverable: false,
  source: "canx.api.dbc",
};

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (cause) {
    return cause;
  }
  throw new Error("Expected the promise to reject.");
}

describe("RUNTIME_URL", () => {
  it("is the loopback origin every Desktop client shares", () => {
    expect(RUNTIME_URL).toBe("http://127.0.0.1:8765");
  });
});

describe("readResponse", () => {
  it("returns the payload of a 2xx answer unchanged", async () => {
    const payload = { schema_version: 1, outcomes: [] };
    await expect(readResponse(jsonResponse(payload, 200))).resolves.toEqual(payload);
  });

  it("returns undefined for a 2xx answer that is not JSON", async () => {
    await expect(readResponse(new Response("<html>", { status: 200 }))).resolves.toBeUndefined();
  });

  it("raises the Runtime's own diagnosis when a failure carries the envelope", async () => {
    const cause = await rejectionOf(readResponse(jsonResponse(ENVELOPE, 404)));
    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    const failure = cause as RuntimeDbcApiError;
    expect(failure.status).toBe(404);
    expect(failure.code).toBe("dbc.asset_not_found");
    expect(failure.recoverable).toBe(false);
    expect(failure.source).toBe("canx.api.dbc");
    expect(failure.details).toEqual({ asset_id: "dbc-asset-0001" });
    // The message is the Runtime's own `code: message`, never a paraphrase.
    expect(failure.message).toBe("dbc.asset_not_found: no such asset");
  });

  it("treats a failure that is not the envelope as a transport failure", async () => {
    const cause = await rejectionOf(readResponse(jsonResponse({ detail: "Not Found" }, 500)));
    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect(cause).not.toBeInstanceOf(RuntimeDbcApiError);
  });

  it("treats a failure that is not JSON as a transport failure", async () => {
    await expect(readResponse(new Response("<html>", { status: 502 }))).rejects.toBeInstanceOf(
      RuntimeDbcTransportError,
    );
  });

  it("keeps a 2xx body that is not JSON a caller problem, not a transport one", async () => {
    // readResponse hands the `undefined` back; classifying it is the endpoint's job.
    const payload = await readResponse(new Response("not json", { status: 200 }));
    expect(payload).toBeUndefined();
  });
});

describe("readErrorEnvelope", () => {
  it("accepts the five-field envelope and copies its details", () => {
    const details = { asset_id: "dbc-asset-0001" };
    const envelope = readErrorEnvelope({ ...ENVELOPE, details });
    expect(envelope).not.toBeNull();
    expect(envelope?.code).toBe("dbc.asset_not_found");
    expect(envelope?.details).toEqual(details);
    // Copied, not aliased: the caller must not hold a live view of the body.
    expect(envelope?.details).not.toBe(details);
  });

  it("refuses a body that is not the envelope at all", () => {
    expect(readErrorEnvelope({ detail: "Not Found" })).toBeNull();
    expect(readErrorEnvelope(null)).toBeNull();
    expect(readErrorEnvelope([ENVELOPE])).toBeNull();
    expect(readErrorEnvelope("boom")).toBeNull();
    expect(readErrorEnvelope(undefined)).toBeNull();
  });

  it("refuses an envelope whose fields are the wrong type", () => {
    expect(readErrorEnvelope({ ...ENVELOPE, code: 404 })).toBeNull();
    expect(readErrorEnvelope({ ...ENVELOPE, message: null })).toBeNull();
    expect(readErrorEnvelope({ ...ENVELOPE, recoverable: "false" })).toBeNull();
    expect(readErrorEnvelope({ ...ENVELOPE, source: 7 })).toBeNull();
    expect(readErrorEnvelope({ ...ENVELOPE, details: null })).toBeNull();
    expect(readErrorEnvelope({ ...ENVELOPE, details: [1, 2] })).toBeNull();
  });
});

describe("readJson", () => {
  it("returns undefined rather than throwing on a non-JSON body", async () => {
    await expect(readJson(new Response("not json", { status: 200 }))).resolves.toBeUndefined();
  });

  it("parses a JSON body", async () => {
    await expect(readJson(jsonResponse({ a: 1 }, 200))).resolves.toEqual({ a: 1 });
  });
});

describe("runtimeFetch", () => {
  it("returns the response when the Runtime answers", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("ok", { status: 200 }))));
    const response = await runtimeFetch(`${RUNTIME_URL}/dbc/assets`, { method: "GET" });
    expect(response.ok).toBe(true);
    expect(response.status).toBe(200);
  });

  it("forwards the url and init untouched", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit) => {
        calls.push({ url, init });
        return Promise.resolve(new Response("ok", { status: 200 }));
      }),
    );
    await runtimeFetch("http://127.0.0.1:8765/dbc/assets/x/decode-batch", {
      body: "{}",
      method: "POST",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("http://127.0.0.1:8765/dbc/assets/x/decode-batch");
    expect(calls[0]?.init.method).toBe("POST");
  });

  it("turns an unreachable Runtime into a transport failure with a static message", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    const cause = await rejectionOf(runtimeFetch(`${RUNTIME_URL}/dbc/assets`, { method: "GET" }));
    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect((cause as Error).message).toBe(TRANSPORT_MESSAGE);
    // The layer's own text never travels: it can carry a URL or an internal address.
    expect((cause as Error).message).not.toContain("Failed to fetch");
  });
});
