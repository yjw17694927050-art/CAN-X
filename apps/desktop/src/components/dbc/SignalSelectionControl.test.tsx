import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "../../i18n/config";
import type {
  RuntimeDbcDatabase,
  RuntimeDbcMessage,
  RuntimeDbcSignal,
} from "../../runtime/dbc-client";
import type { ProjectReadModel } from "../../runtime/project-client";
import { WorkspaceSessionProvider } from "../../workspace/WorkspaceSessionProvider";
import { createWorkspaceSession, type WorkspaceSessionStore } from "../../workspace/session";
import { SignalSelectionControl } from "./SignalSelectionControl";

const PROJECT_PATH = "C:\\customer\\secret-program";

const PROJECT: ProjectReadModel = {
  createdAt: "2026-09-15T09:00:00+00:00",
  displayName: "Secret Program",
  projectId: "0f6b9a52-1d3e-4c7a-9b21-51f0c6f2a7d8",
  schemaVersion: 1,
  updatedAt: "2026-09-17T09:00:00+00:00",
};

const ASSET_A = "dbc-asset-0001";
const ASSET_B = "dbc-asset-0002";

function signalDefinition(name: string, unit: string | null = "rpm"): RuntimeDbcSignal {
  return {
    byteOrder: "little_endian",
    choices: [],
    comment: null,
    factor: 0.125,
    isFloat: false,
    isMultiplexer: false,
    isSigned: false,
    length: 16,
    maximum: null,
    minimum: null,
    multiplexerIds: null,
    multiplexerSignal: null,
    name,
    offset: 0,
    receivers: [],
    startBit: 0,
    unit,
  };
}

function messageDefinition(
  name: string,
  signals: readonly RuntimeDbcSignal[],
): RuntimeDbcMessage {
  return {
    comment: null,
    cycleTime: null,
    frameId: 0x123,
    isExtended: false,
    isFd: false,
    length: 8,
    name,
    senders: [],
    signals,
  };
}

const ENGINE_DATA = messageDefinition("EngineData", [signalDefinition("EngineSpeed")]);
const BODY_DATA = messageDefinition("BodyData", [signalDefinition("DoorState", null)]);

const DATABASE: RuntimeDbcDatabase = { messages: [ENGINE_DATA], nodes: [], version: "1.0" };

const TWO_MESSAGE_DATABASE: RuntimeDbcDatabase = {
  messages: [ENGINE_DATA, BODY_DATA],
  nodes: [],
  version: "1.0",
};

/** A session with the project open — and therefore a session that may hold bindings. */
function openedSession(): WorkspaceSessionStore {
  const session = createWorkspaceSession();
  session.openProject({ project: PROJECT, projectPath: PROJECT_PATH });
  return session;
}

/**
 * Render the control inside the workspace's one session, exactly as a panel would.
 *
 * The returned `session` is the instance the provider was given, so a test can assert on
 * the selection the control actually wrote rather than on the screen alone.
 */
function renderControl(options: {
  readonly assetId?: string | null;
  readonly database?: RuntimeDbcDatabase;
  readonly session?: WorkspaceSessionStore;
}) {
  const session = options.session ?? openedSession();
  const view = render(
    <WorkspaceSessionProvider store={session}>
      <SignalSelectionControl
        assetId={options.assetId === undefined ? ASSET_A : options.assetId}
        database={options.database ?? DATABASE}
      />
    </WorkspaceSessionProvider>,
  );
  return { ...view, session };
}

function channelSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: "Channel" });
}

function plotButton(): HTMLElement {
  return screen.getByRole("button", { name: "Plot" });
}

