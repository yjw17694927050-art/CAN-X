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
import { DbcDatabaseView } from "./DbcDatabaseView";

/**
 * The read-only DBC workspace — a project-scoped view of one project's DBC assets.
 *
 * ```text
 * projectPath (a prop, never a global)
 *   ↓ listDbcAssets(projectPath)              GET /dbc/assets?project_path=…
 *   asset list  →  local selection
 *   ↓ getDbcDatabase(projectPath, assetId)    GET /dbc/assets/{id}/database?…
 *   message list  →  message detail  →  signal definitions
 * ```
 *
 * Four properties are deliberate:
 *
 * * **The project is a prop.** Nothing here reads a store, a module-level path or a
 *   "current project". When no project is open the caller passes `null` and the
 *   workspace shows a real empty state instead of guessing a path or inventing a
 *   picker. That is what lets a future Project Workspace supply the path without this
 *   component having to learn how projects are chosen.
 * * **Selection is UI-local.** `selectedAssetId` says which asset *this panel* is
 *   looking at. It is not an active DBC, not a channel binding and not a decode
 *   target; it is never lifted into a module-level store, and it resets the moment the
 *   project changes so one project's selection cannot leak into another's.
 * * **Runtime state is TanStack Query's.** Server state lives in the shared query
 *   cache keyed by `(projectPath)` and `(projectPath, assetId)` — no second HTTP cache
 *   is built here, and no database is fetched until its asset is actually selected.
 * * **Failures are reported, not dumped.** A structured Runtime failure is shown as a
 *   human message plus its stable code, with a retry. Its `details`, the project path,
 *   the request and the raw body stay where they belong — out of the UI.
 */

export interface DbcWorkspaceProps {
  /**
   * The CAN-X project whose DBC assets are inspected, or `null` when no project is
   * open. Required and explicit: there is no implicit "current" project.
   */
  readonly projectPath: string | null;
}

export function DbcWorkspace({ projectPath }: DbcWorkspaceProps) {
  const { t } = useTranslation();
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [selectionProject, setSelectionProject] = useState(projectPath);

  // A project switch invalidates any selection made against the old project. Adjusting
  // during render (rather than in an effect) keeps the stale selection from ever
  // reaching a paint, so a database is never requested under the wrong project.
  if (selectionProject !== projectPath) {
    setSelectionProject(projectPath);
    setSelectedAssetId(null);
  }

  const assets = useQuery({
    enabled: projectPath !== null,
    queryFn: () => listDbcAssets(requireProjectPath(projectPath)),
    queryKey: ["dbc", "assets", projectPath],
  });

  const database = useQuery({
    enabled: projectPath !== null && selectedAssetId !== null,
    queryFn: () =>
      getDbcDatabase(requireProjectPath(projectPath), requireAssetId(selectedAssetId)),
    queryKey: ["dbc", "database", projectPath, selectedAssetId],
  });

  if (projectPath === null) {
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
        <p>{t("dbc.assets.empty")}</p>
      </section>
    );
  }

  const selectedAsset = collection.find((asset) => asset.assetId === selectedAssetId) ?? null;
  const databaseData = database.data;

  return (
    <section aria-label={t("dbc.title")} className="dbc-workspace">
      <div aria-label={t("dbc.assets.label")} className="dbc-asset-list" role="listbox">
        {collection.map((asset) => (
          <button
            aria-selected={asset.assetId === selectedAssetId}
            className="dbc-asset"
            key={asset.assetId}
            onClick={() => setSelectedAssetId(asset.assetId)}
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
        {selectedAsset === null ? (
          <p className="dbc-select-prompt">{t("dbc.database.selectPrompt")}</p>
        ) : database.isError ? (
          <DbcFailure error={database.error} onRetry={() => void database.refetch()} />
        ) : databaseData === undefined ? (
          <p className="dbc-notice">{t("dbc.database.loading")}</p>
        ) : (
          <DbcDatabaseView
            asset={selectedAsset}
            database={databaseData}
            key={`${projectPath}:${selectedAsset.assetId}`}
          />
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
