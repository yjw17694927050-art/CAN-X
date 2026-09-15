"""Behavior tests for the headless capture fan-out pipeline."""

import asyncio

from canx.capture.pipeline import CapturePipeline
from canx.devices.virtual import VirtualAdapter, VirtualAdapterConfig


async def wait_for_frames(pipeline: CapturePipeline, count: int) -> None:
    """Wait with a hard deadline for a deterministic capture count."""
    async with asyncio.timeout(2):
        while pipeline.captured_frames < count:
            await asyncio.sleep(0)


async def test_capture_normalizes_sequence_and_stream_relative_time() -> None:
    pipeline = CapturePipeline(VirtualAdapter(VirtualAdapterConfig(rate_hz=10_000, seed=11)))
    recorder = pipeline.subscribe("recorder", capacity=8, lossy=False)

    await pipeline.start()
    await wait_for_frames(pipeline, 3)
    await pipeline.stop()
    frames = [await recorder.get() for _ in range(3)]

    assert [frame.sequence for frame in frames] == [0, 1, 2]
    assert frames[0].normalized_timestamp == 0.0
    assert frames[0].host_timestamp <= frames[1].host_timestamp <= frames[2].host_timestamp


async def test_slow_stream_drops_are_observable_without_losing_recorder_frames() -> None:
    pipeline = CapturePipeline(VirtualAdapter(VirtualAdapterConfig(rate_hz=10_000, seed=12)))
    recorder = pipeline.subscribe("recorder", capacity=16, lossy=False)
    stream = pipeline.subscribe("stream", capacity=1, lossy=True)

    await pipeline.start()
    await wait_for_frames(pipeline, 6)
    await pipeline.stop()
    recorded = [await recorder.get() for _ in range(6)]

    assert [frame.sequence for frame in recorded] == list(range(6))
    assert stream.dropped_frames >= 5
    assert pipeline.captured_frames >= 6


async def test_stop_closes_the_adapter_and_capture_task() -> None:
    adapter = VirtualAdapter(VirtualAdapterConfig(rate_hz=10_000, seed=13))
    pipeline = CapturePipeline(adapter)
    pipeline.subscribe("recorder", capacity=4, lossy=False)

    await pipeline.start()
    await wait_for_frames(pipeline, 1)
    await pipeline.stop()

    assert pipeline.is_running is False
