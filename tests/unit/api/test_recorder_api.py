"""HTTP contract for the Recorder control-plane and persisted-session surface.

Two questions over one headless router:

```text
GET /recorder/status                          what the runtime's recorder is doing
GET /recorder/sessions?project_path=…          which sessions a project holds
GET /recorder/sessions/{session_id}?…          one session's durable metadata
```

Four properties are pinned deliberately:

* **observability is not authority.** ``/recorder/status`` reports what the runtime
  and the recorder already hold durably; it never starts, stops or otherwise
  changes a recording, and it never owns CAN capture. Nothing under ``/recorder``
  accepts a ``POST``.
* **the durable row stays the truth.** A session's ``state`` is read from the
  project database through :class:`~canx.data.session.DataSessionService`, never
  inferred by scanning Parquet data. ``ACTIVE`` / ``COMPLETED`` / ``INTERRUPTED`` /
  ``FAILED`` are reported as they are; ``PENDING`` is never guessed into a verdict.
* **every domain failure keeps its own code.** An unknown session is a typed
  ``404 data.session.not_found``; a malformed one is a ``400``; a directory that is
  not a CAN-X project is a ``400 project.*`` — each in the shared five-field
  envelope.
* **the response is bounded and deterministic.** The session list is ordered by the
  data domain's canonical ``(started_at, session_id)`` and bounded by ``limit``,
  with ``has_more`` reporting truncation.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from datetime import UTC
from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient

#: A generous stop budget: these tests finalize reasonable recordings, and the
#: 1 s production default assumes realistic segment sizes.
CLEANUP_TIMEOUT_SECONDS = 6.0

#: The five fields SPEC §38 requires, and nothing else.
ENVELOPE_FIELDS = {"code", "message", "details", "recoverable", "source"}

REQUEST_VALIDATION_CODE = "api.request_validation_failed"

STATUS_FIELDS = {
    "active",
    "stream_id",
    "data_session_id",
    "lifecycle_state",
    "recorded_frame_count",
    "failure",
    "finalization_state",
    "durable_session_state",
}

SESSION_FIELDS = {
    "session_id",
    "project_id",
    "stream_id",
    "state",
    "started_at",
    "ended_at",
    "frame_count",
    "segment_count",
    "first_sequence",
    "last_sequence",
    "first_timestamp",
    "last_timestamp",
    "updated_at",
}


def frame(sequence: int) -> Frame:
    """Build a literal valid frame whose timestamps track its sequence."""
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


def _batch(stream_id: str, count: int) -> FrameBatch:
    return FrameBatch.create(
        stream_id=stream_id, frames=[frame(sequence) for sequence in range(count)]
    )


def _project(tmp_path: Path, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _seed_session(
    root: Path, *, stream_id: str, frames: int = 5, max_frames_per_segment: int = 64
) -> str:
    """Write one real data session straight through the data domain."""
    service = DataSessionService(root, max_frames_per_segment=max_frames_per_segment)
    writer = service.start(stream_id=stream_id)
    if frames:
        writer.append(_batch(stream_id, frames))
    writer.finalize()
    return writer.session_id


def _start_capture_body(root: Path, *, rate_hz: float = 2_000) -> dict[str, object]:
    return {"rate_hz": rate_hz, "batch_size": 10, "project_path": str(root)}


# --- the recorder status model -------------------------------------------------


async def test_no_active_recording_reports_an_idle_recorder() -> None:
    """Before anything runs, the recorder answers with the whole, exact shape."""
    async with _client(create_app()) as client:
        response = await client.get("/recorder/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == STATUS_FIELDS
    assert body == {
        "active": False,
        "stream_id": None,
        "data_session_id": None,
        "lifecycle_state": "idle",
        "recorded_frame_count": 0,
        "failure": None,
        "finalization_state": "idle",
        "durable_session_state": None,
    }


async def test_a_project_backed_recording_reports_its_data_session_identity(
    tmp_path: Path,
) -> None:
    """The identity a caller was handed at start is the one the status reports."""
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_start_capture_body(handle.root))
            await asyncio.sleep(0.1)
            status = (await client.get("/recorder/status")).json()
            await client.post("/capture/stop")

    assert started.status_code == 202, started.text
    identity = started.json()
    assert status["active"] is True
    assert status["data_session_id"] == identity["data_session_id"]
    assert status["stream_id"] == identity["stream_id"]
    assert status["lifecycle_state"] == "recording"
    assert status["durable_session_state"] == "active"
    assert status["finalization_state"] == "idle"
    assert status["failure"] is None


async def test_the_status_reports_the_recorded_frame_count(tmp_path: Path) -> None:
    """The recorder's own committed frame counter is observable while it runs."""
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            await client.post("/capture/start", json=_start_capture_body(handle.root))
            await asyncio.sleep(0.1)
            status = (await client.get("/recorder/status")).json()
            await client.post("/capture/stop")

    assert status["recorded_frame_count"] > 0