describe("SignalSelectionControl — candidate channels", () => {
  it("shows no channel, and disables Plot, when the inspected asset is bound to none", () => {
    const { session } = renderControl({});

    expect(screen.getByText("This asset is not bound to any channel.")).toBeInTheDocument();
    expect(channelSelect()).toBeDisabled();
    expect(plotButton()).toBeDisabled();

    fireEvent.click(plotButton());

    expect(session.getSnapshot().selectedSignal).toBeNull();
  });

  it("stands on the one channel bound to the inspected asset and shows it", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);

    renderControl({ session });

    expect(channelSelect()).toHaveValue("can0");
    expect(within(channelSelect()).getByRole("option", { name: "can0" })).toBeInTheDocument();
    expect(screen.queryByText("This asset is not bound to any channel.")).not.toBeInTheDocument();
    expect(plotButton()).toBeEnabled();
  });

  it("offers only the channels bound to the inspected asset", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);
    session.bindChannel("can1", ASSET_A);
    session.bindChannel("can2", ASSET_B);

    renderControl({ session });

    const options = within(channelSelect())
      .getAllByRole("option")
      .map((option) => option.textContent);
    // can2 decodes the *other* document: it is not a candidate for this asset at all.
    expect(options).toEqual(["Choose a channel", "can0", "can1"]);
  });

  it("requires the user to name a channel when the asset is bound to more than one", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);
    session.bindChannel("can1", ASSET_A);

    renderControl({ session });

    // Nothing is chosen for the user, the choice is stated on screen, and Plot refuses
    // until a channel is named — plotting "whichever arrived" would be a different signal.
    expect(channelSelect()).toHaveValue("");
    expect(screen.getByRole("option", { name: "Choose a channel" })).toBeInTheDocument();
    expect(plotButton()).toBeDisabled();

    fireEvent.click(plotButton());

    expect(session.getSnapshot().selectedSignal).toBeNull();
  });

  it("stops offering a channel that is unbound while the control is on screen", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);

    renderControl({ session });

    act(() => {
      session.unbindChannel("can0");
    });

    expect(screen.getByText("This asset is not bound to any channel.")).toBeInTheDocument();
    expect(plotButton()).toBeDisabled();

    fireEvent.click(plotButton());

    expect(session.getSnapshot().selectedSignal).toBeNull();
  });
});

describe("SignalSelectionControl — plotting a signal", () => {
  it("writes the full channel-bound selection when Plot is activated", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);

    renderControl({ session });
    fireEvent.click(plotButton());

    expect(session.getSnapshot().selectedSignal).toEqual({
      assetId: ASSET_A,
      channelId: "can0",
      messageName: "EngineData",
      signalName: "EngineSpeed",
      unit: "rpm",
    });
  });

  it("keeps the channel the user explicitly chose out of several bound ones", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);
    session.bindChannel("can1", ASSET_A);

    renderControl({ session });
    fireEvent.change(channelSelect(), { target: { value: "can1" } });
    fireEvent.click(plotButton());

    expect(session.getSnapshot().selectedSignal?.channelId).toBe("can1");
    expect(session.getSnapshot().selectedSignal?.assetId).toBe(ASSET_A);
  });

  it("offers the signals of the message the user is looking at, and follows a message change", () => {
    const session = openedSession();
    session.bindChannel("can0", ASSET_A);

    const { container } = renderControl({ database: TWO_MESSAGE_DATABASE, session });

    expect(within(container).getByText("EngineSpeed")).toBeInTheDocument();
    expect(within(container).queryByText("DoorState")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "DBC messages" }), {
      target: { value: "BodyData" },
    });
    fireEvent.click(plotButton());

    expect(session.getSnapshot().selectedSignal?.messageName).toBe("BodyData");
    expect(session.getSnapshot().selectedSignal?.signalName).toBe("DoorState");
    // The unit is presentation metadata: a signal with none is selected with `null`.
    expect(session.getSnapshot().selectedSignal?.unit).toBeNull();
    expect(session.getSnapshot().selectedSignal?.channelId).toBe("can0");
  });
});

describe("SignalSelectionControl — no workspace session", () => {
  it("renders disabled and writes nothing instead of crashing when there is no session", () => {
    render(<SignalSelectionControl assetId={ASSET_A} database={DATABASE} />);

    expect(screen.getByText("This asset is not bound to any channel.")).toBeInTheDocument();
    expect(channelSelect()).toBeDisabled();
    expect(plotButton()).toBeDisabled();

    fireEvent.click(plotButton());
  });
});
