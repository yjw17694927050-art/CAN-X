import { describe, expect, it, vi } from "vitest";

import type { ProjectReadModel } from "../runtime/project-client";
import {
  EMPTY_WORKSPACE_SESSION,
  createWorkspaceSession,
  type OpenedProject,
  type WorkspaceSessionSnapshot,
  type WorkspaceSessionStore,
} from "./session";

function projectReadModel(displayName: string): ProjectReadModel {
  return {
    projectId: `${displayName}-id`,
    displayName,
    schemaVersion: 1,
    createdAt: "2026-09-19T00:00:00+00:00",
    updatedAt: "2026-09-19T00:00:00+00:00",
  };
}

function opened(displayName: string, projectPath: string): OpenedProject {
  return { projectPath, project: projectReadModel(displayName) };
}

const PROJECT_A = "C:\\programs\\alpha.canx";
const PROJECT_B = "D:\\programs\\beta.canx";

describe("WorkspaceSessionStore — construction", () => {
  it("starts empty and creates a fresh store every time", () => {
    const first = createWorkspaceSession();
    const second = createWorkspaceSession();

    expect(first).not.toBe(second);
    expect(first.getSnapshot()).toBe(EMPTY_WORKSPACE_SESSION);
    expect(first.getSnapshot().openedProject).toBeNull();
    expect(first.getSnapshot().dbcBindings.size).toBe(0);
    expect(first.getSnapshot().browsedAssetId).toBeNull();
    expect(first.getSnapshot().selectedSignal).toBeNull();
  });

  it("has no module-level instance: two stores cannot share a project", () => {
    const first = createWorkspaceSession();
    const second = createWorkspaceSession();

    first.openProject(opened("Alpha", PROJECT_A));

    expect(first.getSnapshot().openedProject?.projectPath).toBe(PROJECT_A);
    expect(second.getSnapshot().openedProject).toBeNull();
  });
});

describe("WorkspaceSessionStore — opening a project", () => {
  it("records the path and the Runtime read model together", () => {
    const session = createWorkspaceSession();

    session.openProject(opened("Alpha", PROJECT_A));

    const held = session.getSnapshot().openedProject;
    expect(held?.projectPath).toBe(PROJECT_A);
    expect(held?.project.displayName).toBe("Alpha");
    // The path is carried verbatim: this store is not a path authority.
    expect(held?.projectPath).not.toContain("/");
  });

  it("refuses to open a project with an empty path", () => {
    const session = createWorkspaceSession();

    expect(() => session.openProject(opened("Alpha", ""))).toThrow(
      /cannot open a project without a path/,
    );
    expect(session.getSnapshot().openedProject).toBeNull();
  });

  it("closes back to the empty session", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.browseAsset("asset-a");

    session.closeProject();

    expect(session.getSnapshot()).toBe(EMPTY_WORKSPACE_SESSION);
  });

  it("publishes nothing when closing an already-closed session", () => {
    const session = createWorkspaceSession();
    const listener = vi.fn();
    session.subscribe(listener);

    session.closeProject();

    expect(listener).not.toHaveBeenCalled();
  });
});

describe("WorkspaceSessionStore — project switch is a reset", () => {
  it("clears bindings, browse selection and signal selection chosen against the old project", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.browseAsset("asset-from-alpha");
    session.bindChannel("can0", "asset-from-alpha");
    session.selectSignal({
      assetId: "asset-from-alpha",
      channelId: "can0",
      messageName: "EngineSpeed",
      signalName: "EngineRpm",
      unit: "rpm",
    });

    expect(session.getSnapshot().dbcBindings.get("can0")).toBe("asset-from-alpha");

    session.openProject(opened("Beta", PROJECT_B));

    const after = session.getSnapshot();
    expect(after.openedProject?.projectPath).toBe(PROJECT_B);
    expect(after.dbcBindings.size).toBe(0);
    expect(after.browsedAssetId).toBeNull();
    expect(after.selectedSignal).toBeNull();
    expect(session.assetForChannel("can0")).toBeNull();
  });

  it("clears even when the new project path is the same string", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-from-alpha");

    // A different project can occupy a path that once held another, so the store
    // never carries a selection across an open — not even across an identical path.
    session.openProject(opened("Alpha-again", PROJECT_A));

    expect(session.getSnapshot().dbcBindings.size).toBe(0);
  });

  it("cannot bind an asset from the previous project after a switch", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-from-alpha");

    session.openProject(opened("Beta", PROJECT_B));

    // Nothing survives to be re-read, and a binding still requires an open project,
    // so there is no state in which project B can resolve to project A's asset.
    expect(session.assetForChannel("can0")).toBeNull();
    expect([...session.getSnapshot().dbcBindings.keys()]).toStrictEqual([]);
  });
});

