import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

/**
 * The production desktop entry point mounts the app under a QueryClientProvider
 * (`src/main.tsx`); the DBC workspace reads its Runtime state through TanStack Query,
 * so the shell is rendered the same way here.
 */
function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  it("renders the translated CAN-X product identity", () => {
    renderApp();

    expect(screen.getByRole("heading", { name: "CAN-X" })).toBeInTheDocument();
  });

  it("starts with the V0.1 engineering workspaces", async () => {
    renderApp();

    expect(await screen.findByRole("tab", { name: "Trace" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Plot" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "Agent" })).toBeInTheDocument();
  });

  it("adds the read-only DBC workspace to the production Dockview layout", async () => {
    renderApp();

    expect(await screen.findByRole("tab", { name: "DBC" })).toBeInTheDocument();
  });
});
