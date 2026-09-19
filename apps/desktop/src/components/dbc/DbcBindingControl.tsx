/**
 * The decode-binding control: which project-owned DBC asset decodes which CAN channel.
 *
 * ```text
 * RealtimeStreamStore.snapshot.frames[].channelId     the observed channel list (read-only)
 *   ↑ useRealtimeStream(store)                        subscribed, never constructed here
 * WorkspaceSessionStore
 *   openedProject    must be open, or there is nothing a binding could belong to
 *   dbcBindings      the one thing Bind writes and Unbind removes
 *   browsedAssetId   what the user is inspecting — never a decode target by itself
 * ```
 *
 * Five properties are deliberate:
 *
 * * **The channel list is observed, not owned.** Rows come from `RuntimeFrame.channelId`
 *   values the shared realtime stream has actually carried (see `observed-channels.ts`).
 *   This component holds no channel registry, opens no device and configures nothing; a
 *   channel it has never seen does not exist for it, and before the first frame the list
 *   is empty with a real empty state.
 * * **One pipeline, injected.** The store is a prop, defaulting to the app's one shared
 *   `realtimeStream`; this component subscribes and never constructs a Worker or a
 *   WebSocket. Receiving the store as a prop is also what lets a test observe channels
 *   without a socket, and — because it subscribes to the *store* rather than owning one —
 *   a second subscriber costs no second connection.
 * * **Browsing is not binding.** Only the explicit Bind action calls `bindChannel`, and
 *   only Unbind calls `unbindChannel`. Nothing in this file reads `browsedAssetId` into a
 *   binding: the inspected asset is consulted at the moment Bind is activated and never
 *   before, so inspecting `body.dbc` cannot silently re-target `can0`'s decoder.
 * * **Bind is offered only when it can succeed.** A binding needs an open project (the
 *   session refuses one without it), a live session to write into, and an asset that is
 *   actually being inspected *and* present in the collection — because the inspected
 *   asset's id is the thing that would be written. Where any of those is missing the
 *   action is disabled rather than hidden or allowed to throw.
 * * **Text is frozen.** Every string is a `dbc.binding.*` key, so the control ships in
 *   en and zh-CN without a new copy surface. No asset name is invented: a binding to an
 *   asset the collection does not hold is labelled by its `unlistedAsset` state, not by
 *   a guessed name.
 */

import { useTranslation } from "react-i18next";

import type { RuntimeDbcAsset } from "../../runtime/dbc-client";
import { useRealtimeStream, type RealtimeStreamStore } from "../../runtime/realtime-stream";
import {
  useWorkspaceSessionSnapshot,
  useWorkspaceSessionStore,
} from "../../workspace/WorkspaceSessionProvider";
import { observedChannelBindings, type ObservedChannelBinding } from "./observed-channels";

export interface DbcBindingControlProps {
  /**
   * The open project's DBC assets, as its own asset list read them. Required and
   * explicit: resolving a bound `assetId` to a `sourceName` is the only way a row can
   * name what decodes a channel, and a control that guessed at an asset list would be a
   * second project authority.
   */
  readonly assets: readonly RuntimeDbcAsset[];
  /**
   * The shared realtime store to observe. Defaults to the app's one `realtimeStream`:
   * this control is a second *subscriber*, never a second pipeline.
   */
  readonly store?: RealtimeStreamStore;
}

export function DbcBindingControl({ assets, store }: DbcBindingControlProps) {
  const { t } = useTranslation();
  const { snapshot: viewport } = useRealtimeStream(store);
  const session = useWorkspaceSessionStore();
  const { browsedAssetId, dbcBindings, openedProject } = useWorkspaceSessionSnapshot(session);

  const inspectedAsset =
    browsedAssetId === null
      ? null
      : (assets.find((asset) => asset.assetId === browsedAssetId) ?? null);
  const canBind = session !== null && openedProject !== null && inspectedAsset !== null;
  const rows = observedChannelBindings({ assets, bindings: dbcBindings, snapshot: viewport });

  return (
    <section aria-label={t("dbc.binding.title")} className="dbc-binding">
      <p className="dbc-binding-hint">{t("dbc.binding.hint")}</p>
      <p className="dbc-binding-inspection">
        {inspectedAsset === null
          ? t("dbc.binding.nothingBrowsed")
          : t("dbc.binding.browsing", { asset: inspectedAsset.sourceName })}
      </p>
      {rows.length === 0 ? (
        <p className="dbc-binding-empty">{t("dbc.binding.noChannels")}</p>
      ) : (
        <table aria-label={t("dbc.binding.channelsLabel")} className="dbc-binding-channels">
          <thead>
            <tr>
              <th scope="col">{t("dbc.binding.column.channel")}</th>
              <th scope="col">{t("dbc.binding.column.asset")}</th>
              <th scope="col">{t("dbc.binding.column.state")}</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <BindingRow
                canBind={canBind}
                key={row.channelId}
                onBind={() => {
                  // Guarded, not merely disabled: the inspected asset is read here and
                  // here only, so no other path in this component can write a binding.
                  if (!canBind || session === null || inspectedAsset === null) return;
                  session.bindChannel(row.channelId, inspectedAsset.assetId);
                }}
                onUnbind={() => session?.unbindChannel(row.channelId)}
                row={row}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function BindingRow({
  canBind,
  onBind,
  onUnbind,
  row,
}: {
  readonly canBind: boolean;
  readonly onBind: () => void;
  readonly onUnbind: () => void;
  readonly row: ObservedChannelBinding;
}) {
  const { t } = useTranslation();

  return (
    <tr
      data-asset-id={row.assetId ?? undefined}
      data-binding-state={row.state}
      data-channel-id={row.channelId}
    >
      <td>{row.channelId}</td>
      {/*
        An unbound row has no asset to name, and the state column already says so — an
        empty cell is the honest answer. A row bound to an asset the collection no longer
        holds is neither: it keeps its place and says so, so it can still be unbound.
      */}
      <td>
        {row.state === "bound"
          ? row.sourceName
          : row.state === "unlisted"
            ? t("dbc.binding.unlistedAsset")
            : null}
      </td>
      <td>{row.state === "unbound" ? t("dbc.binding.unbound") : t("dbc.binding.bound")}</td>
      <td>
        {row.state === "unbound" ? (
          <button disabled={!canBind} onClick={onBind} type="button">
            {t("dbc.binding.bind")}
          </button>
        ) : (
          <button onClick={onUnbind} type="button">
            {t("dbc.binding.unbind")}
          </button>
        )}
      </td>
    </tr>
  );
}
