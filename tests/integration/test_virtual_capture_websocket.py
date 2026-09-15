"""Real-server proof of the Virtual CAN control/data-plane chain."""

import asyncio
from contextlib import suppress

import uvicorn
from canx.api.app import create_app
from canx.runtime.service import RuntimeService
from canx.transport.msgpack_codec import decode_batch
from httpx import AsyncClient
from net import find_free_port
from websockets.asyncio.client import connect


async def test_virtual_capture_reaches_a_binary_websocket_client() -> None:
    port = find_free_port()
    service = RuntimeService()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(runtime_service=service), host="127.0.0.1", port=port, log_level="error"
        )
    )
    server_task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)

    try:
        async with connect(f"ws://127.0.0.1:{port}/stream/frames") as websocket:
            await service.broker.wait_for_subscribers(1)
            async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                response = await client.post(
                    "/capture/start", json={"rate_hz": 2_000, "batch_size": 25, "seed": 17}
                )
                assert response.status_code == 202
                payload = await asyncio.wait_for(websocket.recv(), timeout=2)
                assert isinstance(payload, bytes)
                assert decode_batch(payload).frame_count > 0
                assert (await client.post("/capture/stop")).status_code == 200
                assert (await client.get("/metrics")).json()["streamed_frames"] > 0
    finally:
        server.should_exit = True
        with suppress(asyncio.TimeoutError):
            async with asyncio.timeout(5):
                await server_task
