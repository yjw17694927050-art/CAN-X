import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { i18n } from "../../i18n/config";
import {
  RuntimeDbcApiError,
  RuntimeDbcContractError,
  RuntimeDbcTransportError,
  getDbcDatabase,
  listDbcAssets,
} from "../../runtime/dbc-client";
import type { RuntimeDbcAsset, RuntimeDbcDatabase } from "../../runtime/dbc-client";
import { DbcWorkspace } from "./DbcWorkspace";

// Only the two Runtime HTTP entry points are replaced. The typed error classes are
// kept real: the workspace must classify a genuine `RuntimeDbcApiError` /
// `RuntimeDbcTransportError` / `RuntimeDbcContractError`, and a fake class would
// prove nothing about that.
vi.mock("../../runtime/dbc-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../runtime/dbc-client")>();
  return { ...actual, getDbcDatabase: vi.fn(), listDbcAssets: vi.fn() };
});

const PROJECT = "C:\\customer\\secret-program";
const OTHER_PROJECT = "D:\\other-program";

const ASSET_A: RuntimeDbcAsset = {
  assetId: "dbc-asset-0001",
  sourceName: "powertrain.dbc",
  sha256: "a".repeat(64),
  sizeBytes: 2048,
  encoding: "utf-8",
  importedAt: "2026-09-17T08:30:00+00:00",
};

const ASSET_B: RuntimeDbcAsset = {
  assetId: "dbc-asset-0002",
  sourceName: "body.dbc",
  sha256: "b".repeat(64),
  sizeBytes: 512,
  encoding: "latin-1",
  importedAt: "2026-09-16T10:15:00+00:00",
};

const DATABASE_A: RuntimeDbcDatabase = {
  version: "1.0",
  messages: [
    {
      frameId: 0x123,
      name: "EngineSpeed",
      length: 8,
      isExtended: false,
      isFd: false,
      senders: ["ECM"],
      comment: "Engine speed broadcast",
      cycleTime: 10,
      signals: [
        {
          name: "EngineRpm",
          startBit: 0,
          length: 16,
          byteOrder: "little_endian",
          isSigned: false,
          isFloat: false,
          factor: 0.25,
          offset: 0,
          minimum: 0,
          maximum: 16383.75,
          unit: "rpm",
          receivers: ["ECM", "TCM"],
          choices: [],
          isMultiplexer: false,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: "Engine speed",
        },
        {
          name: "GearSelector",
          startBit: 16,
          length: 4,
          byteOrder: "big_endian",
          isSigned: true,
          isFloat: false,
          factor: 1,
          offset: -1,
          minimum: null,
          maximum: null,
          unit: null,
          receivers: [],
          choices: [
            { value: 0, label: "Park" },
            { value: 1, label: "Drive" },
          ],
          isMultiplexer: true,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: null,
        },
      ],
    },
    {
      frameId: 0x18daf110,
      name: "DiagnosticResponse",
      length: 64,
      isExtended: true,
      isFd: true,
      senders: [],
      comment: null,
      cycleTime: null,
      signals: [
        {
          name: "VoltageProbe",
          startBit: 0,
          length: 32,
          byteOrder: "big_endian",
          isSigned: true,
          isFloat: true,
          factor: 1,
          offset: 0,
          minimum: 0,
          maximum: 5,
          unit: "V",
          receivers: ["ECM"],
          choices: [],
          isMultiplexer: false,
          multiplexerSignal: "GearSelector",
          multiplexerIds: [0, 1],
          comment: "Multiplexed probe",
        },
      ],
    },
  ],
  nodes: [
    { name: "ECM", comment: "Engine control module" },
    { name: "TCM", comment: null },
  ],
};

