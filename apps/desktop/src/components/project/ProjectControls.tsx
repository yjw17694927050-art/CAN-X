/**
 * The Project panel's production controls — the one place a user opens a CAN-X
 * project, and the one caller of the project-open composition.
 *
 * ```text
 * click "Open Project…"
 *   ↓ openProjectFromNativeDialog(selectProjectDirectory, inspectProject)   ← the composition
 *   ↓ { status: "cancelled" }                       → a quiet notice, no session write
 *   ↓ { status: "opened", projectPath, project }    → session.openProject({ projectPath, project })
 * ```
 *
 * Five properties are deliberate:
 *
 * * **It wires, it does not re-implement.** Selection (`selectProjectDirectory`),
 *   inspection (`inspectProject`) and the composition that joins them
 *   (`openProjectFromNativeDialog`) are the single implementations, passed in as the
 *   composition's own arguments. There is no picker, no `fetch`, no `invoke`, no
 *   `fs` and no path handling here.
 * * **The session is the only state it owns.** The open project is read from
 *   {@link useWorkspaceSession} and written through {@link useWorkspaceSessionStore}.
 *   There is no module-level current project and no second session: a successful open
 *   writes exactly the `{ projectPath, project }` the composition produced, and two
 *   controls on one page share one session through their one provider.
 * * **Cancel is control flow.** A dismissed picker writes nothing, inspects nothing
 *   and is reported as a quiet, non-error notice — never as a failure.
 * * **Failures are classified, their detail is not forwarded.** A bridge, Runtime api,
 *   transport or contract failure becomes a distinct human message; only a Runtime
 *   api failure surfaces its stable code. The Runtime URL, a request URL, the raw
 *   response body, the project path and every other filesystem diagnostic stay out.
 * * **The path and the id are not the same fact.** The panel renders the project the
 *   Runtime *declared* — displayName, projectId, schemaVersion, updatedAt — and never
 *   the directory the user happened to select.
 */

import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ProjectDirectoryBridgeError,
  selectProjectDirectory,
} from "../../desktop/project-directory-bridge";
import { openProjectFromNativeDialog } from "../../orchestration/project-open";
import {
  RuntimeProjectApiError,
  RuntimeProjectContractError,
  RuntimeProjectTransportError,
  inspectProject,
} from "../../runtime/project-client";
import {
  useWorkspaceSession,
  useWorkspaceSessionStore,
} from "../../workspace/WorkspaceSessionProvider";

/**
 * What one open attempt left behind, when it left a notice at all.
 *
 * A discriminated union rather than a bare message, so a cancelled picker can never
 * be rendered as a failure and a failure can carry its stable code without the
 * cancelled case having to invent one.
 */
type OpenNotice =
  | { readonly kind: "cancelled" }
  | { readonly kind: "failure"; readonly message: string; readonly code: string | null };

export function ProjectControls() {
  const { t } = useTranslation();
  const session = useWorkspaceSession();
  const store = useWorkspaceSessionStore();
  const [opening, setOpening] = useState(false);
  const [notice, setNotice] = useState<OpenNotice | null>(null);

  async function handleOpen(): Promise<void> {
    if (opening) return;
    setOpening(true);
    setNotice(null);
    try {
      const outcome = await openProjectFromNativeDialog(
        selectProjectDirectory,
        inspectProject,
      );
      if (outcome.status === "cancelled") {
        setNotice({ kind: "cancelled" });
        return;
      }
      // The write is the composition's own result, character for character: the
      // string the selector returned and the project the inspector produced, with no
      // path interpretation in between. With no session above this component there is
      // nothing to write to, and a control that owned a private session would be a
      // second authority — so it renders state and writes nothing.
      store?.openProject({ projectPath: outcome.projectPath, project: outcome.project });
    } catch (error) {
      setNotice(describeFailure(error, t));
    } finally {
      setOpening(false);
    }
  }

  const opened = session.openedProject;

  return (
    <section aria-label={t("project.title")} className="project-controls">
      <div className="project-actions">
        <button
          className="project-open"
          disabled={opening}
          onClick={() => void handleOpen()}
          type="button"
        >
          {opening ? t("project.opening") : t("project.open")}
        </button>
      </div>

      {notice === null ? null : notice.kind === "cancelled" ? (
        <p className="project-cancelled">{t("project.cancelled")}</p>
      ) : (
        <section className="project-failure" role="alert">
          <p className="project-failure-message">{notice.message}</p>
          {notice.code === null ? null : (
            <p className="project-failure-code">
              {t("project.error.code", { code: notice.code })}
            </p>
          )}
        </section>
      )}

      {opened === null ? (
        <div className="project-notice project-notice-empty">
          <p className="project-notice-title">{t("project.noProject.title")}</p>
          <p className="project-notice-hint">{t("project.noProject.hint")}</p>
        </div>
      ) : (
        <dl className="project-facts">
          <dt>{t("project.displayName")}</dt>
          <dd>{opened.project.displayName}</dd>
          <dt>{t("project.projectId")}</dt>
          <dd>{opened.project.projectId}</dd>
          <dt>{t("project.schemaVersion")}</dt>
          <dd>{opened.project.schemaVersion}</dd>
          <dt>{t("project.updatedAt")}</dt>
          <dd>{opened.project.updatedAt}</dd>
        </dl>
      )}
    </section>
  );
}

/**
 * Classify a thrown value into a safe message and, for a Runtime api failure, its code.
 *
 * The order matters only in that the four typed classes are disjoint; the fallback is
 * a message of this component's own, so an unexpected throw is reported without its
 * text — a stack line, a URL or a payload never travels through here.
 */
function describeFailure(error: unknown, t: TFunction): OpenNotice {
  if (error instanceof RuntimeProjectApiError) {
    return { kind: "failure", message: t("project.error.api"), code: error.code };
  }
  if (error instanceof ProjectDirectoryBridgeError) {
    return { kind: "failure", message: t("project.error.bridge"), code: null };
  }
  if (error instanceof RuntimeProjectTransportError) {
    return { kind: "failure", message: t("project.error.transport"), code: null };
  }
  if (error instanceof RuntimeProjectContractError) {
    return { kind: "failure", message: t("project.error.contract"), code: null };
  }
  return { kind: "failure", message: t("project.error.unknown"), code: null };
}
