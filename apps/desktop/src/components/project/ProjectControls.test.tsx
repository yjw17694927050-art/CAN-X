import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ProjectDirectoryBridgeError,
  selectProjectDirectory,
} from "../../desktop/project-directory-bridge";
import { i18n } from "../../i18n/config";
import {
  RuntimeProjectApiError,
  RuntimeProjectContractError,
  RuntimeProjectTransportError,
  inspectProject,
} from "../../runtime/project-client";
import type { ProjectReadModel } from "../../runtime/project-client";
import { createWorkspaceSession } from "../../workspace/session";
import type { WorkspaceSessionStore } from "../../workspace/session";
import { WorkspaceSessionProvider } from "../../workspace/WorkspaceSessionProvider";
import { ProjectControls } from "./ProjectControls";

// Only the two leaf boundaries are replaced: the native directory selection and the
// Runtime project read. Everything the component is actually responsible for stays
// real — the `openProjectFromNativeDialog` composition that joins the two, the
// workspace session store the write lands in, and every typed error class. The
// component is a *wiring* layer, so a fake composition or a fake session would prove
// nothing about the wiring; a fake error class would prove nothing about the
// classification.
vi.mock("../../desktop/project-directory-bridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../desktop/project-directory-bridge")>();
  return { ...actual, selectProjectDirectory: vi.fn() };
});

vi.mock("../../runtime/project-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../runtime/project-client")>();
  return { ...actual, inspectProject: vi.fn() };
});

// A path that must never reach the DOM: `secret-program` is the disclosure marker.
const PROJECT_PATH = "C:\\customer\\secret-program";

const PROJECT: ProjectReadModel = {
  projectId: "2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
  displayName: "Vehicle Bus",
  schemaVersion: 1,
  createdAt: "2026-09-15T09:00:00+00:00",
  updatedAt: "2026-09-17T08:30:00+00:00",
};

function renderControls(store: WorkspaceSessionStore = createWorkspaceSession()) {
  return render(
    <WorkspaceSessionProvider store={store}>
      <ProjectControls />
    </WorkspaceSessionProvider>,
  );
}

/** Count the store's publications, so "written exactly once" is observable for real. */
function watchSession(store: WorkspaceSessionStore): () => number {
  let publications = 0;
  store.subscribe(() => {
    publications += 1;
  });
  return () => publications;
}

function openButton(): HTMLElement {
  return screen.getByRole("button", { name: /Open Project/ });
}

beforeEach(() => {
  vi.mocked(selectProjectDirectory).mockReset();
  vi.mocked(inspectProject).mockReset();
  vi.mocked(selectProjectDirectory).mockResolvedValue(PROJECT_PATH);
  vi.mocked(inspectProject).mockResolvedValue(PROJECT);
});

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("ProjectControls — the no-project state", () => {
  it("shows the no-project state and offers Open Project when the session holds none", () => {
    renderControls();

    expect(screen.getByText("No CAN-X project is open.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Open a project to inspect its DBC assets and bind a decoder to a channel.",
      ),
    ).toBeInTheDocument();
    expect(openButton()).toBeInTheDocument();
  });
});

describe("ProjectControls — opening a project", () => {
  it("writes exactly the opened projectPath and project into the session, once", async () => {
    const store = createWorkspaceSession();
    const publications = watchSession(store);
    renderControls(store);

    fireEvent.click(openButton());

    await waitFor(() => expect(store.getSnapshot().openedProject).not.toBeNull());
    expect(publications()).toBe(1);
    expect(store.getSnapshot().openedProject).toStrictEqual({
      projectPath: PROJECT_PATH,
      project: PROJECT,
    });
  });

  it("calls the selection boundary once and the inspection boundary once, with the selected path", async () => {
    renderControls();

    fireEvent.click(openButton());
    await screen.findByText("Vehicle Bus");

    expect(selectProjectDirectory).toHaveBeenCalledTimes(1);
    expect(inspectProject).toHaveBeenCalledTimes(1);
    expect(inspectProject).toHaveBeenCalledWith(PROJECT_PATH);
  });

  it("renders the opened project's displayName, projectId, schemaVersion and updatedAt", async () => {
    renderControls();

    fireEvent.click(openButton());

    expect(await screen.findByText("Vehicle Bus")).toBeInTheDocument();
    expect(screen.getByText(PROJECT.projectId)).toBeInTheDocument();
    expect(screen.getByText(String(PROJECT.schemaVersion))).toBeInTheDocument();
    expect(screen.getByText(PROJECT.updatedAt)).toBeInTheDocument();
    // The project path is a selection detail, not a project fact: it never renders.
    expect(document.body.textContent).not.toContain(PROJECT_PATH);
  });
});

