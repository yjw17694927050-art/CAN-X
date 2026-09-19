"""End-to-end acceptance for the Recorder control plane against real storage.

Nothing is mocked that matters: a real CAN-X project is created on the real
filesystem, a real capture records real Parquet segments through the real
:class:`~canx.recorder.project_recorder.ProjectRecorder` and
:class:`~canx.data.session.DataSessionWriter`, and the Recorder HTTP surface reads
the same durable SQLite rows every other domain reads.

Three properties are pinned:

* **the control plane tracks a real recording.** While a project-backed capture
  runs, ``/recorder/status`` names the very data session ``/capture/start``
  handed out, reports a non-zero recorded frame count, and reads the durable row
  as ``ACTIVE``; after a healthy stop it reports ``settled`` / ``COMPLETED``.
* **a pending finalization is never guessed.** While the finalization worker is
  parked, the status says ``pending`` and the durable row still says ``ACTIVE`` —
  never ``COMPLETED`` and never ``FAILED``, because neither verdict exists yet.
* **a storage failure stays a failure.** A rejected append marks the session
  ``FAILED`` durably, and the status reports ``failed`` — never ``completed``.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.data.errors import DataStorageError
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient

#: The 1 s production deadline assumes realistic segment sizes; these tests
#: finalize small recordings, so they state the budget their workload needs.
CLEANUP_TIMEOUT_SECONDS = 6.0

RATE_HZ = 4_000.0
BATCH_SIZE = 10


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x1A0,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0xA0]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1_000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _body(root: Path) -> dict[str, object]:
    return {"rate_hz": RATE_HZ, "batch_size": BATCH_SIZE, "project_path": str(root)}


class ParkedFinalize:
    """Park ``DataSessionWriter.flush_pending`` until the test releases it.

    Blocking the worker *before* it touches SQLite is what makes the pending
    window deterministic and free of write contention: the durable row is still
    ``ACTIVE`` and no verdict exists yet.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._loop = asyncio.get_running_loop()
        self._entered = asyncio.Event()
        self.release = threading.Event()
        real = DataSessionWriter.flush_pending

        def blocked(writer: DataSessionWriter) -> None:
            self._loop.call_soon_threadsafe(self._entered.set)
            self.release.wait(timeout=30)
            real(writer)

        monkeypatch.setattr(DataSessionWriter, "flush_pending", blocked)

    async def wait_entered(self, *, timeout: float = 10.0) -> None:
        await asyncio.wait_for(self._entered.wait(), timeout=timeout)


async def test_a_project_backed_capture_is_observable_from_start_to_completed(
    tmp_path: Path,
) -> None:
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_body(handle.root))
            session_id = started.json()["data_session_id"]
            await asyncio.sleep(0.15)
            live = (await client.get("/recorder/status")).json()
            durable_live = DataSessionService(handle.root).get_session(session_id)
            stopped = await client.post("/capture/stop")
            settled = (await client.get("/recorder/status")).json()
            durable_settled = DataSessionService(handle.root).get_session(session_id)

    assert started.status_code == 202, started.text
    assert live["active"] is True
    assert live["lifecycle_state"] == "recording"
    assert live["finalization_state"] == "idle"
    assert live["durable_session_state"] == "active"
    assert live["recorded_frame_count"] > 0
    assert durable_live.state is DataSessionState.ACTIVE

    assert stopped.json() == {"status": "stopped", "finalization_pending": False}
    assert settled["active"] is False
    assert settled["lifecycle_state"] == "stopped"
    assert settled["finalization_state"] == "settled"
    assert settled["durable_session_state"] == "completed"
    assert settled["data_session_id"] == session_id
    assert settled["recorded_frame_count"] > 0
    assert durable_settled.state is DataSessionState.COMPLETED
    assert durable_settled.frame_count == settled["recorded_frame_count"]
    assert durable_settled.segment_count > 0


