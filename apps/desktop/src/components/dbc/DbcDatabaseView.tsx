import { useVirtualizer } from "@tanstack/react-virtual";
import type { TFunction } from "i18next";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  RuntimeDbcAsset,
  RuntimeDbcDatabase,
  RuntimeDbcMessage,
  RuntimeDbcSignal,
} from "../../runtime/dbc-client";

/**
 * The read-only rendering of one loaded DBC database.
 *
 * This component is presentational: it is handed a database the Runtime already
 * validated and a matching asset, and it renders them. It performs no query, decodes
 * nothing and computes no physical value — `factor`/`offset` are shown as the
 * definition states them, because interpreting a *frame* is a different increment.
 *
 * Every string that arrives from the DBC document — a message name, a signal name, a
 * comment, a unit, a choice label, a node name — is rendered as a React text node, so
 * it is displayed, never executed. There is no `dangerouslySetInnerHTML` anywhere
 * below, and the document has no path to one.
 *
 * The message list is virtualized for the same reason Trace is: a real DBC can carry
 * thousands of messages and the read model promises no upper bound. Only the visible
 * rows exist in the DOM. The signal table is not virtualized — it renders exactly one
 * message's signals, the only message the user is currently looking at.
 */

export interface DbcDatabaseViewProps {
  readonly asset: RuntimeDbcAsset;
  readonly database: RuntimeDbcDatabase;
}

/** Columns of the message list, in render order. */
const MESSAGE_COLUMNS = ["name", "frameId", "addressing", "format", "length"] as const;

/** Columns of the signal definition table, in render order. */
const SIGNAL_COLUMNS = [
  "name",
  "startBit",
  "length",
  "byteOrder",
  "type",
  "factor",
  "offset",
  "minimum",
  "maximum",
  "unit",
  "receivers",
  "multiplex",
  "choices",
  "comment",
] as const;

