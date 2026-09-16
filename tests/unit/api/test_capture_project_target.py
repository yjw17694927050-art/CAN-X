"""Control-plane contract for a project-backed capture start.

The desktop must be able to ask the runtime to record into a CAN-X project and
learn the resulting data session without guessing, and it must never be able to
make the runtime silently pick one of two recording targets.
"""

import asyncio
from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.project.service import ProjectHandle, ProjectService
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


async def test_a_project_capture_reports_the_data_session_it_created(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=16)
        app = create_app(runtime_service=service)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            started = await client.post(
                "/capture/start",
                json={
                    "rate_hz": 2_000,
                    "batch_size": 10,
                    "project_path": str(handle.root),
                },
            )
            await asyncio.sleep(0.1)
            stopped = await client.post("/capture/stop")

        assert started.status_code == 202
        body = started.json()
        assert body["status"] == "started"
        assert stopped.status_code == 200

        session_id = body["data_session_id"]
        assert session_id is not None
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.stream_id == body["stream_id"]
        assert stored.frame_count > 0
        assert stored.segment_count > 0


async def test_a_healthy_stop_reports_no_pending_finalization(tmp_path: Path) -> None:
    """A settled stop must not leave a caller waiting for an outcome that exists.

    ``status`` says ingress stopped; ``finalization_pending`` says whether the
    durable terminal state is still being resolved. On a healthy capture the two
    must agree that nothing is outstanding, and the status endpoint must report
    the settled capture state right after.
    """
    with project(tmp_path) as handle:
        app = create_app(runtime_service=RuntimeService(project_max_frames_per_segment=16))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            await client.post(
                "/capture/start",
                json={"rate_hz": 1_000, "batch_size": 10, "project_path": str(handle.root)},
            )
            await asyncio.sleep(0.05)
            stopped = await client.post("/capture/stop")
            status = (await client.get("/runtime/status")).json()

        assert stopped.status_code == 200
        assert stopped.json() == {"status": "stopped", "finalization_pending": False}
        assert status["capture_state"] == "idle"
        assert status["failure"] is None


async def test_a_capture_without_a_project_reports_a_null_data_session() -> None:
    app = create_app(runtime_service=RuntimeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        started = await client.post("/capture/start", json={"rate_hz": 1_000, "batch_size": 10})
        await client.post("/capture/stop")

    assert started.status_code == 202
    assert started.json()["data_session_id"] is None
    assert started.json()["stream_id"]


async def test_two_recording_targets_are_a_structured_validation_error(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService()
        async with AsyncClient(
            transport=ASGITransport(app=create_app(runtime_service=service)),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/capture/start",
                json={
                    "project_path": str(handle.root),
                    "recording_path": str(tmp_path / "capture.canxmsg"),
                },
            )

        assert response.status_code == 400
        assert response.json() == {
            "code": "capture.recording_target_conflict",
            "message": (
                "A capture cannot target a recording file and a CAN-X project at the same time."
            ),
            "details": {
                "project_path": str(handle.root),
                "recording_path": str(tmp_path / "capture.canxmsg"),
            },
            "recoverable": True,
            "source": "runtime",
        }
        assert service.has_session is False
        assert DataSessionService(handle.root).list_sessions() == ()


async def test_an_invalid_project_target_is_a_structured_error(tmp_path: Path) -> None:
    plain_directory = tmp_path / "not-a-project"
    plain_directory.mkdir()
    service = RuntimeService()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(runtime_service=service)),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/capture/start", json={"project_path": str(plain_directory)}
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "capture.invalid_project"
    assert body["source"] == "runtime"
    assert body["recoverable"] is False
    assert body["details"]["cause"] == "project.manifest_missing"
    assert sorted(path.name for path in plain_directory.iterdir()) == []


async def test_an_error_response_never_leaks_a_storage_exception(tmp_path: Path) -> None:
    """A rejected target answers with the typed contract, not a 500."""
    service = RuntimeService()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(runtime_service=service)),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/capture/start", json={"project_path": str(tmp_path / "absent.canx")}
        )

    assert response.status_code == 400
    assert set(response.json()) == {"code", "message", "details", "recoverable", "source"}


async def test_a_project_that_cannot_open_a_data_session_answers_with_the_typed_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import canx.data.session as session_module
    from canx.data.errors import DataStorageError

    def fail_start(self: DataSessionService, *, stream_id: str) -> object:
        raise DataStorageError(
            "The project database is missing.", code="data.project_database_missing"
        )

    with project(tmp_path) as handle:
        monkeypatch.setattr(session_module.DataSessionService, "start", fail_start)
        async with AsyncClient(
            transport=ASGITransport(app=create_app(runtime_service=RuntimeService())),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/capture/start", json={"project_path": str(handle.root)}
            )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "capture.session_start_failed"
    assert body["recoverable"] is True
    assert body["source"] == "runtime"
    assert body["details"]["cause"] == "data.project_database_missing"
    assert "sqlite" not in body["message"].lower()
