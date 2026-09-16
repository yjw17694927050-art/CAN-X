"""The public FAILED lifecycle of :class:`DataSessionWriter`.

A capture session that lost frames or hit a storage fault must be able to record
that outcome without pretending the buffered frames landed. These tests pin the
observable contract of the new public :meth:`DataSessionWriter.fail` seam:

* ``ACTIVE`` → ``FAILED`` is the only transition it performs;
* already committed segments and their counters are preserved;
* frames still in the buffer are dropped, never counted;
* repeat/finalize-after-fail behaviour is explicit, not accidental.
"""

import sqlite3
from pathlib import Path

import pytest
from canx.data.errors import DataSessionStateError, DataStorageError, DataValidationError
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService

STREAM_ID = "writer-failure-stream"


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x321,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x21]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=200.0 + sequence,
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


def segments_directory(root: Path, session_id: str) -> Path:
    return root / "data" / "sessions" / session_id / "segments"


def test_fail_marks_an_active_session_failed_and_keeps_committed_segments(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 3))
        committed = service.list_segments(writer.session_id)

        transitioned = writer.fail()

        stored = service.get_session(writer.session_id)
        assert transitioned is True
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == 3
        assert stored.segment_count == 1
        assert service.list_segments(writer.session_id) == committed
        assert (handle.root / committed[0].relative_path).is_file()


def test_fail_drops_the_uncommitted_buffer_without_counting_it(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=10)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))
        assert service.get_session(writer.session_id).frame_count == 0

        writer.fail()

        stored = service.get_session(writer.session_id)
        assert stored.frame_count == 0
        assert stored.segment_count == 0
        assert stored.first_sequence is None
        assert stored.last_sequence is None
        assert service.list_segments(writer.session_id) == ()
        assert list(segments_directory(handle.root, writer.session_id).iterdir()) == []


def test_fail_advances_updated_at_without_inventing_an_ended_at(tmp_path: Path) -> None:
    """The data model carries no ended_at for FAILED; fail must not fabricate one."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        before = service.get_session(writer.session_id)

        writer.fail()

        stored = service.get_session(writer.session_id)
        assert stored.ended_at is None
        assert stored.started_at == before.started_at
        assert stored.created_at == before.created_at
        assert stored.updated_at > before.updated_at


def test_fail_is_idempotent_and_reports_whether_it_transitioned(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)

        assert writer.fail() is True
        assert writer.fail() is False
        assert writer.state is DataSessionState.FAILED


def test_append_after_fail_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        writer.fail()

        with pytest.raises(DataSessionStateError) as info:
            writer.append(batch(0, 1))

        assert info.value.code == "data.session.not_active"
        assert info.value.details["state"] == "failed"


def test_finalize_after_fail_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        service.fail()

        with pytest.raises(DataSessionStateError) as info:
            service.finalize()

        assert info.value.code == "data.session.not_active"


def test_close_after_fail_leaves_the_session_failed(tmp_path: Path) -> None:
    """close() only finalizes an ACTIVE session; a FAILED one stays FAILED."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)
        writer.fail()

        writer.close()

        assert service.get_session(writer.session_id).state is DataSessionState.FAILED


def test_fail_after_finalize_is_a_no_op(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)
        completed = writer.finalize()

        assert writer.fail() is False
        assert service.get_session(writer.session_id) == completed
        assert service.get_session(writer.session_id).state is DataSessionState.COMPLETED


