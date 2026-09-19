import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  decodeDbcFrameBatch,
} from "./dbc-decode-client";
import type { DecodeBatchResult, DecodeFrameBatchInput } from "./dbc-decode-client";
import type { RuntimeFrame } from "./frame-schema";

afterEach(() => vi.unstubAllGlobals());

const DECODE_BATCH_URL = "http://127.0.0.1:8765/dbc/assets/dbc-asset-0001/decode-batch";

const PROJECT_PATH = "C:\\customer\\secret-program";
const STREAM_ID = "stream-1";

/**
 * One canonical input frame whose fields are all distinguishable: the payload is
 * not all-zero, the three timestamps differ, and the sequence is above one — so a
 * serializer that dropped, reordered or defaulted a field would be caught.
 */
function frame(overrides: Partial<RuntimeFrame> = {}): RuntimeFrame {
  return {
    sequence: 1n,
    channelId: "can0",
    arbitrationId: 0x123,
    isExtended: false,
    isFd: false,
    bitrateSwitch: false,
    errorStateIndicator: false,
    dlc: 3,
    data: new Uint8Array([0x01, 0xaf, 0x00]),
    direction: "rx",
    hardwareTimestamp: 1.5,
    hostTimestamp: 2.5,
    normalizedTimestamp: 3.5,
    clockDomain: "monotonic",
    timestampQuality: "hardware",
    flags: 0,
    ...overrides,
  };
}

/** The wire frame the Runtime would echo back for {@link frame}. */
function wireFrame(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sequence: 1,
    channel_id: "can0",
    arbitration_id: 0x123,
    is_extended: false,
    is_fd: false,
    bitrate_switch: false,
    error_state_indicator: false,
    dlc: 3,
    data: "01AF00",
    direction: "rx",
    hardware_timestamp: 1.5,
    host_timestamp: 2.5,
    normalized_timestamp: 3.5,
    clock_domain: "monotonic",
    timestamp_quality: "hardware",
    flags: 0,
    ...overrides,
  };
}

function wireSignal(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "EngineSpeed",
    raw_value: 100,
    physical_value: 250.0,
    choice_label: null,
    unit: "rpm",
    ...overrides,
  };
}

function wireDecoded(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    message_name: "EngineData",
    signals: [wireSignal()],
    ...overrides,
  };
}

/** A successful one-outcome batch for the given frames, echoed as the Runtime would. */
function wireBatchFor(source: readonly RuntimeFrame[]): Record<string, unknown> {
  const outcomes = source.map((input) => ({
    frame: wireFrame({
      sequence: Number(input.sequence),
      channel_id: input.channelId,
      arbitration_id: input.arbitrationId,
      is_extended: input.isExtended,
    }),
    decoded: wireDecoded(),
    failure: null,
  }));
  const sequences = source.map((input) => Number(input.sequence));
  return {
    schema_version: 1,
    stream_id: STREAM_ID,
    first_sequence: sequences[0],
    last_sequence: sequences[sequences.length - 1],
    frame_count: source.length,
    outcomes,
  };
}

/** One batch carrying exactly one outcome the caller wrote. */
function batchWithOutcome(outcome: unknown): Record<string, unknown> {
  return {
    schema_version: 1,
    stream_id: STREAM_ID,
    first_sequence: 1,
    last_sequence: 1,
    frame_count: 1,
    outcomes: [outcome],
  };
}

function framesOf(count: number): RuntimeFrame[] {
  return Array.from({ length: count }, (_, index) => frame({ sequence: BigInt(index + 1) }));
}

const INPUT: DecodeFrameBatchInput = {
  projectPath: PROJECT_PATH,
  assetId: "dbc-asset-0001",
  streamId: STREAM_ID,
  frames: [frame()],
};

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
 * `Response` can only be built from a body, and `JSON.stringify` turns `NaN` and
 * `Infinity` into `null` — which would let a `typeof` check stand in for the
 * finiteness check. These payloads are therefore handed over as objects, carrying
 * the exact values a JSON encoder could produce.
 */
