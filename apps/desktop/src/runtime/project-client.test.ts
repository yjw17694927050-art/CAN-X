import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RuntimeProjectApiError,
  RuntimeProjectContractError,
  RuntimeProjectTransportError,
  inspectProject,
} from "./project-client";
import type { ProjectReadModel } from "./project-client";

afterEach(() => vi.unstubAllGlobals());

const INSPECT_URL = "http://127.0.0.1:8765/project/inspect";

const PROJECT_PATH = "C:\\customer\\secret-program";

const RUNTIME_PROJECT = {
  project_id: "4d2b6f1a-8c3e-4a5b-9d70-1e2f3a4b5c6d",
  display_name: "Secret Program",
  schema_version: 1,
  created_at: "2026-09-15T10:00:00+00:00",
  updated_at: "2026-09-17T08:30:00+00:00",
};

const READ_MODEL: ProjectReadModel = {
  projectId: "4d2b6f1a-8c3e-4a5b-9d70-1e2f3a4b5c6d",
  displayName: "Secret Program",
  schemaVersion: 1,
  createdAt: "2026-09-15T10:00:00+00:00",
  updatedAt: "2026-09-17T08:30:00+00:00",
};

/** Every character a query string, a path separator or a fragment would claim. */
const TRICKY_PATH = "C:\\customer dir\\a/b?c&d#e%25\\f";

const TRANSPORT_MESSAGE = "The CAN-X Runtime could not be reached.";

const UNREADABLE_FAILURE_MESSAGE =
  "The CAN-X Runtime answered with a failure this client cannot interpret.";

const UNREADABLE_PROJECT_MESSAGE =
  "The CAN-X Runtime answered with a project payload that does not match the contract.";

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

/**
 * Record every fetch call so method, URL and headers stay observable.
 *
 * A fresh clone is answered per call: a `Response` body can be read once, and a
 * test that exercises "the same client, told twice" must not fail because the
 * harness handed the second call a body the first one already consumed.
 */
function stubFetch(response: Response): StubbedFetch {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(response.clone());
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

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => {
      throw new Error("expected the inspect to be rejected");
    },
    (cause: unknown) => cause,
  );
}

/** The `project_path` the Runtime would read out of a URL this client built. */
function askedFor(url: string): string | null {
  return new URL(url).searchParams.get("project_path");
}

describe("the Runtime project inspect read model", () => {
  it("resolves a validated project as exactly the five camelCase fields", async () => {
    stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await expect(inspectProject(PROJECT_PATH)).resolves.toEqual(READ_MODEL);
  });

  it("builds a fresh object holding exactly the declared five fields", async () => {
    stubFetch(
      jsonResponse(
        {
          ...RUNTIME_PROJECT,
          project_path: "C:\\customer\\secret-program",
          manifest_path: ".canx\\project.json",
        },
        200,
      ),
    );

    const model = await inspectProject(PROJECT_PATH);

    expect(Object.keys(model).sort()).toEqual([
      "createdAt",
      "displayName",
      "projectId",
      "schemaVersion",
      "updatedAt",
    ]);
    expect(JSON.stringify(model)).not.toContain("secret-program");
    expect(JSON.stringify(model)).not.toContain("manifest_path");
  });

  it("never snakes the Runtime's field names through to the caller", async () => {
    stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    const model = await inspectProject(PROJECT_PATH);

    for (const runtimeName of Object.keys(RUNTIME_PROJECT)) {
      expect(Object.keys(model)).not.toContain(runtimeName);
    }
  });
});

describe("the Runtime project inspect request", () => {
  it("sends one GET to the project inspect endpoint", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject(PROJECT_PATH);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("GET");
    expect(calls[0]?.url.startsWith(`${INSPECT_URL}?project_path=`)).toBe(true);
  });

  it("names the project on the call and keeps no module-level current project", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject("C:\\first");
    await inspectProject("C:\\second");

    expect(calls.map((call) => askedFor(call.url))).toEqual(["C:\\first", "C:\\second"]);
  });

  it("carries no body and no caller-authored header", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject(PROJECT_PATH);

    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.init.headers).toBeUndefined();
  });
});