async def test_a_healthy_stop_settles_finalization_and_reports_completed(
    tmp_path: Path,
) -> None:
    """A settled stop reports the durable verdict, and never invents another."""
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_start_capture_body(handle.root))
            await asyncio.sleep(0.05)
            stopped = await client.post("/capture/stop")
            status = (await client.get("/recorder/status")).json()

    assert stopped.json() == {"status": "stopped", "finalization_pending": False}
    assert status["active"] is False
    assert status["lifecycle_state"] == "stopped"
    assert status["finalization_state"] == "settled"
    assert status["durable_session_state"] == "completed"
    assert status["data_session_id"] == started.json()["data_session_id"]
    assert status["failure"] is None


async def test_a_capture_without_a_project_reports_no_data_session() -> None:
    """A realtime-only capture is real, and honestly has no durable session.

    With no recording target there is nothing being recorded, so the recorder
    state stays ``idle`` even though capture ingress is ``active``: capture and
    recording are different facts, and the control plane must not conflate them.
    """
    service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
    async with _client(create_app(runtime_service=service)) as client:
        await client.post("/capture/start", json={"rate_hz": 1_000, "batch_size": 10})
        await asyncio.sleep(0.05)
        status = (await client.get("/recorder/status")).json()
        await client.post("/capture/stop")

    assert status["active"] is True
    assert status["data_session_id"] is None
    assert status["durable_session_state"] is None
    assert status["lifecycle_state"] == "idle"


# --- observability is not authority -------------------------------------------


async def test_a_status_query_does_not_change_the_recorder_state(tmp_path: Path) -> None:
    """Reading the control plane must not steer it."""
    with _project(tmp_path) as handle:
        service = RuntimeService(recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT_SECONDS)
        async with _client(create_app(runtime_service=service)) as client:
            started = await client.post("/capture/start", json=_start_capture_body(handle.root))
            session_id = started.json()["data_session_id"]
            for _ in range(3):
                await client.get("/recorder/status")
            assert service.has_session is True
            assert service.capture_state.value == "running"
            durable = DataSessionService(handle.root).get_session(session_id)
            final = (await client.get("/recorder/status")).json()
            await client.post("/capture/stop")

    assert durable.state is DataSessionState.ACTIVE
    assert final["active"] is True
    assert final["data_session_id"] == session_id
    assert final["durable_session_state"] == "active"


async def test_the_recorder_router_never_owns_can_capture(tmp_path: Path) -> None:
    """No endpoint under ``/recorder`` can start or stop a capture.

    Asserted behaviourally rather than by introspecting the route table: the
    surface is read-only (a ``POST`` is refused), and querying it any number of
    times never creates a data session in a project.
    """
    with _project(tmp_path) as handle:
        async with _client(create_app()) as client:
            refused = [
                (await client.post("/recorder/status")).status_code,
                (await client.post("/recorder/sessions")).status_code,
                (
                    await client.post(
                        "/recorder/sessions/2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
                    )
                ).status_code,
            ]
            for _ in range(3):
                assert (await client.get("/recorder/status")).status_code == 200
        assert refused == [405, 405, 405]
        assert DataSessionService(handle.root).list_sessions() == ()


