import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the translated CAN-X product identity", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "CAN-X" })).toBeInTheDocument();
  });

  it("starts with the V0.1 engineering workspaces", async () => {
    render(<App />);

    expect(await screen.findByRole("tab", { name: "Trace" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Plot" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Agent" })).toBeInTheDocument();
  });
});
