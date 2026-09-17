import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const queryClient = new QueryClient();
const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("CAN-X root element is missing.");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

// Test-only entry point for the V0.3-07 native-dialog end-to-end smoke harness. It is
// compiled into the bundle **only** when the desktop app is built with
// `VITE_CANX_DBC_SMOKE=1`: `import.meta.env` is replaced at build time, so an ordinary
// build folds the branch away and the harness chunk is dropped with it. It is not a
// product feature and is not part of the DBC Workspace.
if (import.meta.env.VITE_CANX_DBC_SMOKE === "1") {
  void import("./smoke/dbc-dialog-smoke").then((module) => {
    module.startDbcDialogSmoke();
  });
}

