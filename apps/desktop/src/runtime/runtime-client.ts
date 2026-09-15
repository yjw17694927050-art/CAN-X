import { invoke } from "@tauri-apps/api/core";

export type RuntimeLifecycleState =
  | "starting"
  | "ready"
  | "stopping"
  | "stopped"
  | "failed"
  | "unavailable";

export async function getRuntimeStatus(): Promise<RuntimeLifecycleState> {
  return invoke<RuntimeLifecycleState>("runtime_status");
}

export async function restartRuntime(): Promise<RuntimeLifecycleState> {
  return invoke<RuntimeLifecycleState>("restart_runtime");
}
