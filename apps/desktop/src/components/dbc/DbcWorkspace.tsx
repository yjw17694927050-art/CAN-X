import { useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  getDbcDatabase,
  listDbcAssets,
} from "../../runtime/dbc-client";
import {
  useWorkspaceSessionSnapshot,
  useWorkspaceSessionStore,
} from "../../workspace/WorkspaceSessionProvider";
import { DbcBindingControl } from "./DbcBindingControl";
import { DbcDatabaseView } from "./DbcDatabaseView";
import { DbcImportControl } from "./DbcImportControl";
import { SignalSelectionControl } from "./SignalSelectionControl";

/**
 * The read-only DBC workspace — a project-scoped view of one project's DBC assets, with
 * the actions that used to be missing: import a document, and bind a channel to one.
 *
 * ```text
 * projectPath (a prop, or the workspace session when none is given)
 *   ↓ listDbcAssets(projectPath)              GET /dbc/assets?project_path=…
 *   asset list  →  selection (the session's, or this component's own)
 *   ↓ getDbcDatabase(projectPath, assetId)    GET /dbc/assets/{id}/database?…
 *   message list  →  message detail  →  signal definitions
 *   + DbcImportControl      import a document into this project
 *   + DbcBindingControl     channelId -> assetId, explicitly
 * ```
 *
 * Six properties are deliberate:
 *
 * * **The project comes from one authority, chosen once.** An explicit `projectPath` prop
 *   wins, because a caller that names a project means it; otherwise the workspace
 *   session's opened project is read. There is no "current project" fallback, no
 *   module-level path and no picker invented for the empty case — with neither input the
 *   workspace renders a real empty state.
 * * **Selection is UI-local — or the workspace's, when there is a workspace.** With a
 *   session above it, the inspected asset is the session's `browsedAssetId`, so two panels
 *   in two React roots inspect the same asset instead of each keeping a private copy.
 *   Without one the component keeps its own state, which is what lets it be rendered
 *   standalone. Either way the selection resets when the project changes.
 * * **Inspecting is not binding.** `browsedAssetId` says which asset this panel is looking
 *   at. It is not an active DBC, not a channel binding and not a decode target; the
 *   binding changes only when a user activates Bind in {@link DbcBindingControl}.
 * * **Runtime state is TanStack Query's.** Server state lives in the shared query cache,
 *   keyed by `(projectPath)` and `(projectPath, assetId)` — no second HTTP cache is built
 *   here, and no database is fetched until its asset is actually selected.
 * * **The project switch resets everything.** A new project path discards the previous
 *   selection before it can reach a paint, so a database is never requested under the
 *   wrong project; the session discards its bindings in the same switch.
 * * **Failures are reported, not dumped.** A structured Runtime failure is shown as a
 *   human message plus its stable code, with a retry. Its `details`, the project path,
 *   the request and the raw body stay where they belong — out of the UI.
 */

export interface DbcWorkspaceProps {
  /**
   * The CAN-X project whose DBC assets are inspected.
   *
   * Omitted in production, where the workspace session's opened project supplies it. Given
   * explicitly, it wins — which is how this panel is rendered standalone in a test without
   * a workspace above it.
   */
  readonly projectPath?: string | null;
}

