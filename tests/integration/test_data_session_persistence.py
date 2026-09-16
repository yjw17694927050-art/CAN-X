"""End-to-end data-session persistence across a project reopen.

This is the acceptance path for V0.2-02: create a project, persist enough frames
to roll over several Parquet segments, close everything, reopen, and prove the
identity, the aggregates, the files and the frame data all survived.
"""

import shutil
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq
from canx.data.model import DataSessionState
from canx.data.schema import (
    METADATA_FORMAT_KEY,
    METADATA_SCHEMA_VERSION_KEY,
    METADATA_SEGMENT_INDEX_KEY,
    METADATA_SESSION_ID_KEY,
    METADATA_STREAM_ID_KEY,
    SEGMENT_FORMAT_MARKER,
)
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME, DATABASE_SCHEMA_VERSION

STREAM_ID = "integration-stream"
MAX_FRAMES_PER_SEGMENT = 10
TOTAL_FRAMES = 35


def frame(sequence: int) -> Frame:
    """Build a literal valid frame whose timestamps track its sequence."""
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


def source_frames() -> list[Frame]:
    return [frame(sequence) for sequence in range(TOTAL_FRAMES)]


def batches(batch_size: int = 7) -> list[FrameBatch]:
    frames = source_frames()
    return [
        FrameBatch.create(stream_id=STREAM_ID, frames=frames[start : start + batch_size])
        for start in range(0, len(frames), batch_size)
    ]


def batch_of(count: int, *, stream_id: str = STREAM_ID) -> FrameBatch:
    """Build one contiguous batch of ``count`` frames starting at sequence 0."""
    return FrameBatch.create(
        stream_id=stream_id, frames=[frame(sequence) for sequence in range(count)]
    )


def test_a_reopened_project_restores_a_multi_segment_session(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    created = ProjectService().create(root, display_name="Vehicle A")
    project_id = created.project_id
    service = DataSessionService(created.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for batch in batches():
        writer.append(batch)
    completed = writer.finalize()
    session_id = writer.session_id
    created.close()

    assert completed.frame_count == TOTAL_FRAMES
    assert completed.segment_count == 4

    reopened = ProjectService().open(root)
    try:
        restored_service = DataSessionService(reopened.root)
        restored = restored_service.get_session(session_id)
        segments = restored_service.list_segments(session_id)

        assert restored == completed
        assert restored.project_id == project_id == reopened.project_id
        assert restored.state is DataSessionState.COMPLETED
        assert restored.frame_count == TOTAL_FRAMES
        assert restored.segment_count == 4
        assert restored.first_sequence == 0
        assert restored.last_sequence == TOTAL_FRAMES - 1
        assert restored.first_timestamp == 0.0
        assert restored.last_timestamp == float(TOTAL_FRAMES - 1)
        assert [segment.segment_index for segment in segments] == [0, 1, 2, 3]
        assert [segment.frame_count for segment in segments] == [10, 10, 10, 5]
        assert restored_service.list_sessions() == (restored,)

        for segment in segments:
            path = reopened.root / segment.relative_path
            assert path.is_file()
            assert path.stat().st_size == segment.byte_size
            assert segment.relative_path.startswith(f"data/sessions/{session_id}/segments/")

        restored_frames: list[Frame] = []
        for segment in segments:
            restored_frames.extend(restored_service.read_segment(session_id, segment.segment_index))
        assert restored_frames == source_frames()

        assert restored_service.inspect_integrity().clean is True
    finally:
        reopened.close()


def test_the_reopened_project_database_is_at_the_current_schema(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    writer = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT).start(
        stream_id=STREAM_ID
    )
    writer.append(batch_of(MAX_FRAMES_PER_SEGMENT))
    writer.finalize()
    handle.close()

    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        version = connection.execute("PRAGMA user_version").fetchone()
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()

    assert version is not None
    assert version[0] == DATABASE_SCHEMA_VERSION == 3
    assert {"project_metadata", "data_sessions", "data_segments", "dbc_assets"} <= tables


def test_segment_files_carry_the_canx_format_metadata_on_disk(tmp_path: Path) -> None:
    with ProjectService().create(
        tmp_path / "vehicle.canx", display_name="Vehicle A"
    ) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batches()[0])
        writer.finalize()
        segment = service.list_segments(writer.session_id)[0]

        stored = pq.read_table(handle.root / segment.relative_path).schema.metadata
        assert stored is not None
        metadata = {key.decode(): value.decode() for key, value in stored.items()}
        assert metadata[METADATA_FORMAT_KEY] == SEGMENT_FORMAT_MARKER
        assert metadata[METADATA_SCHEMA_VERSION_KEY] == "1"
        assert metadata[METADATA_SESSION_ID_KEY] == writer.session_id
        assert metadata[METADATA_STREAM_ID_KEY] == STREAM_ID
        assert metadata[METADATA_SEGMENT_INDEX_KEY] == "0"


def test_segments_are_written_relative_to_the_project_root(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    writer = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT).start(
        stream_id=STREAM_ID
    )
    writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=source_frames()))
    writer.finalize()
    handle.close()

    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        rows = connection.execute("SELECT relative_path FROM data_segments").fetchall()
        paths = [str(row[0]) for row in rows]
    finally:
        connection.close()

    assert paths
    for path in paths:
        assert not path.startswith("/")
        assert ":" not in path
        assert not path.startswith("C")


