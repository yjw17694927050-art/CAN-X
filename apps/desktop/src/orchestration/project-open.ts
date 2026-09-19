/**
 * The project-open orchestration — the seam between two boundaries, neither of which
 * owns the other.
 *
 * ```text
 * selectDirectory()                       user's explicit choice → a directory path
 * inspectProject(projectPath)             that path            → a project read model
 * ```
 *
 * The two boundaries are **arguments, not imports**. The desktop bridge
 * (`src/desktop/project-directory-bridge.ts`) is one possible `selectDirectory`, and
 * the Runtime project client (`src/runtime/project-client.ts`) is one possible
 * `inspectProject` — but this module names neither of them, so it can be composed
 * with a stub, a future workspace, or a different project source without a change
 * here. The project read model stays a **type parameter**: this module forwards a
 * project, it does not define one, and a second copy of the Runtime's schema would be
 * a second authority.
 *
 * ```text
 * openProjectFromNativeDialog(selectDirectory, inspectProject)
 *   ↓ selectDirectory()                    ← the one OS interaction
 *   ↓ null?  → { status: "cancelled" }     ← and nothing else happens
 *   ↓ inspectProject(projectPath)          ← the one project read, with the path verbatim
 *   ↓ { status: "opened", projectPath, project }
 * ```
 *
 * Four properties are deliberate:
 *
 * * **Cancel is control flow.** A dismissed picker ends the function before any read
 *   is attempted. It never becomes an error, an empty project or an inspection call:
 *   the user changed their mind, and the only honest report of that is `cancelled`.
 * * **The path is forwarded, not interpreted.** The string the selector returned is
 *   the string the inspector receives — no separators normalised, no case folded, no
 *   trailing separator stripped, no resolution against a working directory. This
 *   module does not know what a path *means*, so it must not rewrite one.
 * * **There is no current project.** No module-level variable, no cache, no
 *   "last opened" — the caller that knows which project it means passes the
 *   boundaries in, which is what lets two opens in one session stay independent.
 * * **Failures are not caught.** A typed bridge failure and an inspection failure
 *   mean different things to a caller, and re-wrapping either here would destroy
 *   exactly the distinction that makes them diagnosable.
 */

/**
 * What one project-open attempt produced.
 *
 * A discriminated union rather than `project | null`, so a caller must acknowledge
 * the cancelled case and cannot mistake "the user changed their mind" for a failure
 * or for an empty project.
 */
export type ProjectOpenOutcome<P> =
  | { readonly status: "cancelled" }
  | {
      readonly status: "opened";
      /** The directory the user chose, exactly as the selector returned it. */
      readonly projectPath: string;
      /** The project read model the inspection boundary produced, unchanged. */
      readonly project: P;
    };

/** Ask the user for a project directory: a path, or `null` if they dismissed it. */
export type ProjectDirectorySelector = () => Promise<string | null>;

/** Read one project out of the directory the user chose. */
export type ProjectInspector<P> = (projectPath: string) => Promise<P>;

/**
 * Let the user choose a project directory, then read the project it holds.
 *
 * Args:
 *   selectDirectory: The selection boundary. Called exactly once, and not at all
 *     after a cancel — a dismissed picker is never re-asked.
 *   inspectProject: The project-read boundary, given the selected directory
 *     character for character. Called exactly once, and only when a directory was
 *     actually chosen.
 *
 * Returns:
 *   `{ status: "cancelled" }` when the user dismissed the picker, or
 *   `{ status: "opened", projectPath, project }` with the selected directory and the
 *   project the read boundary produced.
 *
 * Throws:
 *   Whatever the boundary that failed threw — a typed selection failure, or an
 *   inspection failure of any kind. Nothing is translated here, because the caller
 *   can act on the distinction and this module cannot.
 */
export async function openProjectFromNativeDialog<P>(
  selectDirectory: ProjectDirectorySelector,
  inspectProject: ProjectInspector<P>,
): Promise<ProjectOpenOutcome<P>> {
  const projectPath = await selectDirectory();
  if (projectPath === null) {
    return { status: "cancelled" };
  }

  const project = await inspectProject(projectPath);
  return { status: "opened", projectPath, project };
}
