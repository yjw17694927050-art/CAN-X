/**
 * The React binding for the workspace session.
 *
 * Dockview renders **every panel into its own React root** (`WorkspacePanelRenderer`
 * calls `createRoot` per panel), so a provider that wraps `<App />` does not reach a
 * panel and a context created inside a panel would be a *second* authority. This
 * module is how the workspace's one session crosses that boundary, exactly the way the
 * app's one `QueryClient` already does: the shell creates the store and hands it in,
 * and each panel root wraps its content in a provider around the same instance.
 *
 * ```text
 * DockWorkspace                      creates ONE WorkspaceSessionStore
 *   ↓ WorkspaceSessionProvider(store)      wraps each panel's own React root
 *   ↓ useWorkspaceSession()                any panel, in any root, reads that one session
 * ```
 *
 * Two properties are deliberate:
 *
 * * **Absent is a real state.** {@link useWorkspaceSessionStore} returns `null` when no
 *   provider is above the component, and {@link useWorkspaceSessionSnapshot} then
 *   answers with the empty session rather than throwing or inventing a store. That is
 *   what lets a component be rendered standalone — in a unit test, or in a future
 *   embedding that has no workspace — without becoming a second session authority.
 *   A component that needs to *write* selection checks for the store explicitly; a
 *   component that only reads gets a well-defined empty answer.
 * * **Subscription, not copying.** The snapshot is read through `useSyncExternalStore`,
 *   so a change published by one panel re-renders every panel watching the session,
 *   across React roots, with no state duplication anywhere.
 */

import {
  createContext,
  useCallback,
  useContext,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import {
  EMPTY_WORKSPACE_SESSION,
  type WorkspaceSessionSnapshot,
  type WorkspaceSessionStore,
} from "./session";

/**
 * The session above this component, or `null` when there is none.
 *
 * Exported for the provider itself and for tests that need to assert the absence
 * explicitly; components should prefer the hooks below, which handle the absent case.
 */
export const WorkspaceSessionContext = createContext<WorkspaceSessionStore | null>(null);

export interface WorkspaceSessionProviderProps {
  /** The workspace's one session. Created by the shell, never by a panel. */
  readonly store: WorkspaceSessionStore;
  readonly children: ReactNode;
}

/** Make one session visible to a React subtree — call this per panel root. */
export function WorkspaceSessionProvider({
  store,
  children,
}: WorkspaceSessionProviderProps) {
  return (
    <WorkspaceSessionContext.Provider value={store}>
      {children}
    </WorkspaceSessionContext.Provider>
  );
}

/**
 * The session store above this component, or `null`.
 *
 * Callers that only read should use {@link useWorkspaceSession}; this exists so a
 * caller that *writes* can distinguish "there is no workspace here" from "the
 * workspace's state happens to be empty".
 */
export function useWorkspaceSessionStore(): WorkspaceSessionStore | null {
  return useContext(WorkspaceSessionContext);
}

/**
 * Observe one session store, or the empty session when there is none.
 *
 * The subscribe and read functions are re-created only when the store identity
 * changes, so `useSyncExternalStore` does not resubscribe on every render and does not
 * tear out the subscription when a parent re-renders.
 */
export function useWorkspaceSessionSnapshot(
  store: WorkspaceSessionStore | null,
): WorkspaceSessionSnapshot {
  const subscribe = useCallback(
    (listener: () => void) => (store === null ? () => undefined : store.subscribe(listener)),
    [store],
  );
  const read = useCallback(
    () => (store === null ? EMPTY_WORKSPACE_SESSION : store.getSnapshot()),
    [store],
  );
  return useSyncExternalStore(subscribe, read, read);
}

/** Observe the workspace session above this component, empty when there is none. */
export function useWorkspaceSession(): WorkspaceSessionSnapshot {
  return useWorkspaceSessionSnapshot(useWorkspaceSessionStore());
}
