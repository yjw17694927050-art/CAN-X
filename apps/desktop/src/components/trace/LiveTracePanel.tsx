import { useCallback, useState } from "react";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import { useRealtimeStream, type RealtimeStreamStore } from "../../runtime/realtime-stream";
import { TracePanel, type TraceMode } from "./TracePanel";

const NO_FRAMES: readonly RuntimeFrame[] = [];

export interface LiveTracePanelProps {
  readonly store?: RealtimeStreamStore;
}

/**
 * Trace is a projection of the shared realtime store. Freeze is a Trace-only
 * cursor: it captures the current viewport and stops applying snapshots without
 * touching the shared socket, Worker, store, or the Plot subscription.
 */
export function LiveTracePanel({ store }: LiveTracePanelProps = {}) {
  const { snapshot } = useRealtimeStream(store);
  const [mode, setMode] = useState<TraceMode>("follow");
  const [frozenFrames, setFrozenFrames] = useState<readonly RuntimeFrame[] | null>(null);
  const liveFrames = snapshot?.frames ?? NO_FRAMES;
  const frames = mode === "freeze" && frozenFrames !== null ? frozenFrames : liveFrames;

  const changeMode = useCallback(
    (nextMode: TraceMode) => {
      setFrozenFrames(nextMode === "freeze" ? liveFrames : null);
      setMode(nextMode);
    },
    [liveFrames],
  );

  return <TracePanel frames={frames} mode={mode} onModeChange={changeMode} />;
}
