import { useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  RuntimeDbcDatabase,
  RuntimeDbcMessage,
  RuntimeDbcSignal,
} from "../../runtime/dbc-client";
import type { DecodeBindings } from "../../workspace/session";
import {
  useWorkspaceSessionSnapshot,
  useWorkspaceSessionStore,
} from "../../workspace/WorkspaceSessionProvider";

/**
 * The signal-selection control: choose a channel, then a message and a signal, and plot it.
 *
 * ```text
 * props: assetId                      which asset the user is inspecting
 * props: database                     that asset's messages and their signal definitions
 * WorkspaceSessionStore.dbcBindings   channelId -> assetId       the candidate channels
 *   ↓ channelsBoundTo(bindings, assetId)      only the channels bound to THIS asset
 * user picks (channel, message, signal)
 *   ↓ selectSignal({ channelId, assetId, messageName, signalName, unit })
 * WorkspaceSessionStore.selectedSignal        what LivePlotPanel draws
 * ```
 *
 * Six properties are deliberate:
 *
 * * **The channel is chosen, not assumed.** One asset may be bound to `can0` and `can1`
 *   at the same time, and both may carry a message declaring a signal of the same name.
 *   With one bound channel the control may stand on it; with two or more it will not
 *   pick for the user — the selection stays empty until a channel is named, and Plot
 *   stays disabled. Plotting "whichever channel" would draw a curve that means two
 *   different things.
 * * **Candidates come from the binding map, and nothing else.** A channel appears here
 *   only while `dbcBindings` maps it to *this* asset. There is no channel registry and
 *   no observed-stream list: a channel bound to another asset is simply not a candidate,
 *   because decoding this asset's definitions against it is not a thing the Runtime
 *   would do.
 * * **The asset and its database are props.** Resolving which messages and signals the
 *   user may choose from is the DBC workspace's job — it already fetched and validated
 *   that database — and a control that fetched its own would be a second authority on
 *   the same document.
 * * **Plot is offered only when it can succeed.** It needs a live session to write into,
 *   an inspected asset, and a channel bound to that asset. Where any is missing the
 *   action is disabled while the *reason* is shown (`dbc.selection.noChannel`), rather
 *   than the control disappearing.
 * * **Writing is guarded, not merely disabled.** The selection is re-checked against the
 *   binding map at the moment it is written, so a binding removed between the render and
 *   the click cannot produce a selection that names a channel which decodes nothing —
 *   and the session's own refusal is never reached as an exception.
 * * **Text is frozen.** Every string is an existing `dbc.*` / `dbc.selection.*` key, so
 *   the control ships in en and zh-CN with no new copy surface. Signal and message names
 *   are the document's own words, rendered as React text nodes.
 */

export interface SignalSelectionControlProps {
  /**
   * The asset whose signals this control offers, or `null` when nothing is being
   * inspected. Required and explicit: a control that guessed which asset the user means
   * would plot a signal of a document nobody named.
   */
  readonly assetId: string | null;
  /** The loaded database of {@link SignalSelectionControlProps.assetId}. */
  readonly database: RuntimeDbcDatabase;
}

/** One shared empty candidate list, so an unbound asset allocates nothing per render. */
const NO_CHANNELS: readonly string[] = [];

/** What the user has picked so far, and the asset it was picked against. */
interface Choice {
  readonly assetId: string | null;
  readonly channelId: string | null;
  readonly messageName: string | null;
}