describe("the project path on the wire", () => {
  it("sends an empty-free, exactly-once project_path parameter", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject(PROJECT_PATH);

    const url = calls[0]?.url ?? "";
    expect(url.split("?").length).toBe(2);
    expect(url).not.toContain("&");
    expect(askedFor(url)).toBe(PROJECT_PATH);
  });

  it("encodes spaces, ?, &, #, / and % so none of them becomes structure", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject(TRICKY_PATH);

    const url = calls[0]?.url ?? "";
    expect(url.split("?").length).toBe(2);
    expect(url).not.toContain("&");
    expect(url).not.toContain("#");
    expect(url).not.toContain(" ");
    expect(url).toContain("%3F");
    expect(url).toContain("%26");
    expect(url).toContain("%23");
    expect(url).toContain("%2F");
    expect(url).toContain("%25");
  });

  it("hands the Runtime back the path byte for byte", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject(TRICKY_PATH);

    expect(askedFor(calls[0]?.url ?? "")).toBe(TRICKY_PATH);
  });

  it("normalises nothing: case, separators and relative segments travel as written", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject("C:/Mixed/Case/../Dir/");

    expect(askedFor(calls[0]?.url ?? "")).toBe("C:/Mixed/Case/../Dir/");
  });

  it("sends a rooted POSIX path unchanged as well", async () => {
    const { calls } = stubFetch(jsonResponse(RUNTIME_PROJECT, 200));

    await inspectProject("/home/user/Project One");

    expect(askedFor(calls[0]?.url ?? "")).toBe("/home/user/Project One");
  });
});

describe("a malformed success payload", () => {
  it("refuses a payload missing a declared field", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_PROJECT, updated_at: undefined }, 200));

    await expect(inspectProject(PROJECT_PATH)).rejects.toBeInstanceOf(
      RuntimeProjectContractError,
    );
  });

  it("refuses a field whose type is wrong", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_PROJECT, schema_version: "1" }, 200));

    await expect(inspectProject(PROJECT_PATH)).rejects.toBeInstanceOf(
      RuntimeProjectContractError,
    );
  });

  it("refuses a non-integer schema version", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_PROJECT, schema_version: 1.5 }, 200));

    await expect(inspectProject(PROJECT_PATH)).rejects.toBeInstanceOf(
      RuntimeProjectContractError,
    );
  });

  it("refuses a payload that is not an object", async () => {
    stubFetch(jsonResponse("project", 200));

    await expect(inspectProject(PROJECT_PATH)).rejects.toBeInstanceOf(
      RuntimeProjectContractError,
    );
  });

  it("refuses an array where an object was declared", async () => {
    stubFetch(jsonResponse([RUNTIME_PROJECT], 200));

    await expect(inspectProject(PROJECT_PATH)).rejects.toBeInstanceOf(
      RuntimeProjectContractError,
    );
  });

  it("yields no read model at all when a payload is refused", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_PROJECT, display_name: 7 }, 200));

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    expect(cause).toBeInstanceOf(RuntimeProjectContractError);
    expect(cause).not.toBeInstanceOf(RuntimeProjectApiError);
    expect((cause as Error).message).toBe(UNREADABLE_PROJECT_MESSAGE);
  });
});

describe("a structured Runtime failure", () => {
  it("raises the Runtime's own diagnosis with the whole envelope intact", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "project.not_found",
          message: "The project could not be opened.",
          details: { reason: "manifest_missing" },
          recoverable: false,
          source: "project",
        },
        404,
      ),
    );

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    expect(cause).toBeInstanceOf(RuntimeProjectApiError);
    const failure = cause as RuntimeProjectApiError;
    expect(failure.status).toBe(404);
    expect(failure.code).toBe("project.not_found");
    expect(failure.message).toBe("project.not_found: The project could not be opened.");
    expect(failure.details).toEqual({ reason: "manifest_missing" });
    expect(failure.recoverable).toBe(false);
    expect(failure.source).toBe("project");
  });

  it("keeps a recoverable project failure recoverable", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "project.database_schema_invalid",
          message: "The project database does not match the manifest.",
          details: {},
          recoverable: true,
          source: "project",
        },
        500,
      ),
    );

    const failure = (await rejectionOf(inspectProject(PROJECT_PATH))) as RuntimeProjectApiError;

    expect(failure.status).toBe(500);
    expect(failure.recoverable).toBe(true);
    expect(failure.code).toBe("project.database_schema_invalid");
  });

  it("keeps a project failure distinguishable from an api failure", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "api.request_validation_failed",
          message: "The request payload does not match the API contract.",
          details: { errors: [{ location: ["query", "project_path"], type: "missing" }] },
          recoverable: false,
          source: "api",
        },
        422,
      ),
    );

    const failure = (await rejectionOf(inspectProject(PROJECT_PATH))) as RuntimeProjectApiError;

    expect(failure.code).toBe("api.request_validation_failed");
    expect(failure.source).toBe("api");
    expect(failure.status).toBe(422);
    expect(failure.details["errors"]).toEqual([
      { location: ["query", "project_path"], type: "missing" },
    ]);
  });
});

