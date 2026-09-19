import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import type { DecodeOutcome } from "../../workspace/decoded-realtime";
import "../../i18n/config";
import { TracePanel, type TraceRow } from "./TracePanel";

function frame(sequence: number): RuntimeFrame {
  return {
    sequence: BigInt(sequence),
    channelId: "can0",
    arbitrationId: 0x123,
    isExtended: false,
    isFd: false,
    bitrateSwitch: false,
    errorStateIndicator: false,
    dlc: 2,
    data: new Uint8Array([0x01, sequence % 256]),
    direction: "rx",
    hardwareTimestamp: null,
    hostTimestamp: 100 + sequence,
    normalizedTimestamp: sequence / 1000,
    clockDomain: "host.monotonic",
    timestampQuality: "host",
    flags: 0,
  };
}

function rawRows(count: number): TraceRow[] {
  return Array.from({ length: count }, (_, index) => ({ frame: frame(index), decoded: null }));
}

function decodedRow(sequence: number, outcome: DecodeOutcome): TraceRow {
  const runtimeFrame = frame(sequence);
  return {
    frame: runtimeFrame,
    decoded: { assetId: "asset-1", frame: runtimeFrame, outcome },
  };
}

const RENDER_ONLY = () => undefined;

describe("TracePanel", () => {
  it("renders the required professional trace columns in order", async () => {
    render(<TracePanel rows={rawRows(1)} mode="follow" onModeChange={RENDER_ONLY} />);

    // The six raw columns keep their names and their order; the two decoded
    // columns are appended after them.
    expect(screen.getAllByRole("columnheader").map((node) => node.textContent)).toEqual([
      "Timestamp",
      "Channel",
      "ID",
      "DLC",
      "Data",
      "Direction",
      "Message",
      "Decoded signals",
    ]);
    expect(await screen.findByText("01 00")).toBeInTheDocument();
  });

  it("renders the raw cells of a row exactly as before", async () => {
    render(<TracePanel rows={rawRows(1)} mode="freeze" onModeChange={RENDER_ONLY} />);

    expect(await screen.findByText("0.000000")).toBeInTheDocument();
    expect(screen.getByText("can0")).toBeInTheDocument();
    expect(screen.getByText("123")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("RX")).toBeInTheDocument();
  });

  it("shows the decoded message name on the row the outcome belongs to", async () => {
    render(
      <TracePanel
        mode="freeze"
        onModeChange={RENDER_ONLY}
        rows={[
          decodedRow(0, {
            messageName: "EngineData",
            signals: [
              { name: "EngineSpeed", rawValue: 617, physicalValue: 1234, choiceLabel: null, unit: "rpm" },
            ],
            status: "decoded",
          }),
        ]}
      />,
    );

    expect(await screen.findByText("EngineData")).toBeInTheDocument();
  });

  it("shows each signal's physical value with its unit", async () => {
    render(
      <TracePanel
        mode="freeze"
        onModeChange={RENDER_ONLY}
        rows={[
          decodedRow(0, {
            messageName: "EngineData",
            signals: [
              { name: "EngineSpeed", rawValue: 617, physicalValue: 1234, choiceLabel: null, unit: "rpm" },
              { name: "Gear", rawValue: 3, physicalValue: 3, choiceLabel: null, unit: null },
            ],
            status: "decoded",
          }),
        ]}
      />,
    );

    expect(await screen.findByText("EngineSpeed=1234 rpm, Gear=3")).toBeInTheDocument();
    // The scaled value is the one on screen; the raw bus value never is.
    expect(screen.queryByText(/617/)).not.toBeInTheDocument();
  });

  it("annotates a labelled choice without hiding the value under it", async () => {
    render(
      <TracePanel
        mode="freeze"
        onModeChange={RENDER_ONLY}
        rows={[
          decodedRow(0, {
            messageName: "GearSelection",
            signals: [
              { name: "Gear", rawValue: 3, physicalValue: 3, choiceLabel: "Drive", unit: null },
            ],
            status: "decoded",
          }),
        ]}
      />,
    );

    expect(await screen.findByText("Gear=3 (Drive)")).toBeInTheDocument();
    expect(screen.queryByText("Drive")).not.toBeInTheDocument();
    expect(screen.queryByText("(Drive)")).not.toBeInTheDocument();
  });

  it("keeps a row raw-only while no decode outcome has arrived", async () => {
    render(<TracePanel rows={rawRows(1)} mode="freeze" onModeChange={RENDER_ONLY} />);

    expect(await screen.findByText("01 00")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("reports a per-frame failure as its stable code, with no values beside it", async () => {
    render(
      <TracePanel
        mode="freeze"
        onModeChange={RENDER_ONLY}
        rows={[decodedRow(0, { code: "dbc.message_not_found", status: "failed" })]}
      />,
    );

    expect(await screen.findByText("dbc.message_not_found")).toBeInTheDocument();
    // Message carries the code; the signals column stays empty rather than
    // echoing a path, a traceback or a raw response.
    expect(screen.getAllByText("—")).toHaveLength(1);
  });

  it("switches between follow and freeze without owning capture", () => {
    const onModeChange = vi.fn<(mode: "follow" | "freeze") => void>();
    const view = render(
      <TracePanel rows={rawRows(1)} mode="follow" onModeChange={onModeChange} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Freeze" }));
    expect(onModeChange).toHaveBeenCalledWith("freeze");

    view.rerender(<TracePanel rows={rawRows(1)} mode="freeze" onModeChange={onModeChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Follow" }));
    expect(onModeChange).toHaveBeenCalledWith("follow");
  });

  it("renders only a bounded virtual viewport for a large snapshot", async () => {
    const { container } = render(
      <TracePanel rows={rawRows(2_000)} mode="freeze" onModeChange={RENDER_ONLY} />,
    );

    await waitFor(() => expect(container.querySelector("[data-trace-row]")).toBeInTheDocument());
    const renderedRows = container.querySelectorAll("[data-trace-row]").length;
    expect(renderedRows).toBeGreaterThan(0);
    expect(renderedRows).toBeLessThan(80);
  });
});
