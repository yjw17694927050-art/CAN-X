"""Behavior tests for the V0.1 append-only recorder."""

import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event

import pytest
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.recorder.msgpack_recorder import MsgpackRecorder, RecorderState, read_recording


def batch(sequence: int) -> FrameBatch:
    frame = Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=1,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )
    return FrameBatch.create(stream_id="stream-1", frames=[frame])


async def test_recorder_flushes_batches_that_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "capture.canxmsg"
    recorder = MsgpackRecorder()

    await recorder.start(path, stream_id="stream-1")
    await recorder.append(batch(0))
    await recorder.append(batch(1))
    await recorder.stop()

    assert recorder.state is RecorderState.STOPPED
    assert recorder.recorded_frames == 2
    assert [item.first_sequence for item in read_recording(path)] == [0, 1]


async def test_recorder_reports_an_invalid_destination(tmp_path: Path) -> None:
    recorder = MsgpackRecorder()

    with pytest.raises(OSError):
        await recorder.start(tmp_path / "missing" / "capture.canxmsg", stream_id="stream-1")

    assert recorder.state is RecorderState.FAILED
    assert recorder.failure is not None
    assert recorder.failure.code == "recorder.open_failed"


class FailingWriteFile:
    def __init__(self) -> None:
        self.write_count = 0

    def write(self, payload: bytes) -> int:
        self.write_count += 1
        if self.write_count > 1:
            raise OSError("simulated disk failure")
        return len(payload)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class FailingWritePath:
    def __init__(self) -> None:
        self.file = FailingWriteFile()

    def open(self, _mode: str) -> FailingWriteFile:
        return self.file


async def test_recorder_transitions_to_failed_on_a_write_error() -> None:
    recorder = MsgpackRecorder()
    await recorder.start(FailingWritePath(), stream_id="stream-1")  # type: ignore[arg-type]

    with pytest.raises(OSError, match="simulated disk failure"):
        await recorder.append(batch(0))

    assert recorder.state is RecorderState.FAILED
    assert recorder.failure is not None
    assert recorder.failure.code == "recorder.write_failed"
    await recorder.stop()
    assert recorder.state is RecorderState.FAILED


async def test_first_failure_survives_stop_and_failed_restart(tmp_path: Path) -> None:
    recorder = MsgpackRecorder()
    await recorder.start(tmp_path / "first.canxmsg", stream_id="stream-1")
    context: dict[str, object] = {"queue_depth": 7}
    recorder.fail(
        code="recorder.backpressure", message="archive full", recoverable=False, context=context
    )
    first = recorder.failure
    context["queue_depth"] = 0
    recorder.fail(code="recorder.write_failed", message="later error", recoverable=True, context={})
    await recorder.stop()
    with pytest.raises(OSError):
        await recorder.start(tmp_path / "missing" / "capture", stream_id="stream-1")
    assert recorder.state is RecorderState.FAILED
    assert recorder.failure is first
    assert first is not None
    assert first.context == {"queue_depth": 7}
    with pytest.raises(FrozenInstanceError):
        first.code = "changed"  # type: ignore[misc]
    await recorder.start(tmp_path / "second.canxmsg", stream_id="stream-1")
    assert recorder.failure is None
    assert recorder.state is RecorderState.RECORDING
    await recorder.stop()


async def test_flush_failure_is_structured_and_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    path = FailingWritePath()

    def fail_flush() -> None:
        raise OSError("flush failed")

    monkeypatch.setattr(path.file, "flush", fail_flush)
    recorder = MsgpackRecorder()
    await recorder.start(path, stream_id="stream-1")  # type: ignore[arg-type]
    with pytest.raises(OSError, match="flush failed"):
        await recorder.stop()
    assert recorder.state is RecorderState.FAILED
    assert recorder.failure is not None
    assert recorder.failure.code == "recorder.flush_failed"
    await recorder.stop()
    assert recorder.failure.code == "recorder.flush_failed"


async def test_stop_waits_for_disk_write_after_append_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = FailingWritePath()
    entered, released, finished = Event(), Event(), Event()

    def gated_write(payload: bytes) -> int:
        if payload != b"CANXMSG1":
            entered.set()
            released.wait(timeout=2)
            finished.set()
        return len(payload)

    def safe_flush() -> None:
        assert finished.is_set(), "flush must not overlap the cancelled disk write"

    monkeypatch.setattr(path.file, "write", gated_write)
    monkeypatch.setattr(path.file, "flush", safe_flush)
    recorder = MsgpackRecorder()
    await recorder.start(path, stream_id="stream-1")  # type: ignore[arg-type]
    append = asyncio.create_task(recorder.append(batch(0)))
    assert await asyncio.to_thread(entered.wait, 1)
    append.cancel()
    with pytest.raises(asyncio.CancelledError):
        await append
    stop = asyncio.create_task(recorder.stop())
    try:
        await asyncio.sleep(0.02)
        assert not stop.done(), "stop must join the in-flight write before closing"
    finally:
        released.set()
        await stop
    assert recorder.recorded_frames == 0
