"""Behavior tests for interrupted-session recovery and integrity inspection."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import canx.data.session as session_module
import pytest
from canx.data.errors import DataValidationError, ParquetWriteError
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME

STREAM_ID = "stream-1"
RECOVERED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


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
        data=bytes([sequence % 256, 0x10]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=100.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def batch(start: int, count: int) -> FrameBatch:
    return FrameBatch.create(
        stream_id=STREAM_ID, frames=[frame(sequence) for sequence in range(start, start + count)]
    )


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def segments_directory(root: Path, session_id: str) -> Path:
    return root / "data" / "sessions" / session_id / "segments"


def test_a_fresh_project_reports_a_clean_integrity_state(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        report = DataSessionService(handle.root).inspect_integrity()

        assert report.clean is True
        assert report.scanned_sessions == 0
        assert report.temporary_files == ()
        assert report.orphan_segments == ()
        assert report.missing_segments == ()
        assert report.metadata_mismatches == ()


def test_a_fully_committed_session_reports_no_findings(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 6))
        writer.finalize()

        report = service.inspect_integrity()

        assert report.clean is True
        assert report.scanned_sessions == 1


def test_a_temporary_segment_file_is_reported(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        residue = segments_directory(handle.root, writer.session_id) / "000001.parquet.tmp"
        residue.write_bytes(b"half written")

        report = service.inspect_integrity()

        assert report.temporary_files == (
            f"data/sessions/{writer.session_id}/segments/000001.parquet.tmp",
        )
        assert report.orphan_segments == ()
        assert report.clean is False


def test_a_segment_file_with_no_registered_row_is_reported_as_an_orphan(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        registered = service.list_segments(writer.session_id)[0]
        orphan = segments_directory(handle.root, writer.session_id) / "000007.parquet"
        orphan.write_bytes((handle.root / registered.relative_path).read_bytes())

        report = service.inspect_integrity()

        assert report.orphan_segments == (
            f"data/sessions/{writer.session_id}/segments/000007.parquet",
        )
        assert report.missing_segments == ()
        assert report.clean is False


def test_a_registered_segment_with_no_file_is_reported_as_missing(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        segment = service.list_segments(writer.session_id)[0]
        (handle.root / segment.relative_path).unlink()

        report = service.inspect_integrity()

        assert report.missing_segments == (segment.relative_path,)
        assert report.orphan_segments == ()
        assert report.clean is False


def test_a_registered_segment_whose_size_changed_is_reported_as_a_mismatch(
    tmp_path: Path,
) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        segment = service.list_segments(writer.session_id)[0]
        path = handle.root / segment.relative_path
        with path.open("ab") as handle_file:
            handle_file.write(b"trailing bytes")

        report = service.inspect_integrity()

        assert report.metadata_mismatches == (segment.relative_path,)
        assert report.missing_segments == ()
        assert report.clean is False


def test_inspection_reports_without_repairing_anything(tmp_path: Path) -> None:
    """Integrity inspection never deletes or rewrites; a human decides."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        segment = service.list_segments(writer.session_id)[0]
        residue = segments_directory(handle.root, writer.session_id) / "000009.parquet.tmp"
        residue.write_bytes(b"residue")
        orphan = segments_directory(handle.root, writer.session_id) / "000008.parquet"
        orphan.write_bytes(b"orphan")
        missing_source = handle.root / segment.relative_path
        missing_source.unlink()

        report = service.inspect_integrity()

        assert report.temporary_files
        assert report.orphan_segments
        assert report.missing_segments
        assert residue.is_file()
        assert orphan.is_file()
        assert not missing_source.exists()
        assert service.list_segments(writer.session_id) == (segment,)


def test_recovery_moves_an_active_session_to_interrupted(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))

        recovered = service.recover_incomplete_sessions(now=RECOVERED_AT)

        assert recovered == (writer.session_id,)
        restored = service.get_session(writer.session_id)
        assert restored.state is DataSessionState.INTERRUPTED
        assert restored.ended_at == RECOVERED_AT
        assert restored.updated_at == RECOVERED_AT
        assert restored.frame_count == 2


def test_recovery_keeps_every_committed_segment_valid(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))
        before = service.list_segments(writer.session_id)

        service.recover_incomplete_sessions(now=RECOVERED_AT)

        assert service.list_segments(writer.session_id) == before
        assert service.read_segment(writer.session_id, 0) == (frame(0), frame(1))
        assert service.inspect_integrity().clean is True


def test_recovery_leaves_completed_and_interrupted_sessions_untouched(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        completed = service.start(stream_id=STREAM_ID)
        completed.append(batch(0, 2))
        completed.finalize()
        stale = service.start(stream_id="stream-2")
        stale.append(FrameBatch.create(stream_id="stream-2", frames=[frame(0)]))
        service.recover_incomplete_sessions(now=RECOVERED_AT)

        assert service.recover_incomplete_sessions(now=RECOVERED_AT) == ()
        assert service.get_session(completed.session_id).state is DataSessionState.COMPLETED
        assert service.get_session(stale.session_id).state is DataSessionState.INTERRUPTED
        assert service.get_session(stale.session_id).ended_at == RECOVERED_AT


def test_recovery_leaves_a_failed_session_untouched(tmp_path: Path) -> None:
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

        assert service.recover_incomplete_sessions(now=RECOVERED_AT) == ()
        failed = service.get_session(writer.session_id)
        assert failed.state is DataSessionState.FAILED
        assert failed.ended_at is None


def test_recovery_requires_an_aware_timestamp(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)

        with pytest.raises(DataValidationError):
            service.recover_incomplete_sessions(now=datetime(2026, 9, 17, 12, 0))


def test_recovery_is_explicit_and_opening_a_project_changes_nothing(tmp_path: Path) -> None:
    """Opening a project is an integrity operation, never a silent session edit."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=2)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 2))
    handle.close()

    for _ in range(2):
        with ProjectService().open(root) as reopened:
            still_active = DataSessionService(reopened.root).get_session(writer.session_id)
            assert still_active.state is DataSessionState.ACTIVE
            assert still_active.ended_at is None

    with ProjectService().open(root) as reopened:
        assert DataSessionService(reopened.root).recover_incomplete_sessions() == (
            writer.session_id,
        )
        assert (
            DataSessionService(reopened.root).get_session(writer.session_id).state
            is DataSessionState.INTERRUPTED
        )


def test_recovery_state_change_is_durable(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    writer = DataSessionService(handle.root, max_frames_per_segment=2).start(stream_id=STREAM_ID)
    writer.append(batch(0, 2))
    handle.close()

    with ProjectService().open(root) as reopened:
        DataSessionService(reopened.root).recover_incomplete_sessions(now=RECOVERED_AT)

    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        row = connection.execute(
            "SELECT state, ended_at FROM data_sessions WHERE session_id = ?",
            (writer.session_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row == ("interrupted", RECOVERED_AT.isoformat())
