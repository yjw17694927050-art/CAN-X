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
 * A signal the user selected for decode-backed presentation (V0.3-14).
 *
 * **`channelId` is part of the identity, and it is not optional.** One DBC asset may be
 * bound to more than one CAN channel, so `assetId` + `messageName` + `signalName` does
 * *not* name a live signal: the same message and the same signal name can arrive on
 * `can0` and on `can1`, and a selection without a channel would silently mean "whichever
 * one arrived". The channel is what makes the selection a single decidable thing.
 *
 * It is deliberately **not** called an identity of the DBC document: `assetId` names the
 * document, `messageName` + `signalName` name the definition inside it, and `channelId`
 * names the live stream that definition is applied to.
 */
export interface SignalSelection {
  /** The CAN channel whose frames this selection applies to. */
  readonly channelId: string;
  /** The DBC asset that must currently be bound to {@link SignalSelection.channelId}. */
  readonly assetId: string;
  /** The message the signal belongs to. */
  readonly messageName: string;
  /** The signal's name inside that message. */
  readonly signalName: string;
  /**
   * Presentation metadata, never identity.
   *
   * The unit is carried so a Plot axis can label itself without a second lookup; it is
   * not consulted when deciding whether two selections are the same signal.
   */
  readonly unit: string | null;
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
    this.#publish({ ...this.#snapshot, dbcBindings: bindings, selectedSignal: this.#selectionUnder(bindings) });
  }

  /** Remove one channel's binding. A channel with no binding is a no-op. */
  unbindChannel(channelId: string): void {
    if (!this.#snapshot.dbcBindings.has(channelId)) return;
    const bindings = new Map(this.#snapshot.dbcBindings);
    bindings.delete(channelId);
    this.#publish({ ...this.#snapshot, dbcBindings: bindings, selectedSignal: this.#selectionUnder(bindings) });
  }

  /**
   * Remove every binding, keeping the open project and the browse selection.
   *
   * The signal selection goes with them: a selection is a statement about a channel's
   * current binding, and with no bindings there is nothing left for it to be true about.
   */
  clearBindings(): void {
    if (this.#snapshot.dbcBindings.size === 0 && this.#snapshot.selectedSignal === null) return;
    this.#publish({ ...this.#snapshot, dbcBindings: NO_BINDINGS, selectedSignal: null });
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
   * Record the signal the user selected for decode-backed presentation (V0.3-14).
   *
   * A selection is a statement about a **live binding**, so it is only accepted while it
   * is true: the named channel must currently be bound to the named asset. Anything else
   * is refused rather than stored, because a selection that names an unbound channel (or
   * a channel bound to a different document) is not a signal selection at all — it is a
   * reference to something that would never decode.
   *
   * Once accepted it is **involuntarily** dropped the moment it stops being true:
   * unbinding its channel, rebinding that channel to another asset, clearing the
   * bindings, or switching project all clear it. The caller never has to remember to.
   *
   * Throws:
   *   `Error` when `selection` is non-null and its channel is not bound to its asset.
   */
  selectSignal(selection: SignalSelection | null): void {
    if (selection !== null && this.assetForChannel(selection.channelId) !== selection.assetId) {
      throw new Error(
        "A signal selection requires its channel to be bound to the selected asset.",
      );
    }
    if (this.#snapshot.selectedSignal === selection) return;
    this.#publish({ ...this.#snapshot, selectedSignal: selection });
  }

  #requireOpenProject(action: string): void {
    if (this.#snapshot.openedProject === null) {
      throw new Error(`A workspace session cannot ${action} without an open project.`);
    }
  }

  /**
   * The current selection as it stands under a binding map that is about to be published.
   *
   * A selection survives a binding edit only while the channel it names is *still* bound
   * to the asset it names. Every other edit — unbinding a different channel, binding an
   * unrelated one — leaves it exactly where it was. That is the whole rule: the selection
   * follows the binding it was made against, and nothing else.
   */
  #selectionUnder(bindings: DecodeBindings): SignalSelection | null {
    const selection = this.#snapshot.selectedSignal;
    if (selection === null) return null;
    return bindings.get(selection.channelId) === selection.assetId ? selection : null;
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
