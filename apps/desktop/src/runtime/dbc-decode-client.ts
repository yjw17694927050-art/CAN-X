/**
 * The Runtime DBC decode client — the renderer's typed view of the decode-batch
 * endpoint.
 *
 * This module belongs to the **Runtime HTTP boundary**, and it is the sibling of
 * `dbc-client.ts`: that client owns the DBC asset surface (import, list, get,
 * database), and this one owns the single call that turns a bounded batch of
 * captured frames into decoded values. The transport they share — the origin, the
 * one request path and the three failure types — lives in `./runtime-http` and is
 * deliberately not restated here.
 *
 * ```text
 *   DecodeFrameBatchInput { projectPath, assetId, streamId, frames }
 *     ↓ POST http://127.0.0.1:8765/dbc/assets/{assetId}/decode-batch
 *   DecodeBatchResult { streamId, firstSequence, lastSequence, frameCount, records }
 * ```
 *
 * Six properties are deliberate:
 *
 * * **The decoding is not here.** No bit extraction, no `factor`/`offset`
 *   arithmetic, no `VAL_` lookup and no DBC parse happens in this module. The
 *   Runtime owns what a frame decodes to; a second implementation here would be a
 *   second answer to the same question.
 * * **The whole frame goes over, in one spelling.** Every one of the sixteen
 *   fields is serialized as `snake_case` and the payload as uppercase
 *   hexadecimal — the exact spelling the Runtime's `FrameWire` produces — so a
 *   batch this client sends is the batch the Runtime reads.
 * * **A sequence is a number only while it is one.** `sequence` is a `bigint`
 *   here and an integer on the wire; it travels as a JSON number only while
 *   `Number.isSafeInteger` holds, and otherwise the call is refused rather than
 *   truncated, wrapped or rounded.
 * * **The response is validated, never asserted.** The schema version, the stream
 *   identity, the frame count, the outcome order and every echoed frame are
 *   checked against the request rather than believed, and a fresh object is built
 *   field by field. A malformed outcome or a mismatched echoed frame refuses the
 *   whole payload: there is no half-valid batch.
 * * **The submitted frame is the record's frame.** `records[i].frame` is the very
 *   object the caller passed, never the echo the Runtime returned. The echoed
 *   frame exists to prove the outcome belongs to that input; it is not allowed to
 *   replace it.
 * * **A failure never echoes the request.** The shared error types have no field
 *   into which a payload could leak, and no message is built from the project
 *   path, the submitted frames or the response body.
 */

import type { FrameDirection, RuntimeFrame, TimestampQuality } from "./frame-schema";
import {
  RUNTIME_URL,
  RuntimeDbcContractError,
  readResponse,
  runtimeFetch,
} from "./runtime-http";

/**
 * The Runtime boundary's three failure types are defined once, in `./runtime-http`,
 * and re-exported here so a caller branching on one of them sees *the* class rather
 * than a second one that merely shares its name.
 */
export {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
} from "./runtime-http";

/** The DBC asset collection the decode-batch endpoint hangs off. */
const DBC_ASSETS_PATH = "/dbc/assets";

/** This endpoint's own suffix under one asset. */
const DECODE_BATCH_SUFFIX = "/decode-batch";

/**
 * The HTTP guard on one decode-batch call, mirroring the Runtime's own
 * `MAX_BATCH_FRAMES`. Enforced here too, so a request that could never succeed is
 * refused before it leaves the renderer.
 */
const MAX_BATCH_FRAMES = 1000;

/** The only decoded-batch schema this client understands. */
const DECODED_BATCH_SCHEMA_VERSION = 1;

/** The one message for a request this client refuses to send. */
const INVALID_REQUEST_MESSAGE = "The decode-batch request does not match the contract.";

/** The one message for a 2xx answer whose decode-batch shape is wrong. */
const UNREADABLE_BATCH_MESSAGE =
  "The CAN-X Runtime answered with a decode-batch payload that does not match the contract.";

/** The four facts a decode-batch call takes: where, against what, and which frames. */
export interface DecodeFrameBatchInput {
  /** The CAN-X project that owns the asset. */
  readonly projectPath: string;
  /** The registered DBC asset the frames are decoded against. */
  readonly assetId: string;
  /** The stream the submitted frames belong to. */
  readonly streamId: string;
  /** The frames to decode, in submission order. Between one and one thousand. */
  readonly frames: readonly RuntimeFrame[];
}

