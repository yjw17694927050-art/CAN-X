"""Unit contract for the recorder observability seam.

The control plane must report only what is durably true. Two seams are pinned
here, without any transport:

* :meth:`~canx.recorder.project_recorder.ProjectRecorder.read_durable_state` reads
  the recorder's own session row through the data domain and never invents a
  verdict for a row it cannot read;
* :class:`~canx.recorder.control.RecorderStatus` is the immutable shape
  :meth:`~canx.runtime.service.RuntimeService.recorder_status` assembles.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest
from canx.data.errors import DataStorageError
from canx.data.model import DataSessionState
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.recorder.control import FinalizationState, RecorderStatus
from canx.recorder.msgpack_recorder import RecorderState
from canx.recorder.project_recorder import ProjectRecorder
from canx.runtime.service import RuntimeService


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x1A0,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0xA0]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1_000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _batch(stream_id: str, count: int) -> FrameBatch:
    return FrameBatch.create(
        stream_id=stream_id, frames=[frame(sequence) for sequence in range(count)]
    )


def _project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def test_finalization_state_names_exactly_the_three_honest_states() -> None:
    assert {state.value for state in FinalizationState} == {"idle", "pending", "settled"}


def test_the_status_model_is_immutable() -> None:
    status = RecorderStatus(
        active=False,
        stream_id=None,
        data_session_id=None,
        lifecycle_state=RecorderState.IDLE,
        recorded_frame_count=0,
        failure=None,
        finalization_state=FinalizationState.IDLE,
        durable_session_state=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.active = True  # type: ignore[misc]


async def test_the_seam_reports_an_idle_recorder_before_any_capture() -> None:
    status = await RuntimeService().recorder_status()

    assert status.active is False
    assert status.lifecycle_state is RecorderState.IDLE
    assert status.data_session_id is None
    assert status.recorded_frame_count == 0
    assert status.failure is None
    assert status.finalization_state is FinalizationState.IDLE
    assert status.durable_session_state is None


async def test_read_durable_state_is_none_before_a_session_exists() -> None:
    recorder = ProjectRecorder()

    assert recorder.session_id is None
    assert await asyncio.to_thread(recorder.read_durable_state) is None


async def test_read_durable_state_tracks_the_session_the_recorder_wrote(
    tmp_path: Path,
) -> None:
    with _project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=64)
        await recorder.start(handle.root, stream_id="stream-a")
        session_id = recorder.session_id
        assert session_id is not None
        assert await asyncio.to_thread(recorder.read_durable_state) is DataSessionState.ACTIVE

        await recorder.append(_batch("stream-a", 3))
        await recorder.stop()

    # The identity survives finalization; the state is the durable verdict.
    assert recorder.session_id == session_id
    assert recorder.state is RecorderState.STOPPED
    assert await asyncio.to_thread(recorder.read_durable_state) is DataSessionState.COMPLETED


async def test_a_failed_recorder_reads_back_a_failed_session(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=64)
        await recorder.start(handle.root, stream_id="stream-a")
        recorder.fail(
            code="recorder.sequence_gap",
            message="The batch does not continue this recording's sequence.",
            recoverable=False,
            context={"stream_id": "stream-a"},
        )
        await recorder.stop()

    assert await asyncio.to_thread(recorder.read_durable_state) is DataSessionState.FAILED


async def test_read_durable_state_is_none_when_the_row_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable row is reported as unknown, never guessed into a verdict."""

    def refuse(self: object, session_id: str) -> object:
        raise DataStorageError("The project database is unreadable.")

    with _project(tmp_path) as handle:
        recorder = ProjectRecorder(max_frames_per_segment=64)
        await recorder.start(handle.root, stream_id="stream-a")
        monkeypatch.setattr(
            "canx.recorder.project_recorder.DataSessionService.get_session", refuse
        )
        assert await asyncio.to_thread(recorder.read_durable_state) is None
