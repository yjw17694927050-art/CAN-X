"""Behavior tests for runtime lifecycle control endpoints."""

from canx.api.app import create_app
from httpx import ASGITransport, AsyncClient


async def test_runtime_status_is_ready_without_an_active_capture() -> None:
    """The desktop can distinguish a ready idle runtime."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as client:
        response = await client.get("/runtime/status")

    assert response.status_code == 200
    assert response.json() == {"state": "ready", "capture_active": False}


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