const DATABASE_B: RuntimeDbcDatabase = {
  version: null,
  messages: [
    {
      frameId: 0x456,
      name: "DoorState",
      length: 2,
      isExtended: false,
      isFd: false,
      senders: ["BCM"],
      comment: null,
      cycleTime: null,
      signals: [
        {
          name: "DoorOpen",
          startBit: 0,
          length: 1,
          byteOrder: "little_endian",
          isSigned: false,
          isFloat: false,
          factor: 1,
          offset: 0,
          minimum: null,
          maximum: null,
          unit: null,
          receivers: [],
          choices: [],
          isMultiplexer: false,
          multiplexerSignal: null,
          multiplexerIds: null,
          comment: null,
        },
      ],
    },
  ],
  nodes: [],
};

const MARKUP_ASSET: RuntimeDbcAsset = {
  assetId: "dbc-asset-0003",
  sourceName: "<b>evil</b>.dbc",
  sha256: "c".repeat(64),
  sizeBytes: 64,
  encoding: "utf-8",
  importedAt: "2026-09-15T09:00:00+00:00",
};

const MARKUP_DATABASE: RuntimeDbcDatabase = {
  version: "<b>1.0</b>",
  messages: [
    {
      frameId: 0x001,
      name: "<img src=x>.msg",
      length: 1,
      isExtended: false,
      isFd: false,
      senders: [],
      comment: "<script>alert(1)</script>",
      cycleTime: null,
      signals: [],
    },
  ],
  nodes: [{ name: "<i>node</i>", comment: null }],
};

function renderWorkspace(projectPath: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DbcWorkspace projectPath={projectPath} />
    </QueryClientProvider>,
  );
}

function messageRow(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(name) });
}

async function selectAsset(name: RegExp): Promise<void> {
  fireEvent.click(screen.getByRole("option", { name }));
}