def test_a_moved_project_still_resolves_its_segments(tmp_path: Path) -> None:
    original = tmp_path / "original" / "vehicle.canx"
    original.parent.mkdir()
    handle = ProjectService().create(original, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=source_frames()))
    writer.finalize()
    session_id = writer.session_id
    handle.close()

    moved = tmp_path / "moved" / "vehicle.canx"
    moved.parent.mkdir()
    shutil.copytree(original, moved)
    shutil.rmtree(original)

    with ProjectService().open(moved) as reopened:
        moved_service = DataSessionService(reopened.root)
        assert moved_service.get_session(session_id).frame_count == TOTAL_FRAMES
        assert moved_service.inspect_integrity().clean is True
        assert moved_service.read_segment(session_id, 0)[0] == frame(0)


def test_repeated_sessions_in_one_project_stay_isolated(tmp_path: Path) -> None:
    with ProjectService().create(
        tmp_path / "vehicle.canx", display_name="Vehicle A"
    ) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
        first = service.start(stream_id=STREAM_ID)
        first.append(FrameBatch.create(stream_id=STREAM_ID, frames=source_frames()[:5]))
        first.finalize()
        second = service.start(stream_id="other-stream")
        second.append(
            FrameBatch.create(stream_id="other-stream", frames=source_frames()[:20])
        )
        second.finalize()

        assert first.session_id != second.session_id
        assert service.get_session(first.session_id).frame_count == 5
        assert service.get_session(second.session_id).frame_count == 20
        assert len(service.list_segments(first.session_id)) == 1
        assert len(service.list_segments(second.session_id)) == 2
        assert service.read_segment(first.session_id, 0) == tuple(source_frames()[:5])
        assert service.inspect_integrity().clean is True


def test_data_operations_release_the_project_directory(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    service = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=source_frames()))
    writer.finalize()
    service.recover_incomplete_sessions()
    service.inspect_integrity()
    handle.close()

    shutil.rmtree(root)

    assert not root.exists()


def test_a_second_service_instance_never_changes_committed_segments(tmp_path: Path) -> None:
    """Re-deriving the service must not change what was already committed."""
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    frames = source_frames()[:MAX_FRAMES_PER_SEGMENT]
    writer = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT).start(
        stream_id=STREAM_ID
    )
    writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=frames))
    writer.finalize()
    before = DataSessionService(handle.root).list_segments(writer.session_id)
    handle.close()

    with ProjectService().open(root) as reopened:
        again = DataSessionService(reopened.root, max_frames_per_segment=1)
        assert again.list_segments(writer.session_id) == before
        assert again.get_session(writer.session_id) == writer.session
