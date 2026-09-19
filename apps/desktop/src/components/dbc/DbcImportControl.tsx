/**
 * The DBC import control — the production caller for the DBC import boundary.
 *
 * Two boundaries and one orchestration already exist, and this component is a **caller**
 * of the third, never a re-implementation of any of them:
 *
 * ```text
 * workspace session (frozen)              openedProject.projectPath
 *   ↓ DbcImportControl                     this file: one action, one flow, four diagnoses
 * importDbcFromNativeDialog(projectPath)   orchestration/dbc-import.ts
 *   ↓ selectDbcContent()                   desktop/dbc-file-bridge.ts   (OS / Tauri IPC)
 *   ↓ importDbcAsset({ projectPath, … })   runtime/dbc-client.ts        (Runtime HTTP)
 * ```
 *
 * It performs no `invoke`, no Base64 work, no DBC parsing and no `fetch` of its own: the
 * dialog, the encoding and the request all belong to the modules above, and a second
 * implementation of any of them would be a second answer to the same question.
 *
 * Five properties are deliberate:
 *
 * * **The project is the session's, never guessed.** The path comes from the workspace
 *   session's opened project through the frozen `useWorkspaceSession` hook. With no
 *   project open the action is disabled and no import is attempted — there is no
 *   "current project" fallback, no module-level path and no picker invented for the empty
 *   case.
 * * **Cancel is control flow.** `{ status: "cancelled" }` ends the handler having
 *   invalidated nothing and failed at nothing: the user changed their mind, and a neutral
 *   status notice is the only thing said about it.
 * * **Success is an invalidation, not a hand-written cache write.** The imported asset is
 *   reflected by `invalidateQueries` with the *exact* key tuple the DBC workspace reads —
 *   `["dbc", "assets", projectPath]` — so the list refetches from the Runtime and the new
 *   asset appears without this component ever knowing the cache's shape.
 * * **Failures are four distinct facts.** A bridge failure, a Runtime API refusal, a
 *   transport failure and a contract disagreement are classified into four different
 *   messages, because "the shell refused the file", "the Runtime refused the import" and
 *   "the Runtime could not be reached" are not the same problem. A structured Runtime
 *   failure's stable code is surfaced; its message, its `details` and the raw response
 *   body are not.
 * * **Nothing sensitive is rendered.** The selected file's location never enters this
 *   component — the bridge strips it before the orchestration ever sees content — and the
 *   Runtime URL and the raw response body are never read. The only fact ever shown about a
 *   successful import is the asset's provenance basename, which the Runtime itself names.
 */

import { useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { DbcFileBridgeError } from "../../desktop/dbc-file-bridge";
import { importDbcFromNativeDialog } from "../../orchestration/dbc-import";
import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
} from "../../runtime/dbc-client";
import { useWorkspaceSession } from "../../workspace/WorkspaceSessionProvider";

/** What the control reports about its last attempt. */
type ImportNotice =
  | { readonly kind: "idle" }
  | { readonly kind: "cancelled" }
  | { readonly kind: "imported"; readonly sourceName: string }
  | { readonly kind: "failed"; readonly message: string; readonly code: string | null };

/**
 * The last attempt's notice, together with the project it belongs to.
 *
 * The path scopes the notice: a result produced under one project must never be shown
 * under another, so a project switch returns the control to its idle state the way the
 * session itself treats a switch — as a reset, not a merge.
 */
interface ImportState {
  readonly projectPath: string | null;
  readonly notice: ImportNotice;
}

const IDLE: ImportNotice = { kind: "idle" };

export interface DbcImportControlProps {
  /**
   * The project to import into.
   *
   * Given, it wins; omitted, the workspace session's opened project supplies it. The
   * override exists so the control embedded in a panel can be told the panel's project
   * explicitly rather than reading a second, possibly different one — the DBC workspace
   * may itself be rendered with an explicit `projectPath` and no session above it, and a
   * control that insisted on the session would then disable itself beside a populated
   * asset list.
   */
  readonly projectPath?: string | null;
}

