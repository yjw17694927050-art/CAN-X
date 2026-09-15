import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RuntimeFrame } from "../../runtime/frame-schema";
import "../../i18n/config";
import { TracePanel } from "./TracePanel";

function frames(count: number): RuntimeFrame[] {
  return Array.from({ length: count }, (_, index) => ({
    sequence: BigInt(index),
    channelId: "can0",
    arbitrationId: 0x123,
    isExtended: false,
    isFd: false,
    bitrateSwitch: false,
    errorStateIndicator: false,
    dlc: 2,
    data: new Uint8Array([0x01, index % 256]),
    direction: "rx",
    hardwareTimestamp: null,
    hostTimestamp: 100 + index,
    normalizedTimestamp: index / 1000,
    clockDomain: "host.monotonic",
    timestampQuality: "host",
    flags: 0,
  }));
}

describe("TracePanel", () => {
  it("renders the required professional trace columns", async () => {
    render(<TracePanel frames={frames(1)} mode="follow" onModeChange={() => undefined} />);

    for (const name of ["Timestamp", "Channel", "ID", "DLC", "Data", "Direction"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(await screen.findByText("01 00")).toBeInTheDocument();
  });

  it("switches between follow and freeze without owning capture", () => {
    const onModeChange = vi.fn<(mode: "follow" | "freeze") => void>();
    const view = render(
      <TracePanel frames={frames(1)} mode="follow" onModeChange={onModeChange} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Freeze" }));
    expect(onModeChange).toHaveBeenCalledWith("freeze");

    view.rerender(<TracePanel frames={frames(1)} mode="freeze" onModeChange={onModeChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Follow" }));
    expect(onModeChange).toHaveBeenCalledWith("follow");
  });

  it("renders only a bounded virtual viewport for a large snapshot", async () => {
    const { container } = render(
      <TracePanel frames={frames(2_000)} mode="freeze" onModeChange={() => undefined} />,
    );

    await waitFor(() => expect(container.querySelector("[data-trace-row]")).toBeInTheDocument());
    const renderedRows = container.querySelectorAll("[data-trace-row]").length;
    expect(renderedRows).toBeGreaterThan(0);
    expect(renderedRows).toBeLessThan(80);
  });
});