/** One decoded signal value, projected onto the Desktop's camelCase shape. */
export interface DecodedSignalValue {
  readonly name: string;
  /** The raw integer the signal's bits carried, before scaling. */
  readonly rawValue: number;
  /** `raw * factor + offset`, as the Runtime computed it. */
  readonly physicalValue: number;
  /** The `VAL_` label naming this raw value, or `null` when none was declared. */
  readonly choiceLabel: string | null;
  readonly unit: string | null;
}

/**
 * What happened to exactly one frame: it decoded, or it failed with a typed code.
 *
 * `decoded` and `failure` are mutually exclusive — exactly one is non-`null` — so a
 * caller branches on one fact instead of guessing which half to trust.
 */
export type DecodedFrameOutcome =
  | {
      readonly decoded: {
        readonly messageName: string;
        readonly signals: readonly DecodedSignalValue[];
      };
      readonly failure: null;
    }
  | { readonly decoded: null; readonly failure: { readonly code: string } };

/** One frame's outcome, bound to the frame object the caller submitted. */
export interface DecodedFrameRecord {
  readonly frame: RuntimeFrame;
  readonly outcome: DecodedFrameOutcome;
}

/** One decoded batch, in the submitted frame order. */
export interface DecodeBatchResult {
  readonly streamId: string;
  readonly firstSequence: bigint;
  readonly lastSequence: bigint;
  readonly frameCount: number;
  readonly records: readonly DecodedFrameRecord[];
}

/**
 * Decode a bounded batch of captured frames against one project-owned DBC asset.
 *
 * Args:
 *   input: The project and asset to decode against, the stream the frames belong
 *     to, and the frames themselves, in the order outcomes are wanted back.
 *
 * Returns:
 *   One {@link DecodedFrameRecord} per submitted frame, in the submitted order,
 *   each bound to the very frame object that was submitted.
 *
 * Throws:
 *   {@link RuntimeDbcContractError} when the request is refused before it is sent
 *   — an empty or oversized batch, or a sequence that cannot survive as a JSON
 *   number — or when the Runtime answered successfully with a payload this
 *   contract does not describe.
 *   {@link RuntimeDbcApiError} when the Runtime diagnosed the failure itself.
 *   {@link RuntimeDbcTransportError} when the Runtime could not be reached, or
 *   answered with a failure that is not the shared envelope.
 */
export async function decodeDbcFrameBatch(
  input: DecodeFrameBatchInput,
): Promise<DecodeBatchResult> {
  if (input.frames.length < 1 || input.frames.length > MAX_BATCH_FRAMES) {
    throw new RuntimeDbcContractError(INVALID_REQUEST_MESSAGE);
  }
  requireContiguous(input.frames);
  const submitted = input.frames.map((frame) => frameToWire(frame));
  const payload = await readResponse(await post(input, submitted));
  return readBatch(payload, input, submitted);
}

/**
 * Refuse a batch whose sequences are not consecutive.
 *
 * A decode batch is a *slice of one stream's numbering*, which is what makes `first_sequence` and
 * `last_sequence` meaningful and what lets the Runtime read the frames as a range. A hole in the
 * middle is therefore not a smaller batch — it is a different claim about which frames these are.
 *
 * This is the HTTP boundary's own invariant, not a restatement of the coordinator's partitioning:
 * the coordinator decides *which* frames belong to one asset, and this decides whether the batch
 * it hands over is well formed. A caller that assembled frames by hand cannot slip a hole past it.
 *
 * Throws:
 *   {@link RuntimeDbcContractError} before anything is sent, using the same static request
 *   message as the size guard — there is one reason a request is refused, and one message for it.
 */
function requireContiguous(frames: readonly RuntimeFrame[]): void {
  for (let index = 1; index < frames.length; index += 1) {
    const previous = frames[index - 1];
    const current = frames[index];
    if (previous === undefined || current === undefined) {
      throw new RuntimeDbcContractError(INVALID_REQUEST_MESSAGE);
    }
    if (current.sequence !== previous.sequence + 1n) {
      throw new RuntimeDbcContractError(INVALID_REQUEST_MESSAGE);
    }
  }
}

/**
 * Send the decode-batch request.
 *
 * The asset id is encoded as a single path segment and the project path travels in
 * the body, so a separator, a query character, a fragment or whitespace in either
 * value is data and never structure. What an unreachable Runtime means is the shared
 * transport's business, not this module's.
 */
