/**
 * The workspace session — the one place a CAN-X workspace keeps what the *user*
 * selected, and nothing else.
 *
 * ```text
 * WorkspaceSessionStore
 *   openedProject   { projectPath, project }      which project this workspace is on
 *   dbcBindings     channelId -> assetId          the explicit decode binding
 *   browsedAssetId  string | null                 which asset the user is looking at
 *   selectedSignal  SignalSelection | null        reserved for V0.3-14 (Plot/decoder)
 * ```
 *
 * **This is not a Runtime authority and must never become one.** The Runtime stays
 * stateless: `project_path` is an explicit argument on every request, the project
 * database is the only project authority, and the query cache is the only server-state
 * cache. This store holds UI/session *selection* — facts that exist because a person
 * clicked something — and it holds no copy of anything the Runtime owns. There is no
 * `currentProject` module variable, no singleton and no default instance: the store is
 * created by the workspace that owns it and passed to the components that read it, so
 * two workspaces in one page cannot leak into each other.
 *
 * Four properties are deliberate:
 *
 * * **A project switch is a reset, not a merge.** {@link WorkspaceSessionStore.openProject}
 *   replaces the whole session: bindings, browse selection and signal selection are
 *   discarded, because they were chosen against a *different* project and an `assetId`
 *   from project A is meaningless — not merely stale — in project B. Clearing
 *   unconditionally (rather than only when the path string differs) is the fail-closed
 *   reading: two different projects can live at two paths that once held one project,
 *   and the store cannot tell those apart, so it never carries a selection across.
 * * **Browsing is not binding.** {@link WorkspaceSessionStore.browseAsset} changes only
 *   `browsedAssetId`. It cannot reach `dbcBindings` — there is no code path from one to
 *   the other — so "the user clicked an asset" can never silently re-target a channel's
 *   decoder. A binding changes only when a caller explicitly calls
 *   {@link WorkspaceSessionStore.bindChannel}.
 * * **A binding requires an open project.** {@link WorkspaceSessionStore.bindChannel}
 *   refuses when no project is open (and when either key is empty), because a
 *   `channelId -> assetId` map with no project behind it is a binding to nothing. It
 *   cannot verify that `assetId` belongs to the open project — only the asset list can
 *   know that — which is exactly why the switch above clears unconditionally.
 * * **Snapshots are immutable and identity-stable.** Every setter publishes a fresh
 *   snapshot and a no-op setter publishes nothing, so `useSyncExternalStore` never
 *   re-renders on an unchanged session and never misses a change.
 *
 * Session-scoped only: nothing here is persisted, no schema is migrated, and the
 * Runtime learns of none of it. A binding is a decision about *this* workspace run.
 */

import type { ProjectReadModel } from "../runtime/project-client";

/**
 * One open project: the directory the user chose and the Runtime's read model for it.
 *
 * `projectPath` is the exact string the native picker returned and the Runtime was
 * asked about — never normalised here, because this store is not a path authority.
 * The two travel together so a consumer can never hold one without the other.
 */
export interface OpenedProject {
  /** The directory the user selected, character for character. */
  readonly projectPath: string;
  /** What the Runtime said that directory is. */
  readonly project: ProjectReadModel;
}

/**
 * A signal the user selected, reserved for the V0.3-14 decoder and Plot binding.
 *
 * Declared now, and settable now, so the shape is fixed before anything consumes it.
 * Nothing in V0.3-13 reads it: no decoder exists yet, and inventing a consumer would
 * be inventing a decode path this increment does not have.
 */
export interface SignalSelection {
  /** The DBC asset the signal is defined in. */
  readonly assetId: string;
  /** The message the signal belongs to. */
  readonly messageName: string;
  /** The signal's name inside that message. */
  readonly signalName: string;
}

/**
 * The decode binding: which project-owned DBC asset decodes which CAN channel.
 *
 * A plain map rather than a single "active DBC", because the mapping is genuinely
 * many-to-many in one direction: `can0` and `can1` may share one asset while `can2`
 * uses another, and a single active asset cannot express that.
 */
export type DecodeBindings = ReadonlyMap<string, string>;

/** Everything one workspace session holds. */
export interface WorkspaceSessionSnapshot {
  /** The project this workspace is on, or `null` when none is open. */
  readonly openedProject: OpenedProject | null;
  /** `channelId -> assetId`, session-scoped. Empty until a user binds something. */
  readonly dbcBindings: DecodeBindings;
  /** The asset the user is currently inspecting. Never a decode target by itself. */
  readonly browsedAssetId: string | null;
  /** Reserved for V0.3-14; never set by V0.3-13's UI. */
  readonly selectedSignal: SignalSelection | null;
}

/** The one shared empty binding map, so an untouched session allocates nothing. */
const NO_BINDINGS: DecodeBindings = new Map<string, string>();

/** The session of a workspace with nothing open. Frozen by convention, not mutation. */
export const EMPTY_WORKSPACE_SESSION: WorkspaceSessionSnapshot = {
  openedProject: null,
  dbcBindings: NO_BINDINGS,
  browsedAssetId: null,
  selectedSignal: null,
};

