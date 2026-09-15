import { decode } from "@msgpack/msgpack";

import type { RuntimeFrame, RuntimeFrameBatch } from "./frame-schema";

type WireMap = Record<string, unknown>;

export function decodeFrameBatch(payload: ArrayBuffer | Uint8Array): RuntimeFrameBatch {
  const value = decode(payload, { useBigInt64: true });
  const batch = map(value, "FrameBatch");
  if (integer(batch.schema_version, "schema_version") !== 1) {
    throw new Error("Unsupported FrameBatch schema_version");
  }
  const framesValue = batch.frames;
  if (!Array.isArray(framesValue)) {
    throw new Error("FrameBatch frames must be an array");
  }
  const frames = framesValue.map(decodeFrame);
  const result: RuntimeFrameBatch = {
    schemaVersion: 1,
    streamId: string(batch.stream_id, "stream_id"),
    firstSequence: sequence(batch.first_sequence, "first_sequence"),
    lastSequence: sequence(batch.last_sequence, "last_sequence"),
    frameCount: integer(batch.frame_count, "frame_count"),
    frames,
  };
  if (result.frameCount !== frames.length) {
    throw new Error("FrameBatch frame_count does not match frames");
  }
  return result;
}

function decodeFrame(value: unknown): RuntimeFrame {
  const frame = map(value, "Frame");
  const data = frame.data;
  if (!(data instanceof Uint8Array)) {
    throw new Error("Frame data must be binary");
  }
  return {
    sequence: sequence(frame.sequence, "sequence"),
    channelId: string(frame.channel_id, "channel_id"),
    arbitrationId: integer(frame.arbitration_id, "arbitration_id"),
    isExtended: boolean(frame.is_extended, "is_extended"),
    isFd: boolean(frame.is_fd, "is_fd"),
    bitrateSwitch: boolean(frame.bitrate_switch, "bitrate_switch"),
    errorStateIndicator: boolean(frame.error_state_indicator, "error_state_indicator"),
    dlc: integer(frame.dlc, "dlc"),
    data,
    direction: enumeration(frame.direction, "direction", ["rx", "tx"]),
    hardwareTimestamp: nullableNumber(frame.hardware_timestamp, "hardware_timestamp"),
    hostTimestamp: number(frame.host_timestamp, "host_timestamp"),
    normalizedTimestamp: number(frame.normalized_timestamp, "normalized_timestamp"),
    clockDomain: string(frame.clock_domain, "clock_domain"),
    timestampQuality: enumeration(frame.timestamp_quality, "timestamp_quality", [
      "hardware",
      "host",
      "estimated",
    ]),
    flags: integer(frame.flags, "flags"),
  };
}

function map(value: unknown, name: string): WireMap {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${name} must be a map`);
  }
  return value as WireMap;
}

function sequence(value: unknown, name: string): bigint {
  if (typeof value === "bigint") return value;
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return BigInt(value);
  throw new Error(`${name} must be an unsigned integer`);
}

function integer(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new Error(`${name} must be an integer`);
  }
  return value;
}

function number(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number`);
  }
  return value;
}

function nullableNumber(value: unknown, name: string): number | null {
  return value === null ? null : number(value, name);
}

function string(value: unknown, name: string): string {
  if (typeof value !== "string") throw new Error(`${name} must be a string`);
  return value;
}

function boolean(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${name} must be a boolean`);
  return value;
}

function enumeration<const T extends string>(value: unknown, name: string, values: readonly T[]): T {
  if (typeof value === "string" && values.includes(value as T)) return value as T;
  throw new Error(`${name} is not a supported enum value`);
}