export function DbcDatabaseView({ asset, database }: DbcDatabaseViewProps) {
  const { t } = useTranslation();
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: database.messages.length,
    estimateSize: () => 26,
    getScrollElement: () => scrollRef.current,
    initialRect: { height: 320, width: 800 },
    overscan: 8,
  });

  const selected = selectedIndex === null ? null : database.messages[selectedIndex] ?? null;

  return (
    <section aria-label={t("dbc.title")} className="dbc-database">
      <header className="dbc-database-header">
        <h3 className="dbc-database-name">{asset.sourceName}</h3>
        <div className="dbc-summary">
          <SummaryItem
            label={t("dbc.database.version")}
            value={database.version ?? t("dbc.value.none")}
          />
          <SummaryItem
            label={t("dbc.database.messageCount")}
            value={String(database.messages.length)}
          />
          <SummaryItem label={t("dbc.database.nodeCount")} value={String(database.nodes.length)} />
          <SummaryItem label={t("dbc.database.sha256")} value={shortDigest(asset.sha256)} />
        </div>
        {database.nodes.length > 0 ? (
          <div aria-label={t("dbc.database.nodeList")} className="dbc-nodes" role="group">
            <span className="dbc-nodes-label">{t("dbc.database.nodes")}</span>
            <ul className="dbc-node-list">
              {database.nodes.map((node) => (
                <li key={node.name}>
                  {node.comment === null ? node.name : `${node.name} — ${node.comment}`}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </header>

      <div className="dbc-database-body">
        <div aria-label={t("dbc.messages.label")} className="dbc-message-list" role="table">
          <div className="dbc-message-row dbc-message-header" role="row">
            {MESSAGE_COLUMNS.map((column) => (
              <div key={column} role="columnheader">
                {t(`dbc.message.column.${column}`)}
              </div>
            ))}
          </div>
          <div className="dbc-message-scroll" ref={scrollRef}>
            <div className="dbc-message-spacer" style={{ height: virtualizer.getTotalSize() }}>
              {virtualizer.getVirtualItems().map((item) => {
                const message = database.messages[item.index];
                if (message === undefined) return null;
                return (
                  <div
                    aria-selected={item.index === selectedIndex}
                    className="dbc-message-row dbc-message-data-row"
                    key={`${message.frameId}:${message.name}`}
                    onClick={() => setSelectedIndex(item.index)}
                    role="row"
                    style={{ transform: `translateY(${item.start}px)` }}
                  >
                    <div role="cell">{message.name}</div>
                    <div role="cell">{formatFrameId(message)}</div>
                    <div role="cell">
                      {message.isExtended ? t("dbc.message.extended") : t("dbc.message.standard")}
                    </div>
                    <div role="cell">
                      {message.isFd ? t("dbc.message.fd") : t("dbc.message.classic")}
                    </div>
                    <div role="cell">{message.length}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="dbc-message-detail">
          {selected === null ? (
            <p className="dbc-message-prompt">{t("dbc.message.selectPrompt")}</p>
          ) : (
            <MessageDetail message={selected} />
          )}
        </div>
      </div>
    </section>
  );
}

/** One labelled summary figure (version, message count, node count, digest). */
function SummaryItem({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div aria-label={label} className="dbc-summary-item" role="group">
      <span className="dbc-summary-label">{label}</span>
      <span className="dbc-summary-value">{value}</span>
    </div>
  );
}

/** The selected message's header facts and its full signal definition table. */
function MessageDetail({ message }: { readonly message: RuntimeDbcMessage }) {
  const { t } = useTranslation();
  const none = t("dbc.value.none");
  const senders = message.senders.length > 0 ? message.senders.join(", ") : none;
  const cycleTime =
    message.cycleTime === null ? none : t("dbc.message.cycleTimeValue", { ms: message.cycleTime });

  return (
    <div className="dbc-message-detail-body">
      <h4 className="dbc-message-detail-name">{message.name}</h4>
      <dl className="dbc-message-facts">
        <div className="dbc-message-fact">
          <dt>{t("dbc.message.senders")}</dt>
          <dd>{senders}</dd>
        </div>
        <div className="dbc-message-fact">
          <dt>{t("dbc.message.cycleTime")}</dt>
          <dd>{cycleTime}</dd>
        </div>
        <div className="dbc-message-fact">
          <dt>{t("dbc.message.signalCount")}</dt>
          <dd>{message.signals.length}</dd>
        </div>
        <div className="dbc-message-fact">
          <dt>{t("dbc.message.comment")}</dt>
          <dd>{message.comment ?? none}</dd>
        </div>
      </dl>

      <div aria-label={t("dbc.signal.label", { name: message.name })} className="dbc-signal-scroll">
        <table className="dbc-signal-table">
          <thead>
            <tr>
              {SIGNAL_COLUMNS.map((column) => (
                <th key={column} scope="col">
                  {t(`dbc.signal.column.${column}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {message.signals.map((signal) => {
              const cells = signalCells(signal, t, none);
              return (
                <tr key={signal.name}>
                  {cells.map((cell, index) => (
                    <td key={SIGNAL_COLUMNS[index] ?? index}>{cell}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Project one signal definition onto the cells of its table row.
 *
 * A `null` bound, unit or comment becomes the shared "no value" marker rather than
 * disappearing, so "the document declared nothing here" stays visible as a fact
 * instead of a silently empty cell. `0` is rendered as `0` — a minimum of zero is a
 * declared bound, not a missing one.
 */
function signalCells(
  signal: RuntimeDbcSignal,
  t: TFunction,
  none: string,
): readonly (string | number)[] {
  return [
    signal.name,
    signal.startBit,
    signal.length,
    byteOrderLabel(signal.byteOrder, t),
    signalTypeLabel(signal, t),
    signal.factor,
    signal.offset,
    signal.minimum ?? none,
    signal.maximum ?? none,
    signal.unit ?? none,
    signal.receivers.length > 0 ? signal.receivers.join(", ") : none,
    multiplexLabel(signal, t, none),
    signal.choices.length > 0
      ? signal.choices.map((choice) => `${choice.value} = ${choice.label}`).join(", ")
      : none,
    signal.comment ?? none,
  ];
}

/** The identifier of a message, in the space its addressing mode selects. */
function formatFrameId(message: RuntimeDbcMessage): string {
  const digits = message.frameId.toString(16).toUpperCase();
  return `0x${digits.padStart(message.isExtended ? 8 : 3, "0")}`;
}

/** A short, non-distracting prefix of the asset digest for the summary line. */
function shortDigest(sha256: string): string {
  return `${sha256.slice(0, 12)}…`;
}

/**
 * Byte order in the reader's vocabulary.
 *
 * The Runtime reports `little_endian` / `big_endian`; those are the wire words, not
 * the engineer's. An unrecognised value is shown verbatim rather than dropped — a
 * future byte order must be visible, not silently blanked.
 */
function byteOrderLabel(byteOrder: string, t: TFunction): string {
  if (byteOrder === "little_endian") return t("dbc.signal.byteOrder.little");
  if (byteOrder === "big_endian") return t("dbc.signal.byteOrder.big");
  return byteOrder;
}

/** Signed / unsigned / float, as the definition states it. */
function signalTypeLabel(signal: RuntimeDbcSignal, t: TFunction): string {
  if (signal.isFloat) return t("dbc.signal.type.float");
  return signal.isSigned ? t("dbc.signal.type.signed") : t("dbc.signal.type.unsigned");
}

/**
 * Multiplexing metadata for one signal.
 *
 * A multiplexer is named; a multiplexed signal names the selector and the values it
 * answers to. Everything else carries no multiplexing, and says so.
 */
function multiplexLabel(signal: RuntimeDbcSignal, t: TFunction, none: string): string {
  if (signal.isMultiplexer) return t("dbc.signal.multiplexer");
  if (signal.multiplexerSignal !== null) {
    const ids =
      signal.multiplexerIds === null || signal.multiplexerIds.length === 0
        ? none
        : signal.multiplexerIds.join(", ");
    return t("dbc.signal.multiplexedBy", { signal: signal.multiplexerSignal, ids });
  }
  return none;
}
