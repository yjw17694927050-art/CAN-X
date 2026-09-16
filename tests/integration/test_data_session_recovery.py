"""End-to-end recovery of a session left behind by an unclean runtime exit."""

import shutil
from pathlib import Path

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.errors import ProjectError
from canx.project.service import ProjectService

STREAM_ID = "recovery-stream"
MAX_FRAMES_PER_SEGMENT = 4


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x200,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence % 256]),
        direction=Direction.TX,
        hardware_timestamp=None,
        host_timestamp=2_000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def batch(start: int, count: int) -> FrameBatch:
    return FrameBatch.create(
        stream_id=STREAM_ID, frames=[frame(sequence) for sequence in range(start, start + count)]
    )


def test_an_abandoned_session_recovers_without_losing_committed_segments(
    tmp_path: Path,
) -> None:
    """A runtime crash leaves an ACTIVE session; recovery marks it, never deletes."""
    root = tmp_path / "vehicle.canx"
    crashed = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(crashed.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 4))
    writer.append(batch(4, 2))  # stays buffered: lost with the crashed process
    session_id = writer.session_id
    committed = service.list_segments(session_id)
    committed_frames = service.read_segment(session_id, 0)
    crashed.close()

    # Simulate the runtime exiting without finalize: drop every handle.
    del writer, service, crashed

    reopened = ProjectService().open(root)
    try:
        after_crash = DataSessionService(reopened.root)
        interrupted = after_crash.get_session(session_id)
        assert interrupted.state is DataSessionState.ACTIVE
        assert interrupted.ended_at is None
        assert interrupted.frame_count == 4
        assert interrupted.segment_count == 1

        recovered_ids = after_crash.recover_incomplete_sessions()

        assert recovered_ids == (session_id,)
        recovered = after_crash.get_session(session_id)
        assert recovered.state is DataSessionState.INTERRUPTED
        assert recovered.ended_at is not None
        assert recovered.updated_at >= recovered.started_at
        assert recovered.frame_count == 4
        assert recovered.segment_count == 1
        assert recovered.first_sequence == 0
        assert recovered.last_sequence == 3

        assert after_crash.list_segments(session_id) == committed
        assert after_crash.read_segment(session_id, 0) == committed_frames
        assert after_crash.inspect_integrity().clean is True
    finally:
        reopened.close()


def test_the_unflushed_buffer_is_not_claimed_as_persisted(tmp_path: Path) -> None:
    """Frames still in memory when the process dies are honestly reported as absent."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 9))
    session_id = writer.session_id
    handle.close()

    with ProjectService().open(root) as reopened:
        after_crash = DataSessionService(reopened.root)
        stored = after_crash.get_session(session_id)
        assert stored.frame_count == 8
        assert stored.segment_count == 2
        assert len(after_crash.list_segments(session_id)) == 2
        assert sum(segment.frame_count for segment in after_crash.list_segments(session_id)) == 8


def test_recovery_after_reopen_is_durable_across_a_second_reopen(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    writer = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT).start(
        stream_id=STREAM_ID
    )
    writer.append(batch(0, 4))
    session_id = writer.session_id
    handle.close()

    with ProjectService().open(root) as reopened:
        DataSessionService(reopened.root).recover_incomplete_sessions()

    with ProjectService().open(root) as reopened_again:
        final = DataSessionService(reopened_again.root)
        assert final.get_session(session_id).state is DataSessionState.INTERRUPTED
        assert final.recover_incomplete_sessions() == ()
        assert final.inspect_integrity().clean is True


def test_a_recovered_project_can_start_a_new_session(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    crashed = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT).start(
        stream_id=STREAM_ID
    )
    crashed.append(batch(0, 4))
    handle.close()

    with ProjectService().open(root) as reopened:
        service = DataSessionService(reopened.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
        service.recover_incomplete_sessions()
        fresh = service.start(stream_id="stream-after-recovery")
        fresh.append(
            FrameBatch.create(
                stream_id="stream-after-recovery",
                frames=[frame(sequence) for sequence in range(3)],
            )
        )
        completed = fresh.finalize()

        assert completed.state is DataSessionState.COMPLETED
        assert completed.frame_count == 3
        assert service.get_session(crashed.session_id).state is DataSessionState.INTERRUPTED
        assert len(service.list_sessions()) == 2
        assert service.inspect_integrity().clean is True


def test_recovery_never_removes_segment_files_from_disk(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 8))
    files_before = sorted(
        path.name
        for path in (handle.root / "data" / "sessions" / writer.session_id / "segments").iterdir()
    )

    service.recover_incomplete_sessions()

    files_after = sorted(
        path.name
        for path in (handle.root / "data" / "sessions" / writer.session_id / "segments").iterdir()
    )
    assert files_before == files_after == ["000000.parquet", "000001.parquet"]
    handle.close()


def test_the_recovered_project_directory_can_be_removed(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    crashed = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT).start(
        stream_id=STREAM_ID
    )
    crashed.append(batch(0, 4))
    handle.close()

    with ProjectService().open(root) as reopened:
        DataSessionService(reopened.root).recover_incomplete_sessions()

    shutil.rmtree(root)

    assert not root.exists()


def test_recovery_on_a_project_with_no_sessions_is_a_no_op(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A") as handle:
        service = DataSessionService(handle.root)

        assert service.recover_incomplete_sessions() == ()
        assert service.inspect_integrity().clean is True


def test_recovery_refuses_a_project_that_is_not_a_canx_project(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-project.canx"
    empty.mkdir()

    with pytest.raises(ProjectError):
        ProjectService().open(empty)
