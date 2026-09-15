"""Behavior tests for the V0.1 append-only recorder."""

from pathlib import Path

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
    await recorder.stop()
    assert recorder.state is RecorderState.FAILED
