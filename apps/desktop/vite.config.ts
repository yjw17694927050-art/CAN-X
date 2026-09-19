import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // The Tauri dev shell (apps/desktop/src-tauri/tauri.conf.json) declares
  // `devUrl: "http://localhost:1420"` and starts this server through its
  // `beforeDevCommand`. Pinning the port here — and refusing to fall back to
  // another one — is what makes `pnpm tauri dev` connect to the port Tauri is
  // actually waiting on. Without strictPort, Vite silently moves to 1421 when
  // 1420 is taken while Tauri keeps polling 1420, which is the V0.3 defect this
  // closes. The two configs may never drift again: src/smoke/
  // dev-port-contract.test.ts reads both and asserts they agree.
  server: {
    port: 1420,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
