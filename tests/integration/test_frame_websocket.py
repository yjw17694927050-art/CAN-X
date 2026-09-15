"""End-to-end binary WebSocket test against a real ASGI server."""

import asyncio
from contextlib import suppress

import pytest
import uvicorn
from canx.api.app import create_app
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.transport.broker import BatchBroker
from canx.transport.msgpack_codec import decode_batch
from net import find_free_port
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError


async def test_runtime_streams_a_binary_messagepack_batch() -> None:
    port = find_free_port()
    broker = BatchBroker(client_capacity=2)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(batch_broker=broker), host="127.0.0.1", port=port, log_level="error"
        )
    )
    server_task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)

    frame = Frame(
        sequence=0,
        channel_id="can0",
        arbitration_id=0x123,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=b"\x7f",
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1.0,
        normalized_timestamp=0.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )
    batch = FrameBatch.create(stream_id="stream-1", frames=[frame])

    try:
        async with connect(f"ws://127.0.0.1:{port}/stream/frames") as websocket:
            await broker.wait_for_subscribers(1)
            await broker.publish(batch)
            payload = await websocket.recv()
            assert isinstance(payload, bytes)
            assert decode_batch(payload) == batch
    finally:
        server.should_exit = True
        with suppress(asyncio.TimeoutError):
            async with asyncio.timeout(5):
                await server_task


async def test_frame_stream_rejects_client_text_messages() -> None:
    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    server_task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    try:
        async with connect(f"ws://127.0.0.1:{port}/stream/frames") as websocket:
            await websocket.send("text-is-not-part-of-the-protocol")
            with pytest.raises(ConnectionClosedError) as closed:
                await websocket.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1003
    finally:
        server.should_exit = True
        with suppress(asyncio.TimeoutError):
            async with asyncio.timeout(5):
                await server_task