describe("WorkspaceSessionStore — browsing is not binding", () => {
  it("changes only the browse selection", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-a");

    session.browseAsset("asset-b");

    expect(session.getSnapshot().browsedAssetId).toBe("asset-b");
    // The binding is untouched: there is no path from browsing to a binding.
    expect(session.assetForChannel("can0")).toBe("asset-a");
    // And the newly browsed asset did not become any other channel's decode target.
    expect(session.assetForChannel("can1")).toBeNull();
  });

  it("browsing an asset never creates a binding for any channel", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));

    session.browseAsset("asset-a");
    session.browseAsset("asset-b");
    session.browseAsset(null);

    expect(session.getSnapshot().dbcBindings.size).toBe(0);
  });

  it("browsing an asset never makes it a channel's decode target", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));

    session.browseAsset("asset-a");

    // The decode question is answered from the binding map alone. A browsed asset is
    // *being looked at*, which is not the same fact as *decodes can0*, and a store that
    // conflated them would silently re-target a decoder the moment a user clicked a row.
    expect(session.assetForChannel("can0")).toBeNull();
    expect(session.assetForChannel("can1")).toBeNull();
    expect(session.getSnapshot().browsedAssetId).toBe("asset-a");
  });

  it("clears the browse selection with null", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.browseAsset("asset-a");

    session.browseAsset(null);

    expect(session.getSnapshot().browsedAssetId).toBeNull();
  });
});

describe("WorkspaceSessionStore — binding a channel", () => {
  it("refuses to bind without an open project", () => {
    const session = createWorkspaceSession();

    expect(() => session.bindChannel("can0", "asset-a")).toThrow(
      /cannot bind a channel without an open project/,
    );
    expect(session.getSnapshot().dbcBindings.size).toBe(0);
  });

  it("refuses an empty channel or an empty asset", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));

    expect(() => session.bindChannel("", "asset-a")).toThrow(/both a channel and an asset/);
    expect(() => session.bindChannel("can0", "")).toThrow(/both a channel and an asset/);
    expect(session.getSnapshot().dbcBindings.size).toBe(0);
  });

  it("binds one channel to one asset", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));

    session.bindChannel("can0", "asset-a");

    expect(session.assetForChannel("can0")).toBe("asset-a");
    expect(session.assetForChannel("can1")).toBeNull();
  });

  it("lets two channels share one asset and a third use another", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));

    session.bindChannel("can0", "asset-a");
    session.bindChannel("can1", "asset-a");
    session.bindChannel("can2", "asset-b");

    expect(session.assetForChannel("can0")).toBe("asset-a");
    expect(session.assetForChannel("can1")).toBe("asset-a");
    expect(session.assetForChannel("can2")).toBe("asset-b");
  });

  it("rebinding a channel replaces its asset without disturbing the others", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-a");
    session.bindChannel("can1", "asset-a");

    session.bindChannel("can0", "asset-b");

    expect(session.assetForChannel("can0")).toBe("asset-b");
    expect(session.assetForChannel("can1")).toBe("asset-a");
    expect(session.getSnapshot().dbcBindings.size).toBe(2);
  });

  it("publishes nothing when the same pair is bound twice", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-a");
    const before = session.getSnapshot();
    const listener = vi.fn();
    session.subscribe(listener);

    session.bindChannel("can0", "asset-a");

    expect(listener).not.toHaveBeenCalled();
    expect(session.getSnapshot()).toBe(before);
  });

  it("unbinds one channel and treats an unbound channel as a no-op", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-a");
    session.bindChannel("can1", "asset-a");
    const listener = vi.fn();
    session.subscribe(listener);

    session.unbindChannel("can0");
    session.unbindChannel("can0");

    expect(session.assetForChannel("can0")).toBeNull();
    expect(session.assetForChannel("can1")).toBe("asset-a");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("clears every binding while keeping the project and the browse selection", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.browseAsset("asset-a");
    session.bindChannel("can0", "asset-a");
    session.bindChannel("can1", "asset-b");

    session.clearBindings();

    const after = session.getSnapshot();
    expect(after.dbcBindings.size).toBe(0);
    expect(after.openedProject?.projectPath).toBe(PROJECT_A);
    expect(after.browsedAssetId).toBe("asset-a");
  });
});

