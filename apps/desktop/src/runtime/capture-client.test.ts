import { afterEach, describe, expect, it, vi } from "vitest";

import { startVirtualCapture, stopCapture } from "./capture-client";
import {
  RuntimeDbcApiError,
  RuntimeDbcTransportError,
  TRANSPORT_MESSAGE,
  UNREADABLE_FAILURE_MESSAGE,
} from "./runtime-http";

afterEach(() => vi.unstubAllGlobals());

/**
 * The Runtime's five-field error envelope, as `canx.api.errors` renders it.
 *
 * The capture endpoints answer failures with the same envelope as every other Runtime
 * endpoint (`runtime/canx/api/app.py` builds an `ErrorResponse` for both the 409 and the
 * configuration failure), so the fixtures below use the real shape rather than a
 * two-field stand-in: a client that only understood `code` and `message` would pass
 * against the stand-in and fail against the Runtime.
 */
function envelope(code: string, message: string, status: number): Response {
  return new Response(
    JSON.stringify({ code, details: {}, message, recoverable: false, source: "runtime" }),
    { headers: { "Content-Type": "application/json" }, status },
  );
}

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (cause) {
    return cause;
  }
  throw new Error("Expected the promise to reject.");
}

describe("capture control client", () => {
  it("starts the deterministic V0.1 source", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "started" }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    await startVirtualCapture();
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/capture/start",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("reports structured control-plane failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(envelope("capture.failed", "failed", 500)));
    await expect(startVirtualCapture()).rejects.toThrow("capture.failed: failed");
  });

  it("attaches to an already active capture without taking stop ownership", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        envelope("capture.already_running", "Capture is already running.", 409),
      ),
    );
    await expect(startVirtualCapture()).resolves.toBe(false);
  });

  it("stops capture explicitly", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await stopCapture();
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/capture/stop",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("raises the Runtime's own diagnosis through the shared boundary types", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(envelope("capture.invalid_configuration", "bad config", 400)),
    );

    const cause = await rejectionOf(startVirtualCapture());

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    const failure = cause as RuntimeDbcApiError;
    expect(failure.status).toBe(400);
    expect(failure.code).toBe("capture.invalid_configuration");
    expect(failure.message).toBe("capture.invalid_configuration: bad config");
    expect(failure.source).toBe("runtime");
  });

  it("reports an unreachable Runtime with the floor's static transport message", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));

    const cause = await rejectionOf(stopCapture());

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect((cause as Error).message).toBe(TRANSPORT_MESSAGE);
    // The layer's own text never travels: it can carry a URL or an internal address.
    expect((cause as Error).message).not.toContain("Failed to fetch");
  });

  it("treats a non-envelope failure as a transport failure, not an api one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Not Found" }), { status: 500 }),
      ),
    );

    const cause = await rejectionOf(startVirtualCapture());

    expect(cause).toBeInstanceOf(RuntimeDbcTransportError);
    expect(cause).not.toBeInstanceOf(RuntimeDbcApiError);
    expect((cause as Error).message).toBe(UNREADABLE_FAILURE_MESSAGE);
  });

  it("does not read a conflict as ownership unless the Runtime says already-running", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(envelope("capture.conflicting", "some other conflict", 409)),
    );

    const cause = await rejectionOf(startVirtualCapture());

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    expect((cause as RuntimeDbcApiError).code).toBe("capture.conflicting");
  });
});
