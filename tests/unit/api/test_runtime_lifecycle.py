"""Behavior tests for runtime lifecycle control endpoints."""

import asyncio
from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.capture.subscriber import SubscriberFailure
from canx.domain.batch import FrameBatch
from canx.recorder.msgpack_recorder import MsgpackRecorder
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient


async def test_runtime_status_is_ready_without_an_active_capture() -> None:
    """The desktop can distinguish a ready idle runtime."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as client:
        response = await client.get("/runtime/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "ready",
        "capture_active": False,
        "capture_state": "idle",
        "failure": None,
    }


async def test_shutdown_rejects_a_missing_session_token() -> None:
    """An unrelated loopback process cannot stop the runtime."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app(session_token="secret")),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/runtime/shutdown")

    assert response.status_code == 403
    assert response.json() == {
        "code": "runtime.invalid_session_token",
        "message": "The runtime session token is invalid.",
        "details": {},
        "recoverable": False,
        "source": "runtime",
    }


async def test_shutdown_invokes_the_owned_server_callback() -> None:
    """A valid owner token initiates graceful server shutdown exactly once."""
    calls = 0

    def request_shutdown() -> None:
        nonlocal calls
        calls += 1

    app = create_app(session_token="secret", shutdown_callback=request_shutdown)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/runtime/shutdown", headers={"X-CANX-Session-Token": "secret"}
        )

    assert response.status_code == 202
    assert response.json() == {"status": "stopping"}
    assert calls == 1


async def test_backpressure_degrades_recording_while_capture_and_stream_continue(
    tmp_path: Path,
) -> None:
    service = RuntimeService()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(runtime_service=service)),
        base_url="http://testserver",
    ) as client:
        await client.post(
            "/capture/start",
            json={
                "batch_size": 1,
                "rate_hz": 100,
                "recording_path": str(tmp_path / "capture.canxmsg"),
            },
        )
        try:
            running = (await client.get("/runtime/status")).json()
            assert running["capture_state"] == "running"
            assert running["failure"] is None
            failure = SubscriberFailure(
                "subscriber.backpressure", "archive", "queue full", False, 1000, 7, 99
            )
            service._handle_subscriber_failure(failure)
            service._handle_subscriber_failure(failure)
            async with service.broker.subscribe() as queue:
                streamed = await asyncio.wait_for(queue.get(), timeout=1)
                assert streamed.frame_count > 0
            status = (await client.get("/runtime/status")).json()
            assert status["state"] == "ready"
            assert status["capture_active"] is True
            assert status["capture_state"] == "degraded"
            assert status["failure"]["code"] == "recorder.backpressure"
            assert status["failure"]["details"]["frame_sequence"] == 99
            assert (await client.get("/health")).json()["status"] == "ready"
            metrics = (await client.get("/metrics")).json()
            assert metrics["recorder_state"] == "failed"
            assert metrics["recorder_backpressure_events"] == 1
            assert metrics["recorder_failures"] == 1
            assert metrics["recorder_uncommitted_frames"] >= 8
            assert metrics["recorder_failure_code"] == "recorder.backpressure"
        finally:
            await service.stop_capture()
        status = (await client.get("/runtime/status")).json()
        assert status["state"] == "ready"
        assert status["capture_state"] == "failed"
        assert status["capture_active"] is False


class BrokenRecorder(MsgpackRecorder):
    async def append(self, batch: FrameBatch) -> None:
        raise OSError("disk disconnected")


async def test_background_write_error_is_consumed_and_exposed(tmp_path: Path) -> None:
    service = RuntimeService(recorder=BrokenRecorder())
    async with AsyncClient(
        transport=ASGITransport(app=create_app(runtime_service=service)),
        base_url="http://testserver",
    ) as client:
        await client.post(
            "/capture/start",
            json={
                "batch_size": 1,
                "rate_hz": 100,
                "recording_path": str(tmp_path / "capture.canxmsg"),
            },
        )
        try:
            async with asyncio.timeout(1):
                while (
                    service.capture_active and service.metrics_snapshot().recorder_state != "failed"
                ):
                    await asyncio.sleep(0.001)
            status = (await client.get("/runtime/status")).json()
            assert status["capture_state"] == "degraded"
            assert status["capture_active"] is True
            assert status["failure"]["code"] == "recorder.write_failed"
            assert status["failure"]["message"] == "disk disconnected"
            metrics = (await client.get("/metrics")).json()
            assert metrics["recorder_failures"] == 1
            assert metrics["recorder_backpressure_events"] == 0
            assert metrics["recorder_uncommitted_frames"] >= 1
        finally:
            await service.stop_capture()
        assert service.has_session is False