export function DbcWorkspace({ projectPath }: DbcWorkspaceProps = {}) {
  const { t } = useTranslation();
  const sessionStore = useWorkspaceSessionStore();
  const session = useWorkspaceSessionSnapshot(sessionStore);
  const effectivePath =
    projectPath !== undefined ? projectPath : (session.openedProject?.projectPath ?? null);

  const [localAssetId, setLocalAssetId] = useState<string | null>(null);
  const [selectionProject, setSelectionProject] = useState(effectivePath);

  // A project switch invalidates any selection made against the old project. Adjusting
  // during render (rather than in an effect) keeps the stale selection from ever
  // reaching a paint, so a database is never requested under the wrong project.
  if (selectionProject !== effectivePath) {
    setSelectionProject(effectivePath);
    setLocalAssetId(null);
  }

  // With a session above it the inspection is the workspace's, so a sibling panel in
  // another React root sees the same asset. Without one, this component owns it.
  const selectedAssetId = sessionStore === null ? localAssetId : session.browsedAssetId;
  const inspectAsset = (assetId: string | null): void => {
    if (sessionStore === null) setLocalAssetId(assetId);
    else sessionStore.browseAsset(assetId);
  };

  const assets = useQuery({
    enabled: effectivePath !== null,
    queryFn: () => listDbcAssets(requireProjectPath(effectivePath)),
    queryKey: ["dbc", "assets", effectivePath],
  });

  const database = useQuery({
    enabled: effectivePath !== null && selectedAssetId !== null,
    queryFn: () =>
      getDbcDatabase(requireProjectPath(effectivePath), requireAssetId(selectedAssetId)),
    queryKey: ["dbc", "database", effectivePath, selectedAssetId],
  });

  if (effectivePath === null) {
    return (
      <section aria-label={t("dbc.title")} className="dbc-workspace dbc-notice">
        <p className="dbc-notice-title">{t("dbc.noProject.title")}</p>
        <p className="dbc-notice-hint">{t("dbc.noProject.hint")}</p>
      </section>
    );
  }

  if (assets.isError) {
    return <DbcFailure error={assets.error} onRetry={() => void assets.refetch()} />;
  }

  const collection = assets.data;
  if (collection === undefined) {
    return (
      <section aria-label={t("dbc.title")} className="dbc-workspace dbc-notice">
        <p>{t("dbc.assets.loading")}</p>
      </section>
    );
  }

  if (collection.length === 0) {
    return (
      <section aria-label={t("dbc.title")} className="dbc-workspace dbc-notice">
        <DbcImportControl projectPath={effectivePath} />
        <p>{t("dbc.assets.empty")}</p>
      </section>
    );
  }

  const selectedAsset = collection.find((asset) => asset.assetId === selectedAssetId) ?? null;
  const databaseData = database.data;

  return (
    <section aria-label={t("dbc.title")} className="dbc-workspace">
      <DbcImportControl projectPath={effectivePath} />

      <div aria-label={t("dbc.assets.label")} className="dbc-asset-list" role="listbox">
        {collection.map((asset) => (
          <button
            aria-selected={asset.assetId === selectedAssetId}
            className="dbc-asset"
            key={asset.assetId}
            onClick={() => inspectAsset(asset.assetId)}
            role="option"
            type="button"
          >
            <span className="dbc-asset-name">{asset.sourceName}</span>
            <span className="dbc-asset-meta">{t("dbc.assets.size", { bytes: asset.sizeBytes })}</span>
            <span className="dbc-asset-meta">{t("dbc.assets.encoding", { encoding: asset.encoding })}</span>
            <span className="dbc-asset-meta">{t("dbc.assets.importedAt", { at: asset.importedAt })}</span>
          </button>
        ))}
      </div>

      <div className="dbc-detail">
        <DbcBindingControl assets={collection} />
        {selectedAsset === null ? (
          <p className="dbc-select-prompt">{t("dbc.database.selectPrompt")}</p>
        ) : database.isError ? (
          <DbcFailure error={database.error} onRetry={() => void database.refetch()} />
        ) : databaseData === undefined ? (
          <p className="dbc-notice">{t("dbc.database.loading")}</p>
        ) : (
          <>
            <DbcDatabaseView
              asset={selectedAsset}
              database={databaseData}
              key={`${effectivePath}:${selectedAsset.assetId}`}
            />
            {/* Below the document it selects from: the user reads the signals first and picks
                one to plot, rather than choosing a name with nothing on screen behind it. */}
            <SignalSelectionControl assetId={selectedAsset.assetId} database={databaseData} />
          </>
        )}
      </div>
    </section>
  );
}

/**
 * A readable failure notice with a retry, and nothing else.
 *
 * The Runtime's structured diagnosis is classified into one of three shape families —
 * api / transport / contract — and the Runtime's own stable code is surfaced when
 * there is one, because `dbc.asset_integrity_failed` and `project.not_found` are
 * different facts a user may act on differently. Its `details`, the request, the
 * project path and the raw body are deliberately absent.
 */
function DbcFailure({
  error,
  onRetry,
}: {
  readonly error: unknown;
  readonly onRetry: () => void;
}) {
  const { t } = useTranslation();
  const failure = describeFailure(error, t);

  return (
    <section className="dbc-failure" role="alert">
      <p className="dbc-failure-message">{failure.message}</p>
      {failure.code === null ? null : (
        <p className="dbc-failure-code">{t("dbc.error.code", { code: failure.code })}</p>
      )}
      <button className="dbc-retry" onClick={onRetry} type="button">
        {t("dbc.retry")}
      </button>
    </section>
  );
}

/** Classify a thrown value into a safe message and, when present, a stable code. */
function describeFailure(error: unknown, t: TFunction): { message: string; code: string | null } {
  if (error instanceof RuntimeDbcApiError) {
    return { message: t("dbc.error.api"), code: error.code };
  }
  if (error instanceof RuntimeDbcTransportError) {
    return { message: t("dbc.error.transport"), code: null };
  }
  if (error instanceof RuntimeDbcContractError) {
    return { message: t("dbc.error.contract"), code: null };
  }
  return { message: t("dbc.error.unknown"), code: null };
}

/**
 * Narrow a project path the query layer already guaranteed.
 *
 * The queries are `enabled` only when their inputs are present, so these never run a
 * request with a missing argument; they exist so the type is narrowed at the seam
 * rather than asserted with a cast, and so a future change that breaks the guard fails
 * loudly instead of querying the wrong project.
 */
function requireProjectPath(projectPath: string | null): string {
  if (projectPath === null) {
    throw new Error("The DBC workspace queried the Runtime without a project path.");
  }
  return projectPath;
}

function requireAssetId(assetId: string | null): string {
  if (assetId === null) {
    throw new Error("The DBC workspace queried the Runtime without a selected asset.");
  }
  return assetId;
}
