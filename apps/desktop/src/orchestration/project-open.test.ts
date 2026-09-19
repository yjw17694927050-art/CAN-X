import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { ProjectDirectoryBridgeError } from "../desktop/project-directory-bridge";
import { openProjectFromNativeDialog } from "./project-open";

const PROJECT_PATH = "C:\\canx\\projects\\Vehicle_Bus";
const OTHER_PATH = "C:\\canx\\projects\\Powertrain";

/**
 * The project read model is deliberately *not* imported: this orchestration must be
 * generic over whatever the project boundary returns, so the test supplies its own
 * minimal shape instead of reaching for the Runtime's schema.
 */
interface ProjectSummary {
  readonly projectId: string;
  readonly name: string;
}

const PROJECT: ProjectSummary = { projectId: "prj-0001", name: "Vehicle_Bus" };

/** Records every path the inspection boundary was asked about. */
function recordingInspector(project: ProjectSummary = PROJECT): {
  readonly seen: readonly string[];
  readonly inspect: (projectPath: string) => Promise<ProjectSummary>;
} {
  const seen: string[] = [];
  return {
    seen,
    inspect: async (projectPath: string): Promise<ProjectSummary> => {
      seen.push(projectPath);
      return project;
    },
  };
}

describe("opening a project from the native directory picker", () => {
  it("reports a dismissed picker as a cancelled outcome", async () => {
    await expect(
      openProjectFromNativeDialog(async () => null, recordingInspector().inspect),
    ).resolves.toEqual({ status: "cancelled" });
  });

  it("never inspects when the picker was dismissed", async () => {
    const inspection = recordingInspector();

    await openProjectFromNativeDialog(async () => null, inspection.inspect);

    expect(inspection.seen).toEqual([]);
  });

  it("carries nothing but the status in a cancelled outcome", async () => {
    const outcome = await openProjectFromNativeDialog(
      async () => null,
      recordingInspector().inspect,
    );

    expect(Object.keys(outcome)).toEqual(["status"]);
  });

  it("forwards the selected directory character for character", async () => {
    const inspection = recordingInspector();

    await openProjectFromNativeDialog(async () => PROJECT_PATH, inspection.inspect);

    expect(inspection.seen).toEqual([PROJECT_PATH]);
    expect(inspection.seen[0]).toBe(PROJECT_PATH);
  });

  it("normalises nothing about the selected directory", async () => {
    const odd = "  C:\\canx\\Projects\\Mixed Case\\nested\\  ";
    const inspection = recordingInspector();

    await openProjectFromNativeDialog(async () => odd, inspection.inspect);

    expect(inspection.seen).toEqual([odd]);
  });

  it("reports an opened outcome carrying the path and the project", async () => {
    const outcome = await openProjectFromNativeDialog(
      async () => PROJECT_PATH,
      recordingInspector().inspect,
    );

    expect(outcome).toEqual({
      status: "opened",
      projectPath: PROJECT_PATH,
      project: PROJECT,
    });
  });

  it("returns the project the boundary produced, unchanged and by reference", async () => {
    const project: ProjectSummary = { projectId: "prj-0002", name: "Powertrain" };

    const outcome = await openProjectFromNativeDialog(async () => OTHER_PATH, async () => project);

    if (outcome.status !== "opened") throw new Error("expected an opened outcome");
    expect(outcome.project).toBe(project);
    expect(outcome.projectPath).toBe(OTHER_PATH);
  });

  it("selects exactly once and inspects exactly once for one open", async () => {
    const selectDirectory = vi.fn(async () => PROJECT_PATH);
    const inspection = recordingInspector();

    await openProjectFromNativeDialog(selectDirectory, inspection.inspect);

    expect(selectDirectory).toHaveBeenCalledTimes(1);
    expect(inspection.seen).toHaveLength(1);
  });

  it("keeps no state between two opens", async () => {
    const first: ProjectSummary = { projectId: "prj-0001", name: "First" };
    const second: ProjectSummary = { projectId: "prj-0002", name: "Second" };

    const firstOutcome = await openProjectFromNativeDialog(async () => PROJECT_PATH, async () => first);
    const secondOutcome = await openProjectFromNativeDialog(async () => OTHER_PATH, async () => second);

    expect(firstOutcome).toEqual({
      status: "opened",
      projectPath: PROJECT_PATH,
      project: first,
    });
    expect(secondOutcome).toEqual({
      status: "opened",
      projectPath: OTHER_PATH,
      project: second,
    });
    expect(JSON.stringify(secondOutcome)).not.toContain("First");
  });
});

describe("failures cross this orchestration untranslated", () => {
  it("does not swallow or reshape a selection failure", async () => {
    const failure = new ProjectDirectoryBridgeError(
      "desktop.project_directory_not_a_directory",
      "The selection is not a directory.",
      false,
    );
    const inspection = recordingInspector();

    const cause: unknown = await openProjectFromNativeDialog(
      async () => {
        throw failure;
      },
      inspection.inspect,
    ).catch((error: unknown) => error);

    expect(cause).toBe(failure);
    expect(inspection.seen).toEqual([]);
  });

  it("does not swallow or reshape an inspection failure", async () => {
    const failure = new Error("the selected directory holds no CAN-X project");
    const selectDirectory = vi.fn(async () => PROJECT_PATH);

    const cause: unknown = await openProjectFromNativeDialog(selectDirectory, async () => {
      throw failure;
    }).catch((error: unknown) => error);

    expect(cause).toBe(failure);
    expect(selectDirectory).toHaveBeenCalledTimes(1);
  });
});

describe("the boundaries this orchestration keeps apart", () => {
  /** The executable text, comments removed: prose may name what the code must not do. */
  const code = (): string =>
    readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "project-open.ts"), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");

  it("does not import the Runtime project client", () => {
    expect(code()).not.toMatch(/from\s+"\.\.\/runtime\//);
  });

  it("does not reach for Tauri or a dialog of its own", () => {
    expect(code()).not.toMatch(/@tauri-apps/);
    expect(code()).not.toMatch(/\binvoke\b/);
  });

  it("translates no failure", () => {
    expect(code()).not.toMatch(/\bcatch\b/);
    expect(code()).not.toMatch(/\btry\b/);
  });

  it("keeps no mutable module-level state", () => {
    expect(code()).not.toMatch(/^(let|var)\s/m);
    expect(code()).not.toMatch(/\bcurrentProject\b/);
    expect(code()).not.toMatch(/\bactiveProject\b/);
  });

  it("names no DBC concept", () => {
    expect(code()).not.toMatch(/dbc/i);
  });
});