async def test_the_recorder_surface_is_headless_and_ui_independent() -> None:
    """The surface serves without any desktop, and imports no UI module."""
    import canx.api.recorder as recorder_module

    source = Path(recorder_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("PyQt", "apps.desktop", "canx.desktop", "tauri"):
        assert forbidden not in source, forbidden
    assert not any(name.startswith(("PyQt", "canx.desktop")) for name in sys.modules)

    async with _client(create_app()) as client:
        response = await client.get("/recorder/status")
    assert response.status_code == 200


# --- the persisted session surface --------------------------------------------


async def test_a_completed_session_reports_its_durable_metadata(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _seed_session(handle.root, stream_id="stream-a", frames=7)
        durable = DataSessionService(handle.root).get_session(session_id)
        async with _client(create_app()) as client:
            response = await client.get(
                f"/recorder/sessions/{session_id}",
                params={"project_path": str(handle.root)},
            )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == SESSION_FIELDS
    assert body["session_id"] == session_id
    assert body["project_id"] == durable.project_id
    assert body["stream_id"] == "stream-a"
    assert body["state"] == "completed"
    assert body["frame_count"] == durable.frame_count == 7
    assert body["segment_count"] == durable.segment_count == 1
    assert body["first_sequence"] == 0
    assert body["last_sequence"] == 6
    assert body["started_at"] == durable.started_at.astimezone(UTC).isoformat()
    assert body["updated_at"] == durable.updated_at.astimezone(UTC).isoformat()
    assert body["ended_at"] is not None


async def test_an_active_session_is_reported_as_active(tmp_path: Path) -> None:
    """``ACTIVE`` is a durable state, not a runtime guess."""
    with _project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id="stream-a")
        async with _client(create_app()) as client:
            response = await client.get(
                f"/recorder/sessions/{writer.session_id}",
                params={"project_path": str(handle.root)},
            )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"
    assert response.json()["ended_at"] is None


async def test_a_failed_session_is_reported_as_failed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id="stream-a")
        assert service.fail_active_session(writer.session_id) is True
        async with _client(create_app()) as client:
            response = await client.get(
                f"/recorder/sessions/{writer.session_id}",
                params={"project_path": str(handle.root)},
            )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "failed"


async def test_the_session_view_publishes_no_filesystem_location(tmp_path: Path) -> None:
    """Identity and metadata only; never a path the caller did not supply."""
    with _project(tmp_path) as handle:
        session_id = _seed_session(handle.root, stream_id="stream-a")
        async with _client(create_app()) as client:
            response = await client.get(
                f"/recorder/sessions/{session_id}",
                params={"project_path": str(handle.root)},
            )

    rendered = json.dumps(response.json())
    assert str(handle.root) not in rendered
    assert handle.root.name not in rendered
    lowered = rendered.lower()
    for marker in ("parquet", "relative_path", "project.db", "project.json", "data/sessions"):
        assert marker not in lowered, marker


async def test_multiple_historical_sessions_are_listed_in_deterministic_order(
    tmp_path: Path,
) -> None:
    """The list is ordered by the data domain's canonical order, every time."""
    with _project(tmp_path) as handle:
        expected: list[str] = []
        for index in range(3):
            expected.append(_seed_session(handle.root, stream_id=f"stream-{index}", frames=1))
            time.sleep(0.005)
        async with _client(create_app()) as client:
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
    assert order == expected
    assert [session["session_id"] for session in second["sessions"]] == order
    assert all(session["state"] == "completed" for session in first["sessions"])
    assert first["project_id"] == second["project_id"]


