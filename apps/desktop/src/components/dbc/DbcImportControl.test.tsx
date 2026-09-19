import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DbcFileBridgeError } from "../../desktop/dbc-file-bridge";
import { i18n } from "../../i18n/config";
import { importDbcFromNativeDialog } from "../../orchestration/dbc-import";
import type { DbcImportOutcome } from "../../orchestration/dbc-import";
import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  listDbcAssets,
} from "../../runtime/dbc-client";
import type { RuntimeDbcAsset } from "../../runtime/dbc-client";
import type { ProjectReadModel } from "../../runtime/project-client";
import { createWorkspaceSession } from "../../workspace/session";
import type { WorkspaceSessionStore } from "../../workspace/session";
import { WorkspaceSessionProvider } from "../../workspace/WorkspaceSessionProvider";
import { DbcImportControl } from "./DbcImportControl";
import { DbcWorkspace } from "./DbcWorkspace";

// Only the two boundaries are replaced: the import orchestration (the OS dialog plus the
// Runtime POST) and the Runtime asset-list read. The typed error classes are kept real —
// the control must classify a genuine `DbcFileBridgeError` / `RuntimeDbcApiError` /
// `RuntimeDbcTransportError` / `RuntimeDbcContractError`, and a fake class would prove
// nothing about that. The QueryClient is real too: the invalidation is proven by the real
// `DbcWorkspace` asset list actually refetching, not by a mocked cache agreeing with itself.
vi.mock("../../orchestration/dbc-import", () => ({
  importDbcFromNativeDialog: vi.fn(),
}));

vi.mock("../../runtime/dbc-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../runtime/dbc-client")>();
  return { ...actual, listDbcAssets: vi.fn() };
});

const PROJECT = "C:\\customer\\secret-program";
const OTHER_PROJECT = "D:\\other-program";

const PROJECT_MODEL: ProjectReadModel = {
  projectId: "11111111-1111-4111-8111-111111111111",
  displayName: "Secret Program",
  schemaVersion: 3,
  createdAt: "2026-09-17T08:00:00+00:00",
  updatedAt: "2026-09-17T08:30:00+00:00",
};

const NEW_ASSET: RuntimeDbcAsset = {
  assetId: "dbc-asset-0099",
  sourceName: "chassis.dbc",
  sha256: "c".repeat(64),
  sizeBytes: 4096,
  encoding: "utf-8",
  importedAt: "2026-09-18T09:00:00+00:00",
};

// Strings a diagnosable failure legitimately carries and the UI must never show. The
// selected file's location is the one fact the bridge goes out of its way to strip, the
// URL says which boundary answered, and the body is the Runtime's own payload.
const SELECTED_FILE_PATH = "C:\\Users\\alice\\secret\\customer.dbc";
const RUNTIME_URL = "http://127.0.0.1:8765/dbc/assets";
const RAW_RESPONSE_BODY = '{"detail":"internal traceback at /dbc/assets"}';

interface RenderResult {
  readonly client: QueryClient;
  readonly session: WorkspaceSessionStore;
  readonly container: HTMLElement;
}

function renderControl(
  projectPath: string | null,
  options: { readonly withList?: boolean } = {},
): RenderResult {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const session = createWorkspaceSession();
  if (projectPath !== null) {
    session.openProject({ projectPath, project: PROJECT_MODEL });
  }

  const view = render(
    <QueryClientProvider client={client}>
      <WorkspaceSessionProvider store={session}>
        <DbcImportControl />
        {options.withList === true ? <DbcWorkspace projectPath={projectPath} /> : null}
      </WorkspaceSessionProvider>
    </QueryClientProvider>,
  );

  return { client, container: view.container, session };
}

function importAction(): HTMLElement {
  return screen.getByRole("button", { name: /Import DBC/ });
}