describe("the Runtime's own details are the only place a path may appear", () => {
  /**
   * The Runtime fills `details` with `str(path)` on its own project failures, and
   * its five-field envelope is preserved intact by contract — so a Runtime-provided
   * path *does* reach the ApiError surface. What this client must never do is add
   * one of its own, or strip the Runtime's down.
   */
  const RUNTIME_PATH = "C:\\customer\\secret-program";

  function apiFailure(details: Record<string, unknown>): void {
    stubFetch(
      jsonResponse(
        {
          code: "project.not_found",
          message: "The project could not be opened.",
          details,
          recoverable: false,
          source: "project",
        },
        404,
      ),
    );
  }

  it("preserves a Runtime-provided path in details, unchanged and complete", async () => {
    apiFailure({ path: RUNTIME_PATH });

    const failure = (await rejectionOf(inspectProject(PROJECT_PATH))) as RuntimeProjectApiError;

    expect(failure).toBeInstanceOf(RuntimeProjectApiError);
    expect(failure.details).toEqual({ path: RUNTIME_PATH });
    expect(failure.details["path"]).toBe(RUNTIME_PATH);
  });

  it("keeps the five-field envelope intact and invents no sixth field", async () => {
    apiFailure({ path: RUNTIME_PATH, schema_version: 1 });

    const failure = (await rejectionOf(inspectProject(PROJECT_PATH))) as RuntimeProjectApiError;

    expect(failure.status).toBe(404);
    expect(failure.code).toBe("project.not_found");
    expect(failure.recoverable).toBe(false);
    expect(failure.source).toBe("project");
    expect(failure.details).toEqual({ path: RUNTIME_PATH, schema_version: 1 });
  });

  it("does not strip a Runtime details field it does not recognise", async () => {
    apiFailure({ path: RUNTIME_PATH, missing_columns: ["created_at"] });

    const failure = (await rejectionOf(inspectProject(PROJECT_PATH))) as RuntimeProjectApiError;

    expect(Object.keys(failure.details).sort()).toEqual(["missing_columns", "path"]);
    expect(failure.details["missing_columns"]).toEqual(["created_at"]);
  });

  it("adds no client-authored path, URL or raw body when the Runtime's details carry none", async () => {
    apiFailure({ reason: "manifest_missing" });

    const failure = (await rejectionOf(inspectProject(PROJECT_PATH))) as RuntimeProjectApiError;
    const text = JSON.stringify(failure, Object.getOwnPropertyNames(failure));

    expect(JSON.stringify(failure.details)).not.toContain(PROJECT_PATH);
    expect(text).not.toContain(INSPECT_URL);
    expect(text).not.toContain("project_path=");
  });
});

describe("transport and contract failures", () => {
  it("reports a rejected fetch as a transport failure", async () => {
    stubRejectingFetch(new TypeError("Failed to fetch"));

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    expect(cause).toBeInstanceOf(RuntimeProjectTransportError);
    expect((cause as Error).message).toBe(TRANSPORT_MESSAGE);
    expect((cause as Error).message).not.toContain("Failed to fetch");
  });

  it("reports a non-JSON failure response as a transport failure", async () => {
    stubFetch(new Response("<html>502 Bad Gateway</html>", { status: 502 }));

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    expect(cause).toBeInstanceOf(RuntimeProjectTransportError);
    expect((cause as Error).message).not.toContain("502 Bad Gateway");
  });

  it("reports a non-2xx answer without the shared envelope as a transport failure", async () => {
    stubFetch(jsonResponse({ detail: "gateway" }, 500));

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    expect(cause).toBeInstanceOf(RuntimeProjectTransportError);
    expect(cause).not.toBeInstanceOf(RuntimeProjectApiError);
    expect((cause as Error).message).toBe(UNREADABLE_FAILURE_MESSAGE);
  });
});

describe("diagnostics", () => {
  it("echoes neither the project path nor the body when the payload is refused", async () => {
    stubFetch(jsonResponse({ ...RUNTIME_PROJECT, project_id: { leaked: TRICKY_PATH } }, 200));

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    const text = JSON.stringify(cause, Object.getOwnPropertyNames(cause));
    expect((cause as Error).message).toBe(UNREADABLE_PROJECT_MESSAGE);
    expect(text).not.toContain(PROJECT_PATH);
    expect(text).not.toContain("secret-program");
    expect(text).not.toContain(TRICKY_PATH);
  });

  it("echoes neither the project path nor the URL when the Runtime is unreachable", async () => {
    stubRejectingFetch(new TypeError(`fetch failed for ${INSPECT_URL}?project_path=...`));

    const cause = await rejectionOf(inspectProject(TRICKY_PATH));

    const text = JSON.stringify(cause, Object.getOwnPropertyNames(cause));
    expect((cause as Error).message).toBe(TRANSPORT_MESSAGE);
    expect(text).not.toContain(TRICKY_PATH);
    expect(text).not.toContain(INSPECT_URL);
  });

  it("builds the Runtime's own diagnosis from the envelope alone", async () => {
    stubFetch(jsonResponse({ detail: "not the envelope" }, 400));

    const cause = await rejectionOf(inspectProject(PROJECT_PATH));

    const text = JSON.stringify(cause, Object.getOwnPropertyNames(cause));
    expect((cause as Error).message).toBe(UNREADABLE_FAILURE_MESSAGE);
    expect(text).not.toContain("not the envelope");
    expect(text).not.toContain(PROJECT_PATH);
  });
});
