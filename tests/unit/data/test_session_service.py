"""Behavior tests for the data-session lifecycle and its failure paths."""

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

import canx.data.session as session_module
import pytest
from canx.data.errors import (
    DataError,
    DataIntegrityError,
    DataSessionStateError,
    DataStorageError,
    DataStreamMismatchError,
    DataValidationError,
    ParquetWriteError,
)
from canx.data.model import DataSessionState
from canx.data.session import (
    SEGMENTS_DIRECTORY,
    DataSessionService,
    DataSessionWriter,
)
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME

STREAM_ID = "stream-1"


def frame(sequence: int, **changes: object) -> Frame:
    values: dict[str, object] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 2,
        "data": bytes([sequence % 256, 0x10]),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 100.0 + sequence,
        "normalized_timestamp": float(sequence),
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)  # type: ignore[arg-type]


def batch(start: int, count: int, *, stream_id: str = STREAM_ID) -> FrameBatch:
    return FrameBatch.create(
        stream_id=stream_id, frames=[frame(sequence) for sequence in range(start, start + count)]
    )


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


def segments_directory(root: Path, session_id: str) -> Path:
    return root / "data" / "sessions" / session_id / SEGMENTS_DIRECTORY


def test_start_creates_an_active_session_with_the_project_identity(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)

        writer = service.start(stream_id=STREAM_ID)
        stored = service.get_session(writer.session_id)

        assert UUID(writer.session_id)
        assert stored.project_id == handle.project_id
        assert stored.stream_id == STREAM_ID
        assert stored.state is DataSessionState.ACTIVE
        assert stored.frame_count == 0
        assert stored.segment_count == 0
        assert stored.first_sequence is None
        assert stored.ended_at is None
        assert segments_directory(handle.root, writer.session_id).is_dir()


def test_start_creates_the_session_directory_layout(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)

        assert segments_directory(handle.root, writer.session_id).parent.name == writer.session_id


def test_finalizing_a_session_with_no_frames_completes_it_empty(tmp_path: Path) -> None:
    """An empty session is legal: a capture may start and receive no frames."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)

        completed = writer.finalize()

        assert completed.state is DataSessionState.COMPLETED
        assert completed.frame_count == 0
        assert completed.segment_count == 0
        assert completed.first_sequence is None
        assert completed.last_sequence is None
        assert completed.first_timestamp is None
        assert completed.last_timestamp is None
        assert completed.ended_at is not None
        assert service.get_session(writer.session_id) == completed
        assert service.list_segments(writer.session_id) == ()


def test_appending_a_full_segment_commits_it_and_updates_the_aggregate(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(0, 3))

        stored = service.get_session(writer.session_id)
        segments = service.list_segments(writer.session_id)
        assert stored.frame_count == 3
        assert stored.segment_count == 1
        assert stored.first_sequence == 0
        assert stored.last_sequence == 2
        assert [segment.segment_index for segment in segments] == [0]
        assert segments[0].frame_count == 3
        assert segments[0].relative_path == (
            f"data/sessions/{writer.session_id}/segments/000000.parquet"
        )
        assert segments[0].byte_size > 0
        assert (handle.root / segments[0].relative_path).is_file()


def test_frames_still_buffered_are_not_counted_as_durable(tmp_path: Path) -> None:
    """The aggregate reports durable frames, never merely appended ones."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=10)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(0, 4))

        pending = service.get_session(writer.session_id)
        assert pending.frame_count == 0
        assert pending.segment_count == 0

        completed = writer.finalize()
        assert completed.frame_count == 4
        assert completed.segment_count == 1


def test_multiple_batches_roll_over_into_deterministic_segments(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=5)
        writer = service.start(stream_id=STREAM_ID)

        for start in range(0, 15, 5):
            writer.append(batch(start, 5))
        completed = writer.finalize()

        segments = service.list_segments(writer.session_id)
        assert completed.frame_count == 15
        assert completed.segment_count == 3
        assert completed.first_sequence == 0
        assert completed.last_sequence == 14
        assert completed.first_timestamp == 0.0
        assert completed.last_timestamp == 14.0
        assert [segment.relative_path for segment in segments] == [
            f"data/sessions/{writer.session_id}/segments/000000.parquet",
            f"data/sessions/{writer.session_id}/segments/000001.parquet",
            f"data/sessions/{writer.session_id}/segments/000002.parquet",
        ]
        assert [segment.segment_index for segment in segments] == [0, 1, 2]
        assert [segment.first_sequence for segment in segments] == [0, 5, 10]
        assert [segment.last_sequence for segment in segments] == [4, 9, 14]