def test_failed_state_is_durable_across_a_reopen(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=2)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 2))
    writer.fail()
    session_id = writer.session_id
    handle.close()

    with ProjectService().open(root) as reopened:
        stored = DataSessionService(reopened.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == 2
        assert stored.segment_count == 1


def test_fail_survives_an_unwritable_database_without_leaking_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort durability: a storage fault must not mask the real failure."""
    import canx.data.session as session_module

    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)

        def fail_update(*args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(session_module.repository, "advance_session_state", fail_update)

        assert writer.fail() is True
        assert writer.state is DataSessionState.FAILED

        monkeypatch.undo()
        # Recovery is the documented owner of an unpersisted transition, and it
        # can only ever produce INTERRUPTED — never COMPLETED.
        assert service.recover_incomplete_sessions() == (writer.session_id,)
        assert service.get_session(writer.session_id).state is DataSessionState.INTERRUPTED


def test_fail_does_not_hide_an_unsupported_parquet_failure(tmp_path: Path) -> None:
    """A failed write already marks the session; fail() stays a safe repeat."""
    import canx.data.session as session_module
    from canx.data.errors import ParquetWriteError

    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)

        def fail_write(*args: object, **kwargs: object) -> int:
            raise ParquetWriteError("simulated segment failure")

        original = session_module.write_segment_file
        session_module.write_segment_file = fail_write  # type: ignore[assignment]
        try:
            with pytest.raises(ParquetWriteError):
                writer.append(batch(0, 2))
        finally:
            session_module.write_segment_file = original

        assert writer.state is DataSessionState.FAILED
        assert writer.fail() is False
        assert service.get_session(writer.session_id).state is DataSessionState.FAILED


def test_fail_is_never_a_bare_storage_error(tmp_path: Path) -> None:
    """fail() reports a lifecycle outcome, not an I/O error, to its caller."""
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)

        result = writer.fail()

        assert not isinstance(result, DataStorageError)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Split finalization: the flush is cancellable, the completion is decided.
# ---------------------------------------------------------------------------


def test_flush_pending_commits_the_tail_but_leaves_the_session_active(tmp_path: Path) -> None:
    """The long half of finalization must not decide the terminal state."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=10)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))

        writer.flush_pending()

        stored = service.get_session(writer.session_id)
        assert stored.state is DataSessionState.ACTIVE
        assert stored.frame_count == 4
        assert stored.segment_count == 1
        assert [segment.frame_count for segment in service.list_segments(writer.session_id)] == [4]

        # The session is still usable, so the caller can still refuse to complete it.
        assert writer.fail() is True
        assert service.get_session(writer.session_id).state is DataSessionState.FAILED


def test_commit_completed_finishes_a_flushed_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=10)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))
        writer.flush_pending()

        completed = writer.commit_completed()

        assert completed.state is DataSessionState.COMPLETED
        assert completed.ended_at is not None
        assert completed.frame_count == 4
        assert service.get_session(writer.session_id) == completed


def test_commit_completed_is_refused_when_another_writer_terminated_the_session(
    tmp_path: Path,
) -> None:
    """The decisive write is conditional, so a concurrent failure wins durably."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=10)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))
        writer.flush_pending()

        assert service.fail_active_session(writer.session_id) is True

        with pytest.raises(DataSessionStateError) as info:
            writer.commit_completed()

        assert info.value.code == "data.session.completion_refused"
        stored = service.get_session(writer.session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == 4
        assert len(service.list_segments(writer.session_id)) == 1


def test_flush_and_commit_require_an_active_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        writer.fail()

        with pytest.raises(DataSessionStateError):
            writer.flush_pending()
        with pytest.raises(DataSessionStateError):
            writer.commit_completed()


def test_finalize_is_still_flush_then_complete(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=4)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 6))

        completed = writer.finalize()

        assert completed.state is DataSessionState.COMPLETED
        assert completed.frame_count == 6
        assert completed.segment_count == 2


def test_fail_active_session_never_touches_a_completed_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)
        completed = writer.finalize()

        assert service.fail_active_session(writer.session_id) is False
        assert service.get_session(writer.session_id) == completed


def test_fail_active_session_is_durable_and_idempotent(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)

        assert service.fail_active_session(writer.session_id) is True
        assert service.fail_active_session(writer.session_id) is False
        assert service.get_session(writer.session_id).state is DataSessionState.FAILED


def test_fail_active_session_rejects_a_non_uuid(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        with pytest.raises(DataValidationError) as info:
            DataSessionService(handle.root).fail_active_session("not-a-uuid")

        assert info.value.code == "data.session.invalid_session_id"
