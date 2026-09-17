/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Test-only build switch for the native-dialog DBC smoke harness (V0.3-07, extended
   * in V0.3-08).
   *
   * Set to `"1"` when building a **smoke** bundle of the desktop app; unset (or any
   * other value) for an ordinary build, which then drops the harness entirely.
   */
  readonly VITE_CANX_DBC_SMOKE?: string;

  /**
   * Test-only project path the smoke harness imports a DBC into (V0.3-08).
   *
   * Set to a temporary CAN-X project when building a smoke bundle, so the harness has
   * an explicit `projectPath` to hand to `importDbcFromNativeDialog`. It is the same
   * kind of value the Runtime API requires and is never published to the document
   * title, the panel or any evidence record. Unset means the harness can still run
   * the dialog steps but must refuse the import step rather than guess a project.
   */
  readonly VITE_CANX_DBC_SMOKE_PROJECT_PATH?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