def test_a_batch_larger_than_the_threshold_becomes_several_segments(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=4)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(0, 10))

        segments = service.list_segments(writer.session_id)
        assert [segment.frame_count for segment in segments] == [4, 4]
        assert service.get_session(writer.session_id).frame_count == 8


def test_finalize_flushes_the_partial_segment(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=4)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(0, 6))

        assert service.get_session(writer.session_id).frame_count == 4
        completed = writer.finalize()
        assert completed.frame_count == 6
        assert completed.segment_count == 2
        assert service.list_segments(writer.session_id)[1].frame_count == 2


def test_session_metadata_survives_closing_and_reopening_the_project(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=4)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 6))
    completed = writer.finalize()
    session_id = writer.session_id
    handle.close()

    with ProjectService().open(root) as reopened:
        reloaded = DataSessionService(reopened.root)
        restored = reloaded.get_session(session_id)
        assert restored == completed
        assert [segment.segment_index for segment in reloaded.list_segments(session_id)] == [0, 1]
        assert restored.project_id == reopened.project_id


def test_committed_segment_data_can_be_read_back(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        source = [frame(sequence, flags=sequence) for sequence in range(6)]
        writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=source[0:3]))
        writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=source[3:6]))
        writer.finalize()

        assert service.read_segment(writer.session_id, 0) == tuple(source[0:3])
        assert service.read_segment(writer.session_id, 1) == tuple(source[3:6])


