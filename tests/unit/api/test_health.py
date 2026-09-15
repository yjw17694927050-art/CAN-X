"""Behavior tests for the runtime health boundary."""

from canx.api.app import create_app
from httpx import ASGITransport, AsyncClient


async def test_health_reports_ready_runtime() -> None:
    """A process supervisor can distinguish a ready CAN-X runtime."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "canx-runtime",
        "schema_version": 1,
    }
