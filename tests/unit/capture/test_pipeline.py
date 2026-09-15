"""Behavior tests for the headless capture fan-out pipeline."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from canx.capture.pipeline import CapturePipeline
from canx.capture.subscriber import (
    FrameSubscriber,
    SubscriberBackpressureError,
    SubscriberDisabledError,
    SubscriberFailure,
)
from canx.devices.virtual import VirtualAdapter, VirtualAdapterConfig
from canx.domain.frame import Frame


@pytest.fixture
async def frame() -> Frame:
    """Obtain one real canonical frame without capture orchestration."""
    adapter = VirtualAdapter(VirtualAdapterConfig())
    await adapter.open()
    try:
        return await adapter.recv()
    finally:
        await adapter.close()


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


async def test_lossless_deadline_reports_pressure_and_disables_queue(frame: Frame) -> None:
    subscriber = FrameSubscriber("archive", capacity=1, lossy=False, publish_timeout_seconds=0.02)
    await subscriber.publish(frame)

    async with asyncio.timeout(0.1):
        with pytest.raises(SubscriberBackpressureError) as caught:
            await subscriber.publish(frame)

    failure = caught.value.failure
    assert failure.code == "subscriber.backpressure"
    assert failure.subscriber == "archive"
    assert failure.capacity == 1
    assert failure.queue_depth == 1
    assert failure.frame_sequence == frame.sequence
    assert failure.message
    assert failure.recoverable is False
    with pytest.raises(FrozenInstanceError):
        failure.code = "changed"  # type: ignore[misc]
    assert subscriber.queue_depth == 0
    with pytest.raises(SubscriberDisabledError):
        await subscriber.publish(frame)
    with pytest.raises(SubscriberDisabledError):
        await subscriber.get()


@pytest.mark.parametrize("lossy", [False, True])
async def test_disable_accounts_backlog_and_rejects_later_delivery(
    frame: Frame, lossy: bool
) -> None:
    subscriber = FrameSubscriber("consumer", capacity=2, lossy=lossy)
    await subscriber.publish(frame)
    await subscriber.publish(frame)

    assert subscriber.disable() == 2
    assert subscriber.disable() == 0
    assert subscriber.queue_depth == 0
    with pytest.raises(SubscriberDisabledError):
        await subscriber.publish(frame)
    with pytest.raises(SubscriberDisabledError):
        await subscriber.get()


async def test_disable_wakes_blocked_lossless_publisher(frame: Frame) -> None:
    subscriber = FrameSubscriber("archive", capacity=1, lossy=False)
    await subscriber.publish(frame)
    pending = asyncio.create_task(subscriber.publish(frame))
    await asyncio.sleep(0)
    assert not pending.done()

    assert subscriber.disable() == 1

    with pytest.raises(SubscriberDisabledError):
        await asyncio.wait_for(pending, timeout=0.1)
    assert subscriber.queue_depth == 0


async def test_disable_wakes_blocked_consumer() -> None:
    subscriber = FrameSubscriber("archive", capacity=1, lossy=False)
    pending = asyncio.create_task(subscriber.get())
    await asyncio.sleep(0)
    assert not pending.done()

    assert subscriber.disable() == 0

    with pytest.raises(SubscriberDisabledError):
        await asyncio.wait_for(pending, timeout=0.1)


async def test_lossless_delivery_succeeds_when_consumer_frees_capacity(frame: Frame) -> None:
    subscriber = FrameSubscriber("archive", capacity=1, lossy=False, publish_timeout_seconds=0.2)
    await subscriber.publish(frame)
    pending = asyncio.create_task(subscriber.publish(frame))
    await asyncio.sleep(0)
    assert not pending.done()

    assert await subscriber.get() == frame
    await asyncio.wait_for(pending, timeout=0.1)
    assert await subscriber.get() == frame


async def test_archive_pressure_does_not_delay_stream_and_capture_continues() -> None:
    failures: list[SubscriberFailure] = []
    failed = asyncio.Event()

    def on_failure(failure: SubscriberFailure) -> None:
        failures.append(failure)
        failed.set()

    pipeline = CapturePipeline(
        VirtualAdapter(VirtualAdapterConfig(rate_hz=1_000)),
        subscriber_failure_handler=on_failure,
    )
    archive = pipeline.subscribe("archive", capacity=1, lossy=False, publish_timeout_seconds=0.2)
    stream = pipeline.subscribe("stream", capacity=8, lossy=True)
    await pipeline.start()
    try:
        async with asyncio.timeout(0.1):
            assert (await stream.get()).sequence == 0
            assert (await stream.get()).sequence == 1
        assert not failed.is_set()
        assert pipeline.captured_frames == 1
        await asyncio.wait_for(failed.wait(), timeout=0.5)
        assert len(failures) == 1
        assert failures[0].subscriber == "archive"
        assert failures[0].frame_sequence == 1
        assert archive.queue_depth == 0
        assert pipeline.unsubscribe("archive") is None
        assert (await asyncio.wait_for(stream.get(), timeout=0.1)).sequence >= 2
        await wait_for_frames(pipeline, 4)
        assert len(failures) == 1
        assert pipeline.is_running
    finally:
        await pipeline.stop()


async def test_unsubscribe_detaches_and_returns_queue_without_discarding_it() -> None:
    pipeline = CapturePipeline(VirtualAdapter(VirtualAdapterConfig(rate_hz=1_000)))
    archive = pipeline.subscribe("archive", capacity=1, lossy=False)
    await pipeline.start()
    try:
        await wait_for_frames(pipeline, 1)
        assert pipeline.unsubscribe("archive") is archive
        assert pipeline.unsubscribe("missing") is None
        await wait_for_frames(pipeline, 3)
        assert archive.queue_depth == 1
        assert archive.disable() == 1
    finally:
        await pipeline.stop()


async def test_stop_cancels_blocked_fanout_without_pressure_callback(frame: Frame) -> None:
    failures: list[SubscriberFailure] = []
    pipeline = CapturePipeline(
        VirtualAdapter(VirtualAdapterConfig(rate_hz=1_000)),
        subscriber_failure_handler=failures.append,
    )
    archive = pipeline.subscribe("archive", capacity=1, lossy=False)
    stream = pipeline.subscribe("stream", capacity=8, lossy=True)
    await pipeline.start()
    try:
        async with asyncio.timeout(0.1):
            await stream.get()
            assert (await stream.get()).sequence == 1
        await asyncio.wait_for(pipeline.stop(), timeout=0.1)
        assert not pipeline.is_running
        assert failures == []
        assert pipeline.captured_frames == 1
        await archive.get()
        await asyncio.sleep(0)
        assert archive.queue_depth == 0
        await archive.publish(frame)
        assert await archive.get() == frame
    finally:
        await pipeline.stop()


@pytest.mark.parametrize("timeout", [0.0, -0.01, float("inf"), float("nan")])
def test_lossless_timeout_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        FrameSubscriber("archive", capacity=1, lossy=False, publish_timeout_seconds=timeout)


async def test_unsubscribe_and_disable_pending_delivery_preserves_stream() -> None:
    failures: list[SubscriberFailure] = []
    pipeline = CapturePipeline(
        VirtualAdapter(VirtualAdapterConfig(rate_hz=1_000)),
        subscriber_failure_handler=failures.append,
    )
    archive = pipeline.subscribe("archive", capacity=1, lossy=False)
    stream = pipeline.subscribe("stream", capacity=8, lossy=True)
    await pipeline.start()
    try:
        async with asyncio.timeout(0.1):
            await stream.get()
            assert (await stream.get()).sequence == 1
        assert pipeline.unsubscribe("archive") is archive
        assert archive.disable() == 1
        await wait_for_frames(pipeline, 3)
        assert failures == []
        assert pipeline.is_running
        assert (await asyncio.wait_for(stream.get(), timeout=0.1)).sequence >= 2
    finally:
        await pipeline.stop()


async def test_unknown_delivery_error_fails_capture_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_publish(self: FrameSubscriber, frame: Frame) -> None:
        raise ValueError("unexpected delivery failure")

    monkeypatch.setattr(FrameSubscriber, "publish", broken_publish)
    failures: list[SubscriberFailure] = []
    pipeline = CapturePipeline(
        VirtualAdapter(VirtualAdapterConfig()), subscriber_failure_handler=failures.append
    )
    pipeline.subscribe("archive", capacity=1, lossy=False)
    await pipeline.start()
    async with asyncio.timeout(0.1):
        while pipeline.is_running:
            await asyncio.sleep(0)
    assert pipeline.captured_frames == 0
    assert failures == []
    with pytest.raises(ValueError, match="unexpected delivery failure"):
        await pipeline.stop()


async def test_stop_counts_frame_already_delivered_before_gather_resumes() -> None:
    pipeline = CapturePipeline(VirtualAdapter(VirtualAdapterConfig(rate_hz=1)))
    archive = pipeline.subscribe("archive", capacity=1, lossy=False)
    await pipeline.start()
    try:
        frame = await asyncio.wait_for(archive.get(), timeout=0.1)
        await pipeline.stop()
        assert frame.sequence == 0
        assert pipeline.generated_frames == 1
        assert pipeline.captured_frames == 1
    finally:
        await pipeline.stop()


async def test_stop_delivers_received_frame_when_fanout_tasks_have_not_started() -> None:
    pipeline = CapturePipeline(VirtualAdapter(VirtualAdapterConfig(rate_hz=1)))
    archive = pipeline.subscribe("archive", capacity=1, lossy=False)
    await pipeline.start()
    try:
        async with asyncio.timeout(0.1):
            while pipeline.generated_frames == 0:
                await asyncio.sleep(0)
        await pipeline.stop()
        assert pipeline.generated_frames == 1
        assert pipeline.captured_frames == 1
        assert archive.queue_depth == 1
        assert (await archive.get()).sequence == 0
    finally:
        await pipeline.stop()


async def test_stop_delivers_frame_returned_by_receive_during_shutdown() -> None:
    class YieldingAdapter(VirtualAdapter):
        async def recv(self) -> Frame:
            received = await super().recv()
            await asyncio.sleep(0)
            return received

    pipeline = CapturePipeline(YieldingAdapter(VirtualAdapterConfig(rate_hz=1)))
    archive = pipeline.subscribe("archive", capacity=1, lossy=False)
    await pipeline.start()
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(pipeline.stop(), timeout=0.1)
        assert pipeline.generated_frames == 1
        assert pipeline.captured_frames == 1
        assert archive.queue_depth == 1
        assert (await archive.get()).sequence == 0
    finally:
        await pipeline.stop()


async def test_stop_cancels_pending_receive_and_awaits_its_cleanup() -> None:
    receiving = asyncio.Event()
    cleaned_up = asyncio.Event()

    class WaitingAdapter(VirtualAdapter):
        async def recv(self) -> Frame:
            receiving.set()
            try:
                await asyncio.Event().wait()
                return await super().recv()
            finally:
                cleaned_up.set()

    pipeline = CapturePipeline(WaitingAdapter(VirtualAdapterConfig(rate_hz=1)))
    await pipeline.start()
    try:
        await asyncio.wait_for(receiving.wait(), timeout=0.1)
        await asyncio.wait_for(pipeline.stop(), timeout=0.1)
        assert cleaned_up.is_set()
        assert not pipeline.is_running
        assert pipeline.generated_frames == 0
        assert pipeline.captured_frames == 0
    finally:
        await pipeline.stop()