function rawJsonResponse(body: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
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

function framesOfBody(call: FetchCall): Record<string, unknown>[] {
  const frames = bodyOf(call)["frames"];
  if (!Array.isArray(frames)) throw new Error("the request body carried no frames array");
  return frames as Record<string, unknown>[];
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
      throw new Error("expected the call to be rejected");
    },
    (cause: unknown) => cause,
  );
}

describe("the Runtime decode-batch request", () => {
  it("serializes all sixteen frame fields, in snake_case", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch(INPUT);

    const frames = framesOfBody(calls[0] as FetchCall);
    expect(Object.keys(frames[0] as Record<string, unknown>).sort()).toEqual([
      "arbitration_id",
      "bitrate_switch",
      "channel_id",
      "clock_domain",
      "data",
      "direction",
      "dlc",
      "error_state_indicator",
      "flags",
      "hardware_timestamp",
      "host_timestamp",
      "is_extended",
      "is_fd",
      "normalized_timestamp",
      "sequence",
      "timestamp_quality",
    ]);
  });

  it("sends exactly the three request fields the contract declares", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch(INPUT);

    expect(Object.keys(bodyOf(calls[0] as FetchCall)).sort()).toEqual([
      "frames",
      "project_path",
      "stream_id",
    ]);
  });

  it("posts JSON to the decode-batch URL with the asset id as one path segment", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch(INPUT);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("POST");
    expect(calls[0]?.url).toBe(DECODE_BATCH_URL);
    expect(headerOf(calls[0] as FetchCall, "Content-Type")).toBe("application/json");
  });

  it("percent-encodes the asset id so it cannot change the URL's structure", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch({ ...INPUT, assetId: "a/b?c#d e%f" });

    expect(calls[0]?.url).toBe(
      "http://127.0.0.1:8765/dbc/assets/a%2Fb%3Fc%23d%20e%25f/decode-batch",
    );
  });

  it("carries project_path in the body, never in the query", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch(INPUT);

    const call = calls[0] as FetchCall;
    expect(bodyOf(call)["project_path"]).toBe(PROJECT_PATH);
    expect(call.url).not.toContain("?");
    expect(call.url).not.toContain("secret-program");
  });

  it("forwards the stream id unchanged, including case", async () => {
    const response = { ...wireBatchFor(INPUT.frames), stream_id: "Bus_A.Stream" };
    const { calls } = stubFetch(jsonResponse(response, 200));

    await decodeDbcFrameBatch({ ...INPUT, streamId: "Bus_A.Stream" });

    expect(bodyOf(calls[0] as FetchCall)["stream_id"]).toBe("Bus_A.Stream");
  });

  it("encodes the payload as uppercase hexadecimal, not Base64 and not a byte array", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch(INPUT);

    expect(framesOfBody(calls[0] as FetchCall)[0]?.["data"]).toBe("01AF00");
  });
});

describe("the decode-batch sequence encoding", () => {
  it("renders a safe sequence as a JSON number", async () => {
    const { calls } = stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    await decodeDbcFrameBatch(INPUT);

    const sequence = framesOfBody(calls[0] as FetchCall)[0]?.["sequence"];
    expect(sequence).toBe(1);
    expect(typeof sequence).toBe("number");
  });

  it("accepts the largest sequence that still round-trips as a JSON number", async () => {
    const safe = BigInt(Number.MAX_SAFE_INTEGER);
    const { calls } = stubFetch(
      jsonResponse(wireBatchFor([frame({ sequence: safe })]), 200),
    );

    await decodeDbcFrameBatch({ ...INPUT, frames: [frame({ sequence: safe })] });

    expect(framesOfBody(calls[0] as FetchCall)[0]?.["sequence"]).toBe(
      Number.MAX_SAFE_INTEGER,
    );
  });

  it("refuses a sequence that cannot survive as a JSON number", async () => {
    const unsafe = BigInt(Number.MAX_SAFE_INTEGER) + 1n;

    await expect(
      decodeDbcFrameBatch({ ...INPUT, frames: [frame({ sequence: unsafe })] }),
    ).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });
});

