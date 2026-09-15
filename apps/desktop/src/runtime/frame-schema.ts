export type FrameDirection = "rx" | "tx";
export type TimestampQuality = "hardware" | "host" | "estimated";

export interface RuntimeFrame {
  readonly sequence: bigint;
  readonly channelId: string;
  readonly arbitrationId: number;
  readonly isExtended: boolean;
  readonly isFd: boolean;
  readonly bitrateSwitch: boolean;
  readonly errorStateIndicator: boolean;
  readonly dlc: number;
  readonly data: Uint8Array;
  readonly direction: FrameDirection;
  readonly hardwareTimestamp: number | null;
  readonly hostTimestamp: number;
  readonly normalizedTimestamp: number;
  readonly clockDomain: string;
  readonly timestampQuality: TimestampQuality;
  readonly flags: number;
}

export interface RuntimeFrameBatch {
  readonly schemaVersion: 1;
  readonly streamId: string;
  readonly firstSequence: bigint;
  readonly lastSequence: bigint;
  readonly frameCount: number;
  readonly frames: readonly RuntimeFrame[];
}