def test_a_segment_file_written_for_another_session_is_rejected(tmp_path: Path) -> None:
    """A registered row is never enough: the file's own identity must agree."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        donor_session = service.start(stream_id=STREAM_ID)
        donor_session.append(batch(0, 2))
        donor_session.finalize()
        victim_session = service.start(stream_id="stream-2")
        victim_session.append(FrameBatch.create(stream_id="stream-2", frames=[frame(0)]))
        victim_session.finalize()

        donor = service.list_segments(donor_session.session_id)[0]
        victim = service.list_segments(victim_session.session_id)[0]
        shutil.copyfile(handle.root / donor.relative_path, handle.root / victim.relative_path)

        with pytest.raises(DataIntegrityError) as info:
            service.read_segment(victim_session.session_id, 0)

        assert info.value.code == "data.integrity.metadata_mismatch"
        assert donor_session.session_id != victim_session.session_id


def test_a_batch_with_inverted_timestamps_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root, max_frames_per_segment=2).start(
            stream_id=STREAM_ID
        )
        inverted = FrameBatch.create(
            stream_id=STREAM_ID,
            frames=[frame(0, normalized_timestamp=9.0), frame(1, normalized_timestamp=3.0)],
        )

        with pytest.raises(DataIntegrityError) as info:
            writer.append(inverted)

        assert info.value.code == "data.integrity.timestamp_regression"
        assert writer.frame_count == 0


def test_a_batch_starting_before_the_last_committed_frame_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root, max_frames_per_segment=2).start(
            stream_id=STREAM_ID
        )
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[frame(0, normalized_timestamp=10.0), frame(1, normalized_timestamp=11.0)],
            )
        )
        regressed = FrameBatch.create(
            stream_id=STREAM_ID,
            frames=[frame(2, normalized_timestamp=5.0), frame(3, normalized_timestamp=6.0)],
        )

        with pytest.raises(DataIntegrityError) as info:
            writer.append(regressed)

        assert info.value.code == "data.integrity.timestamp_regression"
        assert writer.frame_count == 2


def test_a_batch_from_another_stream_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)

        with pytest.raises(DataStreamMismatchError) as info:
            writer.append(batch(0, 1, stream_id="other-stream"))

        assert info.value.code == "data.session.stream_mismatch"
        assert info.value.details["expected_stream_id"] == STREAM_ID


def test_appending_after_finalize_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        writer.finalize()

        with pytest.raises(DataSessionStateError) as info:
            writer.append(batch(0, 1))

        assert info.value.code == "data.session.not_active"


def test_finalizing_twice_is_a_typed_state_error(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        writer.finalize()

        with pytest.raises(DataSessionStateError) as info:
            writer.finalize()

        assert info.value.code == "data.session.not_active"


def test_the_writer_is_context_managed_and_finalizes_on_clean_exit(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        with service.start(stream_id=STREAM_ID) as writer:
            writer.append(batch(0, 2))
            session_id = writer.session_id

        assert service.get_session(session_id).state is DataSessionState.COMPLETED


def test_a_sequence_regression_across_batches_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        writer.append(batch(10, 2))

        with pytest.raises(DataIntegrityError) as info:
            writer.append(batch(5, 2))

        assert info.value.code == "data.integrity.sequence_regression"


def test_overlapping_sequence_bounds_across_batches_are_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)
        writer.append(batch(0, 3))

        with pytest.raises(DataIntegrityError) as info:
            writer.append(batch(2, 3))

        assert info.value.code == "data.integrity.sequence_regression"


def test_appending_something_that_is_not_a_batch_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)

        with pytest.raises(DataValidationError) as info:
            writer.append([frame(0)])  # type: ignore[arg-type]

        assert info.value.code == "data.session.invalid_batch"


@pytest.mark.parametrize("stream_id", ["", "   ", "\t\n"], ids=["empty", "spaces", "tabs"])
def test_a_blank_stream_id_is_rejected(tmp_path: Path, stream_id: str) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)

        with pytest.raises(DataValidationError) as info:
            service.start(stream_id=stream_id)

        assert info.value.code == "data.session.invalid_stream_id"
        assert service.list_sessions() == ()


def test_a_non_positive_segment_threshold_is_rejected(tmp_path: Path) -> None:
    with project(tmp_path) as handle, pytest.raises(DataValidationError):
        DataSessionService(handle.root, max_frames_per_segment=0)


def test_a_failed_parquet_write_does_not_advance_the_session(tmp_path: Path) -> None:
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

        stored = service.get_session(writer.session_id)
        assert stored.frame_count == 0
        assert stored.segment_count == 0
        assert stored.state is DataSessionState.FAILED
        assert service.list_segments(writer.session_id) == ()
        assert list(segments_directory(handle.root, writer.session_id).iterdir()) == []


def test_a_metadata_registration_failure_cleans_up_the_new_segment(tmp_path: Path) -> None:
    """A segment whose SQLite row never commits must not stay on disk registered-free."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)

        def fail_register(*args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        original = session_module.repository.register_segment
        session_module.repository.register_segment = fail_register  # type: ignore[assignment]
        try:
            with pytest.raises(DataStorageError) as info:
                writer.append(batch(0, 2))
        finally:
            session_module.repository.register_segment = original

        assert info.value.code == "data.session.registration_failed"
        stored = service.get_session(writer.session_id)
        assert stored.frame_count == 0
        assert stored.segment_count == 0
        assert stored.state is DataSessionState.FAILED
        assert service.list_segments(writer.session_id) == ()
        assert list(segments_directory(handle.root, writer.session_id).iterdir()) == []


def test_metadata_registration_failure_residue_is_reported_as_an_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cleanup itself fails, integrity inspection still sees the orphan."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)

        def fail_register(*args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(session_module.repository, "register_segment", fail_register)
        monkeypatch.setattr(session_module, "_remove_quietly", lambda path: None)

        with pytest.raises(DataStorageError):
            writer.append(batch(0, 2))

        monkeypatch.undo()

        report = service.inspect_integrity()

        assert report.orphan_segments == (
            f"data/sessions/{writer.session_id}/segments/000000.parquet",
        )


def test_all_data_handles_are_released_so_the_project_can_be_removed(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=3)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 3))
    writer.finalize()
    service.get_session(writer.session_id)
    service.list_sessions()
    service.list_segments(writer.session_id)
    service.read_segment(writer.session_id, 0)
    handle.close()

    shutil.rmtree(root)

    assert not root.exists()


def test_stored_segment_paths_are_relative_so_the_project_stays_portable(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))

        connection = sqlite3.connect(str(handle.root / DATABASE_FILENAME))
        try:
            rows = connection.execute("SELECT relative_path FROM data_segments").fetchall()
        finally:
            connection.close()

        assert [row[0] for row in rows] == [
            f"data/sessions/{writer.session_id}/segments/000000.parquet"
        ]


