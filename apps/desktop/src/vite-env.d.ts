/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Test-only build switch for the V0.3-07 native-dialog smoke harness.
   *
   * Set to `"1"` when building a **smoke** bundle of the desktop app; unset (or any
   * other value) for an ordinary build, which then drops the harness entirely.
   */
  readonly VITE_CANX_DBC_SMOKE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