beforeEach(() => {
  vi.mocked(listDbcAssets).mockReset();
  vi.mocked(getDbcDatabase).mockReset();
  vi.mocked(listDbcAssets).mockResolvedValue([ASSET_A, ASSET_B]);
  vi.mocked(getDbcDatabase).mockResolvedValue(DATABASE_A);
});

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("DbcWorkspace — project scoping", () => {
  it("shows the no-project state and performs no DBC request without a project path", () => {
    renderWorkspace(null);

    expect(screen.getByText("No CAN-X project is open.")).toBeInTheDocument();
    expect(screen.getByText("Open a project to inspect its DBC assets.")).toBeInTheDocument();
    expect(listDbcAssets).not.toHaveBeenCalled();
    expect(getDbcDatabase).not.toHaveBeenCalled();
  });

  it("lists assets for the exact project path it was given, without normalizing it", async () => {
    renderWorkspace(PROJECT);

    expect(await screen.findByRole("option", { name: /powertrain\.dbc/ })).toBeInTheDocument();
    expect(listDbcAssets).toHaveBeenCalledWith(PROJECT);
    expect(vi.mocked(listDbcAssets).mock.calls[0]?.[0]).toBe(PROJECT);
  });

  it("shows a loading state while the asset collection is in flight", () => {
    vi.mocked(listDbcAssets).mockImplementation(
      () => new Promise<readonly RuntimeDbcAsset[]>(() => undefined),
    );

    renderWorkspace(PROJECT);

    expect(screen.getByText("Loading DBC assets")).toBeInTheDocument();
  });

  it("shows an empty state when the project owns no assets", async () => {
    vi.mocked(listDbcAssets).mockResolvedValue([]);

    renderWorkspace(PROJECT);

    expect(await screen.findByText("This project has no DBC assets.")).toBeInTheDocument();
  });

  it("renders every asset's provenance metadata", async () => {
    renderWorkspace(PROJECT);

    const first = await screen.findByRole("option", { name: /powertrain\.dbc/ });
    expect(within(first).getByText("2048 bytes")).toBeInTheDocument();
    expect(within(first).getByText("Encoding utf-8")).toBeInTheDocument();
    expect(within(first).getByText("Imported 2026-09-17T08:30:00+00:00")).toBeInTheDocument();

    const second = screen.getByRole("option", { name: /body\.dbc/ });
    expect(within(second).getByText("512 bytes")).toBeInTheDocument();
    expect(within(second).getByText("Encoding latin-1")).toBeInTheDocument();
  });

  it("loads no database until an asset is selected", async () => {
    renderWorkspace(PROJECT);

    await screen.findByRole("option", { name: /powertrain\.dbc/ });

    expect(getDbcDatabase).not.toHaveBeenCalled();
    expect(screen.getByText("Select a DBC asset to inspect its messages.")).toBeInTheDocument();
  });

  it("loads the selected asset's database through the typed client", async () => {
    renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /powertrain\.dbc/ });

    await selectAsset(/powertrain\.dbc/);

    await waitFor(() =>
      expect(getDbcDatabase).toHaveBeenCalledWith(PROJECT, "dbc-asset-0001"),
    );
    expect(await screen.findByRole("row", { name: /EngineSpeed/ })).toBeInTheDocument();
  });

  it("shows the database loading state", async () => {
    vi.mocked(getDbcDatabase).mockImplementation(
      () => new Promise<RuntimeDbcDatabase>(() => undefined),
    );

    renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /powertrain\.dbc/ });
    await selectAsset(/powertrain\.dbc/);

    expect(await screen.findByText("Loading DBC database")).toBeInTheDocument();
  });

  it("replaces the previous asset's database when the selection changes", async () => {
    vi.mocked(getDbcDatabase).mockImplementation(async (_projectPath, assetId) =>
      assetId === ASSET_B.assetId ? DATABASE_B : DATABASE_A,
    );

    renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /powertrain\.dbc/ });

    await selectAsset(/powertrain\.dbc/);
    expect(await screen.findByRole("row", { name: /EngineSpeed/ })).toBeInTheDocument();

    await selectAsset(/body\.dbc/);
    expect(await screen.findByRole("row", { name: /DoorState/ })).toBeInTheDocument();
    await waitFor(() =>
      expect(getDbcDatabase).toHaveBeenCalledWith(PROJECT, "dbc-asset-0002"),
    );
    // The first asset's detail must not linger behind the second's.
    expect(screen.queryByRole("row", { name: /EngineSpeed/ })).not.toBeInTheDocument();
  });

  it("resets the selection when the project changes", async () => {
    vi.mocked(listDbcAssets).mockImplementation(async (projectPath) =>
      projectPath === PROJECT ? [ASSET_A, ASSET_B] : [],
    );

    const view = renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /powertrain\.dbc/ });
    await selectAsset(/powertrain\.dbc/);
    expect(await screen.findByRole("row", { name: /EngineSpeed/ })).toBeInTheDocument();

    view.rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DbcWorkspace projectPath={OTHER_PROJECT} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("This project has no DBC assets.")).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /EngineSpeed/ })).not.toBeInTheDocument();
    expect(getDbcDatabase).not.toHaveBeenCalledWith(OTHER_PROJECT, expect.anything());
  });

  it("keeps asset selection local to the component instance", async () => {
    const first = renderWorkspace(PROJECT);
    const second = renderWorkspace(PROJECT);

    await within(first.container).findByRole("option", { name: /powertrain\.dbc/ });
    await within(second.container).findByRole("option", { name: /powertrain\.dbc/ });
    fireEvent.click(within(first.container).getByRole("option", { name: /powertrain\.dbc/ }));

    expect(
      await within(first.container).findByText("Select a message to inspect its signals."),
    ).toBeInTheDocument();
    // Nothing module-level was mutated: the sibling workspace still has no selection.
    expect(
      within(second.container).getByText("Select a DBC asset to inspect its messages."),
    ).toBeInTheDocument();
  });
});