def test_a_copied_project_still_resolves_its_segment_metadata(tmp_path: Path) -> None:
    original = tmp_path / "original" / "vehicle.canx"
    original.parent.mkdir()
    handle = ProjectService().create(original, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=2)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(batch(0, 2))
    writer.finalize()
    session_id = writer.session_id
    handle.close()

    moved = tmp_path / "moved" / "vehicle.canx"
    moved.parent.mkdir()
    shutil.copytree(original, moved)

    with ProjectService().open(moved) as reopened:
        copied = DataSessionService(reopened.root)
        segments = copied.list_segments(session_id)
        assert copied.get_session(session_id).frame_count == 2
        assert (moved / segments[0].relative_path).is_file()
        assert copied.read_segment(session_id, 0) == (frame(0), frame(1))


def test_a_path_escaping_relative_path_is_refused(tmp_path: Path) -> None:
    """Persisted paths are never trusted to stay inside the project root."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))

        outside = tmp_path / "outside.parquet"
        outside.write_bytes(b"x")
        connection = sqlite3.connect(str(handle.root / DATABASE_FILENAME))
        try:
            connection.execute(
                "UPDATE data_segments SET relative_path = ?", ("../../outside.parquet",)
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(DataIntegrityError) as info:
            service.read_segment(writer.session_id, 0)

        assert info.value.code == "data.integrity.path_escape"


def test_read_segment_reports_a_missing_file_instead_of_dropping_the_row(
    tmp_path: Path,
) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        segment = service.list_segments(writer.session_id)[0]
        (handle.root / segment.relative_path).unlink()

        with pytest.raises(DataIntegrityError) as info:
            service.read_segment(writer.session_id, 0)

        assert info.value.code == "data.integrity.segment_missing"
        assert service.list_segments(writer.session_id) == (segment,)


def test_the_service_never_returns_raw_sqlite_values(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        writer.finalize()

        assert not isinstance(service.get_session(writer.session_id), sqlite3.Row)
        for segment in service.list_segments(writer.session_id):
            assert not isinstance(segment, sqlite3.Row)
        assert service.list_sessions()
        assert all(
            not isinstance(session, sqlite3.Row) for session in service.list_sessions()
        )


def test_listing_sessions_is_ordered_and_deterministic(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        started = [service.start(stream_id=f"stream-{index}").session_id for index in range(3)]

        listed = [session.session_id for session in service.list_sessions()]

        assert sorted(listed) == sorted(started)
        assert listed == [session.session_id for session in service.list_sessions()]


def test_a_session_handle_reports_its_own_identity(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root).start(stream_id=STREAM_ID)

        assert isinstance(writer, DataSessionWriter)
        assert writer.stream_id == STREAM_ID
        assert writer.project_id == handle.project_id
        assert writer.state is DataSessionState.ACTIVE


def test_data_errors_never_leak_a_bare_value_error(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)

        with pytest.raises(DataError) as info:
            service.get_session("not-a-uuid")

        assert not isinstance(info.value, ValueError)
        assert info.value.source == "data"


def test_a_missing_project_database_is_a_typed_data_error(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    with ProjectService().create(root, display_name="Vehicle A"):
        pass
    (root / DATABASE_FILENAME).unlink()

    with pytest.raises(DataStorageError) as info:
        DataSessionService(root).list_sessions()

    assert info.value.code == "data.project_database_missing"


def test_a_foreign_database_is_refused_with_a_typed_data_error(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    root.mkdir()
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DataStorageError) as info:
        DataSessionService(root).list_sessions()

    assert info.value.code == "data.project_database_unavailable"


def test_the_project_identity_is_not_duplicated_in_the_session_payload(tmp_path: Path) -> None:
    """The session row records the project identity the database already owns."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        writer = service.start(stream_id=STREAM_ID)

        connection = sqlite3.connect(str(handle.root / DATABASE_FILENAME))
        try:
            row = connection.execute(
                "SELECT project_id, stream_id, state FROM data_sessions WHERE session_id = ?",
                (writer.session_id,),
            ).fetchone()
        finally:
            connection.close()

        assert row == (handle.project_id, STREAM_ID, "active")