describe("the decode-batch bounds", () => {
  it("refuses an empty batch", async () => {
    await expect(decodeDbcFrameBatch({ ...INPUT, frames: [] })).rejects.toBeInstanceOf(
      RuntimeDbcContractError,
    );
  });

  it("accepts a batch of one thousand frames", async () => {
    const batch = framesOf(1000);
    stubFetch(jsonResponse(wireBatchFor(batch), 200));

    const result = await decodeDbcFrameBatch({ ...INPUT, frames: batch });

    expect(result.frameCount).toBe(1000);
    expect(result.records).toHaveLength(1000);
    expect(result.lastSequence).toBe(1000n);
  });

  it("refuses a batch of one thousand and one frames", async () => {
    await expect(
      decodeDbcFrameBatch({ ...INPUT, frames: framesOf(1001) }),
    ).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });
});

describe("a decoded batch", () => {
  it("resolves as a typed value, in the submitted order", async () => {
    stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    const result: DecodeBatchResult = await decodeDbcFrameBatch(INPUT);

    expect(result.streamId).toBe(STREAM_ID);
    expect(result.firstSequence).toBe(1n);
    expect(result.lastSequence).toBe(1n);
    expect(result.frameCount).toBe(1);
    expect(result.records).toHaveLength(1);
    expect(result.records[0]?.outcome.decoded?.messageName).toBe("EngineData");
    expect(result.records[0]?.outcome.decoded?.signals).toEqual([
      {
        name: "EngineSpeed",
        rawValue: 100,
        physicalValue: 250,
        choiceLabel: null,
        unit: "rpm",
      },
    ]);
  });

  it("keeps the submitted frame object, not the one the Runtime echoed", async () => {
    stubFetch(jsonResponse(wireBatchFor(INPUT.frames), 200));

    const result = await decodeDbcFrameBatch(INPUT);

    expect(result.records[0]?.frame).toBe(INPUT.frames[0]);
  });

  it("carries a per-frame failure as data, not as a thrown error", async () => {
    stubFetch(
      rawJsonResponse(
        batchWithOutcome({
          frame: wireFrame(),
          decoded: null,
          failure: {
            code: "dbc.frame_not_decodable",
            message: "No message matched this frame.",
            details: {},
            recoverable: false,
            source: "dbc",
          },
        }),
        200,
      ),
    );

    const result = await decodeDbcFrameBatch(INPUT);

    expect(result.records[0]?.outcome.decoded).toBeNull();
    expect(result.records[0]?.outcome.failure?.code).toBe("dbc.frame_not_decodable");
  });

  it("projects each signal onto exactly the camelCase fields the contract declares", async () => {
    stubFetch(
      rawJsonResponse(
        batchWithOutcome({
          frame: wireFrame(),
          decoded: wireDecoded({ signals: [wireSignal({ decode_index: 7 })] }),
          failure: null,
        }),
        200,
      ),
    );

    const result = await decodeDbcFrameBatch(INPUT);

    const signal = result.records[0]?.outcome.decoded?.signals[0];
    expect(Object.keys(signal as object).sort()).toEqual([
      "choiceLabel",
      "name",
      "physicalValue",
      "rawValue",
      "unit",
    ]);
  });
});

