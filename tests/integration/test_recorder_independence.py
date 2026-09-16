import asyncio

import pytest
from canx.api.app import create_app
from canx.devices.virtual import VirtualAdapterConfig
from canx.domain.batch import FrameBatch
from canx.recorder.msgpack_recorder import MsgpackRecorder, read_recording
from canx.runtime.service import CaptureSessionState, RuntimeService
from httpx import ASGITransport, AsyncClient


async def test_recorder_continues_without_a_stream_client(tmp_path) -> None:
    path = tmp_path / "capture.canxmsg"
    service = RuntimeService()
    await service.start_capture(
        VirtualAdapterConfig(rate_hz=2_000, seed=11),
        batch_size=25,
        recording_path=path,
    )
    await asyncio.sleep(0.08)
    await service.stop_capture()

    batches = read_recording(path)
    recorded = sum(batch.frame_count for batch in batches)
    metrics = service.metrics_snapshot()
    assert recorded > 0
    assert recorded == metrics.recorded_frames == metrics.captured_frames
    assert metrics.dropped_stream_frames == 0


class SlowRecorder(MsgpackRecorder):
    def __init__(self, release: asyncio.Event) -> None:
        super().__init__()
        self._release = release

    async def append(self, batch: FrameBatch) -> None:
        await self._release.wait()
        await super().append(batch)


async def test_slow_recorder_does_not_delay_the_sibling_stream(tmp_path) -> None:
    release = asyncio.Event()
    service = RuntimeService(recorder=SlowRecorder(release))
    async with service.broker.subscribe() as stream:
        await service.start_capture(
            VirtualAdapterConfig(rate_hz=5_000),
            batch_size=25,
            recording_path=tmp_path / "slow.canxmsg",
        )
        batch = await asyncio.wait_for(stream.get(), timeout=1)
        assert batch.frame_count == 25
        assert service.metrics_snapshot().recorded_frames == 0
        release.set()
        await service.stop_capture()


class BlockedRecorder(MsgpackRecorder):
    """Keep append blocked; optionally stall close until its owner cancels it."""

    def __init__(self, *, block_stop: bool = False) -> None:
        super().__init__()
        self.append_started = asyncio.Event()
        self.append_cancelled = asyncio.Event()
        self.stop_started = asyncio.Event()
        self.closed = asyncio.Event()
        self.block_stop = block_stop

    async def append(self, batch: FrameBatch) -> None:
        self.append_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.append_cancelled.set()

    async def stop(self) -> None:
        self.stop_started.set()
        try:
            if self.block_stop:
                await asyncio.Event().wait()
        finally:
            await super().stop()
            self.closed.set()


@pytest.mark.parametrize("block_stop", [False, True])
async def test_archive_saturation_degrades_and_stops_without_releasing_recorder(
    tmp_path, block_stop: bool
) -> None:
    recorder = BlockedRecorder(block_stop=block_stop)
    service = RuntimeService(
        recorder=recorder,
        archive_capacity=4,
        archive_publish_timeout_seconds=0.05,
        recorder_cleanup_timeout_seconds=0.05,
    )
    existing_tasks = asyncio.all_tasks()
    async with (
        service.broker.subscribe() as stream,
        AsyncClient(
            transport=ASGITransport(app=create_app(runtime_service=service)),
            base_url="http://testserver",
        ) as client,
    ):
        stream_id = await service.start_capture(
            VirtualAdapterConfig(rate_hz=50_000),
            batch_size=1,
            recording_path=tmp_path / "saturated.canxmsg",
        )

        async def observe_degradation() -> None:
            await recorder.append_started.wait()
            before = await stream.get()
            assert service.capture_state is CaptureSessionState.RUNNING
            while service.capture_state is not CaptureSessionState.DEGRADED:
                await asyncio.sleep(0.001)
            metrics = service.metrics_snapshot()
            assert metrics.queue_peaks["recorder_queue_depth"] == 4
            assert metrics.recorder_backpressure_events == metrics.recorder_failures == 1
            assert metrics.recorder_state == "failed"
            assert service.capture_active is True
            # One blocked frame + four queued + one rejected publication.
            assert metrics.recorder_uncommitted_frames == 6
            assert metrics.recorded_frames == recorder.recorded_frames == 0
            assert metrics.dropped_frames == 0
            failure = service.failure
            assert failure is not None
            assert failure.code == metrics.recorder_failure_code == "recorder.backpressure"
            assert failure.context == metrics.recorder_failure_context
            assert failure.context["stream_id"] == stream_id
            assert failure.context["capacity"] == failure.context["queue_depth"] == 4
            status = (await client.get("/runtime/status")).json()
            assert status["capture_state"] == "degraded"
            assert status["capture_active"] is True
            assert status["failure"]["code"] == failure.code
            assert status["failure"]["details"] == failure.context
            # Drain old batches: require a sequence generated after the failure.
            after = await stream.get()
            while after.first_sequence <= failure.context["frame_sequence"]:
                after = await stream.get()
            assert after.first_sequence > before.last_sequence
            await recorder.closed.wait()
            assert recorder.append_cancelled.is_set()
            assert service.capture_state is CaptureSessionState.DEGRADED

        try:
            await asyncio.wait_for(observe_degradation(), timeout=1.0)
        finally:
            await asyncio.wait_for(service.stop_capture(), timeout=1.0)
        metrics = service.metrics_snapshot()
        assert service.has_session is False
        assert service.capture_active is False
        assert service.capture_state is CaptureSessionState.FAILED
        assert service._consumer_tasks == ()
        assert service._recorder_cleanup_task is None
        assert recorder.closed.is_set()
        assert metrics.recorder_state == "failed"
        assert metrics.recorder_backpressure_events == metrics.recorder_failures == 1
        assert metrics.recorder_uncommitted_frames == 6
        assert metrics.recorder_queue_depth == metrics.stream_queue_depth == 0
        assert metrics.ingress_queue_depth == 0
        assert not (asyncio.all_tasks() - existing_tasks)
