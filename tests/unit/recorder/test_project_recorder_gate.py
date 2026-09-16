"""The finalization gate: the terminal transition is decided, not assumed.

``asyncio`` cannot cancel the worker thread that runs the finalization, so an
owner whose cleanup deadline expires abandons it rather than stopping it. These
tests pin the three mechanisms that keep that abandoned worker from completing a
session the owner has already condemned:

* the deadline its owner armed — the recorder decides for itself whether the
  decisive write still fits in the budget;
* the terminal lock — the owner's declaration cannot interleave with the
  decision; and
* the conditional durable update — the database referees even a worker that is
  blocked inside its own completion.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.recorder.msgpack_recorder import RecorderState
from canx.recorder.project_recorder import ProjectRecorder

STREAM_ID = "project-recorder-gate-stream"


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


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_an_unusable_stop_deadline_is_rejected(seconds: float) -> None:
    recorder = ProjectRecorder()

    with pytest.raises(ValueError):
        recorder.arm_stop_deadline(seconds)


async def test_a_deadline_with_budget_left_still_completes(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        await recorder.append(batch(0, 6))

        recorder.arm_stop_deadline(10.0)
        await recorder.stop()

        assert recorder.failure is None
        assert recorder.completed is True
        assert recorder.state is RecorderState.STOPPED
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED


async def test_an_expired_deadline_fails_the_session_even_with_no_failure(
    tmp_path: Path,
) -> None:
    """The gate is closed by the clock alone, with nothing else going wrong."""
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        await recorder.append(batch(0, 2))
        recorder.arm_stop_deadline(0.05)
        await asyncio.sleep(0.1)

        await recorder.stop()

        assert recorder.failure is not None
        assert recorder.failure.code == "recorder.stop_deadline_exceeded"
        assert recorder.completed is False
        assert recorder.state is RecorderState.FAILED
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.state is not DataSessionState.COMPLETED


async def test_closing_the_gate_reports_an_already_completed_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        await recorder.append(batch(0, 4))
        await recorder.stop()

        assert (
            recorder.close_finalization_gate(
                code="recorder.cleanup_timeout",
                message="late",
                recoverable=False,
                context={},
            )
            is True
        )
        assert recorder.failure is None


async def test_closing_the_gate_durably_fails_the_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        session_id = recorder.data_session_id
        assert session_id is not None
        await recorder.append(batch(0, 4))

        assert (
            recorder.close_finalization_gate(
                code="recorder.cleanup_timeout",
                message="missed",
                recoverable=False,
                context={"operation": "close"},
            )
            is False
        )
        assert recorder.failure is not None
        assert recorder.failure.code == "recorder.cleanup_timeout"

        await recorder.stop()

        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == 4
        assert recorder.completed is False


async def test_finalization_pending_tracks_the_abandoned_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    entered = threading.Event()
    real_flush = DataSessionWriter.flush_pending

    def blocking(writer: DataSessionWriter) -> None:
        entered.set()
        release.wait(timeout=10)
        real_flush(writer)

    monkeypatch.setattr(DataSessionWriter, "flush_pending", blocking)

    with project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=4)
        await recorder.start(handle.root, stream_id=STREAM_ID)
        assert recorder.finalization_pending is False
        assert recorder.wait_for_finalization(0.01) is True

        stop_task = asyncio.create_task(recorder.stop())
        assert await asyncio.to_thread(entered.wait, 5) is True
        assert recorder.finalization_pending is True
        assert await asyncio.to_thread(recorder.wait_for_finalization, 0.05) is False

        release.set()
        await asyncio.wait_for(stop_task, timeout=10)
        monkeypatch.undo()

        assert await asyncio.to_thread(recorder.wait_for_finalization, 5) is True
        assert recorder.finalization_pending is False
        assert recorder.completed is True