describe("malformed decode-batch payloads", () => {
  /** The only assertion every malformed case shares: refuse, do not half-accept. */
  async function expectBatchRefusal(body: unknown): Promise<void> {
    stubFetch(rawJsonResponse(body, 200));
    await expect(decodeDbcFrameBatch(INPUT)).rejects.toBeInstanceOf(RuntimeDbcContractError);
  }

  it("refuses a payload that is not an object", async () => {
    await expectBatchRefusal([]);
  });

  it("refuses a schema version it does not speak", async () => {
    await expectBatchRefusal({ ...wireBatchFor(INPUT.frames), schema_version: 2 });
  });

  it("refuses a response for a different stream", async () => {
    await expectBatchRefusal({ ...wireBatchFor(INPUT.frames), stream_id: "other-stream" });
  });

  it("refuses a frame_count that does not match the outcomes", async () => {
    await expectBatchRefusal({ ...wireBatchFor(INPUT.frames), frame_count: 2 });
  });

  it("refuses a response sequence that cannot survive as a JSON number", async () => {
    await expectBatchRefusal({
      ...wireBatchFor(INPUT.frames),
      first_sequence: Number.MAX_SAFE_INTEGER + 1,
    });
  });

  it("refuses an outcome whose decoded and failure are both present", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame(),
        decoded: wireDecoded(),
        failure: { code: "dbc.frame_not_decodable" },
      }),
    );
  });

  it("refuses an outcome whose decoded and failure are both absent", async () => {
    await expectBatchRefusal(batchWithOutcome({ frame: wireFrame(), decoded: null, failure: null }));
  });

  it("refuses an outcome that is missing the frame it belongs to", async () => {
    await expectBatchRefusal(batchWithOutcome({ decoded: wireDecoded(), failure: null }));
  });

  it("refuses a signal whose raw_value is not an integer", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame(),
        decoded: wireDecoded({ signals: [wireSignal({ raw_value: "100" })] }),
        failure: null,
      }),
    );
  });

  it("refuses a signal whose physical_value is not a finite number", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame(),
        decoded: wireDecoded({ signals: [wireSignal({ physical_value: Number.NaN })] }),
        failure: null,
      }),
    );
  });

  it("refuses a signal whose choice_label is neither a string nor null", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame(),
        decoded: wireDecoded({ signals: [wireSignal({ choice_label: 7 })] }),
        failure: null,
      }),
    );
  });

  it("refuses an outcome whose echoed frame does not match the submitted one", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame({ arbitration_id: 0x456 }),
        decoded: wireDecoded(),
        failure: null,
      }),
    );
  });

  it("refuses an outcome whose echoed frame carries a different payload", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame({ data: "FF" }),
        decoded: wireDecoded(),
        failure: null,
      }),
    );
  });

  it("refuses outcomes that are not in the submitted order", async () => {
    const source = [frame({ sequence: 1n }), frame({ sequence: 2n })];
    const batch = wireBatchFor(source);
    const outcomes = batch["outcomes"] as unknown[];
    batch["outcomes"] = [outcomes[1], outcomes[0]];
    stubFetch(rawJsonResponse(batch, 200));

    await expect(
      decodeDbcFrameBatch({ ...INPUT, frames: source }),
    ).rejects.toBeInstanceOf(RuntimeDbcContractError);
  });

  it("refuses a frame whose direction is not rx or tx", async () => {
    await expectBatchRefusal(
      batchWithOutcome({
        frame: wireFrame({ direction: "both" }),
        decoded: wireDecoded(),
        failure: null,
      }),
    );
  });
});

describe("the decode-batch failures", () => {
  it("reports an unreachable Runtime as a transport failure", async () => {
    stubRejectingFetch(new TypeError("network down"));

    await expect(decodeDbcFrameBatch(INPUT)).rejects.toBeInstanceOf(RuntimeDbcTransportError);
  });

  it("preserves the Runtime's structured diagnosis, status and code", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "dbc.asset_not_found",
          message: "The requested DBC asset is not registered in this project.",
          details: {},
          recoverable: false,
          source: "dbc",
        },
        404,
      ),
    );

    const cause = await rejectionOf(decodeDbcFrameBatch(INPUT));

    expect(cause).toBeInstanceOf(RuntimeDbcApiError);
    expect((cause as RuntimeDbcApiError).code).toBe("dbc.asset_not_found");
    expect((cause as RuntimeDbcApiError).status).toBe(404);
    expect((cause as RuntimeDbcApiError).source).toBe("dbc");
  });

  it("reports a non-envelope failure as a transport failure, not an API error", async () => {
    stubFetch(jsonResponse({ detail: "Not Found" }, 404));

    await expect(decodeDbcFrameBatch(INPUT)).rejects.toBeInstanceOf(RuntimeDbcTransportError);
  });

  it("never echoes the project path in a diagnosis", async () => {
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

    const cause = await rejectionOf(decodeDbcFrameBatch(INPUT));

    expect((cause as Error).message).not.toContain("secret-program");
  });
});
