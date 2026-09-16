"""The Project-backed recorder boundary: FrameBatch → DataSessionWriter.

Every guarantee here is a guarantee of the runtime persistence path, so the
tests are written against a real project on disk:

* one ``start`` opens exactly one ``ACTIVE`` data session bound to the capture
  stream id;
* ``append`` reuses ``DataSessionWriter`` rather than re-implementing segment
  writing, so committed frames are visible through ``DataSessionService``;
* a normal ``stop`` finalizes to ``COMPLETED``; a recorded failure never does;
* blocking Parquet/SQLite/filesystem work never runs on the event loop, and a
  cancelled ``append`` can never interleave with the final flush.
"""

import asyncio
import time
from pathlib import Path

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.recorder.msgpack_recorder import RecorderState
from canx.recorder.project_recorder import ProjectRecorder, RecorderContinuityError

STREAM_ID = "project-recorder-stream"


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x123,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x11]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=300.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def batch(start: int, count: int, *, stream_id: str = STREAM_ID) -> FrameBatch:
    return FrameBatch.create(
        stream_id=stream_id, frames=[frame(sequence) for sequence in range(start, start + count)]
    )


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


async def test_start_opens_one_active_data_session_bound_to_the_stream(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        assert recorder.data_session_id is None

        await recorder.start(handle.root, stream_id=STREAM_ID)

        session_id = recorder.data_session_id
        assert session_id is not None
        assert recorder.state is RecorderState.RECORDING
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.stream_id == STREAM_ID
        assert stored.state is DataSessionState.ACTIVE
        assert stored.project_id == handle.project_id


async def test_append_persists_batches_into_the_data_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        service = DataSessionService(handle.root)

        await recorder.append(batch(0, 4))
        await recorder.append(batch(4, 2))

        stored = service.get_session(session_id)
        assert stored.frame_count == 4
        assert stored.segment_count == 1
        assert recorder.recorded_frames == 6
        assert [segment.segment_index for segment in service.list_segments(session_id)] == [0]


async def test_stop_finalizes_the_session_as_completed(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        await recorder.append(batch(0, 6))

        await recorder.stop()

        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.frame_count == 6
        assert stored.segment_count == 2
        assert stored.ended_at is not None
        assert recorder.state is RecorderState.STOPPED
        assert recorder.failure is None


async def test_stop_flushes_an_empty_session_to_completed(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder()
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None

        await recorder.stop()

        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.frame_count == 0
        assert stored.segment_count == 0


async def test_stop_after_a_recorded_failure_fails_the_session_but_keeps_segments(
    tmp_path: Path,
) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        service = DataSessionService(handle.root)
        await recorder.append(batch(0, 4))
        committed = service.list_segments(session_id)

        recorder.fail(
            code="recorder.backpressure",
            message="archive queue saturated",
            recoverable=False,
            context={"stream_id": STREAM_ID},
        )
        await recorder.stop()

        stored = service.get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == 4
        assert stored.segment_count == 1
        assert service.list_segments(session_id) == committed
        assert (handle.root / committed[0].relative_path).is_file()
        assert recorder.state is RecorderState.FAILED
        assert recorder.failure is not None
        assert recorder.failure.code == "recorder.backpressure"


async def test_stop_does_not_mask_the_first_diagnostic(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder()
        await recorder.start(handle.root, stream_id=STREAM_ID)

        recorder.fail(code="recorder.backpressure", message="first", recoverable=False, context={})
        recorder.fail(
            code="recorder.cleanup_timeout", message="second", recoverable=False, context={}
        )
        await recorder.stop()

        assert recorder.failure is not None
        assert recorder.failure.code == "recorder.backpressure"
        assert recorder.failure.message == "first"


async def test_a_sequence_gap_is_treated_as_recording_loss(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        await recorder.append(batch(0, 2))

        with pytest.raises(RecorderContinuityError) as info:
            await recorder.append(batch(5, 2))

        assert info.value.code == "recorder.sequence_gap"
        assert recorder.failure is not None
        assert recorder.failure.code == "recorder.sequence_gap"
        await recorder.stop()
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == 0


async def test_a_batch_from_another_stream_is_rejected(tmp_path: Path) -> None:
    from canx.data.errors import DataError

    with project(tmp_path) as handle:
        recorder = ProjectRecorder()
        await recorder.start(handle.root, stream_id=STREAM_ID)

        with pytest.raises(DataError) as info:
            await recorder.append(batch(0, 2, stream_id="other-stream"))

        assert info.value.code == "data.session.stream_mismatch"
        await recorder.stop()


async def test_append_before_start_and_after_stop_are_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder()

        with pytest.raises(RuntimeError):
            await recorder.append(batch(0, 1))

        await recorder.start(handle.root, stream_id=STREAM_ID)
        await recorder.stop()

        with pytest.raises(RuntimeError):
            await recorder.append(batch(0, 1))


async def test_start_twice_is_rejected_and_double_stop_is_safe(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder()
        await recorder.start(handle.root, stream_id=STREAM_ID)

        with pytest.raises(RuntimeError):
            await recorder.start(handle.root, stream_id=STREAM_ID)

        await recorder.stop()
        await recorder.stop()
        assert recorder.state is RecorderState.STOPPED


async def test_reset_session_clears_the_previous_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder()
        await recorder.start(handle.root, stream_id=STREAM_ID)
        await recorder.stop()

        recorder.reset_session()

        assert recorder.state is RecorderState.IDLE
        assert recorder.data_session_id is None
        assert recorder.recorded_frames == 0
        assert recorder.failure is None


async def test_reset_session_refuses_an_active_recorder(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder()
        await recorder.start(handle.root, stream_id=STREAM_ID)

        with pytest.raises(RuntimeError):
            recorder.reset_session()

        await recorder.stop()


async def test_an_unsupported_segment_threshold_is_rejected_before_the_project_is_touched(
    tmp_path: Path,
) -> None:
    from canx.data.errors import DataValidationError

    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=0)

        with pytest.raises(DataValidationError):
            await recorder.start(handle.root, stream_id=STREAM_ID)

        assert DataSessionService(handle.root).list_sessions() == ()


async def test_appends_never_block_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocking Parquet/SQLite work must run off the realtime event loop."""
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        real_append = DataSessionWriter.append

        def slow_append(self: DataSessionWriter, batch: FrameBatch) -> None:
            time.sleep(0.2)
            real_append(self, batch)

        monkeypatch.setattr(DataSessionWriter, "append", slow_append)

        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            await recorder.append(batch(0, 4))
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        monkeypatch.undo()

        assert ticks >= 10, "the event loop starved while a segment was written"
        await recorder.stop()


async def test_a_cancelled_append_commits_before_the_session_is_finalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled append's disk thread must finish before the final flush.

    The runtime cancels the archive consumer on failure and stop; the segment
    write it started is already in flight on a worker thread. Finalizing the
    session concurrently would either drop that segment or reorder it.
    """
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        real_append = DataSessionWriter.append

        def slow_append(self: DataSessionWriter, batch: FrameBatch) -> None:
            time.sleep(0.2)
            real_append(self, batch)

        monkeypatch.setattr(DataSessionWriter, "append", slow_append)
        task = asyncio.create_task(recorder.append(batch(0, 4)))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await recorder.stop()
        monkeypatch.undo()

        service = DataSessionService(handle.root)
        stored = service.get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.frame_count == 4
        assert stored.segment_count == 1
        assert service.read_segment(session_id, 0) == tuple(frame(index) for index in range(4))