beforeEach(() => {
  vi.mocked(importDbcFromNativeDialog).mockReset();
  vi.mocked(listDbcAssets).mockReset();
});

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("DbcImportControl — project scoping", () => {
  it("disables the action and attempts nothing with no project open", async () => {
    renderControl(null);

    const action = importAction();
    expect(action).toBeDisabled();
    expect(screen.getByText("Open a project to import a DBC.")).toBeInTheDocument();

    fireEvent.click(action);

    await waitFor(() => expect(importDbcFromNativeDialog).not.toHaveBeenCalled());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("calls importDbcFromNativeDialog with exactly the session's project path", async () => {
    vi.mocked(importDbcFromNativeDialog).mockResolvedValue({
      status: "imported",
      asset: NEW_ASSET,
    });

    renderControl(PROJECT);
    fireEvent.click(importAction());

    await waitFor(() => expect(importDbcFromNativeDialog).toHaveBeenCalledTimes(1));
    expect(vi.mocked(importDbcFromNativeDialog).mock.calls[0]?.[0]).toBe(PROJECT);
  });

  it("shows the importing state and disables the action while an import is in flight", async () => {
    let settle: (outcome: DbcImportOutcome) => void = () => undefined;
    vi.mocked(importDbcFromNativeDialog).mockImplementation(
      () =>
        new Promise<DbcImportOutcome>((resolve) => {
          settle = resolve;
        }),
    );

    renderControl(PROJECT);
    fireEvent.click(importAction());

    const importing = await screen.findByRole("button", { name: /Importing/ });
    expect(importing).toBeDisabled();

    act(() => settle({ status: "cancelled" }));
    expect(await screen.findByText("No file was imported.")).toBeInTheDocument();
  });
});

describe("DbcImportControl — cancel", () => {
  it("treats a dismissed dialog as control flow: nothing invalidated and no failure", async () => {
    vi.mocked(importDbcFromNativeDialog).mockResolvedValue({ status: "cancelled" });

    const { client } = renderControl(PROJECT);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    fireEvent.click(importAction());

    expect(await screen.findByText("No file was imported.")).toBeInTheDocument();
    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("DbcImportControl — success", () => {
  it("invalidates the asset collection for the exact project and the list refetches the new asset", async () => {
    vi.mocked(listDbcAssets).mockResolvedValue([]);
    vi.mocked(importDbcFromNativeDialog).mockResolvedValue({
      status: "imported",
      asset: NEW_ASSET,
    });

    // A real QueryClient and the real DBC workspace: the proof that the key tuple is the
    // one the list reads is that the list actually refetches and shows the new asset.
    const { client, container } = renderControl(PROJECT, { withList: true });
    const invalidate = vi.spyOn(client, "invalidateQueries");

    expect(await screen.findByText("This project has no DBC assets.")).toBeInTheDocument();
    expect(listDbcAssets).toHaveBeenCalledTimes(1);

    // The Runtime now reports the imported asset. Only a refetch triggered by the
    // invalidation can reveal it — changing the mock alone moves no query.
    vi.mocked(listDbcAssets).mockResolvedValue([NEW_ASSET]);

    fireEvent.click(importAction());

    expect(await screen.findByRole("option", { name: /chassis\.dbc/ })).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dbc", "assets", PROJECT] });
    expect(listDbcAssets).toHaveBeenCalledTimes(2);

    expect(await screen.findByText("Imported chassis.dbc.")).toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain(RUNTIME_URL);
  });

  it("scopes a result to its project: switching project clears the notice", async () => {
    vi.mocked(importDbcFromNativeDialog).mockResolvedValue({
      status: "imported",
      asset: NEW_ASSET,
    });

    const { session } = renderControl(PROJECT);
    fireEvent.click(importAction());
    expect(await screen.findByText("Imported chassis.dbc.")).toBeInTheDocument();

    act(() => {
      session.openProject({ projectPath: OTHER_PROJECT, project: PROJECT_MODEL });
    });

    await waitFor(() => expect(screen.queryByText("Imported chassis.dbc.")).toBeNull());
  });
});

describe("DbcImportControl — failure classification", () => {
  it("renders a bridge failure as its own message, without the bridge's own text", async () => {
    vi.mocked(importDbcFromNativeDialog).mockRejectedValue(
      new DbcFileBridgeError("desktop.dbc_file_empty", "the file was empty", true),
    );

    renderControl(PROJECT);
    fireEvent.click(importAction());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The DBC file could not be selected.");
    expect(alert).not.toHaveTextContent("the file was empty");
  });

  it("renders a Runtime API failure and surfaces its stable code", async () => {
    vi.mocked(importDbcFromNativeDialog).mockRejectedValue(
      new RuntimeDbcApiError(409, "dbc.asset_duplicate", "already imported", {}, false, "dbc"),
    );

    renderControl(PROJECT);
    fireEvent.click(importAction());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The Runtime refused the DBC import.");
    expect(alert).toHaveTextContent("Code dbc.asset_duplicate");
    // The Runtime's own message is not the UI's to show.
    expect(alert).not.toHaveTextContent("already imported");
  });

  it("renders a transport failure as its own message", async () => {
    vi.mocked(importDbcFromNativeDialog).mockRejectedValue(
      new RuntimeDbcTransportError("the Runtime closed the connection"),
    );

    renderControl(PROJECT);
    fireEvent.click(importAction());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The CAN-X Runtime could not be reached.",
    );
  });

  it("renders a contract failure as its own message", async () => {
    vi.mocked(importDbcFromNativeDialog).mockRejectedValue(
      new RuntimeDbcContractError("the asset payload was missing a field"),
    );

    renderControl(PROJECT);
    fireEvent.click(importAction());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The Runtime answered with an unexpected DBC payload.",
    );
  });

  it("renders an unrecognized failure as the unknown message", async () => {
    vi.mocked(importDbcFromNativeDialog).mockRejectedValue(new Error("boom"));

    renderControl(PROJECT);
    fireEvent.click(importAction());

    expect(await screen.findByRole("alert")).toHaveTextContent("The DBC file could not be imported.");
  });

  it("gives the four failure families four distinct messages", async () => {
    const families = [
      new DbcFileBridgeError("desktop.dbc_file_unreadable", "x", true),
      new RuntimeDbcApiError(500, "dbc.import_failed", "x", {}, false, "dbc"),
      new RuntimeDbcTransportError("x"),
      new RuntimeDbcContractError("x"),
    ];

    const messages: string[] = [];
    for (const family of families) {
      vi.mocked(importDbcFromNativeDialog).mockRejectedValue(family);
      const view = renderControl(PROJECT);
      fireEvent.click(importAction());
      messages.push((await screen.findByRole("alert")).textContent ?? "");
      view.container.remove();
    }

    expect(new Set(messages).size).toBe(4);
  });
});

describe("DbcImportControl — nothing sensitive leaks", () => {
  const leaky: ReadonlyArray<readonly [string, unknown]> = [
    [
      "bridge",
      new DbcFileBridgeError(
        "desktop.dbc_file_unreadable",
        `could not read ${SELECTED_FILE_PATH}`,
        false,
      ),
    ],
    [
      "Runtime API",
      new RuntimeDbcApiError(
        500,
        "dbc.import_failed",
        `failed while storing ${RAW_RESPONSE_BODY}`,
        { path: SELECTED_FILE_PATH },
        false,
        "dbc",
      ),
    ],
    ["transport", new RuntimeDbcTransportError(`${RUNTIME_URL} refused the connection`)],
    ["contract", new RuntimeDbcContractError(`the payload was ${RAW_RESPONSE_BODY}`)],
  ];

  for (const [family, error] of leaky) {
    it(`never renders the file path, the Runtime URL or the raw body on a ${family} failure`, async () => {
      vi.mocked(importDbcFromNativeDialog).mockRejectedValue(error);

      const { container } = renderControl(PROJECT);
      fireEvent.click(importAction());

      expect(await screen.findByRole("alert")).toBeInTheDocument();

      const text = container.textContent ?? "";
      expect(text).not.toContain(SELECTED_FILE_PATH);
      expect(text).not.toContain(RUNTIME_URL);
      expect(text).not.toContain(RAW_RESPONSE_BODY);
    });
  }
});

describe("DbcImportControl — i18n", () => {
  it("localizes the import control into zh-CN", async () => {
    await i18n.changeLanguage("zh-CN");
    vi.mocked(importDbcFromNativeDialog).mockResolvedValue({ status: "cancelled" });

    renderControl(PROJECT);
    expect(screen.getByRole("button", { name: "导入 DBC…" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "导入 DBC…" }));
    expect(await screen.findByText("未导入任何文件。")).toBeInTheDocument();
  });
});
