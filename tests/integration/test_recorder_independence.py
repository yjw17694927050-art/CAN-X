import asyncio

from canx.devices.virtual import VirtualAdapterConfig
from canx.domain.batch import FrameBatch
from canx.recorder.msgpack_recorder import MsgpackRecorder, read_recording
from canx.runtime.service import RuntimeService


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