async def test_a_pending_finalization_is_reported_as_pending_and_never_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        parked = ParkedFinalize(monkeypatch)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_body(handle.root))
            session_id = started.json()["data_session_id"]
            await asyncio.sleep(0.1)
            stop_task = asyncio.create_task(client.post("/capture/stop"))
            try:
                await parked.wait_entered()
                pending = (await client.get("/recorder/status")).json()
                assert pending["finalization_state"] == "pending"
                assert pending["durable_session_state"] == "active"
                assert pending["durable_session_state"] not in ("completed", "failed")
                assert pending["active"] is False
                assert service.finalization_pending is True
                # The durable row still says ACTIVE: no verdict has been invented.
                still_active = DataSessionService(handle.root).get_session(session_id)
                assert still_active.state is DataSessionState.ACTIVE
            finally:
                parked.release.set()
            stopped = await stop_task
            settled = (await client.get("/recorder/status")).json()

    assert stopped.status_code == 200, stopped.text
    assert settled["finalization_state"] == "settled"
    assert settled["durable_session_state"] == "completed"


async def test_a_storage_failure_remains_a_failure_and_is_never_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_append(writer: DataSessionWriter, batch: FrameBatch) -> None:
        raise DataStorageError(
            "The committed segment could not be registered.",
            code="data.session.registration_failed",
        )

    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        monkeypatch.setattr(DataSessionWriter, "append", refuse_append)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_body(handle.root))
            session_id = started.json()["data_session_id"]
            await asyncio.sleep(0.15)
            await client.post("/capture/stop")
            status = (await client.get("/recorder/status")).json()

        stored = DataSessionService(handle.root).get_session(session_id)

    assert status["lifecycle_state"] == "failed"
    assert status["durable_session_state"] == "failed"
    assert status["durable_session_state"] != "completed"
    assert status["failure"] is not None
    assert status["failure"]["code"] == "recorder.write_failed"
    assert status["failure"]["recoverable"] is False
    assert stored.state is DataSessionState.FAILED


async def test_reading_the_recorder_status_does_not_change_the_recording(
    tmp_path: Path,
) -> None:
    """Repeated observability reads leave a live recording running and durable."""
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_body(handle.root))
            session_id = started.json()["data_session_id"]
            snapshots = []
            for _ in range(5):
                snapshots.append((await client.get("/recorder/status")).json())
                await asyncio.sleep(0.02)
            assert service.has_session is True
            assert service.capture_state.value == "running"
            assert DataSessionService(handle.root).get_session(session_id).state is (
                DataSessionState.ACTIVE
            )
            await client.post("/capture/stop")

    assert all(snapshot["active"] is True for snapshot in snapshots)
    assert all(snapshot["data_session_id"] == session_id for snapshot in snapshots)
    assert all(snapshot["durable_session_state"] == "active" for snapshot in snapshots)
    assert all(snapshot["lifecycle_state"] == "recording" for snapshot in snapshots)
    assert snapshots[-1]["recorded_frame_count"] >= snapshots[0]["recorded_frame_count"]


async def test_a_recorded_session_is_queryable_after_the_capture_stops(
    tmp_path: Path,
) -> None:
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_body(handle.root))
            session_id = started.json()["data_session_id"]
            await asyncio.sleep(0.1)
            await client.post("/capture/stop")
            listed = (
                await client.get(
                    "/recorder/sessions", params={"project_path": str(handle.root)}
                )
            ).json()
            read = (
                await client.get(
                    f"/recorder/sessions/{session_id}",
                    params={"project_path": str(handle.root)},
                )
            ).json()

    assert [session["session_id"] for session in listed["sessions"]] == [session_id]
    assert listed["sessions"][0]["state"] == "completed"
    assert read["session_id"] == session_id
    assert read["state"] == "completed"
    assert read["frame_count"] > 0
    assert read["segment_count"] > 0


async def test_consecutive_captures_are_distinct_and_deterministically_ordered(
    tmp_path: Path,
) -> None:
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            session_ids = []
            for _ in range(2):
                started = await client.post("/capture/start", json=_body(handle.root))
                session_ids.append(started.json()["data_session_id"])
                await asyncio.sleep(0.08)
                await client.post("/capture/stop")
            first = (
                await client.get(
                    "/recorder/sessions", params={"project_path": str(handle.root)}
                )
            ).json()
            second = (
                await client.get(
                    "/recorder/sessions", params={"project_path": str(handle.root)}
                )
            ).json()

    order = [session["session_id"] for session in first["sessions"]]
    assert order == session_ids
    assert [session["session_id"] for session in second["sessions"]] == order
    assert all(session["state"] == "completed" for session in first["sessions"])
