import { describe, expect, it } from "vitest";

import { CAPTURE_FAILURE_CODE, createCaptureLifecycle } from "./capture-lifecycle";

/**
 * The capture lifecycle, driven directly.
 *
 * Everything here is about *when* a stop happens relative to a start that has not
 * resolved yet, so `start` and `stop` are deferred by hand rather than resolved on the
 * microtask queue: the ordering the lifecycle has to survive is exactly the ordering a
 * test cannot get by accident. Each deferred is resolved by the test at the moment it
 * wants the lifecycle to move.
 */

interface Deferred<T> {
  readonly promise: Promise<T>;
  resolve(value: T): void;
  reject(cause: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, reject, resolve };
}

function harness() {
  const starts: Deferred<boolean>[] = [];
  const stops: Deferred<void>[] = [];
  const lifecycle = createCaptureLifecycle({
    start: () => {
      const pending = deferred<boolean>();
      starts.push(pending);
      return pending.promise;
    },
    stop: () => {
      const pending = deferred<void>();
      stops.push(pending);
      return pending.promise;
    },
  });
  return { lifecycle, starts, stops };
}

/** Let every already-scheduled continuation run, so an intermediate phase is observable. */
function flush(): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, 0));
}

describe("CaptureLifecycle — one capture, one owner", () => {
  it("starts once on acquire and stops the capture it owns on release", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    expect(lifecycle.getState().phase).toBe("starting");
    expect(starts).toHaveLength(1);

    starts[0]?.resolve(true);
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("owned");
    expect(lifecycle.getState().wanted).toBe(true);

    lifecycle.release();
    expect(stops).toHaveLength(1);
    stops[0]?.resolve();
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("idle");
    expect(lifecycle.getState().wanted).toBe(false);
  });

  it("stops a capture whose start resolved only after the workspace was gone", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    // Unmount before the POST has answered. Nothing has claimed ownership yet, so the
    // only correct place to stop it is the continuation that is about to learn it was
    // owned — the cleanup itself has nothing to stop and must not guess.
    lifecycle.release();
    expect(stops).toHaveLength(0);
    expect(lifecycle.getState().phase).toBe("starting");

    starts[0]?.resolve(true);
    await flush();
    expect(stops).toHaveLength(1);

    stops[0]?.resolve();
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("idle");
  });

  it("survives a StrictMode replay without a second start or a lost capture", async () => {
    const { lifecycle, starts, stops } = harness();

    // StrictMode in development runs setup, cleanup, setup. The cleanup arrives before
    // the start has answered, so it must not schedule a stop, and the second setup must
    // not schedule a second start against an endpoint that would answer
    // `capture.already_running`.
    lifecycle.acquire();
    lifecycle.release();
    lifecycle.acquire();

    expect(starts).toHaveLength(1);

    starts[0]?.resolve(true);
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("owned");
    expect(stops).toHaveLength(0);

    // The real unmount still stops exactly the capture that was started here.
    lifecycle.release();
    expect(stops).toHaveLength(1);
    stops[0]?.resolve();
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("idle");
  });

  it("treats a repeated acquire and a repeated release as one intent", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    lifecycle.acquire();
    expect(starts).toHaveLength(1);

    starts[0]?.resolve(true);
    await lifecycle.settled();

    lifecycle.release();
    lifecycle.release();
    expect(stops).toHaveLength(1);

    stops[0]?.resolve();
    await lifecycle.settled();

    expect(starts).toHaveLength(1);
    expect(stops).toHaveLength(1);
  });

  it("never stops a capture it did not start", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    // `false` is the Runtime saying a capture was already running — attaching is not
    // owning, and a workspace that stops someone else's capture is worse than useless.
    starts[0]?.resolve(false);
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("observed");

    lifecycle.release();
    await lifecycle.settled();

    expect(stops).toHaveLength(0);
    expect(lifecycle.getState().phase).toBe("idle");
  });
});

describe("CaptureLifecycle — failures are states, not silence", () => {
  it("records a failed start and stops nothing", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    starts[0]?.reject(new Error("the Runtime is not listening"));
    await lifecycle.settled();

    expect(lifecycle.getState().phase).toBe("failed");
    expect(lifecycle.getState().failureCode).toBe(CAPTURE_FAILURE_CODE);

    lifecycle.release();
    await lifecycle.settled();

    expect(stops).toHaveLength(0);
  });

  it("observes a failed stop instead of dropping the rejection", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    starts[0]?.resolve(true);
    await lifecycle.settled();

    lifecycle.release();
    stops[0]?.reject(new Error("the Runtime is not listening"));

    // The returned promise must settle rather than reject: an unobserved rejection here
    // is exactly the dropped `void stopCapture()` this lifecycle replaced.
    await expect(lifecycle.settled()).resolves.toBeUndefined();

    expect(lifecycle.getState().phase).toBe("failed");
    expect(lifecycle.getState().failureCode).toBe(CAPTURE_FAILURE_CODE);
  });

  it("keeps the Runtime's own code for a failed stop", async () => {
    const { lifecycle, starts, stops } = harness();

    lifecycle.acquire();
    starts[0]?.resolve(true);
    await lifecycle.settled();

    lifecycle.release();
    stops[0]?.reject(
      Object.assign(new Error("capture.not_running: no capture is running"), {
        code: "capture.not_running",
      }),
    );
    await lifecycle.settled();

    expect(lifecycle.getState().failureCode).toBe("capture.not_running");
    expect(lifecycle.getState().phase).toBe("failed");
  });

  it("does not retry a failed start into an unbounded loop", async () => {
    const { lifecycle, starts } = harness();

    lifecycle.acquire();
    starts[0]?.reject(new Error("nope"));
    await lifecycle.settled();

    lifecycle.acquire();
    lifecycle.release();
    await lifecycle.settled();

    expect(starts).toHaveLength(1);
    expect(lifecycle.getState().phase).toBe("failed");
  });
});

describe("CaptureLifecycle — observers", () => {
  it("notifies subscribers on every phase change and stops on unsubscribe", async () => {
    const { lifecycle, starts, stops } = harness();
    const phases: string[] = [];
    const unsubscribe = lifecycle.subscribe(() => phases.push(lifecycle.getState().phase));

    lifecycle.acquire();
    starts[0]?.resolve(true);
    await lifecycle.settled();
    unsubscribe();
    lifecycle.release();
    stops[0]?.resolve();
    await lifecycle.settled();

    expect(phases).toStrictEqual(["starting", "owned"]);
  });
});