describe("DbcWorkspace — Runtime failures", () => {
  it("reports a Runtime api failure with its stable code and no request details", async () => {
    vi.mocked(listDbcAssets).mockRejectedValue(
      new RuntimeDbcApiError(
        400,
        "project.not_found",
        "The project path does not exist.",
        { path: PROJECT },
        false,
        "project",
      ),
    );

    renderWorkspace(PROJECT);

    expect(await screen.findByText(/project\.not_found/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("secret-program");
  });

  it("reports a transport failure without forwarding layer text", async () => {
    vi.mocked(listDbcAssets).mockRejectedValue(
      new RuntimeDbcTransportError("The CAN-X Runtime could not be reached."),
    );

    renderWorkspace(PROJECT);

    expect(
      await screen.findByText("The CAN-X Runtime could not be reached."),
    ).toBeInTheDocument();
  });

  it("reports a contract failure", async () => {
    vi.mocked(listDbcAssets).mockRejectedValue(
      new RuntimeDbcContractError(
        "The CAN-X Runtime answered with an asset list payload that does not match the contract.",
      ),
    );

    renderWorkspace(PROJECT);

    expect(
      await screen.findByText("The Runtime answered with an unexpected DBC payload."),
    ).toBeInTheDocument();
  });

  it("retries the asset collection on demand", async () => {
    vi.mocked(listDbcAssets)
      .mockRejectedValueOnce(
        new RuntimeDbcApiError(500, "dbc.asset_registry_failed", "Registry failure.", {}, true, "dbc"),
      )
      .mockResolvedValueOnce([ASSET_A]);

    renderWorkspace(PROJECT);
    await screen.findByText(/dbc\.asset_registry_failed/);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("option", { name: /powertrain\.dbc/ })).toBeInTheDocument();
    expect(listDbcAssets).toHaveBeenCalledTimes(2);
  });

  it("retries the database read on demand", async () => {
    vi.mocked(getDbcDatabase)
      .mockRejectedValueOnce(
        new RuntimeDbcApiError(404, "dbc.asset_not_found", "Asset not found.", {}, false, "dbc"),
      )
      .mockResolvedValueOnce(DATABASE_A);

    renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /powertrain\.dbc/ });
    await selectAsset(/powertrain\.dbc/);

    expect(await screen.findByText(/dbc\.asset_not_found/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("row", { name: /EngineSpeed/ })).toBeInTheDocument();
    expect(getDbcDatabase).toHaveBeenCalledTimes(2);
  });
});

