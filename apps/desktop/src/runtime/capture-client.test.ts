import { afterEach, describe, expect, it, vi } from "vitest";

import { startVirtualCapture, stopCapture } from "./capture-client";

afterEach(() => vi.unstubAllGlobals());

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
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ code: "capture.failed", message: "failed" }), { status: 500 }),
      ),
    );
    await expect(startVirtualCapture()).rejects.toThrow("capture.failed: failed");
  });

  it("attaches to an already active capture without taking stop ownership", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ code: "capture.already_running" }), { status: 409 }),
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
});