describe("WorkspaceSessionStore — the signal selection follows its binding", () => {
  function withSelection(): WorkspaceSessionStore {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-a");
    session.bindChannel("can1", "asset-b");
    session.selectSignal({
      assetId: "asset-a",
      channelId: "can0",
      messageName: "EngineSpeed",
      signalName: "EngineRpm",
      unit: "rpm",
    });
    return session;
  }

  it("names the channel, because an asset may be bound to more than one", () => {
    const session = withSelection();

    // assetId + message + signal cannot identify a live signal on their own: the same
    // definition can arrive on two channels, so the channel is part of the identity.
    expect(session.getSnapshot().selectedSignal?.channelId).toBe("can0");
    expect(session.getSnapshot().selectedSignal?.assetId).toBe("asset-a");
  });

  it("refuses a selection whose channel is not bound to the selected asset", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.bindChannel("can0", "asset-a");

    expect(() =>
      session.selectSignal({
        assetId: "asset-b",
        channelId: "can0",
        messageName: "EngineSpeed",
        signalName: "EngineRpm",
        unit: null,
      }),
    ).toThrow(/requires its channel to be bound to the selected asset/);
    expect(session.getSnapshot().selectedSignal).toBeNull();
  });

  it("refuses a selection for a channel that is not bound at all", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));

    expect(() =>
      session.selectSignal({
        assetId: "asset-a",
        channelId: "can0",
        messageName: "EngineSpeed",
        signalName: "EngineRpm",
        unit: null,
      }),
    ).toThrow(/requires its channel to be bound/);
  });

  it("survives a binding edit that does not touch its channel", () => {
    const session = withSelection();

    session.bindChannel("can2", "asset-c");
    session.unbindChannel("can1");

    expect(session.getSnapshot().selectedSignal?.signalName).toBe("EngineRpm");
  });

  it("clears when its own channel is unbound", () => {
    const session = withSelection();

    session.unbindChannel("can0");

    expect(session.getSnapshot().selectedSignal).toBeNull();
  });

  it("clears when its own channel is rebound to a different asset", () => {
    const session = withSelection();

    session.bindChannel("can0", "asset-b");

    expect(session.getSnapshot().selectedSignal).toBeNull();
    expect(session.assetForChannel("can0")).toBe("asset-b");
  });

  it("clears when every binding is cleared", () => {
    const session = withSelection();

    session.clearBindings();

    expect(session.getSnapshot().selectedSignal).toBeNull();
    expect(session.getSnapshot().dbcBindings.size).toBe(0);
  });

  it("clears on a project switch, like everything else the old project chose", () => {
    const session = withSelection();

    session.openProject(opened("Beta", PROJECT_B));

    expect(session.getSnapshot().selectedSignal).toBeNull();
  });

  it("can be cleared explicitly", () => {
    const session = withSelection();

    session.selectSignal(null);

    expect(session.getSnapshot().selectedSignal).toBeNull();
  });

  it("treats the unit as presentation, not identity", () => {
    const session = withSelection();

    // Re-selecting the same signal with a different unit is a different object but the
    // same live signal; the store records what it is told and compares nothing on unit.
    session.selectSignal({
      assetId: "asset-a",
      channelId: "can0",
      messageName: "EngineSpeed",
      signalName: "EngineRpm",
      unit: null,
    });

    expect(session.getSnapshot().selectedSignal?.unit).toBeNull();
    expect(session.getSnapshot().selectedSignal?.signalName).toBe("EngineRpm");
  });
});

describe("WorkspaceSessionStore — subscription", () => {  it("notifies every listener, across what would be separate React roots", () => {
    const session = createWorkspaceSession();
    const traceRoot = vi.fn();
    const dbcRoot = vi.fn();
    session.subscribe(traceRoot);
    session.subscribe(dbcRoot);

    session.openProject(opened("Alpha", PROJECT_A));

    expect(traceRoot).toHaveBeenCalledTimes(1);
    expect(dbcRoot).toHaveBeenCalledTimes(1);
  });

  it("stops notifying an unsubscribed listener", () => {
    const session = createWorkspaceSession();
    const listener = vi.fn();
    const unsubscribe = session.subscribe(listener);

    expect(session.listenerCount).toBe(1);
    unsubscribe();
    session.openProject(opened("Alpha", PROJECT_A));

    expect(session.listenerCount).toBe(0);
    expect(listener).not.toHaveBeenCalled();
  });

  it("keeps the snapshot identity stable when nothing changes", () => {
    const session = createWorkspaceSession();
    session.openProject(opened("Alpha", PROJECT_A));
    session.browseAsset("asset-a");
    const before = session.getSnapshot();

    session.browseAsset("asset-a");
    session.clearBindings();
    session.unbindChannel("can9");

    expect(session.getSnapshot()).toBe(before);
  });

  it("publishes a new snapshot object on every real change", () => {
    const session = createWorkspaceSession();
    const seen: WorkspaceSessionSnapshot[] = [];
    session.subscribe(() => seen.push(session.getSnapshot()));

    session.openProject(opened("Alpha", PROJECT_A));
    session.browseAsset("asset-a");
    session.bindChannel("can0", "asset-a");

    expect(seen).toHaveLength(3);
    expect(new Set(seen).size).toBe(3);
  });
});