async function post(
  input: DecodeFrameBatchInput,
  frames: readonly FrameWireBody[],
): Promise<Response> {
  return runtimeFetch(
    `${RUNTIME_URL}${DBC_ASSETS_PATH}/${encodeURIComponent(input.assetId)}${DECODE_BATCH_SUFFIX}`,
    {
      body: JSON.stringify({
        project_path: input.projectPath,
        stream_id: input.streamId,
        frames,
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    },
  );
}

/**
 * Validate a decode-batch body and project it onto {@link DecodeBatchResult}.
 *
 * Every redundant fact the Runtime echoes — schema version, stream identity, frame
 * count, outcome order and each frame — is checked against the request rather than
 * trusted: an answer that disagrees with what was sent does not describe this
 * request, so it refuses the whole payload instead of being half-mapped.
 */
function readBatch(
  payload: unknown,
  input: DecodeFrameBatchInput,
  submitted: readonly FrameWireBody[],
): DecodeBatchResult {
  const message = UNREADABLE_BATCH_MESSAGE;
  const record = readObject(payload, message);
  if (readInteger(record["schema_version"], message) !== DECODED_BATCH_SCHEMA_VERSION) {
    throw new RuntimeDbcContractError(message);
  }
  const streamId = readString(record["stream_id"], message);
  if (streamId !== input.streamId) {
    throw new RuntimeDbcContractError(message);
  }
  const firstSequence = readSequence(record["first_sequence"], message);
  const lastSequence = readSequence(record["last_sequence"], message);
  // The summary must describe the slice that was submitted, and it is checked independently of
  // the echoed frames. The Runtime sends this bookkeeping precisely so a client can verify it: a
  // response whose outcomes happen to echo correctly is still not *this* batch if its summary
  // says it covers a different range.
  if (
    firstSequence !== input.frames[0]?.sequence ||
    lastSequence !== input.frames[input.frames.length - 1]?.sequence
  ) {
    throw new RuntimeDbcContractError(message);
  }
  const frameCount = readInteger(record["frame_count"], message);
  const outcomes = readArray(record["outcomes"], message, (item) => readOutcome(item, message));
  if (frameCount !== outcomes.length || frameCount !== submitted.length) {
    throw new RuntimeDbcContractError(message);
  }
  const records = outcomes.map((outcome, index): DecodedFrameRecord => {
    const frame = input.frames[index];
    const expected = submitted[index];
    if (frame === undefined || expected === undefined || !isSameFrame(outcome.frame, expected)) {
      throw new RuntimeDbcContractError(message);
    }
    return { frame, outcome: outcome.outcome };
  });
  return { streamId, firstSequence, lastSequence, frameCount, records };
}

/** One echoed frame together with the outcome it belongs to. */
interface WireOutcome {
  readonly frame: FrameWireBody;
  readonly outcome: DecodedFrameOutcome;
}

/**
 * Validate one outcome and read its exclusive half.
 *
 * `decoded` and `failure` are mutually exclusive by contract: exactly one is
 * present. Both present, or neither, describes no outcome at all, so it refuses the
 * batch instead of letting a caller guess which half to trust.
 */
function readOutcome(value: unknown, message: string): WireOutcome {
  const record = readObject(value, message);
  const frame = readFrameWire(record["frame"], message);
  const decoded = record["decoded"];
  const failure = record["failure"];
  if ((decoded === null) === (failure === null)) {
    throw new RuntimeDbcContractError(message);
  }
  if (decoded === null) {
    return { frame, outcome: { decoded: null, failure: readFailure(failure, message) } };
  }
  return { frame, outcome: { decoded: readDecoded(decoded, message), failure: null } };
}

/** Validate one decoded half and project it onto the Desktop's camelCase shape. */
function readDecoded(
  value: unknown,
  message: string,
): { messageName: string; signals: DecodedSignalValue[] } {
  const record = readObject(value, message);
  return {
    messageName: readString(record["message_name"], message),
    signals: readArray(record["signals"], message, (item) => readSignalValue(item, message)),
  };
}

/** Validate one decoded signal value, or refuse the payload. */
function readSignalValue(value: unknown, message: string): DecodedSignalValue {
  const record = readObject(value, message);
  return {
    name: readString(record["name"], message),
    rawValue: readInteger(record["raw_value"], message),
    physicalValue: readFiniteNumber(record["physical_value"], message),
    choiceLabel: readNullableString(record["choice_label"], message),
    unit: readNullableString(record["unit"], message),
  };
}

/**
 * Validate one per-frame failure against the Runtime's shared ErrorResponse shape.
 *
 * The whole envelope is checked — `code`, `message`, `details`, `recoverable`, `source` — before
 * any of it is used, because an envelope that is only partly there is a contract mismatch rather
 * than a failure code. Only `code` is projected: the UI branches on it, and the other four
 * fields would be a place for Runtime internals to reach a Trace cell. Validating the wire
 * contract and keeping a safe projection are two different jobs, and this does both in that
 * order.
 */
function readFailure(value: unknown, message: string): { code: string } {
  const record = readObject(value, message);
  const code = readString(record["code"], message);
  readString(record["message"], message);
  readObject(record["details"], message);
  readBoolean(record["recoverable"], message);
  readString(record["source"], message);
  return { code };
}

/**
 * The frame as it travels on the wire — the Runtime's own `FrameWire`, sixteen
 * `snake_case` fields with the payload spelled as uppercase hexadecimal text.
 */
interface FrameWireBody {
  readonly sequence: number;
  readonly channel_id: string;
  readonly arbitration_id: number;
  readonly is_extended: boolean;
  readonly is_fd: boolean;
  readonly bitrate_switch: boolean;
  readonly error_state_indicator: boolean;
  readonly dlc: number;
  readonly data: string;
  readonly direction: FrameDirection;
  readonly hardware_timestamp: number | null;
  readonly host_timestamp: number;
  readonly normalized_timestamp: number;
  readonly clock_domain: string;
  readonly timestamp_quality: TimestampQuality;
  readonly flags: number;
}

/**
 * Project one submitted frame onto the wire contract.
 *
 * All sixteen fields are emitted — none is dropped for being absent from the
 * decoded read model — so the Runtime sees the whole frame, timestamps and flags
 * included.
 */
function frameToWire(frame: RuntimeFrame): FrameWireBody {
  return {
    sequence: sequenceToNumber(frame.sequence),
    channel_id: frame.channelId,
    arbitration_id: frame.arbitrationId,
    is_extended: frame.isExtended,
    is_fd: frame.isFd,
    bitrate_switch: frame.bitrateSwitch,
    error_state_indicator: frame.errorStateIndicator,
    dlc: frame.dlc,
    data: encodeHexUpper(frame.data),
    direction: frame.direction,
    hardware_timestamp: frame.hardwareTimestamp,
    host_timestamp: frame.hostTimestamp,
    normalized_timestamp: frame.normalizedTimestamp,
    clock_domain: frame.clockDomain,
    timestamp_quality: frame.timestampQuality,
    flags: frame.flags,
  };
}

/** Validate one echoed wire frame and rebuild it field by field. */
function readFrameWire(value: unknown, message: string): FrameWireBody {
  const record = readObject(value, message);
  return {
    sequence: readInteger(record["sequence"], message),
    channel_id: readString(record["channel_id"], message),
    arbitration_id: readInteger(record["arbitration_id"], message),
    is_extended: readBoolean(record["is_extended"], message),
    is_fd: readBoolean(record["is_fd"], message),
    bitrate_switch: readBoolean(record["bitrate_switch"], message),
    error_state_indicator: readBoolean(record["error_state_indicator"], message),
    dlc: readInteger(record["dlc"], message),
    data: readString(record["data"], message),
    direction: readDirection(record["direction"], message),
    hardware_timestamp: readNullableFiniteNumber(record["hardware_timestamp"], message),
    host_timestamp: readFiniteNumber(record["host_timestamp"], message),
    normalized_timestamp: readFiniteNumber(record["normalized_timestamp"], message),
    clock_domain: readString(record["clock_domain"], message),
    timestamp_quality: readTimestampQuality(record["timestamp_quality"], message),
    flags: readInteger(record["flags"], message),
  };
}

/**
 * Compare two wire frames field for field.
 *
 * Every field is compared, not only the identity quartet `sequence` /
 * `channel_id` / `arbitration_id` / `is_extended`: the echoed frame exists solely
 * to prove the outcome belongs to the submitted frame, and a mismatch anywhere — a
 * different payload, a rescaled timestamp, another DLC — means it does not.
 */
function isSameFrame(a: FrameWireBody, b: FrameWireBody): boolean {
  return (
    a.sequence === b.sequence &&
    a.channel_id === b.channel_id &&
    a.arbitration_id === b.arbitration_id &&
    a.is_extended === b.is_extended &&
    a.is_fd === b.is_fd &&
    a.bitrate_switch === b.bitrate_switch &&
    a.error_state_indicator === b.error_state_indicator &&
    a.dlc === b.dlc &&
    a.data === b.data &&
    a.direction === b.direction &&
    a.hardware_timestamp === b.hardware_timestamp &&
    a.host_timestamp === b.host_timestamp &&
    a.normalized_timestamp === b.normalized_timestamp &&
    a.clock_domain === b.clock_domain &&
    a.timestamp_quality === b.timestamp_quality &&
    a.flags === b.flags
  );
}

/**
 * Read a `bigint` sequence as a JSON number, or refuse the request.
 *
 * A sequence is an unsigned 64-bit integer and a JSON number is a double: past
 * `Number.MAX_SAFE_INTEGER` the round trip is lossy. Rather than truncate, wrap or
 * round — every one of which would decode the wrong frame — the call is refused.
 */
function sequenceToNumber(sequence: bigint): number {
  const value = Number(sequence);
  if (!Number.isSafeInteger(value)) {
    throw new RuntimeDbcContractError(INVALID_REQUEST_MESSAGE);
  }
  return value;
}

/** Read one echoed sequence number as a `bigint`, or refuse the payload. */
function readSequence(value: unknown, message: string): bigint {
  return BigInt(readInteger(value, message));
}

/**
 * Encode a payload as uppercase hexadecimal, the one spelling `FrameWire` emits.
 *
 * Not Base64 and not a JSON byte array: the Runtime's `data` field is hexadecimal
 * text, and a payload spelled any other way would not be the frame the caller sent.
 */
function encodeHexUpper(data: Uint8Array): string {
  let hex = "";
  for (const byte of data) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex.toUpperCase();
}

/**
 * Read one value as a JSON object, or refuse the payload.
 *
 * Arrays are rejected explicitly: in JavaScript `typeof [] === "object"`, so a
 * payload that answered a list where an object was declared would otherwise pass a
 * naive check and be destructured into `undefined` fields.
 */
function readObject(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value as Record<string, unknown>;
}

/** Read one value as an array, validating each element with `readItem`. */
function readArray<T>(value: unknown, message: string, readItem: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new RuntimeDbcContractError(message);
  return value.map((item) => readItem(item));
}

/** Read one declared string field, or refuse the payload. */
function readString(value: unknown, message: string): string {
  if (typeof value !== "string") throw new RuntimeDbcContractError(message);
  return value;
}

/** Read one declared boolean field, or refuse the payload. `"true"` is not `true`. */
function readBoolean(value: unknown, message: string): boolean {
  if (typeof value !== "boolean") throw new RuntimeDbcContractError(message);
  return value;
}

/**
 * Read a signed safe integer, or refuse the payload.
 *
 * Any sign is accepted — an arbitration id, a DLC or a raw signal value is checked
 * by this reader — but `1.5`, `NaN`, `2 ** 53` and `"0"` all refuse: they are a
 * value the contract does not describe.
 */
function readInteger(value: unknown, message: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value;
}

/**
 * Read a finite number, or refuse the payload.
 *
 * `NaN` and `Infinity` are numbers to `typeof` but cannot describe a physical
 * value or a timestamp: they are refused rather than carried forward to become a
 * wrong value at some later consumer.
 */
function readFiniteNumber(value: unknown, message: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new RuntimeDbcContractError(message);
  }
  return value;
}

/** Read one nullable string field: `null` stays `null`, anything else must be a string. */
function readNullableString(value: unknown, message: string): string | null {
  if (value === null) return null;
  return readString(value, message);
}

/** Read one nullable finite number: `null` stays `null`, anything else must be finite. */
function readNullableFiniteNumber(value: unknown, message: string): number | null {
  if (value === null) return null;
  return readFiniteNumber(value, message);
}

/** Read one frame direction, or refuse the payload. */
function readDirection(value: unknown, message: string): FrameDirection {
  if (value !== "rx" && value !== "tx") throw new RuntimeDbcContractError(message);
  return value;
}

/** Read one timestamp quality, or refuse the payload. */
function readTimestampQuality(value: unknown, message: string): TimestampQuality {
  if (value !== "hardware" && value !== "host" && value !== "estimated") {
    throw new RuntimeDbcContractError(message);
  }
  return value;
}