describe("ProjectControls — a dismissed picker", () => {
  it("writes nothing, inspects nothing and renders no failure", async () => {
    vi.mocked(selectProjectDirectory).mockResolvedValue(null);
    const store = createWorkspaceSession();
    const publications = watchSession(store);
    renderControls(store);

    fireEvent.click(openButton());

    expect(await screen.findByText("No project was opened.")).toBeInTheDocument();
    expect(store.getSnapshot().openedProject).toBeNull();
    expect(publications()).toBe(0);
    expect(selectProjectDirectory).toHaveBeenCalledTimes(1);
    expect(inspectProject).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ProjectControls — failures", () => {
  it("classifies a ProjectDirectoryBridgeError as a directory-selection failure", async () => {
    vi.mocked(selectProjectDirectory).mockRejectedValue(
      new ProjectDirectoryBridgeError(
        "desktop.project_directory_not_a_directory",
        "The selected path is not a directory.",
        true,
      ),
    );

    renderControls();
    fireEvent.click(openButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The project directory could not be selected.");
    // A bridge code is not a Runtime code: it is never surfaced as one.
    expect(alert.textContent).not.toContain("desktop.project_directory_not_a_directory");
    expect(screen.queryByText(/^Code /)).not.toBeInTheDocument();
  });

  it("classifies a RuntimeProjectApiError and surfaces its stable code", async () => {
    vi.mocked(inspectProject).mockRejectedValue(
      new RuntimeProjectApiError(
        404,
        "project.not_found",
        "The project path does not exist.",
        { path: PROJECT_PATH },
        false,
        "project",
      ),
    );

    renderControls();
    fireEvent.click(openButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The Runtime refused the project.");
    expect(alert).toHaveTextContent("Code project.not_found");
    // The Runtime's own `details` carried the path; the component must not render it.
    expect(alert.textContent).not.toContain(PROJECT_PATH);
  });

  it("classifies a RuntimeProjectTransportError without forwarding the layer's text", async () => {
    vi.mocked(inspectProject).mockRejectedValue(
      new RuntimeProjectTransportError("connect ECONNREFUSED 127.0.0.1:8765"),
    );

    renderControls();
    fireEvent.click(openButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The CAN-X Runtime could not be reached.");
    expect(alert.textContent).not.toContain("ECONNREFUSED");
    expect(alert.textContent).not.toContain("127.0.0.1");
  });

  it("classifies a RuntimeProjectContractError without forwarding the payload", async () => {
    vi.mocked(inspectProject).mockRejectedValue(
      new RuntimeProjectContractError(
        'payload {"project_id":"leaked"} did not match the contract',
      ),
    );

    renderControls();
    fireEvent.click(openButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The Runtime answered with an unexpected project payload.",
    );
    expect(alert.textContent).not.toContain("leaked");
    expect(alert.textContent).not.toContain("project_id");
  });

  it("classifies an unrecognized failure as an unknown project error", async () => {
    vi.mocked(selectProjectDirectory).mockRejectedValue(new Error("boom"));

    renderControls();
    fireEvent.click(openButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The project could not be opened.");
    expect(alert.textContent).not.toContain("boom");
  });

  it("never renders the Runtime URL, a request URL or a raw response body", async () => {
    vi.mocked(inspectProject).mockRejectedValue(
      new RuntimeProjectApiError(
        500,
        "project.database_unavailable",
        "GET http://127.0.0.1:8765/project/inspect?project_path=x failed",
        { body: '{"project_id":"leaked"}' },
        true,
        "project",
      ),
    );

    renderControls();
    fireEvent.click(openButton());

    await screen.findByRole("alert");
    expect(document.body.textContent).not.toContain("127.0.0.1");
    expect(document.body.textContent).not.toContain("/project/inspect");
    expect(document.body.textContent).not.toContain("leaked");
  });
});

describe("ProjectControls — boundaries", () => {
  it("performs no fetch of its own", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    renderControls();
    fireEvent.click(openButton());
    await screen.findByText("Vehicle Bus");

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("ProjectControls — i18n", () => {
  it("localizes the controls into zh-CN", async () => {
    await i18n.changeLanguage("zh-CN");

    renderControls();

    expect(screen.getByRole("button", { name: /打开项目/ })).toBeInTheDocument();
    expect(screen.getByText("当前没有打开的 CAN-X 项目。")).toBeInTheDocument();
  });
});