export function DbcImportControl({ projectPath: explicitPath }: DbcImportControlProps = {}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const session = useWorkspaceSession();
  const projectPath =
    explicitPath !== undefined
      ? explicitPath
      : (session.openedProject?.projectPath ?? null);

  const [state, setState] = useState<ImportState>({ projectPath, notice: IDLE });
  const [pending, setPending] = useState(false);

  // A project switch discards the previous project's notice. Adjusting during render
  // (rather than in an effect) keeps another project's result from ever reaching a paint,
  // which is the same reason the session discards a selection when the project changes.
  if (state.projectPath !== projectPath) {
    setState({ projectPath, notice: IDLE });
  }

  // Publish only the attempt's own project's result: if the session moved on while the
  // import was in flight, the outcome belongs to a project that is no longer open.
  const show = (target: string, notice: ImportNotice): void => {
    setState((current) =>
      current.projectPath === target ? { projectPath: target, notice } : current,
    );
  };

  const importDbc = async (): Promise<void> => {
    if (projectPath === null || pending) return;
    const target = projectPath;
    setPending(true);
    show(target, IDLE);
    try {
      const outcome = await importDbcFromNativeDialog(target);
      if (outcome.status === "cancelled") {
        // Control flow, not a failure: nothing was invalidated and nothing failed.
        show(target, { kind: "cancelled" });
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["dbc", "assets", target] });
      show(target, { kind: "imported", sourceName: outcome.asset.sourceName });
    } catch (error) {
      show(target, { kind: "failed", ...classifyImportFailure(error, t) });
    } finally {
      setPending(false);
    }
  };

  const notice = state.notice;

  return (
    <div className="dbc-import-control">
      <button
        className="dbc-import-action"
        disabled={projectPath === null || pending}
        onClick={() => void importDbc()}
        type="button"
      >
        {pending ? t("dbc.import.importing") : t("dbc.import.action")}
      </button>

      {projectPath === null ? (
        <p className="dbc-import-needs-project">{t("dbc.import.needsProject")}</p>
      ) : null}

      {notice.kind === "cancelled" ? (
        <p className="dbc-import-notice" role="status">
          {t("dbc.import.cancelled")}
        </p>
      ) : notice.kind === "imported" ? (
        <p className="dbc-import-notice" role="status">
          {t("dbc.import.success", { name: notice.sourceName })}
        </p>
      ) : notice.kind === "failed" ? (
        <div className="dbc-import-failure" role="alert">
          <p className="dbc-import-failure-message">{notice.message}</p>
          {notice.code === null ? null : (
            <p className="dbc-import-failure-code">
              {t("dbc.import.error.code", { code: notice.code })}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Classify a thrown import failure into one safe message and, when the Runtime named one,
 * its stable code.
 *
 * The four families are distinct on purpose — a refusal by the shell, a refusal by the
 * Runtime, an unreachable Runtime and a contract disagreement are different problems with
 * different next steps, and collapsing them would destroy exactly the distinction that
 * makes them diagnosable. Only the Runtime's own stable `code` crosses into the UI: an
 * error's `message` and `details` may carry a path or a payload the Runtime chose to
 * report, and a user-facing notice is not the place for either.
 */
function classifyImportFailure(
  error: unknown,
  t: TFunction,
): { readonly message: string; readonly code: string | null } {
  if (error instanceof DbcFileBridgeError) {
    return { message: t("dbc.import.error.bridge"), code: null };
  }
  if (error instanceof RuntimeDbcApiError) {
    return { message: t("dbc.import.error.api"), code: error.code };
  }
  if (error instanceof RuntimeDbcTransportError) {
    return { message: t("dbc.import.error.transport"), code: null };
  }
  if (error instanceof RuntimeDbcContractError) {
    return { message: t("dbc.import.error.contract"), code: null };
  }
  return { message: t("dbc.import.error.unknown"), code: null };
}
