import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { DecodedFrameEntry, DecodedSignal } from "../../workspace/decoded-realtime";

export type TraceMode = "follow" | "freeze";

/**
 * One Trace line.
 *
 * A row is a raw frame paired with whatever the Runtime has said about it so far.
 * `decoded` is `null` until a decode response names that frame's sequence — an
 * unresolved row is a normal state, shown as the em-dash placeholder, not an error.
 */
export interface TraceRow {
  readonly frame: RuntimeFrame;
  readonly decoded: DecodedFrameEntry | null;
}

export interface TracePanelProps {
  readonly rows: readonly TraceRow[];
  readonly mode: TraceMode;
  readonly onModeChange: (mode: TraceMode) => void;
}

const columns = [
  "timestamp",
  "channel",
  "id",
  "dlc",
  "data",
  "direction",
  "message",
  "signals",
] as const;

/**
 * The column tracks live here rather than in `styles.css` so the panel keeps
 * owning its own shape; the six raw columns keep their original widths and only
 * the two decoded columns are appended.
 */
const COLUMN_TRACKS =
  "120px 72px 92px 52px minmax(220px, 1fr) 72px minmax(140px, 200px) minmax(220px, 1fr)";

export function TracePanel({ rows, mode, onModeChange }: TracePanelProps) {
  const { t } = useTranslation();
  const none = t("trace.value.none");
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    estimateSize: () => 26,
    getScrollElement: () => scrollRef.current,
    initialRect: { height: 320, width: 800 },
    overscan: 8,
  });

  useEffect(() => {
    if (mode === "follow" && rows.length > 0) {
      virtualizer.scrollToIndex(rows.length - 1, { align: "end" });
    }
  }, [rows.length, mode, virtualizer]);

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
        <div
          className="trace-row trace-header"
          role="row"
          style={{ gridTemplateColumns: COLUMN_TRACKS }}
        >
          {columns.map((column) => (
            <div key={column} role="columnheader">
              {t(`trace.column.${column}`)}
            </div>
          ))}
        </div>
        <div className="trace-scroll" ref={scrollRef}>
          <div className="trace-spacer" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((item) => {
              const row = rows[item.index];
              if (row === undefined) return null;
              const { frame } = row;
              return (
                <div
                  className="trace-row trace-data-row"
                  data-trace-row
                  key={frame.sequence.toString()}
                  role="row"
                  style={{
                    gridTemplateColumns: COLUMN_TRACKS,
                    transform: `translateY(${item.start}px)`,
                  }}
                >
                  <div role="cell">{frame.normalizedTimestamp.toFixed(6)}</div>
                  <div role="cell">{frame.channelId}</div>
                  <div role="cell">{formatId(frame)}</div>
                  <div role="cell">{frame.dlc}</div>
                  <div role="cell">{formatData(frame.data)}</div>
                  <div role="cell">{frame.direction.toUpperCase()}</div>
                  <div role="cell">{describeMessage(row.decoded, none)}</div>
                  <div role="cell">{describeSignals(row.decoded, none)}</div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * The message name, or the failure's stable code.
 *
 * A failure never leaks a path, a traceback, a raw response or a details dump:
 * the code is the whole message, and it is already a stable identifier.
 */
function describeMessage(decoded: DecodedFrameEntry | null, none: string): string {
  if (decoded === null) return none;
  const { outcome } = decoded;
  return outcome.status === "decoded" ? outcome.messageName : outcome.code;
}

/**
 * The compact engineering form of every decoded signal.
 *
 * A frame that failed — or that no decode response has answered yet — has no
 * signal values to show, so the placeholder stands in for the whole column.
 */
function describeSignals(decoded: DecodedFrameEntry | null, none: string): string {
  if (decoded === null) return none;
  const { outcome } = decoded;
  if (outcome.status !== "decoded" || outcome.signals.length === 0) return none;
  return outcome.signals.map(formatSignal).join(", ");
}

/**
 * `EngineSpeed=1234 rpm`, `Gear=3`, `Gear=3 (Drive)`.
 *
 * The physical value is always printed; the `VAL_` label annotates it and the
 * unit follows the number. The raw bus value never appears here — the Runtime
 * already computed `raw * factor + offset` and that product is the answer.
 */
function formatSignal(signal: DecodedSignal): string {
  const unit = signal.unit === null || signal.unit === "" ? "" : ` ${signal.unit}`;
  const label = signal.choiceLabel === null ? "" : ` (${signal.choiceLabel})`;
  return `${signal.name}=${formatNumber(signal.physicalValue)}${unit}${label}`;
}

/** Integers print exactly; scaled values lose the binary-float tail. */
function formatNumber(value: number): string {
  if (Number.isInteger(value)) return value.toString();
  return Number.parseFloat(value.toFixed(6)).toString();
}

function formatId(frame: RuntimeFrame): string {
  return frame.arbitrationId.toString(16).toUpperCase().padStart(frame.isExtended ? 8 : 3, "0");
}

function formatData(data: Uint8Array): string {
  return Array.from(data, (value) => value.toString(16).toUpperCase().padStart(2, "0")).join(" ");
}