def test_the_timestamp_bounds_are_recorded_as_floats(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        completed = writer.finalize()

        assert isinstance(completed.first_timestamp, float)
        assert isinstance(completed.last_timestamp, float)
        assert completed.first_timestamp <= completed.last_timestamp

        connection = sqlite3.connect(str(handle.root / DATABASE_FILENAME))
        try:
            row = connection.execute(
                "SELECT first_timestamp, last_timestamp, ended_at FROM data_sessions"
            ).fetchone()
        finally:
            connection.close()

        assert row is not None
        assert row[0] == completed.first_timestamp
        assert row[1] == completed.last_timestamp
        assert datetime.fromisoformat(row[2]).tzinfo is not None


def test_the_manifest_is_untouched_by_data_sessions(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        before = json.loads((handle.root / "project.json").read_text(encoding="utf-8"))
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 2))
        writer.finalize()

        after = json.loads((handle.root / "project.json").read_text(encoding="utf-8"))

        assert after == before


# ---------------------------------------------------------------------------
# start() failure atomicity: either a usable session exists, or nothing does.
# ---------------------------------------------------------------------------

FIXED_SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
SESSIONS_RELATIVE = ("data", "sessions")


def sessions_directory(root: Path) -> Path:
    return root.joinpath(*SESSIONS_RELATIVE)


def session_directory(root: Path, session_id: str) -> Path:
    return sessions_directory(root) / session_id


def test_start_is_atomic_when_the_session_directory_cannot_be_created(
    tmp_path: Path,
) -> None:
    """A real filesystem failure must not leave an ACTIVE session behind."""
    with project(tmp_path) as handle:
        sessions_root = sessions_directory(handle.root)
        sessions_root.write_bytes(b"not a directory")
        service = DataSessionService(handle.root)

        with pytest.raises(DataStorageError) as info:
            service.start(stream_id=STREAM_ID)

        assert info.value.code == "data.session.directory_create_failed"
        assert not isinstance(info.value, OSError)
        assert service.list_sessions() == ()
        # The pre-existing file is user state and must survive the failure.
        assert sessions_root.is_file()


def test_start_is_atomic_when_directory_creation_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PermissionError surfaces as a typed data error, never as raw OSError."""

    def deny(project_root: Path, session_id: str) -> Path:
        raise PermissionError("simulated denial")

    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        monkeypatch.setattr(session_module, "_create_session_directory", deny)

        with pytest.raises(DataStorageError) as info:
            service.start(stream_id=STREAM_ID)

        assert info.value.code == "data.session.directory_create_failed"
        assert not isinstance(info.value, PermissionError)
        assert service.list_sessions() == ()
        sessions_root = sessions_directory(handle.root)
        assert not sessions_root.exists() or list(sessions_root.iterdir()) == []


def test_a_failed_session_registration_removes_the_new_session_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory created + SQLite insert failed ⇒ no session directory remains."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)

        def fail_insert(*args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(session_module, "uuid4", lambda: UUID(FIXED_SESSION_ID))
        monkeypatch.setattr(session_module.repository, "insert_session", fail_insert)

        with pytest.raises(DataStorageError) as info:
            service.start(stream_id=STREAM_ID)

        assert info.value.code == "data.session.start_failed"
        assert not isinstance(info.value, sqlite3.Error)
        assert service.list_sessions() == ()
        assert not session_directory(handle.root, FIXED_SESSION_ID).exists()

        monkeypatch.undo()

        restarted = service.start(stream_id=STREAM_ID)
        assert restarted.project_id == handle.project_id
        assert segments_directory(handle.root, restarted.session_id).is_dir()
        assert service.get_session(restarted.session_id).state is DataSessionState.ACTIVE


def test_a_database_that_cannot_be_reached_leaves_no_session_directory(
    tmp_path: Path,
) -> None:
    """The directory is created first, so an unreachable database must clean it up."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root)
        handle.close()
        (handle.root / DATABASE_FILENAME).unlink()

        with pytest.raises(DataStorageError) as info:
            service.start(stream_id=STREAM_ID)

        assert info.value.code == "data.project_database_missing"
        sessions_root = sessions_directory(handle.root)
        assert not sessions_root.exists() or list(sessions_root.iterdir()) == []