describe("DbcWorkspace — database inspection", () => {
  async function openPowertrain(): Promise<void> {
    renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /powertrain\.dbc/ });
    await selectAsset(/powertrain\.dbc/);
    await screen.findByRole("row", { name: /EngineSpeed/ });
  }

  it("summarizes the database version, message count and node count", async () => {
    await openPowertrain();

    expect(within(screen.getByRole("group", { name: "Version" })).getByText("1.0")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Messages" })).getByText("2")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Nodes" })).getByText("2")).toBeInTheDocument();
  });

  it("renders a standard Classic CAN message", async () => {
    await openPowertrain();

    const row = messageRow("EngineSpeed");
    expect(within(row).getByText("0x123")).toBeInTheDocument();
    expect(within(row).getByText("Standard")).toBeInTheDocument();
    expect(within(row).getByText("Classic CAN")).toBeInTheDocument();
    expect(within(row).getByText("8")).toBeInTheDocument();
  });

  it("renders an extended CAN FD message", async () => {
    await openPowertrain();

    const row = messageRow("DiagnosticResponse");
    expect(within(row).getByText("0x18DAF110")).toBeInTheDocument();
    expect(within(row).getByText("Extended")).toBeInTheDocument();
    expect(within(row).getByText("CAN FD")).toBeInTheDocument();
    expect(within(row).getByText("64")).toBeInTheDocument();
  });

  it("renders Intel and Motorola signal byte order", async () => {
    await openPowertrain();
    fireEvent.click(messageRow("EngineSpeed"));

    const rpm = await screen.findByRole("row", { name: /EngineRpm/ });
    expect(within(rpm).getByText("Intel (little-endian)")).toBeInTheDocument();

    const gear = screen.getByRole("row", { name: /GearSelector/ });
    expect(within(gear).getByText("Motorola (big-endian)")).toBeInTheDocument();
  });

  it("renders signed and unsigned signals", async () => {
    await openPowertrain();
    fireEvent.click(messageRow("EngineSpeed"));

    expect(within(await screen.findByRole("row", { name: /EngineRpm/ })).getByText("Unsigned")).toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /GearSelector/ })).getByText("Signed")).toBeInTheDocument();
  });

  it("renders factor, offset, bounds and unit", async () => {
    await openPowertrain();
    fireEvent.click(messageRow("EngineSpeed"));

    const rpm = within(await screen.findByRole("row", { name: /EngineRpm/ }));
    expect(rpm.getByText("0.25")).toBeInTheDocument();
    expect(rpm.getByText("16383.75")).toBeInTheDocument();
    expect(rpm.getByText("rpm")).toBeInTheDocument();
    // offset 0 and minimum 0 are both present and neither is dropped as falsy.
    expect(rpm.getAllByText("0").length).toBeGreaterThanOrEqual(2);
  });

  it("renders receivers and value choices", async () => {
    await openPowertrain();
    fireEvent.click(messageRow("EngineSpeed"));

    expect(within(await screen.findByRole("row", { name: /EngineRpm/ })).getByText("ECM, TCM")).toBeInTheDocument();
    expect(
      within(screen.getByRole("row", { name: /GearSelector/ })).getByText("0 = Park, 1 = Drive"),
    ).toBeInTheDocument();
  });

  it("renders multiplexing metadata", async () => {
    await openPowertrain();

    fireEvent.click(messageRow("EngineSpeed"));
    expect(within(await screen.findByRole("row", { name: /GearSelector/ })).getByText("Multiplexer")).toBeInTheDocument();

    fireEvent.click(messageRow("DiagnosticResponse"));
    const probe = await screen.findByRole("row", { name: /VoltageProbe/ });
    expect(within(probe).getByText("GearSelector = 0, 1")).toBeInTheDocument();
    expect(within(probe).getByText("Float")).toBeInTheDocument();
  });

  it("renders nullable comments and versions without failing", async () => {
    vi.mocked(getDbcDatabase).mockResolvedValue(DATABASE_B);

    renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /body\.dbc/ });
    await selectAsset(/body\.dbc/);
    await screen.findByRole("row", { name: /DoorState/ });

    expect(
      within(screen.getByRole("group", { name: "Version" })).getAllByText("—").length,
    ).toBeGreaterThan(0);

    fireEvent.click(messageRow("DoorState"));
    expect(within(await screen.findByRole("row", { name: /DoorOpen/ })).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("renders DBC text as text, never as markup", async () => {
    vi.mocked(listDbcAssets).mockResolvedValue([MARKUP_ASSET]);
    vi.mocked(getDbcDatabase).mockResolvedValue(MARKUP_DATABASE);

    const view = renderWorkspace(PROJECT);
    await screen.findByRole("option", { name: /<b>evil<\/b>\.dbc/ });

    expect(screen.getByText("<b>evil</b>.dbc")).toBeInTheDocument();
    expect(view.container.querySelector("b")).toBeNull();

    await selectAsset(/<b>evil<\/b>\.dbc/);
    await screen.findByRole("row", { name: /<img src=x>\.msg/ });

    expect(screen.getByText("<b>1.0</b>")).toBeInTheDocument();
    expect(screen.getByText("<i>node</i>")).toBeInTheDocument();
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.querySelector("i")).toBeNull();

    fireEvent.click(messageRow("<img src=x>.msg"));
    expect(await screen.findByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(view.container.querySelector("script")).toBeNull();
  });
});

describe("DbcWorkspace — i18n", () => {
  it("localizes the DBC workspace into zh-CN", async () => {
    await i18n.changeLanguage("zh-CN");
    vi.mocked(listDbcAssets).mockResolvedValue([]);

    renderWorkspace(PROJECT);

    expect(await screen.findByText("该项目没有 DBC 资产。")).toBeInTheDocument();

    renderWorkspace(null);
    expect(screen.getByText("当前没有打开的 CAN-X 项目。")).toBeInTheDocument();
  });
});
