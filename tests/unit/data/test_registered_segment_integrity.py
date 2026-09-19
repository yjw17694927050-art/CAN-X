"""One integrity definition for a registered segment, shared by both read paths.

A persisted segment row claims a canonical path, a session, a stream, an index, a
column layout, a row count and a byte size. The file on disk has to agree with
all of it — and ``DataSessionService.read_segment`` and ``QueryService`` must
disagree with it in exactly the same way, because both call the same gate. Each
case below corrupts one claim and asserts that both paths refuse that claim, with
the data-domain invariant visible as the query error's ``cause``.

Nothing here repairs anything: the corruption is still on disk, still registered,
and the *other* segment of the same session is still readable.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from canx.data.errors import DataIntegrityError
from canx.data.parquet import segment_filename
from canx.data.schema import (
    METADATA_FORMAT_KEY,
    METADATA_SCHEMA_VERSION_KEY,
    METADATA_SEGMENT_INDEX_KEY,
    METADATA_SESSION_ID_KEY,
    METADATA_STREAM_ID_KEY,
    SEGMENT_FORMAT_MARKER,
    frames_to_table,
)
from canx.data.session import DataSessionService, segment_relative_path
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME
from canx.query.errors import QueryIntegrityError
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "registered-stream"
FRAMES_PER_SEGMENT = 3
TOTAL_FRAMES = 6
OTHER_SESSION_ID = "11111111-2222-4333-8444-555555555555"


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


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


def write_session(handle: ProjectHandle) -> str:
    """Commit two segments of three frames each and return the session id."""
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[frame(sequence) for sequence in range(start, start + FRAMES_PER_SEGMENT)],
            )
        )
    writer.finalize()
    return writer.session_id


def segment_path(root: Path, session_id: str, segment_index: int) -> Path:
    return root / segment_relative_path(session_id, segment_index)


def rewrite_segment(
    path: Path,
    frames: list[Frame],
    *,
    session_id: str,
    segment_index: int,
    stream_id: str = STREAM_ID,
) -> None:
    table = frames_to_table(
        frames, session_id=session_id, stream_id=stream_id, segment_index=segment_index
    )
    pq.write_table(table, path)


def rewrite_segment_with_layout(
    path: Path, *, session_id: str, stream_id: str, segment_index: int
) -> None:
    """Stamp the CAN-X marker onto a table whose columns are not the frame layout."""
    table = pa.table({"sequence": pa.array([0], type=pa.uint64())})
    metadata = {
        METADATA_FORMAT_KEY: SEGMENT_FORMAT_MARKER,
        METADATA_SCHEMA_VERSION_KEY: "1",
        METADATA_SESSION_ID_KEY: session_id,
        METADATA_STREAM_ID_KEY: stream_id,
        METADATA_SEGMENT_INDEX_KEY: str(segment_index),
    }
    table = table.replace_schema_metadata(
        {key.encode("utf-8"): value.encode("utf-8") for key, value in metadata.items()}
    )
    pq.write_table(table, path)


def mutate_segment_row(root: Path, session_id: str, segment_index: int, **columns: object) -> None:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        assignments = ", ".join(f"{name} = ?" for name in columns)
        connection.execute(
            f"UPDATE data_segments SET {assignments} WHERE session_id = ? AND segment_index = ?",
            (*columns.values(), session_id, segment_index),
        )
        connection.commit()
    finally:
        connection.close()


def _corrupt_foreign_session(root: Path, session_id: str) -> str:
    rewrite_segment(
        segment_path(root, session_id, 0),
        [frame(sequence) for sequence in range(FRAMES_PER_SEGMENT)],
        session_id=OTHER_SESSION_ID,
        segment_index=0,
    )
    return "data.integrity.metadata_mismatch"


def _corrupt_foreign_stream(root: Path, session_id: str) -> str:
    rewrite_segment(
        segment_path(root, session_id, 0),
        [frame(sequence) for sequence in range(FRAMES_PER_SEGMENT)],
        session_id=session_id,
        segment_index=0,
        stream_id="another-stream",
    )
    return "data.integrity.metadata_mismatch"


def _corrupt_index(root: Path, session_id: str) -> str:
    rewrite_segment(
        segment_path(root, session_id, 0),
        [frame(sequence) for sequence in range(FRAMES_PER_SEGMENT)],
        session_id=session_id,
        segment_index=9,
    )
    return "data.integrity.metadata_mismatch"


def _corrupt_layout(root: Path, session_id: str) -> str:
    rewrite_segment_with_layout(
        segment_path(root, session_id, 0),
        session_id=session_id,
        stream_id=STREAM_ID,
        segment_index=0,
    )
    return "data.integrity.parquet_schema_mismatch"


def _corrupt_row_count(root: Path, session_id: str) -> str:
    rewrite_segment(
        segment_path(root, session_id, 0),
        [frame(0)],
        session_id=session_id,
        segment_index=0,
    )
    return "data.integrity.frame_count_mismatch"


def _corrupt_byte_size(root: Path, session_id: str) -> str:
    registered = DataSessionService(root).list_segments(session_id)[0]
    mutate_segment_row(root, session_id, 0, byte_size=registered.byte_size + 1)
    return "data.integrity.byte_size_mismatch"


def _corrupt_registered_path(root: Path, session_id: str) -> str:
    """A row that points at a valid file which is not its canonical path."""
    canonical = segment_path(root, session_id, 0)
    displaced = canonical.with_name("displaced.parquet")
    displaced.write_bytes(canonical.read_bytes())
    mutate_segment_row(
        root,
        session_id,
        0,
        relative_path=segment_relative_path(session_id, 0).replace(
            segment_filename(0), "displaced.parquet"
        ),
    )
    return "data.integrity.registered_path_mismatch"


CORRUPTIONS: dict[str, Callable[[Path, str], str]] = {
    "foreign-session": _corrupt_foreign_session,
    "foreign-stream": _corrupt_foreign_stream,
    "wrong-index": _corrupt_index,
    "broken-layout": _corrupt_layout,
    "row-count": _corrupt_row_count,
    "byte-size": _corrupt_byte_size,
    "non-canonical-path": _corrupt_registered_path,
}


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_both_read_paths_refuse_the_same_registration_disagreement(
    tmp_path: Path, corruption: str
) -> None:
    with project(tmp_path) as handle:
        session_id = write_session(handle)
        reader = DataSessionService(handle.root)

        # Negative control: before the corruption both paths see a healthy session.
        assert reader.read_segment(session_id, 0)[0] == frame(0)
        assert (
            QueryService(handle.root)
            .summarize_frames(FrameFilter(session_id=session_id))
            .matching_frame_count
            == TOTAL_FRAMES
        )

        expected_code = CORRUPTIONS[corruption](handle.root, session_id)

        with pytest.raises(DataIntegrityError) as data_info:
            reader.read_segment(session_id, 0)
        assert data_info.value.code == expected_code

        with pytest.raises(QueryIntegrityError) as query_info:
            QueryService(handle.root).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )
        assert query_info.value.code == "query.segment_invalid"
        assert query_info.value.details["cause"] == expected_code

        # Only the corrupted claim is refused; the rest of the session still reads.
        assert reader.read_segment(session_id, 1)[0] == frame(FRAMES_PER_SEGMENT)


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_a_refused_registration_is_reported_without_being_repaired(
    tmp_path: Path, corruption: str
) -> None:
    with project(tmp_path) as handle:
        session_id = write_session(handle)
        reader = DataSessionService(handle.root)

        CORRUPTIONS[corruption](handle.root, session_id)
        corrupted = reader.list_segments(session_id)

        with pytest.raises(DataIntegrityError):
            reader.read_segment(session_id, 0)

        # The disagreement is still registered and still on disk: nothing was
        # rewritten, deleted or silently repaired by the refusal.
        assert reader.list_segments(session_id) == corrupted
        assert [segment.segment_index for segment in corrupted] == [0, 1]
        assert (handle.root / corrupted[0].relative_path).is_file()
        assert reader.read_segment(session_id, 1) == tuple(
            frame(sequence) for sequence in range(FRAMES_PER_SEGMENT, TOTAL_FRAMES)
        )


def test_a_healthy_registered_segment_is_accepted_by_both_paths(tmp_path: Path) -> None:
    """The gate refuses disagreement, not the shape of a normal session."""
    with project(tmp_path) as handle:
        session_id = write_session(handle)
        reader = DataSessionService(handle.root)

        assert reader.read_segment(session_id, 0) == tuple(
            frame(sequence) for sequence in range(FRAMES_PER_SEGMENT)
        )
        assert reader.read_segment(session_id, 1) == tuple(
            frame(sequence) for sequence in range(FRAMES_PER_SEGMENT, TOTAL_FRAMES)
        )
        assert reader.inspect_integrity().clean is True
        page = QueryService(handle.root).query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id))
        )
        assert [item.sequence for item in page.frames] == list(range(TOTAL_FRAMES))


def test_the_registered_row_keeps_the_decoded_byte_size_of_its_own_file(tmp_path: Path) -> None:
    """The size the writer registers is the observable truth the gate compares against."""
    with project(tmp_path) as handle:
        session_id = write_session(handle)
        for segment in DataSessionService(handle.root).list_segments(session_id):
            path = handle.root / segment.relative_path
            assert path.stat().st_size == segment.byte_size