/**
 * One workspace's selection state, with a subscription for React.
 *
 * Constructed per workspace — `createWorkspaceSession()` is a factory, not a
 * singleton, and this module deliberately exports no instance. Consumers subscribe
 * through {@link useWorkspaceSession}, which reads it with `useSyncExternalStore`
 * so a panel rendered into its own React root (Dockview does exactly that) observes
 * the same session as the shell around it.
 */
export class WorkspaceSessionStore {
  #snapshot: WorkspaceSessionSnapshot = EMPTY_WORKSPACE_SESSION;
  readonly #listeners = new Set<() => void>();

  /** The current snapshot. Identity-stable: unchanged until a setter actually changes it. */
  getSnapshot = (): WorkspaceSessionSnapshot => this.#snapshot;

  /** Subscribe to session changes. Returns the unsubscribe function. */
  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  /** How many React roots are observing this session. Diagnostics, not policy. */
  get listenerCount(): number {
    return this.#listeners.size;
  }

  /**
   * Open a project, discarding everything selected against the previous one.
   *
   * This is the only way a session acquires a project, and it is a **replacement**:
   * bindings, browse selection and signal selection do not survive it. Re-opening the
   * same path clears too — deliberately, because a different project can occupy a path
   * that once held another, and a store that cannot tell those apart must not carry a
   * binding across the ambiguity.
   *
   * Throws:
   *   `Error` when `projectPath` is empty. A session with a project that has no path is
   *   not a state any caller can act on, and silently accepting it would produce
   *   bindings attributed to a project nobody named.
   */
  openProject(opened: OpenedProject): void {
    if (opened.projectPath === "") {
      throw new Error("A workspace session cannot open a project without a path.");
    }
    this.#publish({ ...EMPTY_WORKSPACE_SESSION, openedProject: opened });
  }

  /** Close the project, returning the session to its empty state. */
  closeProject(): void {
    if (this.#snapshot === EMPTY_WORKSPACE_SESSION) return;
    this.#publish(EMPTY_WORKSPACE_SESSION);
  }

  /**
   * Record which asset the user is inspecting, or clear it with `null`.
   *
   * This is *browse state only*. It never touches a binding and there is no path from
   * here to one: an asset becomes a decode target only through
   * {@link WorkspaceSessionStore.bindChannel}.
   */
  browseAsset(assetId: string | null): void {
    if (this.#snapshot.browsedAssetId === assetId) return;
    this.#publish({ ...this.#snapshot, browsedAssetId: assetId });
  }

  /**
   * Bind one CAN channel to one project-owned DBC asset.
   *
   * Re-binding the same channel replaces its asset; re-binding the same pair changes
   * nothing. Other channels are untouched, so `can0` and `can1` can share one asset
   * while `can2` holds another.
   *
   * Throws:
   *   `Error` when no project is open, or when `channelId` / `assetId` is empty. A
   *   binding is a statement about a project and two named things; accepting a partial
   *   one would put an unattributable entry in the map.
   */
  bindChannel(channelId: string, assetId: string): void {
    this.#requireOpenProject("bind a channel");
    if (channelId === "" || assetId === "") {
      throw new Error("A decode binding requires both a channel and an asset.");
    }
    if (this.#snapshot.dbcBindings.get(channelId) === assetId) return;
    const bindings = new Map(this.#snapshot.dbcBindings);
    bindings.set(channelId, assetId);
    this.#publish({ ...this.#snapshot, dbcBindings: bindings });
  }

  /** Remove one channel's binding. A channel with no binding is a no-op. */
  unbindChannel(channelId: string): void {
    if (!this.#snapshot.dbcBindings.has(channelId)) return;
    const bindings = new Map(this.#snapshot.dbcBindings);
    bindings.delete(channelId);
    this.#publish({ ...this.#snapshot, dbcBindings: bindings });
  }

  /** Remove every binding, keeping the open project and the browse selection. */
  clearBindings(): void {
    if (this.#snapshot.dbcBindings.size === 0) return;
    this.#publish({ ...this.#snapshot, dbcBindings: NO_BINDINGS });
  }

  /**
   * Which asset decodes this channel, or `null` when nothing is bound.
   *
   * The query V0.3-14's decoder is expected to make. It answers from the binding map
   * alone and never consults the browse selection, so "what is the user looking at"
   * and "what decodes this channel" cannot be confused by a caller.
   */
  assetForChannel(channelId: string): string | null {
    return this.#snapshot.dbcBindings.get(channelId) ?? null;
  }

  /**
   * Record the selected signal. Reserved for V0.3-14 — nothing in V0.3-13 sets it.
   *
   * Kept here rather than added later so the session's shape is fixed before the
   * increment that consumes it, and so a signal selection is cleared by the same
   * project switch that clears everything else.
   */
  selectSignal(selection: SignalSelection | null): void {
    if (this.#snapshot.selectedSignal === selection) return;
    this.#publish({ ...this.#snapshot, selectedSignal: selection });
  }

  #requireOpenProject(action: string): void {
    if (this.#snapshot.openedProject === null) {
      throw new Error(`A workspace session cannot ${action} without an open project.`);
    }
  }

  #publish(snapshot: WorkspaceSessionSnapshot): void {
    this.#snapshot = snapshot;
    for (const listener of this.#listeners) listener();
  }
}

/** One fresh session. A factory, never a module-level instance — see the header. */
export function createWorkspaceSession(): WorkspaceSessionStore {
  return new WorkspaceSessionStore();
}
