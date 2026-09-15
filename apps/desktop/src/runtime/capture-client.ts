const RUNTIME_URL = "http://127.0.0.1:8765";

interface RuntimeErrorPayload {
  readonly code?: unknown;
  readonly message?: unknown;
}

async function requireSuccess(response: Response): Promise<void> {
  if (response.ok) return;
  let payload: RuntimeErrorPayload = {};
  try {
    payload = (await response.json()) as RuntimeErrorPayload;
  } catch {
    // The HTTP status remains diagnostic when a non-JSON proxy response is returned.
  }
  const code = typeof payload.code === "string" ? payload.code : `http.${response.status}`;
  const message = typeof payload.message === "string" ? payload.message : response.statusText;
  throw new Error(`${code}: ${message}`);
}

export async function startVirtualCapture(): Promise<boolean> {
  const response = await fetch(`${RUNTIME_URL}/capture/start`, {
    body: JSON.stringify({ batch_size: 250, channel_count: 1, is_fd: false, rate_hz: 1_000, seed: 1 }),
    headers: { "Content-Type": "application/json" },
    method: "POST",
  });
  if (response.status === 409) {
    const payload = (await response.json()) as RuntimeErrorPayload;
    if (payload.code === "capture.already_running") return false;
  }
  await requireSuccess(response);
  return true;
}

export async function stopCapture(): Promise<void> {
  const response = await fetch(`${RUNTIME_URL}/capture/stop`, { method: "POST" });
  await requireSuccess(response);
}