async def test_the_session_list_is_bounded_and_reports_truncation(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        for index in range(3):
            _seed_session(handle.root, stream_id=f"stream-{index}", frames=1)
        async with _client(create_app()) as client:
            bounded = (
                await client.get(
                    "/recorder/sessions",
                    params={"project_path": str(handle.root), "limit": 2},
                )
            ).json()
            full = (
                await client.get(
                    "/recorder/sessions",
                    params={"project_path": str(handle.root), "limit": 10},
                )
            ).json()

    assert len(bounded["sessions"]) == 2
    assert bounded["limit"] == 2
    assert bounded["has_more"] is True
    assert len(full["sessions"]) == 3
    assert full["has_more"] is False


async def test_an_empty_project_lists_no_sessions(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        async with _client(create_app()) as client:
            response = await client.get(
                "/recorder/sessions", params={"project_path": str(handle.root)}
            )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sessions"] == []
    assert body["has_more"] is False
    assert body["project_id"]


# --- typed failures ------------------------------------------------------------


async def test_an_unknown_session_is_not_found(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        async with _client(create_app()) as client:
            response = await client.get(
                "/recorder/sessions/2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
                params={"project_path": str(handle.root)},
            )

    assert response.status_code == 404
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "data.session.not_found"
    assert body["source"] == "data"
    assert body["recoverable"] is False


async def test_a_malformed_session_id_is_a_validation_failure(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        async with _client(create_app()) as client:
            response = await client.get(
                "/recorder/sessions/not-a-uuid",
                params={"project_path": str(handle.root)},
            )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "data.session.invalid_session_id"
    assert body["source"] == "data"


async def test_an_invalid_project_is_rejected(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-project"
    plain.mkdir()
    async with _client(create_app()) as client:
        listed = await client.get("/recorder/sessions", params={"project_path": str(plain)})
        read = await client.get(
            "/recorder/sessions/2f1c3d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
            params={"project_path": str(plain)},
        )

    for response in (listed, read):
        assert response.status_code == 400
        body = response.json()
        assert set(body) == ENVELOPE_FIELDS
        assert body["code"] == "project.manifest_missing"
        assert body["source"] == "project"


async def test_an_absent_project_is_rejected(tmp_path: Path) -> None:
    async with _client(create_app()) as client:
        response = await client.get(
            "/recorder/sessions", params={"project_path": str(tmp_path / "absent.canx")}
        )

    assert response.status_code == 400
    assert response.json()["code"] == "project.not_found"


async def test_a_missing_project_path_is_a_request_validation_failure() -> None:
    async with _client(create_app()) as client:
        missing = await client.get("/recorder/sessions")
        empty = await client.get("/recorder/sessions", params={"project_path": ""})

    for response in (missing, empty):
        assert response.status_code == 422
        body = response.json()
        assert set(body) == ENVELOPE_FIELDS
        assert body["code"] == REQUEST_VALIDATION_CODE
        assert body["source"] == "api"


async def test_an_out_of_range_limit_is_a_request_validation_failure(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        async with _client(create_app()) as client:
            too_small = await client.get(
                "/recorder/sessions", params={"project_path": str(handle.root), "limit": 0}
            )
            too_large = await client.get(
                "/recorder/sessions", params={"project_path": str(handle.root), "limit": 100_000}
            )

    for response in (too_small, too_large):
        assert response.status_code == 422
        assert response.json()["code"] == REQUEST_VALIDATION_CODE


async def test_a_domain_failure_is_never_downgraded_to_a_server_error() -> None:
    """Every failure keeps the shared envelope and a non-500 status."""
    async with _client(create_app()) as client:
        response = await client.get(
            "/recorder/sessions/not-a-uuid", params={"project_path": "/definitely/absent"}
        )

    assert response.status_code < 500
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    for leaked in ("traceback", "sqlite", "pyarrow", "ValueError"):
        assert leaked not in str(body).lower()


# --- the blocking boundary -----------------------------------------------------


async def test_the_session_queries_run_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project open and a SQLite read must never run on the serving loop."""
    import canx.api.recorder as recorder_module

    threads: list[int] = []
    real = recorder_module._open_project

    def spy(project_path: str) -> object:
        threads.append(threading.get_ident())
        return real(project_path)

    monkeypatch.setattr(recorder_module, "_open_project", spy)
    loop_thread = threading.get_ident()

    with _project(tmp_path) as handle:
        session_id = _seed_session(handle.root, stream_id="stream-a")
        async with _client(create_app()) as client:
            await client.get("/recorder/sessions", params={"project_path": str(handle.root)})
            await client.get(
                f"/recorder/sessions/{session_id}",
                params={"project_path": str(handle.root)},
            )

    assert threads
    assert all(thread != loop_thread for thread in threads)
