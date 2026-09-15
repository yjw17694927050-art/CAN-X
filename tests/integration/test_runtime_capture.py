import asyncio
from pathlib import Path

from canx.api.app import create_app
from canx.devices.virtual import VirtualAdapterConfig
from canx.runtime.service import RuntimeService
from httpx import ASGITransport, AsyncClient


async def test_control_plane_runs_headless_virtual_capture_and_metrics() -> None:
    service = RuntimeService()
    app = create_app(runtime_service=service)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        started = await client.post(
            "/capture/start",
            json={"channel_count": 4, "is_fd": True, "rate_hz": 2_000, "seed": 7, "batch_size": 50},
        )
        assert started.status_code == 202
        await asyncio.sleep(0.08)
        status = await client.get("/runtime/status")
        assert status.json()["capture_active"] is True
        active_metrics = (await client.get("/metrics")).json()
        stopped = await client.post("/capture/stop")
        metrics = (await client.get("/metrics")).json()

    assert stopped.status_code == 200
    assert metrics["generated_frames"] > 0
    assert metrics["captured_frames"] == metrics["generated_frames"]
    assert active_metrics["active_channels"] == 4
    assert metrics["active_channels"] == 0


async def test_capture_start_conflict_is_structured() -> None:
    service = RuntimeService()
    app = create_app(runtime_service=service)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert (await client.post("/capture/start", json={})).status_code == 202
        conflict = await client.post("/capture/start", json={})
        await client.post("/capture/stop")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "capture.already_running"


async def test_application_lifespan_flushes_an_active_capture(tmp_path: Path) -> None:
    path = tmp_path / "lifespan.canxmsg"
    service = RuntimeService()
    app = create_app(runtime_service=service)

    async with app.router.lifespan_context(app):
        await service.start_capture(
            VirtualAdapterConfig(rate_hz=2_000), recording_path=path, batch_size=25
        )
        await asyncio.sleep(0.05)

    assert service.capture_active is False
    assert path.stat().st_size > 8


async def test_new_capture_session_clears_the_bounded_query_history() -> None:
    service = RuntimeService()
    await service.start_capture(VirtualAdapterConfig(rate_hz=2_000), batch_size=25)
    await asyncio.sleep(0.05)
    await service.stop_capture()
    assert service.frames()

    await service.start_capture(VirtualAdapterConfig(rate_hz=1), batch_size=25)
    assert service.frames() == ()
    await service.stop_capture()


async def test_tauri_origin_can_call_the_capture_control_plane() -> None:
    app = create_app(runtime_service=RuntimeService())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.options(
            "/capture/start",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
