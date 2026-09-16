"""Headless end-to-end smoke test for the packaged runtime (V0.1.1).

Proves the distribution-owned ``canx-runtime.exe`` — not a source-tree
interpreter — serves health, runs a virtual capture session, streams a binary
batch, stops and flushes, and exits cleanly after an authenticated shutdown.

The GUI desktop window cannot be launched under the host execution policy, so
this covers the runtime half of the smoke path; the windowed half is recorded as
NOT VERIFIED in the acceptance report.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
import websockets

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EXE = _REPO_ROOT / "build" / "runtime-dist" / "canx-runtime.exe"


def packaged_runtime() -> Path | None:
    """Resolve the packaged runtime executable, or None when it has not been built."""
    override = os.environ.get("CANX_TEST_RUNTIME_EXE")
    candidate = Path(override) if override else _DEFAULT_EXE
    return candidate if candidate.is_file() else None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
        proc.wait(timeout=10)


async def _await_health(client: httpx.AsyncClient) -> httpx.Response:
    last: httpx.Response | None = None
    for _ in range(80):
        try:
            last = await client.get("/health")
            if last.status_code == 200:
                return last
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise AssertionError(f"packaged runtime never became healthy: {last!r}")


@pytest.mark.skipif(
    packaged_runtime() is None, reason="packaged canx-runtime.exe has not been built"
)
async def test_packaged_runtime_serves_health_capture_stream_and_shutdown() -> None:
    """Distribution-owned runtime: health → capture → stream → flush → graceful exit."""
    exe = packaged_runtime()
    assert exe is not None
    port = _free_port()
    token = "v011-smoke-token"
    proc = subprocess.Popen(
        [str(exe), "--host", "127.0.0.1", "--port", str(port), "--session-token", token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            health = await _await_health(client)
            assert health.json() == {
                "status": "ready",
                "service": "canx-runtime",
                "schema_version": 1,
            }

            started = await client.post(
                "/capture/start", json={"rate_hz": 4_000, "batch_size": 25}
            )
            assert started.status_code == 202
            assert started.json()["stream_id"]

            async with websockets.connect(f"ws://127.0.0.1:{port}/stream/frames") as stream:
                batch = await asyncio.wait_for(stream.recv(), timeout=10)
                assert isinstance(batch, bytes)
                assert len(batch) > 0

            stopped = await client.post("/capture/stop")
            assert stopped.status_code == 200
            assert stopped.json()["status"] == "stopped"

            metrics = (await client.get("/metrics")).json()
            assert metrics["captured_frames"] > 0
            assert metrics["streamed_frames"] > 0
            assert metrics["dropped_frames"] == 0

            assert (await client.get("/runtime/status")).json()["capture_active"] is False

            shutdown = await client.post(
                "/runtime/shutdown", headers={"X-CANX-Session-Token": token}
            )
            assert shutdown.status_code == 202

        proc.wait(timeout=15)
        assert proc.returncode == 0
    finally:
        _terminate(proc)