async def test_failed_recorder_start_releases_session_and_preserves_diagnostic(
    tmp_path: Path,
) -> None:
    from canx.devices.virtual import VirtualAdapterConfig

    service = RuntimeService()
    with pytest.raises(OSError):
        await service.start_capture(
            VirtualAdapterConfig(), recording_path=tmp_path / "missing" / "x"
        )
    assert service.has_session is False
    assert service.capture_state == "failed"
    assert service.failure is not None
    assert service.failure.code == "recorder.open_failed"
    await service.start_capture(VirtualAdapterConfig(), recording_path=tmp_path / "next.canxmsg")
    try:
        assert service.capture_state == "running"
        assert service.failure is None
    finally:
        await service.stop_capture()


async def test_flush_error_still_releases_runtime_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from canx.devices.virtual import VirtualAdapterConfig

    recorder = MsgpackRecorder()
    service = RuntimeService(recorder=recorder)
    await service.start_capture(VirtualAdapterConfig(), recording_path=tmp_path / "capture.canxmsg")
    assert recorder._file is not None

    def fail_flush() -> None:
        raise OSError("flush failed")

    monkeypatch.setattr(recorder._file, "flush", fail_flush)
    await service.stop_capture()
    assert service.has_session is False
    assert service.capture_state == "failed"
    assert service.failure is not None
    assert service.failure.code == "recorder.flush_failed"
    assert service.metrics_snapshot().recorder_failures == 1
    await service.stop_capture()


async def test_disabled_archive_settles_before_backpressure_callback(tmp_path: Path) -> None:
    from canx.devices.virtual import VirtualAdapterConfig

    service = RuntimeService()
    await service.start_capture(
        VirtualAdapterConfig(rate_hz=1), recording_path=tmp_path / "capture.canxmsg"
    )
    try:
        assert service._archive_subscriber is not None
        assert service._pipeline is not None
        service._pipeline.unsubscribe("archive")
        service._archive_subscriber.disable()
        assert service._archive_task is not None
        await asyncio.wait_for(service._archive_task, timeout=1)
        service._handle_subscriber_failure(
            SubscriberFailure("subscriber.backpressure", "archive", "queue full",
                              False, 1000, 7, 99)
        )
        assert service.capture_state == "degraded"
        assert service.capture_active is True
        assert service.metrics_snapshot().recorder_failures == 1
    finally:
        await service.stop_capture()
    assert service.has_session is False
    assert service.capture_state == "failed"
    assert service.failure is not None
    assert service.failure.code == "recorder.backpressure"
    assert service.metrics_snapshot().recorder_failures == 1


async def test_unrecorded_session_does_not_inherit_a_previous_recorder_failure(
    tmp_path: Path,
) -> None:
    from canx.devices.virtual import VirtualAdapterConfig

    service = RuntimeService()
    with pytest.raises(OSError):
        await service.start_capture(
            VirtualAdapterConfig(), recording_path=tmp_path / "missing" / "capture.canxmsg"
        )

    stream_id = await service.start_capture(VirtualAdapterConfig(), batch_size=1)
    try:
        assert service.capture_state == "running"
        assert service.failure is None
        assert service._recorder.state == "idle"
        assert service._recorder.failure is None

        service._handle_subscriber_failure(
            SubscriberFailure("subscriber.backpressure", "archive", "queue full", False, 1000, 7, 99)
        )

        assert service.failure is not None
        assert service.failure.code == "recorder.backpressure"
        assert service.failure.context["stream_id"] == stream_id
    finally:
        await service.stop_capture()