export function SignalSelectionControl({ assetId, database }: SignalSelectionControlProps) {
  const { t } = useTranslation();
  const session = useWorkspaceSessionStore();
  const { dbcBindings } = useWorkspaceSessionSnapshot(session);

  const [choice, setChoice] = useState<Choice>({ assetId, channelId: null, messageName: null });
  // A different asset is a different document: a channel id or a message name chosen
  // against the previous one says nothing about this one. Adjusting during render (rather
  // than in an effect) keeps a stale choice from ever reaching a paint.
  if (choice.assetId !== assetId) {
    setChoice({ assetId, channelId: null, messageName: null });
  }

  const channels = assetId === null ? NO_CHANNELS : channelsBoundTo(dbcBindings, assetId);
  // A choice survives only while it is still a candidate: a channel unbound between two
  // renders stops being selectable immediately, rather than remaining chosen and failing
  // at the moment Plot is pressed.
  const chosenChannel = channels.includes(choice.channelId ?? "") ? choice.channelId : null;
  // A lone bound channel is not a decision the user has to make twice: it is the only
  // channel this asset can be decoded on, and the select below still shows which one.
  const soleChannel = channels.length === 1 ? (channels[0] ?? null) : null;
  const selectedChannel = chosenChannel ?? soleChannel;
  const canPlot = session !== null && assetId !== null && selectedChannel !== null;

  const activeMessage =
    database.messages.find((message) => message.name === choice.messageName) ??
    database.messages[0] ??
    null;

  const plot = (message: RuntimeDbcMessage, signal: RuntimeDbcSignal): void => {
    // Guarded, not merely disabled: the whole selection is read here and here only, and
    // the binding it depends on is re-checked as it is read, so no other path in this
    // component can write a selection its channel does not currently support.
    if (session === null || assetId === null || selectedChannel === null) return;
    if (session.assetForChannel(selectedChannel) !== assetId) return;
    session.selectSignal({
      assetId,
      channelId: selectedChannel,
      messageName: message.name,
      signalName: signal.name,
      unit: signal.unit,
    });
  };

  return (
    <section aria-label={t("dbc.signal.plot")} className="dbc-selection">
      <div className="dbc-selection-channel">
        <span className="dbc-selection-label">{t("dbc.selection.channel")}</span>
        <select
          aria-label={t("dbc.selection.channel")}
          className="dbc-selection-channel-select"
          disabled={channels.length === 0}
          onChange={(event) => {
            const channelId = event.target.value === "" ? null : event.target.value;
            setChoice((current) => ({ ...current, channelId }));
          }}
          value={selectedChannel ?? ""}
        >
          {channels.length > 1 ? (
            <option value="">{t("dbc.selection.chooseChannel")}</option>
          ) : null}
          {channels.map((channelId) => (
            <option key={channelId} value={channelId}>
              {channelId}
            </option>
          ))}
        </select>
        {channels.length === 0 ? (
          <p className="dbc-selection-no-channel">{t("dbc.selection.noChannel")}</p>
        ) : null}
      </div>

      {activeMessage === null ? null : (
        <div className="dbc-selection-signals">
          <div className="dbc-selection-message">
            <span className="dbc-selection-label">{t("dbc.messages.label")}</span>
            <select
              aria-label={t("dbc.messages.label")}
              className="dbc-selection-message-select"
              onChange={(event) => {
                const messageName = event.target.value === "" ? null : event.target.value;
                setChoice((current) => ({ ...current, messageName }));
              }}
              value={activeMessage.name}
            >
              {database.messages.map((message) => (
                <option key={message.name} value={message.name}>
                  {message.name}
                </option>
              ))}
            </select>
          </div>

          <table
            aria-label={t("dbc.signal.label", { name: activeMessage.name })}
            className="dbc-selection-signal-table"
          >
            <thead>
              <tr>
                <th scope="col">{t("dbc.signal.column.name")}</th>
                <th scope="col">{t("dbc.signal.column.unit")}</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {activeMessage.signals.map((signal) => (
                <tr data-signal-name={signal.name} key={signal.name}>
                  <td>{signal.name}</td>
                  <td>{signal.unit ?? t("dbc.value.none")}</td>
                  <td>
                    <button
                      disabled={!canPlot}
                      onClick={() => {
                        plot(activeMessage, signal);
                      }}
                      type="button"
                    >
                      {t("dbc.signal.plot")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/**
 * The channels currently bound to one asset, in a deterministic order.
 *
 * Derived from the binding map rather than from a channel list: the map is the *decision*
 * that a channel decodes this document, and a channel that has not been bound to it has
 * nothing to decode with. Order is plain code-unit ordering, so the same binding set
 * renders the same way on every host and every locale.
 */
function channelsBoundTo(bindings: DecodeBindings, assetId: string): readonly string[] {
  const channels: string[] = [];
  for (const [channelId, boundAssetId] of bindings) {
    if (boundAssetId === assetId) channels.push(channelId);
  }
  return channels.sort(compareCodeUnits);
}

/** Plain code-unit ordering: deterministic on every host, every locale, every run. */
function compareCodeUnits(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}
