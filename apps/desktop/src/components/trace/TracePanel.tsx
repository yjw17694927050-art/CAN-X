import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import type { RuntimeFrame } from "../../runtime/frame-schema";

export type TraceMode = "follow" | "freeze";

export interface TracePanelProps {
  readonly frames: readonly RuntimeFrame[];
  readonly mode: TraceMode;
  readonly onModeChange: (mode: TraceMode) => void;
}

const columns = ["timestamp", "channel", "id", "dlc", "data", "direction"] as const;

export function TracePanel({ frames, mode, onModeChange }: TracePanelProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: frames.length,
    estimateSize: () => 26,
    getScrollElement: () => scrollRef.current,
    initialRect: { height: 320, width: 800 },
    overscan: 8,
  });

  useEffect(() => {
    if (mode === "follow" && frames.length > 0) {
      virtualizer.scrollToIndex(frames.length - 1, { align: "end" });
    }
  }, [frames.length, mode, virtualizer]);

  return (
    <section aria-label={t("trace.title")} className="trace-panel">
      <div className="trace-toolbar">
        <button disabled={mode === "follow"} onClick={() => onModeChange("follow")} type="button">
          {t("trace.follow")}
        </button>
        <button disabled={mode === "freeze"} onClick={() => onModeChange("freeze")} type="button">
          {t("trace.freeze")}
        </button>
      </div>
      <div className="trace-table" role="table">
        <div className="trace-row trace-header" role="row">
          {columns.map((column) => (
            <div key={column} role="columnheader">
              {t(`trace.column.${column}`)}
            </div>
          ))}
        </div>
        <div className="trace-scroll" ref={scrollRef}>
          <div className="trace-spacer" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((item) => {
              const frame = frames[item.index];
              if (frame === undefined) return null;
              return (
                <div
                  className="trace-row trace-data-row"
                  data-trace-row
                  key={frame.sequence.toString()}
                  role="row"
                  style={{ transform: `translateY(${item.start}px)` }}
                >
                  <div role="cell">{frame.normalizedTimestamp.toFixed(6)}</div>
                  <div role="cell">{frame.channelId}</div>
                  <div role="cell">{formatId(frame)}</div>
                  <div role="cell">{frame.dlc}</div>
                  <div role="cell">{formatData(frame.data)}</div>
                  <div role="cell">{frame.direction.toUpperCase()}</div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

function formatId(frame: RuntimeFrame): string {
  return frame.arbitrationId.toString(16).toUpperCase().padStart(frame.isExtended ? 8 : 3, "0");
}

function formatData(data: Uint8Array): string {
  return Array.from(data, (value) => value.toString(16).toUpperCase().padStart(2, "0")).join(" ");
}
