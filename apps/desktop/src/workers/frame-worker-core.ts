import { decodeFrameBatch } from "../runtime/decode-frame-batch";
import type { RuntimeFrame } from "../runtime/frame-schema";

export interface FrameViewportSnapshot {
  readonly frames: readonly RuntimeFrame[];
  readonly droppedViewFrames: number;
  readonly sequenceGaps: number;
  readonly streamId: string | null;
  /** Monotonic duration of the most recent batch decode, in milliseconds. */
  readonly decodeMs: number;
}

export class FrameWorkerCore {
  readonly #slots: Array<RuntimeFrame | undefined>;
  #start = 0;
  #size = 0;
  #frozen = false;
  #droppedViewFrames = 0;
  #sequenceGaps = 0;
  #lastSequence: bigint | null = null;
  #streamId: string | null = null;
  #lastSnapshotAt: number | null = null;
  #lastDecodeMs = 0;

  constructor(
    readonly capacity: number,
    readonly snapshotIntervalMs = 0,
    readonly clock: () => number = () => performance.now(),
  ) {
    if (!Number.isInteger(capacity) || capacity <= 0) {
      throw new Error("Frame worker capacity must be a positive integer");
    }
    if (!Number.isFinite(snapshotIntervalMs) || snapshotIntervalMs < 0) {
      throw new Error("Snapshot interval must be non-negative");
    }
    this.#slots = new Array<RuntimeFrame | undefined>(capacity);
  }

  ingest(payload: ArrayBuffer | Uint8Array): FrameViewportSnapshot | null {
    const decodeStarted = this.clock();
    const batch = decodeFrameBatch(payload);
    this.#lastDecodeMs = this.clock() - decodeStarted;
    if (this.#streamId !== null && batch.streamId !== this.#streamId) this.#resetForStream();
    this.#streamId = batch.streamId;
    if (this.#lastSequence !== null && batch.firstSequence > this.#lastSequence + 1n) {
      const gap = batch.firstSequence - this.#lastSequence - 1n;
      this.#sequenceGaps += Number(gap > BigInt(Number.MAX_SAFE_INTEGER) ? Number.MAX_SAFE_INTEGER : gap);
    }
    for (const frame of batch.frames) this.#push(frame);
    this.#lastSequence = batch.lastSequence;
    if (this.#frozen) return null;
    const now = this.clock();
    if (this.#lastSnapshotAt !== null && now - this.#lastSnapshotAt < this.snapshotIntervalMs) {
      return null;
    }
    this.#lastSnapshotAt = now;
    return this.#snapshot();
  }

  freeze(): void {
    this.#frozen = true;
  }

  resume(): FrameViewportSnapshot {
    this.#frozen = false;
    this.#lastSnapshotAt = this.clock();
    return this.#snapshot();
  }

  flush(): FrameViewportSnapshot | null {
    if (this.#frozen) return null;
    this.#lastSnapshotAt = this.clock();
    return this.#snapshot();
  }

  #push(frame: RuntimeFrame): void {
    if (this.#size < this.capacity) {
      this.#slots[(this.#start + this.#size) % this.capacity] = frame;
      this.#size += 1;
      return;
    }
    this.#slots[this.#start] = frame;
    this.#start = (this.#start + 1) % this.capacity;
    this.#droppedViewFrames += 1;
  }

  #resetForStream(): void {
    this.#slots.fill(undefined);
    this.#start = 0;
    this.#size = 0;
    this.#droppedViewFrames = 0;
    this.#sequenceGaps = 0;
    this.#lastSequence = null;
    this.#lastSnapshotAt = null;
  }

  #snapshot(): FrameViewportSnapshot {
    const frames: RuntimeFrame[] = [];
    for (let offset = 0; offset < this.#size; offset += 1) {
      const frame = this.#slots[(this.#start + offset) % this.capacity];
      if (frame !== undefined) frames.push(frame);
    }
    return {
      frames,
      droppedViewFrames: this.#droppedViewFrames,
      sequenceGaps: this.#sequenceGaps,
      streamId: this.#streamId,
      decodeMs: this.#lastDecodeMs,
    };
  }
}
