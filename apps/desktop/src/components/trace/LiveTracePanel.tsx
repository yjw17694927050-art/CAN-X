import { useCallback, useMemo, useState, useSyncExternalStore } from "react";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import { useRealtimeStream, type RealtimeStreamStore } from "../../runtime/realtime-stream";
import {
  EMPTY_DECODED_REALTIME,
  type DecodedRealtimeState,
  type DecodedRealtimeStore,
} from "../../workspace/decoded-realtime";
import { TracePanel, type TraceMode, type TraceRow } from "./TracePanel";

const NO_FRAMES: readonly RuntimeFrame[] = [];

/** A store that is not injected reads the shared empty state and never notifies. */
const subscribeToNothing = (): (() => void) => () => undefined;
const readEmptyDecoded = (): DecodedRealtimeState => EMPTY_DECODED_REALTIME;

export interface LiveTracePanelProps {
  readonly store?: RealtimeStreamStore;
  /**
   * The workspace's decoded outcomes, injected by the shell.
   *
   * Trace never fetches and never owns decode state: it reads the same store the
   * Plot panel reads, so both project one set of outcomes. Left out, every row
   * simply stays raw.
   */
  readonly decoded?: DecodedRealtimeStore;
}

/**
 * Trace is a projection of two shared stores: the realtime frames and the decode
 * outcomes. Freeze is a Trace-only cursor — it captures the composed rows and
 * stops applying both snapshots without touching the shared socket, Worker,
 * stores, or the Plot subscription.
 */
export function LiveTracePanel({ store, decoded }: LiveTracePanelProps = {}) {
  const { snapshot } = useRealtimeStream(store);
  const decodedState = useSyncExternalStore(
    decoded?.subscribe ?? subscribeToNothing,
    decoded?.getSnapshot ?? readEmptyDecoded,
    decoded?.getSnapshot ?? readEmptyDecoded,
  );
  const [mode, setMode] = useState<TraceMode>("follow");
  const [frozenRows, setFrozenRows] = useState<readonly TraceRow[] | null>(null);

  const frames = snapshot?.frames ?? NO_FRAMES;
  const liveRows = useMemo(
    () =>
      frames.map((frame): TraceRow => ({
        frame,
        decoded: decodedState.entries.get(frame.sequence) ?? null,
      })),
    [decodedState.entries, frames],
  );
  const rows = mode === "freeze" && frozenRows !== null ? frozenRows : liveRows;

  const changeMode = useCallback(
    (nextMode: TraceMode) => {
      // Freeze captures the composed rows, not just the frames. A decode response
      // that lands after the freeze must not rewrite a frozen line, so the whole
      // projection is snapshotted — and only `follow` releases it.
      setFrozenRows(nextMode === "freeze" ? liveRows : null);
      setMode(nextMode);
    },
    [liveRows],
  );

  return <TracePanel mode={mode} onModeChange={changeMode} rows={rows} />;
}
